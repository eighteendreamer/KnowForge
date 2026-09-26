from sqlalchemy import select
from test_documents import pdf_bytes

from app.models import Document
from app.services.storage.file_storage import LocalStorage

UPLOAD_ID = "3f2b6f3a6d3e4c83a0c5df2f8d3a9e11"


def parts(content: bytes) -> list[bytes]:
    return [content[:9], content[9:25], content[25:]]


async def upload_in_chunks(client, headers: dict, content: bytes, filename: str = "chunked.pdf"):
    for number, part in enumerate(parts(content), start=1):
        response = await client.post(
            "/v1/admin/documents/upload/chunk",
            headers=headers,
            data={"upload_id": UPLOAD_ID, "part_number": number},
            files={"file": (f"{filename}.{number}", part, "application/pdf")},
        )
        assert response.status_code == 200, response.text
    return await client.post(
        "/v1/admin/documents/upload/complete",
        headers=headers,
        json={
            "upload_id": UPLOAD_ID,
            "filename": filename,
            "content_type": "application/pdf",
            "total_parts": 3,
        },
    )


async def test_chunked_upload_reassembles_the_document_and_clears_parts(context):
    client = context["client"]
    content = pdf_bytes()
    response = await upload_in_chunks(client, context["admin_headers"], content)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["file_size"] == len(content)
    assert data["source"] == "chunked.pdf"
    assert "queued" not in data
    preview = await client.get(f"/v1/admin/documents/{data['doc_id']}/file", headers=context["admin_headers"])
    assert preview.content == content
    async with context["sessions"]() as session:
        document = await session.scalar(select(Document).where(Document.doc_id == data["doc_id"]))
        assert LocalStorage(context["settings"]).resolve(document.source_path).read_bytes() == content
    storage = LocalStorage(context["settings"])
    assert not storage.resolve(f"uploads/{UPLOAD_ID}").exists()


async def test_chunked_upload_reports_missing_parts_and_rejects_bad_identifiers(context):
    client = context["client"]
    content = pdf_bytes()
    for number, part in enumerate(parts(content)[:2], start=1):
        await client.post(
            "/v1/admin/documents/upload/chunk",
            headers=context["admin_headers"],
            data={"upload_id": UPLOAD_ID, "part_number": number},
            files={"file": ("chunked.pdf", part, "application/pdf")},
        )
    missing = await client.post(
        "/v1/admin/documents/upload/complete",
        headers=context["admin_headers"],
        json={
            "upload_id": UPLOAD_ID,
            "filename": "chunked.pdf",
            "content_type": "application/pdf",
            "total_parts": 3,
        },
    )
    assert missing.status_code == 400
    assert "缺少第 3 个分片" in missing.json()["message"]
    invalid = await client.post(
        "/v1/admin/documents/upload/chunk",
        headers=context["admin_headers"],
        data={"upload_id": "../../escape", "part_number": 1},
        files={"file": ("chunked.pdf", content[:5], "application/pdf")},
    )
    assert invalid.status_code == 400
    zero = await client.post(
        "/v1/admin/documents/upload/chunk",
        headers=context["admin_headers"],
        data={"upload_id": UPLOAD_ID, "part_number": 0},
        files={"file": ("chunked.pdf", content[:5], "application/pdf")},
    )
    assert zero.status_code == 400
    assert "分片序号超出范围" in zero.json()["message"]


async def test_chunked_upload_rejects_a_disguised_file(context):
    client = context["client"]
    response = await client.post(
        "/v1/admin/documents/upload/chunk",
        headers=context["admin_headers"],
        data={"upload_id": UPLOAD_ID, "part_number": 1},
        files={"file": ("fake.pdf", b"#!/bin/sh\nrm -rf /", "application/pdf")},
    )
    assert response.status_code == 200
    complete = await client.post(
        "/v1/admin/documents/upload/complete",
        headers=context["admin_headers"],
        json={
            "upload_id": UPLOAD_ID,
            "filename": "fake.pdf",
            "content_type": "application/pdf",
            "total_parts": 1,
        },
    )
    assert complete.status_code == 400
    assert "PDF 文件格式与声明不匹配" in complete.json()["message"]
