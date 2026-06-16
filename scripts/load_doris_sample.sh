#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOADER="${ROOT_DIR}/scripts/load_doris_sample.py"

if python3 -c "import pymysql" >/dev/null 2>&1; then
  exec python3 "${LOADER}"
fi

if command -v docker >/dev/null 2>&1 \
  && docker image inspect address-normalizer-api:intranet >/dev/null 2>&1 \
  && docker network inspect address-normalizer_doris_network >/dev/null 2>&1; then
  exec docker run --rm \
    --network address-normalizer_doris_network \
    -e DORIS_HOST="${DORIS_HOST:-172.20.80.2}" \
    -e DORIS_PORT="${DORIS_PORT:-9030}" \
    -e DORIS_DATABASE="${DORIS_DATABASE:-address_normalizer}" \
    -e DORIS_TABLE="${DORIS_TABLE:-ysk_datahub_address_standed}" \
    -e DORIS_USERNAME="${DORIS_USERNAME:-root}" \
    -e DORIS_PASSWORD="${DORIS_PASSWORD:-}" \
    -v "${ROOT_DIR}:/work" \
    -w /work \
    address-normalizer-api:intranet \
    python /work/scripts/load_doris_sample.py
fi

cat >&2 <<'EOF'
Could not find a usable Doris sample loader runtime.

Use one of these options:
1. Install backend dependencies, then rerun:
   python3 -m pip install -r backend/requirements.txt
   bash scripts/load_doris_sample.sh

2. Build the API image and start docker-compose.doris.yml, then rerun:
   docker compose -f docker-compose.intranet.yml build api
   docker compose -f docker-compose.yml -f docker-compose.doris.yml up -d doris-fe doris-be
   bash scripts/load_doris_sample.sh
EOF
exit 1
