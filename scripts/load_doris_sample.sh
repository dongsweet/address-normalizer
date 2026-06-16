#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCHEMA_SQL="${ROOT_DIR}/doris/init/001_schema.sql"
SAMPLE_TSV="${ROOT_DIR}/data/hive_sim/ysk_datahub_address_standed.tsv"

DORIS_HOST="${DORIS_HOST:-127.0.0.1}"
DORIS_PORT="${DORIS_PORT:-9030}"
DORIS_DATABASE="${DORIS_DATABASE:-address_normalizer}"
DORIS_TABLE="${DORIS_TABLE:-ysk_datahub_address_standed}"
DORIS_USERNAME="${DORIS_USERNAME:-root}"
DORIS_PASSWORD="${DORIS_PASSWORD:-}"

if ! command -v mysql >/dev/null 2>&1; then
  echo "mysql client is required. Install mysql-client/mariadb-client first." >&2
  exit 1
fi

MYSQL_ARGS=(
  -h "${DORIS_HOST}"
  -P "${DORIS_PORT}"
  -u "${DORIS_USERNAME}"
  --default-character-set=utf8mb4
)

if [[ -n "${DORIS_PASSWORD}" ]]; then
  MYSQL_ARGS+=("-p${DORIS_PASSWORD}")
fi

python3 - "${SCHEMA_SQL}" "${DORIS_DATABASE}" "${DORIS_TABLE}" <<'PY' | mysql "${MYSQL_ARGS[@]}"
from __future__ import annotations

import re
import sys
from pathlib import Path

schema_path = Path(sys.argv[1])
database = sys.argv[2]
table = sys.argv[3]

for value in (database, table):
    if not re.fullmatch(r"[A-Za-z0-9_]+", value):
        raise ValueError(f"unsafe identifier: {value}")

sql = schema_path.read_text(encoding="utf-8")
sql = sql.replace("address_normalizer", database)
sql = sql.replace("ysk_datahub_address_standed", table)
print(sql)
PY

python3 - "${SAMPLE_TSV}" "${DORIS_DATABASE}" "${DORIS_TABLE}" <<'PY' | mysql "${MYSQL_ARGS[@]}" "${DORIS_DATABASE}"
from __future__ import annotations

import csv
import sys
from pathlib import Path

sample_path = Path(sys.argv[1])
database = sys.argv[2]
table = sys.argv[3]

columns = [
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


def ident(value: str) -> str:
    if not value.replace("_", "").isalnum():
        raise ValueError(f"unsafe identifier: {value}")
    return f"`{value}`"


def quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


column_sql = ", ".join(ident(column) for column in columns)
print(f"USE {ident(database)};")
print(f"TRUNCATE TABLE {ident(table)};")
with sample_path.open("r", encoding="utf-8", newline="") as handle:
    reader = csv.reader(handle, delimiter="\t")
    for row in reader:
        values = (row + [""] * len(columns))[: len(columns)]
        value_sql = ", ".join(quote(value) for value in values)
        print(f"INSERT INTO {ident(table)} ({column_sql}) VALUES ({value_sql});")
PY

echo "Loaded Doris sample data into ${DORIS_HOST}:${DORIS_PORT}/${DORIS_DATABASE}.${DORIS_TABLE}"
