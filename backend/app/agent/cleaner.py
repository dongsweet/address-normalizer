from __future__ import annotations

import re
import unicodedata


DELIVERY_NOTE_PATTERNS = [
    r"(?:放|挂|搁|搁在|放到)(?:门口|前台|保安室|外卖柜|架子|桌上|门卫).*",
    r"(?:到了|到楼下|快到|送到后).*",
    r"(?:电话|手机|联系|打电话).*",
    r"(?:不要|不用)(?:敲门|打电话|按门铃).*",
    r"(?:备注|麻烦|谢谢).*",
]


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    text = text.replace("\u3000", " ")
    text = re.sub(r"[;；|]+", "，", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def strip_delivery_notes(value: str) -> str:
    text = normalize_text(value)
    for pattern in DELIVERY_NOTE_PATTERNS:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)
    text = re.sub(r"[，,。.\s]+$", "", text)
    return text.strip()


def clean_address(value: str) -> str:
    text = strip_delivery_notes(value)
    text = re.sub(r"(?<=\d)\s+(?=号|栋|幢|单元|室|层|楼)", "", text)
    text = re.sub(r"\s+", "", text)
    return text


def split_address_lines(value: str) -> list[str]:
    return [line.strip() for line in (value or "").splitlines() if line.strip()]
