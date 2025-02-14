"""Migration script for dimensions.yaml namespace=daf_butler version=7.

Revision ID: 352c30854bb0
Revises: 1fae088c80b6
Create Date: 2024-03-28 12:14:53.021101

"""

import logging

import sqlalchemy
from alembic import op
from lsst.daf.butler_migrate.migration_context import MigrationContext

# revision identifiers, used by Alembic.
revision = "352c30854bb0"
down_revision = "1fae088c80b6"
branch_labels = None
depends_on = None

# Logger name should start with lsst to work with butler logging option.
_LOG = logging.getLogger(f"lsst.{__name__}")

_NEW_COLUMN = "can_see_sky"
_TABLE = "exposure"
_OBSERVATION_TYPE_COLUMN = "observation_type"


def upgrade() -> None:
    """Upgrade from daf_butler universe version 6 to version 7 following update
    of dimensions.yaml in DM-43101.

    Adds ``can_see_sky`` column to the exposure table, and sets its initial
    values based on the the ``observation_type`` column.
    """

    ctx = MigrationContext()

    _LOG.info("Checking that this is an unmodified daf_butler universe 6 repo")
    ctx.attributes.validate_dimensions_json(6)

    _LOG.info("Adding can_see_sky column to exposure table")
    op.add_column(
        _TABLE, sqlalchemy.Column(_NEW_COLUMN, sqlalchemy.Boolean, nullable=True), schema=ctx.schema
    )

    # Set values for existing data based on the exposure's observation_type,
    # which is closely correlated with whether the sky is visible in the
    # exposure.
    #
    # Any exposures with observation types not present not in the two calls to
    # _populate_values below will be left as NULL.
    _LOG.info("Populating can_see_sky column")
    table = ctx.get_table(_TABLE)
    _populate_values(
        table,
        True,
        [
            "science",
            "object",
            "standard",
            "sky flat",
            "standard_star",
            "skyflat",
            "focus",
            "focusing",
            "exp",
            "skyexp",
        ],
    )
    _populate_values(table, False, ["dark", "bias", "agexp", "domeflat", "dome flat", "zero", "spot"])

    unhandled_observation_types = _find_unhandled_observation_types(ctx)
    if unhandled_observation_types:
        _LOG.info(
            "WARNING: No default value for can_see_sky is known for the following observation types:\n"
            f"{unhandled_observation_types}\n"
            "Exposure records with these observation types will have a NULL can_see_sky."
        )
    else:
        _LOG.info("...can_see_sky values were set for all exposure records.")

    _LOG.info("Updating dimensions.json in ButlerAttributes")
    ctx.attributes.replace_dimensions_json(7)


def downgrade() -> None:
    """Perform schema downgrade."""
    ctx = MigrationContext()

    _LOG.info("Checking that this is an unmodified daf_butler universe 7 repo")
    ctx.attributes.validate_dimensions_json(7)

    _LOG.info("dropping can_see_sky column")
    op.drop_column(_TABLE, _NEW_COLUMN, schema=ctx.schema)

    _LOG.info("Updating dimensions.json in ButlerAttributes")
    ctx.attributes.replace_dimensions_json(6)


def _populate_values(table: sqlalchemy.Table, can_see_sky: bool, observation_types: list[str]) -> None:
    """Set can_see_sky column to the specified value for all rows in the
    exposure table that have an observation_type in the specified list.
    """
    op.execute(
        table.update()
        .values({_NEW_COLUMN: can_see_sky})
        .where(table.columns[_OBSERVATION_TYPE_COLUMN].in_(observation_types))
        .where(table.columns[_NEW_COLUMN].is_(None))
    )


def _find_unhandled_observation_types(ctx: MigrationContext) -> list[str]:
    """Return a list of observation types present in the exposure table that
    have a NULL value for the ``can_see_sky`` column at least one of their
    rows.
    """
    table = ctx.get_table(_TABLE)
    return list(
        ctx.bind.execute(
            sqlalchemy.select(table.columns[_OBSERVATION_TYPE_COLUMN])
            .distinct()
            .where(table.columns[_NEW_COLUMN].is_(None))
        )
        .scalars()
        .all()
    )
