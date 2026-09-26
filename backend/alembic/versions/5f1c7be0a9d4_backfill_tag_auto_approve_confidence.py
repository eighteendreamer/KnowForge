"""Backfill tag_auto_approve_confidence into stored runtime config and task snapshots.

Adding a member to RUNTIME_FIELDS is a breaking change for every persisted settings
dictionary, because build_settings and restore_snapshot require an exact field set.
"""

import sqlalchemy as sa
from alembic import op

revision = "5f1c7be0a9d4"
down_revision = "d912c285af04"
branch_labels = None
depends_on = None

# Must equal Settings' default: a persisted gate above LLM_ONLY_CONFIDENCE leaves every LLM tag pending forever,
# and Qdrant payloads, filters.tags and search responses only surface approved tags.
DEFAULT = '{"tag_auto_approve_confidence": 0.7}'
# Only rows that still lack the key are touched, so re-running is a no-op.
GUARD = "NOT values ? 'tag_auto_approve_confidence'"
TASK_GUARD = "NOT config_snapshot ? 'tag_auto_approve_confidence'"


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(f"UPDATE runtime_configurations SET values = values || '{DEFAULT}'::jsonb WHERE {GUARD}")
    )
    bind.execute(
        sa.text(
            "UPDATE index_rebuilds SET target_values = target_values || "
            f"'{DEFAULT}'::jsonb WHERE NOT target_values ? 'tag_auto_approve_confidence'"
        )
    )
    bind.execute(
        sa.text(
            f"UPDATE processing_tasks SET config_snapshot = config_snapshot || '{DEFAULT}'::jsonb "
            f"WHERE status IN ('pending', 'running', 'failed') AND {TASK_GUARD}"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    for table, column in (
        ("runtime_configurations", "values"),
        ("index_rebuilds", "target_values"),
        ("processing_tasks", "config_snapshot"),
    ):
        bind.execute(sa.text(f"UPDATE {table} SET {column} = {column} - 'tag_auto_approve_confidence'"))
