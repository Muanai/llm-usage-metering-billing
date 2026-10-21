import asyncio
import logging
from typing import List, Dict, Any, Optional
import stripe
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings
from src.db.models import Tenant, Subscription, Plan, UsageEvent
from src.db.session import AsyncSessionLocal

logger = logging.getLogger("workers.reconciliation")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


class ReconciliationReport(BaseModel):
    synced_count: int = 0
    total_checked: int = 0
    quota_alerts: List[Dict[str, Any]] = Field(default_factory=list)
    failure_alerts: List[Dict[str, Any]] = Field(default_factory=list)


async def run_reconciliation_once(
    db: AsyncSession,
    stripe_client: Any = None,
    max_retries: int = 3,
    retry_delay: float = 0.5
) -> ReconciliationReport:
    """
    Executes a single reconciliation cycle:
    1. Cross-checks local subscription states against Stripe API to catch dropped webhooks.
    2. Evaluates quota utilization across active tenants, triggering alerts at >=80% and >=100%.
    3. Handles network failures with exponential backoff retries and structured alert logging.
    """
    client = stripe_client if stripe_client is not None else stripe
    report = ReconciliationReport()

    # Part A: Reconcile Stripe Subscriptions
    subs_res = await db.execute(
        select(Subscription).where(Subscription.stripe_subscription_id.isnot(None))
    )
    subscriptions = subs_res.scalars().all()
    report.total_checked = len(subscriptions)

    for sub in subscriptions:
        sub_id = sub.stripe_subscription_id
        retrieved_sub = None

        for attempt in range(1, max_retries + 1):
            try:
                retrieved_sub = client.Subscription.retrieve(sub_id)
                break
            except Exception as e:
                if attempt == max_retries:
                    err_msg = f"Failed to retrieve Stripe subscription '{sub_id}' after {max_retries} attempts: {e}"
                    logger.error(err_msg)
                    report.failure_alerts.append({
                        "tenant_id": sub.tenant_id,
                        "subscription_id": sub_id,
                        "error": str(e)
                    })
                else:
                    await asyncio.sleep(retry_delay * (2 ** (attempt - 1)))

        if retrieved_sub:
            stripe_status = getattr(retrieved_sub, "status", None)
            if stripe_status and stripe_status != sub.status:
                logger.info(
                    "Reconciled tenant '%s' subscription status from '%s' to '%s'",
                    sub.tenant_id, sub.status, stripe_status
                )
                sub.status = stripe_status
                report.synced_count += 1

    # Part B: Evaluate Quota Utilization Alerts (>=80% and >=100%)
    active_subs_res = await db.execute(
        select(Subscription).where(Subscription.status == "active")
    )
    active_subs = active_subs_res.scalars().all()

    for sub in active_subs:
        plan_res = await db.execute(select(Plan).where(Plan.id == sub.plan_id))
        plan = plan_res.scalar_one_or_none()
        if not plan:
            max_calls = 1000 if sub.plan_id == "free" else 50000
            max_tokens = 100000 if sub.plan_id == "free" else 5000000
        else:
            max_calls = plan.monthly_api_limit
            max_tokens = plan.monthly_token_limit

        # Aggregate current usage
        usage_res = await db.execute(
            select(
                func.coalesce(func.sum(UsageEvent.api_calls), 0),
                func.coalesce(
                    func.sum(
                        UsageEvent.input_tokens
                        + UsageEvent.cached_input_tokens
                        + UsageEvent.output_tokens
                        + UsageEvent.reasoning_tokens
                    ),
                    0
                )
            ).where(
                UsageEvent.tenant_id == sub.tenant_id,
                UsageEvent.created_at >= sub.current_period_start,
                UsageEvent.created_at < sub.current_period_end
            )
        )
        used_calls, used_tokens = usage_res.one()

        pct_calls = (used_calls / max_calls) if max_calls > 0 else 0
        pct_tokens = (used_tokens / max_tokens) if max_tokens > 0 else 0

        # Check critical 100% threshold
        if pct_calls >= 1.0 or pct_tokens >= 1.0:
            alert = {
                "tenant_id": sub.tenant_id,
                "level": "CRITICAL_100_PERCENT",
                "used_calls": used_calls,
                "limit_calls": max_calls,
                "used_tokens": used_tokens,
                "limit_tokens": max_tokens
            }
            logger.error("QUOTA ALERT [100%% CRITICAL]: Tenant '%s' has hit or exceeded plan limits.", sub.tenant_id)
            report.quota_alerts.append(alert)
        elif pct_calls >= 0.8 or pct_tokens >= 0.8:
            alert = {
                "tenant_id": sub.tenant_id,
                "level": "WARNING_80_PERCENT",
                "used_calls": used_calls,
                "limit_calls": max_calls,
                "used_tokens": used_tokens,
                "limit_tokens": max_tokens
            }
            logger.warning("QUOTA ALERT [80%% WARNING]: Tenant '%s' is approaching quota threshold.", sub.tenant_id)
            report.quota_alerts.append(alert)

    await db.commit()
    return report


async def run_reconciliation_loop(interval_seconds: int = 60) -> None:
    """
    Continuous background loop started during FastAPI application lifespan.
    Runs off the request path with graceful exception containment.
    """
    logger.info("Starting background reconciliation & quota alert worker loop (interval=%ds)...", interval_seconds)
    while True:
        try:
            async with AsyncSessionLocal() as session:
                await run_reconciliation_once(session)
        except asyncio.CancelledError:
            logger.info("Reconciliation worker loop cancelled gracefully.")
            break
        except Exception as e:
            logger.error("Unexpected error in reconciliation loop: %s", e)

        await asyncio.sleep(interval_seconds)


if __name__ == "__main__":
    async def main():
        async with AsyncSessionLocal() as session:
            res = await run_reconciliation_once(session)
            print(f"[Worker Run] Checked: {res.total_checked}, Synced: {res.synced_count}, Alerts: {len(res.quota_alerts)}")
    asyncio.run(main())
