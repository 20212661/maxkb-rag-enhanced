# Docker Compose 部署验收

## 当前状态

2026-11-13 完成全新 clone、隔离项目和空数据卷下的启动、烟测、扫描 PDF 异步解析、向量化及真实模型带引用问答。2026-11-14 完成 Docker 引擎恢复、容器停止再启动和示例重跑，知识库、文档及应用 ID 一致，所有常驻服务健康。

默认验收使用 MinerU Basic ONNX CPU。GPU profile 已通过 Compose 配置校验，状态为“未在 GPU 硬件上验证”。演示 GIF 由用户后续补充。

## 环境与源码

| 项目 | 实际记录 |
| --- | --- |
| 日期与时区 | 2026-11-13 至 2026-11-14，Asia/Shanghai |
| 宿主环境 | Windows + WSL Ubuntu，内核 6.18.33.2-microsoft-standard-WSL2 |
| WSL 资源 | 总内存约 6857 MiB，Swap 2048 MiB，首次检查根文件系统剩余约 948 GiB |
| 容器工具 | Docker Engine 29.1.3、Compose 2.40.3、Buildx 0.30.1，本次在 WSL 安装 |
| 克隆来源 | 本地 fork 工作仓库，执行 `git clone --no-hardlinks` |
| 初次目录 | `/tmp/maxkb-deploy-clean-20261113` |
| 重试及成功验收目录 | `/tmp/maxkb-deploy-retry-20261113` |
| 镜像构建与首轮通过的源码快照 | `4e3d72273`，后续 tmpfs 与 worker 健康检查使用 `e7a5665a0`，验收记录收尾合并进同一 feat 提交 |
| 成功验收项目 | `maxkb-final-20261113`，web 端口 8089，独立空数据卷 |
| 默认解析后端 | MinerU 4.1.7，Basic ONNX CPU |
| 回答模型 | DeepSeek Flash，通过默认 OpenAI-compatible provider 接入 |
| 凭据处理 | 新目录生成独立数据库、Redis、管理员密码与共享密钥，复用本机已保存回答模型的凭据写入被忽略的 `.env` |

README 扩充首屏采用单独的 docs 提交。部署相关迭代和本报告统一收敛为第二笔 feat 提交，表中的执行快照用于标识实测时的文件状态。

最终运行文件的 SHA-256 可直接对照交付文件核验。

| 文件 | SHA-256 |
| --- | --- |
| `compose.yaml` | `25764d6d46fb219950ea3b09f6a2c885cdb187e17ecbe72ce98fbb8ae8966bd7` |
| `installer/Dockerfile` | `c5c1782449a29f6b55cd864ecf679f8e3ccab93f06003a581e52cabe9535f258` |
| `demo-data/import_demo.py` | `0e81cf7cc7646f03b52b3003fd9d081e71b2226ecb5dfc01f7ecd8d1256f9a3d` |
| `services/mineru/Dockerfile` | `8d1d8f717a8b1e6ae5556fbdf841296d61aae3f190a328481aa3f940661cad20` |

## 从零验收过程

初次命令如下，随后在第二个新目录重试。`<本地仓库路径>` 为实际工作仓库位置的脱敏占位。

```bash
git clone --no-hardlinks "<本地仓库路径>" /tmp/maxkb-deploy-clean-20261113
cd /tmp/maxkb-deploy-clean-20261113
cp .env.example .env
python3 deploy/configure.py
# 在 .env 设置独立项目名、端口及真实回答模型
python3 deploy/verify.py
```

初次启动因镜像仓库连接超时失败。恢复镜像网络后，在 `/tmp/maxkb-deploy-retry-20261113` 重新 clone，完成基础镜像拉取与 MaxKB、MinerU 镜像构建。处理中分别执行过 `docker compose build maxkb-init` 和 `docker compose build mineru-api`，构建日志保存在该目录的 `tmp/`。

最终将项目名设置为 `maxkb-final-20261113`，为 PostgreSQL、Redis、MaxKB、静态文件与 MinerU 创建全新数据卷。此前的试运行使用其他项目名及数据卷。执行 `python3 deploy/verify.py`，脚本依次运行以下命令。

