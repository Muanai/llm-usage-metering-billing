import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from src.db.models import Tenant, Subscription, Plan, UsageEvent

# Per-tenant in-process lock striping for concurrency serialization
_tenant_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

def get_tenant_lock(tenant_id: str) -> asyncio.Lock:
    """Returns an asyncio.Lock striped by tenant_id for in-process concurrency serialization."""
    return _tenant_locks[tenant_id]

class QuotaDecision(str, Enum):
    ALLOWED = "allowed"
    QUOTA_EXCEEDED = "quota_exceeded"
    PAYMENT_REQUIRED = "payment_required"

class QuotaCheckResult(BaseModel):
    status: QuotaDecision
    http_status: int
    message: str
    metric: Optional[str] = None
    current_usage: int = 0
    limit: int = 0
    retry_after_seconds: int = 0
    plan_id: str = "free"

async def check_tenant_quota(
    db: AsyncSession,
    tenant_id: str,
    requested_calls: int = 1,
    requested_tokens: int = 0
) -> QuotaCheckResult:
    """
    Evaluates tenant entitlement and usage quotas prior to executing billable actions.
    Precedence:
    1. Financial default (status in past_due, canceled, unpaid) -> 402 Payment Required
    2. Quota check (current + requested > limit) -> 429 Too Many Requests
    3. Allowed -> 200 OK
    """
    # 1. Pessimistic concurrency lock on tenant record
    tenant_stmt = select(Tenant).where(Tenant.id == tenant_id)
    if db.bind and db.bind.dialect.name == "postgresql":
        tenant_stmt = tenant_stmt.with_for_update()
    tenant_res = await db.execute(tenant_stmt)
    tenant = tenant_res.scalar_one_or_none()

    if not tenant:
        return QuotaCheckResult(
            status=QuotaDecision.PAYMENT_REQUIRED,
            http_status=402,
            message=f"Tenant '{tenant_id}' not found.",
            plan_id="free"
        )

    # 2. Fetch Subscription and Plan
    sub_res = await db.execute(
        select(Subscription).where(Subscription.tenant_id == tenant_id)
    )
    sub = sub_res.scalar_one_or_none()

    now = datetime.now(timezone.utc)
    if not sub:
        # Auto-create active free tier subscription if missing
        first_day = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        # Next month calculation
        if now.month == 12:
            next_month = now.replace(year=now.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            next_month = now.replace(month=now.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)

        sub = Subscription(
            tenant_id=tenant_id,
            plan_id="free",
            status="active",
            current_period_start=first_day,
            current_period_end=next_month
        )
        db.add(sub)
        await db.flush()

    # Step 1 in Precedence: Check financial entitlement
    if sub.status in ["past_due", "unpaid", "canceled", "incomplete"]:
        return QuotaCheckResult(
            status=QuotaDecision.PAYMENT_REQUIRED,
            http_status=402,
            message=f"Subscription is {sub.status}. Please update payment method to resume billable operations.",
            plan_id=sub.plan_id
        )

    # Fetch Plan details
    plan_res = await db.execute(select(Plan).where(Plan.id == sub.plan_id))
    plan = plan_res.scalar_one_or_none()
    if not plan:
        # Fallback to Free defaults if plan row not yet seeded
        plan_limits = (1000, 100000) if sub.plan_id == "free" else (50000, 5000000)
    else:
        plan_limits = (plan.monthly_api_limit, plan.monthly_token_limit)

    max_calls, max_tokens = plan_limits

    # Step 2: Aggregate current cycle usage
    period_start = sub.current_period_start
    period_end = sub.current_period_end

    usage_query = select(
        func.coalesce(func.sum(UsageEvent.api_calls), 0).label("used_calls"),
        func.coalesce(
            func.sum(
                UsageEvent.input_tokens
                + UsageEvent.cached_input_tokens
                + UsageEvent.output_tokens
                + UsageEvent.reasoning_tokens
            ),
            0
        ).label("used_tokens")
    ).where(
        UsageEvent.tenant_id == tenant_id,
        UsageEvent.created_at >= period_start,
        UsageEvent.created_at < period_end
    )

    usage_res = await db.execute(usage_query)
    current_calls, current_tokens = usage_res.one()

    # Calculate remaining time in billing window for Retry-After header
    # Ensure current_period_end is timezone aware
    end_time = period_end if period_end.tzinfo else period_end.replace(tzinfo=timezone.utc)
    seconds_remaining = max(60, int((end_time - now).total_seconds()))

    # Step 3: Quota Boundary Checks
    if current_calls + requested_calls > max_calls:
        return QuotaCheckResult(
            status=QuotaDecision.QUOTA_EXCEEDED,
            http_status=429,
            message=f"Monthly API call limit reached for {sub.plan_id} plan ({current_calls}/{max_calls}). Upgrade to Pro to continue.",
            metric="api_calls",
            current_usage=current_calls,
            limit=max_calls,
            retry_after_seconds=seconds_remaining,
            plan_id=sub.plan_id
        )

    if current_tokens + requested_tokens > max_tokens:
        return QuotaCheckResult(
            status=QuotaDecision.QUOTA_EXCEEDED,
            http_status=429,
            message=f"Monthly token limit reached for {sub.plan_id} plan ({current_tokens}/{max_tokens}). Upgrade to Pro to continue.",
            metric="tokens",
            current_usage=current_tokens,
            limit=max_tokens,
            retry_after_seconds=seconds_remaining,
            plan_id=sub.plan_id
        )

    # Step 4: Allowed
    return QuotaCheckResult(
        status=QuotaDecision.ALLOWED,
        http_status=200,
        message="Quota available.",
        current_usage=current_calls,
        limit=max_calls,
        plan_id=sub.plan_id
    )
