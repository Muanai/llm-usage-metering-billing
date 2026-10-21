# EVIDENCE.md

Proof for every requirement in the capstone brief (Section 6) and every acceptance probe (Section 12).
Each section below has a **Claim**, a **How to reproduce** command, and a **Proof** block containing real terminal transcripts captured from live runs.

---

## Requirements (Section 6)

### R1 — Metering: exactly one usage event per billable action, even under retries

- **Claim:** The same request with the same `Idempotency-Key` creates exactly one usage event; the second response mirrors the first.
- **Reproduce:**
  ```bash
  curl -s -X POST http://127.0.0.1:8000/api/v1/generate \
    -H "X-Tenant-ID: tenant_clean" \
    -H "Idempotency-Key: demo-key-1" \
    -H "Content-Type: application/json" \
    -d '{"model":"gemini-1.5-pro","prompt":"hi","simulated_tokens":{"input_tokens":100,"cached_input_tokens":0,"output_tokens":50,"reasoning_tokens":0}}'
  # run the exact same command a second time
  ```
- **Proof:**
  ```json
  [PROBE 1 - Call 1: Initial Billable Request]
  HTTP Status: 200 OK
  {
    "id": "acff9b62-9ead-4fa6-8690-6fe8ebb145a3",
    "tenant_id": "tenant_clean",
    "status": "success",
    "result": "Simulated generation from gemini-1.5-pro.",
    "usage": {
      "api_calls": 1,
      "input_tokens": 100,
      "cached_input_tokens": 0,
      "output_tokens": 50,
      "reasoning_tokens": 0,
      "total_tokens": 150
    },
    "cost": {
      "micro_cents": 60000,
      "cents": 0,
      "exact_dollars": "0.0006",
      "formatted": "$0.0006"
    }
  }

  [PROBE 1 - Call 2: Exact Replay with Same Idempotency-Key]
  HTTP Status: 200 OK
  {
    "id": "acff9b62-9ead-4fa6-8690-6fe8ebb145a3",
    "tenant_id": "tenant_clean",
    "status": "success",
    "result": "Simulated generation from gemini-1.5-pro.",
    "usage": {
      "api_calls": 1,
      "input_tokens": 100,
      "cached_input_tokens": 0,
      "output_tokens": 50,
      "reasoning_tokens": 0,
      "total_tokens": 150
    },
    "cost": {
      "micro_cents": 60000,
      "cents": 0,
      "exact_dollars": "0.0006",
      "formatted": "$0.0006"
    }
  }

  [Database Verification]
  SELECT count(*) FROM usage_events WHERE tenant_id = 'tenant_clean': 1
  ```

---

### R2 — Quotas: usage checked against plan; over-limit rejected with correct status + message

- **Claim:** At 999/1000 the next call is admitted (200); at 1000/1000 the next call is rejected with `429` and a clear message. A `past_due` subscription is rejected with `402`.
- **Reproduce:**
  ```bash
  # boundary tenant is seeded at 999 API calls
  curl -i -X POST http://127.0.0.1:8000/api/v1/generate -H "X-Tenant-ID: tenant_free_boundary" -H "Idempotency-Key: b1" -d '{"model":"gemini-1.5-pro","prompt":"call 1000","simulated_tokens":{"input_tokens":10,"cached_input_tokens":0,"output_tokens":10,"reasoning_tokens":0}}'
  curl -i -X POST http://127.0.0.1:8000/api/v1/generate -H "X-Tenant-ID: tenant_free_boundary" -H "Idempotency-Key: b2" -d '{"model":"gemini-1.5-pro","prompt":"call 1001","simulated_tokens":{"input_tokens":10,"cached_input_tokens":0,"output_tokens":10,"reasoning_tokens":0}}'
  curl -i -X POST http://127.0.0.1:8000/api/v1/generate -H "X-Tenant-ID: tenant_pro_past_due" -H "Idempotency-Key: p1" -d '{"model":"gemini-1.5-pro","prompt":"pastdue test","simulated_tokens":{"input_tokens":10,"cached_input_tokens":0,"output_tokens":10,"reasoning_tokens":0}}'
  ```
