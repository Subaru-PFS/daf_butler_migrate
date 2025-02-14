"""Migration script for dimensions.yaml namespace=daf_butler version=2.

Revision ID: bf6308af80aa
Revises: 380002bcbb26
Create Date: 2022-05-16 12:11:17.906600

"""
import logging
from typing import Dict, List, Optional

import sqlalchemy as sa
from alembic import context, op
from lsst.daf.butler_migrate.butler_attributes import ButlerAttributes
from lsst.daf.butler_migrate.shrink import shrinkDatabaseEntityName

# revision identifiers, used by Alembic.
revision = "bf6308af80aa"
down_revision = "380002bcbb26"
branch_labels = None
depends_on = None

_LOG = logging.getLogger(__name__)


def upgrade() -> None:
    """Upgrade from version 1 to version 2.

    Summary of changes:

        - new table `visit_system_membership`
        - new columns in dimension tables:
            - instrument table adds visit_system column
            - exposure table adds seq_start, seq_end, has_simulated, azimuth
            - visit table adds seq_num and azimuth
        - some of these new columns need to be populated with non-NULL values
    """

    config = context.config
    has_simulated_str = config.get_section_option("daf_butler_migrate_options", "has_simulated")
    if has_simulated_str is None:
        raise ValueError(
            "This migration script requires a default value be provided for the `has_simulated`"
            " field in the `exposure` record. Please use `--options has_simulated=1` or"
            " `--options has_simulated=0` command line option."
        )
    if has_simulated_str == "0":
        has_simulated = False
    elif has_simulated_str == "1":
        has_simulated = True
    else:
        raise ValueError(f"Unexpected value of `has_simulated` option: {has_simulated_str}")

    _migrate_instrument()
    _migrate_visit()
    _migrate_exposure(has_simulated)
    _migrate_visit_definition()
    _migrate_dimensions_json()
    _migrate_schema_digest()


def downgrade() -> None:
    """Downgrade is not implemented, it may be relatively easy to do but it is
    likely we never need it.
    """
    raise NotImplementedError()


def _FKConstraint(
    src_table: str,
    src_columns: List[str],
    tgt_table: str,
    tgt_columns: List[str],
    bind: sa.engine.Connection,
    schema: Optional[str] = None,
    ondelete: Optional[str] = None,
) -> sa.schema.ForeignKeyConstraint:
    """Create foreign key constraint."""
    fk_name = "_".join(["fkey", src_table, tgt_table] + tgt_columns + src_columns)
    fk_name = shrinkDatabaseEntityName(fk_name, bind)
    tgt_columns = [f"{tgt_table}.{col}" for col in tgt_columns]
    if schema:
        tgt_columns = [f"{schema}.{col}" for col in tgt_columns]
    return sa.schema.ForeignKeyConstraint(src_columns, tgt_columns, name=fk_name, ondelete=ondelete)


def _migrate_visit_definition() -> None:
    """Make new visit_system_membership table, fill from existing data
    in visit_definition."""

    mig_context = context.get_context()
    bind = mig_context.bind
    assert bind is not None
    schema = mig_context.version_table_schema
    metadata = sa.schema.MetaData(schema=schema)

    table = "visit_system_membership"
    _LOG.info("creating %s table", table)
    constraints: list[sa.schema.ColumnCollectionConstraint] = [
        _FKConstraint(table, ["instrument"], "instrument", ["name"], bind, schema=schema),
        _FKConstraint(
            table, ["instrument", "visit_system"], "visit_system", ["instrument", "id"], bind, schema=schema
        ),
        _FKConstraint(table, ["instrument", "visit"], "visit", ["instrument", "id"], bind, schema=schema),
    ]
    if bind.dialect.name == "sqlite":
        constraints.append(
            sa.schema.CheckConstraint(
                "length(instrument)<=16 AND length(instrument)>=1",
                name=shrinkDatabaseEntityName("_".join([table, "len", "instrument"]), bind),
            )
        )
    visit_membership = op.create_table(
        table,
        sa.Column("instrument", sa.String(16), primary_key=True, autoincrement=False),
        sa.Column("visit_system", sa.BigInteger, primary_key=True, autoincrement=False),
        sa.Column("visit", sa.BigInteger, primary_key=True, autoincrement=False),
        *constraints,
        schema=schema,
    )
    assert visit_membership is not None

    op.create_index(
        shrinkDatabaseEntityName("_".join([table, "fkidx", "instrument", "visit_system"]), bind),
        table,
        ["instrument", "visit_system"],
        schema=schema,
    )
    op.create_index(
        shrinkDatabaseEntityName("_".join([table, "fkidx", "instrument", "visit"]), bind),
        table,
        ["instrument", "visit"],
        schema=schema,
    )
    op.create_index(
        shrinkDatabaseEntityName("_".join([table, "fkidx", "instrument"]), bind),
        table,
        ["instrument"],
        schema=schema,
    )

    visit_definition = sa.schema.Table("visit_definition", metadata, autoload_with=bind, schema=schema)

    # Copy everything from visit_definition to the new table.
    selection = sa.select(
        visit_definition.columns["instrument"],
        visit_definition.columns["visit_system"],
        visit_definition.columns["visit"],
    ).distinct()
    sql = visit_membership.insert().from_select(["instrument", "visit_system", "visit"], selection)
    op.execute(sql)  # type: ignore[arg-type]

    # Drop visit_system from visit_definition.
    with op.batch_alter_table("visit_definition", schema=schema) as batch_op:
        # visit_system is in PK. Postgres drops PK when the column is dropped,
        # but sqlite complains that column cannot be dropped if it's in PK.
        # The workaround is to create PK with new columns, but Postgres also
        # requires existing PK to be dropped first, and in sqlite we do not
        # even have name for PK constraint, so it cannot be dropped
        # explicitly.
        if bind.dialect.name == "postgresql":
            batch_op.drop_constraint("visit_definition_pkey")  # type: ignore[attr-defined]
        batch_op.create_primary_key(  # type: ignore[attr-defined]
            "visit_definition_pkey", ["instrument", "exposure", "visit"]
        )
        batch_op.drop_index("visit_definition_fkidx_instrument_visit_system")  # type: ignore[attr-defined]
        batch_op.drop_column("visit_system")  # type: ignore[attr-defined]


