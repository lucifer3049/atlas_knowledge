"""baseline(P0;尚無資料表,僅建立 alembic 版本鏈起點)

Revision ID: 0001_baseline
Revises:
Create Date: 2026-07-11

"""
from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
