"""feat: list_members table

Revision ID: 196e08d330cb
Revises: c3bcdca9f04e
Create Date: 2025-07-11 15:39:01.499736

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '196e08d330cb'
down_revision: Union[str, Sequence[str], None] = 'c3bcdca9f04e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "list_members",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("list_id", sa.Integer, sa.ForeignKey("lists.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "role",
            sa.Enum("owner", "editor", "viewer", name="listmemberrole"),
            nullable=False,
            server_default="viewer",
        ),
        sa.Column("invited_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("accepted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.UniqueConstraint("list_id", "user_id", name="uq_list_member"),
        sa.Index("ix_list_members_list_id", "list_id"),
        sa.Index("ix_list_members_user_id", "user_id"),
    )

def downgrade() -> None:
    op.drop_table("list_members")
    op.execute("DROP TYPE IF EXISTS listmemberrole")