from typing import Literal

from pydantic import Field, SecretStr

from app.schemas.identity import StrictModel

ChannelType = Literal["alipay", "wechat", "stripe", "custom"]


class PackageInput(StrictModel):
    label: str = Field(min_length=1, max_length=100)
    amount_cent: int = Field(gt=0, le=100_000_000)
    bonus_cent: int = Field(default=0, ge=0, le=100_000_000)
    enabled: bool = True
    sort_order: int = Field(default=0, ge=0, le=9999)


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
