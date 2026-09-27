import base64
import datetime

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.services.payments import specs

APIV3_KEY = "KnowforgeTestApiV3Key32Chars!!!!"
FAKE_KEYS: dict[str, dict[str, str]] = {
    "alipay": {
        "app_id": "2021000000000000",
        "gateway_url": specs.ALIPAY_GATEWAY_PRODUCTION,
        "app_private_key": "",
        "alipay_public_key": "",
        "notify_url": "https://pay.example.invalid/v1/payments/notify/alipay",
    },
    "wechat": {
        "mch_id": "1600000000",
        "app_id": "wx0123456789abcdef",
        "apiv3_key": APIV3_KEY,
        "merchant_private_key": "",
        "cert_serial_no": "0123456789ABCDEF0123456789ABCDEF01234567",
        "notify_url": "https://pay.example.invalid/v1/payments/notify/wechat",
    },
    "stripe": {
        "secret_key": "sk_test_KnowforgeFakeKey0123456789",
        "webhook_signing_secret": "whsec_KnowforgeFakeSecret0123456789",
    },
}


def _der_to_base64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


@pytest.fixture(scope="module")
def rsa_material() -> dict[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.UTC)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "KnowForge Test")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(0x1234ABCD)
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return {
        "private_pem": key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ).decode(),
        "private_bare": _der_to_base64(
            key.private_bytes(
                serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            )
        ),
        "public_pem": key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode(),
        "public_bare": _der_to_base64(
            key.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )
        ),
        "cert_pem": certificate.public_bytes(serialization.Encoding.PEM).decode(),
    }


def test_every_field_declares_a_real_validator_and_unique_keys() -> None:
    for channel_type, fields in specs.CREDENTIAL_SPECS.items():
        keys = [field.key for field in fields]
        assert len(keys) == len(set(keys)), channel_type
        for field in fields:
            assert field.format in specs.VALIDATORS, f"{channel_type}.{field.key} 用了不存在的校验器"


def test_readonly_fields_carry_a_default_that_passes_its_own_validator() -> None:
    for fields in specs.CREDENTIAL_SPECS.values():
        for field in fields:
            if field.editable or not field.default:
                continue
            assert specs.VALIDATORS[field.format](field.default, field)


def test_the_three_providers_require_exactly_the_fields_the_merchant_console_gives_us() -> None:
    # 只有"少了就打不通厂商"的项才算必填。网关地址有默认值（正式网关），表单会预选好，
    # 不该以"必填"的姿态去要求人操作它。
    assert set(specs.required_keys("alipay")) == {"app_id", "app_private_key"}
    assert specs.field_map("alipay")["gateway_url"].default == specs.ALIPAY_GATEWAY_PRODUCTION
    assert set(specs.required_keys("wechat")) == {
        "mch_id",
        "app_id",
        "apiv3_key",
        "merchant_private_key",
        "cert_serial_no",
    }
    assert set(specs.required_keys("stripe")) == {"secret_key", "webhook_signing_secret"}
    assert specs.required_keys("custom") == ()
    assert set(specs.PAYABLE_TYPES) == {"alipay", "wechat", "stripe"}
    assert set(specs.PAYABLE_TYPES) <= set(specs.CHANNEL_TYPES)


def test_alipay_accepts_the_bare_base64_key_the_console_copies_out(rsa_material: dict[str, str]) -> None:
    from_bare = specs.normalize_input(
        "alipay", {**FAKE_KEYS["alipay"], "app_private_key": rsa_material["private_bare"]}
    )
    from_pem = specs.normalize_input(
        "alipay", {**FAKE_KEYS["alipay"], "app_private_key": rsa_material["private_pem"]}
    )
    assert from_pem["app_private_key"].startswith("-----BEGIN PRIVATE KEY-----")
    assert from_bare["app_private_key"] == from_pem["app_private_key"]
    # 签名算法/字符集是固定项：填错不报错也不生效，永远存成 RSA2/UTF-8。
    forced = specs.normalize_input(
        "alipay",
        {
            **FAKE_KEYS["alipay"],
            "sign_type": "MD5",
            "charset": "GBK",
            "app_private_key": rsa_material["private_pem"],
        },
    )
    assert forced["sign_type"] == "RSA2" and forced["charset"] == "UTF-8"


