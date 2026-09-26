import secrets

from dotenv import set_key

from app.core.config import ROOT, Settings


def main() -> None:
    directory = ROOT / "data/monitoring"
    directory.mkdir(parents=True, exist_ok=True)
    token = Settings().metrics_token.get_secret_value()
    if not token:
        token = secrets.token_urlsafe(48)
        set_key(ROOT / ".env", "KNOFORGE_METRICS_TOKEN", token)
    (directory / "prometheus-token.txt").write_text(token, encoding="utf-8")
    password = directory / "grafana-password.txt"
    if not password.exists():
        password.write_text(secrets.token_urlsafe(32), encoding="utf-8")
    print("Monitoring credentials are ready in data/monitoring; values are not printed.")


if __name__ == "__main__":
    main()
