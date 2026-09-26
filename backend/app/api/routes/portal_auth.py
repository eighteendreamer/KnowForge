import asyncio

from fastapi import APIRouter, Request

from app.api.deps import PortalAccount, Session
from app.api.routes.auth import user_view
from app.core.errors import AppError, success
from app.core.rate_limit import enforce_limit
from app.core.security import PORTAL_AUDIENCE, create_access_token, hash_password, verify_password
from app.models import Account
from app.schemas.identity import LoginInput, PasswordChangeInput, RegisterInput
from app.services.audit import record_audit
from app.services.authentication import authenticate

router = APIRouter(prefix="/v1/portal/auth", tags=["门户账号"])


@router.post("/register")
async def register(body: RegisterInput, request: Request, session: Session):
    client_ip = request.client.host if request.client else "unknown"
    await enforce_limit(
        request.app.state.redis, request.app.state.settings.redis_prefix, f"register:{client_ip}", 5, 50
    )
    row = Account(
        username=body.username,
        password_hash=await asyncio.to_thread(hash_password, body.password.get_secret_value()),
        role="end_user",
    )
    session.add(row)
    await session.flush()
    record_audit(session, request, row, "register", "account", row.id)
    await session.commit()
    return success(
        {
            "access_token": create_access_token(row.id, request.app.state.settings, PORTAL_AUDIENCE),
            "token_type": "bearer",
            "user": user_view(row),
        }
    )


@router.post("/login")
async def login(body: LoginInput, request: Request, session: Session):
    user = await authenticate(request, session, body, PORTAL_AUDIENCE)
    return success(
        {
            "access_token": create_access_token(user.id, request.app.state.settings, PORTAL_AUDIENCE),
            "token_type": "bearer",
            "user": user_view(user),
        }
    )


@router.get("/me")
async def me(user: PortalAccount):
    return success(user_view(user))


@router.put("/password")
async def change_password(body: PasswordChangeInput, request: Request, session: Session, user: PortalAccount):
    valid = await asyncio.to_thread(
        verify_password, body.current_password.get_secret_value(), user.password_hash
    )
    if not valid:
        raise AppError(401, 2001, "当前密码不正确")
    user.password_hash = await asyncio.to_thread(hash_password, body.new_password.get_secret_value())
    record_audit(session, request, user, "update", "account", user.id, {"fields": ["password"]})
    await session.commit()
    return success(user_view(user))
