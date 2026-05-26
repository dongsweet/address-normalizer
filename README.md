# Address Normalizer Demo

乌鲁木齐外卖地址规范化演示系统。目标是把用户输入的脏地址批量转换成可匹配、可审计的结构化结果。

## Architecture

- Frontend: React + Vite + TypeScript
- Backend: FastAPI + Pydantic
- Database: PostgreSQL + PostGIS + pg_trgm
- POI candidate data: Overture public snapshot for Urumqi
- Optional adapters: Qwen vLLM API, MGeo worker, map API

The data layer is split into three logical libraries:

- `poi_catalog`: public or licensed POI candidates
- `address_memory`: business-confirmed address memory
- `standard_address`: optional authority/customer standard-address table

`standard_address` can stay empty. The pipeline skips it when no rows exist.

## VM Size

For a demo where Qwen is already hosted elsewhere, 4 CPU cores and 16 GB RAM are enough for:

- PostgreSQL/PostGIS
- FastAPI
- Vite frontend
- optional CPU MGeo worker for small batches

MGeo should remain optional because CPU inference may be slow.

## Run With Docker Compose

Create `.env` from the example:

```powershell
Copy-Item .env.example .env
```

Start the stack:

```powershell
docker compose up --build
```

To include the optional MGeo CPU worker:

```powershell
docker compose --profile mgeo up --build
```

Open:

```text
http://localhost:5173
```

API docs:

```text
http://localhost:8000/docs
```

If the VM only exposes SSH, create a local tunnel from your laptop:

```powershell
ssh -L 5173:127.0.0.1:5173 -L 8000:127.0.0.1:8000 -p 1205 dq@172.20.30.201
```

Then open `http://localhost:5173`.

On first API startup, the backend creates PostGIS/pg_trgm extensions, creates tables, and seeds:

```text
data/public_poi/urumqi_overture_poi_sample.csv
```

## Batch Progress And Speed Controls

The web UI uses the streaming endpoint:

```text
POST /api/normalize/stream
```

It returns newline-delimited JSON progress events for each row, including stages such as recall, MGeo, Qwen, fast path, and done. The UI also lets the operator choose batch concurrency from 1 to 4.

High-confidence matches can skip MGeo/Qwen to reduce latency:

```env
MAX_BATCH_CONCURRENCY=4
FAST_PATH_ENABLED=true
FAST_PATH_SCORE=0.9
FAST_PATH_GAP=0.12
```

The older `POST /api/normalize/batch` endpoint is still available and also accepts `concurrency`.

## Qwen Configuration

If the existing vLLM service exposes an OpenAI-compatible endpoint, set:

```env
QWEN_BASE_URL=http://your-qwen-host:port/v1
QWEN_API_KEY=your-key
QWEN_MODEL=your-model-name
```

If these are empty, the demo still works with deterministic scoring.

## MGeo Configuration

MGeo is packaged as an optional HTTP worker. It uses ModelScope's Chinese geographic element tagging model and exposes `POST /parse` on the internal Compose network.

```env
MGEO_ENABLED=true
MGEO_URL=http://mgeo:8090
MGEO_TIMEOUT_SECONDS=60
MGEO_MODEL_ID=iic/mgeo_geographic_elements_tagging_chinese_base
MGEO_MODEL_REVISION=
```

The worker is CPU-only. The first startup downloads and warms the model, so the first run can take several minutes. Parsed address elements are sent to Qwen as extra context and are included in `raw_model_output.mgeo`.

## Map API Configuration

The map adapter is optional. Demo runs without it.

```env
MAP_API_ENABLED=true
MAP_PROVIDER=amap
AMAP_KEY=your-key
```

Map API results are treated as runtime candidates. The adapter only keeps sanitized matching fields such as provider id, adcode, category, coordinates, and normalized display address; it does not return or persist the provider's full raw POI payload. Do not persist provider POI data unless the commercial contract explicitly allows it.

## Rebuild Urumqi POI Snapshot

Install the small fetch dependency:

```powershell
python -m pip install -r requirements.txt
```

Regenerate the current sample:

```powershell
python scripts\fetch_public_poi.py --limit 500
```

The current Overture snapshot yields 173 usable Urumqi rows in the default bbox.
