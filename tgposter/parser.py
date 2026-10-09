# copyright by berlonak
# telegram: @Kilax123
from __future__ import annotations

import bisect
import re
from dataclasses import dataclass
from typing import Any, Optional


STYLE_ALIASES = {
    "!r": "danger",
    "!red": "danger",
    "!к": "danger",
    "!красный": "danger",
    "!g": "success",
    "!green": "success",
    "!з": "success",
    "!зелёный": "success",
    "!зеленый": "success",
    "!b": "primary",
    "!blue": "primary",
    "!с": "primary",
    "!синий": "primary",
}

BUTTON_RE = re.compile(
    r"^\s*(?:(?P<prefix>![\wа-яёА-ЯЁ]+)\s+)?(?P<title>.+?)\s+-\s+(?P<url>\S+)\s*$",
    flags=re.IGNORECASE,
)
SEPARATOR_LINE_RE = re.compile(r"(?m)^\s*---\s*$")


@dataclass(frozen=True)
class Button:
    text: str
    url: str
    style: Optional[str] = None


@dataclass(frozen=True)
class Content:
    type: str
    file_id: Optional[str] = None


@dataclass(frozen=True)
class ParsedPost:
    content: Content
    text: str
    entities: list[dict[str, Any]]
    buttons: list[list[Button]]


def normalize_url(raw_url: str) -> str:
    url = raw_url.strip()
    if url.startswith("t.me/"):
        return "https://" + url
    if url.startswith("@"):
        return "https://t.me/" + url[1:]
    return url


def parse_button_cell(cell: str) -> Button:
    match = BUTTON_RE.match(cell.strip())
    if not match:
        raise ValueError(f"Не понял кнопку: {cell!r}. Формат: !r Текст кнопки - https://example.com")

    prefix = (match.group("prefix") or "").lower()
    text = match.group("title").strip()
    url = normalize_url(match.group("url"))
    style = STYLE_ALIASES.get(prefix)

    if prefix and style is None:
        raise ValueError(f"Неизвестный цвет {prefix}. Используй !r, !g или !b.")
    if not text:
        raise ValueError("Пустой текст кнопки.")
    if not (url.startswith("http://") or url.startswith("https://") or url.startswith("tg://")):
        raise ValueError(f"Ссылка должна начинаться с http://, https://, tg://, t.me/ или @username: {url}")

    return Button(text=text, url=url, style=style)


def parse_buttons(block: str) -> list[list[Button]]:
    rows: list[list[Button]] = []
    for line in block.splitlines():
        line = line.strip()
        if not line:
            continue
        cells = [cell.strip() for cell in line.split("|") if cell.strip()]
        row = [parse_button_cell(cell) for cell in cells]
        if row:
            rows.append(row)
    return rows


def utf16_cumulative_positions(text: str) -> list[int]:
    offsets = [0]
    total = 0
    for char in text:
        total += len(char.encode("utf-16-le")) // 2
        offsets.append(total)
    return offsets


def utf16_to_py_index(cumulative: list[int], utf16_offset: int) -> int:
    index = bisect.bisect_left(cumulative, utf16_offset)
    if index >= len(cumulative):
        return len(cumulative) - 1
    return index


def trim_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def slice_entities(source_text: str, entities: list[dict[str, Any]], start: int, end: int) -> list[dict[str, Any]]:
    """Cut service block away and keep Telegram formatting offsets valid."""
    if not entities:
        return []

    source_u16 = utf16_cumulative_positions(source_text)
    post_text = source_text[start:end]
    post_u16 = utf16_cumulative_positions(post_text)
    result: list[dict[str, Any]] = []

    for entity in entities:
        raw_offset = int(entity.get("offset", 0))
        raw_length = int(entity.get("length", 0))
        ent_start = utf16_to_py_index(source_u16, raw_offset)
        ent_end = utf16_to_py_index(source_u16, raw_offset + raw_length)

        overlap_start = max(ent_start, start)
        overlap_end = min(ent_end, end)
        if overlap_start >= overlap_end:
            continue

        new_start = overlap_start - start
        new_end = overlap_end - start
        copied = dict(entity)
        copied["offset"] = post_u16[new_start]
        copied["length"] = post_u16[new_end] - post_u16[new_start]
        if copied["length"] > 0:
            result.append(copied)

    return result


def extract_content(message: dict[str, Any]) -> tuple[Content, str, list[dict[str, Any]]]:
    if "text" in message:
        return Content(type="text"), message["text"], message.get("entities", [])

    caption = message.get("caption")
    if not caption:
        raise ValueError("Для медиа-поста добавь текст и кнопки в caption.")

    entities = message.get("caption_entities", [])
    if "photo" in message:
        return Content(type="photo", file_id=message["photo"][-1]["file_id"]), caption, entities
    if "video" in message:
        return Content(type="video", file_id=message["video"]["file_id"]), caption, entities
    if "document" in message:
        return Content(type="document", file_id=message["document"]["file_id"]), caption, entities
    if "animation" in message:
        return Content(type="animation", file_id=message["animation"]["file_id"]), caption, entities

    raise ValueError("Этот тип поста пока не поддержан. Используй текст, фото, видео, документ или GIF.")


def parse_post(message: dict[str, Any]) -> ParsedPost:
    content, source_text, source_entities = extract_content(message)
    match = SEPARATOR_LINE_RE.search(source_text)
    if not match:
        raise ValueError(
            "Добавь разделитель --- отдельной строкой между постом и кнопками.\n\n"
            "Пример:\nТекст поста\n---\n!r Красная кнопка - https://example.com"
        )

    post_start, post_end = trim_bounds(source_text, 0, match.start())
    buttons_block = source_text[match.end():].strip()
    text = source_text[post_start:post_end]
    if not text:
        raise ValueError("Текст поста пустой.")

    buttons = parse_buttons(buttons_block)
    if not buttons:
        raise ValueError("После --- нет кнопок.")

    entities = slice_entities(source_text, source_entities, post_start, post_end)
    return ParsedPost(content=content, text=text, entities=entities, buttons=buttons)


def make_reply_markup(button_rows: list[list[Button]], controls: Optional[list[list[dict[str, Any]]]] = None) -> dict[str, Any]:
    keyboard: list[list[dict[str, Any]]] = []
    for row in button_rows:
        keyboard_row: list[dict[str, Any]] = []
        for button in row:
            payload: dict[str, Any] = {"text": button.text, "url": button.url}
            if button.style:
                payload["style"] = button.style
            keyboard_row.append(payload)
        keyboard.append(keyboard_row)

    if controls:
        keyboard.extend(controls)
    return {"inline_keyboard": keyboard}
