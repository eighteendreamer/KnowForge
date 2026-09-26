from uuid import uuid4

from sqlalchemy import select

from app.core.privacy import redact_query
from app.models import Chunk, ChunkTag, Document, DocumentTag, ProcessingTask, Tag


async def test_category_tree_move_updates_descendant_paths_and_rejects_cycles(context):
    client, headers = context["client"], context["admin_headers"]
    root = (await client.post("/v1/admin/categories", headers=headers, json={"name": "Custom"})).json()[
        "data"
    ]
    child = (
        await client.post(
            "/v1/admin/categories", headers=headers, json={"name": "A/B", "parent_id": root["id"]}
        )
    ).json()["data"]
    leaf = (
        await client.post(
            "/v1/admin/categories", headers=headers, json={"name": "Leaf", "parent_id": child["id"]}
        )
    ).json()["data"]
    cycle = await client.patch(
        f"/v1/admin/categories/{root['id']}", headers=headers, json={"parent_id": leaf["id"]}
    )
    assert cycle.status_code == 400
    renamed = await client.patch(
        f"/v1/admin/categories/{root['id']}", headers=headers, json={"name": "Renamed"}
    )
    assert renamed.status_code == 200, renamed.text
    rows = (await client.get("/v1/admin/categories", headers=headers)).json()["data"]["items"]
    assert next(row["path"] for row in rows if row["id"] == leaf["id"]) == "Renamed/A/B/Leaf"
    assert (await client.delete(f"/v1/admin/categories/{root['id']}", headers=headers)).status_code == 400
    assert (await client.delete(f"/v1/admin/categories/{leaf['id']}", headers=headers)).status_code == 200


async def test_category_drop_orders_siblings_and_moves_subtrees_atomically(context):
    client, headers = context["client"], context["admin_headers"]

    async def create(name, parent_id=None):
        response = await client.post(
            "/v1/admin/categories", headers=headers, json={"name": name, "parent_id": parent_id}
        )
        assert response.status_code == 200, response.text
        return response.json()["data"]

    root = await create("SortRoot")
    first, second, third = [await create(name, root["id"]) for name in ["A", "B", "C"]]
    leaf = await create("Leaf", third["id"])
    response = await client.post(
        f"/v1/admin/categories/{third['id']}/move",
        headers=headers,
        json={"target_id": first["id"], "position": "before"},
    )
    assert response.status_code == 200, response.text
    rows = (await client.get("/v1/admin/categories", headers=headers)).json()["data"]["items"]
    assert [row["name"] for row in rows if row["parent_id"] == root["id"]] == ["C", "A", "B"]
    response = await client.post(
        f"/v1/admin/categories/{third['id']}/move",
        headers=headers,
        json={"target_id": second["id"], "position": "inside"},
    )
    assert response.status_code == 200, response.text
    rows = (await client.get("/v1/admin/categories", headers=headers)).json()["data"]["items"]
    assert next(row["path"] for row in rows if row["id"] == leaf["id"]) == "SortRoot/B/C/Leaf"
    cycle = await client.post(
        f"/v1/admin/categories/{second['id']}/move",
        headers=headers,
        json={"target_id": leaf["id"], "position": "after"},
    )
    assert cycle.status_code == 400
    unchanged = (await client.get("/v1/admin/categories", headers=headers)).json()["data"]["items"]
    assert unchanged == rows
    response = await client.post(
        f"/v1/admin/categories/{third['id']}/move",
        headers=headers,
        json={"target_id": second["id"], "position": "after"},
    )
    assert response.status_code == 200
    rows = (await client.get("/v1/admin/categories", headers=headers)).json()["data"]["items"]
    assert [row["name"] for row in rows if row["parent_id"] == root["id"]] == ["A", "B", "C"]
    await create("C", first["id"])
    conflict = await client.post(
        f"/v1/admin/categories/{third['id']}/move",
        headers=headers,
        json={"target_id": first["id"], "position": "inside"},
    )
    assert conflict.status_code == 409
    invalid = await client.post(
        f"/v1/admin/categories/{third['id']}/move",
        headers=headers,
        json={"target_id": third["id"], "position": "before"},
    )
    assert invalid.status_code == 400


async def test_tags_review_merge_and_manual_clear_preserve_integrity(context):
    client, headers = context["client"], context["admin_headers"]
    first = (await client.post("/v1/admin/tags", headers=headers, json={"name": "缓存"})).json()["data"]
    second = (await client.post("/v1/admin/tags", headers=headers, json={"name": "Cache"})).json()["data"]
    assert (await client.post("/v1/admin/tags", headers=headers, json={"name": "cache"})).status_code == 409
    async with context["sessions"]() as session:
        document = Document(
            doc_id="doc_" + uuid4().hex,
            title="Test",
            original_filename="test.pdf",
            file_type="pdf",
            file_size=100,
            source_path="documents/test.pdf",
            status="ready",
            total_chunks=1,
        )
        session.add(document)
        await session.flush()
        point = uuid4()
        chunk = Chunk(
            doc_id=document.id,
            chunk_id="chunk_" + point.hex,
            chunk_index=0,
            text="Redis cache",
            text_with_context="Redis cache",
            char_count=11,
            token_count=2,
            qdrant_point_id=point,
        )
        session.add(chunk)
        await session.flush()
        for tag_id in [first["id"], second["id"]]:
            session.add(DocumentTag(doc_id=document.id, tag_id=tag_id, source="manual", confidence=1))
            session.add(ChunkTag(chunk_id=chunk.id, tag_id=tag_id, source="manual", confidence=1))
        await session.commit()
        doc_id, internal_id = document.doc_id, document.id
    merged = await client.post(
        "/v1/admin/tags/merge", headers=headers, json={"source_ids": [second["id"]], "target_id": first["id"]}
    )
    assert merged.status_code == 200, merged.text
    async with context["sessions"]() as session:
        assert await session.get(Tag, second["id"]) is None
        associations = list(
            await session.scalars(select(DocumentTag).where(DocumentTag.doc_id == internal_id))
        )
        assert len(associations) == 1 and associations[0].source == "manual"
        assert await session.scalar(select(ProcessingTask.id).where(ProcessingTask.doc_id == internal_id))
    clear = await client.put(f"/v1/admin/documents/{doc_id}/tags", headers=headers, json={"tag_ids": []})
    assert clear.status_code == 200
    assert (await client.get(f"/v1/admin/documents/{doc_id}/tags", headers=headers)).json()["data"][
        "items"
    ] == []
    rejected = await client.post(
        "/v1/admin/tags/batch-review",
        headers=headers,
        json={"ids": [first["id"]], "review_status": "rejected"},
    )
    assert rejected.status_code == 200
    assert (
        await client.put(
            f"/v1/admin/documents/{doc_id}/tags", headers=headers, json={"tag_ids": [first["id"]]}
        )
    ).status_code == 400


async def test_dashboard_and_catalog_roles(context):
    response = await context["client"].get("/v1/admin/dashboard", headers=context["editor_headers"])
    assert response.status_code == 200, response.text
    assert len(response.json()["data"]["trend"]) == 7
    assert (await context["client"].get("/v1/admin/tags")).status_code == 401
    assert (
        await context["client"].get("/v1/admin/audit-logs", headers=context["editor_headers"])
    ).status_code == 403


def test_query_redaction_covers_tokens_and_personal_information():
    value = "Redis sk-12345678901234567890 kf_1234567890123456 me@example.com 13812345678 password=secret"
    result = redact_query(value)
    assert result.startswith("Redis")
    assert result.count("[REDACTED]") == 5
