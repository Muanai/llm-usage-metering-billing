import pytest
from unittest.mock import patch
from datetime import datetime, timezone, timedelta
from src.db.models import Tenant, Plan, Subscription, WebhookEvent

@pytest.mark.asyncio
async def test_forged_webhook_signature_returns_400(async_client):
    res = await async_client.post(
        "/api/v1/billing/webhooks/stripe",
        content=b'{"id": "evt_forged_01"}',
        headers={"Stripe-Signature": "t=12345,v1=bad_signature_hash"}
    )
    assert res.status_code == 400
    data = res.json()
    assert data["error"]["code"] == "invalid_webhook_signature"

@pytest.mark.asyncio
async def test_checkout_session_creation(async_client, db_session):
    plan = Plan(id="pro", name="Pro Plan", monthly_api_limit=50000, monthly_token_limit=5000000, base_price_cents=2900)
    tenant = Tenant(id="tenant_checkout_user", name="Checkout Org", email="user@example.com")
    sub = Subscription(tenant_id="tenant_checkout_user", plan_id="free", status="active")
    db_session.add_all([plan, tenant, sub])
    await db_session.commit()

    res = await async_client.post(
        "/api/v1/billing/checkout",
        json={"tenant_id": "tenant_checkout_user", "plan_id": "pro"}
    )
    assert res.status_code == 200
    data = res.json()
    assert "checkout_url" in data
    assert "session_id" in data
    assert "cs_test" in data["session_id"]

@pytest.mark.asyncio
async def test_webhook_checkout_completed_flips_free_to_pro_and_deduplicates(async_client, db_session):
    plan_pro = Plan(id="pro", name="Pro Plan", monthly_api_limit=50000, monthly_token_limit=5000000, base_price_cents=2900)
    tenant = Tenant(id="tenant_flip_test", name="Flip Org", email="flip@example.com")
    now = datetime.now(timezone.utc)
    sub = Subscription(
        tenant_id="tenant_flip_test",
        plan_id="free",
        status="active",
        current_period_start=now - timedelta(days=2),
        current_period_end=now + timedelta(days=28)
    )
    db_session.add_all([plan_pro, tenant, sub])
    await db_session.commit()

    mock_event = {
        "id": "evt_test_checkout_flip_001",
        "type": "checkout.session.completed",
        "created": int(now.timestamp()),
        "data": {
            "object": {
                "client_reference_id": "tenant_flip_test",
                "customer": "cus_flip_123",
                "subscription": "sub_flip_123",
                "metadata": {"tenant_id": "tenant_flip_test", "plan_id": "pro"}
            }
        }
    }

    with patch("stripe.Webhook.construct_event", return_value=mock_event):
        headers = {"Stripe-Signature": "t=1000,v1=valid_sig"}
        # 1. First webhook delivery: Flip Free -> Pro (PROBE 3)
        r1 = await async_client.post("/api/v1/billing/webhooks/stripe", json={}, headers=headers)
        assert r1.status_code == 200
        assert r1.json()["status"] == "success"

        await db_session.refresh(sub)
        assert sub.plan_id == "pro"
        assert sub.status == "active"
        assert sub.stripe_subscription_id == "sub_flip_123"

        # 2. Second webhook delivery (Replay attack / network duplicate) -> Ignored (PROBE 4)
        r2 = await async_client.post("/api/v1/billing/webhooks/stripe", json={}, headers=headers)
        assert r2.status_code == 200
        assert r2.json()["status"] == "ignored"
        assert r2.json()["reason"] == "duplicate_event"

@pytest.mark.asyncio
async def test_webhook_subscription_updated_and_deleted(async_client, db_session):
    plan_pro = Plan(id="pro", name="Pro Plan", monthly_api_limit=50000, monthly_token_limit=5000000, base_price_cents=2900)
    tenant = Tenant(id="tenant_sub_updates", name="Sub Updates Org", email="updates@example.com")
    now = datetime.now(timezone.utc)
    sub = Subscription(
        tenant_id="tenant_sub_updates",
        plan_id="pro",
        status="active",
        stripe_subscription_id="sub_updates_123",
        current_period_start=now - timedelta(days=5),
        current_period_end=now + timedelta(days=25)
    )
    db_session.add_all([plan_pro, tenant, sub])
    await db_session.commit()

    # 1. Update event: status changes to past_due
    update_event = {
        "id": "evt_update_past_due_001",
        "type": "customer.subscription.updated",
        "created": int(now.timestamp()) + 10,
        "data": {
            "object": {
                "id": "sub_updates_123",
                "customer": "cus_updates_123",
                "status": "past_due",
                "metadata": {"tenant_id": "tenant_sub_updates"}
            }
        }
    }

    with patch("stripe.Webhook.construct_event", return_value=update_event):
        headers = {"Stripe-Signature": "t=2000,v1=valid_sig"}
        r1 = await async_client.post("/api/v1/billing/webhooks/stripe", json={}, headers=headers)
        assert r1.status_code == 200

        await db_session.refresh(sub)
        assert sub.status == "past_due"

    # 2. Delete event: reverts to Free plan
    delete_event = {
        "id": "evt_delete_001",
        "type": "customer.subscription.deleted",
        "created": int(now.timestamp()) + 20,
        "data": {
            "object": {
                "id": "sub_updates_123",
                "status": "canceled",
                "metadata": {"tenant_id": "tenant_sub_updates"}
            }
        }
    }

    with patch("stripe.Webhook.construct_event", return_value=delete_event):
        headers = {"Stripe-Signature": "t=3000,v1=valid_sig"}
        r2 = await async_client.post("/api/v1/billing/webhooks/stripe", json={}, headers=headers)
        assert r2.status_code == 200

        await db_session.refresh(sub)
        assert sub.plan_id == "free"

@pytest.mark.asyncio
async def test_create_checkout_session_live_path_resolves_plan(db_session, monkeypatch):
    from src.core.stripe_service import create_checkout_session
    from unittest.mock import MagicMock
    from config.settings import settings

    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_real_simulated_key")
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")

    plan = Plan(id="pro_live", name="Pro Live", monthly_api_limit=50000, monthly_token_limit=5000000, base_price_cents=2900, stripe_price_id="price_live_123")
    tenant = Tenant(id="tenant_live_checkout", name="Live Org", email="live@example.com")
    db_session.add_all([plan, tenant])
    await db_session.commit()

    mock_session = MagicMock()
    mock_session.url = "https://checkout.stripe.com/pay/live_session"
    mock_session.id = "cs_live_123"

    with patch("stripe.checkout.Session.create", return_value=mock_session) as mock_create:
        res = await create_checkout_session(db_session, "tenant_live_checkout", "pro_live")
        assert res["session_id"] == "cs_live_123"
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["line_items"][0]["price"] == "price_live_123"

