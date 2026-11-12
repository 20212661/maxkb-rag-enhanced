# Docker Compose 部署

## 启动

在 Linux 或 Docker Desktop 已启用集成的 WSL 中运行。

```bash
bash deploy/up.sh
```

入口脚本生成并保留 `deploy/.env` 中的数据库密码、Redis 密码和进程共享密钥，后续启动复用这些配置。默认网页端口为 8088，与本机开发服务的 8080 区分。可编辑 `WEB_PORT` 后重新启动。

第一次运行会下载镜像、Python/Node 依赖和 MinerU Basic 模型。前端构建为每个 Node 进程设置 4 GiB 堆，管理端与聊天端按顺序构建。依赖和模型下载完成后的启动流程复用镜像及数据卷。

## 服务

| 服务 | 用途 |
| --- | --- |
| postgres | PostgreSQL 17 + pgvector，初始化 vector、pg_trgm 扩展 |
| redis | Celery broker、缓存和分布式锁，开启 AOF |
| maxkb-init | 单次数据库迁移与静态资源收集 |
| web | 当前仓库代码的 Gunicorn 管理端与聊天 API |
| local-model | 随 MaxKB 基础镜像提供的本地向量模型 |
| worker | celery、model 队列，并发 2 |
| worker-mineru | mineru 队列，并发 2 |
| mineru-models | 单次下载并校验 MinerU Basic ONNX 模型 |
| mineru-api | MinerU 任务服务，模型解析并发 1 |
| mineru-file | 官方同步包装及请求恢复接口 |
| mineru-content | Content List 转换包装，生命周期信号量并发 1 |

服务依赖通过健康检查和初始化任务完成状态排序，持久化存储包括 PostgreSQL、Redis、MaxKB 数据与静态资源、MinerU 模型及任务状态。Compose 的依赖语义见 [Docker 官方说明](https://docs.docker.com/compose/how-tos/startup-order/)。

pgvector 镜像采用官方列出的 PostgreSQL 17 标签，具体标签固定在 Compose 文件中。镜像用法见 [pgvector Docker 说明](https://github.com/pgvector/pgvector#docker)。

## 资源

前端构建需要约 4 GiB Node 堆空间。解析服务的 10 页扫描件样本峰值 RSS 约 3.77 GiB，加上本地向量模型、web 和 worker 后，Docker 可用内存应覆盖整个服务栈。首次验收记录实际 Docker 分配和服务占用，数据见 [MinerU 实测](../services/mineru/README.md)。

## 配置与日常操作

`deploy/.env` 由脚本创建且被 Git 和构建上下文忽略。`EMBEDDING_CACHE_EPOCH` 用于模型升级后的缓存失效，`MINERU_MODEL_SOURCE` 默认 `modelscope`。LLM 凭据在登录后的模型管理中配置。

```bash
docker compose --env-file deploy/.env ps
docker compose --env-file deploy/.env logs --tail 100 web worker-mineru mineru-content
docker compose --env-file deploy/.env exec -T web python deploy/smoke.py
docker compose --env-file deploy/.env stop
docker compose --env-file deploy/.env up -d --wait
```

入口脚本的验收检查数据库扩展及迁移、Redis 读写、本地 768 维向量、MinerU 图片解析和 Celery ping。

## 验证状态

2026-11-13 已完成 Compose 官方 JSON Schema 校验、11 个服务依赖引用检查、Shell 语法检查、配置生成与重试保留测试、Python Ruff 检查。MaxKB 基础镜像和向量模型镜像的 GHCR manifest 均返回 HTTP 200。

本机 Docker Desktop 的 WSL 集成正在由用户启用，当前 Windows/WSL 尚未提供 Docker 命令。完整镜像构建、容器启动、烟测和重启持久化测试等待运行环境就绪，状态在实测后更新。
