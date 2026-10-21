from fastapi import APIRouter, Depends, Request, Header
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from src.api.deps import get_db
from src.api.error_handlers import AppException
from src.core.stripe_service import (
    create_checkout_session,
    verify_webhook_signature,
    process_webhook_event
)

router = APIRouter(prefix="/billing", tags=["Billing & Stripe"])


class CheckoutRequest(BaseModel):
    tenant_id: str = Field(..., description="Target tenant ID")
    plan_id: str = Field(default="pro", description="Plan to subscribe to")


@router.post("/checkout")
async def checkout(body: CheckoutRequest, db: AsyncSession = Depends(get_db)):
    """Creates a Stripe subscription checkout session."""
    try:
        session_info = await create_checkout_session(db, body.tenant_id, body.plan_id)
        return session_info
    except ValueError as e:
        raise AppException(
            status_code=404,
            code="tenant_not_found",
            message=str(e),
            details={"tenant_id": body.tenant_id}
        )


@router.post("/webhooks/stripe")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(None, alias="Stripe-Signature"),
    db: AsyncSession = Depends(get_db)
):
    """
    Receives and processes signed Stripe webhooks.
    Raw request body is cryptographically verified to prevent forgery.
    """
    raw_payload = await request.body()

    try:
        event = verify_webhook_signature(raw_payload, stripe_signature)
    except Exception as e:
        raise AppException(
            status_code=400,
            code="invalid_webhook_signature",
            message="Stripe webhook signature verification failed.",
            details={"error": str(e)}
        )

    result = await process_webhook_event(db, event)
    return result
