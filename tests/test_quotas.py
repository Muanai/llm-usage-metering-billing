import pytest
from datetime import datetime, timezone, timedelta
from src.db.models import Tenant, Plan, Subscription, UsageEvent
from src.core.quota import check_tenant_quota, QuotaDecision

@pytest.mark.asyncio
async def test_past_due_subscription_returns_402_payment_required(db_session):
    plan = Plan(id="pro", name="Pro", monthly_api_limit=50000, monthly_token_limit=5000000, base_price_cents=2900)
    tenant = Tenant(id="tenant_past_due_test", name="Past Due Org", email="pastdue@example.com")
    now = datetime.now(timezone.utc)
    sub = Subscription(
        tenant_id="tenant_past_due_test",
        plan_id="pro",
        status="past_due",
        current_period_start=now - timedelta(days=5),
        current_period_end=now + timedelta(days=25)
    )
    db_session.add_all([plan, tenant, sub])
    await db_session.commit()

    decision = await check_tenant_quota(db_session, "tenant_past_due_test", requested_calls=1)
    assert decision.status == QuotaDecision.PAYMENT_REQUIRED
    assert decision.http_status == 402
    assert "past_due" in decision.message.lower()

@pytest.mark.asyncio
async def test_exact_quota_boundary_999_to_1000_allowed_and_1001_blocked(db_session):
    plan = Plan(id="free", name="Free", monthly_api_limit=1000, monthly_token_limit=100000, base_price_cents=0)
    tenant = Tenant(id="tenant_boundary_test", name="Boundary Org", email="boundary@example.com")
    now = datetime.now(timezone.utc)
    sub = Subscription(
        tenant_id="tenant_boundary_test",
        plan_id="free",
        status="active",
        current_period_start=now - timedelta(days=5),
        current_period_end=now + timedelta(days=25)
    )
    db_session.add_all([plan, tenant, sub])

    # Seed exactly 999 API calls in current cycle
    seed_event = UsageEvent(
        tenant_id="tenant_boundary_test",
        event_type="generation",
        api_calls=999,
        input_tokens=1000,
        cached_input_tokens=0,
        output_tokens=500,
        reasoning_tokens=0,
        cost_micro_cents=500000,
        idempotency_key="seed_999_key"
    )
    db_session.add(seed_event)
    await db_session.commit()

    # 1. At 999: requesting 1 call -> 999 + 1 = 1000 <= 1000 -> ALLOWED (200)
    decision1 = await check_tenant_quota(db_session, "tenant_boundary_test", requested_calls=1, requested_tokens=0)
    assert decision1.status == QuotaDecision.ALLOWED
    assert decision1.http_status == 200

    # Simulate recording the 1000th call
    e1000 = UsageEvent(
        tenant_id="tenant_boundary_test",
        event_type="generation",
        api_calls=1,
        input_tokens=0,
        cached_input_tokens=0,
        output_tokens=0,
        reasoning_tokens=0,
        cost_micro_cents=0,
        idempotency_key="call_1000_key"
    )
    db_session.add(e1000)
    await db_session.commit()

    # 2. At 1000: requesting next call -> 1000 + 1 = 1001 > 1000 -> QUOTA_EXCEEDED (429)
    decision2 = await check_tenant_quota(db_session, "tenant_boundary_test", requested_calls=1, requested_tokens=0)
    assert decision2.status == QuotaDecision.QUOTA_EXCEEDED
    assert decision2.http_status == 429
    assert decision2.retry_after_seconds > 0
    assert "limit reached" in decision2.message.lower() or "exceeded" in decision2.message.lower()

@pytest.mark.asyncio
async def test_token_quota_exceeded_returns_429(db_session):
    plan = Plan(id="free", name="Free", monthly_api_limit=1000, monthly_token_limit=100000, base_price_cents=0)
    tenant = Tenant(id="tenant_tokens_test", name="Tokens Org", email="tokens@example.com")
    now = datetime.now(timezone.utc)
    sub = Subscription(
        tenant_id="tenant_tokens_test",
        plan_id="free",
        status="active",
        current_period_start=now - timedelta(days=2),
        current_period_end=now + timedelta(days=28)
    )
    db_session.add_all([plan, tenant, sub])
    # Seed 95,000 tokens
    event = UsageEvent(
        tenant_id="tenant_tokens_test",
        event_type="generation",
        api_calls=10,
        input_tokens=95000,
        cost_micro_cents=19000000,
        idempotency_key="seed_tokens"
    )
    db_session.add(event)
    await db_session.commit()

    # Requesting 10,000 tokens -> 95,000 + 10,000 = 105,000 > 100,000 -> QUOTA_EXCEEDED (429)
    decision = await check_tenant_quota(db_session, "tenant_tokens_test", requested_calls=1, requested_tokens=10000)
    assert decision.status == QuotaDecision.QUOTA_EXCEEDED
    assert decision.http_status == 429
    assert decision.metric == "tokens"
