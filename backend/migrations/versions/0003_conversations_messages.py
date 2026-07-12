"""T1.2 conversations + messages(權威 = PHASE_1 §4.1 DDL)

Revision ID: 0003_conversations_messages
Revises: 0002_users_auth
Create Date: 2026-07-12

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "0003_conversations_messages"
down_revision: str | None = "0002_users_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("channel", sa.String(16), nullable=False, server_default=sa.text("'web'")),
        sa.Column("model_alias", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # desc 索引 autogenerate 產不出,直接下 §4.1 原文 DDL(以 DDL 為準)。
    op.execute(
        "create index ix_conversations_user_updated "
        "on conversations (user_id, updated_at desc, id desc)"
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "content_meta",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("client_message_id", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.String(32), nullable=True),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "role in ('system','user','assistant','tool')", name="ck_messages_role"
        ),
    )
    op.create_index(
        "ix_messages_conv_created", "messages", ["conversation_id", "created_at", "id"]
    )
    op.create_index(
        "ux_messages_client_id",
        "messages",
        ["conversation_id", "client_message_id"],
        unique=True,
        postgresql_where=sa.text("client_message_id is not null"),
    )


def downgrade() -> None:
    op.drop_index("ux_messages_client_id", table_name="messages")
    op.drop_index("ix_messages_conv_created", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_conversations_user_updated", table_name="conversations")
    op.drop_table("conversations")
