# Design Document: Usage Metering & Billing Engine

> Full detailed specification available at: [docs/superpowers/specs/2026-10-20-usage-metering-billing-design.md](superpowers/specs/2026-10-20-usage-metering-billing-design.md)

## 1. Problem Statement & Mission
Every SaaS product must answer:
1. **How much has this customer used?**
2. **How much should they pay?**
3. **Have they reached their plan limits?**

This capstone builds a backend service addressing all three: idempotent usage metering, subscription quota enforcement (429/402 boundary responses), exact integer micro-cents AI token pricing math (cached input discount & reasoning tokens counted as output), Stripe test-mode subscription management via signature-verified, deduplicated webhooks, and a background reconciliation/quota alert worker.

## 2. Explicit Non-Goals
- No real payments or live Stripe mode (strictly Stripe Test Mode with test cards).
- No external paid LLM calls (token counts metered via simulation payloads).
- No floating-point math for money (strict integer micro-cents: 1 USD = 100,000,000 micro-cents).
- No complex proration or overage invoicing in core (focused on clean hard quotas & Free/Pro tiers).

## 3. Data Model
- **Tenants**: Multi-tenant customer organizations (`id`, `name`, `email`, `created_at`).
- **Plans**: `free` (1k API calls/mo, 100k tokens/mo, $0) and `pro` (50k API calls/mo, 5M tokens/mo, $29/mo).
- **Subscriptions**: Tenant subscription state (`tenant_id`, `plan_id`, `stripe_customer_id`, `stripe_subscription_id`, `status`).
- **UsageEvents**: Individual metered billable events (`tenant_id`, `event_type`, `quantity`, `input_tokens`, `cached_input_tokens`, `output_tokens`, `reasoning_tokens`, `cost_micro_cents`, `idempotency_key`, `timestamp`).
- **IdempotencyKeys**: `(tenant_id, idempotency_key)` unique records with `request_hash`, `response_status_code`, and `response_body` snapshot.
- **WebhookEvents**: Deduplication table tracking processed `stripe_event_id`.

## 4. API Surface
- `POST /api/v1/generate`: Dummy billable endpoint requiring `X-Tenant-ID` and `Idempotency-Key`.
- `GET /api/v1/tenants/{tenant_id}/usage`: Rollup endpoint calculating monthly usage, limit percentage, and exact token costs.
- `POST /api/v1/billing/checkout`: Initiates Stripe test mode subscription checkout session.
- `POST /api/v1/billing/webhooks/stripe`: Receives signed Stripe webhooks (`checkout.session.completed`, `customer.subscription.updated`, `customer.subscription.deleted`), verifies signatures, deduplicates replays, and syncs plans.
- `GET /health`: Healthcheck endpoint for container orchestration.

## 5. Layer Sketch
1. **HTTP Layer** (`src/api/`): FastAPI routes, dependency injection, boundary validation, and global error handlers (clean 4xx JSON, no 500 leaks).
2. **Core Domain** (`src/core/`): Idempotency deduplication, quota boundary checks (429 vs 402), token pricing calculator, and Stripe service.
3. **Persistence Layer** (`src/db/`): SQLAlchemy 2.0 Async models, repositories, and PostgreSQL / SQLite auto-fallback.
4. **Worker Layer** (`src/workers/`): Background reconciliation of Stripe subscription state and quota alert evaluator with exponential retries.
