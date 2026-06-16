# Address Normalizer Intranet Delivery

这份文档只面向最终交付和内网部署。标准地址库默认连接内网 Doris，不包含本地 Hive 模拟环境说明。

## Delivery Package

最小交付文件：

- `address-normalizer-api_intranet.tar`
- `address-normalizer-frontend_intranet.tar`
- `postgis-16-3.4.tar`
- `docker-compose.intranet.yml`
- `.env.intranet.example`
- `DELIVERY.md`

只有需要离线启用 MGeo 时，再额外提供：

- `address-normalizer-mgeo_intranet.tar`

## What Is Included

- `api`: 后端服务，启动时可自动初始化 PostgreSQL 表结构
- `frontend`: 前端静态文件，Nginx 监听 `5173` 并反向代理 `/api`
- `db`: PostgreSQL + PostGIS，用于 POI、记忆库、任务结果和调用统计
- `mgeo`: 可选，模型已预打包进镜像时可离线启动

这套交付包按 Linux Docker host network 运行，不依赖 `api`、`db`、`mgeo` 这类 Docker 内部容器名互联。

## Import Images

```bash
docker load -i postgis-16-3.4.tar
docker load -i address-normalizer-api_intranet.tar
docker load -i address-normalizer-frontend_intranet.tar
```

如果需要离线启用 MGeo：

```bash
docker load -i address-normalizer-mgeo_intranet.tar
```

## Prepare Configuration

复制环境模板：

```bash
cp .env.intranet.example .env
```

至少需要修改这些配置：

- `POSTGRES_DB`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `DATABASE_URL`
- `DORIS_HOST`
- `DORIS_PORT`
- `DORIS_DATABASE`
- `DORIS_TABLE`
- `DORIS_USERNAME`
- `DORIS_PASSWORD`

`POSTGRES_*` 用于初始化数据库容器，`DATABASE_URL` 是 API 的运行时连接串；如果改了数据库用户名、密码或库名，需要同步修改 `DATABASE_URL`。

Doris 通过 MySQL 协议连接，常见端口是 `9030`。确认 `.env` 中保持：

```env
STANDARD_ADDRESS_SOURCE=doris
DORIS_ENABLED=true
```

如需启用 Qwen，还需要配置：

- `QWEN_BASE_URL`
- `QWEN_API_KEY`
- `QWEN_MODEL`

如需启用离线 MGeo：

```env
MGEO_ENABLED=true
```

## Start Services

不启用 MGeo：

```bash
docker compose -f docker-compose.intranet.yml up -d
```

启用离线 MGeo：

```bash
docker compose -f docker-compose.intranet.yml --profile mgeo up -d
```

## Update Existing Deployment

如果机器上已经部署过旧版本，建议在同一目录执行：

```bash
docker compose -f docker-compose.intranet.yml down
docker load -i postgis-16-3.4.tar
docker load -i address-normalizer-api_intranet.tar
docker load -i address-normalizer-frontend_intranet.tar
docker compose -f docker-compose.intranet.yml up -d --force-recreate
```

如果启用了 MGeo，也先执行：

```bash
docker load -i address-normalizer-mgeo_intranet.tar
docker compose -f docker-compose.intranet.yml --profile mgeo up -d --force-recreate
```

## Default Behavior

- `AUTO_INIT_DB=true`: API 启动时自动建表
- `AUTO_SEED_PUBLIC_POI=false`: 默认不导入测试 POI
- `RECALL_SCOPE_MODE=auto`: 默认从输入中自动提取城市/区县范围
- `CORS_ORIGINS=*`: 便于内网验收测试；正式安全要求更严格时可改成真实前端地址

## Verify

- Frontend: `http://<host>:5173`
- API docs: `http://<host>:8000/docs`
- Status: `http://<host>:8000/api/config/status`

推荐检查：

1. `standard_source` 为 `doris`
2. `standard` 为 `connected`
3. `database` 为 `configured`
4. `POI` 初始可为 `0`
5. 批量输入测试地址时，标准库命中正常返回

## Troubleshooting

- 如果 `standard=disconnected`，先从部署机器确认能访问 `DORIS_HOST:DORIS_PORT`
- 如果 PostgreSQL 启动失败，检查宿主机是否已有进程占用 `5432`
- 如果前端打不开，检查宿主机是否已有进程占用 `5173`
- 如果前端请求一直超时，先看 `docker logs address-normalizer-api` 中是否有 Doris 查询超时或网络错误
- 如果浏览器直连 API 被 CORS 拦截，临时保留 `CORS_ORIGINS=*`；正式环境再收紧
