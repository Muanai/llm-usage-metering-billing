from typing import Any, Dict, Optional
from fastapi import Request, FastAPI
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

class AppException(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None
    ):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
        self.headers = headers or {}
        super().__init__(message)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException):
        return JSONResponse(
            status_code=exc.status_code,
            headers=exc.headers,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details
                }
            }
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        # Extract structured code if detail is a dict, or fallback to status phrase
        code = "http_error"
        message = str(exc.detail)
        details = {}

        if isinstance(exc.detail, dict):
            code = exc.detail.get("code", code)
            message = exc.detail.get("message", message)
            details = exc.detail.get("details", {})

        return JSONResponse(
            status_code=exc.status_code,
            headers=exc.headers,
            content={
                "error": {
                    "code": code,
                    "message": message,
                    "details": details
                }
            }
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        # Format FastAPI validation errors into clean standardized envelope
        errors = []
        for err in exc.errors():
            loc = ".".join(str(x) for x in err.get("loc", []))
            errors.append({"field": loc, "issue": err.get("msg", "Invalid input")})

        return JSONResponse(
            status_code=400,  # Clean 400 instead of default 422 for boundary cleanliness
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Invalid request parameters or payload.",
                    "details": {"validation_errors": errors}
                }
            }
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_server_error",
                    "message": "An unexpected server error occurred. Please contact support.",
                    "details": {}
                }
            }
        )
