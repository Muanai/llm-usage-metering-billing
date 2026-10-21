# FlyRank LLM Usage Metering & Billing Engine

Production-grade, multi-tenant usage metering and subscription billing engine designed for AI platforms. Built in Python 3.12 with FastAPI, SQLAlchemy 2.0 Async, Pydantic v2, and integrated with Stripe test mode.

---

## 1. System Architecture

The engine is built as a **Unified Single Service** adhering strictly to a single runtime command, eliminating distributed network failures and inter-process latency while preserving complete modularity.

```mermaid
graph TD
    Client[Client / Evaluator / AI Consumer] -->|POST /api/v1/generate| Meter[API: Meter & Quotas]
    Client -->|GET /api/v1/tenants/:id/usage| Usage[API: Rollup Engine]
    Client -->|POST /api/v1/billing/checkout| Checkout[API: Stripe Checkout]
    Stripe[Stripe / Stripe Webhooks] -->|POST /api/v1/billing/webhooks/stripe| Webhook[API: Webhook Handler]

    subgraph Core Domain Logic
        Idemp[Idempotency Engine<br/>Two-Phase SHA-256 Reservation]
        Quota[Quota Boundary Guard<br/>Row-Level Concurrency Lock]
        Calc[Token Pricing Calculator<br/>Lossless Integer Micro-Cents]
        StripeSvc[Stripe Service<br/>Signature Verification & Dedup]
    end

    subgraph Background Processing
        Worker[Reconciliation Worker<br/>Missed Webhook Sync & Alerts]
    end

    subgraph Persistence Layer
        DB[(PostgreSQL 16 / SQLite Fallback)]
        T_Tenants[(Tenants)]
        T_Plans[(Plans)]
        T_Subs[(Subscriptions)]
        T_Events[(Usage Events)]
        T_Keys[(Idempotency Keys)]
        T_Webhooks[(Webhook Events)]
    end

    Meter --> Idemp
    Meter --> Quota
    Meter --> Calc
    Checkout --> StripeSvc
    Webhook --> StripeSvc
    Worker --> StripeSvc
    Worker --> Quota

    Idemp --> T_Keys
    Quota --> T_Subs
    Quota --> T_Events
    Calc --> T_Events
    StripeSvc --> T_Subs
    StripeSvc --> T_Webhooks
```

---

## 2. Core Architectural Pillars

### Exactly-Once Metering & Idempotency (PROBE 1)
- Every billable operation requires `X-Tenant-ID` and `Idempotency-Key` headers.
- **Two-Phase Reservation:** Keys are atomically locked in `processing` status before downstream logic begins, preventing concurrent duplicate billing.
- **Request Integrity:** The SHA-256 hash of the request body is verified. Identical requests replay the cached response (`200 OK`); altered payloads with reused keys are rejected with `400 Bad Request` (`idempotency_key_reuse`).
- Exactly **one** row is recorded in `usage_events` per generation call.

### Quota Enforcement & Boundary Precedence (PROBE 2)
- **Financial Default Precedence:** If a subscription is in `past_due`, `unpaid`, or `canceled`, requests are immediately rejected with **`402 Payment Required`** prior to volume calculations.
- **Pessimistic Concurrency Locking:** Tenant rows are locked via `with_for_update()` during quota evaluation to prevent concurrent race conditions.
- **Strict Boundary Honesty:** At 999/1,000 calls, the next request is admitted (reaching 1,000/1,000); the 1,001st request is blocked with **`429 Too Many Requests`** and a `Retry-After` header.

### Lossless Integer Money Math (PROBE 5)
- Internal prices and token costs are stored strictly in **integer micro-cents** ($1\text{ USD} = 100{,}000{,}000\ \mu\text{c}$).
- Zero floating-point arithmetic.
- Cached input receives a 75% discount ($0.50/1M tokens); reasoning tokens are priced identically to output tokens ($8.00/1M tokens).
- Rounding follows `ROUND_HALF_UP_AT_INVOICE` at the final billing boundary. API calls have zero marginal usage cost and serve purely for quota bounds.

