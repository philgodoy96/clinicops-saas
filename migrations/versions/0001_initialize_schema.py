"""Initialize the ClinicOps migration chain.

Revision ID: 0001_initialize_schema
Revises:
Create Date: 2026-07-18
"""

from collections.abc import Sequence

revision: str = "0001_initialize_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Establish the initial migration revision."""

    pass


def downgrade() -> None:
    """Remove the initial migration revision."""

    pass
