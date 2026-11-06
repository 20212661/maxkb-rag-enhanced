# MinerU 知识库解析字段契约 v1

## 状态与实施顺序

本文件冻结知识库上传链路的 v1 字段与行为。8004 的 HTTP 协议已由现有服务实现，路由、转换器、缓存、占位保存和 Celery 编排将在后续步骤按本契约实现。

实施顺序为字段文档、转换器、路由与探测、模型与服务、预览路径、保存分流与 Celery 任务、前端。每步单独提交并运行相关回归。

| 范围 | v1 行为 |
| --- | --- |
| 分流入口 | `DocumentSerializers.Split.file_to_paragraph` 显式调用 `mineru_router.decide()` |
| MinerUSplitHandle | 实现 `support`、`handle`、`get_content`，由知识库入口显式调用 |
| 共享 handler 列表 | 保持现有原生处理器列表 |
| 原生处理器 | 复用现有实现，回退时直接调用对应原生处理器 |
| ZIP | 外层文件及成员沿现有原生路径处理，优先于知识库强制路由 |
| 工作流 document_extract_node | 沿现有原生路径处理 |
| 复杂表格 | 知识库级显式强制路由 |
| 前端 | 文件白名单和解析状态文案 |
| v2 | 占位预览锁定、重试按钮、ZIP 成员接入 |

## 一、文件身份、页码与版本

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `source_file_id` | UUID 字符串 | MaxKB 已保存的原始文件记录，由后端校验归属 |
| `sha256` | 64 位小写十六进制字符串 | 原始上传字节的 SHA-256，使用 `File.sha256_hash` 的同一口径 |
| `parse_job_id` | UUID 字符串 | MaxKB 持久化解析任务的身份 |
| `request_id` | 字符串 | 8004 的恢复标识，提交前生成并持久化，允许 1–64 位字母、数字、下划线和连字符 |
| `task_id` | 字符串或 null | MinerU 上游任务 ID，提交获得确认后保存 |
| `page_idx` | 非负整数 | Content List 中从 0 开始的源文档页索引 |
| `page_count` | 正整数 | 返回结果的文档页数，含没有正文块的空白页 |
| `block_index` | 非负整数 | Content List 数组中的位置，从 0 开始，用于转换溯源 |
| `schema_version` | 整数 | 8004 JSON 信封版本，当前为 1 |
| `converter_version` | 字符串 | MaxKB 转换规则版本，首版为 `v1` |

`parse_job_id`、`request_id` 和 `task_id` 分别标识三层记录。服务端按知识库权限、原件及哈希确认绑定关系。前端携带的标识用于关联和展示，任务执行依据持久化记录。

PDF 源页数使用 `pypdf` 读取，普通单帧图片按 1 页，PPTX 按 slide XML 数量计页。二进制 PPT 的当前客户端需要显式提供准确页数。强制路由的其他 Office 文件使用可配置的文档时间预算，实际输出页数以服务返回值为准。预算单位与实际页数分别记录，完整页数一致性校验用于能够准确计页的输入。

## 二、路由与 PDF 探测

路由规则位于独立的 `mineru_router.py`。输入包括文件名、可重复读取的原始字节、知识库配置和全局阈值。输出包含引擎、原因、实际档位、预算信息和探测值。

### 路由优先级

1. ZIP 返回 `native`，成员继续由 ZIP 原生列表处理。
2. 知识库的 `Knowledge.meta.mineru_route` 使用 `auto`、`force_mineru`、`force_native`，缺省为 `auto`。强制原生直接返回 `forced`。强制 MinerU 对支持格式生效，返回 `forced`，不支持的格式返回原生及 `forced_unsupported`。该配置由具有知识库管理权限的后端配置或管理接口设置。
3. PNG、JPG、JPEG、WEBP、BMP 交给 MinerU Basic。PPTX、PPT 交给 MinerU Flash 文本提取。
4. PDF 按下述两个条件进行判断，任一条件成立即交给 MinerU Basic。
5. 其他情况沿原有原生处理循环处理。DOCX、XLSX、XLS 可由知识库强制路由交给 Flash。TXT、MD、CSV 等格式强制 MinerU 时返回 `forced_unsupported`。

扩展名判断不区分大小写。强制 MinerU 的 PDF 仍校验可读性、加密标记和非零页数，校验成功后跳过文字抽取。加密 PDF 即使使用空密码也返回 `probe_failed`。

