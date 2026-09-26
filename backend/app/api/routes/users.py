import asyncio
from typing import Literal

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select, text

from app.api.deps import Session, SuperAdmin
from app.api.routes.auth import user_view
from app.core.errors import AppError, success
from app.core.security import hash_password
from app.models import Account, BalanceTransaction
from app.schemas.billing import RechargeInput
from app.schemas.identity import UserInput, UserPatch
from app.services import billing
from app.services.audit import record_audit

router = APIRouter(prefix="/v1/admin/users", tags=["用户管理"])

AccountRole = Literal["super_admin", "content_admin", "end_user"]


def account_view(row: Account, balance_cent: int) -> dict:
    return {**user_view(row), "created_at": row.created_at, "balance_cent": balance_cent}


@router.get("")
async def list_users(
    session: Session,
    user: SuperAdmin,
    q: str = Query("", max_length=100),
    role: AccountRole | None = None,
    status: Literal["active", "disabled"] | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    filters = []
    if q:
        filters.append(Account.username.icontains(q, autoescape=True))
    if role:
        filters.append(Account.role == role)
    if status:
        filters.append(Account.status == status)
    rows = await session.scalars(
        select(Account).where(*filters).order_by(Account.id).limit(limit).offset(offset)
    )
    total = await session.scalar(select(func.count()).select_from(Account).where(*filters))
    balances = await billing.balances_by_account(session)
    return success({"items": [account_view(row, balances.get(row.id, 0)) for row in rows], "total": total})


@router.post("")
async def create_user(body: UserInput, request: Request, session: Session, user: SuperAdmin):
    row = Account(
        username=body.username,
        password_hash=await asyncio.to_thread(hash_password, body.password.get_secret_value()),
        role=body.role,
    )
    session.add(row)
    await session.flush()
    record_audit(
        session, request, user, "create", "account", row.id, {"username": row.username, "role": row.role}
    )
    await session.commit()
    return success(user_view(row))


@router.patch("/{user_id}")
async def patch_user(user_id: int, body: UserPatch, request: Request, session: Session, user: SuperAdmin):
    await session.execute(text("SELECT pg_advisory_xact_lock(734601)"))
    row = await session.get(Account, user_id, with_for_update=True)
    if row is None:
        raise AppError(404, 1001, "账号不存在")
    changes = body.model_dump(exclude_unset=True)
    if any(value is None for value in changes.values()):
        raise AppError(400, 1001, "账号字段不能设为空")
    if user_id == user.id and (
        changes.get("status") == "disabled" or changes.get("role") not in {None, "super_admin"}
    ):
        raise AppError(400, 1001, "不能停用或降级当前登录账号")
    for field in ("role", "status"):
        if field in changes:
            setattr(row, field, changes[field])
    if body.password:
        row.password_hash = await asyncio.to_thread(hash_password, body.password.get_secret_value())
    record_audit(session, request, user, "update", "account", row.id, {"fields": list(changes)})
    await session.commit()
    return success(user_view(row))


@router.post("/{user_id}/recharge")
async def recharge(user_id: int, body: RechargeInput, request: Request, session: Session, user: SuperAdmin):
    row = await session.get(Account, user_id)
    if row is None:
        raise AppError(404, 1001, "账号不存在")
    # 余额由流水汇总得出，充值只是追加一行，没有读改写，所以不需要锁账号行。
    entry = BalanceTransaction(
        account_id=row.id, amount_cent=body.amount_cent, channel="manual", operator_id=user.id, note=body.note
    )
    session.add(entry)
    await session.flush()
    record_audit(
        session,
        request,
        user,
        "recharge",
        "balance_transaction",
        entry.id,
        {"account_id": row.id, "amount_cent": body.amount_cent},
    )
    await session.commit()
    return success({"account_id": row.id, "balance_cent": await billing.balance_cent(session, row.id)})


@router.get("/{user_id}/transactions")
async def user_transactions(user_id: int, session: Session, user: SuperAdmin):
    if await session.get(Account, user_id) is None:
        raise AppError(404, 1001, "账号不存在")
    rows = await session.scalars(
        select(BalanceTransaction)
        .where(BalanceTransaction.account_id == user_id)
        .order_by(BalanceTransaction.id.desc())
        .limit(100)
    )
    return success({"items": [billing.transaction_view(row) for row in rows]})
