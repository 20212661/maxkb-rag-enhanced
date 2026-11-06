# MinerU 独立服务与客户端

## 部署

已在 WSL Ubuntu 的独立环境 `/opt/mineru4/venv` 安装 MinerU 4.1.7。使用 Python 3.11.18、ONNX CPU、Basic 档位，解析并发为 1。主机为 Ryzen 7 5800H，8 核 16 线程，Windows 物理内存约 13.86 GiB，WSL 分配约 6.7 GiB。MaxKB 与 MinerU 在测试期间同时运行。

| 服务 | 地址 | 用途 |
| --- | --- | --- |
| API Server | `http://localhost:8000` | `/v1/health`、`/v1/tiers`、`/v1/tasks` |
| File Parse Server | `http://localhost:8003` | 同步 `/v1/file_parse` 与请求恢复 |
| Content List Tasks | `http://localhost:8004` | 异步提交、状态查询、Content List JSON |
| API 文档 | `http://localhost:8000/docs`、`http://localhost:8003/docs` | 当前安装版本的 OpenAPI |

模型通过 ModelScope 下载并经过 `models verify --tier basic` 校验，位于 `/opt/mineru4/runtime/models`。运行时配置为 `source=local`、`small_backend=onnx`、`onnx_device=cpu`。两项服务均绑定 `127.0.0.1`，单文件包装服务连接本机解析服务。服务环境设置 `OMP_NUM_THREADS=4`、`OPENBLAS_NUM_THREADS=1`。依赖版本固定于 `requirements.lock`，模型文件哈希及运行环境见 `results/environment.json`。

## 文件

- `install.sh` 创建独立环境，按固定依赖安装、下载并校验 Basic 模型。
- `env.sh` 统一模型目录、CPU 设置与服务环境。`config.yaml` 是 MinerU 官方配置。
- `service.sh` 管理三个服务，提供 `start`、`stop`、`status`，启动时检查端口及健康状态，停止前验证 PID 所属命令，服务命令使用文件锁防止同时执行。
- `benchmark.py` 实际调用 curl 上传文件，记录 HTTP 总耗时、响应头、请求 ID、输出、Linux 进程树 RSS/PSS 和内存采样轨迹。
- `samples/manifest.json` 固定样本哈希、来源和原页映射。扫描 PDF 复用前一周 `eval/assets/corpus/scan_pages.pdf`，共 10 页。图片来自公开安全漫画的第 1 页。
- `results/benchmark.json` 记录先扫描 PDF 两次、再图片两次的连续测试。
- `results/image-fresh-benchmark.json` 记录重启服务后单独对图片测试两次。
- `results/run-*/` 在本机保存每次请求、测量结果、Markdown、Middle JSON、Structured Content、Content List、导出图片及 `memory.csv`。Git 忽略运行结果子目录、图片、PDF 样本和模型目录，只保留小型测量 JSON。
- `prepare_business_sample.py` 从已版本化的评测语料恢复 Day 1 图片，并生成新增的 7 页业务制度模拟扫描。首次检出后、执行文件测试前运行该脚本。

## 实测

本地时间 2026-11-06 完成共 6 次请求，均为 HTTP 200，实际解析档位均为 `basic`，模式均为 `ocr`。结果目录的时间戳使用 UTC。

| 场景 | 页数 | HTTP 耗时 | 进程树峰值 RSS | 进程树峰值 PSS |
| --- | ---: | ---: | ---: | ---: |
| 扫描 PDF，服务首次请求 | 10 | 72.896 s | 3616.61 MiB | 3508.02 MiB |
| 扫描 PDF，第二次请求 | 10 | 57.399 s | 3856.31 MiB | 3761.76 MiB |
| 图片，在 PDF 之后，第 1 次 | 1 | 3.707 s | 3775.41 MiB | 3680.63 MiB |
| 图片，在 PDF 之后，第 2 次 | 1 | 3.687 s | 3816.64 MiB | 3722.10 MiB |
| 图片，重启服务后的首次请求 | 1 | 9.501 s | 1058.11 MiB | 985.24 MiB |
| 图片，重启服务后的第二次请求 | 1 | 5.194 s | 1243.35 MiB | 1170.24 MiB |

HTTP 耗时从 curl 上传开始，到完整 ZIP 下载结束，包含包装服务内部轮询与导出，排除客户端解压和 Content List 转换。每次使用新的 `request_id`，每次实际执行解析。首个请求包含延迟模型加载，模型下载已提前完成。

