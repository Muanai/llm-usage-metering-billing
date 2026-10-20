# Design Document: Usage Metering & Billing Engine

- **Author**: Antigravity & Engineer
- **Date**: 2026-10-20
- **Status**: Validated Design (Ready for Review & Planning)
- **Target Repo**: `llm-usage-metering-billing` (FlyRank Backend Track Capstone)

---

## 1. Executive Summary & Mission

Every SaaS product must reliably answer three fundamental questions:
1. **How much has this customer used?** (Usage Metering)
2. **How much should they pay?** (Money Math & Cost Calculation)
3. **Have they reached their plan limits?** (Quota Enforcement)

This service provides an idempotent, multi-tenant backend engine that meters billable events (API calls and simulated AI tokens), enforces plan quotas at strict numerical boundaries (yielding `429 Too Many Requests` or `402 Payment Required`), executes accurate integer-based token cost calculations, synchronizes subscription state via Stripe test mode webhooks with signature verification and replay protection, and runs a background reconciliation and quota alert worker.

---

## 2. Explicit Non-Goals (Scope Boundaries)

To ensure zero-risk correctness and respect capstone constraints:
- **No Real Payments or Live Mode**: The system operates strictly within Stripe Test Mode using test card numbers (e.g. `4242 4242 4242 4242`).
- **No Live External AI API Calls**: Token counts are metered and simulated via payload metadata; no external LLM API key or paid credit is required.
- **No Invoicing, Proration, or Overage Billing in Core**: Core focuses strictly on hard quotas and recurring plans (Free vs. Pro). Advanced invoicing and mid-cycle proration are deferred as optional post-core stretch goals.
- **No Floating-Point Math for Currency**: Money amounts are strictly stored and computed as integer units (cents and micro-cents).
- **No Per-Call Marginal Pricing**: This is a flat-fee subscription model. API calls are metered purely for quota enforcement and carry **zero marginal cost**; the only cost components are token usage and the plan's base subscription fee.
- **No Rounding of Stored or Intermediate Values**: Exact integer micro-cents are persisted and summed; rounding (half-up) happens once, at the presentation boundary only.

---

## 3. Technology Stack & Runtime Architecture

- **Language & Runtime**: Python 3.11+
- **Web Framework**: FastAPI (asynchronous, auto-generating OpenAPI/Swagger UI at `/docs`)
- **Data Validation & Settings**: Pydantic v2 + Pydantic-Settings
- **ORM & Database Driver**: SQLAlchemy 2.0 (asyncio) + AsyncPG (PostgreSQL) / aiosqlite (SQLite fallback)
- **Migrations**: Alembic
- **Test Suite**: Pytest + pytest-asyncio + HTTPX
- **Database**: PostgreSQL (via Docker Compose) with automatic zero-configuration SQLite fallback for seamless single-command testing.
- **Stripe Tooling**: Stripe Python SDK (`stripe`) + Stripe CLI (`stripe listen`, `stripe trigger`)

### Zero-Config Startup Contract
The evaluator must be able to boot the system on a clean machine with **one command and no Docker**. The startup path is:

1. `run:` executes `uvicorn src.main:app --host 0.0.0.0 --port 8000` (see `capstone.yaml`).
2. On FastAPI lifespan startup, the app **auto-runs Alembic migrations** (`alembic upgrade head`) against the configured database before serving traffic. No manual migration step.
3. If `DATABASE_URL` is unset or points at Postgres that is unreachable, the app **falls back to a local SQLite file** (`./data/app.db`) and logs a clear warning. Postgres via `docker compose up` is the recommended path, never a hard requirement.
4. `seed:` runs `python -m scripts.seed`, which is idempotent and safe to re-run.

This guarantees Layer 1 (submission pack) passes even on a machine with no Docker and no Postgres.

### Layered Architecture Structure

