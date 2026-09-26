"""Upload every PDF in the local corpus through the real admin API and wait for terminal states."""

import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "http://127.0.0.1:8000"


async def login(client: httpx.AsyncClient) -> dict:
    credentials = dotenv_values(ROOT / "data/admin-credentials.txt")
    login_response = await client.post(
        "/v1/admin/auth/login",
        json={"username": credentials["username"], "password": credentials["password"]},
    )
    login_response.raise_for_status()
    return {"Authorization": "Bearer " + login_response.json()["data"]["access_token"]}


async def authorized(client: httpx.AsyncClient, headers: dict, method: str, path: str, **kwargs):
    """Refresh the short-lived admin token instead of aborting a multi-hour poll."""
    response = await client.request(method, path, headers=headers, **kwargs)
    if response.status_code == 401:
        headers = await login(client)
        response = await client.request(method, path, headers=headers, **kwargs)
    response.raise_for_status()
    return response


async def main() -> None:
    files = sorted((ROOT / "docs-spider/pdfs").rglob("*.pdf"), key=lambda path: path.stat().st_size)
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=120) as client:
        headers = await login(client)
        listed = await client.get("/v1/admin/documents", headers=headers, params={"limit": 100})
        listed.raise_for_status()
        known = {item["source"] for item in listed.json()["data"]["items"]}
        pending = [path for path in files if path.name not in known]
        print(json.dumps({"corpus": len(files), "already_known": len(known), "to_upload": len(pending)}))
        doc_ids = []
        for path in pending:
            response = await client.post(
                "/v1/admin/documents/upload",
                headers=headers,
                files={"file": (path.name, path.read_bytes(), "application/pdf")},
            )
            response.raise_for_status()
            doc_ids.append(response.json()["data"]["doc_id"])
            print(json.dumps({"uploaded": path.name, "size_mb": round(path.stat().st_size / 1048576, 2)}))
        if not doc_ids:
            doc_ids = [item["doc_id"] for item in listed.json()["data"]["items"]]
        deadline = time.monotonic() + 6 * 3600
        last = 0.0
        while True:
            states = {}
            for doc_id in doc_ids:
                response = await authorized(client, headers, "GET", f"/v1/admin/documents/{doc_id}")
                document = response.json()["data"]
                states[doc_id] = (document["source"], document["status"], document["total_chunks"])
            done = sum(1 for _, status, _ in states.values() if status in {"ready", "failed"})
            if time.monotonic() - last > 60:
                print(
                    json.dumps(
                        {
                            "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
                            "done": done,
                            "total": len(states),
                            "chunks": sum(
                                chunks for _, status, chunks in states.values() if status == "ready"
                            ),
                        }
                    ),
                    flush=True,
                )
                last = time.monotonic()
            if done == len(states):
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("语料入库未在 6 小时内收敛")
            await asyncio.sleep(10)
        report = {
            "created_at": datetime.now(UTC).isoformat(),
            "documents": [
                {"source": source, "status": status, "total_chunks": chunks}
                for source, status, chunks in sorted(states.values())
            ],
            "ready": sum(1 for _, status, _ in states.values() if status == "ready"),
            "failed": sum(1 for _, status, _ in states.values() if status == "failed"),
        }
        path = ROOT / "data/acceptance/corpus-report.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"ready": report["ready"], "failed": report["failed"], "report": str(path)}))


if __name__ == "__main__":
    asyncio.run(main())
