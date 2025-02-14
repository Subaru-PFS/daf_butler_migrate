"""Migration script for dimensions.yaml namespace=daf_butler version=3.

Revision ID: c5ae3a2cd7c2
Revises: bf6308af80aa
Create Date: 2022-11-25 12:04:18.424257

"""

import sqlalchemy as sa
from alembic import context, op
from lsst.daf.butler_migrate.butler_attributes import ButlerAttributes

# revision identifiers, used by Alembic.
revision = "c5ae3a2cd7c2"
down_revision = "bf6308af80aa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Upgrade from version 2 to version 3.

    Summary of changes:

        - Change observation_reason column size in dimensions.json for visit
          and exposure elements.
        - Change observation_reason column size for visit and exposure tables.
        - For sqlite backend update check constraint for new column size.
    """
    _migrate(2, 3, 68, 32)


def downgrade() -> None:
    """Undo migration."""
    _migrate(3, 2, 32, 68)


def _migrate(old_version: int, new_version: int, column_size: int, old_column_size: int) -> None:
    mig_context = context.get_context()

    # When we use schemas in postgres then all tables belong to the same schema
    # so we can use alembic's version_table_schema to see where everything goes
    schema = mig_context.version_table_schema

    def _update_config(config: dict) -> dict:
        """Update dimension.json configuration"""

        assert config["version"] == old_version, f"dimensions.json version mismatch: {config['version']}"

        config["version"] = new_version

        column = "observation_reason"
        for element in ("visit", "exposure"):
            element_dict = config["elements"][element]
            column_def = [metadata for metadata in element_dict["metadata"] if metadata["name"] == column]
            assert len(column_def) == 1, f"Cannot find {column} metadata definition for {element}"
            column_def[0]["length"] = column_size

        return config

    # Update attributes
    assert mig_context.bind is not None
    attributes = ButlerAttributes(mig_context.bind, schema)
    attributes.update_dimensions_json(_update_config)

    # Update actual schema
    for table_name in ("visit", "exposure"):
        with op.batch_alter_table(table_name, schema=schema) as batch_op:
            # change column type
            column = "observation_reason"
            column_type: sa.types.TypeEngine
            if column_size > 32:
                # daf_butler uses Text for all string columns longer than 32
                # characters.
                column_type = sa.Text()
            else:
                column_type = sa.String(column_size)
            batch_op.alter_column(column, type_=column_type)  # type: ignore[attr-defined]

            assert mig_context.bind is not None, "Requires an existing bind"
            if mig_context.bind.dialect.name == "sqlite":
                # For sqlite we also define check constraint
                constraint_name = f"{table_name}_len_{column}"
                constraint = f'length("{column}")<={column_size} AND length("{column}")>=1'
                if old_column_size <= 32:
                    # Constraint only exists for shorter strings.
                    batch_op.drop_constraint(constraint_name)  # type: ignore[attr-defined]
                if column_size <= 32:
                    batch_op.create_check_constraint(constraint_name, sa.text(constraint))  # type: ignore