```text
llm-usage-metering-billing/
├── config/
│   ├── settings.py               # Pydantic Settings (.env configuration & validation)
│   └── pricing.json              # Pinned pricing rules & plan constants
├── src/
│   ├── api/                      # Layer 1: HTTP Controllers & Boundary Validation
│   │   ├── deps.py               # Dependency Injection (DB session, Tenant context)
│   │   ├── error_handlers.py     # Global exception handlers (clean 4xx JSON, no 500 leaks)
│   │   └── v1/
│   │       ├── router.py         # Main v1 API router aggregation
│   │       ├── meter.py          # POST /api/v1/generate (Dummy billable endpoint)
│   │       ├── usage.py          # GET /api/v1/usage & GET /api/v1/tenants/{id}/usage
│   │       └── billing.py        # POST /api/v1/billing/checkout & /webhooks/stripe
│   ├── core/                     # Layer 2: Core Domain Logic & Calculations
│   │   ├── idempotency.py        # Request deduplication & cached response replay
│   │   ├── quota.py              # Boundary checker (429 quota vs 402 payment status)
│   │   ├── calculator.py         # Integer micro-unit money math & token pricing rules
│   │   └── stripe_service.py     # Stripe API client, webhook verification & subscription mapping
│   ├── db/                       # Layer 3: Persistence & Data Access
│   │   ├── session.py            # Engine & sessionmaker (PostgreSQL / SQLite auto-switch)
│   │   ├── models/               # SQLAlchemy models (Tenant, Plan, Subscription, UsageEvent, etc.)
│   │   └── repositories/         # Repositories for transactional ACID database operations
│   ├── workers/                  # Layer 4: Background Jobs (Requirement #3)
│   │   └── reconciliation.py     # Periodic Stripe subscription sync & quota alert evaluator
│   └── main.py                   # FastAPI application factory, lifespan, & middleware
├── migrations/                   # Alembic schema migrations
├── scripts/
│   └── seed.py                   # Demo data seeder (plans, tenants, baseline usage)
├── tests/                        # Acceptance probe test suite
├── docker-compose.yml            # PostgreSQL container definition
├── capstone.yaml                 # Evaluator manifest: run, seed, test, base_url
├── EVIDENCE.md                   # Pasted proof per acceptance requirement
├── BUILDLOG.md                   # AI usage log & engineering decision records
├── README.md                     # Comprehensive setup, architecture & limitations guide
└── .env.example                  # Safe configuration template
```

---

## 4. Data Model & Database Schema

```mermaid
erDiagram
    TENANTS ||--o{ SUBSCRIPTIONS : "has"
    TENANTS ||--o{ USAGE_EVENTS : "records"
    TENANTS ||--o{ IDEMPOTENCY_KEYS : "owns"
    PLANS ||--o{ SUBSCRIPTIONS : "assigned"

    TENANTS {
        string id PK "e.g., 'tenant_a'"
        string name "Organization name"
        string email "Contact email"
        datetime created_at
        datetime updated_at
    }

    PLANS {
        string id PK "'free' or 'pro'"
        string name "Plan Name"
        int monthly_api_limit "1,000 (Free) / 50,000 (Pro)"
        int monthly_token_limit "100,000 (Free) / 5,000,000 (Pro)"
        int base_price_cents "0 for Free, 2900 for Pro ($29)"
        string stripe_price_id "Stripe test mode price ID"
    }

    SUBSCRIPTIONS {
        string id PK "UUID"
        string tenant_id FK "Unique per tenant"
        string plan_id FK "Ref to plans.id"
        string stripe_customer_id "cus_test_..."
        string stripe_subscription_id "sub_test_..."
        string status "'active', 'past_due', 'canceled', 'incomplete'"
        datetime current_period_start
        datetime current_period_end
        datetime updated_at
    }

    USAGE_EVENTS {
        string id PK "UUID"
        string tenant_id FK "Index (tenant_id, created_at)"
        string event_type "'api_call' or 'ai_tokens'"
        int quantity "Total count"
        int input_tokens "Standard input tokens"
        int cached_input_tokens "Cached input tokens"
        int output_tokens "Standard output tokens"
        int reasoning_tokens "Reasoning tokens"
        bigint cost_micro_cents "Integer micro-cents (1 USD = 100,000,000)"
        string idempotency_key "Indexed"
        datetime created_at "Timestamp"
    }

    IDEMPOTENCY_KEYS {
        string id PK "UUID"
        string tenant_id FK
        string idempotency_key "Unique constraint: (tenant_id, idempotency_key)"
        string request_path "API route path"
        string request_hash "SHA-256 of normalized request body"
        int response_status_code "Cached HTTP status (e.g. 200, 429)"
        text response_body "Stored JSON response string"
        datetime created_at
        datetime expires_at "TTL timestamp"
    }

    WEBHOOK_EVENTS {
        string stripe_event_id PK "e.g., 'evt_...'"
        string event_type "e.g., 'checkout.session.completed'"
        string status "'processed', 'failed', 'ignored'"
        datetime processed_at
    }
```

