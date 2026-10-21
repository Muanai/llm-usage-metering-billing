import pytest
from src.core.idempotency import (
    compute_payload_hash,
    check_or_reserve_idempotency,
    save_idempotency_response,
    IdempotencyStatus
)
from src.db.models import Tenant

@pytest.mark.asyncio
async def test_idempotency_lifecycle_new_and_replay(db_session):
    tenant = Tenant(id="tenant_idemp_01", name="Idemp Org", email="idemp@example.com")
    db_session.add(tenant)
    await db_session.commit()

    key = "req_key_001"
    path = "/api/v1/generate"
    hash_val = compute_payload_hash({"prompt": "Hello world", "tokens": 100})

    # 1. First attempt: Reserve key
    res1 = await check_or_reserve_idempotency(db_session, "tenant_idemp_01", key, path, hash_val)
    assert res1.status == IdempotencyStatus.NEW

    # 2. Complete and save response
    await save_idempotency_response(
        db_session,
        "tenant_idemp_01",
        key,
        status_code=200,
        response_body='{"result": "generated_text", "cost": 100}'
    )

    # 3. Second attempt with same key, path, and payload -> REPLAY
    res2 = await check_or_reserve_idempotency(db_session, "tenant_idemp_01", key, path, hash_val)
    assert res2.status == IdempotencyStatus.REPLAY
    assert res2.cached_status_code == 200
    assert 'generated_text' in res2.cached_response_body

@pytest.mark.asyncio
async def test_idempotency_payload_mismatch_conflict(db_session):
    tenant = Tenant(id="tenant_idemp_02", name="Idemp Org 2", email="idemp2@example.com")
    db_session.add(tenant)
    await db_session.commit()

    key = "req_key_conflict"
    path = "/api/v1/generate"
    hash1 = compute_payload_hash({"prompt": "first prompt"})
    hash2 = compute_payload_hash({"prompt": "completely different prompt"})

    # Reserve and complete first request
    res1 = await check_or_reserve_idempotency(db_session, "tenant_idemp_02", key, path, hash1)
    assert res1.status == IdempotencyStatus.NEW
    await save_idempotency_response(db_session, "tenant_idemp_02", key, 200, '{"result": "ok"}')

    # Re-send same key with modified body -> CONFLICT
    res2 = await check_or_reserve_idempotency(db_session, "tenant_idemp_02", key, path, hash2)
    assert res2.status == IdempotencyStatus.CONFLICT
    assert "different" in res2.error_message.lower()

@pytest.mark.asyncio
async def test_idempotency_concurrent_in_progress(db_session):
    tenant = Tenant(id="tenant_idemp_03", name="Idemp Org 3", email="idemp3@example.com")
    db_session.add(tenant)
    await db_session.commit()

    key = "req_key_concurrent"
    path = "/api/v1/generate"
    hash_val = compute_payload_hash({"prompt": "long running prompt"})

    # First request reserves key but has not called save_idempotency_response yet (still 'processing')
    res1 = await check_or_reserve_idempotency(db_session, "tenant_idemp_03", key, path, hash_val)
    assert res1.status == IdempotencyStatus.NEW

    # Second concurrent request arrives with same key -> IN_PROGRESS
    res2 = await check_or_reserve_idempotency(db_session, "tenant_idemp_03", key, path, hash_val)
    assert res2.status == IdempotencyStatus.IN_PROGRESS
