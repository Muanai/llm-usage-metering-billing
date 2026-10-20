# Usage Metering & Billing Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an idempotent, multi-tenant usage metering, quota enforcement, integer token pricing, and Stripe test-mode subscription billing backend engine in Python with FastAPI, SQLAlchemy, and a background reconciliation worker.

**Architecture:** A clean 4-layer architecture (HTTP API controllers, Core business domain logic, Persistence/Repository with PostgreSQL/SQLite fallback, and Background worker loop) adhering strictly to the 5 FlyRank evaluation acceptance probes and shared requirements.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2.0 (asyncio), AsyncPG / aiosqlite, Alembic, Stripe Python SDK, Pytest, HTTPX, Docker Compose.

**Spec:** [docs/superpowers/specs/2026-10-20-usage-metering-billing-design.md](file:///c:/Workspace/projects/swe/llm-usage-metering-billing/docs/superpowers/specs/2026-10-20-usage-metering-billing-design.md)

---

## Global Constraints

- Stripe test mode only with test cards; no real credit card or live money ever.
- All monetary calculations and persistence must use integer micro-cents ($1\text{ USD} = 100\text{ cents} = 100,000,000\text{ micro-cents}$); no float for currency.
- Single billable action produces exactly one usage event in `usage_events` table (`event_type: "api_call"`).
- Idempotency uniqueness enforced per tenant: `UNIQUE(tenant_id, idempotency_key)`.
- All API errors must follow the uniform envelope: `{"error": {"code": "...", "message": "...", "details": {...}}}`.
- Single command `run:` in `capstone.yaml` must boot both API and background worker with zero required Docker dependencies (auto-SQLite fallback).
- Secrets remain in `.env` (git-ignored); `.env.example` committed with safe placeholders.

---

## Review Focus

1. **Concurrent requests at quota boundary**: Two simultaneous requests at 999/1000 calls $\rightarrow$ row locking serializes evaluation so the second receives `429 Too Many Requests`, preventing quota overshoot. (Tested in Task 5).
2. **Idempotency key payload mismatch**: Same key resent with a different payload or path $\rightarrow$ rejected with `400 Bad Request` (`code: "idempotency_key_reuse"`), preserving initial result. (Tested in Task 4).
3. **Out-of-order Stripe webhooks**: `customer.subscription.updated` arrives before `checkout.session.completed` $\rightarrow$ upsert pattern with timestamp guard prevents stale overwrites and missing state. (Tested in Task 7).
4. **Sub-cent cumulative rounding**: Multiple calls accumulating fractional cents $\rightarrow$ micro-cents summed without intermediate rounding, rounded at summary boundary using `ROUND_HALF_UP`. (Tested in Task 3 & 6).
5. **Missing mandatory headers**: Requests without `Idempotency-Key` or `X-Tenant-ID` $\rightarrow$ clean `400 Bad Request` with structured error envelope, never a 500 error. (Tested in Task 6).

---

## File Structure

```text
llm-usage-metering-billing/
├── config/
│   ├── __init__.py
│   ├── settings.py           # Pydantic Settings, environment loading, secrets validation
│   └── pricing.json          # Pinned rates (input, cached, output, reasoning in micro-cents)
├── src/
│   ├── __init__.py
│   ├── api/
│   │   ├── __init__.py
│   │   ├── deps.py           # DB session dependency & tenant extraction
│   │   ├── error_handlers.py # Unified error envelope and exception handlers
│   │   └── v1/
│   │       ├── __init__.py
│   │       ├── router.py     # Aggregates /generate, /usage, /billing routes
│   │       ├── meter.py      # POST /api/v1/generate (Billable endpoint)
│   │       ├── usage.py      # GET /api/v1/tenants/{tenant_id}/usage (Rollup)
│   │       └── billing.py    # POST /api/v1/billing/checkout & /webhooks/stripe
│   ├── core/
│   │   ├── __init__.py
│   │   ├── calculator.py     # Exact integer money math & token pricing rules
│   │   ├── idempotency.py    # Request deduplication, payload hashing & replay
│   │   ├── quota.py          # Boundary checker (429 vs 402) with row-level locking
│   │   └── stripe_service.py # Stripe API wrapper, webhook verification, signature check
│   ├── db/
│   │   ├── __init__.py
│   │   ├── session.py        # SQLAlchemy async engine (Postgres + SQLite auto-fallback)
│   │   └── models.py         # Tenant, Plan, Subscription, UsageEvent, IdempotencyKey, WebhookEvent
│   ├── workers/
│   │   ├── __init__.py
│   │   └── reconciliation.py # Asyncio loop & standalone runner (Stripe sync + quota alerts)
│   └── main.py               # FastAPI app, lifespan setup, CORS, router inclusion
├── migrations/
│   ├── env.py                # Alembic async environment
│   └── versions/             # Schema migration revisions (initial + later)
├── alembic.ini               # Alembic configuration
├── scripts/
│   └── seed.py               # Deterministic fixture seeder (boundary, past_due, demo)
├── tests/
│   ├── __init__.py
│   ├── conftest.py           # Isolated test DB fixture (SQLite), client fixture
│   ├── test_calculator.py    # Unit tests for money math & token rules (PROBE 5)
│   ├── test_idempotency.py   # Atomic deduplication and replay tests (PROBE 1)
│   ├── test_quotas.py        # Quota boundary 999->1000->1001 & 402 tests (PROBE 2)
│   ├── test_meter_api.py     # /generate and /usage integration tests
│   ├── test_stripe.py        # Checkout & webhook verification/replay tests (PROBE 3 & 4)
│   └── test_worker.py        # Background reconciliation and alert tests (REQ #3)
├── .env.example              # Safe environment variable placeholders
├── .gitignore                # Ignores .env, *.db, __pycache__, .pytest_cache
├── capstone.yaml             # Evaluator manifest: run, seed, test, endpoints
├── docker-compose.yml        # PostgreSQL service container
├── README.md                 # Setup guide, architecture diagram, manual probe instructions
└── EVIDENCE.md               # Pasted execution proofs for each requirement
```

---

## Tasks

### Task 1: Environment Configuration & Pinned Pricing Constants

**Files:**
- Create: `requirements.txt`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `config/__init__.py`
- Create: `config/settings.py`
- Create: `config/pricing.json`
- Create: `tests/__init__.py`
- Create: `tests/test_config.py`

**Interfaces:**
- Produces: `config.settings.Settings` instance `settings` containing `DATABASE_URL`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRO_PRICE_ID`, `WORKER_ENABLED` (default false), `WORKER_INTERVAL_SECONDS` (default 900), `ENVIRONMENT`.
- Produces: `config/pricing.json` with pinned rates in micro-cents ($1\text{ USD} = 100{,}000{,}000\text{ micro-cents}$).

- [ ] **Step 1: Write the failing test for configuration loading**

```python
# tests/test_config.py
from config.settings import Settings
import json
from pathlib import Path

def test_settings_loads_defaults():
    s = Settings(DATABASE_URL="sqlite+aiosqlite:///./data/test.db")
    assert "sqlite" in s.DATABASE_URL
    assert s.API_V1_PREFIX == "/api/v1"

def test_pricing_json_rates_exist_and_pinned():
    pricing_path = Path(__file__).parent.parent / "config" / "pricing.json"
    assert pricing_path.exists()
    with open(pricing_path, "r") as f:
        data = json.load(f)
    rates = data["rates_per_million_tokens_cents"]
    assert rates["standard_input"] == 200
    assert rates["cached_input"] == 50
    assert rates["standard_output"] == 800
    assert rates["reasoning_output"] == 800
    assert rates["cached_input"] < rates["standard_input"]
    assert rates["reasoning_output"] == rates["standard_output"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL (ModuleNotFoundError: No module named 'config.settings')

- [ ] **Step 3: Implement dependencies, `.gitignore`, `.env.example`, `config/settings.py`, and `config/pricing.json`**

1. Create `requirements.txt`:
   ```text
   fastapi>=0.110.0
   uvicorn[standard]>=0.28.0
   pydantic>=2.6.0
   pydantic-settings>=2.2.0
   sqlalchemy>=2.0.28
   asyncpg>=0.29.0
   aiosqlite>=0.20.0
   alembic>=1.13.1
   stripe>=8.0.0
   httpx>=0.27.0
   pytest>=8.0.0
   pytest-asyncio>=0.23.5
   python-dotenv>=1.0.1
   ```
2. Create `.gitignore` ignoring `.env`, `*.db`, `data/`, `__pycache__`, `.pytest_cache/`.
3. Create `.env.example` with placeholders.
4. Create `config/settings.py` using `pydantic_settings.BaseSettings`.
5. Create `config/pricing.json` with exact rates from spec.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add requirements.txt .gitignore .env.example config/ tests/test_config.py
git commit -m "feat: add environment configuration and pinned pricing constants"
```

---

### Task 2: Persistence Layer & Domain Models (PostgreSQL & SQLite Auto-Fallback)

**Files:**
- Create: `src/__init__.py`
- Create: `src/db/__init__.py`
- Create: `src/db/session.py`
- Create: `src/db/models.py`
- Create: `tests/conftest.py`
- Create: `tests/test_db_models.py`
- Create: `alembic.ini`
- Create: `migrations/env.py`
- Create: `migrations/versions/0001_initial.py`

**Interfaces:**
- Produces: `src.db.models.Base`, `Tenant`, `Plan`, `Subscription`, `UsageEvent`, `IdempotencyKey`, `WebhookEvent`.
- Produces: `src.db.session.get_db() -> AsyncGenerator[AsyncSession, None]`.
- Produces: `src.db.session.init_db()` (test/utility table creation) and `run_migrations()` (runs `alembic upgrade head`).

- [ ] **Step 1: Write failing tests for model creation and DB session**

```python
# tests/test_db_models.py
import pytest
from sqlalchemy import select
from src.db.models import Tenant, Plan, Subscription, UsageEvent, IdempotencyKey

@pytest.mark.asyncio
async def test_create_and_query_tenant_and_plan(db_session):
    plan = Plan(id="free", name="Free Plan", monthly_api_limit=1000, monthly_token_limit=100000, base_price_cents=0)
    tenant = Tenant(id="tenant_test", name="Test Org", email="test@example.com")
    db_session.add_all([plan, tenant])
    await db_session.commit()

    result = await db_session.execute(select(Tenant).where(Tenant.id == "tenant_test"))
    fetched = result.scalar_one_or_none()
    assert fetched is not None
    assert fetched.name == "Test Org"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_db_models.py -v`
Expected: FAIL (No module named 'src.db.models')

- [ ] **Step 3: Implement `src/db/models.py` and `src/db/session.py` with `tests/conftest.py`**

1. Define SQLAlchemy models matching Section 4 of Spec:
   - `Tenant`: `id`, `name`, `email`, `created_at`, `updated_at`.
   - `Plan`: `id`, `name`, `monthly_api_limit`, `monthly_token_limit`, `base_price_cents`, `stripe_price_id`.
   - `Subscription`: `id`, `tenant_id` (FK), `plan_id` (FK), `stripe_customer_id`, `stripe_subscription_id`, `status`, `current_period_start`, `current_period_end`, `stripe_last_event_created_at`.
   - `UsageEvent`: `id`, `tenant_id` (FK), `event_type="api_call"`, `api_calls=1`, `input_tokens`, `cached_input_tokens`, `output_tokens`, `reasoning_tokens`, `cost_micro_cents`, `idempotency_key`, `created_at`.
   - `IdempotencyKey`: `id`, `tenant_id` (FK), `idempotency_key`, `request_path`, `request_hash`, `response_status_code`, `response_body`, `created_at`, `expires_at`. Table constraint: `UniqueConstraint('tenant_id', 'idempotency_key')`.
   - `WebhookEvent`: `stripe_event_id` (PK), `event_type`, `status`, `processed_at`.
2. Implement `src/db/session.py` with dynamic async engine supporting SQLite fallback (`sqlite+aiosqlite:///./data/app.db`) and PostgreSQL (`postgresql+asyncpg://...`).
3. Configure Alembic for async SQLAlchemy: `alembic.ini` points at the settings URL, `migrations/env.py` imports `Base.metadata`, and `migrations/versions/0001_initial.py` creates all six tables with the documented indexes and the `UNIQUE (tenant_id, idempotency_key)` constraint.
4. Add `run_migrations()` (invoking `alembic upgrade head`); the FastAPI lifespan in Task 6 calls it before serving traffic, so `run:` needs no manual migration step.
5. Setup `tests/conftest.py` fixture `db_session` using a dedicated `./data/test.db` created by `Base.metadata.create_all` (fast, no Alembic) with transaction rollback per test.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_db_models.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/db/ tests/conftest.py tests/test_db_models.py
git commit -m "feat: implement database session factory and multi-tenant domain models"
```

---

### Task 3: Integer Money Math & AI Token Pricing Calculator (PROBE 5)

**Files:**
- Create: `src/core/__init__.py`
- Create: `src/core/calculator.py`
- Create: `tests/test_calculator.py`

**Interfaces:**
- Consumes: `config/pricing.json`.
- Produces: `TokenUsage(input_tokens, cached_input_tokens, output_tokens, reasoning_tokens)`.
- Produces: `calculate_token_cost(usage: TokenUsage) -> CostBreakdown`.
- Produces: `round_micro_cents_to_cents(micro_cents: int) -> int` (using `ROUND_HALF_UP`).
- Produces: `calculate_monthly_rollup(total_api_calls: int, token_micro_cents: int, base_price_cents: int) -> RollupSummary`, where `RollupSummary` exposes `total_micro_cents: int`, `total_cents: int` (rounded once, half-up, from the summed total), and `formatted_total: str`.

- [ ] **Step 1: Write failing tests for token pricing math and rounding rules**

```python
# tests/test_calculator.py
from src.core.calculator import (
    TokenUsage,
    calculate_token_cost,
    round_micro_cents_to_cents,
    calculate_monthly_rollup
)

def test_token_pricing_rules_and_exact_match():
    # 50k input, 25k cached, 40k output, 10k reasoning
    usage = TokenUsage(
        input_tokens=50000,
        cached_input_tokens=25000,
        output_tokens=40000,
        reasoning_tokens=10000
    )
    cost = calculate_token_cost(usage)
    # Expected:
    # input: 50,000 * 200 / 1M = $0.10 = 10,000,000 micro-cents
    # cached: 25,000 * 50 / 1M = $0.0125 = 1,250,000 micro-cents
    # output + reasoning: (40k + 10k) * 800 / 1M = $0.40 = 40,000,000 micro-cents
    # total micro-cents: 51,250,000 micro-cents = $0.5125
    assert cost.total_micro_cents == 51250000
    assert cost.exact_dollars == "0.5125"
    assert cost.billed_cents == 51

def test_rounding_half_up_rule():
    # 50.4 cents -> 50
    assert round_micro_cents_to_cents(50400000) == 50
    # 50.5 cents -> 51 (Half up)
    assert round_micro_cents_to_cents(50500000) == 51
    # 50.6 cents -> 51
    assert round_micro_cents_to_cents(50600000) == 51

def test_api_calls_have_zero_marginal_cost():
    # Rollup with base Pro plan $29 (2900 cents) + 51250000 micro-cents tokens
    summary = calculate_monthly_rollup(
        total_api_calls=150,
        token_micro_cents=51250000,
        base_price_cents=2900
    )
    assert summary.total_cents == 2951
    assert summary.formatted_total == "$29.51"

def test_rollup_rounds_once_on_total_not_per_component():
    # 0.6 + 0.6 cents: rounding each part first would wrongly yield 2 cents
    summary = calculate_monthly_rollup(total_api_calls=0, token_micro_cents=1_200_000, base_price_cents=0)
    assert summary.total_cents == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_calculator.py -v`
Expected: FAIL (No module named 'src.core.calculator')

- [ ] **Step 3: Implement `src/core/calculator.py`**

1. Load rates from `config/pricing.json`.
2. Compute exact micro-cents for each component:
   - `input_micro_cents = (input_tokens * rate_input_cents * 1_000_000) // 1_000_000`
   - `cached_micro_cents = (cached_input_tokens * rate_cached_cents * 1_000_000) // 1_000_000`
   - `output_micro_cents = ((output_tokens + reasoning_tokens) * rate_output_cents * 1_000_000) // 1_000_000`
3. Implement `round_micro_cents_to_cents(micro_cents)` via `(micro_cents + 500_000) // 1_000_000`.
4. Return `CostBreakdown` with `total_micro_cents`, `billed_cents`, `exact_dollars`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_calculator.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/calculator.py tests/test_calculator.py
git commit -m "feat: implement integer token pricing calculator and ROUND_HALF_UP rule"
```

---

### Task 4: Idempotency Engine & Request Deduplication (PROBE 1)

**Files:**
- Create: `src/core/idempotency.py`
- Create: `tests/test_idempotency.py`

**Interfaces:**
- Consumes: `src.db.models.IdempotencyKey`.
- Produces: `compute_payload_hash(payload: bytes | str | dict) -> str` (SHA-256).
- Produces: `check_or_reserve_idempotency(db, tenant_id: str, key: str, path: str, payload_hash: str) -> IdempotencyCheckResult`.
- Produces: `save_idempotency_response(db, tenant_id: str, key: str, status_code: int, response_body: str) -> None`.

- [ ] **Step 1: Write failing tests for idempotency deduplication and conflict detection**

```python
# tests/test_idempotency.py
import pytest
from src.core.idempotency import (
    compute_payload_hash,
    check_or_reserve_idempotency,
    save_idempotency_response,
    IdempotencyStatus
)

@pytest.mark.asyncio
async def test_idempotency_first_seen_and_replay(db_session):
    tenant_id = "tenant_idemp"
    key = "key_123"
    path = "/api/v1/generate"
    hash1 = compute_payload_hash({"prompt": "hello"})

    # 1. First seen
    res = await check_or_reserve_idempotency(db_session, tenant_id, key, path, hash1)
    assert res.status == IdempotencyStatus.NEW

    # 2. Save response
    await save_idempotency_response(db_session, tenant_id, key, 200, '{"ok": true}')
    res = await check_or_reserve_idempotency(db_session, tenant_id, key, "/api/v1/billing/checkout", h)
    assert res.status == IdempotencyStatus.CONFLICT
```
- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_idempotency.py -v`
Expected: FAIL (No module named 'src.core.idempotency')

- [ ] **Step 3: Implement `src/core/idempotency.py`**

1. Compute SHA-256 hash of normalized JSON / bytes.
2. Query `IdempotencyKey` by `(tenant_id, idempotency_key)`.
3. If found:
   - If `request_path` differs from the stored path OR `request_hash` differs: return status `CONFLICT`, surfaced as `400` with code `idempotency_key_reuse`. A key is scoped to one execution per tenant across all endpoints, so cross-endpoint reuse is a conflict, never a replay.
   - If match: return status `REPLAY` with stored `response_status_code` and `response_body`.
4. If not found: return status `NEW`.
5. Implement `save_idempotency_response` to persist the execution result.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_idempotency.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/idempotency.py tests/test_idempotency.py
git commit -m "feat: implement atomic idempotency key deduplication and conflict validation"
```

---

### Task 5: Quota Enforcement & Boundary Concurrency (PROBE 2)

**Files:**
- Create: `src/core/quota.py`
- Create: `tests/test_quotas.py`

**Interfaces:**
- Consumes: `src.db.models.Tenant`, `Subscription`, `Plan`, `UsageEvent`.
- Produces: `check_tenant_quota(db, tenant_id: str, requested_calls: int = 1, requested_tokens: int = 0) -> QuotaCheckResult`.
- Produces: `QuotaDecision` enum (`ALLOWED`, `QUOTA_EXCEEDED` [429], `PAYMENT_REQUIRED` [402]).
- Produces: `resolve_usage_window(plan_id: str, subscription: Subscription) -> tuple[datetime, datetime]` (Free = UTC calendar month; Pro = Stripe billing period).

- [ ] **Step 1: Write failing tests for 402/429 precedence and boundary conditions**

```python
# tests/test_quotas.py
import pytest
from datetime import datetime, timezone, timedelta
from src.db.models import Tenant, Plan, Subscription, UsageEvent
from src.core.quota import check_tenant_quota, QuotaDecision

@pytest.mark.asyncio
async def test_past_due_subscription_returns_402_precedence(db_session):
    plan = Plan(id="pro", name="Pro", monthly_api_limit=50000, monthly_token_limit=5000000, base_price_cents=2900)
    tenant = Tenant(id="tenant_past_due", name="Past Due Org", email="pastdue@example.com")
    sub = Subscription(
        tenant_id="tenant_past_due", plan_id="pro", status="past_due",
        current_period_start=datetime.now(timezone.utc) - timedelta(days=5),
        current_period_end=datetime.now(timezone.utc) + timedelta(days=25)
    )
    db_session.add_all([plan, tenant, sub])
    await db_session.commit()

    decision = await check_tenant_quota(db_session, "tenant_past_due", requested_calls=1)
    assert decision.status == QuotaDecision.PAYMENT_REQUIRED
    assert decision.http_status == 402

@pytest.mark.asyncio
async def test_exact_quota_boundary_999_to_1000_and_1001(db_session):
    plan = Plan(id="free", name="Free", monthly_api_limit=1000, monthly_token_limit=100000, base_price_cents=0)
    tenant = Tenant(id="tenant_boundary", name="Boundary Org", email="boundary@example.com")
    now = datetime.now(timezone.utc)
    sub = Subscription(
        tenant_id="tenant_boundary", plan_id="free", status="active",
        current_period_start=now - timedelta(days=5),
        current_period_end=now + timedelta(days=25)
    )
    db_session.add_all([plan, tenant, sub])
    # Seed 999 usage events
    event = UsageEvent(
        tenant_id="tenant_boundary", event_type="api_call",
        api_calls=999, input_tokens=1000, cached_input_tokens=0,
        output_tokens=500, reasoning_tokens=0, cost_micro_cents=500000,
        idempotency_key="seed_999"
    )
    db_session.add(event)
    await db_session.commit()

    # Call at 999: Requesting 1 call -> total 1000 <= 1000 -> ALLOWED (200)
    d1 = await check_tenant_quota(db_session, "tenant_boundary", requested_calls=1)
    assert d1.status == QuotaDecision.ALLOWED

    # Now add the 1000th call to DB
    e1000 = UsageEvent(
        tenant_id="tenant_boundary", event_type="api_call", api_calls=1,
        input_tokens=0, cached_input_tokens=0, output_tokens=0, reasoning_tokens=0,
        cost_micro_cents=0, idempotency_key="call_1000"
    )
    db_session.add(e1000)
    await db_session.commit()

    # Call at 1000: Requesting next call -> 1001 > 1000 -> QUOTA_EXCEEDED (429)
    d2 = await check_tenant_quota(db_session, "tenant_boundary", requested_calls=1)
    assert d2.status == QuotaDecision.QUOTA_EXCEEDED
    assert d2.http_status == 429
    assert d2.retry_after_seconds > 0

@pytest.mark.asyncio
async def test_free_tenant_usage_window_is_calendar_month(db_session):
    from src.core.quota import resolve_usage_window
    sub = Subscription(tenant_id="tenant_window", plan_id="free", status="active")
    start, end = resolve_usage_window("free", sub)
    assert start.day == 1 and start.hour == 0 and start.minute == 0 and start.second == 0
    assert end > start
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_quotas.py -v`
Expected: FAIL (No module named 'src.core.quota')

- [ ] **Step 3: Implement `src/core/quota.py` with row-level locking (`with_for_update()`)**

1. Lock tenant row via `select(Tenant).where(Tenant.id == tenant_id).with_for_update()`.
2. Fetch `Subscription` and `Plan`.
3. Check status: if `status in ["past_due", "unpaid", "canceled", "incomplete"]`, return `PAYMENT_REQUIRED` (402).
4. Resolve the window with `resolve_usage_window`: Free uses the UTC calendar month, Pro uses `[current_period_start, current_period_end)`. Aggregate only events inside that window: `SUM(api_calls)` and the four token columns.
5. Compare `current_calls + requested_calls <= plan.monthly_api_limit` and tokens.
6. If exceeded, return `QUOTA_EXCEEDED` (429) with calculated `Retry-After` seconds until `current_period_end`.
7. Otherwise, return `ALLOWED` (200).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_quotas.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/quota.py tests/test_quotas.py
git commit -m "feat: implement quota boundary checker with 402/429 precedence and concurrency row-lock"
```

---

### Task 6: API Layer: Metering (`/generate`), Rollup (`/usage`), & Error Envelope

**Files:**
- Create: `src/api/__init__.py`
- Create: `src/api/deps.py`
- Create: `src/api/error_handlers.py`
- Create: `src/api/v1/__init__.py`
- Create: `src/api/v1/router.py`
- Create: `src/api/v1/meter.py`
- Create: `src/api/v1/usage.py`
- Create: `src/main.py`
- Create: `tests/test_meter_api.py`

**Interfaces:**
- Produces: `POST /api/v1/generate` (Headers: `X-Tenant-ID`, `Idempotency-Key`).
- Produces: `GET /api/v1/tenants/{tenant_id}/usage`.
- Produces: `GET /health`.
- Produces: Standardized error envelope for missing headers, 400, 402, 422, 429.

- [ ] **Step 1: Write failing tests for `/generate` and `/usage` endpoints**

```python
# tests/test_meter_api.py
import pytest
from httpx import AsyncClient, ASGITransport
from src.main import app
from src.db.models import Tenant, Plan, Subscription

@pytest.mark.asyncio
async def test_generate_missing_headers_returns_clean_400(db_session):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post("/api/v1/generate", json={"prompt": "hi"})
        assert res.status_code == 400
        data = res.json()
        assert "error" in data
        assert data["error"]["code"] == "missing_tenant_id"

        res2 = await client.post(
            "/api/v1/generate",
            json={"prompt": "hi"},
            headers={"X-Tenant-ID": "tenant_api_test"},
        )
        assert res2.status_code == 400
        assert res2.json()["error"]["code"] == "missing_idempotency_key"

@pytest.mark.asyncio
async def test_generate_billable_call_and_idempotent_replay(db_session):
    plan = Plan(id="free", name="Free", monthly_api_limit=1000, monthly_token_limit=100000, base_price_cents=0)
    tenant = Tenant(id="tenant_api_test", name="API Org", email="api@example.com")
    sub = Subscription(tenant_id="tenant_api_test", plan_id="free", status="active")
    db_session.add_all([plan, tenant, sub])
    await db_session.commit()

    payload = {
        "model": "gemini-1.5-pro",
        "prompt": "Summarize",
        "simulated_tokens": {
            "input_tokens": 1000,
            "cached_input_tokens": 500,
            "output_tokens": 400,
            "reasoning_tokens": 100
        }
    }
    headers = {"X-Tenant-ID": "tenant_api_test", "Idempotency-Key": "api_key_001"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Request 1: New
        r1 = await client.post("/api/v1/generate", json=payload, headers=headers)
        assert r1.status_code == 200
        b1 = r1.json()
        assert b1["status"] == "success"
        assert b1["usage"]["api_calls"] == 1
        assert b1["usage"]["total_tokens"] == 2000

        # Request 2: Replay with same key
        r2 = await client.post("/api/v1/generate", json=payload, headers=headers)
        assert r2.status_code == 200
        assert r2.json() == b1

        # Check rollup
        r_usage = await client.get("/api/v1/tenants/tenant_api_test/usage")
        assert r_usage.status_code == 200
        u_data = r_usage.json()
        assert u_data["usage"]["api_calls"]["used"] == 1
        assert u_data["usage"]["tokens"]["total_used"] == 2000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_meter_api.py -v`
Expected: FAIL (No module named 'src.main')

- [ ] **Step 3: Implement API routers, endpoints, error envelope, and `src/main.py`**

1. Create `src/api/error_handlers.py` registering global exception handlers for `HTTPException`, `RequestValidationError`, converting all into `{"error": {"code": "...", "message": "...", "details": {...}}}`.
2. Create `src/api/deps.py` validating `X-Tenant-ID` and `Idempotency-Key`.
3. Create `src/api/v1/meter.py`:
   - Runs idempotency check.
   - Runs quota check.
   - Records single `UsageEvent` row.
   - Computes token cost via `calculator`.
   - Saves idempotency snapshot and commits.
4. Create `src/api/v1/usage.py` performing the monthly rollup query and returning exact JSON.
5. Create `src/main.py` initializing FastAPI, routers, and CORS. The lifespan calls `run_migrations()` first so `run:` needs no manual migration step.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_meter_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/api/ src/main.py tests/test_meter_api.py
git commit -m "feat: implement generate and usage endpoints with uniform error envelopes"
```

---

### Task 7: Stripe Integration & Webhooks (PROBE 3 & PROBE 4)

**Files:**
- Create: `src/core/stripe_service.py`
- Create: `src/api/v1/billing.py`
- Modify: `src/api/v1/router.py`
- Create: `tests/test_stripe.py`

**Interfaces:**
- Produces: `create_checkout_session(db, tenant_id: str, plan_id: str) -> dict`.
- Produces: `handle_stripe_webhook(db, raw_payload: bytes, sig_header: str) -> WebhookResult`.
- Verifies: Forged signature $\rightarrow$ 400 Bad Request.
- Verifies: Webhook replay $\rightarrow$ 200 OK `{"status": "ignored", "reason": "duplicate_event"}`.
- Verifies: `checkout.session.completed` flips tenant from Free to Pro.

- [ ] **Step 1: Write failing tests for Stripe checkout and webhook signature/deduplication**

```python
# tests/test_stripe.py
import pytest
from unittest.mock import patch, MagicMock
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from src.main import app
from src.db.models import Tenant, Plan, Subscription, WebhookEvent

@pytest.mark.asyncio
async def test_forged_webhook_signature_returns_400(db_session):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/api/v1/billing/webhooks/stripe",
            content=b'{"id": "evt_forged"}',
            headers={"Stripe-Signature": "t=123,v1=invalid_signature"}
        )
        assert res.status_code == 400
        assert res.json()["error"]["code"] == "invalid_signature"

@pytest.mark.asyncio
async def test_webhook_checkout_completed_flips_free_to_pro_and_replay_ignored(db_session):
    plan_pro = Plan(id="pro", name="Pro", monthly_api_limit=50000, monthly_token_limit=5000000, base_price_cents=2900)
    tenant = Tenant(id="tenant_stripe", name="Stripe Org", email="stripe@example.com")
    sub = Subscription(tenant_id="tenant_stripe", plan_id="free", status="active")
    db_session.add_all([plan_pro, tenant, sub])
    await db_session.commit()

    mock_event = {
        "id": "evt_test_checkout_001",
        "type": "checkout.session.completed",
        "created": 1730000000,
        "data": {
            "object": {
                "client_reference_id": "tenant_stripe",
                "customer": "cus_test_123",
                "subscription": "sub_test_123",
                "metadata": {"tenant_id": "tenant_stripe", "plan_id": "pro"}
            }
        }
    }

    with patch("stripe.Webhook.construct_event", return_value=mock_event):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = {"Stripe-Signature": "t=123,v1=valid"}
            # First delivery
            r1 = await client.post("/api/v1/billing/webhooks/stripe", json={}, headers=headers)
            assert r1.status_code == 200
            assert r1.json()["status"] == "success"

            # Check DB updated to Pro
            await db_session.refresh(sub)
            assert sub.plan_id == "pro"
            assert sub.stripe_subscription_id == "sub_test_123"

            # Replay same event -> Processed once, ignored
            r2 = await client.post("/api/v1/billing/webhooks/stripe", json={}, headers=headers)
            assert r2.status_code == 200
            assert r2.json()["status"] == "ignored"
            assert r2.json()["reason"] == "duplicate_event"

@pytest.mark.asyncio
async def test_subscription_updated_before_checkout_is_upserted(db_session):
    plan_pro = Plan(id="pro", name="Pro", monthly_api_limit=50000, monthly_token_limit=5000000, base_price_cents=2900, stripe_price_id="price_pro")
    tenant = Tenant(id="tenant_ooo", name="OOO Org", email="ooo@example.com")
    db_session.add_all([plan_pro, tenant])
    await db_session.commit()

    mock_event = {
        "id": "evt_ooo_001",
        "type": "customer.subscription.updated",
        "created": 1730000100,
        "data": {"object": {
            "id": "sub_ooo", "customer": "cus_ooo", "status": "active",
            "current_period_start": 1730000000, "current_period_end": 1732592000,
            "items": {"data": [{"price": {"id": "price_pro"}}]},
        }},
    }
    with patch("stripe.Webhook.construct_event", return_value=mock_event):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/v1/billing/webhooks/stripe", json={}, headers={"Stripe-Signature": "t=1,v1=x"})
            assert r.status_code == 200

    res = await db_session.execute(select(Subscription).where(Subscription.tenant_id == "tenant_ooo"))
    sub = res.scalar_one()
    assert sub.plan_id == "pro"
    assert sub.status == "active"
    assert sub.stripe_subscription_id == "sub_ooo"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_stripe.py -v`
Expected: FAIL (Cannot import name 'billing' or endpoint not found)

- [ ] **Step 3: Implement `src/core/stripe_service.py` and `src/api/v1/billing.py`**

1. Implement `create_checkout_session`:
   - Checks `STRIPE_SECRET_KEY` and plan price ID.
   - Calls `stripe.checkout.Session.create`.
2. Implement `POST /api/v1/billing/webhooks/stripe`:
   - Reads `await request.body()` for raw payload bytes.
   - Verifies signature with `stripe.Webhook.construct_event`.
   - On error: raises `HTTPException(status_code=400, detail={"code": "invalid_signature", ...})`.
   - Deduplication: checks `WebhookEvent` table for `event["id"]`. If found, returns `{"status": "ignored", "reason": "duplicate_event"}`.
   - Out-of-order guard: if the subscription row has `stripe_last_event_created_at` and `event["created"]` is older than it, acknowledge `200` and skip application.
   - Dispatches (upsert semantics; order-independent):
     - `checkout.session.completed`: upsert subscription to the plan resolved from the session price ID (fallback `metadata.plan_id`), `status = "active"`, recording Stripe customer/subscription IDs.
     - `customer.subscription.updated`: upsert the subscription (create it if it does not exist yet) resolving the plan from the event price ID, and update status + cycle window.
     - `customer.subscription.deleted`: downgrade to `free` and clear Stripe subscription IDs.
   - Persist the applied event's `created` value to `stripe_last_event_created_at`.
   - Records `WebhookEvent(stripe_event_id=event["id"], status="processed")` and commits.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_stripe.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/stripe_service.py src/api/v1/billing.py src/api/v1/router.py tests/test_stripe.py
git commit -m "feat: implement Stripe checkout and signature-verified webhook handler with replay deduplication"
```

---

### Task 8: Background Worker: Stripe Reconciliation & Quota Alerts (REQ #3)

**Files:**
- Create: `src/workers/__init__.py`
- Create: `src/workers/reconciliation.py`
- Modify: `src/main.py` (lifespan hook)
- Create: `tests/test_worker.py`

**Interfaces:**
- Produces: `run_reconciliation_once(db_session, stripe_client=None) -> ReconciliationReport`.
- Produces: `run_reconciliation_loop(interval_seconds: int = settings.WORKER_INTERVAL_SECONDS)` started in the FastAPI lifespan only when `settings.WORKER_ENABLED` is true.
- Detects: Out-of-sync subscriptions and updates DB.
- Detects: Quota usage $\ge 80\%$ (warning alert) and $\ge 100\%$ (critical alert).
- Includes: Exponential backoff retry logic on Stripe API network failure.

- [ ] **Step 1: Write failing tests for background worker reconciliation and alerts**

```python
# tests/test_worker.py
import pytest
from unittest.mock import MagicMock
from src.db.models import Tenant, Plan, Subscription, UsageEvent
from src.workers.reconciliation import run_reconciliation_once

@pytest.mark.asyncio
async def test_worker_detects_missed_webhook_and_syncs_status(db_session):
    plan = Plan(id="pro", name="Pro", monthly_api_limit=50000, monthly_token_limit=5000000, base_price_cents=2900)
    tenant = Tenant(id="tenant_sync", name="Sync Org", email="sync@example.com")
    sub = Subscription(tenant_id="tenant_sync", plan_id="pro", status="active", stripe_subscription_id="sub_sync_123")
    db_session.add_all([plan, tenant, sub])
    await db_session.commit()

    # Mock Stripe returning 'past_due' (missed webhook)
    mock_stripe_sub = MagicMock()
    mock_stripe_sub.status = "past_due"
    mock_stripe_client = MagicMock()
    mock_stripe_client.Subscription.retrieve.return_value = mock_stripe_sub

    report = await run_reconciliation_once(db_session, stripe_client=mock_stripe_client)
    assert report.synced_count == 1

    await db_session.refresh(sub)
    assert sub.status == "past_due"

@pytest.mark.asyncio
async def test_worker_detects_quota_alert_thresholds(db_session):
    plan = Plan(id="free", name="Free", monthly_api_limit=1000, monthly_token_limit=100000, base_price_cents=0)
    tenant = Tenant(id="tenant_alert", name="Alert Org", email="alert@example.com")
    sub = Subscription(tenant_id="tenant_alert", plan_id="free", status="active")
    event = UsageEvent(
        tenant_id="tenant_alert", event_type="api_call", api_calls=850,
        input_tokens=0, cached_input_tokens=0, output_tokens=0, reasoning_tokens=0,
        cost_micro_cents=0, idempotency_key="alert_850"
    )
    db_session.add_all([plan, tenant, sub, event])
    await db_session.commit()

    report = await run_reconciliation_once(db_session)
    assert len(report.quota_alerts) >= 1
    alert = report.quota_alerts[0]
    assert alert["tenant_id"] == "tenant_alert"
    assert alert["level"] == "WARNING_80_PERCENT"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_worker.py -v`
Expected: FAIL (No module named 'src.workers.reconciliation')

- [ ] **Step 3: Implement `src/workers/reconciliation.py` and hook into `src/main.py` lifespan**

1. Create `src/workers/reconciliation.py`:
   - Iterates subscriptions having `stripe_subscription_id`.
   - Calls Stripe `Subscription.retrieve` with exponential backoff (3 attempts).
   - If status differs, reconciles local DB and logs notice.
   - Calculates monthly usage for all active tenants; if $\ge 80\%$, logs warning; if $\ge 100\%$, logs critical alert.
   - Exposes `run_reconciliation_loop()` for lifespan and `run_reconciliation_once()` for tests.
2. In `src/main.py`, start `asyncio.create_task(run_reconciliation_loop())` during lifespan only if `settings.WORKER_ENABLED` (default false, so tests are deterministic).
3. Guard a run with a database advisory lock so a standalone `python -m src.workers.reconciliation` and the embedded loop cannot execute concurrently.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_worker.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/workers/ src/main.py tests/test_worker.py
git commit -m "feat: implement background reconciliation worker and quota threshold alert evaluator"
```

---

### Task 9: Seed Fixtures, Stripe Provisioning, Docker Compose, & Manifests

**Files:**
- Create: `scripts/__init__.py`
- Create: `scripts/seed.py`
- Create: `docker-compose.yml`
- Create: `capstone.yaml`
- Modify: `README.md`
- Create: `tests/test_seed.py`

**Interfaces:**
- Produces: `python -m scripts.seed` creating exact deterministic test fixtures:
  - `tenant_free_boundary` (999 calls seeded).
  - `tenant_pro_past_due` (status: `past_due`).
  - `tenant_checkout_demo` (clean Free tenant).
  - `tenant_pricing_demo` (50k in, 25k cached, 40k out, 10k reasoning tokens).
  - `tenant_clean` (clean Free tenant).
- Produces: `capstone.yaml` manifest with `run:`, `seed:`, `test:`, `base_url:`, endpoints.
- Produces: `docker-compose.yml` for optional PostgreSQL container.

- [ ] **Step 1: Write test verifying seed script execution and fixture integrity**

```python
# tests/test_seed.py
import pytest
from sqlalchemy import select
from scripts.seed import run_seed
from src.db.models import Tenant, Subscription, UsageEvent

@pytest.mark.asyncio
async def test_seed_creates_expected_fixtures(db_session):
    await run_seed(db_session)

    # 1. Boundary tenant exists with 999 calls
    res = await db_session.execute(select(UsageEvent).where(UsageEvent.tenant_id == "tenant_free_boundary"))
    events = res.scalars().all()
    assert sum(e.api_calls for e in events) == 999

    # 2. Past due tenant exists with past_due status
    res_sub = await db_session.execute(select(Subscription).where(Subscription.tenant_id == "tenant_pro_past_due"))
    sub = res_sub.scalar_one_or_none()
    assert sub is not None
    assert sub.status == "past_due"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_seed.py -v`
Expected: FAIL (No module named 'scripts.seed')

- [ ] **Step 3: Implement `scripts/seed.py`, `docker-compose.yml`, `capstone.yaml`, and `README.md`**

1. Create `scripts/seed.py` inserting plans and the 5 fixtures. It is idempotent: it upserts plans/tenants and resets each fixture tenant's usage events to the documented baseline. If `STRIPE_SECRET_KEY` is set, it looks up a test price by key `flyrank_pro_monthly` and creates the product + recurring $29/mo price only when absent, then stores the returned `price_id` on the `pro` plan. If the key is unset it logs a warning and leaves `stripe_price_id` null (non-Stripe probes still pass). Document the CLI fallback in the README: `stripe prices create --unit-amount 2900 --currency usd --recurring interval=month --lookup-key flyrank_pro_monthly`.
2. Create `docker-compose.yml` for PostgreSQL:
   ```yaml
   version: '3.8'
   services:
     db:
       image: postgres:16-alpine
       environment:
         POSTGRES_USER: user
         POSTGRES_PASSWORD: password
         POSTGRES_DB: billing_db
       ports:
         - "5432:5432"
   ```
3. Create `capstone.yaml`:
   ```yaml
   run: uvicorn src.main:app --host 0.0.0.0 --port 8000
   seed: python -m scripts.seed
   test: pytest -v
   base_url: http://localhost:8000
   endpoints:
     health: GET /health
     generate: POST /api/v1/generate
     usage: GET /api/v1/tenants/{tenant_id}/usage
     checkout: POST /api/v1/billing/checkout
     webhook: POST /api/v1/billing/webhooks/stripe
   ```
4. Write comprehensive `README.md` detailing architecture diagram, setup steps, running locally, and limitations.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_seed.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/ docker-compose.yml capstone.yaml README.md tests/test_seed.py
git commit -m "feat: add deterministic fixture seeder, docker compose, capstone manifest, and README"
```

---

### Task 10: Full Acceptance Probes Verification & Evidence Documentation

**Files:**
- Modify: `EVIDENCE.md`
- Create: `BUILDLOG.md`

**Interfaces:**
- Produces: Complete, verified `EVIDENCE.md` populated with actual test run transcripts for all 5 acceptance probes and REQ #1–7.
- Produces: `BUILDLOG.md` documenting architecture decisions, AI pair-programming reflections, and bug fixes.

- [ ] **Step 1: Execute complete test suite**

Run: `pytest -v`
Expected: All tests PASS (0 failures, 100% green).

- [ ] **Step 2: Execute live probe verifications via CLI against running server**

1. Run seeder: `python scripts/seed.py`.
2. PROBE 1: Send billable request twice with same idempotency key $\rightarrow$ capture curl and DB count verification.
3. PROBE 2: Call `tenant_free_boundary` at 999 $\rightarrow$ 200 OK (1000/1000) $\rightarrow$ Call next $\rightarrow$ 429 Too Many Requests; Call `tenant_pro_past_due` $\rightarrow$ 402 Payment Required.
4. PROBE 3 & 4: Stripe Checkout test & webhook replay deduplication.
5. PROBE 5: Query `GET /api/v1/tenants/tenant_pricing_demo/usage` and capture exact breakdown ($29.51 / 51,250,000 micro-cents).

- [ ] **Step 3: Paste transcripts into `EVIDENCE.md` and write `BUILDLOG.md`**

Populate all sections of `EVIDENCE.md` with the verified CLI logs and terminal outputs.

- [ ] **Step 4: Final verification scan**

Verify:
- All required submission files exist (`README.md`, `capstone.yaml`, `EVIDENCE.md`, `BUILDLOG.md`, `.env.example`).
- No secret keys are committed in git.
- Clean run on machine without Docker works via SQLite auto-fallback.

- [ ] **Step 5: Final commit**

```bash
git add EVIDENCE.md BUILDLOG.md
git commit -m "docs: complete EVIDENCE.md acceptance probe proofs and BUILDLOG.md records"
```
