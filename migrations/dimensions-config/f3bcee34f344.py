"""Migration script for dimensions.yaml namespace=daf_butler version=0.

Revision ID: f3bcee34f344
Revises: 3e2891b82110
Create Date: 2022-05-13 14:38:32.175603

"""

# revision identifiers, used by Alembic.
revision = "f3bcee34f344"
down_revision = "3e2891b82110"
branch_labels = ("dimensions-config-daf_butler",)
depends_on = None


def upgrade() -> None:
    raise NotImplementedError("Version 0 can only be created by daf_butler")


def downgrade() -> None:
    raise NotImplementedError()
