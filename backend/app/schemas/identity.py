from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LoginInput(StrictModel):
    username: str = Field(min_length=1, max_length=100)
    password: SecretStr = Field(min_length=1, max_length=256)


class UserInput(StrictModel):
    username: str = Field(min_length=2, max_length=100, pattern=r"^[\w.@-]+$")
    password: SecretStr = Field(min_length=12, max_length=256)
    role: Literal["super_admin", "content_admin", "end_user"] = "content_admin"


class UserPatch(StrictModel):
    role: Literal["super_admin", "content_admin", "end_user"] | None = None
    status: Literal["active", "disabled"] | None = None
    password: SecretStr | None = Field(default=None, min_length=12, max_length=256)


class RegisterInput(StrictModel):
    username: str = Field(min_length=2, max_length=100, pattern=r"^[\w.@-]+$")
    password: SecretStr = Field(min_length=12, max_length=256)


class PasswordChangeInput(StrictModel):
    current_password: SecretStr = Field(min_length=1, max_length=256)
    new_password: SecretStr = Field(min_length=12, max_length=256)


class ApiKeyInput(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    rate_limit_per_day: int = Field(default=1000, ge=1, le=10_000_000)
    rate_limit_per_minute: int = Field(default=60, ge=1, le=100_000)
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def timezone_required(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("expires_at must include timezone")
        return value


class ApiKeyPatch(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    rate_limit_per_day: int | None = Field(default=None, ge=1, le=10_000_000)
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=100_000)
    expires_at: datetime | None = None
    status: Literal["active", "disabled"] | None = None

    @field_validator("expires_at")
    @classmethod
    def timezone_required(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("expires_at must include timezone")
        return value


class PortalKeyInput(StrictModel):
    """门户建钥只允许命名与有效期；调用限额由列默认值决定，扩容是管理端动作。"""

    name: str = Field(min_length=1, max_length=100)
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def timezone_required(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("expires_at must include timezone")
        return value


class PortalKeyPatch(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    expires_at: datetime | None = None
    status: Literal["active", "disabled"] | None = None

    @field_validator("expires_at")
    @classmethod
    def timezone_required(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("expires_at must include timezone")
        return value
