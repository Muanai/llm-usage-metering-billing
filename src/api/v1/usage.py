from fastapi import APIRouter, Depends, Header
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from src.api.deps import get_db
from src.api.error_handlers import AppException
from src.core.calculator import calculate_monthly_rollup
from src.db.models import Tenant, Subscription, Plan, UsageEvent

router = APIRouter()


@router.get("/tenants/{tenant_id}/usage")
async def get_tenant_usage(tenant_id: str, db: AsyncSession = Depends(get_db)):
    """
    Returns monthly usage rollup and itemized costs for a given tenant.
    """
    tenant_res = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = tenant_res.scalar_one_or_none()
    if not tenant:
        raise AppException(
            status_code=404,
            code="tenant_not_found",
            message=f"Tenant '{tenant_id}' not found.",
            details={"tenant_id": tenant_id}
        )

    sub_res = await db.execute(select(Subscription).where(Subscription.tenant_id == tenant_id))
    sub = sub_res.scalar_one_or_none()
    if not sub:
        raise AppException(
            status_code=404,
            code="subscription_not_found",
            message=f"No subscription found for tenant '{tenant_id}'.",
            details={"tenant_id": tenant_id}
        )

    plan_res = await db.execute(select(Plan).where(Plan.id == sub.plan_id))
    plan = plan_res.scalar_one_or_none()
    if not plan:
        # Fallback default limits
        max_calls = 1000 if sub.plan_id == "free" else 50000
        max_tokens = 100000 if sub.plan_id == "free" else 5000000
        base_price_cents = 0 if sub.plan_id == "free" else 2900
        plan_name = "Free Plan" if sub.plan_id == "free" else "Pro Plan"
    else:
        max_calls = plan.monthly_api_limit
        max_tokens = plan.monthly_token_limit
        base_price_cents = plan.base_price_cents
        plan_name = plan.name

    # Aggregate usage events within billing period
    query = select(
        func.coalesce(func.sum(UsageEvent.api_calls), 0).label("used_calls"),
        func.coalesce(func.sum(UsageEvent.input_tokens), 0).label("input_tokens"),
        func.coalesce(func.sum(UsageEvent.cached_input_tokens), 0).label("cached_input_tokens"),
        func.coalesce(func.sum(UsageEvent.output_tokens), 0).label("output_tokens"),
        func.coalesce(func.sum(UsageEvent.reasoning_tokens), 0).label("reasoning_tokens"),
        func.coalesce(func.sum(UsageEvent.cost_micro_cents), 0).label("total_micro_cents"),
    ).where(
        UsageEvent.tenant_id == tenant_id,
        UsageEvent.created_at >= sub.current_period_start,
        UsageEvent.created_at < sub.current_period_end
    )
    res = await db.execute(query)
    used_calls, in_tok, cached_tok, out_tok, reason_tok, total_micro = res.one()

    total_tokens = in_tok + cached_tok + out_tok + reason_tok
    rollup = calculate_monthly_rollup(
        total_api_calls=used_calls,
        token_micro_cents=total_micro,
        base_price_cents=base_price_cents
    )

    percent_calls = round((used_calls / max_calls * 100), 2) if max_calls > 0 else 0
    percent_tokens = round((total_tokens / max_tokens * 100), 2) if max_tokens > 0 else 0

    return {
        "tenant_id": tenant_id,
        "period": {
            "start": sub.current_period_start.isoformat(),
            "end": sub.current_period_end.isoformat(),
            "calendar_month": sub.current_period_start.strftime("%Y-%m")
        },
        "plan": {
            "id": sub.plan_id,
            "name": plan_name,
            "base_price_cents": base_price_cents
        },
        "subscription_status": sub.status,
        "usage": {
            "api_calls": {
                "used": used_calls,
                "limit": max_calls,
                "percent_used": percent_calls
            },
            "tokens": {
                "total_used": total_tokens,
                "limit": max_tokens,
                "percent_used": percent_tokens,
                "breakdown": {
                    "standard_input": in_tok,
                    "cached_input": cached_tok,
                    "standard_output": out_tok,
                    "reasoning": reason_tok
                }
            }
        },
        "cost": {
            "token_cost_micro_cents": total_micro,
            "token_cost_cents": rollup.token_cost_cents,
            "base_subscription_cents": base_price_cents,
            "total_cents": rollup.total_cents,
            "formatted": rollup.formatted,
            "rounding_rule": "ROUND_HALF_UP_AT_INVOICE"
        }
    }


@router.get("/usage")
async def get_usage_by_header(
    x_tenant_id: str = Header(..., alias="X-Tenant-ID"),
    db: AsyncSession = Depends(get_db)
):
    """Convenience alias endpoint reading tenant ID from header."""
    return await get_tenant_usage(tenant_id=x_tenant_id, db=db)
