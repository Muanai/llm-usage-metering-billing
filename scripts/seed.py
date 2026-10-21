import sys
from pathlib import Path

# Add project root to sys.path so running directly via `python scripts/seed.py` works seamlessly
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import asyncio
import logging
from datetime import datetime, timezone
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from config.settings import settings
from src.db.models import Tenant, Plan, Subscription, UsageEvent, IdempotencyKey, WebhookEvent
from src.db.session import AsyncSessionLocal, init_db

logger = logging.getLogger("seeder")
logging.basicConfig(level=logging.INFO)

async def provision_or_lookup_stripe_price(stripe_client=None) -> str:
    """
    Looks up or provisions the Pro Plan price in Stripe test mode.
    Falls back gracefully to a deterministic mock price if offline or key is missing.
    """
    key = settings.STRIPE_SECRET_KEY
    if not key or key.startswith("sk_test_placeholder") or "your_stripe" in key:
        logger.info("Using deterministic mock Stripe price ID (offline mode).")
        return "price_flyrank_pro_test"

    try:
        import stripe
        client = stripe_client or stripe
        client.api_key = key

        # Look up existing price with lookup_key
        prices = client.Price.list(lookup_keys=["flyrank_pro_monthly"], active=True)
        if prices.data:
            logger.info("Found existing Stripe Price for flyrank_pro_monthly: %s", prices.data[0].id)
            return prices.data[0].id

        # Create product & price if not existing
        logger.info("Creating new Stripe test product & recurring price ($29/mo)...")
        product = client.Product.create(
            name="FlyRank Pro Plan",
            metadata={"plan_id": "pro", "service": "llm-metering-engine"}
        )
        price = client.Price.create(
            product=product.id,
            unit_amount=2900,
            currency="usd",
            recurring={"interval": "month"},
            lookup_key="flyrank_pro_monthly"
        )
        logger.info("Provisioned Stripe Price: %s", price.id)
        return price.id
    except Exception as e:
        logger.warning("Could not provision live Stripe price (%s). Falling back to mock test price ID.", e)
        return "price_flyrank_pro_test"

async def run_seed(db: AsyncSession, stripe_client=None) -> None:
    """
    Seeds plans and deterministic test fixtures required by evaluation probes.
    Execution is completely idempotent.
    """
    now = datetime.now(timezone.utc)
    first_day = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if now.month == 12:
        next_month = now.replace(year=now.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        next_month = now.replace(month=now.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)

    # 1. Seed or update Plans
    pro_stripe_price_id = await provision_or_lookup_stripe_price(stripe_client)

    plans_data = [
        {
            "id": "free",
            "name": "Free",
            "monthly_api_limit": 1000,
            "monthly_token_limit": 100_000,
            "base_price_cents": 0,
            "stripe_price_id": None
        },
        {
            "id": "pro",
            "name": "Pro",
            "monthly_api_limit": 50_000,
            "monthly_token_limit": 5_000_000,
            "base_price_cents": 2900,
            "stripe_price_id": pro_stripe_price_id
        }
    ]

    for p in plans_data:
        existing_p = (await db.execute(select(Plan).where(Plan.id == p["id"]))).scalar_one_or_none()
        if existing_p:
            existing_p.name = p["name"]
            existing_p.monthly_api_limit = p["monthly_api_limit"]
            existing_p.monthly_token_limit = p["monthly_token_limit"]
            existing_p.base_price_cents = p["base_price_cents"]
            existing_p.stripe_price_id = p["stripe_price_id"]
        else:
            db.add(Plan(**p))

    await db.flush()

    # 2. Define Deterministic Fixtures
    fixtures = [
        {
            "id": "tenant_free_boundary",
            "name": "Boundary Test Org",
            "email": "boundary@example.com",
            "plan_id": "free",
            "status": "active",
            "events": [
                {
                    "event_type": "generation",
                    "api_calls": 999,
                    "input_tokens": 0,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_tokens": 0,
                    "cost_micro_cents": 0,
                    "idempotency_key": "seed_boundary_999"
                }
            ]
        },
        {
            "id": "tenant_pro_past_due",
            "name": "Past Due Test Org",
            "email": "pastdue@example.com",
            "plan_id": "pro",
            "status": "past_due",
            "events": []
        },
        {
            "id": "tenant_checkout_demo",
            "name": "Checkout Demo Org",
            "email": "checkout@example.com",
            "plan_id": "free",
            "status": "active",
            "events": []
        },
        {
            "id": "tenant_pricing_demo",
            "name": "Pricing Demo Org",
            "email": "pricing@example.com",
            "plan_id": "pro",
            "status": "active",
            "events": [
                {
                    "event_type": "generation",
                    "api_calls": 1,
                    "input_tokens": 50_000,
                    "cached_input_tokens": 25_000,
                    "output_tokens": 40_000,
                    "reasoning_tokens": 10_000,
                    "cost_micro_cents": 51_250_000,
                    "idempotency_key": "seed_pricing_demo_001"
                }
            ]
        },
        {
            "id": "tenant_clean",
            "name": "Clean Demo Org",
            "email": "clean@example.com",
            "plan_id": "free",
            "status": "active",
            "events": []
        }
    ]

    for f in fixtures:
        t_id = f["id"]

        # Reset Tenant
        tenant = (await db.execute(select(Tenant).where(Tenant.id == t_id))).scalar_one_or_none()
        if not tenant:
            tenant = Tenant(id=t_id, name=f["name"], email=f["email"])
            db.add(tenant)
        else:
            tenant.name = f["name"]
            tenant.email = f["email"]

        # Reset Subscription
        sub = (await db.execute(select(Subscription).where(Subscription.tenant_id == t_id))).scalar_one_or_none()
        if not sub:
            sub = Subscription(
                tenant_id=t_id,
                plan_id=f["plan_id"],
                status=f["status"],
                current_period_start=first_day,
                current_period_end=next_month
            )
            db.add(sub)
        else:
            sub.plan_id = f["plan_id"]
            sub.status = f["status"]
            sub.current_period_start = first_day
            sub.current_period_end = next_month

        # Clear existing usage events and idempotency keys to ensure pristine baseline
        await db.execute(delete(UsageEvent).where(UsageEvent.tenant_id == t_id))
        await db.execute(delete(IdempotencyKey).where(IdempotencyKey.tenant_id == t_id))

        # Insert seeded events
        for ev in f["events"]:
            db.add(UsageEvent(
                tenant_id=t_id,
                created_at=now,
                **ev
            ))

    # Also clear processed webhook events so test runs start fresh
    await db.execute(delete(WebhookEvent))
    await db.commit()
    logger.info("Successfully seeded plans and 5 deterministic fixtures.")

async def main():
    await init_db()
    async with AsyncSessionLocal() as session:
        await run_seed(session)

if __name__ == "__main__":
    asyncio.run(main())
