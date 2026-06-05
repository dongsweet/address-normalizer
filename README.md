# Address Normalizer

乌鲁木齐地址规范化工作台，面向公安内网联调版本。系统把用户输入的脏地址转换成可匹配、可审计的结构化结果，并尽量在进入内网前把 Hive 标准地址链路跑通。

## Architecture

- Frontend: React + Vite + TypeScript
- Backend: FastAPI + Pydantic
- Business DB: PostgreSQL + PostGIS + pg_trgm
- Standard address source: HiveServer2
- Optional context adapters: Qwen OpenAI-compatible API, MGeo worker
- Local recall data: public POI snapshot + business memory

当前主召回链路：

1. `address_memory`
2. Hive 标准地址表 `ysk_datahub_address_standed`
3. `poi_catalog`
4. Qwen 候选选择或拒识

## Run With Docker Compose

先从示例配置生成 `.env`：

```powershell
Copy-Item .env.example .env
```

启动主应用栈和模拟 Hive：

```powershell
docker compose -f docker-compose.yml -f docker-compose.hive.yml up --build
```

如果需要同时带上 MGeo：

```powershell
docker compose -f docker-compose.yml -f docker-compose.hive.yml --profile mgeo up --build
```

打开：

```text
http://localhost:5173
```

API 文档：

```text
http://localhost:8000/docs
```

如果测试机只开放 SSH，可以建立本地隧道：

```powershell
ssh -L 5173:127.0.0.1:5173 -L 8000:127.0.0.1:8000 -p 1205 dq@172.20.30.201
```

## Simulated Hive

`docker-compose.hive.yml` 提供一套独立的 Hive 模拟环境，包含：

- `hive-metastore-postgres`
- `hive-metastore`
- `hive`
- `hive-init`

初始化脚本会创建并装载：

```text
default.ysk_datahub_address_standed
```

样例数据文件位于：

```text
data/hive_sim/ysk_datahub_address_standed.tsv
```

它覆盖了这些典型场景：

- 标准地址和源地址不一致
- 同名 POI 歧义
- 门牌号、楼栋、单元、楼层、房号
- `poi/community` 缺失时的名称回退
- 空字段和业务区域字段

## Configuration

### Qwen

继续按 OpenAI 兼容接口接入：

```env
QWEN_BASE_URL=http://your-qwen-host:port/v1
QWEN_API_KEY=
QWEN_MODEL=your-model-name
```

`QWEN_API_KEY` 可以留空；留空时不会发送 `Authorization` 请求头。

### Hive

开发测试默认配置已经指向模拟 Hive：

```env
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

进入内网后，只需要把这些值改成目标环境即可。仓库不会保存真实密码。

### MGeo

MGeo 仍然是可选组件，只在启用 Qwen 时为模型补充地址要素上下文：

```env
MGEO_ENABLED=true
MGEO_URL=http://mgeo:8090
MGEO_TIMEOUT_SECONDS=60
MGEO_MODEL_ID=iic/mgeo_geographic_elements_tagging_chinese_base
MGEO_MODEL_REVISION=
```

## Status And Progress

前端会读取：

```text
GET /api/config/status
```

当前状态里会展示：

- PostgreSQL 是否可用
- Hive 是否已配置
- 当前 Hive 表名
- 今日 Hive 查询次数
- 今日 Qwen 调用次数
- POI / 记忆库 / 明细条数

批处理走流式接口：

```text
POST /api/normalize/stream
```

阶段名称包括：

- `clean`
- `recall`
- `hive`
- `rank`
- `mgeo`
- `repair`
- `qwen`
- `fast_path`
- `unmatched`
- `done`

## Acceptance Checklist

1. 启动 `docker-compose.yml + docker-compose.hive.yml`
2. 打开 `/api/config/status`，确认 `hive=configured`
3. 在前端跑一批模拟地址
4. 验证主结果来源为 `standard`
5. 验证高置信标准库命中可自动沉淀到记忆库
6. 把 Hive 配置改成接近真实内网的占位值，确认应用仍可启动，并在查询时给出可理解的失败提示

## Rebuild Public POI Snapshot

安装抓取脚本依赖：

```powershell
python -m pip install -r requirements.txt
```

重新生成乌鲁木齐示例 POI：

```powershell
python scripts\fetch_public_poi.py --limit 500
```
