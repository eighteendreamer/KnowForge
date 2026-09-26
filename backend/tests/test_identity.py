from datetime import UTC, datetime, timedelta

import pytest
from conftest import TEST_PASSWORD
from sqlalchemy import select

from app.models import Account, ApiKey, ApiLog, AuditLog


async def test_login_me_and_no_password_leak(context):
    client = context["client"]
    response = await client.post(
        "/v1/admin/auth/login", json={"username": "admin", "password": TEST_PASSWORD}
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["user"]["role"] == "super_admin"
    assert "password" not in response.text
    me = await client.get("/v1/admin/auth/me", headers={"Authorization": "Bearer " + data["access_token"]})
    assert me.json()["data"]["username"] == "admin"
    async with context["sessions"]() as session:
        assert await session.scalar(select(AuditLog.action)) == "login"


@pytest.mark.parametrize("username,password", [("admin", "wrong"), ("missing", TEST_PASSWORD)])
async def test_login_rejects_invalid_credentials(context, username, password):
    response = await context["client"].post(
        "/v1/admin/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 401
    assert response.json()["code"] == 2001


async def test_role_boundary_and_disabled_jwt(context):
    client = context["client"]
    assert (await client.get("/v1/admin/users")).status_code == 401
    assert (await client.get("/v1/admin/users", headers=context["editor_headers"])).status_code == 403
    result = await client.patch(
        f"/v1/admin/users/{context['editor'].id}",
        headers=context["admin_headers"],
        json={"status": "disabled"},
    )
    assert result.status_code == 200
    assert (await client.get("/v1/admin/auth/me", headers=context["editor_headers"])).status_code == 401


async def test_user_create_password_hash_and_duplicate(context):
    client = context["client"]
    body = {"username": "new-editor", "password": TEST_PASSWORD, "role": "content_admin"}
    first = await client.post("/v1/admin/users", headers=context["admin_headers"], json=body)
    assert first.status_code == 200, first.text
    assert "password" not in first.text
    async with context["sessions"]() as session:
        user = await session.get(Account, first.json()["data"]["id"])
        assert user.password_hash.startswith("$argon2id$")
    second = await client.post("/v1/admin/users", headers=context["admin_headers"], json=body)
    assert second.status_code == 409
    assert TEST_PASSWORD not in second.text


@pytest.mark.parametrize("changes", [{"status": "disabled"}, {"role": "content_admin"}, {"status": None}])
async def test_account_mutation_guards(context, changes):
    response = await context["client"].patch(
        f"/v1/admin/users/{context['admin'].id}", headers=context["admin_headers"], json=changes
    )
    assert response.status_code == 400


async def test_api_key_lifecycle_and_scopes(context):
    client, headers = context["client"], context["admin_headers"]
    created = await client.post("/v1/admin/api-keys", headers=headers, json={"name": "AI client"})
    assert created.status_code == 200, created.text
    data = created.json()["data"]
    key_headers = {"Authorization": "Bearer " + data["key"]}
    async with context["sessions"]() as session:
        row = await session.get(ApiKey, data["id"])
        assert row.key_hash != data["key"]
        assert len(row.key_hash) == 64
    listed = await client.get("/v1/admin/api-keys", headers=headers)
    assert data["key"] not in listed.text
    assert "key_hash" not in listed.text
    assert (await client.get("/test/knowledge", headers=key_headers)).status_code == 200
    assert (await client.get("/v1/admin/users", headers=key_headers)).status_code == 401
    assert (await client.get("/test/knowledge", headers=headers)).status_code == 401
    async with context["sessions"]() as session:
        assert await session.scalar(select(ApiLog.status_code).where(ApiLog.api_key_id == data["id"])) == 200
    assert (await client.delete(f"/v1/admin/api-keys/{data['id']}", headers=headers)).status_code == 200
    assert (await client.get("/test/knowledge", headers=key_headers)).status_code == 401
    assert (
        await client.patch(
            f"/v1/admin/api-keys/{data['id']}",
            headers=headers,
            json={"name": "AI client", "status": "active"},
        )
    ).status_code == 400


async def test_patch_key_accepts_partial_update_and_preserves_name(context):
    client = context["client"]
    headers = context["admin_headers"]
    data = (await client.post("/v1/admin/api-keys", headers=headers, json={"name": "Keep name"})).json()[
        "data"
    ]
    response = await client.patch(
        f"/v1/admin/api-keys/{data['id']}", headers=headers, json={"rate_limit_per_minute": 10}
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["name"] == "Keep name"
    assert response.json()["data"]["rate_limit_per_minute"] == 10
    invalid = await client.patch(f"/v1/admin/api-keys/{data['id']}", headers=headers, json={"name": None})
    assert invalid.status_code == 400


async def test_expired_key_and_scope_denied(context):
    client = context["client"]
    data = (
        await client.post("/v1/admin/api-keys", headers=context["admin_headers"], json={"name": "test"})
    ).json()["data"]
    headers = {"Authorization": "Bearer " + data["key"]}
    async with context["sessions"]() as session:
        row = await session.get(ApiKey, data["id"])
        row.scopes = []
        await session.commit()
    assert (await client.get("/test/knowledge", headers=headers)).status_code == 403
    async with context["sessions"]() as session:
        row = await session.get(ApiKey, data["id"])
        row.scopes = ["knowledge:read"]
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    assert (await client.get("/test/knowledge", headers=headers)).status_code == 401


async def test_key_rate_limit_with_real_redis(context):
    client = context["client"]
    data = (
        await client.post(
            "/v1/admin/api-keys",
            headers=context["admin_headers"],
            json={"name": "limited", "rate_limit_per_minute": 1},
        )
    ).json()["data"]
    headers = {"Authorization": "Bearer " + data["key"]}
    assert (await client.get("/test/knowledge", headers=headers)).status_code == 200
    response = await client.get("/test/knowledge", headers=headers)
    assert response.status_code == 429
    assert response.json()["code"] == 3001
    assert int(response.headers["Retry-After"]) >= 1


@pytest.mark.parametrize(
    "body",
    [
        {"name": "test", "expires_at": "2026-01-01T00:00:00Z"},
        {"name": "test", "expires_at": "2099-01-01T00:00:00"},
        {"name": "test", "rate_limit_per_day": 0},
    ],
)
async def test_invalid_api_key_input(context, body):
    response = await context["client"].post("/v1/admin/api-keys", headers=context["admin_headers"], json=body)
    assert response.status_code == 400
