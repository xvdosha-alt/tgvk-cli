from __future__ import annotations


def normalize_username(value: str) -> str:
    return value.strip().lstrip("@").lower()


def parse_target_arg(arg: str) -> tuple[str, int | str]:
    raw = arg.strip()
    if raw.lstrip("-").isdigit():
        return "id", int(raw)
    return "username", normalize_username(raw)


def join_reply_text(args: list[str]) -> str | None:
    if len(args) < 2:
        return None
    text = " ".join(args[1:]).strip()
    return text or None
