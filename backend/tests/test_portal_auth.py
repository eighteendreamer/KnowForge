import pytest
from conftest import TEST_PASSWORD
from sqlalchemy import select, update

from app.models import Account


async def test_register_creates_end_user_and_signs_portal_token(context):
    client = context["client"]
    response = await client.post(
        "/v1/portal/auth/register", json={"username": "shop-ai", "password": TEST_PASSWORD}
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["user"]["role"] == "end_user"
    assert "password" not in response.text and "$argon2" not in response.text
    me = await client.get("/v1/portal/auth/me", headers={"Authorization": "Bearer " + data["access_token"]})
    assert me.json()["data"]["username"] == "shop-ai"
    async with context["sessions"]() as session:
        row = await session.scalar(select(Account).where(Account.username == "shop-ai"))
        assert row.password_hash.startswith("$argon2id$")


@pytest.mark.parametrize(
    "body",
    [
        {"username": "shop-ai", "password": "short123"},
        {"username": "a", "password": TEST_PASSWORD},
        {"username": "bad name!", "password": TEST_PASSWORD},
        {"username": "shop-ai", "password": TEST_PASSWORD, "role": "super_admin"},
    ],
)
async def test_register_rejects_weak_or_privileged_input(context, body):
    response = await context["client"].post("/v1/portal/auth/register", json=body)
    assert response.status_code == 400
    assert response.json()["code"] == 1001


async def test_duplicate_username_is_conflict(context):
    client = context["client"]
    body = {"username": "shop-ai", "password": TEST_PASSWORD}
    assert (await client.post("/v1/portal/auth/register", json=body)).status_code == 200
    again = await client.post("/v1/portal/auth/register", json=body)
    assert again.status_code == 409
    assert TEST_PASSWORD not in again.text


async def test_register_rate_limit_per_ip(context):
    client = context["client"]
    for index in range(5):
        created = await client.post(
            "/v1/portal/auth/register", json={"username": f"bulk-{index}", "password": TEST_PASSWORD}
        )
        assert created.status_code == 200, created.text
    blocked = await client.post(
        "/v1/portal/auth/register", json={"username": "bulk-late", "password": TEST_PASSWORD}
    )
    assert blocked.status_code == 429
    assert blocked.json()["code"] == 3001


async def test_login_entries_are_mutually_closed(context):
    client = context["client"]
    admin_door = await client.post(
        "/v1/admin/auth/login", json={"username": "customer", "password": TEST_PASSWORD}
    )
    assert admin_door.status_code == 403
    assert admin_door.json()["code"] == 2002
    portal_door = await client.post(
        "/v1/portal/auth/login", json={"username": "admin", "password": TEST_PASSWORD}
    )
    assert portal_door.status_code == 403
    assert portal_door.json()["code"] == 2002


async def test_tokens_do_not_cross_surfaces(context):
    client = context["client"]
    assert (await client.get("/v1/portal/auth/me", headers=context["customer_headers"])).status_code == 200
    blocked_admin = await client.get("/v1/admin/dashboard", headers=context["customer_headers"])
    assert blocked_admin.status_code == 401
    assert blocked_admin.json()["code"] == 2001
    assert (await client.get("/v1/admin/auth/me", headers=context["admin_headers"])).status_code == 200
    blocked_portal = await client.get("/v1/portal/overview", headers=context["admin_headers"])
    assert blocked_portal.status_code == 401


async def test_disabled_portal_account_rejected(context):
    async with context["sessions"]() as session:
        await session.execute(
            update(Account).where(Account.id == context["customer"].id).values(status="disabled")
        )
        await session.commit()
    blocked = await context["client"].get("/v1/portal/auth/me", headers=context["customer_headers"])
    assert blocked.status_code == 401


async def test_password_change_verifies_current_password(context):
    client = context["client"]
    wrong = await client.put(
        "/v1/portal/auth/password",
        headers=context["customer_headers"],
        json={"current_password": "not-the-password", "new_password": "Another-password-2026"},
    )
    assert wrong.status_code == 401
    changed = await client.put(
        "/v1/portal/auth/password",
        headers=context["customer_headers"],
        json={"current_password": TEST_PASSWORD, "new_password": "Another-password-2026"},
    )
    assert changed.status_code == 200, changed.text
    assert "password" not in changed.text
    stale = await client.post(
        "/v1/portal/auth/login", json={"username": "customer", "password": TEST_PASSWORD}
    )
    assert stale.status_code == 401
    fresh = await client.post(
        "/v1/portal/auth/login", json={"username": "customer", "password": "Another-password-2026"}
    )
    assert fresh.status_code == 200
