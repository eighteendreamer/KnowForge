from fastapi import APIRouter, Request

from app.api.deps import Admin, Session
from app.core.errors import success
from app.core.security import ADMIN_AUDIENCE, create_access_token
from app.models import Account
from app.schemas.identity import LoginInput
from app.services.authentication import authenticate

router = APIRouter(prefix="/v1/admin/auth", tags=["管理登录"])


def user_view(user: Account) -> dict:
    return {"id": user.id, "username": user.username, "role": user.role, "status": user.status}


@router.post("/login")
async def login(body: LoginInput, request: Request, session: Session):
    user = await authenticate(request, session, body, ADMIN_AUDIENCE)
    return success(
        {
            "access_token": create_access_token(user.id, request.app.state.settings, ADMIN_AUDIENCE),
            "token_type": "bearer",
            "user": user_view(user),
        }
    )


@router.get("/me")
async def me(user: Admin):
    return success(user_view(user))