内存为 API Server、File Parse Server 及其子进程总量，按约 200 ms 间隔采样。RSS 会重复统计共享驻留页，PSS 按共享比例分摊。采样时刻见每次的 `memory.csv`，它们代表观测峰值。测试中的服务进程树峰值换出量均为 0。扫描 PDF 最大 RSS 约 3.77 GiB，PSS 约 3.67 GiB，后续容量规划应采用这个样本实测值。

PDF 后的图片测试继承了 PDF 模型和内存缓存，报告中的图片重启测试用于观察单独图片的加载成本。两组图片热运行耗时存在差异，后续路由计时还需使用更多样本，按文件页数、尺寸和服务冷热状态区分统计。

## 输出检查

扫描 PDF 首次输出位于 `results/run-20261105T160205Z/scan_pdf-1/`。

- Markdown 共 6480 字符。
- Content List 共 182 块，包含 `header`、`text`、`footer`、`page_number`、`table` 和 `image`。
- `page_idx` 完整覆盖 0–9。对应原公开文档的页码映射见样本清单。
- 导出 2 张图片，分别为第 9 个输入页中的表格区域和第 10 个输入页中的图示。
- 扫描页中的安全检查周期、资产清单和授权审批等文字已恢复为可检索文本。

图片首次输出位于 `results/run-20261105T160205Z/image-1/`。

- Markdown 共 254 字符。
- Content List 共 11 块，6 个文本块和 5 个图片块，`page_idx=0`。
- 页面标题和安全建议被提取为文字，导出 5 张图片。
- 漫画中的对白区域仍保留在图片块内。Basic 对这种图内文字的恢复范围需要在后续质量评测中单列。

4.1 默认 HTTP ZIP 包含 `markdown.md`、`middle_json.json`、`structured_content.json` 和 `images/`。本工程从返回的 Middle JSON 通过官方 `render(..., RenderFormat.CONTENT_LIST)` 再生成 `content_list.json`，未重复解析文档。Content List 含块类型、零基页码和归一化 `bbox`。实际 API 产物及转换文件均保留，便于后续适配器使用。

## 安装与服务管理

在项目根目录的 WSL 终端中执行。全新环境需要 uv、Python 3.11、curl 和 flock，安装目录需可写。

```bash
bash services/mineru/install.sh
/opt/mineru4/venv/bin/python services/mineru/prepare_business_sample.py
bash services/mineru/service.sh start
bash services/mineru/service.sh status
bash services/mineru/service.sh stop
```

当前机器从 PowerShell 启动服务可执行下列命令。

```powershell
wsl -d Ubuntu -- bash services/mineru/service.sh start
wsl -d Ubuntu -- bash services/mineru/service.sh status
```

`MINERU_ROOT` 默认 `/opt/mineru4`，`MINERU_HOME` 默认其 `runtime/` 子目录。可通过同名环境变量指定其他位置。服务日志位于 `$MINERU_HOME/logs/`，PID 文件位于 `$MINERU_HOME/run/`。下载源由 `MODEL_SOURCE` 指定，默认 `modelscope`。uv 和 Python 路径可由 `UV_BIN`、`PYTHON_BIN` 指定。安装使用 `uv --no-config`，避免读取 MaxKB 的 uv 项目索引配置。

本机安装验证使用过下列命令。

```powershell
wsl -d Ubuntu -- env UV_BIN=/root/.local/bin/uv PYTHON_BIN=/opt/maxkb-dev/venv/bin/python UV_DEFAULT_INDEX=https://mirrors.aliyun.com/pypi/simple bash services/mineru/install.sh
```

## curl 复现

以下在项目根目录的 WSL Bash 中执行。同步成功返回 ZIP 字节。HTTP 202 返回可恢复的状态 JSON，可用已保存的请求 ID 查询。

