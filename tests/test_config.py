from pathlib import Path
import json
import pytest
from config.settings import Settings

def test_settings_loads_resilient_defaults():
    s = Settings()
    assert "sqlite" in s.DATABASE_URL
    assert s.API_V1_PREFIX == "/api/v1"
    assert s.ENVIRONMENT in ["development", "testing", "production"]
    assert s.STRIPE_SECRET_KEY is not None

def test_pricing_json_rates_exist_and_pinned():
    pricing_path = Path(__file__).parent.parent / "config" / "pricing.json"
    assert pricing_path.exists(), f"Pricing config missing at {pricing_path}"
    with open(pricing_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    rates = data["rates_per_million_tokens_cents"]
    assert rates["standard_input"] == 200
    assert rates["cached_input"] == 50
    assert rates["standard_output"] == 800
    assert rates["reasoning_output"] == 800
    assert rates["cached_input"] < rates["standard_input"]
    assert rates["reasoning_output"] == rates["standard_output"]

    plans = data["plans"]
    assert plans["free"]["quota_api_calls"] == 1000
    assert plans["free"]["quota_tokens"] == 100000
    assert plans["free"]["base_price_cents"] == 0
    assert plans["pro"]["quota_api_calls"] == 50000
    assert plans["pro"]["quota_tokens"] == 5000000
    assert plans["pro"]["base_price_cents"] == 2900
