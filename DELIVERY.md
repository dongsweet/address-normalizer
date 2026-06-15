# Address Normalizer Intranet Delivery

这份文档只面向最终交付和内网部署，不包含本地联调、模拟 Hive 或公网测试说明。

## Delivery Package

最小交付建议只给这些文件：

- `address-normalizer-api_intranet.tar`
- `address-normalizer-frontend_intranet.tar`
- `postgis-16-3.4.tar`
- `docker-compose.intranet.yml`
- `.env.intranet.example`

只有在需要离线启用 MGeo 时，再额外提供：

- `address-normalizer-mgeo_intranet.tar`

## What Is Included

- `api`
  包含后端服务代码和样例 `data` 目录
- `frontend`
  包含前端静态文件，并通过 Nginx 反向代理 `/api`
- `db`
  使用 `postgis/postgis:16-3.4`
- `mgeo`
  可选。若交付 `address-normalizer-mgeo:intranet`，模型已预打包进镜像，可离线启动

这套交付包默认按 **Linux Docker host network** 运行，不再依赖 `api`、`db`、`mgeo` 这类容器名互联。
内网环境应直接连接真实 HiveServer2，不包含本地模拟 Hive。

## Import Images

```bash
docker load -i address-normalizer-api_intranet.tar
docker load -i address-normalizer-frontend_intranet.tar
docker load -i postgis-16-3.4.tar
```

如果需要离线启用 MGeo，再额外执行：

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
- `HIVE_HOST`
- `HIVE_PORT`
- `HIVE_DATABASE`
- `HIVE_TABLE`
- `HIVE_USERNAME`
- `HIVE_PASSWORD`

如需启用 Qwen，还需要配置：

- `QWEN_BASE_URL`
- `QWEN_API_KEY`
- `QWEN_MODEL`

如需启用离线 MGeo：

- `MGEO_ENABLED=true`

## Start Services

不启用 MGeo：

```bash
docker compose -f docker-compose.intranet.yml up -d
```

启用离线 MGeo：

```bash
docker compose -f docker-compose.intranet.yml --profile mgeo up -d
```

## Default Behavior

- `AUTO_INIT_DB=true`
  API 启动时会自动建表
- `AUTO_SEED_PUBLIC_POI=false`
  默认不导入测试 POI
- `RECALL_SCOPE_MODE=auto`
  默认按输入自动提取城市/区县范围

## Verify

- Frontend: `http://<host>:5173`
- API docs: `http://<host>:8000/docs`
- Status: `http://<host>:8000/api/config/status`

推荐检查：

1. `database` 为 `configured`
2. `hive` 为 `connected`
3. `POI` 初始可为 `0`
4. 批量输入测试地址时，标准库命中正常返回

## Notes

- `docker-compose.intranet.yml` 是最终交付使用的唯一 compose 文件
- `docker-compose.hive.yml` 只用于本地或测试环境模拟 Hive，不属于内网交付内容
- 前端默认监听 `5173`，并代理到 `127.0.0.1:8000`
- API 默认访问 `127.0.0.1:5432` 的 PostgreSQL，以及 `127.0.0.1:8090` 的 MGeo（若启用）
- 浏览器默认通过前端 `/api` 代理访问后端，通常不需要额外处理 CORS
