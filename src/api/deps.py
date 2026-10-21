from fastapi import Request
from src.api.error_handlers import AppException
from src.db.session import get_db

async def get_required_meter_headers(request: Request) -> tuple[str, str]:
    """
    Extracts and validates X-Tenant-ID and Idempotency-Key headers.
    Returns 400 Bad Request with standardized error envelope if either is absent.
    """
    tenant_id = request.headers.get("X-Tenant-ID")
    if not tenant_id or not tenant_id.strip():
        raise AppException(
            status_code=400,
            code="missing_header",
            message="Required header 'X-Tenant-ID' is missing.",
            details={"required_header": "X-Tenant-ID"}
        )

    idempotency_key = request.headers.get("Idempotency-Key")
    if not idempotency_key or not idempotency_key.strip():
        raise AppException(
            status_code=400,
            code="missing_header",
            message="Required header 'Idempotency-Key' is missing.",
            details={"required_header": "Idempotency-Key"}
        )

    return tenant_id.strip(), idempotency_key.strip()