---

## 5. Core Engine Specifications

### 5.1 Exactly-Once Metering & Idempotency Engine
- **Headers Required**: `X-Tenant-ID: <string>` and `Idempotency-Key: <string>`.
- **Deduplication Logic**:
  1. Inspect `IDEMPOTENCY_KEYS` table for `(tenant_id, idempotency_key)`.
  2. If found:
     - Verify SHA-256 hash of request payload against stored `request_hash`.
     - If hash differs: return `400 Bad Request` with message `"Idempotency-Key reused with different request payload"`.
     - If hash matches: return the previously stored status code, headers, and response body immediately. No usage event is created.
  3. If not found:
     - Run quota checks and billable action within a single database transaction.
     - Save response snapshot and status into `IDEMPOTENCY_KEYS`.
     - Return the freshly generated response.
- **Concurrency Safety**: Enforced via DB unique constraint `UNIQUE (tenant_id, idempotency_key)` and row-level locking to prevent race conditions during simultaneous identical requests.
- **Key Scope**: Idempotency keys are **globally unique per tenant across every endpoint**, not per route — one key = one execution from the client. The constraint is `UNIQUE (tenant_id, idempotency_key)`; `request_path` is stored for diagnostics only and is not part of the uniqueness key.

### 5.1b Event Granularity (One Request = One Event)
A single `POST /api/v1/generate` call produces **exactly one** `USAGE_EVENTS` row - never two. That row carries both dimensions of the request:

- `event_type = "api_call"`, `quantity = 1` (drives the API-call quota counter), **and**
- the four token fields populated (`input_tokens`, `cached_input_tokens`, `output_tokens`, `reasoning_tokens`) plus the computed `cost_micro_cents` (drive the token quota counter and cost rollup).

Consequences:
- The API-call rollup counts rows where `event_type = "api_call"` (equivalently, sums `quantity`).
- The token rollup sums the token columns across those same rows; it does not require a separate `ai_tokens` event.
- Idempotency replay returns the single stored response for the single stored event - there is no partial-event state to reconcile.
- `event_type = "ai_tokens"` remains a valid enum value for a future token-only endpoint, but the core `/generate` flow does not emit it.

### 5.2 Quota Enforcement & Boundary Honesty
- **Rule**: Check is evaluated *before* the billable action executes:
  $$\text{current\_monthly\_usage} + \text{requested\_quantity} \le \text{plan\_limit}$$
- **Boundary Behavior**:
  - Boundary: Free Plan limit = 1,000 API calls.
  - At 999 used: request requesting 1 call $\rightarrow$ **Allowed (200 OK)**. Usage becomes 1,000.
  - At 1,000 used: request requesting 1 call $\rightarrow$ **Blocked (429 Too Many Requests)**.
- **Concurrency Safety**: Quota evaluation and event insertion happen inside one transaction that first takes a row lock on the tenant (`SELECT ... FROM tenants WHERE id = :tenant_id FOR UPDATE`). All billable requests for a tenant therefore serialize, so two concurrent requests can never both observe `999/1000` and both be admitted. The tradeoff — reduced per-tenant throughput — is accepted in exchange for provably exact boundaries.
- **Status Code Demarcation**: Account health is evaluated **before** the numeric quota. A subscription that is not in good standing fails with `402` regardless of remaining quota; only a healthy account reaches the quota check and its `429`.

  | Condition | Status | Meaning |
  |---|---|---|
  | Subscription status not in good standing (`past_due`, `canceled`, `unpaid`, `incomplete`) | `402 Payment Required` | Payment or card update required before any billable action |
  | Account healthy, `current + requested > limit` | `429 Too Many Requests` | Monthly allowance exhausted; includes `Retry-After: <seconds>` |
  | Account healthy, `current + requested <= limit` | `200 OK` | Action admitted and metered |