def test_wechat_key_material_certificate_and_public_key_validate(rsa_material: dict[str, str]) -> None:
    stored = specs.normalize_input(
        "wechat",
        {
            **FAKE_KEYS["wechat"],
            "merchant_private_key": rsa_material["private_pem"],
            "wechat_pay_public_key": rsa_material["public_bare"],
            "wechat_pay_public_key_id": "PUB_KEY_ID_1234567890",
            "platform_cert": rsa_material["cert_pem"],
        },
    )
    assert stored["wechat_pay_public_key"].startswith("-----BEGIN PUBLIC KEY-----")
    assert stored["platform_cert"] == rsa_material["cert_pem"].strip()
    assert stored["merchant_private_key"] == rsa_material["private_pem"]


def test_wechat_notify_url_rejects_the_plain_http_tunnel() -> None:
    with pytest.raises(ValueError, match="https"):
        specs.normalize_input("wechat", {"notify_url": "http://tunnel.example.invalid/notify"})
    with pytest.raises(ValueError, match="APIv3"):
        specs.normalize_input("wechat", {"apiv3_key": "too-short"})
    with pytest.raises(ValueError, match="商户号"):
        specs.normalize_input("wechat", {"mch_id": "admin"})
    with pytest.raises(ValueError, match="私钥"):
        specs.normalize_input("wechat", {"merchant_private_key": "not-a-key"})


def test_unknown_key_is_refused_instead_of_silently_dropped() -> None:
    with pytest.raises(ValueError, match="没有这个配置项"):
        specs.normalize_input("stripe", {"secret_keys": "sk_test_KnowforgeFakeKey0123456789"})


def test_empty_and_null_values_are_reported_as_clear_not_keep() -> None:
    assert specs.normalize_input("stripe", {"publishable_key": "", "api_version": None}) == {}


def test_legacy_columns_stay_visible_after_the_migration() -> None:
    stored = specs.normalize_input(
        "wechat", {"legacy_secret": "old-single-secret", "legacy_merchant_id": "admin"}
    )
    assert stored == {"legacy_secret": "old-single-secret", "legacy_merchant_id": "admin"}


def test_configuration_gate_names_what_is_still_missing(rsa_material: dict[str, str]) -> None:
    assert "商户号" in specs.missing_required("wechat", ["app_id"])
    assert specs.missing_required("wechat", list(FAKE_KEYS["wechat"])) == []
    # 公钥模式与平台证书二选一：只填公钥不填公钥ID，回调验签时必然找不到钥匙。
    assert specs.configuration_error("alipay", {"app_id", "app_private_key"}) == specs.ALIPAY_KEY_PAIR_MESSAGE
    assert specs.configuration_error("alipay", {"app_id", "alipay_public_cert"}) is None
    assert specs.configuration_error("wechat", {"mch_id"}) == specs.WECHAT_KEY_PAIR_MESSAGE
    assert specs.configuration_error("wechat", {"wechat_pay_public_key", "wechat_pay_public_key_id"}) is None
    assert specs.configuration_error("wechat", {"platform_cert"}) is None
    assert specs.configuration_error("stripe", {"secret_key"}) is None


def test_fixed_fields_fall_back_to_the_spec_default() -> None:
    # 迁移回填的历史行没有固定项，取用时按规格补，不能因此打不通厂商。
    effective = specs.with_defaults("alipay", {"app_id": "2021000000000000"})
    assert effective["sign_type"] == "RSA2" and effective["charset"] == "UTF-8"
    assert effective["app_id"] == "2021000000000000"


def test_metadata_endpoint_exposes_labels_but_never_values() -> None:
    view = specs.spec_view("alipay")
    assert view["payable"] is True and view["display"] == "支付宝"
    assert {field["key"] for field in view["fields"]} == {field.key for field in specs.spec_for("alipay")}
    private_key = next(field for field in view["fields"] if field["key"] == "app_private_key")
    assert private_key["secret"] is True and private_key["multiline"] is True
    fixed = next(field for field in view["fields"] if field["key"] == "sign_type")
    assert fixed["editable"] is False and fixed["required"] is False
    assert specs.spec_view("custom")["payable"] is False and specs.spec_view("custom")["fields"] == []
    with pytest.raises(ValueError, match="未知的渠道类型"):
        specs.spec_for("paypal")
