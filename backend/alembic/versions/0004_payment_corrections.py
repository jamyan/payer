"""Preserve original payments and append corrections; backfill Irkutsk payment dates."""

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("payments", sa.Column("paid_on", sa.Date(), nullable=True))
    op.execute("UPDATE payments SET paid_on = (created_at AT TIME ZONE 'Asia/Irkutsk')::date")
    op.alter_column("payments", "paid_on", nullable=False)
    op.create_table(
        "payment_corrections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("payment_id", sa.Uuid(), sa.ForeignKey("payments.id"), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("paid_on", sa.Date(), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("payment_id", "revision", name="uq_correction_revision"),
        sa.UniqueConstraint("owner_id", "idempotency_key", name="uq_correction_key"),
        sa.CheckConstraint("amount >= 0", name="ck_correction_nonnegative"),
        sa.CheckConstraint("revision > 0", name="ck_correction_revision"),
    )


def downgrade():
    raise RuntimeError(
        "Corrections are financial history: restore the pre-upgrade backup instead of dropping them"
    )
