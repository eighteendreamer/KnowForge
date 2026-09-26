from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from test_recharge_config import alipay_credentials, create_channel

from app.models import AuditLog

CHANNELS = "/v1/admin/recharge/channels"


async def patch_channel(context, channel_id: int, body: dict[str, Any]):
    return await context["client"].patch(
        f"{CHANNELS}/{channel_id}", headers=context["admin_headers"], json=body
    )


def credentials_of(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["key"]: item for item in payload["credentials"]}


async def test_editing_only_the_display_name_leaves_every_credential_untouched(context, payment_keys):
    """旧版把 secret 总是带在 body 里，改个显示名就把密钥静默清空了。"""
    created = await create_channel(context, payment_keys)
    before = credentials_of(created.json()["data"])
    renamed = await patch_channel(context, created.json()["data"]["id"], {"display_name": "支付宝网页支付"})
    assert renamed.status_code == 200, renamed.text
    after = credentials_of(renamed.json()["data"])
    assert set(after) == set(before)
    assert all(after[key]["fingerprint"] == before[key]["fingerprint"] for key in before)
    assert all(after[key]["key_version"] == 1 for key in after)
    assert after["app_private_key"]["value"] is None


async def test_a_new_value_rotates_only_that_credential(context, payment_keys):
    created = await create_channel(context, payment_keys)
    channel_id = created.json()["data"]["id"]
    rotated = await patch_channel(context, channel_id, {"credentials": {"app_id": "2021000000000001"}})
    after = credentials_of(rotated.json()["data"])
    assert after["app_id"]["key_version"] == 2 and after["app_id"]["value"] == "2021000000000001"
    assert after["app_private_key"]["key_version"] == 1
    assert (
        after["app_private_key"]["fingerprint"]
        == credentials_of(created.json()["data"])["app_private_key"]["fingerprint"]
    )


async def test_resending_the_same_value_does_not_claim_a_rotation(context, payment_keys):
    created = await create_channel(context, payment_keys)
    channel_id = created.json()["data"]["id"]
    same = await patch_channel(context, channel_id, {"credentials": {"app_id": "2021000000000000"}})
    assert credentials_of(same.json()["data"])["app_id"]["key_version"] == 1


async def test_null_and_empty_string_both_clear_a_credential(context, payment_keys):
    created = await create_channel(context, payment_keys)
    channel_id = created.json()["data"]["id"]
    cleared = await patch_channel(context, channel_id, {"credentials": {"notify_url": None, "app_id": ""}})
    after = credentials_of(cleared.json()["data"])
    assert "notify_url" not in after and "app_id" not in after
    assert after["app_private_key"]["configured"] is True


async def test_enabling_requires_the_full_field_set_and_names_what_is_missing(context, payment_keys):
    partial = await create_channel(
        context,
        payment_keys,
        code="wx-partial",
        channel_type="wechat",
        credentials={"mch_id": "1600000000", "app_id": "wx0123456789abcdef"},
        enabled=True,
    )
    assert partial.status_code == 400
    message = partial.json()["message"]
    assert "APIv3" in message and "商户API私钥" in message
    channel_id = (await create_channel(context, payment_keys, enabled=False)).json()["data"]["id"]
    refused = await patch_channel(context, channel_id, {"enabled": True, "credentials": {"app_id": ""}})
    assert refused.status_code == 400 and "应用 APPID" in refused.json()["message"]


async def test_the_two_key_modes_are_enforced_as_a_pair(context, payment_keys):
    body = alipay_credentials(payment_keys)
    body.pop("alipay_public_key")
    half = await create_channel(context, payment_keys, code="alipay-half", credentials=body, enabled=True)
    assert half.status_code == 400 and "支付宝公钥" in half.json()["message"]
    with_cert = await create_channel(
        context,
        payment_keys,
        code="alipay-cert",
        credentials={**body, "alipay_public_cert": payment_keys["cert_pem"]},
        enabled=True,
    )
    assert with_cert.status_code == 200, with_cert.text


async def test_unknown_credential_key_is_refused_instead_of_dropped(context, payment_keys):
    body = alipay_credentials(payment_keys)
    body["app_private_keyx"] = "whatever"
    rejected = await create_channel(context, payment_keys, code="alipay-typo", credentials=body)
    assert rejected.status_code == 400 and "没有这个配置项" in rejected.json()["message"]


async def test_a_bad_private_key_is_refused_at_save_time(context, payment_keys):
    rejected = await create_channel(
        context,
        payment_keys,
        code="alipay-badkey",
        credentials={**alipay_credentials(payment_keys), "app_private_key": "not-a-key"},
    )
    assert rejected.status_code == 400 and "无法解析" in rejected.json()["message"]