### Stripe Integration & Resilient Webhooks (PROBE 3 & 4)
- **Checkout Sessions:** Creates standard Stripe test-mode checkout sessions with tenant metadata.
- **Cryptographic Signature Verification:** Webhook payloads are verified against the raw binary request stream. Forged signatures return `400 Bad Request`.
- **Replay Deduplication:** Webhook event IDs are idempotently recorded in `webhook_events`. Replayed deliveries return `200 OK` with `{"status": "ignored", "reason": "duplicate_event"}`.
- **Out-of-Order Delivery Protection:** Event timestamps (`stripe_last_event_created_at`) prevent stale updates from overwriting newer subscription states.

### Dual-Engine Persistence
- **Zero-Config Auto-Fallback:** If PostgreSQL is unavailable, the engine automatically initializes and runs on an embedded SQLite database (`./data/app.db`) without requiring Docker or manual configuration.
- **Automatic Schema Initialization:** Tables and indexes are bootstrapped on application startup during the lifespan event.

---

## 3. Pinned Pricing & Quotas

Configured in `config/pricing.json`:

| Metric | Free Plan | Pro Plan | Unit Cost |
| :--- | :--- | :--- | :--- |
| **Monthly Base Price** | $0.00 | $29.00 / mo | Flat |
| **Monthly API Calls** | 1,000 calls | 50,000 calls | $0 marginal cost |
| **Monthly Token Limit** | 100,000 tokens | 5,000,000 tokens | Included in plan |
| **Standard Input Tokens**| — | — | $2.00 / 1M ($200\ \mu\text{c}$ / 1k) |
| **Cached Input Tokens** | — | — | $0.50 / 1M ($50\ \mu\text{c}$ / 1k) |
| **Output Tokens** | — | — | $8.00 / 1M ($800\ \mu\text{c}$ / 1k) |
| **Reasoning Tokens** | — | — | $8.00 / 1M ($800\ \mu\text{c}$ / 1k) |

---

## 4. Deterministic Evaluation Fixtures

Seeded via `scripts/seed.py`:

| Tenant ID | Plan | Seeded State | Purpose |
| :--- | :--- | :--- | :--- |
| `tenant_free_boundary` | Free | Exactly 999 API calls seeded | PROBE 2: 1,000th call succeeds (200), 1,001st call blocked (429) |
| `tenant_pro_past_due` | Pro | Status = `past_due` | PROBE 2: Returns 402 Payment Required |
| `tenant_checkout_demo` | Free | Clean Free tenant (0 calls) | PROBE 3: Stripe Checkout upgrade Free $\rightarrow$ Pro |
| `tenant_pricing_demo` | Pro | 50k in, 25k cached, 40k out, 10k reasoning | PROBE 5: Usage rollup verifies exact $29.51 total |
| `tenant_clean` | Free | Clean Free tenant (0 calls) | PROBE 1: Idempotency replay verification |

---

## 5. Quick Start & Execution

Package and dependency management is handled via **`uv`**.

### 1. Installation & Environment Setup
```bash
# Install dependencies with uv
uv sync

# Configure environment variables
cp .env.example .env
```

### 2. Seed Deterministic Fixtures
```bash
uv run python scripts/seed.py
```

### 3. Run Application Server
```bash
uv run uvicorn src.main:app --host 0.0.0.0 --port 8000
```
*The database tables will be automatically created on startup.*

### 4. Run Automated Test Suite
```bash
uv run pytest -v
```

### Optional: PostgreSQL via Docker Compose
To run against PostgreSQL 16 instead of SQLite fallback:
```bash
docker compose up -d
# Update .env to: DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/billing_db
uv run python scripts/seed.py
uv run uvicorn src.main:app --host 0.0.0.0 --port 8000
```

---

## 6. API Reference

### Uniform Error Envelope
All error responses adhere strictly to the standardized envelope:
```json
{
  "error": {
    "code": "quota_exceeded",
    "message": "Monthly API call limit reached for tenant.",
    "details": {
      "metric": "api_calls",
      "current_usage": 1000,
      "limit": 1000,
      "plan_id": "free"
    }
  }
}
```

