# Evidence Document: Usage Metering & Billing Engine

This document provides machine-verifiable and reproducible proof for every requirement checkbox from Section 6 and each of the 5 acceptance probes of the FlyRank Backend Track Capstone Brief.

---

## 1. Metering & Idempotency (PROBE 1)

> **Requirement**: A billable action creates exactly one usage event, even under retries — deduplicated by idempotency key. Double-counting cannot happen.

### Automated Test Proof
<!-- Paste `pytest tests/test_idempotency.py -v` transcript here -->
```text
[Pending Implementation Execution]
```

### Transcript / Curl Proof (Same Request Sent Twice)
<!-- Paste curl transcript showing 1st request (200 OK) and 2nd retried request (200 OK identical cached body, 0 extra usage rows created) -->
```bash
# Request 1:
# curl -i -X POST http://localhost:8000/api/v1/generate ...

# Request 2 (Duplicate Idempotency-Key):
# curl -i -X POST http://localhost:8000/api/v1/generate ...

# DB Count Check:
# SELECT count(*) FROM usage_events WHERE idempotency_key = '...';
# Expected: 1
```

---

## 2. Quotas & Boundary Honesty (PROBE 2)

> **Requirement**: Usage is checked against the tenant's plan; requests over the limit are rejected. Responses carry 429 / 402 with clear explanation.

### Boundary Test: Call 999 -> 1,000 (Allowed) -> 1,001 (Blocked 429)
<!-- Paste test/curl output showing boundary transition -->
```text
[Pending Implementation Execution]
```

### Inactive / Unpaid Subscription Test (402 Payment Required)
<!-- Paste curl output for past_due tenant receiving 402 -->
```text
[Pending Implementation Execution]
```

---

## 3. Cost Calculation & Token Pricing Rules (PROBE 5)

> **Requirement**: Monthly usage rolls up into a cost figure per tenant. AI token pricing handles cached input tokens (discounted), reasoning tokens (billed as output), and individual category multipliers. Pricing constants pinned in config.

### Pinned Rates Configuration (`config/pricing.json`)
<!-- Verify pinned pricing rates -->
```json
{
  "rates_per_million_tokens_cents": {
    "standard_input": 200,
    "cached_input": 50,
    "standard_output": 800,
    "reasoning_output": 800
  }
}
```

### Verification Calculation Proof (`GET /api/v1/tenants/tenant_pricing_demo/usage`)
<!-- Paste curl transcript showing exact breakdown matching $29.51 -->
```text
[Pending Implementation Execution]
```

---

## 4. Stripe Integration & Webhooks (PROBE 3 & PROBE 4)

> **Requirement**: Subscription checkout in test mode; signature-verified webhooks; replay deduplication; Free -> Pro plan synchronization.

### PROBE 3: Checkout Completion Flips Free -> Pro
<!-- Paste Stripe CLI webhook forward and tenant plan update transcript -->
```text
[Pending Implementation Execution]
```

### PROBE 4: Forged Webhook Signature Rejected (400 Bad Request)
<!-- Paste curl sending invalid signature yielding 400 Bad Request -->
```text
[Pending Implementation Execution]
```

### PROBE 4: Replay Webhook Event Processed Once (Duplicate Ignored)
<!-- Paste curl/Stripe CLI sending same event twice yielding status: ignored on second call -->
```text
[Pending Implementation Execution]
```

---

## 5. Background Job: Stripe Reconciliation & Quota Alerts (Requirement #3)

> **Requirement**: ≥1 background job off the request path, with retries and failure alert logging.

### Background Worker Execution Proof
<!-- Paste worker logs showing periodic cycle, Stripe sync, and >=80% / 100% quota alert triggers -->
```text
[Pending Implementation Execution]
```

---

## 6. Shared Requirements Verification Summary

| # | Requirement | Implementation Evidence |
|---|---|---|
| 1 | **Layered architecture** | `src/api` (HTTP), `src/core` (logic), `src/db` (data), `src/workers` (jobs) |
| 2 | **Validation at boundary** | Pydantic v2 schemas; bad input $\rightarrow$ clean 4xx envelope, never 500 |
| 3 | **≥1 background job** | `src/workers/reconciliation.py` (Stripe sync + quota alerts + retries) |
| 4 | **Real persistence** | PostgreSQL + SQLite fallback, Alembic migrations, tenant isolation indexes |
| 5 | **Idempotency** | Atomic `UNIQUE(tenant_id, idempotency_key)` + row lock, zero duplicate events |
| 6 | **Secrets clean** | Environment variables via `.env`, template in `.env.example`, no hardcoded keys |
| 7 | **Cost tracked** | Integer micro-cents arithmetic, pinned token pricing rules, `ROUND_HALF_UP` |
