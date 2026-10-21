import hashlib
import json
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.db.models import IdempotencyKey

class IdempotencyStatus(str, Enum):
    NEW = "new"
    REPLAY = "replay"
    CONFLICT = "conflict"
    IN_PROGRESS = "in_progress"

class IdempotencyCheckResult(BaseModel):
    status: IdempotencyStatus
    cached_status_code: Optional[int] = None
    cached_response_body: Optional[str] = None
    error_message: Optional[str] = None

def compute_payload_hash(payload: Any) -> str:
    """Computes a deterministic SHA-256 hash of the request payload."""
    if isinstance(payload, dict):
        raw = json.dumps(payload, sort_keys=True).encode("utf-8")
    elif isinstance(payload, str):
        raw = payload.encode("utf-8")
    elif isinstance(payload, bytes):
        raw = payload
    else:
        raw = str(payload).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()

async def check_or_reserve_idempotency(
    db: AsyncSession,
    tenant_id: str,
    key: str,
    path: str,
    payload_hash: str
) -> IdempotencyCheckResult:
    """
    Checks if an idempotency key exists for this tenant.
    - If found and completed: returns cached response (REPLAY).
    - If found and still processing: returns IN_PROGRESS.
    - If found with different path or payload: returns CONFLICT.
    - If not found: atomically reserves key as 'processing' (NEW).
    """
    query = select(IdempotencyKey).where(
        IdempotencyKey.tenant_id == tenant_id,
        IdempotencyKey.idempotency_key == key
    )
    result = await db.execute(query)
    stored = result.scalar_one_or_none()

    if stored is not None:
        # Check payload and path integrity
        if stored.request_path != path or stored.request_hash != payload_hash:
            return IdempotencyCheckResult(
                status=IdempotencyStatus.CONFLICT,
                error_message="Idempotency key was already used with a different request path or payload."
            )
        if stored.status == "processing":
            return IdempotencyCheckResult(
                status=IdempotencyStatus.IN_PROGRESS,
                error_message="A request with this idempotency key is currently in progress. Please retry shortly."
            )
        return IdempotencyCheckResult(
            status=IdempotencyStatus.REPLAY,
            cached_status_code=stored.response_status_code,
            cached_response_body=stored.response_body
        )

    # Key is new: reserve key in database
    new_record = IdempotencyKey(
        tenant_id=tenant_id,
        idempotency_key=key,
        request_path=path,
        request_hash=payload_hash,
        status="processing"
    )
    db.add(new_record)
    await db.commit()

    return IdempotencyCheckResult(status=IdempotencyStatus.NEW)

async def save_idempotency_response(
    db: AsyncSession,
    tenant_id: str,
    key: str,
    status_code: int,
    response_body: str
) -> None:
    """Updates reserved idempotency key with final status and response body snapshot."""
    query = select(IdempotencyKey).where(
        IdempotencyKey.tenant_id == tenant_id,
        IdempotencyKey.idempotency_key == key
    )
    result = await db.execute(query)
    record = result.scalar_one_or_none()
    if record:
        record.response_status_code = status_code
        record.response_body = response_body
        record.status = "completed"
        await db.commit()
