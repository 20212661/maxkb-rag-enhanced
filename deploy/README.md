# Docker Compose 部署

## 启动

在 Docker 引擎已经运行的 Linux 或 Docker Desktop 已启用集成的 WSL 中运行。本次验收另在 WSL 安装独立 Docker Engine，手动运行 `dockerd`，WSL 重启后先恢复引擎。

```bash
bash deploy/up.sh
```

入口脚本读取根目录 `.env`，补齐缺失配置，为留空或使用 `change-me` 的数据库密码、Redis 密码、共享密钥和初始管理员密码生成随机值，已有凭据会复用。也可以按根目录 README 的三步执行 `docker compose up`。默认网页端口为 8088，可编辑 `WEB_PORT` 和 `WEB_PUBLIC_URL`。

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
| demo-import | 按需执行的 demo profile，通过 HTTP 建库、发布应用并核验带引用回答 |

服务依赖通过健康检查和初始化任务完成状态排序，持久化存储包括 PostgreSQL、Redis、MaxKB 数据与静态资源、MinerU 模型及任务状态。Compose 的依赖语义见 [Docker 官方说明](https://docs.docker.com/compose/how-tos/startup-order/)。

MaxKB 容器通过 tmpfs 提供运行时临时目录，两个 worker 的健康检查同时核对就绪标记与近期心跳，示例导入等待 worker 健康后执行。

pgvector 镜像采用官方列出的 PostgreSQL 17 标签，具体标签固定在 Compose 文件中。镜像用法见 [pgvector Docker 说明](https://github.com/pgvector/pgvector#docker)。

## 资源

前端构建需要约 4 GiB Node 堆空间。解析服务的 10 页扫描件样本峰值 RSS 约 3.77 GiB，加上本地向量模型、web 和 worker 后，Docker 可用内存应覆盖整个服务栈。首次验收记录实际 Docker 分配和服务占用，数据见 [MinerU 实测](../services/mineru/README.md)。

## 配置与日常操作

根目录 `.env` 被 Git 和构建上下文忽略，`.env.example` 中的本地密码及共享密钥使用 `change-me` 占位，API Key 留空。首次启动前修改占位符，或运行 `deploy/configure.py` 自动生成随机值。脚本原子写入配置并保留已有设置。初始用户为 `admin`，`ADMIN_PASSWORD` 在首次数据库迁移时生效，后续修改管理员密码使用管理端。`ADMIN_USERNAME` 用于导入脚本登录，应与实际账号一致。

数据库、Redis、local_model、MinerU 和演示 HTTP 地址均可在 `.env` 配置。Compose 中的容器主机名通过默认网络解析。`EMBEDDING_CACHE_EPOCH` 用于模型升级后的缓存失效，`MINERU_MODEL_SOURCE` 默认 `modelscope`。`LLM_API_BASE`、`LLM_MODEL_NAME`、`LLM_API_KEY` 用于示例导入，默认接入 OpenAI-compatible provider，也可配置 `LLM_PROVIDER`。`DEMO_EMBEDDING_MODEL_ID` 默认选择迁移创建的本地模型。示例模型属于独立命名资源。

扫描件时间预算默认采用基础 120 秒加每页 30 秒，覆盖首次模型加载开销，原有业务的预算实现从部署环境变量读取。

需要代理的网络中，为 Docker Engine 配置镜像拉取代理，并给执行 Compose 的终端设置 `HTTP_PROXY`、`HTTPS_PROXY`，供 BuildKit 的认证请求使用。构建容器内的依赖下载代理由 `.env` 中的 `BUILD_HTTP_PROXY`、`BUILD_HTTPS_PROXY`、`BUILD_NO_PROXY` 配置，地址应能从构建容器访问。WSL 的 localhost 与 Windows 代理监听地址是否互通取决于本机网络模式。 [Docker 代理说明](https://docs.docker.com/engine/daemon/proxy/)

```bash
docker compose ps
docker compose logs --tail 100 web worker-mineru mineru-content
docker compose exec -T web python deploy/smoke.py
docker compose run --rm demo-import
docker compose stop
docker compose up -d --wait
```

入口脚本的验收检查数据库扩展及迁移、Redis 读写、本地 768 维向量、MinerU 图片解析和 Celery ping。完整示例验收使用 `demo-import`，包含原始 PDF 上传及真实 LLM 回答。

## 可选 GPU profile

验证状态为“未在 GPU 硬件上验证”，已通过 Compose 配置校验。

默认部署使用 Basic ONNX CPU。Linux / WSL 下的 NVIDIA GPU 部署需要宿主机 NVIDIA 驱动和 NVIDIA Container Toolkit，并在 `.env` 设置以下配置。合并文件使用 `!override`，需要 Docker Compose 2.24.4 或更新版本。

```dotenv
COMPOSE_FILE=compose.yaml:compose.gpu.yaml
COMPOSE_PROFILES=gpu
MINERU_TASK_API_URL=http://mineru-api-gpu:8000
MINERU_GPU_DEVICE_ID=0
```

然后执行相同的启动命令。

```bash
docker compose up -d --build --wait --wait-timeout 1800
```

GPU 服务显式选择 `MINERU_MODEL_ONNX_DEVICE=gpu`，使用 ONNX/WebGPU，小模型与默认部署共用 Basic 权重，解析容量仍为单并发。覆盖文件将 CPU 上游移入 cpu profile，并将文件服务依赖切换到 GPU 服务。8004 封装继续单进程启动。设备与驱动兼容性依赖宿主机，验收报告记录实际覆盖范围。 [MinerU 运行时说明](https://opendatalab.github.io/MinerU/usage/tiers/) [Docker GPU 配置](https://docs.docker.com/compose/how-tos/gpu-support/)

## 全新目录验收

在全新 clone、独立项目名与全新数据卷中运行以下命令。脚本记录启动、基础烟测和示例问答的耗时与结果，输出至被忽略的 `tmp/deploy-verification.json`。

```bash
python3 deploy/verify.py
```

启动耗时包含本次镜像拉取、构建、模型下载和健康等待，分别记录问答验收阶段耗时。若启动失败，结果中的 `status` 和 `failed_step` 保留失败阶段，日志记录在 `tmp/deploy-verification.log`。完成后可执行 `docker compose stop`、`docker compose up -d --wait` 并重跑导入，核验持久化恢复。

实际运行环境、从零启动结果与已知问题见 [部署验收记录](../docs/deploy-verification.md)。
