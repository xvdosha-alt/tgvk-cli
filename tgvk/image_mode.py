from __future__ import annotations

import textwrap
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

BG = (18, 18, 20)
HEADER_BG = (30, 30, 34)
BUBBLE_IN = (38, 38, 42)
BUBBLE_OUT = (45, 85, 65)
TEXT = (235, 235, 235)
MUTED = (160, 160, 168)
ACCENT = (100, 180, 255)

WIDTH = 720
PADDING = 24
LINE_HEIGHT = 22
FONT_SIZE = 16
HEADER_SIZE = 18


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "C:\\Windows\\Fonts\\arial.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _wrap_lines(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    if not text:
        return ["(пусто)"]
    words = text.replace("\r", "").split("\n")
    lines: list[str] = []
    for paragraph in words:
        if not paragraph:
            lines.append("")
            continue
        wrapped = textwrap.wrap(paragraph, width=48)
        lines.extend(wrapped or [""])
    return lines


def _measure_block(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.ImageFont,
    extra_h: int = 0,
) -> int:
    return len(lines) * LINE_HEIGHT + extra_h + PADDING


def render_chat_image(
    messages: list[dict[str, Any]],
    *,
    title: str = "Telegram",
    subtitle: str = "",
) -> bytes:
    if not messages:
        messages = [
            {
                "sender_name": "—",
                "text": "Нет сообщений для отображения",
                "tg_chat_title": title,
            }
        ]

    font = _load_font(FONT_SIZE)
    font_bold = _load_font(HEADER_SIZE, bold=True)
    font_small = _load_font(13)

    dummy = Image.new("RGB", (WIDTH, 100), BG)
    draw = ImageDraw.Draw(dummy)

    header_h = 72
    blocks: list[tuple[dict[str, Any], list[str], int]] = []
    total_h = header_h + PADDING

    for msg in reversed(messages):
        meta = f"{msg.get('sender_name', '?')}"
        chat = msg.get("tg_chat_title", "")
        if chat and chat != title:
            meta += f" · {chat}"
        body_lines = _wrap_lines(draw, msg.get("text", ""), font, WIDTH - PADDING * 4)
        block_h = _measure_block(draw, body_lines, font, extra_h=LINE_HEIGHT + 8)
        blocks.append((msg, body_lines, block_h))
        total_h += block_h + 12

    total_h += PADDING
    img = Image.new("RGB", (WIDTH, total_h), BG)
    draw = ImageDraw.Draw(img)

    draw.rectangle((0, 0, WIDTH, header_h), fill=HEADER_BG)
    draw.text((PADDING, 16), title, font=font_bold, fill=TEXT)
    if subtitle:
        draw.text((PADDING, 42), subtitle, font=font_small, fill=MUTED)

    y = header_h + PADDING // 2
    for msg, body_lines, block_h in blocks:
        sender = msg.get("sender_name", "?")
        draw.text((PADDING, y), sender, font=font_small, fill=ACCENT)
        y += LINE_HEIGHT

        bubble_x1 = PADDING
        bubble_x2 = WIDTH - PADDING
        bubble_y1 = y
        bubble_y2 = y + len(body_lines) * LINE_HEIGHT + 16
        draw.rounded_rectangle(
            (bubble_x1, bubble_y1, bubble_x2, bubble_y2),
            radius=14,
            fill=BUBBLE_IN,
        )

        ty = bubble_y1 + 8
        for line in body_lines:
            draw.text((bubble_x1 + 14, ty), line, font=font, fill=TEXT)
            ty += LINE_HEIGHT

        y = bubble_y2 + 12

    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
