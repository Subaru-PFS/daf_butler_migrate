"""Migration script for dimensions.yaml namespace=daf_butler version=1.

Revision ID: 380002bcbb26
Revises: f3bcee34f344
Create Date: 2022-05-13 14:38:35.806960

"""
from typing import Callable, Dict

from alembic import context
from lsst.daf.butler_migrate.butler_attributes import ButlerAttributes

# revision identifiers, used by Alembic.
revision = "380002bcbb26"
down_revision = "f3bcee34f344"
branch_labels = None
depends_on = None

# Change this when making a copy for different branch
_NAMESPACE = "daf_butler"


def upgrade() -> None:
    """Upgrade from version 0 to version 1.

    Version 1 adds few simple things to dimensions.json that do not modify
    the schema:

        - changes "version" value from 0 to 1
        - adds "namespace" key to the configuration
        - adds healpix to skypix

    """

    def update_config(config: Dict) -> Dict:
        config["version"] = 1
        config["namespace"] = _NAMESPACE
        config["skypix"]["healpix"] = {
            "class": "lsst.sphgeom.HealpixPixelization",
            "max_level": 17,
        }
        return config

    _migrate(update_config)


def downgrade() -> None:
    """Downgrade from version 1 to version 0, reverses changes made by
    `upgrade`.
    """

    def update_config(config: Dict) -> Dict:
        config["version"] = 0
        del config["namespace"]
        del config["skypix"]["healpix"]
        return config

    _migrate(update_config)


def _migrate(update_config: Callable[[Dict], Dict]) -> None:
    mig_context = context.get_context()

    # When we use schemas in postgres then all tables belong to the same schema
    # so we can use alembic's version_table_schema to see where everything goes
    schema = mig_context.version_table_schema

    assert mig_context.bind is not None
    attributes = ButlerAttributes(mig_context.bind, schema)
    attributes.update_dimensions_json(update_config)
