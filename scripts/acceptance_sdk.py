import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk/python"))
from knowforge_sdk import AsyncKnowForge, KnowForgeError  # noqa: E402


async def main() -> None:
    credentials = dotenv_values(ROOT / "data/admin-credentials.txt")
    base_url = os.environ.get("KNOFORGE_ACCEPTANCE_URL", "http://127.0.0.1:8000")
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "scope": "SDK functional verification on real API and existing corpus; not a relevance or load benchmark",
        "clients": [],
    }
    async with httpx.AsyncClient(base_url=base_url, timeout=30) as admin:
        login = await admin.post(
            "/v1/admin/auth/login",
            json={"username": credentials["username"], "password": credentials["password"]},
        )
        login.raise_for_status()
        headers = {"Authorization": "Bearer " + login.json()["data"]["access_token"]}
        created = await admin.post(
            "/v1/admin/api-keys",
            headers=headers,
            json={
                "name": "SDK acceptance " + datetime.now(UTC).isoformat(),
                "expires_at": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
                "rate_limit_per_minute": 120,
            },
        )
        created.raise_for_status()
        key = created.json()["data"]
        try:
            async with AsyncKnowForge(base_url + "/v1", key["key"], timeout=180) as sdk:
                runs = []
                for mode in ("semantic", "keyword", "fuzzy", "hybrid"):
                    started = time.perf_counter()
                    data = await sdk.search("Redis 缓存穿透如何解决", search_type=mode, top_k=5)
                    assert data["results"], f"No real results for {mode}"
                    first = data["results"][0]
                    source = await sdk.source(first["doc_id"])
                    detail = await sdk.lookup(first["doc_id"])
                    assert source and detail["content"], "Source traceability failed"
                    runs.append(
                        {
                            "mode": mode,
                            "results": len(data["results"]),
                            "source_bytes": len(source),
                            "took_ms": round((time.perf_counter() - started) * 1000),
                        }
                    )
                try:
                    await sdk.search(" ")
                    raise AssertionError("Invalid query was accepted")
                except KnowForgeError as error:
                    assert error.code == 1002
                report["clients"].append(
                    {
                        "language": "python",
                        "runs": runs,
                        "tags": len((await sdk.tags())["tags"]),
                        "categories": len((await sdk.categories())["tree"]),
                        "validation_error": 1002,
                    }
                )
            result = await asyncio.to_thread(
                subprocess.run,
                ["node", str(ROOT / "sdk/javascript/acceptance.js")],
                input=json.dumps({"baseUrl": base_url + "/v1", "apiKey": key["key"]}),
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=900,
            )
            if result.returncode:
                raise RuntimeError("JavaScript SDK acceptance failed; check local service logs")
            report["clients"].append(json.loads(result.stdout))
        finally:
            revoked = await admin.delete(f"/v1/admin/api-keys/{key['id']}", headers=headers)
            revoked.raise_for_status()
            report["temporary_key_revoked"] = revoked.json()["data"]["status"] == "revoked"
            async with AsyncKnowForge(base_url + "/v1", key["key"]) as sdk:
                try:
                    await sdk.categories()
                    raise AssertionError("Revoked key was accepted")
                except KnowForgeError as error:
                    assert error.status == 401
                    report["revocation_verified"] = True
            output = ROOT / "data/acceptance/sdk-report.json"
            await asyncio.to_thread(
                output.write_text, json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