```bash
docker compose config --quiet
docker compose up -d --build --wait --wait-timeout 1800
docker compose exec -T web python deploy/smoke.py
docker compose run --rm demo-import
```

镜像构建时设置 `NODE_OPTIONS=--max-old-space-size=4096`。管理端与聊天端顺序构建，两次类型检查均通过。该构建日志中，前端安装及构建 RUN 阶段耗时 596.7 s，后端 Python 安装、编译消息与沙箱依赖 RUN 阶段耗时 226.5 s，两个阶段并行执行。

## 首轮成功结果与耗时

首轮通过开始于 2026-11-13 23:44:36，原始汇总为 `tmp/deploy-verification.json`，命令日志为 `tmp/deploy-verification.log`。

| 阶段 | 实测耗时 | 结果与范围 |
| --- | --- | --- |
| Compose 配置检查 | 0.108 s | 通过 |
| Compose 启动与健康等待 | 88.135 s | 通过，复用本次已构建镜像，数据卷为空，包含 Basic 模型下载、迁移和静态收集 |
| 基础烟测 | 42.402 s | 通过，包含首次本地向量请求与 MinerU 图片解析 |
| 示例导入与带引用回答 | 60.741 s | 通过，包含 Compose 依赖确认及导入脚本 |
| 完整成功轮次 | 191.394 s | 上述阶段的总耗时 |
| 导入脚本内上传、解析及向量化等待 | 42.228 s | 由脚本计时，包含管理 API 调用及异步轮询 |
| 解析任务开始至完成记录 | 30.907 s | 来自解析任务时间字段，包含轮询与结果处理 |
| 示例完整回答 HTTP 请求 | 1.946 s | 单次非流式请求，框架记录输入 1245、输出 195 token |

上述启动计时复用了已构建镜像。镜像、依赖下载及网络排查的耗时另见构建日志和失败记录。问答前的烟测已执行一次图片解析并加载本地向量模型，扫描 PDF 对应的解析缓存从本轮新任务生成。

| 核验项 | 实际结果 |
| --- | --- |
| 数据库 | 首次迁移通过，vector、pg_trgm 扩展存在 |
| Redis | 读写及清理测试通过 |
| 本地模型 | 返回有限的 768 维向量 |
| 前端静态服务 | 管理端、聊天端 HTML 及其 JavaScript 均由 web 返回，资源类型与内容检查通过 |
| 扫描文档 | `security-policy-scan.pdf`，7 页，pypdf 提取文字总数 0，来源哈希核对通过 |
| 异步任务 | `MinerUParseJob.state=succeeded`，存在上游任务 `job_31ee9cf5fe483ddbab83eb01` |
| 文档构建 | `MinerUDocumentBuild.parse_status=succeeded`，`embedding_pending=false` |
| 结果 | 10 个段落完成向量化，文档状态 `nnn2` |
| 发布绑定 | 最新发布版本的 `knowledge_ids` 包含本轮示例知识库 |
| 回答引用 | 保存的 `paragraph_list` 包含 `security-policy-scan.pdf` 及对应正文 |
| 8004 | 单个 Python / Uvicorn 进程，解析容量为 1 |

解析任务记录的开始时间为 2026-11-13 23:47:01.006840，完成时间为 23:47:31.913439。解析缓存创建于 23:47:31.902165，数据库中存在本轮异步文档构建记录。审计结果保存在验收目录的 `tmp/container-audit.json`。

首轮回答如下。

> 中卫市卫生和计划生育局公开的《信息安全管理制度》采用“**谁主管、谁负责**”的责任原则，并据此在局内建立**信息安全分级责任制**，由各层级落实信息系统安全责任。
>
> 来源：【security-policy-scan.pdf】中卫市卫生和计划生育局信息安全管理制度 / 第三章信息安全策略的基本要求 / 第七条。

本轮自动验收检查预期短语和来源文档，回答评测的 AI 评分状态仍按 README 所链接的原报告披露。

## 修复后重启与持久化

