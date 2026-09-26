import asyncio
import json
import sys
import time
from pathlib import Path

import httpx
import pymupdf
from dotenv import dotenv_values

from app.core.config import Settings
from app.services.parser.chunker import Chunker
from app.services.parser.html_parser import ParseError, parse_html
from app.services.parser.pdf_parser import parse_pdf

ROOT = Path(__file__).resolve().parents[1]


def prepare_samples() -> list[Path]:
    samples = sorted((ROOT / "docs-spider/pdfs").rglob("*.pdf"), key=lambda path: path.stat().st_size)[:10]
    if len(samples) < 10:
        raise RuntimeError("At least ten user PDF documents are required")
    output = ROOT / "data/acceptance"
    output.mkdir(exist_ok=True)
    html = output / "redis-cache.html"
    html.write_text(
        "<!doctype html><html><head><title>Redis缓存实践</title></head><body><main><h1>Redis缓存实践</h1><h2>缓存穿透</h2><p>使用布隆过滤器提前判断查询键是否存在，并使用短期空值缓存，避免不存在的数据反复请求数据库。</p><h2>缓存雪崩</h2><p>为缓存过期时间增加随机偏移，避免大量键同时过期。</p><table><tr><th>问题</th><th>处理方式</th></tr><tr><td>缓存击穿</td><td>互斥锁与逻辑过期</td></tr></table></main></body></html>",
        encoding="utf-8",
    )
    scan = output / "real-scanned-page.pdf"
    original = next((ROOT / "docs-spider/pdfs").rglob("web架构师训练营-服务.pdf"))
    if not scan.exists():
        with pymupdf.open(original) as source, pymupdf.open() as target:
            target.insert_pdf(source, from_page=0, to_page=0)
            target.save(scan)
    return [*samples, html, scan]


def throughput_report(
    pages: int,
    recognition_pages: int,
    chunks: int,
    documents: int,
    failures: list[str],
    seconds: float,
    scanned_documents: int = 0,
    scanned_pages: int = 0,
) -> dict:
    """把解析+分块的计时结果整理成方案 8.4 要的几个指标。

    整篇扫描的文档单独计数，既不算失败也不进"页/分钟"：它们在本机解析不出正文，真实入库时要走
    Qwen3-VL 那一跳（延迟与配额都在模型侧），和 CPU 段混在一个速率里两个数就都不可信了。
    """
    if documents <= 0:
        raise ValueError("语料为空，无法测批量入库吞吐")
    cpu_documents = documents - scanned_documents
    return {
        "documents": documents,
        "scanned_documents": scanned_documents,
        "scanned_pages": scanned_pages,
        "failed_documents": len(failures),
        "failure_rate": round(len(failures) / documents, 4),
        "pages": pages,
        "recognition_pages": recognition_pages,
        "text_pages": pages - recognition_pages,
        "chunks": chunks,
        "seconds": round(seconds, 1),
        "pages_per_minute": round(pages * 60 / seconds, 1) if seconds > 0 else None,
        "chunks_per_minute": round(chunks * 60 / seconds, 1) if seconds > 0 else None,
        "documents_per_minute": round(cpu_documents * 60 / seconds, 1) if seconds > 0 else None,
        "failures": failures[:10],
        "scope": "页/分钟与 Chunk/分钟只统计能本机解析出正文的文档；向量化、打标与整篇扫描文档的 Qwen3-VL 识别另算，那三段要远程模型",
    }


def parse_only_throughput(settings: Settings) -> dict:
    started = time.perf_counter()
    pages = recognition_pages = chunks = scanned_documents = scanned_pages = 0
    failures: list[str] = []
    files = sorted((ROOT / "docs-spider/pdfs").rglob("*.pdf")) + sorted(
        (ROOT / "docs-spider/pdfs").rglob("*.html")
    )
    for index, path in enumerate(files):
        try:
            parsed = parse_pdf(path, settings) if path.suffix == ".pdf" else parse_html(path)
            made = Chunker(
                settings.tokenizer_path,
                settings.chunk_size,
                settings.chunk_overlap,
                settings.embedding_max_tokens,
            ).build(f"doc_throughput_{index}", parsed.elements, path.name)
        except ParseError:
            # 整篇都是扫描页：本机拿不到正文，真实入库要经过 Qwen3-VL，单独计数。
            with pymupdf.open(path) as source:
                scanned_pages += source.page_count
            scanned_documents += 1
            continue
        except Exception as exc:
            failures.append(f"{path.name}: {type(exc).__name__}: {exc}"[:180])
            continue
        pages += parsed.total_pages or len({element.page for element in parsed.elements if element.page})
        recognition_pages += len(parsed.recognition_pages)
        chunks += len(made)
    return throughput_report(
        pages,
        recognition_pages,
        chunks,
        len(files),
        failures,
        time.perf_counter() - started,
        scanned_documents,
        scanned_pages,
    )