复杂表格的首版入口为知识库级强制路由。自动判断表格复杂度和逐文件手工切换列入后续扩展。

### 探测口径

对每页 `extract_text()` 的结果清理 NUL，按 Unicode 空白规则去掉空白字符，再统计字符数。页眉、页脚及页码的可提取文字计入该页。图片中文字由此步骤得到的计数为 0。

最多 30 页时全量探测。超过 30 页时取前 10 页，再从剩余页均匀取 20 页，包含最后一页，索引去重排序。`page_count` 为总页数，`probed_pages` 为实际完成探测的零基页索引。以下 `N` 表示实际完成的探测页数，第 i 个探测页字符数为 `c_i`。抽样与软预算提前终止时，指标均以完成页为分母。

```text
total_non_whitespace_chars = sum(c_i)
avg_chars_per_page = total_non_whitespace_chars / N
low_text_page_count = count(c_i < low_page_char_threshold)
low_text_page_ratio = low_text_page_count / N

use_mineru = (
    avg_chars_per_page < avg_char_threshold
    or low_text_page_ratio >= low_page_ratio_threshold
)
```

| 配置键 | 默认值 | 取值要求 |
| --- | --- | --- |
| `MINERU_SCAN_AVG_CHAR_THRESHOLD` | `100` | 非负有限数 |
| `MINERU_SCAN_LOW_PAGE_CHAR_THRESHOLD` | `50` | 非负有限数 |
| `MINERU_SCAN_LOW_PAGE_RATIO_THRESHOLD` | `0.30` | 0–1 的有限数 |
| `MINERU_PDF_PROBE_TIMEOUT_SECONDS` | `5` | 正有限数，逐页检查的软预算 |

ENV 模式下使用 `MAXKB_` 前缀。判定使用原始统计值，日志中的格式化数值用于展示。探测结束恢复文件读取位置，供后续原生处理或任务提交复用。

| 边界 | 结果 |
| --- | --- |
| 平均每页 99 字 | 命中平均字符条件 |
| 平均每页恰好 100 字 | 继续检查低文字页比例 |
| 某页 49 字 | 计为低文字页 |
| 某页恰好 50 字 | 计为正常文字页 |
| 10 页中 3 页低于 50 字 | 命中 30% 条件 |
| 平均 100 字且低文字页比例 20% | 原生路由 |

零页、加密、损坏或文字提取异常产生 `probe_failed` 原因，并交给原生 PDF 处理器。探测异常单独记录，区分文字计数为 0 的扫描件。

`probe_pdf(file, *, config=None)` 在打开 PDF 前开始计时，每完成一页检查已用时间，超过软预算即停止并返回已完成页的指标。读取元数据或开始下一页前耗尽预算且没有完成页时抛出 `PDFProbeError`。单页完成后即使超过预算，该页仍计入统计。文件读取位置在成功、失败和提前终止路径均通过 `finally` 恢复。已知限制为单页文字提取卡死无法中断，软预算不保证整个请求在 5 秒内返回。

路由日志记录文件名和完整 `RouteDecision`，探测日志另记录 `chars_per_page`、生效阈值、耗时及抽样标记。字符总量和低文字页数量可由逐页计数复核。上传入口接入时补充原件、知识库及哈希身份。正文、图片 Base64 及凭据不进入日志。跳过 PDF 探测的分支使用 null 表达缺省探测值。

`decide(file, knowledge_meta, *, config=None)` 返回 `RouteDecision`，含 `use_mineru`、`tier`、`reason`、`metrics`。`tier` 为 `basic`、`flash` 或 null。`metrics` 含 `avg_chars_per_page`、`low_text_page_ratio`、`page_count` 和 `probed_pages`。跳过文字探测时统计值为 null，完成页列表为空。强制 MinerU 的 PDF 可读性校验返回实际页数。

`reason` 使用稳定代码，包括 `zip_native_only`、`forced`、`forced_unsupported`、`image_ext`、`ppt`、`pptx`、`low_text_avg`、`low_text_ratio`、`probe_failed` 和 `native_default`。两个 PDF 条件同时成立时主原因取 `low_text_avg`，日志同时保留两个条件的命中情况。`config=None` 延迟读取 `CONFIG`，测试可显式传入配置字典。

