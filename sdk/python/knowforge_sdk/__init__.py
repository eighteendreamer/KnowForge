import re
from typing import Any, Literal, Self
from urllib.parse import urlsplit

import httpx

SearchType = Literal["semantic", "keyword", "fuzzy", "hybrid", "auto"]


class KnowForgeError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: int | None = None,
        request_id: str | None = None,
        retry_after: str | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.code = code
        self.request_id = request_id
        self.retry_after = retry_after


class AsyncKnowForge:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 120,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        url = urlsplit(base_url)
        local = url.hostname in {"localhost", "127.0.0.1", "::1"}
        if (
            not url.hostname
            or (url.scheme != "https" and not (url.scheme == "http" and local))
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
        ):
            raise ValueError(
                "base_url must be HTTPS (HTTP is allowed only on loopback), without credentials, query or fragment"
            )
        if not api_key.strip() or "\r" in api_key or "\n" in api_key:
            raise ValueError("A non-empty API key is required")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            headers={"Authorization": "Bearer " + api_key, "Accept": "application/json"},
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, *, binary: bool = False, **kwargs: Any) -> Any:
        try:
            response = await self._client.request(method, "knowledge/" + path, **kwargs)
        except httpx.TimeoutException:
            raise KnowForgeError("KnowForge request timed out") from None
        except httpx.RequestError:
            raise KnowForgeError("KnowForge connection failed") from None
        details = {
            "status": response.status_code,
            "request_id": response.headers.get("X-Request-ID"),
            "retry_after": response.headers.get("Retry-After"),
        }
        if binary and response.is_success:
            return response.content
        try:
            payload = response.json()
        except ValueError:
            raise KnowForgeError("KnowForge returned a non-JSON response", **details) from None
        if not isinstance(payload, dict) or type(payload.get("code")) is not int:
            raise KnowForgeError("KnowForge returned an invalid response envelope", **details)
        if not response.is_success or payload["code"] != 0:
            message = payload.get("message")
            raise KnowForgeError(
                message if isinstance(message, str) else "KnowForge request failed",
                code=payload["code"],
                **details,
            )
        if "data" not in payload:
            raise KnowForgeError("KnowForge response is missing data", **details)
        return payload["data"]

    async def search(
        self,
        query: str,
        *,
        search_type: SearchType = "hybrid",
        top_k: int = 8,
        filters: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "search",
            json={
                "query": query,
                "search_type": search_type,
                "top_k": top_k,
                "filters": filters or {},
                "options": options or {},
            },
        )

    async def lookup(
        self,
        doc_id: str,
        *,
        page_start: int | None = None,
        page_end: int | None = None,
        include_chunks: bool = True,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "lookup",
            json={
                "doc_id": doc_id,
                "page_start": page_start,
                "page_end": page_end,
                "include_chunks": include_chunks,
            },
        )

    async def tags(self, category: str | None = None) -> dict[str, Any]:
        return await self._request("GET", "tags", params={"category": category} if category else {})

    async def categories(self) -> dict[str, Any]:
        return await self._request("GET", "categories")

    async def source(self, doc_id: str) -> bytes:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", doc_id):
            raise ValueError("Invalid document ID")
        return await self._request("GET", f"documents/{doc_id}/file", binary=True)
