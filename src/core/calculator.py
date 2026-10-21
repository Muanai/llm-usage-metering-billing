from decimal import Decimal
import json
from pathlib import Path
from pydantic import BaseModel, Field

# Load pinned pricing config
PRICING_FILE = Path(__file__).resolve().parent.parent.parent / "config" / "pricing.json"

with open(PRICING_FILE, "r", encoding="utf-8") as f:
    PRICING_CONFIG = json.load(f)

RATES_MICRO_CENTS = PRICING_CONFIG.get("rates_per_token_micro_cents", {
    "standard_input": 200,
    "cached_input": 50,
    "standard_output": 800,
    "reasoning_output": 800,
})


class TokenUsage(BaseModel):
    input_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.cached_input_tokens
            + self.output_tokens
            + self.reasoning_tokens
        )


class CostBreakdown(BaseModel):
    input_cost_micro_cents: int
    cached_input_cost_micro_cents: int
    output_cost_micro_cents: int
    reasoning_cost_micro_cents: int
    total_micro_cents: int
    billed_cents: int
    exact_dollars: str
    formatted_total: str


class RollupSummary(BaseModel):
    total_api_calls: int
    token_micro_cents: int
    token_cost_cents: int
    base_subscription_cents: int
    total_cents: int
    formatted: str


def round_micro_cents_to_cents(micro_cents: int) -> int:
    """
    Rounds integer micro-cents to standard integer cents using ROUND_HALF_UP.
    1 cent = 1,000,000 micro-cents.
    """
    if micro_cents <= 0:
        return 0
    return (micro_cents + 500_000) // 1_000_000


def calculate_token_cost(usage: TokenUsage) -> CostBreakdown:
    """
    Computes exact integer cost in micro-cents for AI token consumption.
    Rules:
    - Standard input: 200 micro-cents/token ($2.00 / 1M)
    - Cached input: 50 micro-cents/token ($0.50 / 1M - 75% discount)
    - Standard output: 800 micro-cents/token ($8.00 / 1M)
    - Reasoning tokens: 800 micro-cents/token ($8.00 / 1M, counted as output)
    """
    input_cost = usage.input_tokens * RATES_MICRO_CENTS["standard_input"]
    cached_cost = usage.cached_input_tokens * RATES_MICRO_CENTS["cached_input"]
    output_cost = usage.output_tokens * RATES_MICRO_CENTS["standard_output"]
    reasoning_cost = usage.reasoning_tokens * RATES_MICRO_CENTS["reasoning_output"]

    total_micro = input_cost + cached_cost + output_cost + reasoning_cost
    billed_cents = round_micro_cents_to_cents(total_micro)

    # Lossless dollar string for exact probe evaluation
    exact_dollars = str(Decimal(total_micro) / Decimal(100_000_000))
    formatted_total = f"${exact_dollars}"

    return CostBreakdown(
        input_cost_micro_cents=input_cost,
        cached_input_cost_micro_cents=cached_cost,
        output_cost_micro_cents=output_cost,
        reasoning_cost_micro_cents=reasoning_cost,
        total_micro_cents=total_micro,
        billed_cents=billed_cents,
        exact_dollars=exact_dollars,
        formatted_total=formatted_total,
    )


def calculate_monthly_rollup(
    total_api_calls: int,
    token_micro_cents: int,
    base_price_cents: int
) -> RollupSummary:
    """
    Aggregates monthly usage and calculates billable total cents.
    API calls within quota have $0.00 marginal cost.
    Rounding (ROUND_HALF_UP) is strictly applied at the invoice boundary.
    """
    token_cents = round_micro_cents_to_cents(token_micro_cents)
    total_cents = base_price_cents + token_cents
    formatted = f"${total_cents / 100:.2f}"

    return RollupSummary(
        total_api_calls=total_api_calls,
        token_micro_cents=token_micro_cents,
        token_cost_cents=token_cents,
        base_subscription_cents=base_price_cents,
        total_cents=total_cents,
        formatted=formatted,
    )
