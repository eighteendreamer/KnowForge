import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import Settings
from app.core.errors import AppError

hasher = PasswordHasher()
DUMMY_HASH = hasher.hash(secrets.token_urlsafe(32))

ADMIN_AUDIENCE = "knowforge-admin"
PORTAL_AUDIENCE = "knowforge-portal"
# 角色到签发对象的唯一映射：登录按它选 audience，依赖按它复核，两处共用同一张表。
ROLE_AUDIENCE = {
    "super_admin": ADMIN_AUDIENCE,
    "content_admin": ADMIN_AUDIENCE,
    "end_user": PORTAL_AUDIENCE,
}


def hash_password(password: str) -> str:
    return hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def create_access_token(user_id: int, settings: Settings, audience: str) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user_id),
            "iat": now,
            "exp": now + timedelta(minutes=settings.jwt_ttl_minutes),
            "iss": "knowforge",
            "aud": audience,
        },
        settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )


def decode_access_token(token: str, settings: Settings, audience: str) -> int:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=["HS256"],
            issuer="knowforge",
            audience=audience,
            options={"require": ["sub", "iat", "exp"]},
        )
        user_id = int(payload["sub"])
        if user_id < 1:
            raise ValueError("Invalid subject")
        return user_id
    except (jwt.InvalidTokenError, ValueError, TypeError) as exc:
        raise AppError(401, 2001, "登录凭据无效或已过期") from exc


def generate_api_key() -> tuple[str, str]:
    raw = "kf_" + secrets.token_urlsafe(32)
    return raw, api_key_digest(raw)


def api_key_digest(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()
