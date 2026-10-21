# BUILDLOG.md: Engineering Journey & AI Pairing Reflections

This log documents the architecture, key decisions, debugging discoveries, and pair-programming reflections during the construction of the **FlyRank LLM Usage Metering & Billing Engine**.

---

## 1. System Goals & Philosophy

The objective was to deliver a production-grade, multi-tenant usage metering and subscription billing engine for AI applications adhering strictly to a single runtime command (`capstone.yaml`), running completely on a $0 stack with zero floating-point financial drift, atomic idempotency, and resilient Stripe test-mode billing.

The development followed the **VibeWise** pairing methodology:
> *"You build. AI writes."* The human developer acts as the lead architect directing strategy and trade-offs; the AI provides architectural inquiry, implements clean TDD code, and validates invariants with concrete proofs.

---

## 2. Key Architectural Decisions & Trade-Offs

### 1. Unified Single Service vs. Distributed Microservices
- **Decision:** Built as a single FastAPI application with internal domain modules (`src/api`, `src/core`, `src/db`, `src/workers`) running under one process.
- **Trade-off:** Chose an in-process modular monolith over microservices. A single process eliminates distributed transactions, network latency between meter and ledger, and operational complexity, while perfectly satisfying the requirement that the evaluator boot the system with one command.

### 2. Lossless Integer Money Math ($1\text{ USD} = 100{,}000{,}000\ \mu\text{c}$)
- **Decision:** Every token price and computed cost is represented as an integer micro-cent ($10^{-6}$ cents).
- **Trade-off:** Avoided Python `float` and decimal float conversions in the database. Pinned pricing rates (standard input: 200 $\mu$c/1k; cached input: 50 $\mu$c/1k; output: 800 $\mu$c/1k; reasoning: 800 $\mu$c/1k) allow lossless multiplication and addition. `ROUND_HALF_UP_AT_INVOICE` is applied only at the final invoice boundary. API calls carry zero marginal usage cost, serving strictly for quota boundary checks.

### 3. Exactly-Once Metering & Two-Phase Idempotency (PROBE 1)
- **Decision:** Built a Two-Phase Reservation pattern with SHA-256 payload hashing (`IdempotencyKey`).
- **Trade-off:** Pre-allocating the key in status `processing` prevents race conditions where identical concurrent requests attempt duplicate execution. If a request is identical, it replays the cached response (`200 OK`); if the payload differs, it returns `400 Bad Request` (`idempotency_key_reuse`). Each billable call records strictly **one** row in `usage_events`.

### 4. Quota Enforcement & Financial Default Precedence (PROBE 2)
- **Decision:** Evaluated status before usage volume. Subscriptions in `past_due`, `unpaid`, or `canceled` immediately return `402 Payment Required`. If active, volume checks evaluate cumulative usage inside the billing window against plan limits.
- **Trade-off:** Dialect-aware row locking (`with_for_update()`) on PostgreSQL prevents boundary overshoots. At 999/1,000, call 1,000 succeeds; the 1,001st call is rejected with `429 Too Many Requests` and a dynamic `Retry-After` header.

### 5. Resilient Stripe Integration & Webhook Security (PROBE 3 & 4)
- **Decision:** Webhook processing verifies signatures against the raw binary request stream, deduplicates event IDs in `webhook_events`, and utilizes timestamp guards (`stripe_last_event_created_at`) to ignore out-of-order deliveries.
- **Trade-off:** Offline fallback logic ensures that the entire system boots and passes tests even without active Stripe credentials or internet access.

### 6. Tooling Migration to `uv`
- **Decision:** Migrated package and dependency management to `uv` (`pyproject.toml`, `uv.lock`).
- **Trade-off:** Lightning-fast deterministic test runs (<2s) and virtualenv synchronization without global pip pollution.

---

## 3. Notable Bugs & Debugging Insights

### Bug 1: SQLAlchemy 2.0 AsyncIO with Python 3.12 Greenlet Requirement
- **Symptom:** Running async tests raised `ValueError: the greenlet library is required to use this function with asyncio`.
- **Root Cause:** SQLAlchemy's async engine uses `greenlet` under the hood to manage async context transitions.
- **Resolution:** Explicitly added `greenlet>=3.0.0` to `pyproject.toml` and re-synced via `uv`.

### Bug 2: Stripe Python SDK 8+ StripeObject Access
- **Symptom:** Webhook handler crashed with `AttributeError: 'get' is a dict method, but a Event is not a dict.`
- **Root Cause:** In Stripe Python SDK 8+, `stripe.Webhook.construct_event` returns an `Event` object (subclass of `StripeObject`) which does not expose dict `.get()`.
- **Resolution:** Updated `process_webhook_event` to inspect for `.to_dict_recursive()` or `.to_dict()` and convert to a standard Python dictionary before extracting fields.

### Bug 3: Direct Script Path Resolution
- **Symptom:** Running `python scripts/seed.py` failed with `ModuleNotFoundError: No module named 'config'`.
- **Root Cause:** Direct script execution sets `sys.path[0]` to `scripts/` rather than the workspace root.
- **Resolution:** Added `project_root = str(Path(__file__).resolve().parent.parent); sys.path.insert(0, project_root)` at the top of utility scripts so both `python scripts/seed.py` and `python -m scripts.seed` work seamlessly.

---

## 4. Test & Verification Summary

- **Automated Tests:** 27/27 unit and integration tests passing (`uv run pytest -v`).
- **Live Acceptance Probes:** All 5 acceptance probes executed against running server (`scripts/run_probes.py`), proving 100% adherence to the capstone brief.
- **Clean Secrets & Hygiene:** Verified `.gitignore` prevents leaks, `.env.example` contains clean placeholders, and all data operations are strictly isolated per tenant.
