"""Persistent Telegram conversation, update cursor and reply outbox."""

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bot_states",
        sa.Column("owner_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("last_update_id", sa.BigInteger(), nullable=False),
        sa.Column("step", sa.String(20), nullable=False),
        sa.Column("draft", sa.JSON(), nullable=False),
    )
    op.create_table(
        "bot_replies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.BigInteger(), sa.ForeignKey("bot_states.owner_id"), nullable=False),
        sa.Column("update_id", sa.BigInteger(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("reply_markup", sa.JSON(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("owner_id", "update_id", name="uq_bot_reply_update"),
    )


def downgrade():
    op.drop_table("bot_replies")
    op.drop_table("bot_states")
