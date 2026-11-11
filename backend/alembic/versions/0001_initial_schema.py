"""initial_schema

Revision ID: 0001
Revises:
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_audit_log_owner_id"), "audit_log", ["owner_id"], unique=False)
    op.create_table(
        "recurring_payments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("anchor_date", sa.Date(), nullable=False),
        sa.Column("frequency", sa.String(length=10), nullable=False),
        sa.Column("interval", sa.Integer(), nullable=False),
        sa.Column("variable", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("frequency IN ('days','weeks','months','years')", name="ck_frequency"),
        sa.CheckConstraint("interval >= 1 AND interval <= 3660", name="ck_interval"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_recurring_owner", "recurring_payments", ["owner_id"], unique=False)
    op.create_table(
        "occurrences",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("recurring_id", sa.Uuid(), nullable=False),
        sa.Column("scheduled_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("expected_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("manually_adjusted", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('pending','partial','paid','skipped','cancelled')", name="ck_status"),
        sa.CheckConstraint("expected_amount > 0", name="ck_expected_positive"),
        sa.ForeignKeyConstraint(
            ["recurring_id"],
            ["recurring_payments.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("recurring_id", "scheduled_date", name="uq_occurrence_date"),
    )
    op.create_index("ix_occurrences_due", "occurrences", ["due_date"], unique=False)
    op.create_table(
        "tariffs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("recurring_id", sa.Uuid(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.CheckConstraint("amount > 0", name="ck_tariff_positive"),
        sa.ForeignKeyConstraint(
            ["recurring_id"],
            ["recurring_payments.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("recurring_id", "effective_from", name="uq_tariff_date"),
    )
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("occurrence_id", sa.Uuid(), nullable=False),
        sa.Column("due_snapshot", sa.Date(), nullable=False),
        sa.Column("days_before", sa.Integer(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','sent','cancelled','expired')", name="ck_notification_status"
        ),
        sa.CheckConstraint("days_before IN (3,1,0)", name="ck_notification_offset"),
        sa.ForeignKeyConstraint(
            ["occurrence_id"],
            ["occurrences.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("occurrence_id", "due_snapshot", "days_before", name="uq_notification"),
    )
    op.create_index("ix_notification_schedule", "notifications", ["status", "scheduled_at"], unique=False)
    op.create_table(
        "payments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("occurrence_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount > 0", name="ck_payment_positive"),
        sa.ForeignKeyConstraint(
            ["occurrence_id"],
            ["occurrences.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "idempotency_key", name="uq_payment_key"),
    )
    op.create_index("ix_payments_occurrence", "payments", ["occurrence_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_payments_occurrence", table_name="payments")
    op.drop_table("payments")
    op.drop_index("ix_notification_schedule", table_name="notifications")
    op.drop_table("notifications")
    op.drop_table("tariffs")
    op.drop_index("ix_occurrences_due", table_name="occurrences")
    op.drop_table("occurrences")
    op.drop_index("ix_recurring_owner", table_name="recurring_payments")
    op.drop_table("recurring_payments")
    op.drop_index(op.f("ix_audit_log_owner_id"), table_name="audit_log")
    op.drop_table("audit_log")
