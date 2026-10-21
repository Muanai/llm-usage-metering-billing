# Learning Log & Architectural Decisions

## Overview
This log documents system design decisions, edge-case resolutions, and architectural trade-offs solved during the construction of the Usage Metering & Billing Engine.

---

### [2026-10-20] System Architecture & Foundation
- **Decision:** Unified Single Service in Python + FastAPI with SQLAlchemy 2.0 Async and dual-engine persistence (PostgreSQL with SQLite auto-fallback).
- **Trade-off:** Chose a clean layered monolith over polyglot microservices to eliminate inter-process latency and network failure modes while strictly complying with the single `run:` command requirement in `capstone.yaml`.
- **Key Pattern:** Layered Architecture (HTTP Controllers $\rightarrow$ Domain Services $\rightarrow$ Database Repositories).

### [2026-10-20] Edge Cases & Billing Integrity (Q1 - Q15)
- **Decision:**
  1. Internal money math stored strictly in integer micro-cents ($1\text{ USD} = 100{,}000{,}000\text{ micro-cents}$) with `ROUND_HALF_UP_AT_INVOICE`.
  2. Exactly one event row recorded per billable call (`event_type: "generation"`, `api_calls: 1` + token metrics) satisfying PROBE 1.
  3. Row-level locking (`with_for_update()`) on tenant records to prevent boundary overshoots at 1,000 calls.
  4. Strict status precedence: financial default (`past_due`) returns `402 Payment Required` before volume quota checks (`429 Too Many Requests`).
  5. Webhook upsert with event timestamp guards to handle out-of-order Stripe deliveries safely.
- **Trade-off:** Pre-execution locking introduces slight serialization per tenant, but guarantees 100% ACID correctness and zero over-quota leaks.
- **Key Pattern:** Pessimistic Row Locking, Idempotency Token Replay, Event Upserting.

### [2026-10-21] Task 1: Environment Configuration & Tooling Migration
- **Decision:** Migrated package and environment management to `uv` (`pyproject.toml` + `uv.lock`) while compiling a synchronous `requirements.txt` for legacy environments.
- **Trade-off:** Modern `uv` tooling provides lightning-fast deterministic builds (<2s) and eliminates global pip environment pollution.
- **Key Pattern:** Resilient Settings Defaulting (Pydantic-Settings with auto-fallback to SQLite).