def _migrate_instrument() -> None:
    """Add and populate visit_system column in instrument table."""
    mig_context = context.get_context()
    bind = mig_context.bind
    schema = mig_context.version_table_schema
    metadata = sa.schema.MetaData(schema=schema)

    _LOG.info("Migrating instrument table")
    op.add_column(
        "instrument",
        sa.Column("visit_system", sa.BigInteger),
        schema=schema,
    )

    table = sa.schema.Table("instrument", metadata, autoload_with=bind, schema=schema)

    op.execute(table.update().values(visit_system=0))
    op.execute(
        table.update()
        .where(table.columns["name"].in_(["LSSTCam", "LATISS", "LSSTComCam"]))
        .values(visit_system=2)
    )


def _migrate_visit() -> None:
    """Drop visit_system column from visit table and add seq_num and azimuth
    columns to visit. The seq_num column is populated from the minimum seq_num
    value of the matching exposures.
    """
    mig_context = context.get_context()
    bind = mig_context.bind
    schema = mig_context.version_table_schema
    metadata = sa.schema.MetaData(schema=schema)

    _LOG.info("migrating visit table")
    with op.batch_alter_table("visit", schema=schema) as batch_op:
        batch_op.drop_index("visit_fkidx_instrument_visit_system")  # type: ignore[attr-defined]
        batch_op.drop_column("visit_system")  # type: ignore[attr-defined]
        batch_op.add_column(sa.Column("seq_num", sa.BigInteger))  # type: ignore[attr-defined]
        batch_op.add_column(sa.Column("azimuth", sa.Float))  # type: ignore[attr-defined]

    # Fill seq_num column with the lowest value of matching exposure.seq_num.
    #
    # Query for correlated update:
    #
    # UPDATE
    #     visit
    # SET
    #     seq_num = (
    #         SELECT
    #             MIN(exposure.seq_num)
    #         FROM
    #             exposure
    #             JOIN visit_definition ON visit_definition.exposure = exposure.id
    #             and visit_definition.instrument = exposure.instrument
    #         WHERE
    #             visit_definition.instrument = visit.instrument,
    #             visit_definition.visit = visit.id
    #     )

    visit = sa.schema.Table("visit", metadata, autoload_with=bind, schema=schema)
    exposure = sa.schema.Table("exposure", metadata, autoload_with=bind, schema=schema)
    visit_def = sa.schema.Table("visit_definition", metadata, autoload_with=bind, schema=schema)

    min_seq: sa.sql.Select = (
        sa.select(sa.sql.functions.min(exposure.columns["seq_num"]))
        .select_from(exposure.join(visit_def))
        .where(
            sa.sql.expression.and_(
                visit_def.columns["instrument"] == visit.columns["instrument"],
                visit_def.columns["visit"] == visit.columns["id"],
            )
        )
    )
    sql = visit.update().values(seq_num=min_seq.scalar_subquery())
    op.execute(sql)


def _migrate_exposure(has_simulated: bool) -> None:
    """Update exposure table.

    Parameters
    ----------
    has_simulated : `bool`
        Value for the new ``has_simulated`` column.
    """
    mig_context = context.get_context()
    bind = mig_context.bind
    schema = mig_context.version_table_schema
    metadata = sa.schema.MetaData(schema=schema)

    _LOG.info("migrating exposure table")
    with op.batch_alter_table("exposure", schema=schema) as batch_op:
        batch_op.add_column(sa.Column("seq_start", sa.BigInteger))  # type: ignore[attr-defined]
        batch_op.add_column(sa.Column("seq_end", sa.BigInteger))  # type: ignore[attr-defined]
        batch_op.add_column(sa.Column("azimuth", sa.Float))  # type: ignore[attr-defined]
        batch_op.add_column(sa.Column("has_simulated", sa.Boolean))  # type: ignore[attr-defined]

    table = sa.schema.Table("exposure", metadata, autoload_with=bind, schema=schema)
    op.execute(
        table.update().values(
            seq_start=table.columns["seq_num"], seq_end=table.columns["seq_num"], has_simulated=has_simulated
        )
    )


