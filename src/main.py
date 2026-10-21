from contextlib import asynccontextmanager
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from config.settings import settings
from src.api.error_handlers import register_error_handlers
from src.api.v1.router import api_v1_router
from src.db.session import init_db

import asyncio
from src.workers.reconciliation import run_reconciliation_loop

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Auto-initialize tables for zero-friction local/evaluation execution
    await init_db()

    worker_task = None
    if settings.ENVIRONMENT != "testing":
        worker_task = asyncio.create_task(run_reconciliation_loop(interval_seconds=60))

    yield

    # Shutdown: cancel background worker task cleanly
    if worker_task:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

app = FastAPI(
    title="LLM Usage Metering & Billing Engine",
    description="Multi-tenant usage metering, quota enforcement, and Stripe billing engine.",
    version="1.0.0",
    lifespan=lifespan
)

# Register uniform error envelope handlers
register_error_handlers(app)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Healthcheck endpoint
@app.get("/health", tags=["Health"])
async def healthcheck():
    return {"status": "healthy"}

# Include v1 API routes
app.include_router(api_v1_router, prefix=settings.API_V1_PREFIX)

if __name__ == "__main__":
    uvicorn.run(
        "src.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=(settings.ENVIRONMENT == "development")
    )
