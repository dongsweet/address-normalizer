from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AdminDivision:
    province: str
    city: str
    district: str


@dataclass(frozen=True)
class AdminHint:
    province: str | None = None
    city: str | None = None
    district: str | None = None
    stripped_text: str = ""


@dataclass(frozen=True)
class _AdminAlias:
    alias: str
    province: str
    city: str | None = None
    district: str | None = None
    short: bool = False


BUILTIN_ADMIN_DIVISIONS = [
    AdminDivision("新疆维吾尔自治区", "乌鲁木齐市", "天山区"),
    AdminDivision("新疆维吾尔自治区", "乌鲁木齐市", "沙依巴克区"),
    AdminDivision("新疆维吾尔自治区", "乌鲁木齐市", "新市区"),
    AdminDivision("新疆维吾尔自治区", "乌鲁木齐市", "水磨沟区"),
    AdminDivision("新疆维吾尔自治区", "乌鲁木齐市", "头屯河区"),
    AdminDivision("新疆维吾尔自治区", "乌鲁木齐市", "达坂城区"),
    AdminDivision("新疆维吾尔自治区", "乌鲁木齐市", "米东区"),
    AdminDivision("新疆维吾尔自治区", "乌鲁木齐市", "乌鲁木齐县"),
    AdminDivision("新疆维吾尔自治区", "克拉玛依市", "克拉玛依区"),
    AdminDivision("新疆维吾尔自治区", "克拉玛依市", "独山子区"),
    AdminDivision("新疆维吾尔自治区", "克拉玛依市", "白碱滩区"),
    AdminDivision("新疆维吾尔自治区", "克拉玛依市", "乌尔禾区"),
    AdminDivision("新疆维吾尔自治区", "昌吉回族自治州", "昌吉市"),
    AdminDivision("新疆维吾尔自治区", "昌吉回族自治州", "阜康市"),
    AdminDivision("新疆维吾尔自治区", "昌吉回族自治州", "呼图壁县"),
    AdminDivision("新疆维吾尔自治区", "伊犁哈萨克自治州", "伊宁市"),
    AdminDivision("新疆维吾尔自治区", "伊犁哈萨克自治州", "奎屯市"),
    AdminDivision("新疆维吾尔自治区", "喀什地区", "喀什市"),
    AdminDivision("新疆维吾尔自治区", "阿克苏地区", "阿克苏市"),
    AdminDivision("北京市", "北京市", "朝阳区"),
    AdminDivision("北京市", "北京市", "海淀区"),
    AdminDivision("北京市", "北京市", "西城区"),
    AdminDivision("北京市", "北京市", "丰台区"),
    AdminDivision("上海市", "上海市", "浦东新区"),
    AdminDivision("上海市", "上海市", "黄浦区"),
    AdminDivision("上海市", "上海市", "徐汇区"),
    AdminDivision("广东省", "广州市", "天河区"),
    AdminDivision("广东省", "广州市", "越秀区"),
    AdminDivision("广东省", "广州市", "番禺区"),
    AdminDivision("广东省", "深圳市", "南山区"),
    AdminDivision("广东省", "深圳市", "福田区"),
    AdminDivision("广东省", "深圳市", "宝安区"),
    AdminDivision("浙江省", "杭州市", "西湖区"),
    AdminDivision("浙江省", "杭州市", "滨江区"),
    AdminDivision("浙江省", "杭州市", "余杭区"),
    AdminDivision("江苏省", "南京市", "玄武区"),
    AdminDivision("江苏省", "南京市", "鼓楼区"),
    AdminDivision("江苏省", "苏州市", "姑苏区"),
    AdminDivision("江苏省", "苏州市", "吴中区"),
    AdminDivision("四川省", "成都市", "锦江区"),
    AdminDivision("四川省", "成都市", "武侯区"),
    AdminDivision("湖北省", "武汉市", "江汉区"),
    AdminDivision("湖北省", "武汉市", "武昌区"),
    AdminDivision("陕西省", "西安市", "雁塔区"),
    AdminDivision("陕西省", "西安市", "碑林区"),
    AdminDivision("山东省", "济南市", "历下区"),
    AdminDivision("山东省", "青岛市", "市南区"),
    AdminDivision("河南省", "郑州市", "金水区"),
    AdminDivision("河北省", "石家庄市", "长安区"),
]