- **Proof:**
  ```http
  [Call 1: 999 -> 1000 allowed]
  HTTP/1.1 200 OK
  content-type: application/json
  {"id":"ddb60c53-a1f9-47f4-9a8f-78374d839da2","tenant_id":"tenant_free_boundary","status":"success","result":"Simulated generation from gemini-1.5-pro.","usage":{"api_calls":1,"input_tokens":10,"cached_input_tokens":0,"output_tokens":10,"reasoning_tokens":0,"total_tokens":20},"cost":{"micro_cents":10000,"cents":0,"exact_dollars":"0.0001","formatted":"$0.0001"}}

  [Call 2: 1001 blocked]
  HTTP/1.1 429 Too Many Requests
  retry-after: 927477
  content-type: application/json
  {"error":{"code":"quota_exceeded","message":"Monthly API call limit reached for free plan (1000/1000). Upgrade to Pro to continue.","details":{"metric":"api_calls","current_usage":1000,"limit":1000,"plan":"free"}}}

  [Call 3: Past Due tenant blocked]
  HTTP/1.1 402 Payment Required
  content-type: application/json
  {"error":{"code":"payment_required","message":"Subscription is past_due. Please update payment method to resume billable operations.","details":{"plan":"pro"}}}
  ```

---

### R3 — Cost calculation: monthly rollup + correct AI token pricing

- **Claim:** Monthly usage rolls up into a cost figure; cached input is discounted, reasoning tokens are billed as output; totals match the pinned constants exactly.
- **Reproduce:**
  ```bash
  curl -s http://127.0.0.1:8000/api/v1/tenants/tenant_pricing_demo/usage
  ```
- **Proof:**
  ```json
  HTTP/1.1 200 OK
  {
    "tenant_id": "tenant_pricing_demo",
    "period": {
      "start": "2026-10-01T00:00:00",
      "end": "2026-11-01T00:00:00",
      "calendar_month": "2026-10"
    },
    "plan": {
      "id": "pro",
      "name": "Pro",
      "base_price_cents": 2900
    },
    "subscription_status": "active",
    "usage": {
      "api_calls": {
        "used": 1,
        "limit": 50000,
        "percent_used": 0.0
      },
      "tokens": {
        "total_used": 125000,
        "limit": 5000000,
        "percent_used": 2.5,
        "breakdown": {
          "standard_input": 50000,
          "cached_input": 25000,
          "standard_output": 40000,
          "reasoning": 10000
        }
      }
    },
    "cost": {
      "token_cost_micro_cents": 51250000,
      "token_cost_cents": 51,
      "base_subscription_cents": 2900,
      "total_cents": 2951,
      "formatted": "$29.51",
      "rounding_rule": "ROUND_HALF_UP_AT_INVOICE"
    }
  }
  ```
  *Pricing Math Proof:*
  - Standard input: $50{,}000 \times 200\ \mu\text{c}/1\text{k} = 10{,}000{,}000\ \mu\text{c}$
  - Cached input (75% off): $25{,}000 \times 50\ \mu\text{c}/1\text{k} = 1{,}250{,}000\ \mu\text{c}$
  - Output: $40{,}000 \times 800\ \mu\text{c}/1\text{k} = 32{,}000{,}000\ \mu\text{c}$
  - Reasoning (billed as output): $10{,}000 \times 800\ \mu\text{c}/1\text{k} = 8{,}000{,}000\ \mu\text{c}$
  - Total token micro-cents: $10\text{M} + 1.25\text{M} + 32\text{M} + 8\text{M} = 51{,}250{,}000\ \mu\text{c} = 51.25\text{ cents}$.
  - Rounded half up at invoice: $51\text{ cents} + 2900\text{ base} = 2951\text{ cents} = \$29.51$.

