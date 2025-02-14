"""Migration script for dimensions.yaml namespace=daf_butler version=4.

Revision ID: 9888256c6a18
Revises: c5ae3a2cd7c2
Create Date: 2023-12-04 18:16:25.375102

"""
from alembic import context
from lsst.daf.butler_migrate.butler_attributes import ButlerAttributes

# revision identifiers, used by Alembic.
revision = "9888256c6a18"
down_revision = "c5ae3a2cd7c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Upgrade from version 3 to version 4 following update of dimension.yaml
    in DM-34589.

    Summary of changes:

        - Database schema did not change, only contents of
          `config:dimensions.json` in `butler_attributes`.
        - Changes in `config:dimensions.json`:
          - "version" value updated to 4.
          - "populated_by: visit" added to three elements:
            `visit_detector_region`, `visit_definition` and
            `visit_system_membership`.
    """
    _migrate(3, 4, True)


def downgrade() -> None:
    """Undo changes made in upgrade()."""
    _migrate(4, 3, False)


def _migrate(old_version: int, new_version: int, upgrade: bool) -> None:
    """Do migration in either direction."""

    def _update_config(config: dict) -> dict:
        """Update dimension.json configuration"""
        assert config["version"] == old_version, f"dimensions.json version mismatch: {config['version']}"

        config["version"] = new_version

        elements = config["elements"]
        for element_name in ("visit_detector_region", "visit_definition", "visit_system_membership"):
            element = elements[element_name]
            if upgrade:
                element["populated_by"] = "visit"
            else:
                del element["populated_by"]

        return config

    mig_context = context.get_context()

    # When we use schemas in postgres then all tables belong to the same schema
    # so we can use alembic's version_table_schema to see where everything goes
    schema = mig_context.version_table_schema

    # Update attributes
    assert mig_context.bind is not None
    attributes = ButlerAttributes(mig_context.bind, schema)
    attributes.update_dimensions_json(_update_config)
