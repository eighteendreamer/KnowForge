"""促销码报价：折扣口径只有一份，服务端算出来的数才是数。

门户在用户点"应用"时调 quote 展示，下单时服务端再算一遍并把结果冻结进订单快照，
所以前端改价、重放请求、活动规则后来变动都不会让一张已下出的订单改金额。
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PaymentOrder, RechargePackage, RechargePromoCode


@dataclass(frozen=True)
class Quote:
    """一次报价结论。applied 为假时 discount_cent 一定是 0，可以照原价下单。

    code_id 带回来是为了让下单不再查第二次表，也免得"报价时有效、下单时又解析一遍"多出一种漂移。
    """

    applied: bool
    amount_cent: int
    bonus_cent: int
    discount_cent: int
    label: str = ""
    reason: str = ""
    code_id: int | None = None

    @property
    def payable_cent(self) -> int:
        return self.amount_cent - self.discount_cent

    @property
    def credited_cent(self) -> int:
        """到账金额与折扣无关：活动让掉的是收单的钱，不是给用户的额度。"""
        return self.amount_cent + self.bonus_cent

    def view(self) -> dict[str, object]:
        return {
            "applied": self.applied,
            "label": self.label,
            "amount_cent": self.amount_cent,
            "bonus_cent": self.bonus_cent,
            "discount_cent": self.discount_cent,
            "payable_cent": self.payable_cent,
            "credited_cent": self.credited_cent,
            "reason": self.reason,
        }


def normalize(code: str) -> str:
    return code.strip().upper()


def _yuan(amount_cent: int) -> str:
    return f"{amount_cent // 100}.{amount_cent % 100:02d}元"


def _discount_of(rule: RechargePromoCode, amount_cent: int) -> int:
    if rule.kind == "percent":
        return amount_cent * rule.value // 100
    return rule.value


async def quote(
    session: AsyncSession,
    package: RechargePackage,
    code: str,
    account_id: int,
    *,
    now: datetime,
) -> Quote:
    """算一次折扣。任何不合格都返回带原因的不适用报价，而不是抛错——输入框旁边就是要显示它。"""
    amount, bonus = package.amount_cent, package.bonus_cent
    key = normalize(code)
    if not key:
        return Quote(False, amount, bonus, 0)
    rule = await session.scalar(select(RechargePromoCode).where(RechargePromoCode.code == key))
    if rule is None:
        return Quote(False, amount, bonus, 0, reason="促销码不存在")

    def rejected(reason: str) -> Quote:
        return Quote(False, amount, bonus, 0, reason=reason)

    if not rule.enabled:
        return rejected("促销码已停用")
    if rule.starts_at and rule.starts_at > now:
        return rejected("促销码尚未生效")
    if rule.ends_at and rule.ends_at <= now:
        return rejected("促销码已过期")
    if amount < rule.min_amount_cent:
        return rejected(f"该码需单笔满 {_yuan(rule.min_amount_cent)}")
    if rule.max_uses is not None and rule.used_count >= rule.max_uses:
        return rejected("促销码名额已用完")
    if rule.per_account_limit is not None:
        used = await session.scalar(
            select(func.count())
            .select_from(PaymentOrder)
            .where(
                PaymentOrder.account_id == account_id,
                PaymentOrder.promo_code_id == rule.id,
                PaymentOrder.status == "paid",
            )
        )
        if (used or 0) >= rule.per_account_limit:
            return rejected("你已经用过这个促销码")
    discount = _discount_of(rule, amount)
    if discount >= amount:
        # 抵扣到 0 元的单在厂商侧下不出去，也失去"钱确实经过厂商"这层证明，宁可拒掉。
        return rejected("本单金额不足以使用该促销码")
    return Quote(True, amount, bonus, discount, label=rule.label or rule.code, code_id=rule.id)