首次问答通过后，worker 日志暴露心跳目录缺失。通过 Compose 的 tmpfs 挂载提供 `/opt/maxkb-app/tmp`，并为两个 worker 增加就绪标记与心跳新鲜度健康检查。修复后执行 `docker compose up -d --no-build --wait`，全部常驻服务健康。

2026-11-14 恢复 Docker 引擎后，持久化容器自动恢复，示例问答再次通过。随后显式执行以下停止、启动和重跑命令。

```bash
docker compose stop
docker compose up -d --no-build --wait --wait-timeout 600
docker compose run --rm --no-deps demo-import
```

| 检查 | 结果 |
| --- | --- |
| 停止服务 | 7.491 s |
| 重新启动至全部健康 | 37.976 s |
| 重跑示例及回答 | 140.853 s，包含真实模型凭据验证，本轮模型验证请求约 137 s |
| 知识库 ID | `01a1d597-3b03-74f3-a052-4fd28315f303`，一致 |
| 文档 ID | `01a1d597-3e2e-7331-8022-dfb3ef7f1d8d`，一致 |
| 应用 ID | `01a1d597-e532-7151-a789-71f8fdf885f3`，一致 |
| 文档与引用 | 持久化保留，回答再次包含“谁主管、谁负责”及扫描文档来源 |
| worker 日志 | 修复后检查范围内无心跳目录异常 |

原始重启记录为 `tmp/restart-verification.json` 和 `tmp/restart-verification.log`。重跑使用既有解析与索引结果，延迟受到回答供应商及凭据验证请求影响。

## 遇到的问题与处理

- 本机初始缺少 Docker，已在 WSL 安装 Engine、Compose 与 Buildx。当前 WSL 使用手动启动的 dockerd，WSL 重启后需要先启动 Docker 引擎，项目启动前提见部署说明。
- Docker Hub、GHCR 初期出现 TLS EOF、DNS 地址异常和超时。配置引擎及 CLI 代理后完成下载，WSL 到 Windows 本地代理使用临时桥接。一次桥接连接池耗尽导致镜像层短读，修正桥接并重拉后完成。
- BuildKit 的认证请求需要 CLI 侧代理，构建容器的 apt、npm、uv 下载需要可从容器访问的代理地址。部署模板提供了构建代理变量。
- apt 下载出现一次 502，已为 MaxKB 镜像构建的 apt 步骤增加重试。CPU embedding 明确使用 CPU Torch 安装源。
- 框架 ENV 配置按字符串读取 DEBUG。部署使用框架的默认布尔关闭值，静态服务通过真实 HTTP 检查。
- worker 心跳依赖运行时临时目录，已通过 tmpfs 和 worker 健康检查修复。
- 发布版本列表接口省略知识库字段。导入检查发布返回的关联，实际聊天核对来源，数据库审计再次核对发布快照。
- 扫描 PDF 使用 ASCII 编码流，已为 `demo-data/*.pdf` 设置 binary 属性，全新 clone 中两份 PDF 的 SHA-256 与清单一致。

初次失败尝试的配置检查耗时 0.708 s，启动在 30.453 s 后因 Redis 镜像拉取超时退出，总耗时 31.162 s。后续失败和重试日志保留在两个验收目录的 `tmp/` 下。

## 检查范围

- 部署回归 7 项通过，覆盖随机凭据保留、配置补齐、解析等待、解析失败与回退、向量化失败、重复资源和发布绑定缺失。
- 部署 Python 与示例导入脚本 Ruff、Python 编译、Shell 语法检查通过。
- CPU 默认与 GPU 合并配置均通过 Docker Compose 原生配置检查，GPU 服务集合正确切换依赖。
- Mermaid 通过仓库现有 Mermaid 库的语法解析，README 本地链接检查通过。
- 前端管理端、聊天端容器内构建和类型检查通过，业务代码改动数为 0。
- 两份公开 PDF 的来源与哈希可追溯。真实凭据保存在被忽略的 `.env` 和运行数据库中。
- GPU 设备执行与演示 GIF 仍按前述状态记录。已有 WSL 开发服务的先行补充验证复用了旧解析缓存，正式空卷验收使用了本轮新解析任务。
