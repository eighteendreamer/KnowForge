from conftest import TEST_PASSWORD
from sqlalchemy import select

from app.models import Account, ApiKey


async def create_key(client, headers, name="生产密钥"):
    response = await client.post("/v1/portal/keys", headers=headers, json={"name": name})
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def other_customer(context, username="outsider"):
    """门户接口只认 end_user，所以“另一个用户”必须是注册的账号，不能用 editor。"""
    response = await context["client"].post(
        "/v1/portal/auth/register", json={"username": username, "password": TEST_PASSWORD}
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    return {"Authorization": "Bearer " + data["access_token"]}


async def test_portal_key_uses_column_defaults_and_shows_plaintext_once(context):
    client = context["client"]
    data = await create_key(client, context["customer_headers"])
    headers = {"Authorization": "Bearer " + data["key"]}
    assert data["rate_limit_per_day"] == 1000
    assert data["rate_limit_per_minute"] == 60
    assert data["scopes"] == ["knowledge:read"]
    async with context["sessions"]() as session:
        row = await session.get(ApiKey, data["id"])
        assert row.key_hash != data["key"] and row.owner_id == context["customer"].id
    listed = await client.get("/v1/portal/keys", headers=context["customer_headers"])
    assert data["key"] not in listed.text and "key_hash" not in listed.text
    assert (await client.get("/test/knowledge", headers=headers)).status_code == 200


async def test_portal_cannot_grant_itself_more_quota(context):
    response = await context["client"].post(
        "/v1/portal/keys",
        headers=context["customer_headers"],
        json={"name": "贪心", "rate_limit_per_day": 9999999},
    )
    assert response.status_code == 400
    assert response.json()["code"] == 1001


async def test_portal_sees_only_its_own_keys(context):
    client = context["client"]
    admin_key = await client.post(
        "/v1/admin/api-keys", headers=context["admin_headers"], json={"name": "管理员自建"}
    )
    assert admin_key.status_code == 200
    mine = await client.get("/v1/portal/keys", headers=context["customer_headers"])
    assert mine.json()["data"]["items"] == []
    assert admin_key.json()["data"]["name"] not in mine.text


async def test_other_peoples_keys_are_not_visible(context):
    client = context["client"]
    outsider = await other_customer(context)
    other = await create_key(client, outsider, name="别人的密钥")
    patched = await client.patch(
        f"/v1/portal/keys/{other['id']}", headers=context["customer_headers"], json={"name": "改名"}
    )
    assert patched.status_code == 404 and patched.json()["code"] == 1001
    revoked = await client.delete(f"/v1/portal/keys/{other['id']}", headers=context["customer_headers"])
    assert revoked.status_code == 404 and revoked.json()["code"] == 1001
    logs = await client.get(f"/v1/portal/keys/{other['id']}/logs", headers=context["customer_headers"])
    assert logs.status_code == 404


async def test_revoked_portal_key_is_immutable_and_stops_working(context):
    client = context["client"]
    data = await create_key(client, context["customer_headers"])
    headers = {"Authorization": "Bearer " + data["key"]}
    assert (await client.get("/test/knowledge", headers=headers)).status_code == 200
    assert (
        await client.delete(f"/v1/portal/keys/{data['id']}", headers=context["customer_headers"])
    ).status_code == 200
    assert (await client.get("/test/knowledge", headers=headers)).status_code == 401
    revive = await client.patch(
        f"/v1/portal/keys/{data['id']}", headers=context["customer_headers"], json={"status": "active"}
    )
    assert revive.status_code == 400


async def test_portal_usage_lists_only_own_calls_with_filters(context):
    client = context["client"]
    mine = await create_key(client, context["customer_headers"])
    outsider = await other_customer(context)
    theirs = await create_key(client, outsider, name="别人的密钥")
    for token in (mine["key"], theirs["key"]):
        assert (
            await client.get("/test/knowledge", headers={"Authorization": "Bearer " + token})
        ).status_code == 200
    usage = await client.get("/v1/portal/usage", headers=context["customer_headers"])
    data = usage.json()["data"]
    assert {row["api_key_id"] for row in data["items"]} == {mine["id"]}
    assert data["total"] == 1
    scoped = await client.get(f"/v1/portal/keys/{mine['id']}/logs", headers=context["customer_headers"])
    assert scoped.json()["data"]["total"] == 1
    empty = await client.get(
        "/v1/portal/usage", headers=context["customer_headers"], params={"status_code": 500}
    )
    assert empty.json()["data"]["items"] == []


async def test_future_only_expiry_and_naive_timezone_rejected(context):
    client = context["client"]
    headers = context["customer_headers"]
    past = await client.post(
        "/v1/portal/keys", headers=headers, json={"name": "过期", "expires_at": "2000-01-01T00:00:00Z"}
    )
    assert past.status_code == 400
    naive = await client.post(
        "/v1/portal/keys", headers=headers, json={"name": "无时区", "expires_at": "2099-01-01T00:00:00"}
    )
    assert naive.status_code == 400


async def test_admin_can_see_who_owns_a_portal_key(context):
    client = context["client"]
    created = await create_key(client, context["customer_headers"], name="门户密钥")
    listed = await client.get("/v1/admin/api-keys", headers=context["admin_headers"])
    row = next(item for item in listed.json()["data"]["items"] if item["id"] == created["id"])
    assert row["owner_username"] == "customer" and row["owner_role"] == "end_user"
    filtered = await client.get(
        "/v1/admin/api-keys", headers=context["admin_headers"], params={"owner_id": context["customer"].id}
    )
    assert [item["id"] for item in filtered.json()["data"]["items"]] == [created["id"]]


async def test_admin_endpoints_reject_end_user_accounts(context):
    client = context["client"]
    async with context["sessions"]() as session:
        row = await session.scalar(select(Account).where(Account.username == "customer"))
        assert row.role == "end_user"
    for path in ("/v1/admin/users", "/v1/admin/api-keys", "/v1/admin/recharge/channels"):
        assert (await client.get(path, headers=context["customer_headers"])).status_code == 401
    assert (
        await client.post(
            "/v1/admin/users",
            headers=context["customer_headers"],
            json={"username": "ghost", "password": TEST_PASSWORD, "role": "super_admin"},
        )
    ).status_code == 401
