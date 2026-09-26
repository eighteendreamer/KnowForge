import pytest
from conftest import TEST_PASSWORD
from sqlalchemy import func, select

from app.models import Account, AuditLog, BalanceTransaction


async def register_customer(context, username="payer"):
    response = await context["client"].post(
        "/v1/portal/auth/register", json={"username": username, "password": TEST_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["user"]["id"]


async def sum_of_ledger(context, account_id: int) -> int:
    async with context["sessions"]() as session:
        total = await session.scalar(
            select(func.coalesce(func.sum(BalanceTransaction.amount_cent), 0)).where(
                BalanceTransaction.account_id == account_id
            )
        )
    return int(total)


async def recharge(context, account_id: int, amount_cent: int, note: str, headers=None):
    return await context["client"].post(
        f"/v1/admin/users/{account_id}/recharge",
        headers=headers or context["admin_headers"],
        json={"amount_cent": amount_cent, "note": note},
    )


async def test_new_account_starts_at_zero(context):
    account_id = await register_customer(context)
    wallet = await context["client"].get("/v1/portal/wallet", headers=context["customer_headers"])
    assert wallet.json()["data"]["balance_cent"] == 0
    assert wallet.json()["data"]["transactions"] == []
    assert await sum_of_ledger(context, account_id) == 0


async def test_manual_recharge_matches_the_ledger_sum(context):
    client = context["client"]
    account_id = context["customer"].id
    first = await recharge(context, account_id, 5050, "开业活动赠送")
    assert first.status_code == 200, first.text
    assert first.json()["data"]["balance_cent"] == 5050
    second = await recharge(context, account_id, 10000, "二次手动充值")
    assert second.json()["data"]["balance_cent"] == 15050
    assert await sum_of_ledger(context, account_id) == 15050

    wallet = await client.get("/v1/portal/wallet", headers=context["customer_headers"])
    rows = wallet.json()["data"]["transactions"]
    assert [row["amount_cent"] for row in rows] == [10000, 5050]
    assert {row["channel"] for row in rows} == {"manual"}
    assert all(row["note"] for row in rows)
    assert wallet.json()["data"]["balance_cent"] == await sum_of_ledger(context, account_id)

    async with context["sessions"]() as session:
        audit = await session.scalar(
            select(AuditLog).where(
                AuditLog.action == "recharge", AuditLog.target_type == "balance_transaction"
            )
        )
        assert audit.details == {"account_id": account_id, "amount_cent": 5050}
        entry = await session.scalar(select(BalanceTransaction).limit(1))
        assert entry.operator_id == context["admin"].id
        assert audit.operator_id == context["admin"].id


async def test_overview_and_admin_list_report_the_same_balance(context):
    client = context["client"]
    await recharge(context, context["customer"].id, 8800, "对账")
    overview = await client.get("/v1/portal/overview", headers=context["customer_headers"])
    counts = overview.json()["data"]["counts"]
    assert counts["balance_cent"] == 8800
    assert counts["active_keys"] == 0 and counts["total_calls"] == 0
    listed = await client.get("/v1/admin/users", headers=context["admin_headers"])
    row = next(item for item in listed.json()["data"]["items"] if item["id"] == context["customer"].id)
    assert row["balance_cent"] == 8800 and row["role"] == "end_user" and row["created_at"]


@pytest.mark.parametrize("amount", [0, -5, 100_000_001])
async def test_recharge_rejects_amounts_outside_range(context, amount):
    response = await recharge(context, context["customer"].id, amount, "越界")
    assert response.status_code == 400


async def test_recharge_requires_note_and_known_account(context):
    client = context["client"]
    missing_note = await client.post(
        f"/v1/admin/users/{context['customer'].id}/recharge",
        headers=context["admin_headers"],
        json={"amount_cent": 100},
    )
    assert missing_note.status_code == 400
    ghost = await recharge(context, 999999, 100, "幽灵")
    assert ghost.status_code == 404


async def test_content_admin_cannot_recharge(context):
    response = await recharge(context, context["customer"].id, 100, "越权", context["editor_headers"])
    assert response.status_code == 403


async def test_transactions_endpoint_is_super_admin_only(context):
    client = context["client"]
    await recharge(context, context["customer"].id, 700, "查账")
    rows = await client.get(
        f"/v1/admin/users/{context['customer'].id}/transactions", headers=context["admin_headers"]
    )
    assert [row["amount_cent"] for row in rows.json()["data"]["items"]] == [700]
    assert (
        await client.get(
            f"/v1/admin/users/{context['customer'].id}/transactions", headers=context["editor_headers"]
        )
    ).status_code == 403
    assert (
        await client.get(
            f"/v1/admin/users/{context['customer'].id}/transactions", headers=context["customer_headers"]
        )
    ).status_code == 401


async def test_role_cannot_be_set_to_an_unknown_value(context):
    forbidden = await context["client"].patch(
        f"/v1/admin/users/{context['customer'].id}", headers=context["admin_headers"], json={"role": "owner"}
    )
    assert forbidden.status_code == 400
    async with context["sessions"]() as session:
        assert (await session.get(Account, context["customer"].id)).role == "end_user"


async def test_super_admin_can_promote_a_portal_account(context):
    promoted = await context["client"].patch(
        f"/v1/admin/users/{context['customer'].id}",
        headers=context["admin_headers"],
        json={"role": "content_admin"},
    )
    assert promoted.status_code == 200 and promoted.json()["data"]["role"] == "content_admin"
    stale_portal_token = await context["client"].get(
        "/v1/portal/auth/me", headers=context["customer_headers"]
    )
    assert stale_portal_token.status_code == 401
