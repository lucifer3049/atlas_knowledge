"""T1.4 model_usage_logs(權威 = PHASE_1 §4.1 DDL)

Revision ID: 0004_model_usage_logs
Revises: 0003_conversations_messages
Create Date: 2026-07-12

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_model_usage_logs"
down_revision: str | None = "0003_conversations_messages"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_usage_logs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        # 軟引用:user_id 有 FK(set null);conversation_id / message_id 無 FK(§D3)。
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("message_id", sa.Uuid(), nullable=True),
        sa.Column("channel", sa.String(16), nullable=False, server_default=sa.text("'web'")),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("status in ('ok','error')", name="ck_usage_status"),
    )
    op.create_index("ix_usage_created", "model_usage_logs", ["created_at"])
    op.create_index("ix_usage_user_created", "model_usage_logs", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_usage_user_created", table_name="model_usage_logs")
    op.drop_index("ix_usage_created", table_name="model_usage_logs")
    op.drop_table("model_usage_logs")
