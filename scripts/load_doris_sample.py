from __future__ import annotations

import csv
import os
import re
import time
from pathlib import Path

import pymysql


ROOT_DIR = Path(__file__).resolve().parents[1]
SCHEMA_SQL = ROOT_DIR / "doris" / "init" / "001_schema.sql"
SAMPLE_TSV = ROOT_DIR / "data" / "hive_sim" / "ysk_datahub_address_standed.tsv"

DORIS_HOST = os.getenv("DORIS_HOST", "127.0.0.1")
DORIS_PORT = int(os.getenv("DORIS_PORT", "9030"))
DORIS_DATABASE = os.getenv("DORIS_DATABASE", "address_normalizer")
DORIS_TABLE = os.getenv("DORIS_TABLE", "ysk_datahub_address_standed")
DORIS_USERNAME = os.getenv("DORIS_USERNAME", "root")
DORIS_PASSWORD = os.getenv("DORIS_PASSWORD", "")
DORIS_READY_TIMEOUT_SECONDS = int(os.getenv("DORIS_READY_TIMEOUT_SECONDS", "180"))

COLUMNS = [
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


def main() -> None:
    wait_for_doris()
    apply_schema()
    loaded_rows = load_sample_rows()
    print(f"Loaded Doris sample data into {DORIS_HOST}:{DORIS_PORT}/{DORIS_DATABASE}.{DORIS_TABLE}")
    print(f"loaded_rows={loaded_rows}")


def wait_for_doris() -> None:
    deadline = time.monotonic() + DORIS_READY_TIMEOUT_SECONDS
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with connect() as connection:
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


def apply_schema() -> None:
    sql = SCHEMA_SQL.read_text(encoding="utf-8")
    sql = sql.replace("address_normalizer", DORIS_DATABASE)
    sql = sql.replace("ysk_datahub_address_standed", DORIS_TABLE)
    with connect() as connection:
        with connection.cursor() as cursor:
            for statement in [part.strip() for part in sql.split(";") if part.strip()]:
                cursor.execute(statement)


def load_sample_rows() -> int:
    rows: list[list[str]] = []
    with SAMPLE_TSV.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.reader(handle, delimiter="\t"):
            rows.append((row + [""] * len(COLUMNS))[: len(COLUMNS)])

    with connect(DORIS_DATABASE) as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"TRUNCATE TABLE {ident(DORIS_TABLE)}")
            column_sql = ", ".join(ident(column) for column in COLUMNS)
            placeholders = ", ".join(["%s"] * len(COLUMNS))
            cursor.executemany(
                f"INSERT INTO {ident(DORIS_TABLE)} ({column_sql}) VALUES ({placeholders})",
                rows,
            )
            cursor.execute(f"SELECT COUNT(*) FROM {ident(DORIS_TABLE)}")
            return int(cursor.fetchone()[0])


def connect(database: str | None = None):
    kwargs = {
        "host": DORIS_HOST,
        "port": DORIS_PORT,
        "user": DORIS_USERNAME,
        "password": DORIS_PASSWORD,
        "connect_timeout": 3,
        "read_timeout": 8,
        "write_timeout": 8,
        "charset": "utf8mb4",
        "autocommit": True,
    }
    if database:
        kwargs["database"] = database
    return pymysql.connect(**kwargs)


def ident(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_]+", value):
        raise ValueError(f"unsafe identifier: {value}")
    return f"`{value}`"


if __name__ == "__main__":
    main()
