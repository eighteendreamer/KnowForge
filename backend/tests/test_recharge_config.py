import pytest


def alipay_credentials(payment_keys) -> dict[str, str]:
    return {
        "app_id": "2021000000000000",
        "gateway_url": "https://openapi.alipay.com/gateway.do",
        "app_private_key": payment_keys["private_pem"],
        "alipay_public_key": payment_keys["public_pem"],
        "notify_url": "https://pay.example.test/v1/payments/notify/alipay",
    }


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


async def create_channel(context, payment_keys, code="alipay", **extra):
    body = {
        "code": code,
        "display_name": "支付宝",
        "channel_type": "alipay",
        "enabled": True,
        "credentials": alipay_credentials(payment_keys),
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


async def test_channel_secret_material_never_leaves_the_server(context, payment_keys):
    client = context["client"]
    created = await create_channel(context, payment_keys)
    assert created.status_code == 200, created.text
    row = created.json()["data"]
    assert row["configuration"]["complete"] is True and row["enabled"] is True
    listed = await client.get("/v1/admin/recharge/channels", headers=context["admin_headers"])
    private = next(
        item for item in listed.json()["data"]["items"][0]["credentials"] if item["key"] == "app_private_key"
    )
    # 密钥项只回显指纹与版本；非密钥项（AppID、回调地址）要能看见原文，否则运营无法确认是哪个环境。
    assert private["secret"] is True and private["value"] is None and private["fingerprint"]
    assert payment_keys["private_pem"] not in created.text
    assert payment_keys["private_pem"] not in listed.text
    assert '"app_private_key"' in listed.text
    app_id = next(
        item for item in listed.json()["data"]["items"][0]["credentials"] if item["key"] == "app_id"
    )
    assert app_id["secret"] is False and app_id["value"] == "2021000000000000"
    wallet = await client.get("/v1/portal/wallet", headers=context["customer_headers"])
    # 门户只拿得到"能不能下单"这类展示字段，凭据一个键都不能出现在这个响应里。
    assert wallet.json()["data"]["channels"] == [
        {"code": "alipay", "display_name": "支付宝", "channel_type": "alipay", "orderable": True}
    ]
    assert "app_id" not in wallet.text and "credentials" not in wallet.text


async def test_channel_credentials_are_never_decrypted_for_the_list(context, payment_keys, monkeypatch):
    """列表面板只要指纹，把密钥类凭据解密出来没有任何用处，也就不必冒这个险。"""
    from app.services.payments import crypto

    await create_channel(context, payment_keys)
    calls: list[str] = []
    real = crypto.open_secret

    def spy(master, channel, key_name, version, blob, label):
        calls.append(key_name)
        return real(master, channel, key_name, version, blob, label)

    monkeypatch.setattr(crypto, "open_secret", spy)
    listed = await context["client"].get("/v1/admin/recharge/channels", headers=context["admin_headers"])
    assert listed.status_code == 200
    # 支付宝公钥按规格是"非密钥项"，它本来就是要给运营核对的；私钥才连解密都不发生。
    assert "app_private_key" not in calls
    assert {"app_id", "alipay_public_key", "notify_url"} <= set(calls)


async def test_channel_code_and_type_are_immutable_and_unique(context, payment_keys):
    client = context["client"]
    channel_id = (await create_channel(context, payment_keys)).json()["data"]["id"]
    for body in (
        {"code": "wechat"},
        {"channel_type": "stripe"},
        {"display_name": "支付宝", "merchant_id": "2088121200000000"},
    ):
        rejected = await client.patch(
            f"/v1/admin/recharge/channels/{channel_id}", headers=context["admin_headers"], json=body
        )
        assert rejected.status_code == 400, body
    duplicate = await create_channel(context, payment_keys)
    assert duplicate.status_code == 409
    bad = await create_channel(context, payment_keys, code="支付宝")
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