### Endpoints
- `GET /health`: Healthcheck endpoint reporting database and Stripe status.
- `POST /api/v1/generate`: Primary billable LLM generation gateway.
  - **Headers:** `X-Tenant-ID` (string), `Idempotency-Key` (string).
  - **Payload:** `{"model": "gemini-1.5-pro", "prompt": "...", "simulated_tokens": {"input_tokens": 1000, "cached_input_tokens": 500, "output_tokens": 400, "reasoning_tokens": 100}}`.
- `GET /api/v1/tenants/{tenant_id}/usage`: Current billing cycle rollup with token breakdowns, micro-cents, rounded cents, and plan limits.
- `POST /api/v1/billing/checkout`: Initiates Stripe Checkout session for Pro plan.
- `POST /api/v1/billing/webhooks/stripe`: Cryptographically verified webhook endpoint for Stripe events.

---

## 7. Capstone Manifest (`capstone.yaml`)

```yaml
run: uv run uvicorn src.main:app --host 0.0.0.0 --port 8000
seed: uv run python scripts/seed.py
test: uv run pytest -v
base_url: http://localhost:8000
endpoints:
  health: GET /health
  generate: POST /api/v1/generate
  usage: GET /api/v1/tenants/{tenant_id}/usage
  checkout: POST /api/v1/billing/checkout
  webhook: POST /api/v1/billing/webhooks/stripe
```

---

## 8. High-Concurrency Stress Benchmark & Quantitative Portfolio Metrics

To rigorously validate quota boundary honesty and latency under lock contention, the repository includes an automated benchmark harness ([scripts/benchmark.py](file:///c:/Workspace/projects/swe/llm-usage-metering-billing/scripts/benchmark.py)) and concurrency regression test ([tests/test_benchmark.py](file:///c:/Workspace/projects/swe/llm-usage-metering-billing/tests/test_benchmark.py)).

### Concurrency Stress Test Architecture
- **Boundary Scenario**: Pre-seeds a tenant to **995 / 1,000 API calls** (leaving exactly 5 calls of headroom).
- **Concurrent Burst**: Fires **50 simultaneous async requests** through the metering gateway (`POST /api/v1/generate`).
- **Zero-Leakage Invariant**: Must admit **exactly 5 requests (200 OK)** and reject **exactly 45 requests (429 Too Many Requests)**, with the final database count reaching exactly 1,000 / 1,000 (0 over-allocation leakage).

### Execution Command
```bash
uv run python scripts/benchmark.py
```

### Empirical Benchmark Results
```
================================================================================
CONCURRENCY & QUOTA BOUNDARY INTEGRITY BENCHMARK RESULTS
================================================================================
Total Completed Requests:  50 in 1.276s
Throughput:                39.2 req/sec
Admitted (200 OK):         5 (Expected: 5)
Rejected (429 Quota):      45 (Expected: 45)
Other / Errors:            0
Final DB Usage Total:      1000 / 1000 calls
Over-Quota Leaks:          0 (ZERO LEAKAGE CONFIRMED: True)
--------------------------------------------------------------------------------

LATENCY PERCENTILES (ms):
Category           |      Min |      p50 |      p90 |      p95 |      p99 |      Max |      Avg
--------------------------------------------------------------------------------
All Requests       |    90.88 |   632.46 |  1085.10 |  1150.01 |  1200.59 |  1210.73 |   644.60
Admitted (200)     |    90.88 |   151.35 |   191.54 |   195.28 |   198.28 |   199.03 |   148.11
Rejected (429)     |   219.39 |   697.06 |  1094.80 |  1161.52 |  1201.63 |  1210.73 |   699.77
================================================================================
```

### Quantitative Portfolio Descriptions (Google XYZ Resume Format)
- **Engineered a high-integrity LLM usage metering engine** achieving **100% quota boundary honesty (0 over-allocation leaks)** under 50-worker concurrent race conditions using dialect-aware pessimistic row locking and striped in-process mutexes.
- **Sustained 39.2 req/sec throughput** with an admitted median latency of **151.4ms** and p99 latency of **198.3ms** while atomically executing SHA-256 idempotency deduplication and dynamic cycle rollups.
- **Architected lossless integer micro-cent billing math** ($1\text{ USD} = 100\text{M } \mu\text{c}$) eliminating IEEE-754 floating-point financial drift across multi-million simulated token volumes.
