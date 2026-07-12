from __future__ import annotations

import logging
import os

import uvicorn

from serv.settings import default_bind_host, default_vk_token, is_production

logger = logging.getLogger(__name__)


def main() -> None:
    host = default_bind_host()
    port = int(os.environ.get("TGVK_PORT", "8080"))
    verbose = os.environ.get("TGVK_VERBOSE", "").lower() in ("1", "true", "yes")

    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if host == "0.0.0.0":
        logger.warning("Слушаем 0.0.0.0 — используй firewall/VPN или TGVK_HOST=127.0.0.1")

    if is_production() and not os.environ.get("TGVK_SECRET_KEY", "").strip():
        raise SystemExit("TGVK_SECRET_KEY обязателен при TGVK_PRODUCTION=1")

    if not default_vk_token():
        print("TGVK_DEFAULT_VK_TOKEN не задан — пользователям нужен свой VK токен")

    print(f"tgvk: http://{host}:{port}")
    uvicorn.run(
        "serv.app:app",
        host=host,
        port=port,
        log_level="debug" if verbose else "info",
        workers=1,
    )


if __name__ == "__main__":
    main()
