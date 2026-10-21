from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from pathlib import Path

# Base project directory
BASE_DIR = Path(__file__).resolve().parent.parent

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Environment
    ENVIRONMENT: str = Field(default="development", description="development | testing | production")

    # Database
    # Resilient default: auto-falls back to local SQLite if not configured
    DATABASE_URL: str = Field(
        default="sqlite+aiosqlite:///./data/app.db",
        description="SQLAlchemy async database connection string"
    )

    # Stripe Test Mode Configuration
    STRIPE_SECRET_KEY: str = Field(
        default="sk_test_placeholder_key",
        description="Stripe secret key in test mode"
    )
    STRIPE_WEBHOOK_SECRET: str = Field(
        default="whsec_placeholder_secret",
        description="Stripe webhook endpoint signing secret"
    )
    STRIPE_PRO_PRICE_ID: str = Field(
        default="price_placeholder_pro",
        description="Stripe test price ID for Pro plan ($29/month)"
    )

    # Server & API Routing
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    API_V1_PREFIX: str = "/api/v1"

settings = Settings()
