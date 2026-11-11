"""Obligation editing, archival deletion and one-time browser login."""

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = depends_on = None


def upgrade():
    op.add_column("bot_replies", sa.Column("sensitive", sa.Boolean(), server_default="false", nullable=False))
    op.add_column("recurring_payments", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "recurring_payments", sa.Column("schedule_revision", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column(
        "occurrences", sa.Column("schedule_revision", sa.Integer(), server_default="0", nullable=False)
    )
    op.drop_constraint("uq_occurrence_date", "occurrences", type_="unique")
    op.create_unique_constraint(
        "uq_occurrence_date", "occurrences", ["recurring_id", "scheduled_date", "schedule_revision"]
    )
    op.create_table(
        "login_codes",
        sa.Column("owner_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "browser_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_browser_sessions_owner_id", "browser_sessions", ["owner_id"])
    op.create_table(
        "login_guards",
        sa.Column("owner_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    # Different schedule revisions can have the same date; don't silently discard history.
    conn = op.get_bind()
    duplicates = conn.scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM occurrences GROUP BY recurring_id, scheduled_date HAVING count(*)>1)"
        )
    )
    if duplicates:
        raise RuntimeError(
            "Schedule history contains duplicate dates; restore a backup instead of destructive downgrade"
        )
    op.drop_table("login_guards")
    op.drop_index("ix_browser_sessions_owner_id", table_name="browser_sessions")
    op.drop_table("browser_sessions")
    op.drop_table("login_codes")
    op.drop_constraint("uq_occurrence_date", "occurrences", type_="unique")
    op.create_unique_constraint("uq_occurrence_date", "occurrences", ["recurring_id", "scheduled_date"])
    op.drop_column("occurrences", "schedule_revision")
    op.drop_column("recurring_payments", "schedule_revision")
    op.drop_column("recurring_payments", "deleted_at")
    op.drop_column("bot_replies", "sensitive")
