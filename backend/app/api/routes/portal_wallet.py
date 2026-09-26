from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import PortalAccount, Session
from app.core.errors import success
from app.models import BalanceTransaction, RechargeChannel, RechargePackage
from app.services import billing

router = APIRouter(prefix="/v1/portal", tags=["门户余额与充值"])


@router.get("/wallet")
async def wallet(session: Session, user: PortalAccount):
    transactions = await session.scalars(
        select(BalanceTransaction)
        .where(BalanceTransaction.account_id == user.id)
        .order_by(BalanceTransaction.id.desc())
        .limit(100)
    )
    packages = await session.scalars(
        select(RechargePackage)
        .where(RechargePackage.enabled.is_(True))
        .order_by(RechargePackage.sort_order, RechargePackage.id)
    )
    channels = await session.scalars(
        select(RechargeChannel).where(RechargeChannel.enabled.is_(True)).order_by(RechargeChannel.id)
    )
    return success(
        {
            "balance_cent": await billing.balance_cent(session, user.id),
            "transactions": [billing.transaction_view(row) for row in transactions],
            # 在线支付未接入时这两组就是空数组，门户据此显示“支付通道未开通”。
            "packages": [
                {"label": row.label, "amount_cent": row.amount_cent, "bonus_cent": row.bonus_cent}
                for row in packages
            ],
            "channels": [{"code": row.code, "display_name": row.display_name} for row in channels],
        }
    )
