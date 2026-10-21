import pytest
from unittest.mock import MagicMock
from datetime import datetime, timezone, timedelta
from src.db.models import Tenant, Plan, Subscription, UsageEvent
from src.workers.reconciliation import run_reconciliation_once

@pytest.mark.asyncio
async def test_worker_reconciles_missed_webhook_subscription(db_session):
    plan = Plan(id="pro", name="Pro Plan", monthly_api_limit=50000, monthly_token_limit=5000000, base_price_cents=2900)
    tenant = Tenant(id="tenant_sync_demo", name="Sync Demo Org", email="syncdemo@example.com")
    now = datetime.now(timezone.utc)
    sub = Subscription(
        tenant_id="tenant_sync_demo",
        plan_id="pro",
        status="active",
        stripe_subscription_id="sub_missed_webhook_01",
        current_period_start=now - timedelta(days=5),
        current_period_end=now + timedelta(days=25)
    )
    db_session.add_all([plan, tenant, sub])
    await db_session.commit()

    # Mock Stripe client returning past_due (simulating a webhook that failed or was dropped)
    mock_stripe_sub = MagicMock()
    mock_stripe_sub.status = "past_due"
    mock_stripe_client = MagicMock()
    mock_stripe_client.Subscription.retrieve.return_value = mock_stripe_sub

    report = await run_reconciliation_once(db_session, stripe_client=mock_stripe_client)
    assert report.synced_count == 1
    assert report.total_checked >= 1

    await db_session.refresh(sub)
    assert sub.status == "past_due"

@pytest.mark.asyncio
async def test_worker_detects_quota_alert_thresholds_80_and_100_percent(db_session):
    plan = Plan(id="free", name="Free Plan", monthly_api_limit=1000, monthly_token_limit=100000, base_price_cents=0)
    now = datetime.now(timezone.utc)

    # Tenant 1: at 850 calls (85% >= 80% warning threshold)
    t1 = Tenant(id="tenant_alert_80", name="Alert 80 Org", email="a80@example.com")
    sub1 = Subscription(
        tenant_id="tenant_alert_80",
        plan_id="free",
        status="active",
        current_period_start=now - timedelta(days=2),
        current_period_end=now + timedelta(days=28)
    )
    event1 = UsageEvent(
        tenant_id="tenant_alert_80",
        event_type="generation",
        api_calls=850,
        cost_micro_cents=0,
        idempotency_key="alert_80_key"
    )

    # Tenant 2: at 1000 calls (100% critical threshold)
    t2 = Tenant(id="tenant_alert_100", name="Alert 100 Org", email="a100@example.com")
    sub2 = Subscription(
        tenant_id="tenant_alert_100",
        plan_id="free",
        status="active",
        current_period_start=now - timedelta(days=2),
        current_period_end=now + timedelta(days=28)
    )
    event2 = UsageEvent(
        tenant_id="tenant_alert_100",
        event_type="generation",
        api_calls=1000,
        cost_micro_cents=0,
        idempotency_key="alert_100_key"
    )

    db_session.add_all([plan, t1, sub1, event1, t2, sub2, event2])
    await db_session.commit()

    report = await run_reconciliation_once(db_session)
    assert len(report.quota_alerts) >= 2

    levels = {a["tenant_id"]: a["level"] for a in report.quota_alerts}
    assert levels.get("tenant_alert_80") == "WARNING_80_PERCENT"
    assert levels.get("tenant_alert_100") == "CRITICAL_100_PERCENT"

@pytest.mark.asyncio
async def test_worker_retries_and_handles_stripe_failure(db_session):
    plan = Plan(id="pro", name="Pro Plan", monthly_api_limit=50000, monthly_token_limit=5000000, base_price_cents=2900)
    tenant = Tenant(id="tenant_fail_retry", name="Fail Retry Org", email="fail@example.com")
    sub = Subscription(
        tenant_id="tenant_fail_retry",
        plan_id="pro",
        status="active",
        stripe_subscription_id="sub_failing_network_01"
    )
    db_session.add_all([plan, tenant, sub])
    await db_session.commit()

    mock_failing_client = MagicMock()
    mock_failing_client.Subscription.retrieve.side_effect = Exception("Simulated Stripe network timeout")

    # Run with max_retries=2
    report = await run_reconciliation_once(db_session, stripe_client=mock_failing_client, max_retries=2, retry_delay=0.01)
    assert len(report.failure_alerts) >= 1
    assert "timeout" in report.failure_alerts[0]["error"].lower()
    assert mock_failing_client.Subscription.retrieve.call_count == 2