def _migrate_dimensions_json() -> None:
    """Updates dimensions definitions in dimensions.json."""

    mig_context = context.get_context()
    schema = mig_context.version_table_schema
    assert mig_context.bind is not None
    attributes = ButlerAttributes(mig_context.bind, schema)

    def update_config(config: Dict) -> Dict:
        config["version"] = 2

        instrument = config["elements"]["instrument"]
        instrument["metadata"].insert(
            1,
            {"name": "visit_system", "type": "int", "doc": "The preferred visit system for this instrument."},
        )

        visit = config["elements"]["visit"]
        visit["implies"].remove("visit_system")
        visit["metadata"].insert(
            1,
            {
                "name": "seq_num",
                "type": "int",
                "doc": "The sequence number of the first exposure that is part of this visit.",
            },
        )
        visit["metadata"].insert(
            -1,
            {
                "name": "azimuth",
                "type": "float",
                "doc": (
                    "Approximate azimuth of the telescope in degrees during the visit. "
                    "Can only be approximate since it is continually changing during "
                    "an observation and multiple exposures can be combined from a "
                    "relatively long period."
                ),
            },
        )

        exposure = config["elements"]["exposure"]
        exposure["metadata"].insert(
            6,
            {
                "name": "seq_start",
                "type": "int",
                "doc": "The sequence number of the first exposure of the visit that contains this exposure.",
            },
        )
        exposure["metadata"].insert(
            7,
            {
                "name": "seq_end",
                "type": "int",
                "doc": "The sequence number of the final exposure of the visit that contains this exposure.",
            },
        )
        exposure["metadata"].insert(
            -1,
            {
                "name": "azimuth",
                "type": "float",
                "doc": (
                    "Azimuth of the telescope at the start of the exposure in degrees. "
                    "Can be NULL for observations that are not on sky, or for observations "
                    "where the azimuth is not relevant."
                ),
            },
        )
        exposure["metadata"].append(
            {
                "name": "has_simulated",
                "type": "bool",
                "doc": (
                    "True if this exposure has some content that was simulated. "
                    "This can be if the data itself were simulated or if "
                    "parts of the header came from simulated systems, such as observations "
                    "in the lab that are recorded as on-sky."
                ),
            },
        )

        visit_system = config["elements"]["visit_system"]
        visit_system["doc"] = (
            "A system of self-consistent visit definitions, within which each "
            "exposure should appear at most once.\n"
            "A visit may belong to multiple visit systems, if the logical definitions "
            "for those systems happen to result in the same set of exposures - the "
            "main (and probably only) example is when a single-snap visit is observed, "
            'for which both the "one-to-one" visit system and a "group by header metadata" visit '
            "system will define the same single-exposure visit. "
        )

        visit_definition = config["elements"]["visit_definition"]
        visit_definition["requires"].remove("visit_system")
        visit_definition["requires"].append("visit")
        del visit_definition["implies"]

        config["elements"]["visit_system_membership"] = {
            "doc": "A many-to-many join table that relates visits to the visit_systems they belong to.",
            "requires": ["visit", "visit_system"],
            "always_join": True,
            "storage": {"cls": "lsst.daf.butler.registry.dimensions.table.TableDimensionRecordStorage"},
        }

        return config

    _LOG.info("migrating stored dimesions.json")
    attributes.update_dimensions_json(update_config)


def _migrate_schema_digest() -> None:
    """Update schema digest for dimension record tables.

    Note that that new releases, right before dimensions manager version
    6.0.2, stopped validating digests. We want to keep older releases happy
    though, so this digest actually corresponds to dimensions manager 6.0.1
    with updated dimensions configuration.
    """

    mig_context = context.get_context()
    bind = mig_context.bind
    assert bind is not None
    if bind.dialect.name == "postgresql":
        digest = "689da2cd495d8caba0eb05cb137c177f"
    elif bind.dialect.name == "sqlite":
        digest = "67be8681bc44dd8ed717b9056679f62c"
    else:
        return

    schema = mig_context.version_table_schema
    assert mig_context.bind is not None
    attributes = ButlerAttributes(mig_context.bind, schema)

    manager_class = attributes.get("config:registry.managers.dimensions")
    assert manager_class is not None, "Missing dimensions manager class"
    assert manager_class.endswith(
        "StaticDimensionRecordStorageManager"
    ), f"Unexpected class name of dimensions manager {manager_class}"

    _LOG.info("migrating dimensions schema digest")

    count = attributes.update(f"schema_digest:{manager_class}", digest)
    # In newer releases schema digest may not be written to the table.
    assert count < 2, "expected to update zero or one rows"
