from pydantic import Field, SecretStr

from app.schemas.identity import StrictModel


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
    merchant_id: str | None = Field(default=None, max_length=200)
    # None 表示不改动已存的密钥；显式传空串才是清除。
    secret: SecretStr | None = None
    enabled: bool = False


class ChannelPatch(StrictModel):
    """code 是流水表 channel 引用的标识，建好后不可改，所以编辑体里没有它。"""

    display_name: str = Field(min_length=1, max_length=100)
    merchant_id: str | None = Field(default=None, max_length=200)
    secret: SecretStr | None = None
    enabled: bool = False
