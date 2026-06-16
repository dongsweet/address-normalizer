from __future__ import annotations

import argparse
import random
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Iterable

try:
    import pymysql
except ImportError:  # pragma: no cover - shell wrapper can run this inside the API image
    pymysql = None


TABLE_COLUMNS = [
    "jxkid",
    "cjd",
    "rjxksj",
    "xxdz",
    "row_num_id",
    "src_address",
    "stand_address",
    "city",
    "county",
    "develop_area",
    "town",
    "community",
    "village_group",
    "bus_area",
    "road",
    "sub_road",
    "road_no",
    "subroad_no",
    "poi",
    "building",
    "unit",
    "floor",
    "room",
    "part_path",
]


@dataclass(frozen=True)
class AdminDivision:
    province: str
    city: str
    district: str


ADMIN_DIVISIONS = [
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

ROAD_STEMS = [
    "友好",
    "光明",
    "北京",
    "人民",
    "幸福",
    "建设",
    "团结",
    "新华",
    "解放",
    "迎宾",
    "文化",
    "和平",
    "南湖",
    "科技",
    "长江",
    "昆仑",
    "金桥",
    "银川",
    "红山",
    "绿洲",
]
ROAD_DIRECTIONS = ["", "东", "南", "西", "北", "中"]
ROAD_SUFFIXES = ["路", "街", "大道", "巷", "公路"]
TOWN_STEMS = ["幸福", "团结", "新华", "胜利", "南湖", "迎宾", "高新", "友好", "红山", "金桥"]
TOWN_SUFFIXES = ["街道", "镇", "乡"]
COMMUNITY_SUFFIXES = ["小区", "花园", "家园", "公寓", "新村", "苑"]
POI_SUFFIXES = ["购物中心", "广场", "大厦", "写字楼", "产业园", "酒店", "超市", "学校", "医院"]
BRANDS = ["H&M", "星河", "万达", "天悦", "银泰", "盒马", "优选", "国贸", "时代", "华府", "美美", "德汇"]
BUS_AREA_SUFFIXES = ["商圈", "片区", "商务区", "生活圈"]
DEVELOP_AREAS = ["", "", "", "高新技术开发区", "经济技术开发区", "工业园区", "新区"]


@dataclass(frozen=True)
class DorisConfig:
    host: str
    port: int
    database: str
    table: str
    username: str
    password: str
    connect_timeout: int
    read_timeout: int
    write_timeout: int


def main() -> None:
    args = parse_args()
    config = DorisConfig(
        host=args.host,
        port=args.port,
        database=args.database,
        table=args.table,
        username=args.username,
        password=args.password,
        connect_timeout=args.connect_timeout,
        read_timeout=args.read_timeout,
        write_timeout=args.write_timeout,
    )
    wait_for_doris(config, args.ready_timeout)
    ensure_table(config, truncate=args.truncate)
    inserted = insert_rows(config, args)
    total = count_rows(config)
    print(f"inserted_rows={inserted}")
    print(f"table_rows={total}")
    print(f"target={config.host}:{config.port}/{config.database}.{config.table}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate synthetic standard-address rows and insert them into Doris.")
    parser.add_argument("--rows", type=int, default=100_000, help="rows to generate, default: 100000")
    parser.add_argument("--batch-size", type=int, default=1000, help="insert batch size, default: 1000")
    parser.add_argument("--seed", type=int, default=20260616, help="random seed for repeatable data")
    parser.add_argument("--run-id", default=datetime.now(UTC).strftime("%Y%m%d%H%M%S"), help="ID namespace suffix")
    parser.add_argument("--id-prefix", default="SYN", help="jxkid prefix")
    parser.add_argument("--truncate", action="store_true", help="truncate the target table before inserting")
    parser.add_argument("--host", default=_env("DORIS_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(_env("DORIS_PORT", "9030")))
    parser.add_argument("--database", default=_env("DORIS_DATABASE", "address_normalizer"))
    parser.add_argument("--table", default=_env("DORIS_TABLE", "ysk_datahub_address_standed"))
    parser.add_argument("--username", default=_env("DORIS_USERNAME", "root"))
    parser.add_argument("--password", default=_env("DORIS_PASSWORD", ""))
    parser.add_argument("--connect-timeout", type=int, default=5)
    parser.add_argument("--read-timeout", type=int, default=30)
    parser.add_argument("--write-timeout", type=int, default=30)
    parser.add_argument("--ready-timeout", type=int, default=180)
    parser.add_argument("--progress-every", type=int, default=10_000)
    return parser.parse_args()


def wait_for_doris(config: DorisConfig, ready_timeout: int) -> None:
    deadline = time.monotonic() + ready_timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with connect(config) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SHOW BACKENDS")
                    columns = [column[0] for column in cursor.description or []]
                    rows = cursor.fetchall()
                    alive_index = columns.index("Alive")
                    if any(str(row[alive_index]).lower() == "true" for row in rows):
                        return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        time.sleep(2)
    raise RuntimeError(f"Doris backend did not become alive: {last_error}")


def ensure_table(config: DorisConfig, *, truncate: bool) -> None:
    with connect(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS {ident(config.database)}")
            if not table_exists(cursor, config):
                cursor.execute(f"USE {ident(config.database)}")
                cursor.execute(create_table_sql(config.table))
            elif truncate:
                cursor.execute(f"TRUNCATE TABLE {ident(config.database)}.{ident(config.table)}")


def table_exists(cursor, config: DorisConfig) -> bool:
    cursor.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
        (config.database, config.table),
    )
    return int(cursor.fetchone()[0]) > 0


def create_table_sql(table: str) -> str:
    return f"""
        CREATE TABLE {ident(table)} (
            jxkid VARCHAR(128),
            cjd VARCHAR(255),
            rjxksj VARCHAR(64),
            xxdz STRING,
            row_num_id VARCHAR(64),
            src_address STRING,
            stand_address STRING,
            city VARCHAR(64),
            county VARCHAR(64),
            develop_area VARCHAR(128),
            town VARCHAR(128),
            community VARCHAR(255),
            village_group VARCHAR(255),
            bus_area VARCHAR(255),
            road VARCHAR(255),
            sub_road VARCHAR(255),
            road_no VARCHAR(64),
            subroad_no VARCHAR(64),
            poi VARCHAR(255),
            building VARCHAR(64),
            unit VARCHAR(64),
            floor VARCHAR(64),
            room VARCHAR(64),
            part_path STRING
        )
        DUPLICATE KEY(jxkid)
        DISTRIBUTED BY HASH(jxkid) BUCKETS 8
        PROPERTIES ("replication_num" = "1")
    """


def insert_rows(config: DorisConfig, args: argparse.Namespace) -> int:
    rng = random.Random(args.seed)
    inserted = 0
    with connect(config, database=config.database) as connection:
        with connection.cursor() as cursor:
            column_sql = ", ".join(ident(column) for column in TABLE_COLUMNS)
            placeholders = ", ".join(["%s"] * len(TABLE_COLUMNS))
            sql = f"INSERT INTO {ident(config.table)} ({column_sql}) VALUES ({placeholders})"
            for batch in batched(generate_rows(rng, args.rows, args.id_prefix, args.run_id), args.batch_size):
                cursor.executemany(sql, batch)
                inserted += len(batch)
                if args.progress_every and inserted % args.progress_every == 0:
                    print(f"progress={inserted}/{args.rows}", flush=True)
    return inserted


def generate_rows(rng: random.Random, rows: int, id_prefix: str, run_id: str) -> Iterable[list[str]]:
    base_time = datetime(2026, 1, 1, 8, 0, 0)
    for index in range(1, rows + 1):
        admin = rng.choice(ADMIN_DIVISIONS)
        road = fake_road(rng)
        road_no = f"{rng.randint(1, 9999)}号"
        town = fake_town(rng)
        develop_area = rng.choice(DEVELOP_AREAS)
        anchor_name, community, poi = fake_anchor(rng)
        building = f"{rng.randint(1, 80)}栋"
        unit = f"{rng.randint(1, 6)}单元"
        floor = f"{rng.randint(1, 36)}楼"
        room = f"{rng.randint(1, 36):02d}{rng.randint(1, 24):02d}室"
        detail = "-".join([building, unit, floor, room])
        address_core = f"{town}{road}{road_no}{anchor_name}{detail}"
        stand_address = f"{admin.province}{admin.city}{admin.district}{address_core}"
        src_address = noisy_source_address(rng, road, road_no, anchor_name, building, unit, floor, room)
        xxdz = f"{road}{road_no}{anchor_name}{building}{unit}{floor}{room}"
        collected_at = base_time + timedelta(minutes=index % 600_000)
        jxkid = f"{id_prefix}-{run_id}-{index:06d}"
        yield [
            jxkid,
            "合成采集",
            collected_at.strftime("%Y-%m-%d %H:%M:%S"),
            xxdz,
            str(index),
            src_address,
            stand_address,
            admin.city,
            admin.district,
            develop_area,
            town,
            community,
            "",
            fake_bus_area(rng),
            road,
            "",
            road_no,
            "",
            poi,
            building,
            unit,
            floor,
            room,
            f"province={admin.province}/city={admin.city}/county={admin.district}",
        ]


def fake_road(rng: random.Random) -> str:
    stem = rng.choice(ROAD_STEMS)
    direction = rng.choice(ROAD_DIRECTIONS)
    suffix = rng.choice(ROAD_SUFFIXES)
    if suffix == "大道" and direction:
        return f"{stem}{direction}{suffix}"
    return f"{stem}{direction}{suffix}"


def fake_town(rng: random.Random) -> str:
    return f"{rng.choice(TOWN_STEMS)}{rng.choice(TOWN_SUFFIXES)}"


def fake_anchor(rng: random.Random) -> tuple[str, str, str]:
    brand = rng.choice(BRANDS)
    if rng.random() < 0.58:
        community = f"{brand}{rng.choice(COMMUNITY_SUFFIXES)}"
        return community, community, community
    poi = f"{brand}{rng.choice(POI_SUFFIXES)}"
    return poi, "", poi


def fake_bus_area(rng: random.Random) -> str:
    return f"{rng.choice(TOWN_STEMS)}{rng.choice(BUS_AREA_SUFFIXES)}"


def noisy_source_address(
    rng: random.Random,
    road: str,
    road_no: str,
    anchor_name: str,
    building: str,
    unit: str,
    floor: str,
    room: str,
) -> str:
    variants = [
        f"{road}{road_no}{anchor_name}{building}{unit}{floor}{room}",
        f"{road}{road_no}{anchor_name}{building}{unit}{room}",
        f"{anchor_name}{road_no}{building}{unit}{floor}{room}",
        f"{road}{road_no}{anchor_name}{building}-{unit}-{room}",
    ]
    return rng.choice(variants)


def count_rows(config: DorisConfig) -> int:
    with connect(config, database=config.database) as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM {ident(config.table)}")
            return int(cursor.fetchone()[0])


def connect(config: DorisConfig, database: str | None = None):
    if pymysql is None:
        raise RuntimeError("PyMySQL is required. Install backend requirements or run through the Docker wrapper.")
    kwargs = {
        "host": config.host,
        "port": config.port,
        "user": config.username,
        "password": config.password,
        "connect_timeout": config.connect_timeout,
        "read_timeout": config.read_timeout,
        "write_timeout": config.write_timeout,
        "charset": "utf8mb4",
        "autocommit": True,
    }
    if database:
        kwargs["database"] = database
    return pymysql.connect(**kwargs)


def batched(rows: Iterable[list[str]], size: int) -> Iterable[list[list[str]]]:
    batch: list[list[str]] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def ident(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_]+", value):
        raise ValueError(f"unsafe identifier: {value}")
    return f"`{value}`"


def _env(name: str, default: str) -> str:
    import os

    return os.getenv(name, default)


if __name__ == "__main__":
    main()