async def test_missing_master_key_fails_closed_without_touching_plaintext(context, payment_keys, monkeypatch):
    created = await create_channel(context, payment_keys)
    channel_id = created.json()["data"]["id"]
    settings = context["settings"].model_copy(update={"payment_master_key": SecretStr("")})
    monkeypatch.setattr(context["app"].state, "settings", settings)
    listed = await context["client"].get(CHANNELS, headers=context["admin_headers"])
    assert listed.status_code == 503 and listed.json()["code"] == 5003
    assert "KNOFORGE_PAYMENT_MASTER_KEY" in listed.json()["message"]
    patched = await patch_channel(context, channel_id, {"display_name": "改名也要主密钥"})
    assert patched.status_code == 503
    # 主密钥缺失时连"新建渠道"都不能落一半，否则会出现没有凭据的空壳渠道。
    created_again = await create_channel(context, payment_keys, code="alipay-nokey")
    assert created_again.status_code == 503


async def test_specs_endpoint_drives_the_form_and_marks_secret_fields(context):
    listed = await context["client"].get(
        "/v1/admin/recharge/credential-specs", headers=context["admin_headers"]
    )
    assert listed.status_code == 200
    types = {item["channel_type"]: item for item in listed.json()["data"]["types"]}
    assert set(types) == {"alipay", "wechat", "stripe", "custom"}
    assert types["custom"]["payable"] is False and types["custom"]["fields"] == []
    fields = {item["key"]: item for item in types["wechat"]["fields"]}
    assert fields["apiv3_key"]["secret"] is True and fields["mch_id"]["secret"] is False
    assert fields["merchant_private_key"]["multiline"] is True
    assert types["alipay"]["display"] == "支付宝"


async def test_self_check_conclusion_is_remembered_and_a_rotated_key_voids_it(
    context, payment_keys, monkeypatch
):
    """ "配置完整"是我们自己的判断，"厂商认这把钥匙"必须被记住，否则列表又会开始说谎。"""
    from app.services.payments.providers import registry
    from app.services.payments.providers.base import Check, SelfCheck

    async def passing(channel_type: str, credentials: dict[str, str]) -> SelfCheck:
        return SelfCheck("live", (Check("网关受理签名且应用有效", True, "code=40004"),))

    monkeypatch.setattr(registry, "run_self_check", passing)
    channel_id = (await create_channel(context, payment_keys, code="alipay-verified")).json()["data"]["id"]
    assert (await _channel(context, channel_id))["verification"]["checked"] is False

    verified = await context["client"].post(
        f"/v1/admin/recharge/channels/{channel_id}/verify", headers=context["admin_headers"]
    )
    assert verified.status_code == 200 and verified.json()["data"]["passed"] is True
    row = await _channel(context, channel_id)
    assert row["verification"] == {
        "checked": True,
        "passed": True,
        "at": row["verification"]["at"],
        "detail": None,
    }
    assert row["verification"]["at"]

    rotated = await patch_channel(context, channel_id, {"credentials": {"app_id": "2021000000000009"}})
    assert rotated.json()["data"]["verification"]["checked"] is False


async def test_a_failed_self_check_keeps_the_reason_for_the_list_badge(context, payment_keys, monkeypatch):
    from app.services.payments.providers import registry
    from app.services.payments.providers.base import Check, SelfCheck

    async def failing(channel_type: str, credentials: dict[str, str]) -> SelfCheck:
        return SelfCheck("live", (Check("响应验签", False, "配的支付宝公钥与网关签名不匹配"),))

    monkeypatch.setattr(registry, "run_self_check", failing)
    channel_id = (await create_channel(context, payment_keys, code="alipay-badkey2")).json()["data"]["id"]
    await context["client"].post(
        f"/v1/admin/recharge/channels/{channel_id}/verify", headers=context["admin_headers"]
    )
    row = await _channel(context, channel_id)
    assert row["verification"]["passed"] is False
    assert "支付宝公钥" in row["verification"]["detail"]


async def _channel(context, channel_id: int) -> dict[str, Any]:
    listed = await context["client"].get("/v1/admin/recharge/channels", headers=context["admin_headers"])
    return next(row for row in listed.json()["data"]["items"] if row["id"] == channel_id)


async def test_audit_logs_field_names_only(context, payment_keys):
    created = await create_channel(context, payment_keys)
    channel_id = created.json()["data"]["id"]
    await patch_channel(
        context, channel_id, {"credentials": {"app_private_key": payment_keys["private_bare"]}}
    )
    async with context["sessions"]() as session:
        rows = (
            await session.scalars(
                select(AuditLog).where(AuditLog.target_type == "recharge_channel").order_by(AuditLog.id)
            )
        ).all()
    fields = [name for row in rows for name in row.details.get("fields", [])]
    assert "credentials.app_private_key" in fields
    assert payment_keys["private_pem"] not in str([row.details for row in rows])
    assert payment_keys["private_bare"] not in str([row.details for row in rows])


@pytest.mark.parametrize("code", ["支付宝", "A" * 31, "with space"])
async def test_channel_code_shape_is_still_enforced(context, payment_keys, code):
    rejected = await create_channel(context, payment_keys, code=code)
    assert rejected.status_code == 400