校准命令为 `python scripts/calibrate_router.py eval/assets/corpus services/mineru/samples --output docs/router-calibration.md`。目录按参数顺序分别成节，递归处理全部 PDF，报告包含实际抽样页、原因、独立的预期路由及标注依据。预期值由已有清单、评测文字核验记录、来源记录或明确文件名推断，未知值留空。末尾分别汇总电子版误送 MinerU 和扫描／图片 PDF 误送原生的清单。

## 三、现有 8004 HTTP 契约

| 服务 | 默认地址 | 职责 |
| --- | --- | --- |
| 任务服务 | `http://127.0.0.1:8000` | 实际解析任务 |
| 单文件包装服务 | `http://127.0.0.1:8003` | 文件提交、请求恢复、ZIP 结果 |
| Content List 服务 | `http://127.0.0.1:8004` | 异步收据、状态和 Content List JSON |

8000 与 8003 分别使用 `MINERU_TASK_API_URL`、`MINERU_FILE_PARSE_URL` 配置。MaxKB 的 `MinerUClient` 通过 `MAXKB_MINERU_CONTENT_LIST_URL` 访问 8004。官方结果转换在 MinerU 独立环境执行。

### 提交及状态

`POST /v1/content-list/tasks` 使用 multipart，字段为 `file`、`request_id`、`tier`、`ocr_mode`。8004 向 8003 固定提交 `wait_timeout=0`，对调用者返回 HTTP 202 收据。

```json
{
  "request_id": "preview-6d5f55d0",
  "task_id": "job_example",
  "status": "running",
  "error": null
}
```

`task_id` 在尚未确认上游接受时可为 null。`GET /v1/content-list/tasks/{request_id}` 返回同形态状态对象。处理中状态包括 `prepared`、`submitting`、`waiting-file`、`pending` 和 `running`，成功状态为 `completed`。`failed`、`canceled`、`submission_unknown` 进入显式异常处理。

HTTP 200 表示状态查询成功，解析结果由 `status` 判断。网络超时后使用已保存的请求 ID 查询原任务，创建请求由调用者控制重试。

### 结果信封

`GET /v1/content-list/tasks/{request_id}/result` 在成功后返回 JSON。

```json
{
  "schema_version": 1,
  "request_id": "preview-6d5f55d0",
  "task_id": "job_example",
  "content_list": [
    {"type": "text", "text": "信息安全制度", "text_level": 1, "page_idx": 0, "bbox": [100, 80, 900, 140]},
    {"type": "text", "text": "重要数据应定期备份。", "page_idx": 0, "bbox": [100, 160, 900, 240]},
    {"type": "image", "img_path": "images/page_0_image_2.jpg", "image_caption": [], "image_footnote": [], "page_idx": 0, "bbox": [100, 260, 900, 600]}
  ],
  "markdown": "# 信息安全制度\n\n重要数据应定期备份。\n\n![](images/page_0_image_2.jpg)",
  "images": {
    "images/page_0_image_2.jpg": {"mime_type": "image/jpeg", "data_base64": "/9j/2Q=="}
  },
  "page_count": 1,
  "is_full_document": true,
  "metadata": {"producer": {"name": "mineru", "version": "4.1.7"}},
  "parse_info": {"tier": "basic", "parse_mode": "ocr"}
}
```

以上为结构示例，图片字节为示意值。`bbox` 为 Content List 的 `[x0, y0, x1, y1]`，坐标范围为 0–1000，允许缺省。它与 Middle JSON 的 0–1 坐标分别处理。

`content_list` 的数组顺序作为阅读顺序，`page_idx` 保留源文档零基索引。`images` 的键与块内相对路径对应。`markdown` 保留用于检查，分块与溯源以 Content List 为输入。

## 四、Content List 转 MaxKB 段落

| 块类型／字段 | v1 转换 |
| --- | --- |
| `text.text` | 正文文本，清理 NUL |
| `text.text_level` | 有效的 1–6 级标题保存在 `level`，保留原始 `type=text`，其他值按正文处理 |
| `table.table_body` | 保留 HTML 表格结构及合并单元格 |
| `table_caption`、`table_footnote` | 按原顺序保留文字 |
| `image.img_path` | 作为 `image_ref` 原样返回，由调用方保存图片并替换引用 |
| `image_caption`、`image_footnote` | 保留图片说明文字 |
| `equation.text`、`img_path` | 保留公式文本与图片引用，正文优先使用文本，缺少文本时引用图片 |
| `list.list_items` | 按条目顺序转列表文本 |
| `header`、`footer`、`page_number` | 作为版面辅助信息保留于原始结果及溯源，正文分块默认过滤 |
| 其他类型 | 转换器保留原始类型名及可读文字，服务调用方负责未知类型日志与整份结果可用性判断 |

