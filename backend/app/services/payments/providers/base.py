from dataclasses import dataclass
from typing import Any

CHECK_TIMEOUT_SECONDS = 12


class ProviderError(Exception):
    """厂商侧的失败原因，直接进自检结果，不把它伪装成"配置无效"。"""


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class SelfCheck:
    """mode 只允许 live / local_only：live 表示真打过厂商，local_only 表示只证明了本地自洽。

    stores 装自检过程中顺手取回、应当回存成凭据的内容（微信用它缓存平台证书）。
    """

    mode: str
    checks: tuple[Check, ...]
    stores: dict[str, str] | None = None

    def view(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "checks": [{"name": item.name, "ok": item.ok, "detail": item.detail} for item in self.checks],
        }

    @property
    def passed(self) -> bool:
        return all(item.ok for item in self.checks)


def require(credentials: dict[str, str], *keys: str) -> None:
    missing = [key for key in keys if not credentials.get(key)]
    if missing:
        raise ProviderError("缺少配置项：" + "、".join(missing))
