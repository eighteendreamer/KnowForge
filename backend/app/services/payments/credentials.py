import asyncio
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RechargeChannelCredential
from app.services.payments import crypto, specs


async def rows_for(session: AsyncSession, channel_id: int) -> list[RechargeChannelCredential]:
    rows = await session.scalars(
        select(RechargeChannelCredential)
        .where(RechargeChannelCredential.channel_id == channel_id)
        .order_by(RechargeChannelCredential.key_name)
    )
    return list(rows)


def _decrypt_many(
    master: bytes, channel_id: int, rows: Iterable[RechargeChannelCredential]
) -> dict[str, str]:
    return {
        row.key_name: crypto.open_secret(
            master, channel_id, row.key_name, row.key_version, row.ciphertext, row.label
        )
        for row in rows
    }


async def load_values(
    session: AsyncSession, master: bytes, channel_id: int, *, include_secrets: bool = False
) -> dict[str, str]:
    """读出渠道凭据。默认连密钥类都不解密——列表面板只需要指纹，解密它们没有任何用处。"""
    rows = await rows_for(session, channel_id)
    picked = rows if include_secrets else [row for row in rows if not row.secret_class]
    return await asyncio.to_thread(_decrypt_many, master, channel_id, picked)


def _decrypt_all(
    master: bytes, grouped: Mapping[int, Iterable[RechargeChannelCredential]]
) -> dict[int, dict[str, str]]:
    return {
        channel_id: _decrypt_many(master, channel_id, [row for row in rows if not row.secret_class])
        for channel_id, rows in grouped.items()
    }


async def load_all(session: AsyncSession, master: bytes) -> dict[int, list[dict[str, Any]]]:
    """一次取回所有渠道的凭据视图。渠道列表只有几行，但也不该按行发查询。"""
    rows = await session.scalars(
        select(RechargeChannelCredential).order_by(
            RechargeChannelCredential.channel_id, RechargeChannelCredential.key_name
        )
    )
    grouped: dict[int, list[RechargeChannelCredential]] = {}
    for row in rows:
        grouped.setdefault(row.channel_id, []).append(row)
    values = await asyncio.to_thread(_decrypt_all, master, grouped)
    return {
        channel_id: credential_views(channel_rows, values.get(channel_id, {}))
        for channel_id, channel_rows in grouped.items()
    }


async def apply(
    session: AsyncSession,
    master: bytes,
    channel_id: int,
    channel_type: str,
    values: Mapping[str, str | None],
    operator_id: int,
) -> dict[str, list[str]]:
    """按三态语义写入：值缺省=不动，有值=轮换（版本 +1），None/空串=删除。

    值没变就不写、也不涨版本号，否则每次保存都会让"上次什么时候换的密钥"这个信息失真。
    """
    existing = {row.key_name: row for row in await rows_for(session, channel_id)}
    rotated: list[str] = []
    cleared: list[str] = []
    for key, value in values.items():
        row = existing.get(key)
        if value is None or not value.strip():
            if row is not None:
                await session.delete(row)
                cleared.append(key)
            continue
        label, secret_class = specs.describe(channel_type, key)
        if row is not None and row.fingerprint == crypto.fingerprint(value):
            continue
        version = 1 if row is None else row.key_version + 1
        blob = await asyncio.to_thread(crypto.seal, master, channel_id, key, version, value)
        if row is None:
            session.add(
                RechargeChannelCredential(
                    channel_id=channel_id,
                    key_name=key,
                    label=label,
                    secret_class=secret_class,
                    ciphertext=blob,
                    key_version=version,
                    fingerprint=crypto.fingerprint(value),
                    set_by_id=operator_id,
                )
            )
        else:
            row.ciphertext = blob
            row.key_version = version
            row.label = label
            row.secret_class = secret_class
            row.set_by_id = operator_id
        rotated.append(key)
    return {"rotated": rotated, "cleared": cleared}


def state(channel_type: str, keys: Iterable[str]) -> dict[str, Any]:
    """配置完整性判定。缺哪几项直接给中文名，管理端徽章和下单前的闸门共用这一份结论。"""
    present = set(keys)
    missing = specs.missing_required(channel_type, present)
    problem = specs.configuration_error(channel_type, present)
    return {
        "payable": channel_type in specs.PAYABLE_TYPES,
        "complete": not missing and problem is None,
        "missing": missing,
        "problem": problem,
    }


def credential_views(
    rows: Iterable[RechargeChannelCredential], values: Mapping[str, str]
) -> list[dict[str, Any]]:
    return [
        {
            "key": row.key_name,
            "label": row.label,
            "secret": row.secret_class,
            "configured": True,
            "fingerprint": row.fingerprint,
            "key_version": row.key_version,
            "set_at": row.updated_at,
            # 非密钥项（商户号、AppID、回调地址）要能看见原文，否则运营无法确认填的是哪个环境。
            "value": None if row.secret_class else values.get(row.key_name),
        }
        for row in rows
    ]