公式和列表字段已对照本机 MinerU 4.1.7 的 `mineru/render/_internal/content_list/v1.py` 核验。公式文本保存在 `text`，缺少文字时通过 `image_ref` 引用图片。转换器校验字段结构、页索引及图片引用，图片字节和资产完整性由调用方校验。外层统一决定异常后的回退或失败。

### 第二步的纯函数接口

`common.utils.mineru_converter.content_list_to_blocks(content_list, *, page_count=None)` 返回结构化块列表，保持输入不变。它只接受 Content List 数组，收据、结果信封和预览占位对象需由调用方识别。空数组返回空块列表。

| 块字段 | 类型与语义 |
| --- | --- |
| `type` | 原始类型名，含页眉、页脚和页码 |
| `text` | 文本、列表条目、公式或表格原文，清理 NUL |
| `level` | 1–6 的标题层级，其余为 null |
| `page` | 非负整数，沿用零基 `page_idx` |
| `bbox` | 4 项浮点数列表或 null，范围 0–1000，JSON 缓存往返后结构相同 |
| `image_ref` | 原始图片相对路径或 null |
| `table_header` | HTML／Markdown 表头原文或 null，支持多行 HTML 表头 |
| `source_index` | 输入 Content List 数组的零基索引 |

`page_count` 为非负整数或 null。null 跳过页码上限校验，负数、非整数页码仍产生 `MinerUConversionError`。块顺序及原始类型完整保留。表格说明和脚注按顺序纳入 `text`，显式表头单独提取，普通 `<td>` 首行保持原文。

`blocks_to_paragraphs(blocks, *, limit=4096, drop_types=("header", "footer", "page_number"))` 为 v1 默认分块函数。两函数均独立于数据库、网络和文件读写。图片保存与引用替换在两函数之间由调用方完成。

分块按标题划分小节，同一小节内的连续块在长度目标内合并。多级标题以 ` / ` 连接，续段保留当前标题。超长文本优先使用邻近换行分隔并均衡续段长度，避免产生很短的尾段。只有标题或图片的内容也保留。`drop_types=()` 可保留全部块类型，过滤仅发生在分块阶段。

`limit` 默认为 4096，可由调用方传入 1–102400 的整数。它是普通文本段落的长度上限及结构块的软目标。表格、图片引用和公式保持完整，可超过软目标，单块超过 102400 字符时产生显式转换异常。超大表格按行重组与单元格降级纳入第 3 周结构感知分块器。跨页表格保留各页独立源块、表头和阅读顺序。

`handle()` 的结果形态沿用现有处理器。

```json
{
  "name": "制度扫描.pdf",
  "content": [
    {"title": "信息安全制度", "content": "重要数据应定期备份。", "problem_list": []}
  ]
}
```

MaxKB 最终段落使用现有 `title`、`content`、`problem_list` 等字段。标题按 Python 字符索引截取前 256 个字符，溢出部分前置到本小节第一段正文，保留可检索文字。正文上限为 102400 字符。

分块结果同时返回 `page_indices`、`source_block_indices`，均去重排序，包含标题上下文和正文来源。调用方可据此查回块类型及 bbox。溯源存储在第 4 步决定，当前倾向独立 `paragraph_source` 表。用户修改缓存预览正文后，溯源需重新核验。

原生链路继续使用既有分段选项。MinerU 的段落保持小节级粒度，后续保存阶段按现有流程生成 `Paragraph.chunks`，供逐块向量化及整段检索返回。

### 图片保存

1. 校验图片相对路径、Base64、声明类型与实际字节，限制资产大小。
2. 为当前知识库建立资产路径到 File ID 的映射，重复引用复用同一 File ID。
3. 复用现有写法构造 `File(id=..., file_name=..., meta={"debug": false, "content": image_bytes})`。
4. 批量调用传入的 `save_image(image_list)`，由回调设置知识库归属并持久化。
5. 将 Markdown 图片路径及 HTML 中的图片引用改为 `./oss/file/{file_id}`。

