import hashlib
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

NONCE_BYTES = 12
KEY_BYTES = 32
FINGERPRINT_CHARS = 12


class MasterKeyUnavailable(RuntimeError):
    """主密钥没配好时不给任何读写凭据的口子：静默降级成明文落库比直接失败危险得多。"""


def require_master_key(master_key: bytes | None) -> bytes:
    if not master_key:
        raise MasterKeyUnavailable(
            "未配置支付主密钥：请在 .env 设置 KNOFORGE_PAYMENT_MASTER_KEY（32 字节随机数的 64 位十六进制）"
        )
    if len(master_key) != KEY_BYTES:
        raise MasterKeyUnavailable("支付主密钥必须是 32 字节")
    return master_key


def _derive(master_key: bytes, channel_id: int, key_name: str, key_version: int) -> bytes:
    # 子密钥按 渠道+字段+版本 派生：换字段或换版本都解不开旧密文，密文也就无法跨渠道移植。
    return HKDF(
        algorithm=hashes.SHA256(),
        length=KEY_BYTES,
        salt=str(channel_id).encode(),
        info=f"knowforge-payment:{key_name}:{key_version}".encode(),
    ).derive(master_key)


def _associated_data(channel_id: int, key_name: str, key_version: int) -> bytes:
    return f"{channel_id}|{key_name}|{key_version}".encode()


def seal(master_key: bytes, channel_id: int, key_name: str, key_version: int, plaintext: str) -> bytes:
    key = _derive(require_master_key(master_key), channel_id, key_name, key_version)
    nonce = os.urandom(NONCE_BYTES)
    return nonce + AESGCM(key).encrypt(
        nonce, plaintext.encode(), _associated_data(channel_id, key_name, key_version)
    )


def open_secret(
    master_key: bytes, channel_id: int, key_name: str, key_version: int, blob: bytes, label: str
) -> str:
    key = _derive(require_master_key(master_key), channel_id, key_name, key_version)
    if len(blob) <= NONCE_BYTES:
        raise ValueError(f"{label}的密文长度异常，无法解密")
    nonce, payload = blob[:NONCE_BYTES], blob[NONCE_BYTES:]
    try:
        return (
            AESGCM(key).decrypt(nonce, payload, _associated_data(channel_id, key_name, key_version)).decode()
        )
    except InvalidTag as reason:
        raise ValueError(f"{label}解密失败：密文被改动、密钥版本不符或来自其他渠道") from reason


def fingerprint(plaintext: str) -> str:
    """列表只回显这个前缀：能确认"换没换过"，又不足以反推任何密钥片段。"""
    return hashlib.sha256(plaintext.encode()).hexdigest()[:FINGERPRINT_CHARS]