async def main() -> None:
    if "--parse-throughput" in sys.argv:
        settings = Settings()
        report = await asyncio.to_thread(parse_only_throughput, settings)
        path = ROOT / "data/acceptance/ingest-throughput.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({**report, "report": str(path)}, ensure_ascii=False))
        return
    samples = prepare_samples()
    credentials = dotenv_values(ROOT / "data/admin-credentials.txt")
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=60) as client:
        health = await client.get("/health")
        health.raise_for_status()
        login = await client.post(
            "/v1/admin/auth/login",
            json={"username": credentials["username"], "password": credentials["password"]},
        )
        login.raise_for_status()
        headers = {"Authorization": "Bearer " + login.json()["data"]["access_token"]}
        existing = await client.get("/v1/admin/documents", headers=headers, params={"limit": 100})
        existing.raise_for_status()
        existing_by_name = {item["source"]: item for item in existing.json()["data"]["items"]}
        document_ids = []
        for path in samples:
            if path.name in existing_by_name:
                document = existing_by_name[path.name]
                document_ids.append(document["doc_id"])
                if document["status"] == "failed":
                    response = await client.post(
                        f"/v1/admin/documents/{document['doc_id']}/retry", headers=headers
                    )
                    response.raise_for_status()
                continue
            response = await client.post(
                "/v1/admin/documents/upload",
                headers=headers,
                files={
                    "file": (
                        path.name,
                        path.read_bytes(),
                        "application/pdf" if path.suffix == ".pdf" else "text/html",
                    )
                },
            )
            response.raise_for_status()
            document = response.json()["data"]
            document_ids.append(document["doc_id"])
            print(
                json.dumps({"uploaded": path.name, "doc_id": document["doc_id"]}, ensure_ascii=False),
                flush=True,
            )
        deadline = time.monotonic() + 1800
        while True:
            documents = []
            for doc_id in document_ids:
                response = await client.get(f"/v1/admin/documents/{doc_id}", headers=headers)
                response.raise_for_status()
                documents.append(response.json()["data"])
            states = [document["status"] for document in documents]
            if all(state in {"ready", "failed"} for state in states):
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("Document ingestion did not finish within 30 minutes")
            await asyncio.sleep(5)
        report = {
            "documents": documents,
            "ready": states.count("ready"),
            "failed": states.count("failed"),
            "searches": [],
        }
        report_path = ROOT / "data/acceptance/ingest-report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if report["failed"]:
            print(
                json.dumps(
                    {
                        "failed": [
                            {"source": doc["source"], "error": doc["parse_error"]}
                            for doc in documents
                            if doc["status"] == "failed"
                        ]
                    },
                    ensure_ascii=False,
                )
            )
            raise RuntimeError("One or more real ingestion tasks failed")
        key_response = await client.post(
            "/v1/admin/api-keys", headers=headers, json={"name": "acceptance-ingest"}
        )
        key_response.raise_for_status()
        key = key_response.json()["data"]
        try:
            for mode in ["semantic", "keyword", "fuzzy", "hybrid"]:
                response = await client.post(
                    "/v1/knowledge/search",
                    headers={"Authorization": "Bearer " + key["key"]},
                    json={"query": "Redis 缓存穿透如何解决", "search_type": mode, "top_k": 5},
                )
                response.raise_for_status()
                data = response.json()["data"]
                report["searches"].append(
                    {
                        "mode": mode,
                        "results": len(data["results"]),
                        "took_ms": data["took_ms"],
                        "sources": [item["source"] for item in data["results"]],
                    }
                )
        finally:
            await client.delete(f"/v1/admin/api-keys/{key['id']}", headers=headers)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            json.dumps(
                {"ready": report["ready"], "searches": report["searches"], "report": str(report_path)},
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
