import hashlib
import os

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.services.payments import crypto

MASTER = bytes(range(32))
SECRET = "-----BEGIN PRIVATE KEY-----\nMIIBVgIBADANBgkqhkiG9w0BAgME\n-----END PRIVATE KEY-----\n"


def test_seal_then_open_round_trips_and_never_carries_the_plaintext() -> None:
    blob = crypto.seal(MASTER, 7, "merchant_private_key", 1, SECRET)
    assert SECRET.encode() not in blob
    assert crypto.open_secret(MASTER, 7, "merchant_private_key", 1, blob, "商户API私钥") == SECRET
    assert (
        crypto.open_secret(
            MASTER, 7, "notify_url", 3, crypto.seal(MASTER, 7, "notify_url", 3, "https://x/notify"), "回调"
        )
        == "https://x/notify"
    )


def test_the_construction_is_plain_hkdf_plus_aes_gcm_so_other_tools_can_read_it() -> None:
    # 只在自己的加解密函数里自洽不算数：按公开构造独立复现一次，能对上才说明没有自造密码格式。
    blob = crypto.seal(MASTER, 11, "apiv3_key", 2, "KnowforgeTestApiV3Key32Chars!!!!")
    key = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=b"11", info=b"knowforge-payment:apiv3_key:2"
    ).derive(MASTER)
    recovered = (
        AESGCM(key)
        .decrypt(blob[: crypto.NONCE_BYTES], blob[crypto.NONCE_BYTES :], b"11|apiv3_key|2")
        .decode()
    )
    assert recovered == "KnowforgeTestApiV3Key32Chars!!!!"


@pytest.mark.parametrize(
    ("channel_id", "key_name", "key_version"),
    [(8, "merchant_private_key", 1), (7, "apiv3_key", 1), (7, "merchant_private_key", 2)],
)
def test_any_side_of_the_binding_being_wrong_fails_closed(
    channel_id: int, key_name: str, key_version: int
) -> None:
    blob = crypto.seal(MASTER, 7, "merchant_private_key", 1, SECRET)
    with pytest.raises(ValueError, match="解密失败"):
        crypto.open_secret(MASTER, channel_id, key_name, key_version, blob, "商户API私钥")


def test_a_transplanted_ciphertext_is_refused_even_with_the_same_key_name() -> None:
    blob = crypto.seal(MASTER, 3, "app_private_key", 1, SECRET)
    with pytest.raises(ValueError, match="其他渠道"):
        crypto.open_secret(MASTER, 4, "app_private_key", 1, blob, "应用私钥")


@pytest.mark.parametrize("blob", [b"", os.urandom(crypto.NONCE_BYTES)])
def test_a_ciphertext_without_payload_is_rejected_by_length(blob: bytes) -> None:
    with pytest.raises(ValueError, match="长度异常"):
        crypto.open_secret(MASTER, 1, "app_id", 1, blob, "应用 APPID")


@pytest.mark.parametrize("blob", [os.urandom(crypto.NONCE_BYTES + 1), os.urandom(64)])
def test_random_bytes_fail_cleanly_instead_of_crashing(blob: bytes) -> None:
    with pytest.raises(ValueError, match="解密失败"):
        crypto.open_secret(MASTER, 1, "app_id", 1, blob, "应用 APPID")


@pytest.mark.parametrize(
    ("master", "message"),
    [
        (None, "KNOFORGE_PAYMENT_MASTER_KEY"),
        (b"", "KNOFORGE_PAYMENT_MASTER_KEY"),
        (b"too-short", "32 字节"),
    ],
)
def test_missing_or_wrong_sized_master_key_refuses_everything(master: bytes | None, message: str) -> None:
    with pytest.raises(crypto.MasterKeyUnavailable, match=message):
        crypto.require_master_key(master)
    with pytest.raises(crypto.MasterKeyUnavailable, match=message):
        crypto.seal(master or b"", 1, "app_id", 1, "x")


def test_fingerprint_is_stable_short_and_reveals_nothing() -> None:
    first = crypto.fingerprint(SECRET)
    assert first == crypto.fingerprint(SECRET)
    assert len(first) == crypto.FINGERPRINT_CHARS
    assert all(char in "0123456789abcdef" for char in first)
    assert first == hashlib.sha256(SECRET.encode()).hexdigest()[: crypto.FINGERPRINT_CHARS]
    assert crypto.fingerprint(SECRET + "x") != first
    assert SECRET[:20] not in first
