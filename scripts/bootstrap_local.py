import json
import os
import secrets
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def psql(database: str, sql: str) -> str:
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            "-u",
            "postgres",
            "bigdata-major",
            "psql",
            "-X",
            "-At",
            "-v",
            "ON_ERROR_STOP=1",
            "-d",
            database,
        ],
        input=sql,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            "PostgreSQL bootstrap failed; inspect container permissions and database ownership"
        )
    return result.stdout.strip()


def main() -> None:
    env_path = ROOT / ".env"
    if env_path.exists():
        raise SystemExit(".env exists; refusing to overwrite local credentials")
    existing = psql(
        "postgres", "SELECT datname FROM pg_database WHERE datname IN ('knowforge', 'knowforge_test');"
    )
    if existing:
        raise SystemExit("Project databases already exist; verify ownership before bootstrap")
    if psql("postgres", "SELECT rolname FROM pg_roles WHERE rolname = 'knowforge_app';"):
        raise SystemExit("knowforge_app already exists; refusing to change its credentials")
    password = secrets.token_urlsafe(32)
    psql("postgres", f"CREATE ROLE knowforge_app LOGIN PASSWORD '{password}';")
    for database in ("knowforge", "knowforge_test"):
        psql("postgres", f"CREATE DATABASE {database} OWNER knowforge_app;")
        psql(database, "CREATE EXTENSION IF NOT EXISTS pg_trgm;")
    values = {
        "KNOFORGE_DATABASE_URL": f"postgresql+asyncpg://knowforge_app:{password}@127.0.0.1:5432/knowforge",
        "KNOFORGE_TEST_DATABASE_URL": f"postgresql+asyncpg://knowforge_app:{password}@127.0.0.1:5432/knowforge_test",
        "KNOFORGE_REDIS_URL": "redis://127.0.0.1:6379/0",
        "KNOFORGE_CELERY_BROKER_URL": "redis://127.0.0.1:6379/0",
        "KNOFORGE_JWT_SECRET": secrets.token_urlsafe(48),
        "KNOFORGE_MODEL_API_BASE_URL": "",
        "KNOFORGE_MODEL_API_KEY": "",
        "KNOFORGE_QDRANT_URL": "http://127.0.0.1:6333",
    }
    with env_path.open("x", encoding="utf-8") as stream:
        for key, value in values.items():
            stream.write(f"{key}={value}\n")
    os.chmod(env_path, 0o600)
    print(
        json.dumps(
            {
                "created_databases": ["knowforge", "knowforge_test"],
                "extension": "pg_trgm",
                "config": ".env",
                "credentials": "generated; not printed",
            }
        )
    )


if __name__ == "__main__":
    main()
