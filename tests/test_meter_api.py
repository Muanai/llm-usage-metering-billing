import pytest
from datetime import datetime, timezone, timedelta
from src.db.models import Tenant, Plan, Subscription, UsageEvent

@pytest.mark.asyncio
async def test_healthcheck(async_client):
    res = await async_client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "healthy"}

@pytest.mark.asyncio
async def test_generate_missing_headers_returns_clean_400(async_client):
    # Missing both headers
    r1 = await async_client.post("/api/v1/generate", json={"prompt": "test"})
    assert r1.status_code == 400
    d1 = r1.json()
    assert d1["error"]["code"] == "missing_header"
    assert "X-Tenant-ID" in d1["error"]["message"] or "Idempotency-Key" in d1["error"]["message"]

    # Missing Idempotency-Key only
    r2 = await async_client.post("/api/v1/generate", json={"prompt": "test"}, headers={"X-Tenant-ID": "t1"})
    assert r2.status_code == 400
    d2 = r2.json()
    assert d2["error"]["code"] == "missing_header"
    assert "Idempotency-Key" in d2["error"]["message"]

@pytest.mark.asyncio
async def test_generate_billable_call_and_idempotent_replay(async_client, db_session):
    plan = Plan(id="free", name="Free Plan", monthly_api_limit=1000, monthly_token_limit=100000, base_price_cents=0)
    tenant = Tenant(id="tenant_api_1", name="API Org 1", email="api1@example.com")
    now = datetime.now(timezone.utc)
    sub = Subscription(
        tenant_id="tenant_api_1",
        plan_id="free",
        status="active",
        current_period_start=now - timedelta(days=1),
        current_period_end=now + timedelta(days=29)
    )
    db_session.add_all([plan, tenant, sub])
    await db_session.commit()

    payload = {
        "model": "gemini-1.5-pro",
        "prompt": "Summarize this quarterly financial statement",
        "simulated_tokens": {
            "input_tokens": 1000,
            "cached_input_tokens": 500,
            "output_tokens": 400,
            "reasoning_tokens": 100
        }
    }
    headers = {
        "X-Tenant-ID": "tenant_api_1",
        "Idempotency-Key": "idemp_api_test_001"
    }

    # 1. First execution
    r1 = await async_client.post("/api/v1/generate", json=payload, headers=headers)
    assert r1.status_code == 200
    data1 = r1.json()
    assert data1["status"] == "success"
    assert data1["tenant_id"] == "tenant_api_1"
    assert data1["usage"]["api_calls"] == 1
    assert data1["usage"]["total_tokens"] == 2000
    assert data1["cost"]["micro_cents"] == 625000

    # 2. Second retried execution with exact same key -> REPLAY (PROBE 1)
    r2 = await async_client.post("/api/v1/generate", json=payload, headers=headers)
    assert r2.status_code == 200
    data2 = r2.json()
    assert data2 == data1

    # 3. Third call with same key but DIFFERENT prompt -> CONFLICT (400)
    payload_modified = payload.copy()
    payload_modified["prompt"] = "different prompt"
    r3 = await async_client.post("/api/v1/generate", json=payload_modified, headers=headers)
    assert r3.status_code == 400
    assert r3.json()["error"]["code"] == "idempotency_conflict"

@pytest.mark.asyncio
async def test_usage_rollup_endpoint(async_client, db_session):
    plan = Plan(id="pro", name="Pro Plan", monthly_api_limit=50000, monthly_token_limit=5000000, base_price_cents=2900)
    tenant = Tenant(id="tenant_rollup_demo", name="Rollup Org", email="rollup@example.com")
    now = datetime.now(timezone.utc)
    sub = Subscription(
        tenant_id="tenant_rollup_demo",
        plan_id="pro",
        status="active",
        current_period_start=now - timedelta(days=2),
        current_period_end=now + timedelta(days=28)
    )
    # Seed 50k input, 25k cached, 40k output, 10k reasoning
    event = UsageEvent(
        tenant_id="tenant_rollup_demo",
        event_type="generation",
        api_calls=5,
        input_tokens=50000,
        cached_input_tokens=25000,
        output_tokens=40000,
        reasoning_tokens=10000,
        cost_micro_cents=51250000,
        idempotency_key="rollup_event_key"
    )
    db_session.add_all([plan, tenant, sub, event])
    await db_session.commit()

    res = await async_client.get("/api/v1/tenants/tenant_rollup_demo/usage")
    assert res.status_code == 200
    data = res.json()
    assert data["tenant_id"] == "tenant_rollup_demo"
    assert data["plan"]["id"] == "pro"
    assert data["usage"]["api_calls"]["used"] == 5
    assert data["usage"]["tokens"]["total_used"] == 125000
    assert data["usage"]["tokens"]["breakdown"]["standard_input"] == 50000
    assert data["usage"]["tokens"]["breakdown"]["cached_input"] == 25000
    assert data["usage"]["tokens"]["breakdown"]["standard_output"] == 40000
    assert data["usage"]["tokens"]["breakdown"]["reasoning"] == 10000
    assert data["cost"]["token_cost_micro_cents"] == 51250000
    assert data["cost"]["token_cost_cents"] == 51
    assert data["cost"]["base_subscription_cents"] == 2900
    assert data["cost"]["total_cents"] == 2951
    assert data["cost"]["formatted"] == "$29.51"
