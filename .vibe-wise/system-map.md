# System Map: Usage Metering & Billing Engine

```mermaid
graph TD
    Client[Client / Evaluator] -->|POST /api/v1/generate| Meter[API: Meter & Quotas]
    Client -->|GET /api/v1/usage| Usage[API: Rollup]
    Client -->|POST /api/v1/billing/checkout| Checkout[API: Stripe Checkout]
    Stripe[Stripe / Stripe CLI] -->|POST /webhooks/stripe| Webhook[API: Webhook Handler]

    subgraph Core Domain Logic
        Idemp[Idempotency Engine]
        Quota[Quota Boundary Guard]
        Calc[Token Pricing Calculator]
        StripeSvc[Stripe Service]
    end

    subgraph Background Processing
        Worker[Reconciliation & Alert Worker]
    end

    subgraph Persistence Layer
        DB[(PostgreSQL / SQLite Fallback)]
        T_Tenants[Tenants]
        T_Plans[Plans]
        T_Subs[Subscriptions]
        T_Events[UsageEvents]
        T_Keys[IdempotencyKeys]
        T_Webhooks[WebhookEvents]
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
    Calc --> DB
    StripeSvc --> T_Subs
    StripeSvc --> T_Webhooks
```

## Active Modules & Responsibilities
- `config/settings.py`: Pydantic settings loading `.env` with validation.
- `config/pricing.json`: Pinned pricing constants (token rates in micro-cents).
- `src/db/`: SQLAlchemy async engine and multi-tenant isolated models.
- `src/core/calculator.py`: Lossless integer token pricing & `ROUND_HALF_UP_AT_INVOICE`.
- `src/core/idempotency.py`: Atomic SHA-256 payload deduplication & cached response replay.
- `src/core/quota.py`: Pre-execution boundary checks with row-level locking (429 vs 402).
- `src/core/stripe_service.py`: Stripe Checkout session creation & webhook verification/replay defense.
- `src/workers/reconciliation.py`: Background sync for missed webhooks & threshold alerts ($\ge 80\%$, $\ge 100\%$).
- `scripts/seed.py`: Deterministic test fixtures for evaluator acceptance probes.