```bash
curl --fail-with-body --max-time 3700 \
  -D /tmp/scan.headers -o /tmp/scan.zip -w '%{http_code}\n' \
  http://127.0.0.1:8003/v1/file_parse \
  -F 'file=@eval/assets/corpus/scan_pages.pdf' \
  -F 'request_id=manual-scan-001' \
  -F 'tier=basic' -F 'ocr_mode=ocr' \
  -F 'output_formats=default' -F 'wait_timeout=3600'

curl --fail-with-body --max-time 3700 \
  -D /tmp/image.headers -o /tmp/image.zip -w '%{http_code}\n' \
  http://127.0.0.1:8003/v1/file_parse \
  -F 'file=@services/mineru/samples/security-poster.jpg' \
  -F 'request_id=manual-image-001' \
  -F 'tier=basic' -F 'ocr_mode=ocr' \
  -F 'output_formats=default' -F 'wait_timeout=3600'

curl http://127.0.0.1:8003/v1/file_parse/manual-scan-001
```

相同请求 ID、同一文件及同一组选项用于恢复已有请求。更换输入或选项需要新 ID。使用 `/v1/file_parse/{request_id}/result` 重新下载已完成结果。请求 ID、任务 ID 和状态以实际响应为准。

完整计时与内存测试可执行下列命令，输出另存为新实验，保留 Day 1 原始记录。

```bash
/opt/mineru4/venv/bin/python services/mineru/benchmark.py \
  --output services/mineru/results/recheck.json
```

## 后续路由的版本约束

MinerU 4.1 的 `/v1/file_parse` 位于单独包装服务，`/v1/tasks` 位于解析服务。Basic OCR 用于 PDF 和图片。原生 PPT、Word、Excel 等 Office 文件在 MinerU 中实际使用 Flash，即使请求填写 `tier=basic`。PPT 中嵌入的扫描图需要在后续路由中另外设计图片提取或转 PDF 的处理路径。

Day 1 性能实测包括扫描 PDF 与 JPG。卡 2.2 另用 2 页 PPTX 文本夹具验证 Flash 提取，其他业务 PPT 和独立复杂表格的性能、准确性将按对应样本记录。

## 卡 2.2 MinerUClient

MaxKB 客户端位于 `apps/common/utils/mineru_client.py`，依赖项目已有的 `requests`、`pypdf` 和标准库。8004 的 `content_list_api.py` 负责官方结果转换与图片字节编码，在 MinerU 独立环境运行。MaxKB 只访问该封装服务。

上游地址分别配置为 `MINERU_TASK_API_URL=http://127.0.0.1:8000` 和 `MINERU_FILE_PARSE_URL=http://127.0.0.1:8003`，由 `env.sh` 提供默认值。前者用于文件包装服务的任务上游，后者用于 Content List 服务的上传与恢复。三个服务的健康状态由启动脚本检查。

| MaxKB 环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| `MAXKB_MINERU_CONTENT_LIST_URL` | `http://127.0.0.1:8004` | 唯一客户端服务入口 |
| `MAXKB_MINERU_TIMEOUT_BASE_SECONDS` | `30` | 基础时间预算 |
| `MAXKB_MINERU_TIMEOUT_PER_PAGE_SECONDS` | `10` | 每页增加的时间预算 |
| `MAXKB_MINERU_CONNECT_TIMEOUT_SECONDS` | `5` | 单次连接等待上限 |
| `MAXKB_MINERU_POLL_INTERVAL_SECONDS` | `1` | 状态轮询间隔 |

使用项目已有的 ENV 配置模式时加载这些变量。时间预算为基础秒数加每页秒数乘以页数，PDF 使用 `pypdf` 计页，普通图片按 1 页，PPTX 读取压缩包内的 slide XML 数量。二进制 `.ppt` 需要调用者显式提供准确的 `page_count`，第一版执行 Flash 文本提取。

```python
import uuid
from common.utils.mineru_client import MinerUClient

# 在后台任务中执行，并在提交之前保存 request_id。
request_id = uuid.uuid4().hex
with MinerUClient() as client:
    receipt = client.submit(local_path, request_id=request_id)
    # 将 request_id、task_id、page_count 持久化到任务记录。
    result = client.wait(receipt["request_id"], receipt["page_count"])
```

`submit()` 上传并返回 HTTP 202 收据。8004 固定向 8003 发送 `wait_timeout=0`，模型推理在官方任务服务中继续执行。`wait()` 按页数预算轮询，完成后取得 Content List。后台便捷方法 `parse()` 将提交、轮询、结果下载放在同一个总时间预算内。分阶段调用时可向 `wait(timeout_seconds=...)` 传入剩余预算。

