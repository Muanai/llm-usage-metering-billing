from decimal import Decimal
import pytest
from src.core.calculator import (
    TokenUsage,
    CostBreakdown,
    RollupSummary,
    calculate_token_cost,
    round_micro_cents_to_cents,
    calculate_monthly_rollup
)

def test_token_pricing_rules_and_exact_match():
    # 50k standard input, 25k cached input, 40k output, 10k reasoning
    usage = TokenUsage(
        input_tokens=50000,
        cached_input_tokens=25000,
        output_tokens=40000,
        reasoning_tokens=10000
    )
    cost = calculate_token_cost(usage)

    # Verifikasi Aturan:
    # 1. Standard Input: 50,000 * 200 micro-cents = 10,000,000 micro-cents ($0.10)
    # 2. Cached Input: 25,000 * 50 micro-cents = 1,250,000 micro-cents ($0.0125 - discount 75%)
    # 3. Output + Reasoning: (40,000 + 10,000) * 800 micro-cents = 40,000,000 micro-cents ($0.40)
    # Total Token Cost = 51,250,000 micro-cents = $0.5125
    assert cost.input_cost_micro_cents == 10000000
    assert cost.cached_input_cost_micro_cents == 1250000
    assert cost.output_cost_micro_cents == 32000000
    assert cost.reasoning_cost_micro_cents == 8000000
    assert cost.total_micro_cents == 51250000
    assert cost.exact_dollars == "0.5125"
    assert cost.billed_cents == 51  # 51.25 cents rounds down to 51
    assert cost.formatted_total == "$0.5125"

def test_rounding_half_up_rule():
    # 50.4 cents -> 50
    assert round_micro_cents_to_cents(50400000) == 50
    # 50.5 cents -> 51 (Half up)
    assert round_micro_cents_to_cents(50500000) == 51
    # 50.6 cents -> 51
    assert round_micro_cents_to_cents(50600000) == 51
    # 0.5 cents -> 1
    assert round_micro_cents_to_cents(500000) == 1
    # 0.49 cents -> 0
    assert round_micro_cents_to_cents(490000) == 0

def test_api_calls_have_zero_marginal_cost_in_rollup():
    # 150 calls, 51,250,000 micro-cents token usage, Pro plan ($29 base = 2900 cents)
    summary = calculate_monthly_rollup(
        total_api_calls=150,
        token_micro_cents=51250000,
        base_price_cents=2900
    )
    # Base 2900 cents ($29.00) + 51 cents ($0.51) = 2951 cents ($29.51)
    assert summary.total_api_calls == 150
    assert summary.token_micro_cents == 51250000
    assert summary.token_cost_cents == 51
    assert summary.base_subscription_cents == 2900
    assert summary.total_cents == 2951
    assert summary.formatted == "$29.51"