- **Free Tenants at Quota**: A Free tenant with an active plan that exhausts its allowance receives **`429`**, never `402` — their plan is healthy, their allowance is not. The upgrade path is surfaced in the response body (`upgrade_required: true`, `upgrade_plan: "pro"`), not by overloading the status code.

### 5.3 Cost Calculation & Token Pricing Rules
- **Pricing Model**: Flat-fee subscription. The billable cost of a request is token usage priced per category, plus the plan's base fee. API calls carry **zero marginal cost** and exist only to drive quota enforcement; they never appear as a line item in cost.
- **Rule 1**: Cached input tokens are priced at a discount compared to fresh input tokens.
- **Rule 2**: Reasoning tokens count and are priced as output tokens.
- **Rule 3**: Token categories are never summed indiscriminately; each is computed with its specific rate.
- **Formula**:
  $$\text{Cost}_{\text{micro}} = (\text{fresh\_input} \times R_{\text{input}}) + (\text{cached\_input} \times R_{\text{cached}}) + ((\text{output} + \text{reasoning}) \times R_{\text{output}})$$
- **Precision & Rounding**:
  - Every per-event cost is stored as an exact integer **micro-cent** value ($1\text{ USD} = 100\text{ cents} = 100{,}000{,}000\text{ micro-cents}$). No rounding or truncation is applied at write time.
  - Rollups **sum exact micro-cents first, then round once at the presentation boundary** using **round-half-up** (the accounting standard). Intermediate values are never rounded and then summed — that accumulates drift and will mismatch PROBE 5's exact totals.
  - The API returns all three representations so callers can validate: `micro_cents` (exact integer, authoritative), `cents` (round-half-up of the *total*, as a decimal, derived), and `formatted` (display string, derived). Only `micro_cents` is billing truth.
  - `cents` and `formatted` are computed on read and never persisted as billing truth.
- **Pinned Rates (`config/pricing.json`)**:
  - Standard Input: $2.00 / 1M tokens ($200\text{ cents} / 1\text{M} = 200\text{ micro-cents/token}$)
  - Cached Input: $0.50 / 1M tokens ($50\text{ cents} / 1\text{M} = 50\text{ micro-cents/token}$, 75% discount)
  - Output Tokens: $8.00 / 1M tokens ($800\text{ cents} / 1\text{M} = 800\text{ micro-cents/token}$)
  - Reasoning Tokens: $8.00 / 1M tokens ($800\text{ cents} / 1\text{M} = 800\text{ micro-cents/token}$)

### 5.4 Stripe Test Mode Integration & Webhooks
- **Checkout (`POST /api/v1/billing/checkout`)**:
  - Creates a Stripe Checkout Session in subscription mode with `client_reference_id = tenant_id`.
  - Returns `checkout_url` and `session_id`.
- **Webhook Endpoint (`POST /api/v1/billing/webhooks/stripe`)**:
  - Uses raw request byte payload and `Stripe-Signature` header with `stripe.Webhook.construct_event`.
  - Forged signatures or missing headers $\rightarrow$ immediate `400 Bad Request` with no state modification.
  - Replay deduplication: Checks `WEBHOOK_EVENTS` for `stripe_event_id`. If already present $\rightarrow$ returns `200 OK` with `{"status": "ignored", "reason": "duplicate_event"}`.
  - Event Handlers:
    - `checkout.session.completed`: Sets tenant's plan to `pro`, status to `active`, records Stripe customer & subscription IDs.
    - `customer.subscription.updated`: Updates subscription status (`active`, `past_due`, etc.).
    - `customer.subscription.deleted`: Reverts tenant to `free` plan and clears Stripe subscription IDs.
  - **Ordering & Upsert**: Stripe does not guarantee delivery order. Handlers **upsert** rather than assume a prior event created the row, so a `customer.subscription.updated` arriving before `checkout.session.completed` is applied rather than dropped. Plan identity is resolved from the event's Stripe price ID against `plans.stripe_price_id`; the tenant is resolved from the stored customer/subscription mapping, or from `client_reference_id` when present. The checkout session persists `stripe_customer_id` before redirect so later subscription events can always resolve their tenant.

