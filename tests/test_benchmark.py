import pytest
import pytest_asyncio
import asyncio
from datetime import datetime, timezone
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
from src.main import app
from src.db.session import get_db
from src.db.models import Tenant, Plan, Subscription, UsageEvent, IdempotencyKey
from scripts.benchmark import calculate_percentiles

@pytest_asyncio.fixture
async def concurrent_client(test_engine):
    """Provides an AsyncClient where each concurrent request gets its own session instance from the pool."""
    session_maker = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)
    async def override_get_db():
        async with session_maker() as session:
            yield session
    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_concurrency_quota_boundary_zero_leakage(concurrent_client, db_session):
    """
    Validates that concurrent requests at quota boundary maintain 100% integrity (0 leakage).
    Initial: 995 calls / 1,000 limit.
    Concurrent burst: 15 requests.
    Expected: Exactly 5 admitted (200), 10 blocked (429), DB total == 1,000.
    """
    now = datetime.now(timezone.utc)
    first_day = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_month = now.replace(year=now.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0) if now.month == 12 else now.replace(month=now.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)

    # 1. Setup Tenant and Plan
    plan = (await db_session.execute(select(Plan).where(Plan.id == "free"))).scalar_one_or_none()
    if not plan:
        plan = Plan(id="free", name="Free", monthly_api_limit=1000, monthly_token_limit=100_000, base_price_cents=0)
        db_session.add(plan)

    tenant_id = "tenant_test_burst"
    tenant = (await db_session.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one_or_none()
    if not tenant:
        tenant = Tenant(id=tenant_id, name="Test Burst Org", email="burst@example.com")
        db_session.add(tenant)

    sub = (await db_session.execute(select(Subscription).where(Subscription.tenant_id == tenant_id))).scalar_one_or_none()
    if not sub:
        sub = Subscription(
            tenant_id=tenant_id,
            plan_id="free",
            status="active",
            current_period_start=first_day,
            current_period_end=next_month
        )
        db_session.add(sub)
    else:
        sub.status = "active"
        sub.current_period_start = first_day
        sub.current_period_end = next_month

    await db_session.execute(delete(UsageEvent).where(UsageEvent.tenant_id == tenant_id))
    await db_session.execute(delete(IdempotencyKey).where(IdempotencyKey.tenant_id == tenant_id))

    # Seed 995 calls
    seed_event = UsageEvent(
        tenant_id=tenant_id,
        event_type="generation",
        api_calls=995,
        input_tokens=0,
        cached_input_tokens=0,
        output_tokens=0,
        reasoning_tokens=0,
        cost_micro_cents=0,
        idempotency_key="seed_burst_995",
        created_at=now
    )
    db_session.add(seed_event)
    await db_session.commit()

    # 2. Fire 15 concurrent requests
    payload = {
        "model": "gemini-1.5-pro",
        "prompt": "burst benchmark",
        "simulated_tokens": {"input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 10, "reasoning_tokens": 0}
    }

    async def send_req(idx: int):
        headers = {
            "X-Tenant-ID": tenant_id,
            "Idempotency-Key": f"test-burst-key-{idx}",
            "Content-Type": "application/json"
        }
        return await concurrent_client.post("/api/v1/generate", json=payload, headers=headers)

    tasks = [send_req(i) for i in range(15)]
    responses = await asyncio.gather(*tasks)

    # 3. Analyze status codes
    status_counts = {}
    for r in responses:
        status_counts[r.status_code] = status_counts.get(r.status_code, 0) + 1

    assert status_counts.get(200) == 5, f"Expected exactly 5 admitted requests, got {status_counts.get(200)}"
    assert status_counts.get(429) == 10, f"Expected exactly 10 rejected requests, got {status_counts.get(429)}"

    # 4. Verify database total usage
    res = await db_session.execute(
        select(func.sum(UsageEvent.api_calls)).where(UsageEvent.tenant_id == tenant_id)
    )
    total_calls = res.scalar()
    assert total_calls == 1000, f"Expected exactly 1,000 calls in database, got {total_calls} (quota leak!)"

def test_calculate_percentiles():
    latencies = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
    p = calculate_percentiles(latencies)
    assert p["min"] == 10.0
    assert p["max"] == 100.0
    assert p["p50"] == 55.0
    assert p["p90"] == 91.0