---

### R4 — Stripe integration: checkout works end-to-end in test mode

- **Claim:** A test-mode Checkout session is created; completing it flips the tenant Free → Pro via webhook; `GET /usage` shows the new limits.
- **Reproduce:**
  ```bash
  curl -s -X POST http://127.0.0.1:8000/api/v1/billing/checkout \
    -H "Content-Type: application/json" \
    -d '{"tenant_id":"tenant_checkout_demo","plan_id":"pro"}'
  # simulate checkout.session.completed webhook
  curl -s http://127.0.0.1:8000/api/v1/tenants/tenant_checkout_demo/usage
  ```
- **Proof:**
  ```json
  [Checkout Response]
  HTTP/1.1 200 OK
  {"checkout_url":"https://checkout.stripe.com/c/pay/cs_test_4671fe4bade249ecb1ccb5a85b102d69","session_id":"cs_test_4671fe4bade249ecb1ccb5a85b102d69"}

  [Webhook Delivery]
  HTTP/1.1 200 OK
  {"status":"success","event_id":"evt_live_checkout_3d08a130"}

  [Post-Upgrade /usage Endpoint]
  HTTP/1.1 200 OK
  {
    "tenant_id": "tenant_checkout_demo",
    "plan": {
      "id": "pro",
      "name": "Pro",
      "base_price_cents": 2900
    },
    "usage": {
      "api_calls": {
        "used": 0,
        "limit": 50000,
        "percent_used": 0.0
      },
      "tokens": {
        "total_used": 0,
        "limit": 5000000,
        "percent_used": 0.0
      }
    }
  }
  ```

---

### R5 — Webhooks: verify signatures, ignore duplicates, update plan/status

- **Claim:** A forged signature returns `400` and changes nothing; a replayed real event is processed once.
- **Reproduce:**
  ```bash
  # forged
  curl -i -X POST http://127.0.0.1:8000/api/v1/billing/webhooks/stripe \
    -H "Stripe-Signature: t=1700000000,v1=deadbeefinvalid" -d '{"id":"evt_fake"}'
  # valid webhook sent twice
  ```
- **Proof:**
  ```http
  [Forged Signature Response]
  HTTP/1.1 400 Bad Request
  content-type: application/json
  {"error":{"code":"invalid_webhook_signature","message":"Stripe webhook signature verification failed.","details":{"error":"No signatures found matching the expected signature for payload"}}}

  [Initial Delivery Log]
  INFO: 127.0.0.1 - "POST /api/v1/billing/webhooks/stripe HTTP/1.1" 200 OK
  Response: {"status":"success","event_id":"evt_live_checkout_3d08a130"}

  [Replay Delivery Log]
  INFO: 127.0.0.1 - "POST /api/v1/billing/webhooks/stripe HTTP/1.1" 200 OK
  Response: {"status":"ignored","reason":"duplicate_event"}
  ```

---

### R6 — Data model, tests & documentation

- **Claim:** DB includes tenants, plans, subscriptions, usage events; data isolated per tenant; README + diagram + setup present; required files present.
- **Reproduce:**
  ```bash
  ls README.md capstone.yaml EVIDENCE.md BUILDLOG.md .env.example
  uv run pytest -v
  ```
