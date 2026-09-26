import asyncio
import time
from io import BytesIO
from uuid import UUID, uuid4

import pymupdf
from sqlalchemy import func, select
from starlette.datastructures import Headers, UploadFile

from app.api.routes import documents
from app.core.errors import AppError
from app.models import Chunk, ChunkTag, Document, DocumentTag, ProcessingTask, Tag
from app.services.storage.file_storage import LocalStorage


def pdf_bytes() -> bytes:
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text(
            (50, 50), "Redis caching avoids repeated database reads. Use expiry and invalidation."
        )
        return document.tobytes()


async def test_upload_is_refused_before_writing_the_file_when_no_worker_consumes_the_queue(
    context, monkeypatch
):
    """A committed document whose task is never consumed cannot be re-submitted from the UI, so refuse at the door."""
    from app.services import task_queue

    async def offline(celery, session):
        return False

    monkeypatch.setattr(task_queue, "consumer_online", offline)
    root = context["settings"].storage_path
    before = sorted(str(path.relative_to(root)) for path in root.rglob("*") if path.is_file())
    response = await context["client"].post(
        "/v1/admin/documents/upload",
        headers=context["admin_headers"],
        files={"file": ("cache.pdf", pdf_bytes(), "application/pdf")},
    )
    assert response.status_code == 409 and "Worker" in response.json()["message"]
    assert sorted(str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()) == before
    async with context["sessions"]() as session:
        assert await session.scalar(select(func.count()).select_from(ProcessingTask)) == 0
        assert await session.scalar(select(func.count()).select_from(Document)) == 0


async def test_patching_a_ready_document_is_refused_when_no_worker_consumes_the_queue(context, monkeypatch):
    from app.services import task_queue

    client = context["client"]
    created = await client.post(
        "/v1/admin/documents/upload",
        headers=context["admin_headers"],
        files={"file": ("cache.pdf", pdf_bytes(), "application/pdf")},
    )
    doc_id = created.json()["data"]["doc_id"]
    async with context["sessions"]() as session:
        document = await session.scalar(select(Document).where(Document.doc_id == doc_id))
        document.status = "ready"
        await session.commit()

    async def offline(celery, session):
        return False

    monkeypatch.setattr(task_queue, "consumer_online", offline)
    refused = await client.patch(
        f"/v1/admin/documents/{doc_id}", headers=context["admin_headers"], json={"title": "改名"}
    )
    assert refused.status_code == 409 and "Worker" in refused.json()["message"]
    async with context["sessions"]() as session:
        document = await session.scalar(select(Document).where(Document.doc_id == doc_id))
        assert document.title != "改名"


