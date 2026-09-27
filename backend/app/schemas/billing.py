from datetime import datetime
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator

from app.schemas.identity import StrictModel

ChannelType = Literal["alipay", "wechat", "stripe", "custom"]
PromoKind = Literal["amount_off", "percent"]


class PackageInput(StrictModel):
    label: str = Field(min_length=1, max_length=100)
    amount_cent: int = Field(gt=0, le=100_000_000)
    bonus_cent: int = Field(default=0, ge=0, le=100_000_000)
    enabled: bool = True
    sort_order: int = Field(default=0, ge=0, le=9999)


class OrderInput(StrictModel):
    """下单只收"选哪个渠道、买哪个档位、用哪个促销码"：金额一律服务端算，客户端报的数字一概不信。"""

    channel_code: str = Field(min_length=1, max_length=30, pattern=r"^[a-z0-9_-]+$")
    package_id: int = Field(gt=0)
    promo_code: str = Field(default="", max_length=32)


class PromoQuoteInput(StrictModel):
    """结算页的试算：只要档位和码，账号从令牌来，避免"替别人试掉一个限用一次的码"。"""

    package_id: int = Field(gt=0)
    promo_code: str = Field(default="", max_length=32)


class PromoInput(StrictModel):
    code: str = Field(min_length=2, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    label: str = Field(default="", max_length=100)
    kind: PromoKind = "amount_off"
    # 同一个 value 列两种单位：amount_off 是分（可以上千），percent 是百分比（1-90）。
    # 上限只能按 kind 判，写在 Field 上会把立减金额一起卡死。
    value: int = Field(gt=0, le=100_000_000)
    min_amount_cent: int = Field(default=0, ge=0, le=100_000_000)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    max_uses: int | None = Field(default=None, ge=1)
    per_account_limit: int | None = Field(default=None, ge=1)
    enabled: bool = True

    @field_validator("code")
    @classmethod
    def upper_code(cls, value: str) -> str:
        return value.strip().upper()

    @model_validator(mode="after")
    def value_matches_kind(self) -> "PromoInput":
        if self.kind == "percent" and self.value > 90:
            raise ValueError("折扣百分比不能超过 90%")
        if self.ends_at and self.starts_at and self.ends_at <= self.starts_at:
            raise ValueError("结束时间必须晚于生效时间")
        return self


class PromoPatch(StrictModel):
    """促销码不含密钥，字段缺省即不改；kind 与 value 要一起改才不会出现"立减 5000 分被当成 5000%"。"""

    label: str | None = Field(default=None, max_length=100)
    kind: PromoKind | None = None
    value: int | None = Field(default=None, gt=0, le=100_000_000)
    min_amount_cent: int | None = Field(default=None, ge=0, le=100_000_000)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    max_uses: int | None = Field(default=None, ge=1)
    per_account_limit: int | None = Field(default=None, ge=1)
    enabled: bool | None = None


class RechargeInput(StrictModel):
    amount_cent: int = Field(gt=0, le=100_000_000)
    note: str = Field(min_length=1, max_length=200)


class ChannelInput(StrictModel):
    code: str = Field(min_length=1, max_length=30, pattern=r"^[a-z0-9_-]+$")
    display_name: str = Field(min_length=1, max_length=100)
    channel_type: ChannelType = "custom"
    enabled: bool = False
    # 键名来自 GET /recharge/credential-specs；SecretStr 只为了让值不进 repr 与校验错误回显。
    credentials: dict[str, SecretStr | None] = Field(default_factory=dict)


class ChannelPatch(StrictModel):
    """code 与 channel_type 建好后不可改：流水表按 code 字符串引用渠道，换类型等于换语义。

    credentials 是三态契约——键不出现表示保留，出现且有值表示轮换（版本 +1），出现但为 null 表示清除。
    旧版把 secret 总是带在 body 里，改个显示名就会把密钥静默清空，这里从契约上堵掉。
    """

    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool | None = None
    credentials: dict[str, SecretStr | None] | None = None