缓存保存 Content List 与原始图片资产，知识库图片记录在物化阶段建立。

## 五、同步预览与占位传递

`Split.file_to_paragraph` 保存原件、恢复流位置后显式调用路由。原生分支继续使用现有循环，MinerU 分支由入口直接调用 `MinerUSplitHandle`。

缓存命中时同步物化图片并返回真实分段。未命中时先保存任务与原件绑定，使用短提交预算提交或复用解析任务，返回一个展示占位段落。

```json
{
  "name": "制度扫描.pdf",
  "source_file_id": "00000000-0000-0000-0000-000000000001",
  "content": [
    {
      "title": "解析中",
      "content": "文档已提交解析，保存后在文档列表查看处理状态。",
      "mineru": {
        "pending": true,
        "parse_job_id": "00000000-0000-0000-0000-000000000002",
        "request_id": "preview-6d5f55d0",
        "task_id": "job_example"
      }
    }
  ]
}
```

现有前端保存请求只发送 `name`、`paragraphs` 和 `source_file_id`。v1 以原件 `File.meta.mineru` 及解析任务表中的服务器绑定传递权威状态，段落里的 `mineru` 用于展示和辅助校验。后端根据原件绑定识别待解析保存，覆盖占位字段被编辑、删去或前端重建的情况。

原件的 `File.meta.mineru` 保存 `preview_kind=pending`、`parse_job_id`、`split_params`。任务表保存原件哈希、首次知识库、路由、版本和上游标识。`split_params` 保存规范化后的 `patterns`、`with_filter`、`limit`，表单字符串通过既有 `DocumentSplitRequest` 转为正确类型，后续后台按每份文档的参数快照分块。

保存时排除待解析预览的占位正文及占位问题。关联标题问题的选项按现有保存字段记录，实际问题从真实段落标题生成。v1 最终正文以绑定原件的后台解析结果生成，占位预览锁定属于 v2。

预览提交预算由 `MINERU_PREVIEW_SUBMIT_TIMEOUT_SECONDS` 配置，建议默认 5 秒。后台等待预算使用已有基础 30 秒加每页 10 秒。任务记录保存预算、开始时间及本轮截止时间，重投同一轮任务时复用截止时间。排队、执行和用户保存时间分别记录。

第五步入口在保存原件后查询缓存。命中时同步物化本知识库图片并返回真实段落，未命中时复用解析任务，仅首次创建执行短提交。`parse_capacity`、连接错误或不确定提交均返回单条占位，并投递 `run_parse_job`。worker 按稳定请求 ID 查询，404 且没有上游 task_id 时重试提交，容量仍满则按 10 秒重排队。总截止时间持续生效。

单个和批量保存均按服务端原件标记排除占位正文及问题，创建构建记录并在提交后投递 `build_document`。缓存已完成时也能立即触发构建。新建的异步文档通过构建任务投递向量化。普通原生文档沿现有保存和向量化流程处理。

缓存版本由 `MINERU_ENGINE_VERSION`（默认 4.1.7）、`MINERU_PROTOCOL_VERSION`（默认 1）配置。无法准确计页的 Flash 文件使用 `MINERU_FLASH_BUDGET_PAGES`（默认 10）作为时间预算单位，输出实际页数单独校验为有效正整数。

`get_content()` 使用已有结果做纯内容转换或返回可恢复的待解析异常。耗时轮询由后台任务承担。该处理器的调用范围由知识库上传入口控制。

## 六、保存、任务与缓存记录

| 持久化对象 | 最少字段 |
| --- | --- |
| `MinerUParseJob` | 首次提交知识库及原件、SHA-256、tier、引擎和协议版本、路由快照、请求／任务 ID、状态、截止时间、次数及版本、错误 |
| `MinerUParseCache` | SHA-256、tier、引擎和协议版本四元组唯一，File ID 列表、命中次数及最后命中时间 |
| `MinerUDocumentBuild` | 唯一文档 ID、任务及本次原件、独立分段参数、pending/succeeded/fallback/failed、版本、错误、向量化待投递标记 |
| `ParagraphSource` | 唯一段落、文档、去重排序的页码及源块索引数组 |

