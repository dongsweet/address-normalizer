from __future__ import annotations

import re
import unicodedata


DELIVERY_NOTE_PATTERNS = [
    r"(?:放|挂|搁|搁在|放到)(?:门口|前台|保安室|外卖柜|架子|桌上|门卫)[^，,；;\n。]*",
    r"(?:到了|到楼下|快到|送到后)[^，,；;\n。]*",
    r"(?:电话|手机|联系|打电话)[^，,；;\n。]*",
    r"(?:不要|不用)(?:敲门|打电话|按门铃)[^，,；;\n。]*",
    r"(?:备注|麻烦|谢谢)[^，,；;\n。]*",
]
SEGMENT_SPLIT_RE = re.compile(r"[\n,，;；]+")
ADDRESS_HINT_RE = re.compile(
    r"(?:"
    r"省|市|区|县|乡|镇|街|路|巷|弄|道|号|栋|幢|座|单元|室|层|楼|F|f|"
    r"大厦|中心|广场|花园|小区|公寓|酒店|大院|园区|产业园|写字楼|商场|市场|学校|医院|"
    r"国际|城|园|苑|厦|门)"
)
DETAIL_ONLY_RE = re.compile(
    r"^(?:[A-Za-z0-9一二三四五六七八九十零〇两负地下B#-]+(?:栋|幢|号楼|座|单元|室|房|号|层|楼|F|f)?)$"
)
NOTE_HINT_RE = re.compile(
    r"(?:"
    r"前台|门口|楼下|保安|外卖柜|架子|桌上|门卫|电话|手机|联系|打电话|敲门|门铃|备注|"
    r"麻烦|谢谢|东西|快递|外卖|送到|送达|到了|快到)"
)
NOTE_PREFIX_RE = re.compile(r"^(?:放|送|到|联系|打|备注|麻烦|谢谢|东西|快递|外卖)")


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    text = text.replace("\u3000", " ")
    text = re.sub(r"[|]+", "，", text)
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
    text = _extract_address_segments(text)
    text = re.sub(r"(?<=\d)\s+(?=号|栋|幢|单元|室|层|楼)", "", text)
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[，,。.\s]+$", "", text)
    return text


def split_address_lines(value: str) -> list[str]:
    return [line.strip() for line in (value or "").splitlines() if line.strip()]


def _extract_address_segments(value: str) -> str:
    if not value:
        return ""

    segments = [segment.strip(" ，,。.;；") for segment in SEGMENT_SPLIT_RE.split(value) if segment.strip(" ，,。.;；")]
    if len(segments) <= 1:
        return value.strip()

    kept = [segment for segment in segments if not _is_note_segment(segment)]
    if not kept:
        return value.strip()

    selected: list[str] = []
    anchor_found = False
    for segment in kept:
        if _has_address_signal(segment):
            selected.append(segment)
            anchor_found = True
            continue
        if anchor_found and _looks_like_detail(segment):
            selected.append(segment)

    if selected:
        return "".join(selected)
    return "".join(kept)


def _is_note_segment(value: str) -> bool:
    if not value:
        return True
    if NOTE_PREFIX_RE.search(value) and not _has_address_signal(value):
        return True
    if NOTE_HINT_RE.search(value) and not _has_address_signal(value):
        return True
    return False


def _has_address_signal(value: str) -> bool:
    if not value:
        return False
    if ADDRESS_HINT_RE.search(value):
        return True
    return bool(re.search(r"\d", value) and re.search(r"(号|栋|幢|单元|室|层|楼|F|f|座|门)", value))


def _looks_like_detail(value: str) -> bool:
    if not value:
        return False
    return bool(DETAIL_ONLY_RE.fullmatch(value))