_COMPACT_RE = re.compile(r"[\s,，。;；]+")
_SHORT_ALIAS_ROAD_RE = re.compile(r"^(?:东|南|西|北|中)?(?:路|街|道|巷|弄)")
_PROVINCE_SUFFIXES = ("特别行政区", "自治区", "省", "市")
_CITY_SUFFIXES = ("自治州", "地区", "盟", "市")
_DISTRICT_SUFFIXES = ("区", "县", "旗", "市")
_MUNICIPALITIES = {"北京市", "上海市", "天津市", "重庆市"}
_UNSAFE_SHORT_DISTRICT_SUFFIXES = ("乡", "镇", "街", "路", "道", "巷", "弄")
_MANUAL_CITY_ALIASES = {
    "乌鲁木齐": "乌鲁木齐市",
    "克拉玛依": "克拉玛依市",
    "昌吉": "昌吉回族自治州",
    "伊犁": "伊犁哈萨克自治州",
    "喀什": "喀什地区",
    "阿克苏": "阿克苏地区",
}
_MANUAL_DISTRICT_ALIASES = {
    "沙区": ("新疆维吾尔自治区", "乌鲁木齐市", "沙依巴克区"),
    "水区": ("新疆维吾尔自治区", "乌鲁木齐市", "水磨沟区"),
    "米东": ("新疆维吾尔自治区", "乌鲁木齐市", "米东区"),
}
_DB_ADMIN_DIVISIONS: tuple[AdminDivision, ...] | None = None


def compact_text(value: str | None) -> str:
    return _COMPACT_RE.sub("", value or "")


def query_without_admin_scope(value: str) -> str:
    hint = resolve_admin_hint(value)
    return hint.stripped_text or compact_text(value)


def has_admin_scope(value: str | None) -> bool:
    hint = resolve_admin_hint(value or "")
    return bool(hint.province or hint.city or hint.district)


def resolve_admin_hint(value: str) -> AdminHint:
    text = compact_text(value)
    if not text:
        return AdminHint(stripped_text="")

    province_aliases, city_aliases, district_aliases = _admin_aliases()
    cursor = 0
    province_match = _match_prefix(text, cursor, province_aliases)
    if province_match:
        cursor += len(province_match.alias)

    city_match = _match_prefix(text, cursor, city_aliases)
    if city_match:
        cursor += len(city_match.alias)

    district_match = _match_prefix(text, cursor, district_aliases)
    if district_match:
        cursor += len(district_match.alias)

    province = (
        province_match.province if province_match else
        city_match.province if city_match else
        district_match.province if district_match else
        None
    )
    city = (
        city_match.city if city_match else
        district_match.city if district_match else
        _municipality_city(province)
    )
    district = district_match.district if district_match else None
    stripped = text[cursor:] or text
    return AdminHint(province=province, city=city, district=district, stripped_text=stripped)


def _municipality_city(province: str | None) -> str | None:
    if province in _MUNICIPALITIES:
        return province
    return None


def _match_prefix(text: str, cursor: int, aliases: tuple[_AdminAlias, ...]) -> _AdminAlias | None:
    remaining = text[cursor:]
    for alias in aliases:
        if not remaining.startswith(alias.alias):
            continue
        if alias.short:
            suffix = remaining[len(alias.alias) :]
            if _SHORT_ALIAS_ROAD_RE.match(suffix):
                continue
            if suffix and not re.match(r"[\u4e00-\u9fffa-zA-Z0-9]", suffix[0]):
                continue
        return alias
    return None


