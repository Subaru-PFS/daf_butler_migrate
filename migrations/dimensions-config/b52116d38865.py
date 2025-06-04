"""Migration script for pfs 3.

Revision ID: b52116d38865
Revises: 0cef7bfd6333
Create Date: 2025-05-13 09:38:25.869091

"""
import logging

import sqlalchemy as sa
import alembic

from lsst.daf.butler_migrate.butler_attributes import ButlerAttributes

# revision identifiers, used by Alembic.
revision = "b52116d38865"
down_revision = "0cef7bfd6333"
branch_labels = None
depends_on = None

# Logger name should start with lsst to work with butler logging option.
_LOG = logging.getLogger(f"lsst.{__name__}")


def upgrade() -> None:
    """Upgrade from PFS dimensions version 2 to version 3.

    - Rename dataset types:
       - pfsCoadd
       - pfsCoaddLsf
       - coaddSpectra_metadata
       - coaddSpectra_log
    - Change the 'combination' table so that pfs_visit_hash is no longer a key.
    - Add obj_group dimension.
    """
    _LOG.info("Upgrading to PFS version 3")
    conn = alembic.op.get_bind()

    # Rename dataset types.
    for fromName in ("pfsCoadd", "pfsCoaddLsf", "coaddSpectra_metadata", "coaddSpectra_log"):
        toName = fromName + "1"
        _LOG.info("Renaming dataset type %s to %s", fromName, toName)
        conn.execute(
            sa.text("UPDATE dataset_type SET name = :toName WHERE name = :fromName"),
            dict(fromName=fromName, toName=toName),
        )

    # Delete 'combination' table's unique constraint on 'pfs_visit_hash'
    _LOG.info("Removing unique constraint on combination.pfs_visit_hash")
    alembic.op.drop_constraint("combination_unq_instrument_pfs_visit_hash", "combination")

    # Add obj_group dimension
    _LOG.info("Adding obj_group dimension")
    alembic.op.create_table(
        "obj_group",
        sa.Column("instrument", sa.String(16), nullable=False),
        sa.Column("cat_id", sa.BigInteger(), nullable=False),
        sa.Column("combination", sa.String(64), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
    )
    alembic.op.create_primary_key(
        "obj_group_pkey", "obj_group", ["instrument", "cat_id", "combination", "id"]
    )
    alembic.op.create_index(
         "obj_group_fkidx_instrument", "obj_group", ["instrument"]
    )
    alembic.op.create_index(
         "obj_group_fkidx_instrument_cat_id", "obj_group", ["instrument", "cat_id"]
    )
    alembic.op.create_index(
         "obj_group_fkidx_instrument_combination", "obj_group", ["instrument", "combination"]
    )
    alembic.op.create_foreign_key(
        "fkey_obj_group_instrument_name_instrument", "obj_group", "instrument", ["instrument"], ["name"]
    )
    alembic.op.create_foreign_key(
        "fkey_obj_group_cat_id_instrument_id_instrument_cat_id",
        "obj_group",
        "cat_id",
        ["instrument", "cat_id"],
        ["instrument", "id"],
    )
    alembic.op.create_foreign_key(
        "fkey_obj_group_combination_instrument_name_instrument__73612790",
        "obj_group",
        "combination",
        ["instrument", "combination"],
        ["instrument", "name"],
    )

    # Finally, we need to update the version of dimensions.yaml that's stored
    # in the database.
    _LOG.info("Converting butler attributes")
    schema = alembic.context.get_context().version_table_schema
    attributes = ButlerAttributes(conn, schema)

    def update_config(config):
        config["version"] = 3

        dims = config["elements"]

        # Downgrade combination.pfs_visit_hash from key to metadata
        combination = dims["combination"]
        keys = combination["keys"]
        hashKey = keys[-1]
        assert hashKey["name"] == "pfs_visit_hash"
        combination["keys"] = [keys[0]]
        combination["metadata"] = [hashKey]

        # Add obj_group dimension
        dims["obj_group"] = dict(
            doc=(
                "Groups of objects within the same catalog.\n"
                "This serves to sub-divide a cat_id for a particular combination of visits."
            ),
            keys=[dict(name="id", type="int")],
            requires=["combination", "cat_id"],
            storage=dict(cls="lsst.daf.butler.registry.dimensions.table.TableDimensionRecordStorage"),
        )

        return config

    attributes.update_dimensions_json(update_config)


def downgrade() -> None:
    """Perform schema downgrade."""
    raise NotImplementedError()
