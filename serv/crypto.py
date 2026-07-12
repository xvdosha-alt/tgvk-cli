from __future__ import annotations

import base64
import hashlib
import logging
import os

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_PREFIX = "enc:"


def _secret() -> str:
    return os.environ.get("TGVK_SECRET_KEY", "").strip()


def _fernet() -> Fernet | None:
    secret = _secret()
    if not secret:
        return None
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def encryption_enabled() -> bool:
    return _fernet() is not None


def encrypt_text(value: str) -> str:
    if not value:
        return value
    f = _fernet()
    if f is None:
        return value
    token = f.encrypt(value.encode("utf-8")).decode("ascii")
    return f"{_PREFIX}{token}"


def decrypt_text(value: str) -> str:
    if not value:
        return value
    if value.startswith(_PREFIX):
        f = _fernet()
        if f is None:
            logger.warning("Зашифрованные данные без TGVK_SECRET_KEY")
            return ""
        try:
            return f.decrypt(value[len(_PREFIX) :].encode("ascii")).decode("utf-8")
        except InvalidToken:
            logger.warning("Не удалось расшифровать значение")
            return ""
    return value


def require_encryption_if_needed() -> None:
    from serv.settings import is_local_bind, is_production

    if (is_production() or not is_local_bind()) and not encryption_enabled():
        raise RuntimeError(
            "TGVK_SECRET_KEY обязателен при публичном доступе "
            "(TGVK_PRODUCTION=1 или TGVK_HOST != 127.0.0.1)"
        )


require_encryption_in_production = require_encryption_if_needed
