# EVIDENCE.md

Proof for every requirement in the capstone brief (Section 6) and every acceptance probe (Section 12).
Each section below has a **Claim**, a **How to reproduce** command, and a **Proof** block to paste real output into.

> Rule: a claim without pasted evidence scores as not done. Fill each `Proof` block with a real terminal transcript, test output, or log line — not a description of one.

---

## Requirements (Section 6)

### R1 — Metering: exactly one usage event per billable action, even under retries

- **Claim:** The same request with the same `Idempotency-Key` creates exactly one usage event; the second response mirrors the first.
- **Reproduce:**
  ```bash
  curl -s -X POST http://localhost:8000/api/v1/generate \
    -H "X-Tenant-ID: tenant_free_fresh" \
    -H "Idempotency-Key: demo-key-1" \
    -H "Content-Type: application/json" \
    -d '{"model":"gemini-1.5-pro","prompt":"hi","simulated_tokens":{"input_tokens":100,"cached_input_tokens":0,"output_tokens":50,"reasoning_tokens":0}}'
  # run the exact same command a second time
  ```
- **Proof:**
  ```
  <paste both responses here; note identical `id` and that usage count stays at 1>
  ```

### R2 — Quotas: usage checked against plan; over-limit rejected with correct status + message

- **Claim:** At 999/1000 the next call is admitted (200); at 1000/1000 the next call is rejected with `429` and a clear message. A `past_due` subscription is rejected with `402`.
- **Reproduce:**
  ```bash
  # boundary tenant is seeded at 999 API calls
  curl -i -X POST http://localhost:8000/api/v1/generate -H "X-Tenant-ID: tenant_free_boundary" -H "Idempotency-Key: b1" -d '{...}'   # expect 200
  curl -i -X POST http://localhost:8000/api/v1/generate -H "X-Tenant-ID: tenant_free_boundary" -H "Idempotency-Key: b2" -d '{...}'   # expect 429
  curl -i -X POST http://localhost:8000/api/v1/generate -H "X-Tenant-ID: tenant_pro_past_due" -H "Idempotency-Key: p1" -d '{...}'    # expect 402
  ```
- **Proof:**
  ```
  <paste the three status lines + JSON bodies>
  ```

### R3 — Cost calculation: monthly rollup + correct AI token pricing

- **Claim:** Monthly usage rolls up into a cost figure; cached input is discounted, reasoning tokens are billed as output; totals match the pinned constants exactly.
- **Reproduce:**
  ```bash
  curl -s http://localhost:8000/api/v1/tenants/tenant_pro_active/usage
  ```
- **Proof:**
  ```
  <paste the rollup JSON; show micro_cents matches the hand-computed value from config/pricing.json>
  ```

### R4 — Stripe integration: checkout works end-to-end in test mode

- **Claim:** A test-mode Checkout session is created; completing it flips the tenant Free → Pro via webhook; `GET /usage` shows the new limits.
- **Reproduce:**
  ```bash
  curl -s -X POST http://localhost:8000/api/v1/billing/checkout \
    -H "Content-Type: application/json" \
    -d '{"tenant_id":"tenant_free_fresh","plan_id":"pro"}'
  # open checkout_url, pay with 4242 4242 4242 4242
  # then:
  curl -s http://localhost:8000/api/v1/tenants/tenant_free_fresh/usage
  ```
- **Proof:**
  ```
  <paste checkout response, the webhook log line, and the post-upgrade /usage showing plan=pro>
  ```

### R5 — Webhooks: verify signatures, ignore duplicates, update plan/status

- **Claim:** A forged signature returns `400` and changes nothing; a replayed real event is processed once.
- **Reproduce:**
  ```bash
  # forged
  curl -i -X POST http://localhost:8000/api/v1/billing/webhooks/stripe \
    -H "Stripe-Signature: t=1,v1=deadbeef" -d '{"id":"evt_fake"}'
  # replay a real event twice via Stripe CLI
  stripe trigger checkout.session.completed
  stripe trigger checkout.session.completed   # same event id
  ```
- **Proof:**
  ```
  <paste the 400 response, then two webhook log lines showing processed once + ignored duplicate>
  ```

### R6 — Data model, tests & documentation

- **Claim:** DB includes tenants, plans, subscriptions, usage events; data isolated per tenant; README + diagram + setup present; required files present.
- **Reproduce:**
  ```bash
  ls README.md capstone.yaml EVIDENCE.md BUILDLOG.md .env.example
  python -m pytest -q
  ```
- **Proof:**
  ```
  <paste file listing + test summary>
  ```

---

## Acceptance Probes (Section 12)

### PROBE 1 — Idempotency: same request twice → one event, mirrored response

- **Test:** `tests/test_idempotency.py`
- **Proof:**
  ```
  <paste pytest output for this file>
  ```

### PROBE 2 — Quota boundary: exact boundary behavior + 429/402

- **Test:** `tests/test_quotas.py`
- **Proof:**
  ```
  <paste pytest output for this file>
  ```

### PROBE 3 — Checkout flips Free → Pro; /usage reflects new limits

- **Test:** `tests/test_stripe_webhooks.py`
- **Proof:**
  ```
  <paste pytest output for this file>
  ```

### PROBE 4 — Forged webhook → 400; replay → processed once

- **Test:** `tests/test_stripe_webhooks.py`
- **Proof:**
  ```
  <paste pytest output for this file>
  ```

### PROBE 5 — Pinned pricing rules produce exact expected totals

- **Test:** `tests/test_pricing.py`
- **Proof:**
  ```
  <paste pytest output for this file>
  ```

---

## Shared Requirements (Section 12)

### REQ #1 — Layered architecture (data / logic / HTTP separated)

- **Proof:** `<point to src/api, src/core, src/db; paste the tree>`

### REQ #2 — Validation at the boundary (bad input → clean 4xx, never 500)

- **Proof:** `<paste a curl with a malformed body returning 422 in the standard envelope>`

### REQ #3 — ≥1 background job (off request path, retries + failure alert)

- **Test:** `tests/test_worker.py`
- **Proof:** `<paste pytest output + a log line showing a retry and a failure alert>`

### REQ #4 — Real persistence (migrations, indexes, isolated tenants)

- **Proof:** `<paste alembic history + the index definitions>`

### REQ #5 — Idempotency where it matters

- **Proof:** `<cross-reference PROBE 1>`

### REQ #6 — Secrets clean (env only, never logged)

- **Proof:** `<paste .gitignore showing .env, and grep proving no key appears in logs>`

### REQ #7 — Cost tracked, if AI is used (per call, attributed, budget guard)

- **Proof:** `<paste per-event cost_micro_cents + the quota guard behavior>`