async def test_upload_persists_document_task_and_safe_preview(context):
    client = context["client"]
    response = await client.post(
        "/v1/admin/documents/upload",
        headers=context["admin_headers"],
        files={"file": ("cache.pdf", pdf_bytes(), "application/pdf")},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "pending"
    assert "queued" not in data
    assert "source_path" not in data
    async with context["sessions"]() as session:
        task = await session.get(ProcessingTask, UUID(data["task_id"]))
        assert task.status == "pending"
        assert "model_api_key" not in task.config_snapshot
    preview = await client.get(f"/v1/admin/documents/{data['doc_id']}/file", headers=context["admin_headers"])
    assert preview.status_code == 200
    assert preview.content.startswith(b"%PDF")
    unauthenticated = await client.get(f"/v1/admin/documents/{data['doc_id']}/file")
    assert unauthenticated.status_code == 401
    tasks = await client.get("/v1/admin/tasks", headers=context["admin_headers"])
    assert tasks.json()["data"]["items"][0]["id"] == data["task_id"]
    created = await client.post(
        "/v1/admin/tags", headers=context["admin_headers"], json={"name": "缓存穿透", "color": "#18A058"}
    )
    assert created.status_code == 200, created.text
    tag_id = created.json()["data"]["id"]
    attached = await client.put(
        f"/v1/admin/documents/{data['doc_id']}/tags",
        headers=context["admin_headers"],
        json={"tag_ids": [tag_id]},
    )
    assert attached.status_code == 200, attached.text
    listed = await client.get("/v1/admin/documents", headers=context["admin_headers"])
    row = next(item for item in listed.json()["data"]["items"] if item["doc_id"] == data["doc_id"])
    assert row["source"] == "cache.pdf"
    assert row["tags"] == ["缓存穿透"]
    assert row["tag_total"] == 1
    audits = (await client.get("/v1/admin/audit-logs", headers=context["admin_headers"])).json()["data"][
        "items"
    ]
    upload_entry = next(
        entry for entry in audits if entry["action"] == "upload" and entry["target_id"] == data["doc_id"]
    )
    assert "client_ip" in upload_entry


async def test_html_preview_never_executes_scripts(context):
    client = context["client"]
    document = await client.post(
        "/v1/admin/documents/upload",
        headers=context["admin_headers"],
        files={
            "file": (
                "test.html",
                b'<html><p onclick="evil()">Redis</p><script>alert(1)</script><img src="https://evil.test"/></html>',
                "text/html",
            )
        },
    )
    assert document.status_code == 200, document.text
    doc_id = document.json()["data"]["doc_id"]
    preview = await client.get(f"/v1/admin/documents/{doc_id}/file", headers=context["admin_headers"])
    assert preview.status_code == 200
    assert "script" not in preview.text and "onclick" not in preview.text and "evil.test" not in preview.text
    assert "sandbox" in preview.headers["Content-Security-Policy"]


async def test_html_preview_declares_utf8_and_preserves_source_encoding(context):
    client, headers = context["client"], context["admin_headers"]
    for encoding in ["utf-8", "gb18030"]:
        source = (
            f'<html><head><meta charset="{encoding}"></head><body><h1>缓存穿透与布隆过滤器</h1></body></html>'
        )
        document = await client.post(
            "/v1/admin/documents/upload",
            headers=headers,
            files={"file": (f"preview-{encoding}.html", source.encode(encoding), "text/html")},
        )
        assert document.status_code == 200, document.text
        doc_id = document.json()["data"]["doc_id"]
        preview = await client.get(f"/v1/admin/documents/{doc_id}/file", headers=headers)
        assert preview.status_code == 200
        assert '<meta charset="utf-8">' in preview.text
        assert "缓存穿透与布隆过滤器" in preview.text
        assert "�" not in preview.text


async def test_html_preview_keeps_the_event_loop_responsive(context, monkeypatch):
    client = context["client"]
    upload = await client.post(
        "/v1/admin/documents/upload",
        headers=context["admin_headers"],
        files={"file": ("loop.html", b"<html><p>Redis</p></html>", "text/html")},
    )
    doc_id = upload.json()["data"]["doc_id"]

    def slow_clean(content: str, **kwargs: object) -> str:
        time.sleep(0.5)  # stands in for the real CPU cost of decoding and sanitizing
        return content

    monkeypatch.setattr(documents.nh3, "clean", slow_clean)
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    task = asyncio.create_task(ticker())
    try:
        preview = await client.get(f"/v1/admin/documents/{doc_id}/file", headers=context["admin_headers"])
    finally:
        task.cancel()
    assert preview.status_code == 200
    assert ticks >= 5, f"事件循环在 HTML 预览期间停摆，只走了 {ticks} 拍"


async def test_upload_rejects_disguised_pdf(context):
    response = await context["client"].post(
        "/v1/admin/documents/upload",
        headers=context["admin_headers"],
        files={"file": ("bad.pdf", b"<html>not PDF</html>", "application/pdf")},
    )
    assert response.status_code == 400
    async with context["sessions"]() as session:
        assert await session.scalar(select(ProcessingTask.id)) is None


async def test_storage_paths_and_size_are_bounded(context):
    storage = LocalStorage(context["settings"].model_copy(update={"upload_max_bytes": 10}))
    try:
        storage.resolve("../../.env")
        raise AssertionError("path traversal accepted")
    except AppError as error:
        assert error.code == 1001
    upload = UploadFile(
        BytesIO(b"%PDF-" + b"x" * 50),
        filename="large.pdf",
        headers=Headers({"content-type": "application/pdf"}),
    )
    try:
        await storage.save_upload(upload)
        raise AssertionError("oversize file accepted")
    except AppError as error:
        assert error.status == 400


async def test_delete_marks_tombstone_before_queue_processing(context):
    client = context["client"]
    response = await client.post(
        "/v1/admin/documents/upload",
        headers=context["admin_headers"],
        files={"file": ("cache.pdf", pdf_bytes(), "application/pdf")},
    )
    doc_id = response.json()["data"]["doc_id"]
    deleted = await client.delete(f"/v1/admin/documents/{doc_id}", headers=context["admin_headers"])
    assert deleted.status_code == 200
    detail = await client.get(f"/v1/admin/documents/{doc_id}", headers=context["admin_headers"])
    assert detail.json()["data"]["status"] == "deleting"
    retry = await client.post(f"/v1/admin/documents/{doc_id}/retry", headers=context["admin_headers"])
    assert retry.status_code == 400


async def test_document_list_limits_tags_to_manual_then_best_covered(context):
    client = context["client"]
    upload = await client.post(
        "/v1/admin/documents/upload",
        headers=context["admin_headers"],
        files={"file": ("cache.pdf", pdf_bytes(), "application/pdf")},
    )
    doc_id = upload.json()["data"]["doc_id"]
    async with context["sessions"]() as session:
        document = await session.scalar(select(Document).where(Document.doc_id == doc_id))
        chunk = Chunk(
            chunk_id="chunk_" + uuid4().hex,
            doc_id=document.id,
            chunk_index=0,
            text="Redis 缓存穿透",
            text_with_context="Redis 缓存穿透",
            char_count=10,
            token_count=4,
            qdrant_point_id=uuid4(),
        )
        session.add(chunk)
        await session.flush()
        covered = {f"标签{index}": index for index in range(12)}
        ids = {}
        for name in ["缓存穿透", *covered]:
            tag = Tag(name=name, normalized_name=name.casefold(), review_status="approved")
            session.add(tag)
            await session.flush()
            ids[name] = tag.id
        for name in covered:
            session.add(ChunkTag(chunk_id=chunk.id, tag_id=ids[name], source="auto", confidence=0.7))
            session.add(DocumentTag(doc_id=document.id, tag_id=ids[name], source="auto", confidence=0.7))
        # Manual curation on the document has no chunk coverage at all, yet must still lead the row.
        session.add(DocumentTag(doc_id=document.id, tag_id=ids["缓存穿透"], source="manual", confidence=1))
        await session.commit()
    listed = await client.get("/v1/admin/documents", headers=context["admin_headers"])
    row = next(item for item in listed.json()["data"]["items"] if item["doc_id"] == doc_id)
    assert row["tag_total"] == 13
    assert len(row["tags"]) == 8 < row["tag_total"]
    assert row["tags"][0] == "缓存穿透"
    assert row["tags"][1] == "标签0"