@lru_cache
def _admin_aliases() -> tuple[tuple[_AdminAlias, ...], tuple[_AdminAlias, ...], tuple[_AdminAlias, ...]]:
    province_aliases: dict[str, _AdminAlias] = {}
    city_aliases: dict[str, _AdminAlias] = {}
    district_aliases: dict[str, _AdminAlias] = {}
    district_short_counts: dict[str, int] = {}
    city_short_aliases: set[str] = set()
    province_short_aliases: set[str] = set()

    for division in _admin_divisions():
        for alias in _name_aliases(division.province, _PROVINCE_SUFFIXES):
            province_aliases.setdefault(
                alias,
                _AdminAlias(alias=alias, province=division.province, short=alias != division.province),
            )
            if alias != division.province:
                province_short_aliases.add(alias)

        for alias in _name_aliases(division.city, _CITY_SUFFIXES):
            city_aliases.setdefault(
                alias,
                _AdminAlias(alias=alias, province=division.province, city=division.city, short=alias != division.city),
            )
            if alias != division.city:
                city_short_aliases.add(alias)

        short_district = _strip_suffix(division.district, _DISTRICT_SUFFIXES)
        if short_district and short_district != division.district:
            district_short_counts[short_district] = district_short_counts.get(short_district, 0) + 1

        district_aliases.setdefault(
            division.district,
            _AdminAlias(
                alias=division.district,
                province=division.province,
                city=division.city,
                district=division.district,
            ),
        )

    for alias, canonical_city in _MANUAL_CITY_ALIASES.items():
        for division in _admin_divisions():
            if division.city == canonical_city:
                city_aliases[alias] = _AdminAlias(
                    alias=alias,
                    province=division.province,
                    city=division.city,
                    short=True,
                )
                city_short_aliases.add(alias)
                break

    for division in _admin_divisions():
        short_district = _strip_suffix(division.district, _DISTRICT_SUFFIXES)
        if not short_district or short_district == division.district:
            continue
        if not _is_safe_short_district_alias(short_district):
            continue
        if district_short_counts.get(short_district) != 1:
            continue
        if short_district in city_short_aliases or short_district in province_short_aliases:
            continue
        district_aliases.setdefault(
            short_district,
            _AdminAlias(
                alias=short_district,
                province=division.province,
                city=division.city,
                district=division.district,
                short=True,
            ),
        )

    for alias, (province, city, district) in _MANUAL_DISTRICT_ALIASES.items():
        district_aliases[alias] = _AdminAlias(
            alias=alias,
            province=province,
            city=city,
            district=district,
            short=True,
        )

    return (
        _sorted_aliases(province_aliases),
        _sorted_aliases(city_aliases),
        _sorted_aliases(district_aliases),
    )


def _name_aliases(value: str, suffixes: tuple[str, ...]) -> set[str]:
    aliases = {value}
    stripped = _strip_suffix(value, suffixes)
    if stripped and len(stripped) >= 2:
        aliases.add(stripped)
    return aliases


def _strip_suffix(value: str, suffixes: tuple[str, ...]) -> str:
    for suffix in suffixes:
        if value.endswith(suffix) and len(value) > len(suffix):
            return value[: -len(suffix)]
    return value


def _sorted_aliases(values: dict[str, _AdminAlias]) -> tuple[_AdminAlias, ...]:
    return tuple(sorted(values.values(), key=lambda item: (-len(item.alias), item.alias)))


def _is_safe_short_district_alias(value: str) -> bool:
    if len(value) < 2 or len(value) > 4:
        return False
    return not value.endswith(_UNSAFE_SHORT_DISTRICT_SUFFIXES)


@lru_cache
def _admin_divisions() -> tuple[AdminDivision, ...]:
    if _DB_ADMIN_DIVISIONS:
        return _DB_ADMIN_DIVISIONS
    loaded = _load_admin_divisions_from_file()
    if loaded:
        return loaded
    return tuple(BUILTIN_ADMIN_DIVISIONS)


def set_admin_divisions(rows: list[dict[str, Any]] | None) -> None:
    global _DB_ADMIN_DIVISIONS
    if not rows:
        _DB_ADMIN_DIVISIONS = None
    else:
        divisions: list[AdminDivision] = []
        for row in rows:
            level = int(row.get("level") or 0)
            if level >= 3:
                province = str(row.get("province") or "").strip()
                city = str(row.get("city") or "").strip()
                district = str(row.get("name") or row.get("district") or "").strip()
                if province and city and district:
                    divisions.append(AdminDivision(province=province, city=city, district=district))
        _DB_ADMIN_DIVISIONS = tuple(divisions) or None
    _admin_divisions.cache_clear()
    _admin_aliases.cache_clear()


def _load_admin_divisions_from_file() -> tuple[AdminDivision, ...]:
    path = Path(__file__).resolve().parents[2] / "data" / "xzqh.txt"
    if not path.exists():
        return ()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return ()

    root = payload.get("data") or {}
    provinces = root.get("children") or []
    divisions: list[AdminDivision] = []
    for province_node in provinces:
        province = str(province_node.get("name") or "").strip()
        if not province:
            continue
        province_children = province_node.get("children") or []
        if not province_children:
            continue
        if _is_direct_municipality_children(province_children):
            for district_node in province_children:
                district = str(district_node.get("name") or "").strip()
                if district:
                    divisions.append(AdminDivision(province=province, city=province, district=district))
            continue
        for city_node in province_children:
            city = str(city_node.get("name") or "").strip()
            if not city:
                continue
            district_children = city_node.get("children") or []
            if not district_children:
                continue
            for district_node in district_children:
                district = str(district_node.get("name") or "").strip()
                if district:
                    divisions.append(AdminDivision(province=province, city=city, district=district))
    return tuple(divisions)


def _is_direct_municipality_children(children: list[dict]) -> bool:
    return bool(children) and all(int(child.get("level") or 0) >= 3 for child in children)
