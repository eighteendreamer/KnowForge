"""Repair tag_auto_approve_confidence values the first backfill wrote as 0.9.

The backfill in 5f1c7be0a9d4 seeded the key with 0.9, but the tagger gives an LLM-only tag confidence 0.7, so a
gate of 0.9 means no AI tag ever auto-approves — and Qdrant payloads, filters.tags and search responses publish
only approved tags, so the whole tag feature goes dark without any error. Settings now rejects a gate above the
LLM-only confidence (app.core.config.LLM_ONLY_CONFIDENCE), so any stored 0.9 would also break the read path for
these rows.

downgrade() is intentionally a no-op: the value being removed was invalid under the invariant, and there is no
prior intent to restore.
"""

import sqlalchemy as sa
from alembic import op

revision = "a3d9c51e7b48"
down_revision = "b7e4a19c0d52"
branch_labels = None
depends_on = None

TARGETS = (
    ("runtime_configurations", "values"),
    ("index_rebuilds", "target_values"),
    ("processing_tasks", "config_snapshot"),
)


def upgrade() -> None:
    bind = op.get_bind()
    for table, column in TARGETS:
        bind.execute(
            sa.text(
                f"UPDATE {table} SET {column} = jsonb_set({column}, '{{tag_auto_approve_confidence}}', '0.7') "
                f"WHERE {column} ? 'tag_auto_approve_confidence' "
                f"AND ({column}->>'tag_auto_approve_confidence')::numeric > 0.7"
            )
        )


def downgrade() -> None:
    # No-op by design: restoring 0.9 would re-break the tag pipeline.
    pass
