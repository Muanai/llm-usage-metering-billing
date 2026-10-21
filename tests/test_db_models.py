import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from src.db.models import Tenant, Plan, Subscription, UsageEvent, IdempotencyKey, WebhookEvent

@pytest.mark.asyncio
async def test_create_and_query_tenant_and_plan(db_session):
    plan = Plan(
        id="free",
        name="Free Plan",
        monthly_api_limit=1000,
        monthly_token_limit=100000,
        base_price_cents=0
    )
    tenant = Tenant(id="tenant_alpha", name="Alpha Org", email="alpha@example.com")
    db_session.add_all([plan, tenant])
    await db_session.commit()

    result = await db_session.execute(select(Tenant).where(Tenant.id == "tenant_alpha"))
    fetched_tenant = result.scalar_one_or_none()
    assert fetched_tenant is not None
    assert fetched_tenant.name == "Alpha Org"

@pytest.mark.asyncio
async def test_subscription_creation_and_defaults(db_session):
    plan = Plan(id="pro", name="Pro Plan", monthly_api_limit=50000, monthly_token_limit=5000000, base_price_cents=2900)
    tenant = Tenant(id="tenant_beta", name="Beta Org", email="beta@example.com")
    now = datetime.now(timezone.utc)
    sub = Subscription(
        tenant_id="tenant_beta",
        plan_id="pro",
        status="active",
        stripe_customer_id="cus_test_123",
        stripe_subscription_id="sub_test_123",
        current_period_start=now,
        current_period_end=now + timedelta(days=30)
    )
    db_session.add_all([plan, tenant, sub])
    await db_session.commit()

    res = await db_session.execute(select(Subscription).where(Subscription.tenant_id == "tenant_beta"))
    fetched_sub = res.scalar_one_or_none()
    assert fetched_sub is not None
    assert fetched_sub.plan_id == "pro"
    assert fetched_sub.status == "active"

@pytest.mark.asyncio
async def test_usage_event_money_math_and_idempotency_keys(db_session):
    tenant = Tenant(id="tenant_gamma", name="Gamma Org", email="gamma@example.com")
    db_session.add(tenant)
    await db_session.flush()

    event = UsageEvent(
        tenant_id="tenant_gamma",
        event_type="generation",
        api_calls=1,
        input_tokens=1000,
        cached_input_tokens=500,
        output_tokens=400,
        reasoning_tokens=100,
        cost_micro_cents=625000,
        idempotency_key="idemp_test_key_01"
    )
    key_record = IdempotencyKey(
        tenant_id="tenant_gamma",
        idempotency_key="idemp_test_key_01",
        request_path="/api/v1/generate",
        request_hash="sha256_mock_hash",
        response_status_code=200,
        response_body='{"status": "success"}'
    )
    db_session.add_all([event, key_record])
    await db_session.commit()

    res_event = await db_session.execute(select(UsageEvent).where(UsageEvent.idempotency_key == "idemp_test_key_01"))
    saved_event = res_event.scalar_one_or_none()
    assert saved_event is not None
    assert saved_event.cost_micro_cents == 625000
    assert saved_event.api_calls == 1