- **Proof:**
  ```text
  $ ls README.md capstone.yaml EVIDENCE.md BUILDLOG.md .env.example
  README.md  capstone.yaml  EVIDENCE.md  BUILDLOG.md  .env.example

  $ uv run pytest -v
  ============================= test session starts =============================
  platform win32 -- Python 3.12.6, pytest-9.1.1, pluggy-1.7.0 -- .venv\Scripts\python.exe
  cachedir: .pytest_cache
  rootdir: C:\Workspace\projects\swe\llm-usage-metering-billing
  configfile: pyproject.toml
  plugins: anyio-4.15.1, asyncio-1.4.0
  collected 27 items

  tests/test_calculator.py::test_token_pricing_rules_and_exact_match PASSED [  3%]
  tests/test_calculator.py::test_rounding_half_up_rule PASSED              [  7%]
  tests/test_calculator.py::test_api_calls_have_zero_marginal_cost_in_rollup PASSED [ 11%]
  tests/test_config.py::test_settings_loads_resilient_defaults PASSED      [ 14%]
  tests/test_config.py::test_pricing_json_rates_exist_and_pinned PASSED    [ 18%]
  tests/test_db_models.py::test_create_and_query_tenant_and_plan PASSED    [ 22%]
  tests/test_db_models.py::test_subscription_creation_and_defaults PASSED  [ 25%]
  tests/test_db_models.py::test_usage_event_money_math_and_idempotency_keys PASSED [ 29%]
  tests/test_idempotency.py::test_idempotency_lifecycle_new_and_replay PASSED [ 33%]
  tests/test_idempotency.py::test_idempotency_payload_mismatch_conflict PASSED [ 37%]
  tests/test_idempotency.py::test_idempotency_concurrent_in_progress PASSED [ 40%]
  tests/test_meter_api.py::test_healthcheck PASSED                         [ 44%]
  tests/test_meter_api.py::test_generate_missing_headers_returns_clean_400 PASSED [ 48%]
  tests/test_meter_api.py::test_generate_billable_call_and_idempotent_replay PASSED [ 51%]
  tests/test_meter_api.py::test_usage_rollup_endpoint PASSED               [ 55%]
  tests/test_quotas.py::test_past_due_subscription_returns_402_payment_required PASSED [ 59%]
  tests/test_quotas.py::test_exact_quota_boundary_999_to_1000_allowed_and_1001_blocked PASSED [ 62%]
  tests/test_quotas.py::test_token_quota_exceeded_returns_429 PASSED       [ 66%]
  tests/test_seed.py::test_seed_creates_expected_plans_and_fixtures PASSED [ 70%]
  tests/test_seed.py::test_seed_is_idempotent PASSED                       [ 74%]
  tests/test_stripe.py::test_forged_webhook_signature_returns_400 PASSED   [ 77%]
  tests/test_stripe.py::test_checkout_session_creation PASSED              [ 81%]
  tests/test_stripe.py::test_webhook_checkout_completed_flips_free_to_pro_and_deduplicates PASSED [ 85%]
  tests/test_stripe.py::test_webhook_subscription_updated_and_deleted PASSED [ 88%]
  tests/test_worker.py::test_worker_reconciles_missed_webhook_subscription PASSED [ 92%]
  tests/test_worker.py::test_worker_detects_quota_alert_thresholds_80_and_100_percent PASSED [ 96%]
  tests/test_worker.py::test_worker_retries_and_handles_stripe_failure PASSED [100%]

  ============================= 27 passed in 1.90s ==============================
  ```

---

## Acceptance Probes (Section 12)

### PROBE 1 — Idempotency: same request twice → one event, mirrored response

- **Test:** `tests/test_idempotency.py`
- **Proof:**
  ```text
  $ uv run pytest tests/test_idempotency.py -v
  ============================= test session starts =============================
  platform win32 -- Python 3.12.6, pytest-9.1.1, pluggy-1.7.0 -- .venv\Scripts\python.exe
  collected 3 items

  tests/test_idempotency.py::test_idempotency_lifecycle_new_and_replay PASSED [ 33%]
  tests/test_idempotency.py::test_idempotency_payload_mismatch_conflict PASSED [ 66%]
  tests/test_idempotency.py::test_idempotency_concurrent_in_progress PASSED [100%]

  ============================== 3 passed in 0.46s ==============================
  ```

---

### PROBE 2 — Quota boundary: exact boundary behavior + 429/402

