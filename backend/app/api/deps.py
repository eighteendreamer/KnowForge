from datetime import UTC, datetime
from typing import Annotated, NamedTuple

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.database import get_session
from app.core.errors import AppError
from app.core.model_client import ModelGateway
from app.core.rate_limit import enforce_limit
from app.core.security import (
    ADMIN_AUDIENCE,
    PORTAL_AUDIENCE,
    ROLE_AUDIENCE,
    api_key_digest,
    decode_access_token,
)
from app.models import Account, ApiKey
from app.services.runtime_config import active_settings
from app.services.storage.qdrant_store import QdrantStore

Session = Annotated[AsyncSession, Depends(get_session)]
bearer = HTTPBearer(auto_error=False)
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]


async def current_admin(request: Request, session: Session, credentials: Credentials) -> Account:
    if credentials is None:
        raise AppError(401, 2001, "请先登录")
    user_id = decode_access_token(credentials.credentials, request.app.state.settings, ADMIN_AUDIENCE)
    user = await session.get(Account, user_id)
    if user is None or user.status != "active":
        raise AppError(401, 2001, "账号不存在或已停用")
    if ROLE_AUDIENCE.get(user.role) != ADMIN_AUDIENCE:
        raise AppError(401, 2001, "账号角色已变更，请重新登录")
    return user


Admin = Annotated[Account, Depends(current_admin)]


async def require_super_admin(user: Admin) -> Account:
    if user.role != "super_admin":
        raise AppError(403, 2002, "需要超级管理员权限")
    return user


SuperAdmin = Annotated[Account, Depends(require_super_admin)]


async def current_portal_account(request: Request, session: Session, credentials: Credentials) -> Account:
    if credentials is None:
        raise AppError(401, 2001, "请先登录")
    user_id = decode_access_token(credentials.credentials, request.app.state.settings, PORTAL_AUDIENCE)
    user = await session.get(Account, user_id)
    if user is None or user.status != "active":
        raise AppError(401, 2001, "账号不存在或已停用")
    if ROLE_AUDIENCE.get(user.role) != PORTAL_AUDIENCE:
        raise AppError(401, 2001, "账号角色已变更，请重新登录")
    return user


PortalAccount = Annotated[Account, Depends(current_portal_account)]


async def knowledge_key(request: Request, session: Session, credentials: Credentials) -> ApiKey:
    if credentials is None:
        raise AppError(401, 2001, "缺少 API Key")
    key = await session.scalar(
        select(ApiKey).where(ApiKey.key_hash == api_key_digest(credentials.credentials))
    )
    now = datetime.now(UTC)
    if key is None or key.status != "active" or (key.expires_at and key.expires_at <= now):
        raise AppError(401, 2001, "API Key 无效或已过期")
    if "knowledge:read" not in key.scopes:
        raise AppError(403, 2002, "API Key 无检索权限")
    request.state.api_key_id = key.id
    await enforce_limit(
        request.app.state.redis,
        request.app.state.settings.redis_prefix,
        f"key:{key.id}",
        key.rate_limit_per_minute,
        key.rate_limit_per_day,
    )
    await session.execute(
        update(ApiKey).where(ApiKey.id == key.id).values(last_used_at=now, total_calls=ApiKey.total_calls + 1)
    )
    await session.commit()
    return key


KnowledgeKey = Annotated[ApiKey, Depends(knowledge_key)]


class RuntimeContext(NamedTuple):
    settings: Settings
    gateway: ModelGateway
    store: QdrantStore


async def runtime_context(request: Request, session: Session) -> RuntimeContext:
    """One request reads the active configuration once, so query model and collection always pair."""
    cached = getattr(request.state, "runtime", None)
    if cached is None:
        config = await active_settings(session, request.app.state.settings)
        cached = RuntimeContext(
            config,
            request.app.state.models.for_config(config),
            QdrantStore(request.app.state.qdrant, config),
        )
        request.state.runtime = cached
    return cached


Runtime = Annotated[RuntimeContext, Depends(runtime_context)]