客户端网络超时会抛出带 `request_id` 的 `MinerUTimeoutError`。连接失败、上游失败、取消及 `submission_unknown` 均显式返回异常。创建请求不会自动重试，调用者可保存请求 ID 后用 `wait()` 恢复。HTTP 200 且任务状态为 `failed` 会按失败处理。完整文档页数和 Content List 块类型、页码也会核验。

8004 提供以下接口。

| 方法 | 路径 | 返回 |
| --- | --- | --- |
| POST multipart | `/v1/content-list/tasks` | HTTP 202，`request_id`、`task_id`、状态 |
| GET | `/v1/content-list/tasks/{request_id}` | 原任务状态 |
| GET | `/v1/content-list/tasks/{request_id}/result` | Content List JSON 与图片资产 |

结果包含 `schema_version=1`、`request_id`、`task_id`、`content_list`、`markdown`、`images`、`page_count`、`is_full_document`、`metadata` 和实际 `parse_info`。图片按相对路径存放在 `images` 映射中，含 `mime_type` 和 `data_base64`，与块中的引用一一保留。封装服务只做下载、官方渲染和编码。结果 ZIP 默认最多 128 MiB，解包总量默认最多 256 MiB，可配置 `MINERU_MAX_RESULT_BYTES`、`MINERU_MAX_UNPACKED_BYTES`。转换并发为 1。

## 业务扫描件补测与回归验证

新增中卫市公开信息安全管理制度的 7 页模拟扫描，使用每页长边 1800 像素，源文件哈希及转换参数见 `samples/business_policy_scan.provenance.json`。它与原先 10 页制度样本均为公开文件的受控栅格化扫描。

本次通过 MaxKB 独立环境中的客户端实测，异步提交约 0.684 秒返回，端到端总耗时约 47.643 秒，7 页时间预算为 100 秒。输出 3264 字符，84 个文本块、7 个页码块，图片块为 0。预先从原文选定的 10 条关键句全部恢复。具体记录见 `results/business-scan-client.json`。

已有的 10 页制度样本共 182 块。表单同时保留图片和 HTML 表格文字，一张流程图仍只有图片，其 7 个文字标签未进入图块文本字段。人工核验记录见 `results/business-quality-audit.json`。正文与图内文字分开评估，后续视觉能力实验可针对这类业务图示。

真实接口还验证了客户端超时后使用相同请求 ID 取回原任务，以及 2 页 PPTX 的 Flash 文本提取。核验记录见 `results/client-integration-checks.json`，MaxKB 测试进程未加载 `mineru` 包。

```bash
PYTHONPATH=apps /opt/maxkb-dev/venv/bin/python -m unittest common.test_mineru_client -v
/opt/mineru4/venv/bin/python -m unittest discover -s services/mineru -p test_content_list_api.py -v
```

## 卡 2.4 接入约束

上传请求完成落盘及排队，Celery 后台任务再调用本客户端。使用独立 `mineru` 队列和并发 1 的 worker，避免多个任务同时等待解析并占用按页计算的预算。MinerU 官方 API 的解析并发也固定为 1，依据是 10 页扫描样本约 3.77 GiB 的服务进程树峰值 RSS。

后台任务在调用前持久化请求 ID，并在收到提交回执后记录上游任务 ID，重试时先查原任务。解析缓存将在该卡实现，以文件哈希、引擎和模型版本、档位、OCR 选项、输出协议组成缓存身份，完成结果原子发布。当前 57.4 秒是热模型重解析成本，缓存命中的实际净收益需要用缓存实验记录。

第 1 周的 `eval/results/baseline.json` 已有完整 60 题结果，Recall@5 为 56.67%，接口错误 0，题集与评测代码哈希已再次核验。后续检索对比继续使用该固定基线。

## 官方依据

- 安装和版本要求见 [Quick Start](https://opendatalab.github.io/MinerU/quick_start/)。
- 同步服务及恢复协议见 [Synchronous File Parse](https://opendatalab.github.io/MinerU/usage/file_parse/)。
- 任务上传、查询与结果下载见 [Tasks API](https://opendatalab.github.io/MinerU/usage/tasks/)。
- 档位和 Office 实际处理路径见 [Tiers and Runtimes](https://opendatalab.github.io/MinerU/usage/tiers/)。
- 默认 ZIP 与官方 Content List 渲染接口见 [Output File Format](https://opendatalab.github.io/MinerU/reference/output_files/)。
