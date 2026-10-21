import json
from typing import Optional
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_required_meter_headers, get_db
from src.api.error_handlers import AppException
from src.core.calculator import TokenUsage, calculate_token_cost
from src.core.idempotency import (
    compute_payload_hash,
    check_or_reserve_idempotency,
    save_idempotency_response,
    IdempotencyStatus,
)
from src.core.quota import check_tenant_quota, QuotaDecision
from src.db.models import UsageEvent

router = APIRouter()


class GenerateRequest(BaseModel):
    model: str = Field(default="gemini-1.5-pro", description="AI Model identifier")
    prompt: str = Field(..., description="Prompt text")
    simulated_tokens: Optional[TokenUsage] = Field(
        default_factory=TokenUsage,
        description="Simulated token counts for metering"
    )


@router.post("/generate")
async def generate_billable(
    body: GenerateRequest,
    request: Request,
    headers: tuple[str, str] = Depends(get_required_meter_headers),
    db: AsyncSession = Depends(get_db)
):
    tenant_id, idempotency_key = headers
    path = str(request.url.path)
    payload_hash = compute_payload_hash(body.model_dump())

    # Step 1: Idempotency Check & Reservation
    idemp_res = await check_or_reserve_idempotency(db, tenant_id, idempotency_key, path, payload_hash)

    if idemp_res.status == IdempotencyStatus.REPLAY:
        return Response(
            content=idemp_res.cached_response_body,
            status_code=idemp_res.cached_status_code,
            media_type="application/json"
        )
    elif idemp_res.status == IdempotencyStatus.CONFLICT:
        raise AppException(
            status_code=400,
            code="idempotency_conflict",
            message=idemp_res.error_message or "Idempotency key was used with a different request payload or path."
        )
    elif idemp_res.status == IdempotencyStatus.IN_PROGRESS:
        raise AppException(
            status_code=409,
            code="concurrent_request",
            message=idemp_res.error_message or "A request with this idempotency key is currently processing."
        )

    # Step 2: Quota Pre-Execution Check
    tokens = body.simulated_tokens or TokenUsage()
    requested_tokens = tokens.total_tokens
    quota_res = await check_tenant_quota(
        db,
        tenant_id=tenant_id,
        requested_calls=1,
        requested_tokens=requested_tokens
    )

    if quota_res.status == QuotaDecision.PAYMENT_REQUIRED:
        raise AppException(
            status_code=402,
            code="payment_required",
            message=quota_res.message,
            details={"plan": quota_res.plan_id}
        )
    elif quota_res.status == QuotaDecision.QUOTA_EXCEEDED:
        raise AppException(
            status_code=429,
            code="quota_exceeded",
            message=quota_res.message,
            details={
                "metric": quota_res.metric,
                "current_usage": quota_res.current_usage,
                "limit": quota_res.limit,
                "plan": quota_res.plan_id
            },
            headers={"Retry-After": str(quota_res.retry_after_seconds)}
        )

    # Step 3: Money Math & Pricing Calculation
    cost = calculate_token_cost(tokens)

    # Step 4: Record exactly ONE usage event (PROBE 1)
    event = UsageEvent(
        tenant_id=tenant_id,
        event_type="generation",
        api_calls=1,
        input_tokens=tokens.input_tokens,
        cached_input_tokens=tokens.cached_input_tokens,
        output_tokens=tokens.output_tokens,
        reasoning_tokens=tokens.reasoning_tokens,
        cost_micro_cents=cost.total_micro_cents,
        idempotency_key=idempotency_key
    )
    db.add(event)
    await db.flush()

    # Step 5: Format response payload
    response_data = {
        "id": event.id,
        "tenant_id": tenant_id,
        "status": "success",
        "result": f"Simulated generation from {body.model}.",
        "usage": {
            "api_calls": 1,
            "input_tokens": tokens.input_tokens,
            "cached_input_tokens": tokens.cached_input_tokens,
            "output_tokens": tokens.output_tokens,
            "reasoning_tokens": tokens.reasoning_tokens,
            "total_tokens": tokens.total_tokens
        },
        "cost": {
            "micro_cents": cost.total_micro_cents,
            "cents": cost.billed_cents,
            "exact_dollars": cost.exact_dollars,
            "formatted": cost.formatted_total
        }
    }
    response_json = json.dumps(response_data)

    # Step 6: Complete idempotency record with snapshot
    await save_idempotency_response(db, tenant_id, idempotency_key, 200, response_json)

    return Response(
        content=response_json,
        status_code=200,
        media_type="application/json"
    )
