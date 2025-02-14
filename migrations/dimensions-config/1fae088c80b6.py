"""Migration script for dimensions.yaml namespace=daf_butler version=6.

Revision ID: 1fae088c80b6
Revises: 2a8a32e1bec3
Create Date: 2024-03-12 14:35:38.888572

"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any, TypeAlias

import sqlalchemy as sa
from alembic import op
from lsst.daf.butler import Timespan
from lsst.daf.butler_migrate.migration_context import MigrationContext
from lsst.daf.butler_migrate.naming import make_string_length_constraint
from lsst.daf.butler_migrate.timespan import create_timespan_column_definitions, format_timespan_value
from lsst.utils import doImportType

# revision identifiers, used by Alembic.
revision = "1fae088c80b6"
down_revision = "2a8a32e1bec3"
branch_labels = None
depends_on = None

# Logger name should start with lsst to work with butler logging option.
_LOG = logging.getLogger(f"lsst.{__name__}")


def upgrade() -> None:
    """Upgrade from version 5 to version 6 following update of dimensions.yaml in DM-42636.

    - Add ``group`` table, and populate it based on the ``group_name`` field in
      the ``exposure`` table.
    - Add ``day_obs`` table, and populate based on the ``day_obs`` field from
      the ``exposure`` table and timespan offsets from Butler ``Instrument``
      classes.
    - Rename ``group_name`` in the exposure table to ``group``.
    - Update the ``exposure`` table so ``group`` and ``day_obs`` are foreign
      keys to the new tables.
    - Remove ``group_id`` from ``exposure`` table.
    - Update ``config:dimensions.json`` to universe 6.
    """
    ctx = MigrationContext()
    _lock_exposure_table(ctx)
    _validate_initial_dimension_universe(ctx)
    _migrate_day_obs(ctx)
    _migrate_groups(ctx)
    _migrate_dimensions_json(ctx)


def downgrade() -> None:
    """Perform schema downgrade."""
    raise NotImplementedError()


def _lock_exposure_table(ctx: MigrationContext) -> None:
    # In this migration we generate new tables based on the content of the
    # exposure table, so make sure that it is not modified while we are
    # working.

    if ctx.is_sqlite:
        # Sqlite does not support table locks
        return

    _LOG.info("Locking exposure table")
    schema = ""
    if ctx.schema:
        schema = f"{ctx.schema}."
    ctx.bind.execute(sa.text(f"LOCK TABLE {schema}exposure IN EXCLUSIVE MODE"))


def _validate_initial_dimension_universe(ctx: MigrationContext) -> None:
    config = ctx.mig_context.config
    allow_mismatch = config is not None and "1" == config.get_section_option(
        "daf_butler_migrate_options", "allow_dimension_universe_mismatch"
    )
    if not allow_mismatch:
        _LOG.info("Checking that this is an unmodified daf_butler universe 5 repo")
        try:
            ctx.attributes.validate_dimensions_json(5)
        except ValueError as e:
            e.add_note(
                "Re-run butler migrate with the flag '--options allow_dimension_universe_mismatch=1' to"
                " bypass this check.\n"
                "This will overwrite any customizations made to the dimension universe."
            )
            raise


def _migrate_groups(ctx: MigrationContext) -> None:
    # Create group table
    _LOG.info("Creating group table")
    check_constraints = []
    if ctx.is_sqlite:
        check_constraints = [make_string_length_constraint("instrument", 32, "group_len_instrument")]
    table = op.create_table(
        "group",
        sa.Column("instrument", sa.String(32), primary_key=True),
        sa.Column("name", sa.Text, primary_key=True),
        sa.schema.ForeignKeyConstraint(
            columns=["instrument"],
            refcolumns=[ctx.get_table("instrument").c.name],
            name="fkey_group_instrument_name_instrument",
        ),
        *check_constraints,
        schema=ctx.schema,
    )

    # Populate group table based on the data in the exposure table.
    _LOG.info("Populating group table")
    exposure_table = ctx.get_table("exposure")
    select = sa.select(
        exposure_table.columns["instrument"],
        exposure_table.columns["group_name"],
    ).distinct()
    op.execute(
        table.insert().from_select(
            [
                "instrument",
                "name",
            ],
            select,
        )
    )

    # Create index on instrument
    _LOG.info("Creating instrument index for group table")
    op.create_index(
        "group_fkidx_instrument",
        "group",
        ["instrument"],
        schema=ctx.schema,
    )

    # Update the exposure table to reference the group table.
    _LOG.info("Updating exposure table to reference group table")
    with op.batch_alter_table("exposure", schema=ctx.schema) as batch_op:
        batch_op.alter_column("group_name", new_column_name="group", nullable=False)
        batch_op.drop_column("group_id")

    # In theory we should do this create_foreign_key as part of the batch
    # above.  However, there is some undocumented weirdness with the column
    # rename from "group_name" to "group".  When done in the batch above, this
    # foreign key only works if you specify the original column name instead of
    # the final one.  This seems fragile (and is likely incompatible with
    # Postgres, which ignores the batching). So do it in a separate batch.
    with op.batch_alter_table("exposure", schema=ctx.schema) as batch_op:
        batch_op.create_foreign_key(
            constraint_name="fkey_exposure_group_instrument_name_instrument_group",
            referent_table="group",
            local_cols=["instrument", "group"],
            remote_cols=["instrument", "name"],
            referent_schema=ctx.schema,
        )

    # Create index on exposure for group fkey
    op.create_index(
        "exposure_fkidx_instrument_group",
        "exposure",
        ["instrument", "group"],
        schema=ctx.schema,
    )


def _migrate_day_obs(ctx: MigrationContext) -> None:
    # Before doing anything else, generate the rows for the new day_obs table
    # from the data in the exposure table.  This is prone to failure due to the
    # need to import instrument classes.
    _LOG.info("Generating data for day_obs table from exposure_table")
    day_obs_rows = list(_generate_day_obs_rows(ctx))

    # Create day_obs table
    _LOG.info("Creating day_obs table")
    check_constraints = []
    if ctx.is_sqlite:
        check_constraints = [make_string_length_constraint("instrument", 32, "day_obs_len_instrument")]

    table = op.create_table(
        "day_obs",
        sa.Column("instrument", sa.String(32), primary_key=True),
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=False),
        *create_timespan_column_definitions("timespan", ctx.dialect),
        sa.schema.ForeignKeyConstraint(
            columns=["instrument"],
            refcolumns=[ctx.get_table("instrument").c.name],
            name="fkey_day_obs_instrument_name_instrument",
        ),
        *check_constraints,
        schema=ctx.schema,
    )

    # Populate the day_obs table based on the data in the exposure table.
    _LOG.info("Populating day_obs table")
    op.bulk_insert(table, day_obs_rows)

    # Create index on instrument
    _LOG.info("Creating instrument index for day_obs table")
    op.create_index(
        "day_obs_fkidx_instrument",
        "day_obs",
        ["instrument"],
        schema=ctx.schema,
    )

    # Update exposure table to reference day_obs table
    _LOG.info("Updating exposure table to reference day_obs table")
    with op.batch_alter_table("exposure", schema=ctx.schema) as batch_op:
        batch_op.alter_column("day_obs", nullable=False)
        batch_op.create_foreign_key(
            constraint_name="fkey_exposure_day_obs_instrument_id_instrument_day_obs",
            referent_table="day_obs",
            local_cols=["instrument", "day_obs"],
            remote_cols=["instrument", "id"],
            referent_schema=ctx.schema,
        )

    # Create index on exposure for day_obs fkey
    op.create_index(
        "exposure_fkidx_instrument_day_obs",
        "exposure",
        ["instrument", "day_obs"],
        schema=ctx.schema,
    )


def _migrate_dimensions_json(ctx: MigrationContext) -> None:
    _LOG.info("Updating dimensions.json in ButlerAttributes")
    ctx.attributes.replace_dimensions_json(6)


def _generate_day_obs_rows(ctx: MigrationContext) -> Iterator[dict]:
    exposure_table = ctx.get_table("exposure")
    select = sa.select(
        exposure_table.columns["instrument"],
        exposure_table.columns["day_obs"],
    ).distinct()
    rows = ctx.bind.execute(select).all()

    instrument_fetcher = _InstrumentFetcher(ctx)
    for row in rows:
        day_obs = row.day_obs

        # Different instruments define the start and end times for day_obs differently.
        instrument_name = row.instrument
        instrument_class = instrument_fetcher.get_instrument(instrument_name)
        offset = _get_day_obs_offset(instrument_name, instrument_class, day_obs)

        timespan = Timespan.from_day_obs(day_obs, offset)
        yield {
            "instrument": row.instrument,
            "id": day_obs,
            **format_timespan_value(timespan, "timespan", ctx.dialect),
        }


def _get_day_obs_offset(instrument_name: str, instrument: _Instrument, day_obs: int) -> int:
    day_as_astropy_time = Timespan.from_day_obs(day_obs, 0).begin
    translator = instrument.translatorClass
    if translator is None:
        raise TypeError(
            f"Instrument {instrument_name} does not have a translatorClass defined,"
            " cannot determine offset for day_obs."
        )
    offset = translator.observing_date_to_offset(day_as_astropy_time)
    # Convert astropy TimeDelta to integer seconds.
    return round(offset.to_value("s"))


_Instrument: TypeAlias = Any
"""A dynamically loaded lsst.obs_base.Instrument."""


class _InstrumentFetcher:
    def __init__(self, ctx: MigrationContext) -> None:
        self._instruments: dict[str, _Instrument] = {}
        self._ctx = ctx

    def get_instrument(self, name: str) -> _Instrument:
        """Dynamically load an lsst.obs_base.Instrument based on its class name stored in the database."""
        instrument = self._instruments.get(name)
        if instrument is not None:
            return instrument

        instrument_table = self._ctx.get_table("instrument")
        rows = self._ctx.bind.execute(
            sa.select(instrument_table.c.class_name).where(instrument_table.c.name == name)
        ).all()
        assert len(rows) == 1, f"Should be exactly one class name for instrument {name}"
        class_name = rows[0][0]
        _LOG.info(f"Loading instrument definition {name} from class {class_name}")
        instrument = doImportType(class_name)()
        self._instruments[name] = instrument
        return instrument
