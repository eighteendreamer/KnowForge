"""方案 8.3 的入库侧异常：Worker 重复投递、Qdrant 写入失败、扫描页与文本页混排。

这三条都是"处理到一半出了问题"，验收点是状态不能骗人：不能出现重复分块、不能把半索引的文档标成
ready、也不能把识别失败的扫描页当成空白页静默通过。

前两条要驱动 `process_task`，它会自己另开数据库连接，所以测试数据必须写在 fixture 会话之外
（fixture 的会话在事务里回滚，另开的连接看不到），并在结束时显式清理。
"""

import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

import pymupdf
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.core.database import make_engine, make_session_factory
from app.models import Chunk, ChunkTag, Document, DocumentTag, ProcessingTask, Tag
from app.services.parser.pdf_parser import parse_pdf
from app.services.pipeline import new_task, process_task
from app.services.storage.file_storage import LocalStorage
from app.services.storage.qdrant_store import QdrantStore

HTML = "<html><h1>Redis</h1><p>Redis cache penetration is prevented with Bloom filters.</p></html>"


class StubGateway:
    """只提供入库需要的远程调用，让测试专注在投递幂等与写入失败上。"""

    async def embed(self, texts, priority="batch"):
        return [[0.1] * 4096 for _ in texts]

    async def json_chat(self, system, content, priority="batch"):
        return {"tags": ["Redis"], "category": "后端开发/缓存/Redis", "difficulty": "中级"}

    async def close(self) -> None:
        return None


@asynccontextmanager
async def _queued_document(context, filename):
    settings = context["runtime"]
    LocalStorage(settings).write_json(f"documents/{filename}", HTML)
    engine = make_engine(settings)
    sessions = make_session_factory(engine)
    async with sessions() as session:
        # 规则与 LLM 打标会往 tags 里插行，这些行不在 fixture 的事务里，必须自己收干净。
        existing_tags = set(await session.scalars(select(Tag.id)))
        document = Document(
            doc_id="doc_" + uuid4().hex,
            title="Redis",
            original_filename=filename,
            file_type="html",
            file_size=len(HTML),
            source_path=f"documents/{filename}",
        )
        session.add(document)
        await session.flush()
        task = new_task(document, settings)
        session.add(task)
        await session.commit()
        document_id, task_id = document.id, task.id
    try:
        yield async_sessionmaker(engine, expire_on_commit=False), document_id, task_id
    finally:
        async with sessions() as session:
            await session.execute(delete(Document).where(Document.id == document_id))
            leaked = [
                tag_id for tag_id in await session.scalars(select(Tag.id)) if tag_id not in existing_tags
            ]
            if leaked:
                await session.execute(delete(ChunkTag).where(ChunkTag.tag_id.in_(leaked)))
                await session.execute(delete(DocumentTag).where(DocumentTag.tag_id.in_(leaked)))
                await session.execute(delete(Tag).where(Tag.id.in_(leaked)))
            await session.commit()
        await engine.dispose()


async def test_duplicate_delivery_of_the_same_task_does_not_duplicate_chunks(context):
    async with _queued_document(context, "dup.html") as (sessions, document_id, task_id):
        gateway = StubGateway()
        # 同一个 task id 被投两次：后到的那次必须被任务状态或咨询锁挡在门外，且不能把异常抛给 Worker。
        results = await asyncio.gather(
            *(process_task(task_id, context["runtime"], gateway_override=gateway) for _ in range(2)),
            return_exceptions=True,
        )
        assert not [item for item in results if isinstance(item, BaseException)], results
        async with sessions() as session:
            task = await session.get(ProcessingTask, task_id)
            document = await session.get(Document, document_id)
            chunks = await session.scalar(
                select(func.count()).select_from(Chunk).where(Chunk.doc_id == document_id)
            )
            assert task.status == "succeeded"
            assert document.status == "ready"
            assert 0 < chunks == document.total_chunks


async def test_qdrant_write_failure_leaves_the_document_failed_not_ready(context, monkeypatch):
    async def broken_upsert(self, *args, **kwargs):
        raise RuntimeError("qdrant down")

    monkeypatch.setattr(QdrantStore, "upsert", broken_upsert)
    async with _queued_document(context, "writefail.html") as (sessions, document_id, task_id):
        await process_task(task_id, context["runtime"], gateway_override=StubGateway())
        async with sessions() as session:
            task = await session.get(ProcessingTask, task_id)
            document = await session.get(Document, document_id)
            assert task.status == "failed"
            assert document.status == "failed"
            assert document.parse_error


def test_mixed_scan_and_text_pages_flag_only_the_scanned_page(tmp_path):
    path = tmp_path / "mixed.pdf"
    with pymupdf.open() as source:
        text_page = source.new_page()
        text_page.insert_text(
            (50, 100), "Two phase commit coordinates distributed transactions.", fontsize=14
        )
        render = text_page.get_pixmap()
        image_page = source.new_page()
        image_page.insert_image(image_page.rect, stream=render.tobytes("png"))
        source.save(path)

    result = parse_pdf(path, Settings())

    assert result.recognition_pages == [2]
    assert {element.page for element in result.elements} == {1}
