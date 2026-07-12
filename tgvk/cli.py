from __future__ import annotations

import asyncio
import logging
import sys

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from tgvk.bridge import BridgeService
from tgvk.config import AppConfig, CONFIG_FILE

app = typer.Typer(
    name="tgvk",
    help="Telegram → VK мост: дублирует входящие сообщения в VK бота.",
    no_args_is_help=True,
)
console = Console()


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


@app.command("init")
def init_cmd() -> None:
    console.print(Panel.fit("Настройка tgvk", style="bold cyan"))

    cfg = AppConfig.load()

    console.print(
        "\n[dim]Telegram API: получи api_id/api_hash на https://my.telegram.org/apps[/dim]\n"
        "[dim]Или задай TGVK_TELEGRAM_API_ID и TGVK_TELEGRAM_API_HASH в окружении[/dim]\n"
        "[dim]Session string: scripts/export_session.py или вставь готовый[/dim]\n"
        "[dim]VK token: токен сообщества с правами messages[/dim]\n"
        "[dim]VK peer_id: твой user id (например 123456789)[/dim]\n"
    )

    if not cfg.telegram_api_id:
        raw_id = Prompt.ask("Telegram api_id", default=None)
        if raw_id:
            cfg.telegram_api_id = int(raw_id)
    if not cfg.telegram_api_hash:
        api_hash = Prompt.ask("Telegram api_hash", default=None, password=True)
        if api_hash:
            cfg.telegram_api_hash = api_hash

    session = Prompt.ask(
        "Telegram session string",
        default=cfg.telegram_session or None,
        password=True,
    )
    vk_token = Prompt.ask("VK bot token", default=cfg.vk_token or None, password=True)
    peer = Prompt.ask("VK peer_id (куда слать)", default=str(cfg.vk_peer_id) or None)
    img_mode = Confirm.ask("Включить img mode сразу?", default=cfg.img_mode)

    cfg.telegram_session = session
    cfg.vk_token = vk_token
    cfg.vk_peer_id = int(peer)
    cfg.img_mode = img_mode
    cfg.save()

    console.print(f"\n[green]✓[/green] Конфиг сохранён: {CONFIG_FILE}")
    console.print("Запуск: [bold]tgvk run[/bold]")


@app.command("config")
def config_cmd(
    action: str = typer.Argument(..., help="show | set"),
    key: str = typer.Argument(None, help="Имя параметра"),
    value: str = typer.Argument(None, help="Значение"),
) -> None:
    cfg = AppConfig.load()

    if action == "show":
        table = Table(title="tgvk config")
        table.add_column("Ключ", style="cyan")
        table.add_column("Значение")
        for field, info in cfg.model_fields.items():
            val = getattr(cfg, field)
            if field in ("telegram_session", "vk_token", "telegram_api_hash") and val:
                display = val[:8] + "…" + val[-4:] if len(str(val)) > 16 else "***"
            else:
                display = str(val)
            table.add_row(field, display)
        console.print(table)
        console.print(f"\n[dim]{CONFIG_FILE}[/dim]")
        return

    if action == "set":
        if not key or value is None:
            console.print("[red]Использование: tgvk config set <key> <value>[/red]")
            raise typer.Exit(1)
        int_fields = {"telegram_api_id", "vk_peer_id", "history_limit"}
        bool_fields = {"img_mode"}
        if key in int_fields:
            parsed: str | int | bool = int(value)
        elif key in bool_fields:
            parsed = value.lower() in ("1", "true", "yes", "on", "да")
        else:
            parsed = value
        try:
            cfg.set_field(key, parsed)
            console.print(f"[green]✓[/green] {key} = {parsed}")
        except KeyError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
        return

    console.print("[red]Действие: show или set[/red]")
    raise typer.Exit(1)


@app.command("run")
def run_cmd(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Подробные логи"),
) -> None:
    _setup_logging(verbose)
    cfg = AppConfig.load()
    ready, missing = cfg.is_ready()
    if not ready:
        console.print(f"[red]Не хватает настроек: {', '.join(missing)}[/red]")
        console.print("Запусти: [bold]tgvk init[/bold]")
        raise typer.Exit(1)

    console.print(Panel.fit("tgvk запускается…", style="bold green"))
    service = BridgeService(cfg)

    try:
        asyncio.run(service.start())
    except KeyboardInterrupt:
        console.print("\n[yellow]Остановлено[/yellow]")
    except Exception as exc:
        console.print(f"[red]Ошибка: {exc}[/red]")
        raise typer.Exit(1) from exc


@app.command("history")
def history_cmd(
    limit: int = typer.Option(20, "--limit", "-n"),
    chat_id: int | None = typer.Option(None, "--chat", "-c"),
) -> None:
    from tgvk.formatting import format_history_list
    from tgvk.storage import MessageStore

    async def _run() -> None:
        store = MessageStore()
        await store.init()
        messages = await store.get_recent(limit=limit, chat_id=chat_id)
        console.print(format_history_list(messages))

    asyncio.run(_run())


@app.command("version")
def version_cmd() -> None:
    from tgvk import __version__

    console.print(f"tgvk {__version__}")


@app.command("web")
def web_cmd(
    host: str = typer.Option("127.0.0.1", "--host", "-H"),
    port: int = typer.Option(8080, "--port", "-p"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    _setup_logging(verbose)
    import uvicorn

    from serv.settings import default_vk_token

    token = default_vk_token()
    if token:
        console.print("[dim]VK: общий токен из TGVK_DEFAULT_VK_TOKEN[/dim]")
    else:
        console.print(
            "[yellow]TGVK_DEFAULT_VK_TOKEN не задан — пользователям нужен свой VK токен[/yellow]"
        )
    console.print(Panel.fit(f"Веб-панель: http://{host}:{port}", style="bold green"))
    uvicorn.run("serv.app:app", host=host, port=port, log_level="debug" if verbose else "info", workers=1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