### 5.5 Background Job: Stripe Reconciliation & Quota Alerts
- **Worker Script / Background Task**: `src/workers/reconciliation.py`.
- **Responsibilities**:
  1. **Reconciliation**: Periodically queries tenants with active Stripe subscriptions, calls Stripe API to cross-verify subscription state, and reconciles any discrepancies caused by missed webhooks.
  2. **Quota Alerting**: Checks current cycle usage against plan limits. Emits structured log alerts at $\ge 80\%$ and $\ge 100\%$ thresholds.
  3. **Resilience**: Implements exponential backoff retries on network failures and logs failure alerts if retries are exhausted.
- **Execution Model (Hybrid)**: The worker is both runnable standalone and self-scheduling:
  - **Standalone (A)**: `python -m src.workers.reconciliation` runs one full pass and exits, so it can be driven by cron, a supervisor, or the evaluator directly. This is the canonical entry point referenced by `capstone.yaml`.
  - **Embedded (B)**: When `WORKER_ENABLED=true`, the FastAPI lifespan starts an APScheduler job that invokes the same pass on a fixed interval (default 15 min), so a single `run:` command boots both API and worker.
  - Both paths call the identical `run_once()` function; there is no duplicated logic. The embedded scheduler is disabled by default in tests to keep them deterministic.

---

### 5.6 Billing Period Windows
"Monthly" is resolved differently by plan, because the two plans have different sources of truth:

- **Free plan**: the window is the **calendar month** in UTC - `[first day 00:00:00Z, first day of next month 00:00:00Z)`. No Stripe object governs a Free tenant's cycle.
- **Paid plan (Pro)**: the window is the **Stripe billing period** - `[current_period_start, current_period_end)` from the subscription record, refreshed by `customer.subscription.updated`. This is the period the customer is actually invoiced for.

**Rules:**
- A usage event is attributed to the window in effect **at the moment it is recorded**. Usage is never retroactively re-bucketed.
- On a Free to Pro upgrade, the new Stripe period begins fresh; the partial calendar-month usage already recorded on Free does not carry into the Pro period's counters and is not prorated (proration is a declared non-goal).
- On a Pro to Free downgrade, the tenant re-enters the calendar-month window; usage already accumulated in the current calendar month counts, so a downgraded tenant may immediately be at or near its Free quota.
- If a paid subscription's `current_period_end` has passed and no `customer.subscription.updated` refreshed it, the reconciliation worker re-fetches the subscription from Stripe; until it does, the last known period is used and the discrepancy is logged.

### 5.7 Standardized Error Envelope
Every error response, on every endpoint, uses one shape. No endpoint returns a bare string or an ad-hoc object.

```json
{
  "error": {
    "code": "quota_exceeded",
    "message": "Human-readable explanation.",
    "details": { }
  }
}
```

- `code`: stable machine-readable snake_case identifier (e.g. `quota_exceeded`, `payment_required`, `invalid_signature`, `missing_idempotency_key`, `idempotency_key_reuse`, `validation_error`).
- `message`: human-readable, safe to display.
- `details`: optional object with context (metric, current_usage, limit, plan, field errors). Omitted when empty.

**Status mapping:**

| Situation | Status | `code` |
|---|---|---|
| Missing/invalid `Idempotency-Key` header | `400` | `missing_idempotency_key` |
| Same key, different payload | `400` | `idempotency_key_reuse` |
| Request body fails schema validation | `422` | `validation_error` |
| Subscription not in good standing | `402` | `payment_required` |
| Healthy account over quota | `429` | `quota_exceeded` |
| Webhook signature invalid/missing | `400` | `invalid_signature` |
| Webhook payload unparseable | `400` | `invalid_payload` |
| Unexpected server fault | `500` | `internal_error` (generic message; no stack traces or secrets leaked) |

