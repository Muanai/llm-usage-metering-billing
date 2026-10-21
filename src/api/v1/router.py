from fastapi import APIRouter
from src.api.v1.meter import router as meter_router
from src.api.v1.usage import router as usage_router
from src.api.v1.billing import router as billing_router

api_v1_router = APIRouter()

api_v1_router.include_router(meter_router, tags=["Metering"])
api_v1_router.include_router(usage_router, tags=["Usage"])
api_v1_router.include_router(billing_router, tags=["Billing"])