成功缓存按原始字节 SHA-256、tier、engine_version、protocol_version 全局匹配，只有完整成功结果可发布。第一个 File 保存结果 JSON 和资产路径映射，其他 File 保存图片字节。文件保留发布知识库归属，跨库构建时物化目标知识库图片。缺失资产按缓存失效处理。相同四元组的处理中任务通过条件唯一约束复用，分段参数独立存入每份文档的构建记录。

待解析保存创建文档及任务关联，段落写入阶段使用真实结果。单个保存和批量保存的自动向量化回调均按解析状态分流。Celery 投递通过 `transaction.on_commit()` 执行，任务参数使用持久化 ID。

任务进入独立 `mineru` 队列，worker 可设并发 2–4。`run_parse_job` 每次查询一次状态，未完成时以 10 秒 countdown 重试，总预算为基础 30 秒加每页 10 秒。8004 对上游解析生命周期使用独立信号量，默认 1，结果转换另有信号量。

后台先查成功缓存，再查询原任务。成功后物化图片，在数据库事务中写入真实段落、位置、字数及解析状态，事务提交后调用 `embedding_by_document`。向量化继续使用现有状态机制。

同一文档及尝试版本幂等处理。写入前检查文档存在、任务关联及版本。已提交段落的任务重投时复用写入结果，补发尚未完成的向量化投递。预览后直接放弃保存的任务与原件关联按保留策略清理。

## 七、状态、失败与回退

解析任务状态为 submitted/running/succeeded/failed/orphaned。文档解析状态存入 `MinerUDocumentBuild`，`list_document.sql` 通过 LEFT JOIN 返回 `mineru_parse_status`、`mineru_parse_error`、`mineru_parse_version`。该记录独立于文档 meta，文档迁移后构建时重新校验当前知识库与原件归属，失配则明确失败。

| 解析状态 | 展示与处理 |
| --- | --- |
| `pending` | 等待或正在构建 |
| `succeeded` | MinerU 真实段落已写入，后续展示向量化状态 |
| `fallback` | 原生回退段落已写入，记录回退原因并继续向量化 |
| `failed` | 解析失败，保留原件、任务身份及失败原因 |

MinerU 的提交、轮询、结果协议、资产处理、转换失败及超时统一进入失败分流。PDF、DOCX、XLSX、XLS、CSV 等存在对应原生处理器的格式直接调用该处理器，重置文件流并复用当前分段参数。原生回退同样没有可用内容时记录解析失败。

图片和 PPT 在 MinerU 失败时记录 `FAILED` 并保留原件。该分支按 v1 已确认的失败策略处理。回退直接选择原生处理器，避免再次进入 MinerU 路由。

日志及文档元数据记录 `parse_job_id`、`request_id`、`task_id`、`document_id`、原件哈希、阶段、错误代码、`fallback_used`、回退处理器、耗时和原因。迟到的 MinerU 结果需通过文档尝试版本校验。已完成回退或已更新的文档由对应版本保护。

错误代码区分 `submit_timeout`、`submission_unknown`、`parse_timeout`、`upstream_failed`、`protocol_error`、`asset_error`、`conversion_error`、`native_fallback_failed` 和 `no_native_fallback`。异常堆栈留在服务日志，文档元数据保留可展示的简要原因。

## 八、分步验收

| 步骤 | 最少回归点 |
| --- | --- |
| 字段文档 | 对照当前 8004 信封、真实 Content List、现有保存请求核验，运行已有客户端及服务回归 |
| 转换器 | 标题、正文、公式、列表、HTML 表格、图片引用、分页溯源、空白页、坏资产及未知块 |
| 路由与探测 | 100／50／30% 边界、两个条件分别及同时命中、知识库强制、ZIP、探测失败和流位置恢复 |
| 模型与服务 | 原件权限、重复预览、缓存版本隔离、任务绑定及幂等 |
| 预览路径 | 缓存真实分段、短提交、单条占位、服务器绑定、原生及工作流路径 |
| 保存与 Celery | 占位排除、单个／批量保存、提交后投递、重复任务、删除文档、超时、回退及向量化顺序 |
| 前端 | 白名单、通用图标、解析状态文案、现有保存字段和正常文档流程 |

第 1 周 `eval/results/baseline.json` 的固定 60 题结果作为检索比较起点。新增 MinerU 实验使用单独结果文件记录。