The global exception handlers in `src/api/error_handlers.py` guarantee this shape, including for framework-raised validation errors (FastAPI's default `422` body is remapped into the envelope).

---

### 5.8 Seed Fixtures, Stripe Provisioning & Test Isolation

#### 5.8.1 Fixed Tenant Fixtures (Seed Contract)
`python -m scripts.seed` is idempotent and creates a **fixed, documented set of tenants with stable IDs**. Evaluators and tests depend on these exact IDs, so they are part of the contract:

| Tenant ID | Plan | Subscription status | Seeded usage | Purpose |
|---|---|---|---|---|
| `tenant_free_fresh` | free | active | 0 API calls, 0 tokens | Clean baseline; PROBE 1 (idempotency) |
| `tenant_free_boundary` | free | active | 999 API calls | PROBE 2 (boundary: next call → 200, then 429) |
| `tenant_pro_active` | pro | active | 150 API calls, 125k tokens | PROBE 5 (pricing rollup), PROBE 3 target |
| `tenant_pro_past_due` | pro | past_due | 0 | 402 path (account health before quota) |

The seeder also upserts the two `plans` rows (`free`, `pro`) with their pinned limits and prices. Re-running the seeder resets these tenants to their documented baseline state (delete-then-insert for their usage events) so probes are reproducible.

#### 5.8.2 Stripe Test-Mode Provisioning
Checkout requires a real Stripe test `price_id` for the Pro plan, which is account-specific. The seeder **auto-provisions** it:

1. On run, the seeder looks up an existing test price by a stable lookup key (`flyrank_pro_monthly`).
2. If absent, it creates the product + recurring price via the Stripe API (test mode) and stores the resulting `price_id` in the `plans` row.
3. If `STRIPE_SECRET_KEY` is unset, the seeder skips Stripe provisioning, logs a warning, and leaves `stripe_price_id` null — the API still boots and all non-Stripe probes still pass.

**Documented CLI fallback** (in README, for when auto-provisioning is undesirable):
```
stripe prices create --unit-amount 2900 --currency usd \
  --recurring interval=month --lookup-key flyrank_pro_monthly
```
The resulting `price_...` ID is pasted into `.env` as `STRIPE_PRO_PRICE_ID`.

#### 5.8.3 Test Isolation
Tests never touch the development or evaluator database, and never start the scheduler:

- **Isolated database**: the test suite sets `DATABASE_URL` to a dedicated SQLite file (or a per-run temp file) created fresh at session start and torn down at the end. Each test runs inside a transaction that is rolled back, so tests are order-independent and deterministic.
- **Direct worker invocation**: worker tests call `run_once()` directly. The APScheduler job is never started under test (`WORKER_ENABLED=false` in the test environment), so there is no background timer racing the assertions.
- **No live Stripe calls in unit tests**: webhook tests construct signed payloads locally with the test `whsec_` secret and post them to the endpoint; they do not call the Stripe API. Only the optional end-to-end Checkout walkthrough (documented in README, not part of `test:`) touches Stripe's servers.

---
## 6. API Interface Contracts

### 1. `POST /api/v1/generate` (Billable Endpoint)
- **Headers**:
  - `X-Tenant-ID: tenant_a` (Required)
  - `Idempotency-Key: req_abc123` (Required)
- **Request Body**:
  ```json
  {
    "model": "gemini-1.5-pro",
    "prompt": "Summarize this quarterly report",
    "simulated_tokens": {
      "input_tokens": 1000,
      "cached_input_tokens": 500,
      "output_tokens": 400,
      "reasoning_tokens": 100
    }
  }
  ```
- **Success Response (200 OK)**:
  ```json
  {
    "id": "evt_019283aa-8812-70b1-bce2-6412e8b091f0",
    "tenant_id": "tenant_a",
    "status": "success",
    "result": "Simulated AI generation output text.",
    "usage": {
      "api_calls": 1,
      "input_tokens": 1000,
      "cached_input_tokens": 500,
      "output_tokens": 400,
      "reasoning_tokens": 100,
      "total_tokens": 2000
    },
    "cost": {
      "micro_cents": 625000,
      "cents": 1,
      "formatted": "$0.00625"
    }
  }
  ```
- **Error Response (429 Too Many Requests)**:
  ```json
  {
    "error": {
      "code": "quota_exceeded",
      "message": "Monthly API call limit reached for Free plan (1000/1000). Upgrade to Pro to continue.",
      "details": {
        "metric": "api_calls",
        "current_usage": 1000,
        "requested": 1,
        "limit": 1000,
        "plan": "free",
        "upgrade_required": true,
        "upgrade_plan": "pro",
        "retry_after_seconds": 1728000
      }
    }
  }
  ```

### 2. `GET /api/v1/tenants/{tenant_id}/usage` (Rollup Endpoint)
- **Response (200 OK)**:
  ```json
  {
    "tenant_id": "tenant_a",
    "period": "2026-10",
    "plan": {
      "id": "pro",
      "name": "Pro Plan",
      "base_price_cents": 2900
    },
    "subscription_status": "active",
    "usage": {
      "api_calls": {
        "used": 150,
        "limit": 50000,
        "percent_used": 0.3
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
      "base_subscription_micro_cents": 2900000000,
      "total_micro_cents": 2951250000,
      "total_cents": 2951,
      "formatted": "$29.51"
    }
  }
  ```

### 3. `POST /api/v1/billing/checkout`
- **Request Body**:
  ```json
  {
    "tenant_id": "tenant_a",
    "plan_id": "pro"
  }
  ```
- **Response (200 OK)**:
  ```json
  {
    "checkout_url": "https://checkout.stripe.com/c/pay/cs_test_a1b2c3...",
    "session_id": "cs_test_a1b2c3..."
  }
  ```

### 4. `POST /api/v1/billing/webhooks/stripe`
- **Headers**: `Stripe-Signature: t=...,v1=...`
- **Body**: Raw binary Stripe event payload.
- **Responses**:
  - `200 OK`: `{"status": "success", "event_id": "evt_..."}` or `{"status": "ignored", "reason": "duplicate_event"}`
  - `400 Bad Request`: `{"error": {"code": "invalid_signature", "message": "Webhook signature verification failed."}}` or `{"error": {"code": "invalid_payload", "message": "Webhook payload could not be parsed."}}`

---

## 7. Acceptance Probes & Verification Matrix

| Probe | Requirement Checked | Verification Test File |
|---|---|---|
| **PROBE 1** | Exactly-once metering via idempotency key (same key $\rightarrow$ 1 event, identical response) | `tests/test_idempotency.py` |
| **PROBE 2** | Quota boundary honesty: at 1,000/1,000 $\rightarrow$ 200 OK, at 1,001 $\rightarrow$ 429 Too Many Requests; inactive plan $\rightarrow$ 402 Payment Required | `tests/test_quotas.py` |
| **PROBE 3** | Stripe Checkout test completion $\rightarrow$ webhook flips tenant Free $\rightarrow$ Pro; `/usage` limits updated | `tests/test_stripe_webhooks.py` |
| **PROBE 4** | Webhook forged signature $\rightarrow$ 400 Bad Request; Webhook replay twice $\rightarrow$ processed once | `tests/test_stripe_webhooks.py` |
| **PROBE 5** | AI token pricing: cached-input 75% discount, reasoning tokens billed as output, rollup matches pinned constants | `tests/test_pricing.py` |
| **REQ #3** | Background worker reconciliation & quota alert detection with retries | `tests/test_worker.py` |

---

## 8. Submission Artifacts

1. **`capstone.yaml`**: Standard manifest with `run:`, `seed:`, `test:`, and endpoints list.
2. **`EVIDENCE.md`**: Direct terminal transcripts and test execution proofs for each probe and requirement.
3. **`BUILDLOG.md`**: Transparent log of design choices, AI pairing iterations, and debugging steps.
4. **`README.md`**: Architectural diagram, quickstart setup, Stripe CLI testing guide, and documented limitations.
5. **`.env.example`**: Safe environment variable template.
