import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    String,
    Integer,
    BigInteger,
    DateTime,
    ForeignKey,
    Text,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

def generate_uuid() -> str:
    return str(uuid.uuid4())

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(String(64), primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    subscription = relationship("Subscription", back_populates="tenant", uselist=False, cascade="all, delete-orphan")
    usage_events = relationship("UsageEvent", back_populates="tenant", cascade="all, delete-orphan")
    idempotency_keys = relationship("IdempotencyKey", back_populates="tenant", cascade="all, delete-orphan")


class Plan(Base):
    __tablename__ = "plans"

    id = Column(String(32), primary_key=True)  # 'free' or 'pro'
    name = Column(String(64), nullable=False)
    monthly_api_limit = Column(Integer, nullable=False)
    monthly_token_limit = Column(Integer, nullable=False)
    base_price_cents = Column(Integer, default=0, nullable=False)
    stripe_price_id = Column(String(255), nullable=True)

    subscriptions = relationship("Subscription", back_populates="plan")


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    tenant_id = Column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), unique=True, nullable=False)
    plan_id = Column(String(32), ForeignKey("plans.id"), nullable=False)
    stripe_customer_id = Column(String(255), nullable=True, index=True)
    stripe_subscription_id = Column(String(255), nullable=True, index=True)
    status = Column(String(32), default="active", nullable=False)  # active, past_due, canceled, incomplete
    current_period_start = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    current_period_end = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    stripe_last_event_created_at = Column(BigInteger, default=0, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    tenant = relationship("Tenant", back_populates="subscription")
    plan = relationship("Plan", back_populates="subscriptions")


class UsageEvent(Base):
    __tablename__ = "usage_events"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    tenant_id = Column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type = Column(String(32), default="generation", nullable=False)
    api_calls = Column(Integer, default=1, nullable=False)
    input_tokens = Column(Integer, default=0, nullable=False)
    cached_input_tokens = Column(Integer, default=0, nullable=False)
    output_tokens = Column(Integer, default=0, nullable=False)
    reasoning_tokens = Column(Integer, default=0, nullable=False)
    cost_micro_cents = Column(BigInteger, default=0, nullable=False)  # 1 USD = 100,000,000 micro-cents
    idempotency_key = Column(String(255), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)

    tenant = relationship("Tenant", back_populates="usage_events")

    __table_args__ = (
        Index("ix_usage_events_tenant_created", "tenant_id", "created_at"),
    )


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    tenant_id = Column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    idempotency_key = Column(String(255), nullable=False)
    request_path = Column(String(255), nullable=False)
    request_hash = Column(String(64), nullable=False)  # SHA-256
    response_status_code = Column(Integer, nullable=False)
    response_body = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)

    tenant = relationship("Tenant", back_populates="idempotency_keys")

    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_tenant_idempotency_key"),
    )


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    stripe_event_id = Column(String(255), primary_key=True)
    event_type = Column(String(128), nullable=False)
    status = Column(String(32), default="processed", nullable=False)  # processed, ignored, failed
    processed_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
