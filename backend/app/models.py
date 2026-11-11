import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Recurring(Base):
    __tablename__ = "recurring_payments"
    __table_args__ = (
        CheckConstraint("frequency IN ('days','weeks','months','years')", name="ck_frequency"),
        CheckConstraint("interval >= 1 AND interval <= 3660", name="ck_interval"),
        Index("ix_recurring_owner", "owner_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[int] = mapped_column(BigInteger)
    title: Mapped[str] = mapped_column(String(200))
    anchor_date: Mapped[date] = mapped_column(Date)
    frequency: Mapped[str] = mapped_column(String(10))
    interval: Mapped[int] = mapped_column(Integer, default=1)
    variable: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    schedule_revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Tariff(Base):
    __tablename__ = "tariffs"
    __table_args__ = (
        UniqueConstraint("recurring_id", "effective_from", name="uq_tariff_date"),
        CheckConstraint("amount > 0", name="ck_tariff_positive"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    recurring_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recurring_payments.id"))
    effective_from: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))


class Occurrence(Base):
    __tablename__ = "occurrences"
    __table_args__ = (
        UniqueConstraint("recurring_id", "scheduled_date", "schedule_revision", name="uq_occurrence_date"),
        CheckConstraint("expected_amount > 0", name="ck_expected_positive"),
        CheckConstraint("status IN ('pending','partial','paid','skipped','cancelled')", name="ck_status"),
        Index("ix_occurrences_due", "due_date"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    recurring_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recurring_payments.id"))
    scheduled_date: Mapped[date] = mapped_column(Date)
    schedule_revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    due_date: Mapped[date] = mapped_column(Date)
    expected_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    manually_adjusted: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(12), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("owner_id", "idempotency_key", name="uq_payment_key"),
        CheckConstraint("amount > 0", name="ck_payment_positive"),
        Index("ix_payments_occurrence", "occurrence_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[int] = mapped_column(BigInteger)
    occurrence_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("occurrences.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    paid_on: Mapped[date] = mapped_column(Date, default=lambda: datetime.now(ZoneInfo("Asia/Irkutsk")).date())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PaymentCorrection(Base):
    __tablename__ = "payment_corrections"
    __table_args__ = (
        UniqueConstraint("payment_id", "revision", name="uq_correction_revision"),
        UniqueConstraint("owner_id", "idempotency_key", name="uq_correction_key"),
        CheckConstraint("amount >= 0", name="ck_correction_nonnegative"),
        CheckConstraint("revision > 0", name="ck_correction_revision"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    payment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payments.id"))
    owner_id: Mapped[int] = mapped_column(BigInteger)
    revision: Mapped[int] = mapped_column(Integer)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    paid_on: Mapped[date] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(String(500))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Audit(Base):
    __tablename__ = "audit_log"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[int] = mapped_column(BigInteger, index=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    action: Mapped[str] = mapped_column(String(50))
    details: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("occurrence_id", "due_snapshot", "days_before", name="uq_notification"),
        CheckConstraint("days_before IN (3,1,0)", name="ck_notification_offset"),
        CheckConstraint("status IN ('pending','sent','cancelled','expired')", name="ck_notification_status"),
        Index("ix_notification_schedule", "status", "scheduled_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    occurrence_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("occurrences.id"))
    due_snapshot: Mapped[date] = mapped_column(Date)
    days_before: Mapped[int] = mapped_column(Integer)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(12), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class BotState(Base):
    __tablename__ = "bot_states"
    owner_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    last_update_id: Mapped[int] = mapped_column(BigInteger, default=-1)
    step: Mapped[str] = mapped_column(String(20), default="idle")
    draft: Mapped[dict] = mapped_column(JSON, default=dict)


class BotReply(Base):
    __tablename__ = "bot_replies"
    __table_args__ = (UniqueConstraint("owner_id", "update_id", name="uq_bot_reply_update"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[int] = mapped_column(ForeignKey("bot_states.owner_id"))
    update_id: Mapped[int] = mapped_column(BigInteger)
    text: Mapped[str] = mapped_column(Text)
    reply_markup: Mapped[dict] = mapped_column(JSON)
    sensitive: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LoginCode(Base):
    __tablename__ = "login_codes"
    owner_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BrowserSession(Base):
    __tablename__ = "browser_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LoginGuard(Base):
    __tablename__ = "login_guards"
    owner_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
