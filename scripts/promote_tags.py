"""Approve the AI tags the configured auto-approve gate already trusts, then re-push document payloads.

Retired (rejected) tags are never touched: a human rejection is a decision, not backlog. Each affected ready
document gets exactly one sync_payload task — routing this through `POST /v1/admin/tags/batch-review` would
enqueue one task per document *per batch* and starve ingestion on the solo worker.
"""

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import select, update

from app.api.routes.tags import associated_documents
from app.core.config import Settings
from app.core.database import make_engine, make_session_factory
from app.models import Category, Tag
from app.services.catalog_sync import enqueue_catalog_sync
from app.services.runtime_config import active_settings
from app.services.tagging.tagger import taxonomy_names
from app.services.task_queue import dispatch
from app.worker.celery_app import celery_app

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "data/acceptance/quality-report.json"
UPDATE_BATCH = 500


def accuracy_evidence(path: Path) -> dict:
    """Bulk approval without a measured accuracy number is how unreviewed junk reaches retrieval."""
    if not path.exists():
        raise SystemExit(f"缺少 {path.name}：先跑 acceptance_quality.py --tags 120 并由人工背书，再批量过审")
    check = json.loads(path.read_text(encoding="utf-8")).get("checks", {}).get("标签准确率")
    if check is None:
        raise SystemExit("quality-report.json 里没有标签准确率这一项，不能批量过审")
    if not check.get("passed"):
        raise SystemExit(
            f"标签准确率未达标（accuracy={check.get('accuracy')}，有效样本={check.get('judged_links')}），不能批量过审"
        )
    return check


async def pending_trusted_tag_ids(session, gate: float) -> tuple[list[int], int]:
    """Auto tags the gate already trusts, minus classification names like 后端开发 — approving those would push
    category nodes into `filters.tags`, which is the failure mode that made the sampled accuracy drop in the first place.
    """
    blocked = taxonomy_names(list(await session.scalars(select(Category.path))))
    rows = await session.execute(
        select(Tag.id, Tag.name)
        .where(Tag.review_status == "pending", Tag.auto_generated.is_(True), Tag.confidence >= gate)
        .order_by(Tag.id)
    )
    kept = [tag_id for tag_id, name in rows.all() if name.casefold() not in blocked]
    return kept, len(rows.all()) - len(kept)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", default=str(REPORT))
    parser.add_argument("--dry-run", action="store_true", help="只报告将要过审的标签数，不写库也不排队")
    args = parser.parse_args()
    check = accuracy_evidence(Path(args.report))
    settings = Settings()
    engine = make_engine(settings)
    try:
        async with make_session_factory(engine)() as session:
            runtime = await active_settings(session, settings)
            ids, skipped_taxonomy = await pending_trusted_tag_ids(
                session, runtime.tag_auto_approve_confidence
            )
            document_ids = await associated_documents(session, ids) if ids else set()
            for start in range(0, len(ids), UPDATE_BATCH):
                await session.execute(
                    update(Tag)
                    .where(Tag.id.in_(ids[start : start + UPDATE_BATCH]))
                    .values(review_status="approved")
                )
            tasks = [] if args.dry_run else await enqueue_catalog_sync(session, document_ids, runtime)
            if args.dry_run:
                await session.rollback()
            else:
                await session.commit()
                for task in tasks:
                    await dispatch(celery_app, session, str(task.id))
            print(
                json.dumps(
                    {
                        "dry_run": args.dry_run,
                        "promoted_tags": len(ids),
                        "skipped_as_category_name": skipped_taxonomy,
                        "docs_enqueued": len(tasks),
                        "gate": runtime.tag_auto_approve_confidence,
                        "accuracy_behind_this": check["accuracy"],
                        "judged_by": check.get("judged_by"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
