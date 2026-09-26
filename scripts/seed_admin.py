import asyncio
import secrets
from pathlib import Path

from sqlalchemy import select

from app.core.config import Settings
from app.core.database import make_engine, make_session_factory
from app.core.security import hash_password
from app.models import Account


def save_credentials(path: Path, password: str) -> None:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(f"username=admin\npassword={password}\n")


async def main() -> None:
    settings = Settings()
    engine = make_engine(settings)
    credentials = Path("data/admin-credentials.txt")
    try:
        async with make_session_factory(engine)() as session:
            if await session.scalar(select(Account.id).limit(1)):
                raise SystemExit("Admin accounts already exist; refusing to reset credentials")
            password = secrets.token_urlsafe(24)
            session.add(Account(username="admin", password_hash=hash_password(password), role="super_admin"))
            await asyncio.to_thread(save_credentials, credentials, password)
            await session.commit()
            print(f"Admin created. Credentials saved locally in {credentials}; not printed.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
