import asyncio

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.rate_limit import enforce_limit
from app.core.security import DUMMY_HASH, ROLE_AUDIENCE, verify_password
from app.models import Account
from app.schemas.identity import LoginInput
from app.services.audit import record_audit


async def authenticate(request: Request, session: AsyncSession, body: LoginInput, audience: str) -> Account:
    """登录的唯一实现，管理端与门户端只差签发对象。"""
    client_ip = request.client.host if request.client else "unknown"
    await enforce_limit(
        request.app.state.redis, request.app.state.settings.redis_prefix, f"login:{client_ip}", 10, 200
    )
    user = await session.scalar(select(Account).where(Account.username == body.username))
    valid = await asyncio.to_thread(
        verify_password, body.password.get_secret_value(), user.password_hash if user else DUMMY_HASH
    )
    if not valid or user is None or user.status != "active":
        raise AppError(401, 2001, "用户名或密码错误")
    if ROLE_AUDIENCE.get(user.role) != audience:
        raise AppError(403, 2002, "该账号不属于当前登录入口")
    record_audit(session, request, user, "login", "account", user.id)
    await session.commit()
    return user