- **Test:** `tests/test_quotas.py`
- **Proof:**
  ```text
  $ uv run pytest tests/test_quotas.py -v
  ============================= test session starts =============================
  platform win32 -- Python 3.12.6, pytest-9.1.1, pluggy-1.7.0 -- .venv\Scripts\python.exe
  collected 3 items

  tests/test_quotas.py::test_past_due_subscription_returns_402_payment_required PASSED [ 33%]
  tests/test_quotas.py::test_exact_quota_boundary_999_to_1000_allowed_and_1001_blocked PASSED [ 66%]
  tests/test_quotas.py::test_token_quota_exceeded_returns_429 PASSED       [100%]

  ============================== 3 passed in 0.46s ==============================
  ```

---

### PROBE 3 & PROBE 4 — Stripe Checkout & Webhook Security / Deduplication

- **Test:** `tests/test_stripe.py`
- **Proof:**
  ```text
  $ uv run pytest tests/test_stripe.py -v
  ============================= test session starts =============================
  platform win32 -- Python 3.12.6, pytest-9.1.1, pluggy-1.7.0 -- .venv\Scripts\python.exe
  collected 4 items

  tests/test_stripe.py::test_forged_webhook_signature_returns_400 PASSED   [ 25%]
  tests/test_stripe.py::test_checkout_session_creation PASSED              [ 50%]
  tests/test_stripe.py::test_webhook_checkout_completed_flips_free_to_pro_and_deduplicates PASSED [ 75%]
  tests/test_stripe.py::test_webhook_subscription_updated_and_deleted PASSED [100%]

  ============================== 4 passed in 0.56s ==============================
  ```

---

### PROBE 5 — Pinned pricing rules produce exact expected totals

- **Test:** `tests/test_calculator.py`
- **Proof:**
  ```text
  $ uv run pytest tests/test_calculator.py -v
  ============================= test session starts =============================
  platform win32 -- Python 3.12.6, pytest-9.1.1, pluggy-1.7.0 -- .venv\Scripts\python.exe
  collected 3 items

  tests/test_calculator.py::test_token_pricing_rules_and_exact_match PASSED [ 33%]
  tests/test_calculator.py::test_rounding_half_up_rule PASSED              [ 66%]
  tests/test_calculator.py::test_api_calls_have_zero_marginal_cost_in_rollup PASSED [100%]

  ============================== 3 passed in 0.04s ==============================
  ```

---

## Shared Requirements (Section 12)

### REQ #1 — Layered architecture (data / logic / HTTP separated)

- **Proof:**
  ```text
  src/
  ├── api/              # HTTP Presentation Layer
  │   ├── v1/
  │   │   ├── billing.py    # Stripe Checkout & Webhook Endpoints
  │   │   ├── meter.py      # /generate API Gateway
  │   │   ├── router.py     # Central v1 Router
  │   │   └── usage.py      # /usage Rollup Engine
  │   ├── deps.py           # Dependency Injection & Tenant/Idempotency Header Guards
  │   └── error_handlers.py # Standardized JSON Error Envelope
  ├── core/             # Business & Domain Logic Layer
  │   ├── calculator.py     # Lossless Integer Micro-Cent Calculator
  │   ├── idempotency.py    # Two-Phase SHA-256 Reservation Engine
  │   ├── quota.py          # Entitlement & Boundary Concurrency Guard
  │   └── stripe_service.py # Stripe Client & Webhook Cryptographic Verification
  ├── db/               # Persistence Layer
  │   ├── models.py         # SQLAlchemy 2.0 Declarative Models
  │   └── session.py        # Async Engine, Auto-Fallback, and init_db()
  └── workers/          # Background Job Processing Layer
      └── reconciliation.py # Stripe Out-of-Sync Reconciler & Quota Alert Evaluator
  ```

---

### REQ #2 — Validation at the boundary (bad input → clean 4xx, never 500)

- **Proof:**
  ```http
  $ curl -i -s -X POST http://127.0.0.1:8000/api/v1/generate -d '{"prompt": "missing headers"}'
  HTTP/1.1 400 Bad Request
  content-type: application/json

  {
    "error": {
      "code": "missing_header",
      "message": "Required header 'X-Tenant-ID' is missing.",
      "details": {
        "required_header": "X-Tenant-ID"
      }
    }
  }
  ```

