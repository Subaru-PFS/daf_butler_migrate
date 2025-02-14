"""Migration script for dimensions.yaml namespace=daf_butler version=5.

Revision ID: 2a8a32e1bec3
Revises: 9888256c6a18
Create Date: 2024-02-20 14:49:26.435042

"""

import logging

import sqlalchemy
from alembic import context, op
from lsst.daf.butler_migrate.butler_attributes import ButlerAttributes

# revision identifiers, used by Alembic.
revision = "2a8a32e1bec3"
down_revision = "9888256c6a18"
branch_labels = None
depends_on = None

# Logger name should start with lsst to work with butler logging option.
_LOG = logging.getLogger(f"lsst.{__name__}")


def upgrade() -> None:
    """Upgrade from version 4 to version 5 following update of dimension.yaml
    in DM-42896.

    Summary of changes:

        - Length of the ``name`` column in ``instrument`` table changed from
          16 to 32 character.
        - Changes in `config:dimensions.json`:
          - "version" value updated to 5,
          - size of the "name" column for "instrument" element changed to 32.
    """
    _migrate(4, 5, 32)


def downgrade() -> None:
    """Perform schema downgrade."""
    _migrate(5, 4, 16)


def _migrate(old_version: int, new_version: int, size: int) -> None:
    """Perform schema migration.

    Parameters
    ----------
    old_version : `int`
        Current schema version.
    new_version : `int`
        Schema version to migrate to.
    size : `int`
        New size of the name column.
    """

    def _update_config(config: dict) -> dict:
        """Update dimension.json configuration"""
        assert config["version"] == old_version, f"dimensions.json version mismatch: {config['version']}"

        _LOG.info("Update dimensions.json version to %s", new_version)
        config["version"] = new_version

        elements = config["elements"]
        instrument = elements["instrument"]
        for key in instrument["keys"]:
            if key["name"] == "name":
                _LOG.info("Update dimensions.json column size to %s", size)
                key["length"] = size
                break

        return config

    if context.is_offline_mode():
        raise RuntimeError("This script does not support offline mode")

    mig_context = context.get_context()

    # When we use schemas in postgres then all tables belong to the same schema
    # so we can use alembic's version_table_schema to see where everything goes
    schema = mig_context.version_table_schema

    # New column type.
    new_type = sqlalchemy.String(size)

    # Table/column to alter.
    table_columns = _columns_to_migrate(schema)

    # Lock everything in advance to avoid potential deadlock.
    _lock_tables([item[0] for item in table_columns], schema)

    # Actual schema change.
    for table, column in table_columns:
        _LOG.info("Alter %s.%s column type to %s", table, column, new_type)
        with op.batch_alter_table(table, schema=schema) as batch_op:
            batch_op.alter_column(column, type_=new_type)
            if op.get_bind().dialect.name == "sqlite" and table == "instrument":
                # SQLite uses special check constraint.
                constraint_name = "instrument_len_name"
                batch_op.drop_constraint(constraint_name)
                constraint = f'length("{column}")<={size} AND length("{column}")>=1'
                batch_op.create_check_constraint(constraint_name, sqlalchemy.text(constraint))

    # Update attributes
    assert mig_context.bind is not None
    attributes = ButlerAttributes(mig_context.bind, schema)
    attributes.update_dimensions_json(_update_config)


def _columns_to_migrate(schema: str) -> list[tuple[str, str]]:
    """Return list of table and column names that will be migrated."""
    result: list[tuple[str, str]] = []

    pk_table = "instrument"
    pk_column: str

    inspector = sqlalchemy.inspect(op.get_bind())
    table_names = list(inspector.get_table_names(schema))

    for table in table_names:
        if table == pk_table:
            # Instrument table will be the first in the list, in case the
            # order can matter. Its PK column name is "name".
            pk = inspector.get_pk_constraint(table, schema)
            assert len(pk["constrained_columns"]) == 1, "Expect single column in PK"
            pk_column = pk["constrained_columns"][0]
            result.append((table, pk_column))
            _LOG.debug("found %s table, PK column = %s", table, pk_column)
            break
    else:
        raise ValueError(f"Cannot find {pk_table} table in the schema")

    for table in table_names:
        # Check FK of each table to find ones that reference instrument.
        fks = inspector.get_foreign_keys(table, schema)
        for fk in fks:
            if fk["referred_schema"] == schema and fk["referred_table"] == pk_table:
                if len(fk["referred_columns"]) == 1 and fk["referred_columns"][0] == pk_column:
                    fk_column = fk["constrained_columns"][0]
                    result.append((table, fk_column))
                    _LOG.debug("found dependent table: %s.%s", table, fk_column)
                    break

    return result


def _lock_tables(tables: list[str], schema: str) -> None:
    """Lock all tables that need to be migrated to avoid conflicts."""

    connection = op.get_bind()
    if connection.dialect.name == "sqlite":
        # SQLite does not support LOCK TABLE.
        return

    for table in tables:
        # We do not need quoting for schema/table names.
        if schema:
            query = f"LOCK TABLE {schema}.{table} IN EXCLUSIVE MODE"
        else:
            query = f"LOCK TABLE {table} IN EXCLUSIVE MODE"
        _LOG.info("Locking table %s", table)
        connection.execute(sqlalchemy.text(query))
