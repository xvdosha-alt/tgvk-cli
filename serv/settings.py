from __future__ import annotations

import os
import stat
from pathlib import Path

SERV_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("TGVK_DATA_DIR", SERV_DIR / "data"))
WEB_DB_PATH = DATA_DIR / "web.db"
USERS_DIR = DATA_DIR / "users"
COOKIE_NAME = "tgvk_sid"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30
SESSION_IDLE_TIMEOUT = int(os.environ.get("TGVK_SESSION_IDLE_SEC", str(60 * 30)))
DEFAULT_PUBLIC_HOST = "tg.vk.cli.dosha.pw"
DEFAULT_PUBLIC_URL = f"https://{DEFAULT_PUBLIC_HOST}"
LOCAL_PANEL_URL = "http://127.0.0.1:8080"


def is_local_bind() -> bool:
    host = default_bind_host().strip().lower()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host in ("127.0.0.1", "localhost", "::1")


def is_production() -> bool:
    return os.environ.get("TGVK_PRODUCTION", "").lower() in ("1", "true", "yes")


def cookie_secure() -> bool:
    env = os.environ.get("TGVK_SECURE_COOKIES", "").strip().lower()
    if env in ("0", "false", "no"):
        return False
    if env in ("1", "true", "yes"):
        return True
    return is_production()


def default_bind_host() -> str:
    return os.environ.get("TGVK_HOST", "127.0.0.1")


def trusted_hosts() -> list[str]:
    raw = os.environ.get(
        "TGVK_TRUSTED_HOSTS",
        f"localhost,127.0.0.1,{DEFAULT_PUBLIC_HOST}",
    )
    return [h.strip() for h in raw.split(",") if h.strip()]


def trusted_proxy_ips() -> set[str]:
    raw = os.environ.get("TGVK_TRUSTED_PROXIES", "127.0.0.1,::1")
    return {ip.strip() for ip in raw.split(",") if ip.strip()}


def default_vk_token() -> str:
    return os.environ.get("TGVK_DEFAULT_VK_TOKEN", "").strip()


def public_panel_url() -> str:
    env = os.environ.get("TGVK_PUBLIC_URL", "").strip().rstrip("/")
    if env:
        return env
    if is_production():
        return DEFAULT_PUBLIC_URL
    return LOCAL_PANEL_URL


def user_messages_db(user_id: str) -> Path:
    return USERS_DIR / user_id / "messages.db"


def harden_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        DATA_DIR.chmod(stat.S_IRWXU)
    except OSError:
        pass