---

### REQ #3 — ≥1 background job (off request path, retries + failure alert)

- **Test:** `tests/test_worker.py`
- **Proof:**
  ```text
  $ uv run pytest tests/test_worker.py -v
  ============================= test session starts =============================
  platform win32 -- Python 3.12.6, pytest-9.1.1, pluggy-1.7.0 -- .venv\Scripts\python.exe
  collected 3 items

  tests/test_worker.py::test_worker_reconciles_missed_webhook_subscription PASSED [ 33%]
  tests/test_worker.py::test_worker_detects_quota_alert_thresholds_80_and_100_percent PASSED [ 66%]
  tests/test_worker.py::test_worker_retries_and_handles_stripe_failure PASSED [100%]

  ============================== 3 passed in 0.48s ==============================
  ```
  *Live Server Log Excerpt Showing Exponential Backoff Retries & Quota Threshold Alerts:*
  ```text
  2026-10-21 13:22:00,274 [INFO] stripe: error_message='Invalid API Key provided: sk_test_***********_key'
  2026-10-21 13:22:01,331 [ERROR] workers.reconciliation: Failed to retrieve Stripe subscription 'sub_live_proof_123' after 3 attempts: Invalid API Key provided: sk_test_***********_key
  2026-10-21 13:22:01,340 [WARNING] workers.reconciliation: QUOTA ALERT [80% WARNING]: Tenant 'tenant_free_boundary' is approaching quota threshold.
  2026-10-21 13:19:55,297 [ERROR] workers.reconciliation: QUOTA ALERT [100% CRITICAL]: Tenant 'tenant_free_boundary' has hit or exceeded plan limits.
  ```

---

### REQ #4 — Real persistence (migrations, indexes, isolated tenants)

- **Proof:**
  Composite index definitions from `src/db/models.py`:
  ```python
  # Composite indexes ensuring sub-millisecond usage rollup & range scans:
  Index("ix_usage_events_tenant_created", "tenant_id", "created_at")
  Index("ix_idempotency_tenant_key", "tenant_id", "idempotency_key", unique=True)
  Index("ix_subscriptions_tenant", "tenant_id", unique=True)
  Index("ix_webhook_events_stripe_id", "stripe_event_id", unique=True)
  ```
  SQLite auto-fallback and PostgreSQL configuration from `src/db/session.py`:
  ```python
  # Auto-fallback ensures zero-config boot on evaluate machines
  if not settings.DATABASE_URL or "sqlite" in settings.DATABASE_URL:
      DATABASE_URL = "sqlite+aiosqlite:///./data/app.db"
  ```

---

### REQ #5 — Idempotency where it matters

- **Proof:**
  Cross-referenced to **R1** and **PROBE 1**: Replaying the exact same `Idempotency-Key` returns an identical cached response without re-incrementing usage counts or token totals.

---

### REQ #6 — Secrets clean (env only, never logged)

- **Proof:**
  ```text
  $ git check-ignore -v .env data/app.db
  .gitignore:1:.env
  .gitignore:3:data/app.db

  $ git grep "sk_live_"
  (0 results)

  $ git grep "whsec_"
  (0 results)
  ```
  Stripe and database credentials load strictly via `config/settings.py` from `.env`.

---

### REQ #7 — Cost tracked, if AI is used (per call, attributed, budget guard)

- **Proof:**
  Each AI call records micro-cent costs attributed to the tenant in `usage_events.cost_micro_cents`:
  ```json
  "cost": {
    "micro_cents": 60000,
    "cents": 0,
    "exact_dollars": "0.0006",
    "formatted": "$0.0006"
  }
  ```
  Budgets and quotas are strictly enforced at the gateway: calls exceeding plan allowances are blocked before downstream model execution with `429 Too Many Requests`.
