import pytest

SECRET = "2088121200000000-private-key-material"


async def create_package(context, label="标准档 ¥100", amount_cent=10000, **extra):
    body = {
        "label": label,
        "amount_cent": amount_cent,
        "bonus_cent": 0,
        "enabled": True,
        "sort_order": 10,
        **extra,
    }
    return await context["client"].post(
        "/v1/admin/recharge/packages", headers=context["admin_headers"], json=body
    )


async def create_channel(context, code="alipay", **extra):
    body = {
        "code": code,
        "display_name": "支付宝",
        "merchant_id": "2088121200000000",
        "secret": SECRET,
        "enabled": True,
        **extra,
    }
    return await context["client"].post(
        "/v1/admin/recharge/channels", headers=context["admin_headers"], json=body
    )


async def test_recharge_tables_start_empty_so_portal_shows_placeholder(context):
    wallet = await context["client"].get("/v1/portal/wallet", headers=context["customer_headers"])
    assert wallet.json()["data"]["packages"] == [] and wallet.json()["data"]["channels"] == []
    for path in ("/v1/admin/recharge/packages", "/v1/admin/recharge/channels"):
        listed = await context["client"].get(path, headers=context["admin_headers"])
        assert listed.json()["data"]["items"] == []


async def test_package_crud_and_portal_only_sees_enabled(context):
    client = context["client"]
    created = await create_package(context)
    assert created.status_code == 200, created.text
    assert created.json()["data"]["amount_cent"] == 10000
    hidden = await create_package(context, label="下架档", amount_cent=5000, enabled=False)
    patched = await client.patch(
        f"/v1/admin/recharge/packages/{created.json()['data']['id']}",
        headers=context["admin_headers"],
        json={
            "label": "标准档 ¥100",
            "amount_cent": 12000,
            "bonus_cent": 2000,
            "enabled": True,
            "sort_order": 5,
        },
    )
    assert patched.json()["data"]["amount_cent"] == 12000 and patched.json()["data"]["bonus_cent"] == 2000
    wallet = await client.get("/v1/portal/wallet", headers=context["customer_headers"])
    labels = [row["label"] for row in wallet.json()["data"]["packages"]]
    assert labels == ["标准档 ¥100"]
    assert hidden.json()["data"]["label"] not in wallet.text
    deleted = await client.delete(
        f"/v1/admin/recharge/packages/{created.json()['data']['id']}", headers=context["admin_headers"]
    )
    assert deleted.status_code == 200
    listed = await client.get("/v1/admin/recharge/packages", headers=context["admin_headers"])
    assert [row["label"] for row in listed.json()["data"]["items"]] == ["下架档"]


@pytest.mark.parametrize(
    "body",
    [
        {"label": "零元档", "amount_cent": 0, "bonus_cent": 0, "enabled": True, "sort_order": 0},
        {"label": "倒贴", "amount_cent": 100, "bonus_cent": -1, "enabled": True, "sort_order": 0},
        {"amount_cent": 100, "bonus_cent": 0, "enabled": True, "sort_order": 0},
    ],
)
async def test_package_validation_is_fail_closed(context, body):
    response = await context["client"].post(
        "/v1/admin/recharge/packages", headers=context["admin_headers"], json=body
    )
    assert response.status_code == 400


async def test_channel_secret_is_write_only(context):
    client = context["client"]
    created = await create_channel(context)
    assert created.status_code == 200, created.text
    row = created.json()["data"]
    assert row["secret_configured"] is True and row["enabled"] is True
    assert SECRET not in created.text
    admin_listed = await client.get("/v1/admin/recharge/channels", headers=context["admin_headers"])
    assert SECRET not in admin_listed.text and '"secret":' not in admin_listed.text
    wallet = await client.get("/v1/portal/wallet", headers=context["customer_headers"])
    assert wallet.json()["data"]["channels"] == [{"code": "alipay", "display_name": "支付宝"}]
    assert "merchant_id" not in wallet.text


async def test_channel_secret_empty_string_clears_and_omitting_keeps(context):
    client = context["client"]
    channel_id = (await create_channel(context)).json()["data"]["id"]
    keep = await client.patch(
        f"/v1/admin/recharge/channels/{channel_id}",
        headers=context["admin_headers"],
        json={"display_name": "支付宝", "merchant_id": "2088121200000000", "enabled": True},
    )
    assert keep.json()["data"]["secret_configured"] is True
    clear = await client.patch(
        f"/v1/admin/recharge/channels/{channel_id}",
        headers=context["admin_headers"],
        json={"display_name": "支付宝", "merchant_id": None, "secret": "", "enabled": False},
    )
    assert clear.json()["data"]["secret_configured"] is False
    assert clear.json()["data"]["enabled"] is False
    assert clear.json()["data"]["merchant_id"] is None
    wallet = await client.get("/v1/portal/wallet", headers=context["customer_headers"])
    assert wallet.json()["data"]["channels"] == []


async def test_channel_code_is_immutable_and_unique(context):
    client = context["client"]
    channel_id = (await create_channel(context)).json()["data"]["id"]
    with_code = await client.patch(
        f"/v1/admin/recharge/channels/{channel_id}",
        headers=context["admin_headers"],
        json={"code": "wechat", "display_name": "微信支付", "enabled": True},
    )
    assert with_code.status_code == 400
    duplicate = await create_channel(context)
    assert duplicate.status_code == 409
    bad = await create_channel(context, code="支付宝")
    assert bad.status_code == 400


async def test_recharge_config_is_super_admin_only(context):
    for path in ("/v1/admin/recharge/packages", "/v1/admin/recharge/channels"):
        assert (await context["client"].get(path, headers=context["editor_headers"])).status_code == 403
        assert (await context["client"].get(path, headers=context["customer_headers"])).status_code == 401
        created = await context["client"].post(path, headers=context["editor_headers"], json={})
        assert created.status_code == 403


async def test_missing_config_rows_return_404(context):
    client = context["client"]
    patch = await client.patch(
        "/v1/admin/recharge/packages/999999",
        headers=context["admin_headers"],
        json={"label": "幽灵", "amount_cent": 100, "bonus_cent": 0, "enabled": True, "sort_order": 0},
    )
    assert patch.status_code == 404
    assert (
        await client.delete("/v1/admin/recharge/channels/999999", headers=context["admin_headers"])
    ).status_code == 404
