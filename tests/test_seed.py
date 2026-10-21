import pytest
from sqlalchemy import select, func
from scripts.seed import run_seed
from src.db.models import Tenant, Plan, Subscription, UsageEvent

@pytest.mark.asyncio
async def test_seed_creates_expected_plans_and_fixtures(db_session):
    # Run seed against isolated test db session
    await run_seed(db_session)

    # 1. Verify Plans
    res_free = await db_session.execute(select(Plan).where(Plan.id == "free"))
    free_plan = res_free.scalar_one_or_none()
    assert free_plan is not None
    assert free_plan.monthly_api_limit == 1000
    assert free_plan.monthly_token_limit == 100_000
    assert free_plan.base_price_cents == 0

    res_pro = await db_session.execute(select(Plan).where(Plan.id == "pro"))
    pro_plan = res_pro.scalar_one_or_none()
    assert pro_plan is not None
    assert pro_plan.monthly_api_limit == 50_000
    assert pro_plan.monthly_token_limit == 5_000_000
    assert pro_plan.base_price_cents == 2900

    # 2. Verify Boundary Tenant (seeded with exactly 999 calls)
    res_b = await db_session.execute(
        select(func.sum(UsageEvent.api_calls)).where(UsageEvent.tenant_id == "tenant_free_boundary")
    )
    assert res_b.scalar() == 999

    # 3. Verify Past Due Tenant (status: past_due for 402 path)
    res_pd = await db_session.execute(
        select(Subscription).where(Subscription.tenant_id == "tenant_pro_past_due")
    )
    sub_pd = res_pd.scalar_one_or_none()
    assert sub_pd is not None
    assert sub_pd.status == "past_due"
    assert sub_pd.plan_id == "pro"

    # 4. Verify Pricing Demo Tenant (50k input, 25k cached, 40k output, 10k reasoning)
    res_pr = await db_session.execute(
        select(UsageEvent).where(UsageEvent.tenant_id == "tenant_pricing_demo")
    )
    pr_event = res_pr.scalar_one_or_none()
    assert pr_event is not None
    assert pr_event.input_tokens == 50_000
    assert pr_event.cached_input_tokens == 25_000
    assert pr_event.output_tokens == 40_000
    assert pr_event.reasoning_tokens == 10_000
    assert pr_event.cost_micro_cents == 51_250_000

    # 5. Verify Clean Tenants (0 usage)
    for clean_id in ["tenant_checkout_demo", "tenant_clean"]:
        res_c = await db_session.execute(
            select(func.count(UsageEvent.id)).where(UsageEvent.tenant_id == clean_id)
        )
        assert res_c.scalar() == 0

@pytest.mark.asyncio
async def test_seed_is_idempotent(db_session):
    # Running seed twice must reset cleanly without unique constraint crashes
    await run_seed(db_session)
    await run_seed(db_session)

    res_tenants = await db_session.execute(select(Tenant))
    tenants = res_tenants.scalars().all()
    assert len(tenants) == 5
