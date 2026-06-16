# Address Normalizer

公安内网地址规范化工作台。系统把用户输入的脏地址清洗、召回、排序后，输出可审计的标准地址；Qwen 只在已有候选中选择或拒识，不负责凭空生成地址。

## Architecture

- Frontend: React + Vite + TypeScript
- Backend: FastAPI + Pydantic
- Business DB: PostgreSQL + PostGIS + pg_trgm
- Standard address source: Doris by default for intranet delivery, Hive kept as compatibility fallback
- Optional context adapters: Qwen OpenAI-compatible API, MGeo worker
- Local recall data: public POI snapshot + business memory

当前主召回链路：

1. `address_memory`
2. 标准地址库（Doris 或 Hive，业务结果统一为 `standard`）
3. `poi_catalog`
4. Qwen 候选选择或拒识

## Run With Docker Compose

先从示例配置生成 `.env`：

```powershell
Copy-Item .env.example .env
```

本地保留 Hive 模拟环境，适合不依赖外部 Doris 时快速验证：

```powershell
docker compose -f docker-compose.yml -f docker-compose.hive.yml up --build
```

如需测试 Doris 版标准库：

```bash
docker compose -f docker-compose.yml -f docker-compose.doris.yml up --build -d
bash scripts/load_doris_sample.sh
```

打开：

```text
http://localhost:5173
```

API 文档：

```text
http://localhost:8000/docs
```

## Intranet Delivery

内网交付默认连接真实 Doris：

```bash
cp .env.intranet.example .env
docker compose -f docker-compose.intranet.yml up -d
```

完整交付说明见：

```text
DELIVERY.md
```

实际交付时：

- 使用 `docker-compose.intranet.yml`
- 标准库默认走 Doris MySQL 协议端口，通常是 `9030`
- 默认不导入测试 POI
- `MGeo` 是可选 profile，可单独决定是否随包提供

## Configuration

### Standard Address Source

内网推荐 Doris：

```env
STANDARD_ADDRESS_SOURCE=doris
DORIS_ENABLED=true
DORIS_HOST=your-doris-fe-host
DORIS_PORT=9030
DORIS_DATABASE=address_normalizer
DORIS_TABLE=ysk_datahub_address_standed
DORIS_USERNAME=root
DORIS_PASSWORD=
DORIS_QUERY_TIMEOUT_SECONDS=8
DORIS_FETCH_LIMIT=20
```

Hive 兼容配置仍保留，适合本地模拟或旧环境回退：

```env
STANDARD_ADDRESS_SOURCE=hive
HIVE_ENABLED=true
HIVE_HOST=hive
HIVE_PORT=10000
HIVE_DATABASE=default
HIVE_TABLE=ysk_datahub_address_standed
HIVE_USERNAME=hive
HIVE_PASSWORD=changeme
HIVE_AUTH_MECHANISM=PLAIN
HIVE_QUERY_TIMEOUT_SECONDS=8
HIVE_FETCH_LIMIT=20
```

### Recall Scope

地址召回范围不再固定为乌鲁木齐，可通过：

```env
RECALL_SCOPE_MODE=auto
DEFAULT_CITY=
```

控制策略：

- `auto`: 尝试从输入里提取 `市/区/县` 作为过滤条件；提取不到就不限制城市
- `fixed`: 强制使用 `DEFAULT_CITY`
- `off`: 不加城市范围过滤

### Qwen

继续按 OpenAI 兼容接口接入：

```env
QWEN_BASE_URL=http://your-qwen-host:port/v1
QWEN_API_KEY=
QWEN_MODEL=your-model-name
```

`QWEN_API_KEY` 可以留空；留空时不会发送 `Authorization` 请求头。

### MGeo

MGeo 是可选组件，只在启用 Qwen 时为模型补充地址要素上下文：

```env
MGEO_ENABLED=true
MGEO_URL=http://127.0.0.1:8090
MGEO_TIMEOUT_SECONDS=60
MGEO_MODEL_ID=iic/mgeo_geographic_elements_tagging_chinese_base
MGEO_MODEL_REVISION=
```

## Status And Progress

前端会读取：

```text
GET /api/config/status
```

顶部状态重点展示：

- POI 是否存在
- 记忆库、别名、明细记录数
- 标准库连接状态
- 今日标准库查询次数
- 今日 Qwen 调用次数

批处理走流式接口：

```text
POST /api/normalize/stream
```

阶段名称包括：

- `clean`
- `recall`
- `standard`
- `rank`
- `mgeo`
- `repair`
- `qwen`
- `fast_path`
- `unmatched`
- `done`

## Doris Test Table

Doris 测试环境使用与 Hive 样例相同的字段：

```text
doris/init/001_schema.sql
data/hive_sim/ysk_datahub_address_standed.tsv
```

加载样例数据后，可用下面这条地址验证标准库命中：

```text
友好北路689号美美友好购物中心H&M，放前台
```

预期来源为 `standard`，标准库 provider 为 `doris`。

## Acceptance Checklist

1. 启动服务后打开 `/api/config/status`
2. 确认 `standard=connected`
3. 确认 `standard_source=doris` 或预期的兼容源
4. 在前端跑一批模拟地址
5. 验证主结果来源为 `standard`
6. 验证高置信标准库命中可自动沉淀到记忆库

## Rebuild Public POI Snapshot

安装抓取脚本依赖：

```powershell
python -m pip install -r requirements.txt
```

重新生成乌鲁木齐示例 POI：

```powershell
python scripts\fetch_public_poi.py --limit 500
```
