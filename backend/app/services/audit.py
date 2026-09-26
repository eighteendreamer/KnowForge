from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, AuditLog


def record_audit(
    session: AsyncSession,
    request: Request,
    user: Account,
    action: str,
    target_type: str,
    target_id: int | str,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            operator_id=user.id,
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            client_ip=request.client.host if request.client else None,
            details=details or {},
        )
    )
