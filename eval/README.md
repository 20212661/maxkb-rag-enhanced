# Day 4–5 检索评测

## 本次基线

2026-11-07 使用修正后的 v2 题集和 normalization_version=2，在本机 MaxKB 的真实命中测试接口上重跑 60 题，完成 60 题，接口错误 0。检索库为 `Day45-检索评测-60题`，ID 为 `01a1ac3b-b14a-7dd3-bd38-13a61f63eee1`。

| 类型 | 题数 | Recall@1 | Recall@5 | MRR | 平均检索耗时 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 事实型 | 20 | 60.00% | 85.00% | 0.6783 | 151.24 ms |
| 跨段落型 | 10 | 30.00% | 70.00% | 0.7333 | 274.65 ms |
| 表格型 | 10 | 10.00% | 100.00% | 0.4333 | 197.91 ms |
| 扫描件型 | 10 | 0.00% | 0.00% | 0.0000 | 187.41 ms |
| 图片型 | 10 | 0.00% | 0.00% | 0.0000 | 220.11 ms |
| 总计 | 60 | 26.67% | 56.67% | 0.4206 | 197.09 ms |

Top-5 收齐全部标准证据的问题比例为 53.33%。DeepSeek 配置按此前安排延后，端到端问答为 `not_run`，`mean_response_ms` 为 `null`。

v2 同时重跑了 baseline、native、after，三份使用相同题集哈希和评分器哈希。修改原因与历史版本见 [CHANGELOG.md](CHANGELOG.md)，最新 Auto 对比见 [compare.md](results/compare.md)。

## 文件

- `dataset.jsonl` 是固定的 60 题人工标注集。一行一个问题，包含 `id`、`question`、`type`、`gold_passages`。每条标准证据记录文档名、关键句、章节或页码和核验方式。
- `assets/manifest.json` 记录公开来源、文件 SHA-256、扫描及图片的原页映射。
- `assets/corpus/` 固定本次导入的 11 份材料，总计约 10 MiB。
- `prepare_corpus.py` 通过正常上传、切分、建文档 API 建立专用评测库，等待异步向量化完成。
- `run_eval.py` 调用命中测试接口，排序并取 Top-5，评分后保存汇总、分类型指标和每题完整检索证据。
- `results/baseline.json` 是首次完整基线，包含题集、语料清单及评测代码哈希和实际索引概况。
- `results/chain-smoke.jpg` 展示本次评测库的命中测试，`results/chain-detail.jpg` 展示第一名命中段落中的标准关键句，对应 `q010`。
- `.local.json` 保存当前部署的库和文档 ID，由导入脚本生成，已忽略。

## 素材与标注口径

普通文本材料共 9 份，含 4 份 Word 附件和 5 份 PDF。题目覆盖机房管理、供应商、备份、网络安全事件分级、报告表单及应急处置。

扫描件使用浙江财经大学东方学院公开制度的 10 个页面栅格化生成 `scan_pages.pdf`。原物理页码依次为 13、14、17、18、20、21、25、34、42、58。它们属于受控的模拟扫描，未添加模糊、倾斜或拍照噪声。整本制度的文本版本未导入评测库，以避免从另一份文本直接命中扫描题答案。

图片题使用新乡学院网站公开安全漫画的 10 个真实图文页面，以 `image_panels.pdf` 容器导入。原物理页码依次为 1、2、3、4、5、6、7、9、18、24。扫描与漫画容器均无可提取文本层。每题有人工核对的可见文字，标准答案仅用于离线评分。

原生基线解析器为这两个容器分别生成 10 个图片 Markdown 段落，未抽出图片中文字。本次 20 道难题均在分母内，关键句召回为 0。它们主要衡量图片文字提取对后续检索的影响。漫画题依据页面文字作答，尚未覆盖图形关系或空间推理。

跨段落型每题标注两项证据，已按本次 1200 字切分结果核查，来自不同导入段落或不同文档。表格题来自真实 PDF 职责表和 Word 报告表。表格里的字段或条目可作为关键句。此题集为小规模手工编写集，同一来源有多道题，指标用于固定条件下的本地迭代比较。

## 评分定义

1. 按 `comprehensive_score` 降序排列，取 Top-5，与命中测试页面的排序一致。管理 API 返回列表的原始顺序不能直接当作名次。
2. 一条标准证据的命中条件为文档名一致，且段落 `content` 包含其关键句。比较前统一 NFKC、大小写并去除空白和零宽字符。v2 额外统一 ∕ 与 /、∼ 与 ~，并规范化支持的带定界符 LaTeX 温度、运算符及简单字体包装。未知数学命令和其他标点保留，U 与 ∪ 保持区别。完整规则和 q059 标注修正见 CHANGELOG.md。标题命中不计分。
3. 每题 Recall@K 为前 K 段覆盖的标准证据数除以该题标准证据总数，重复覆盖只计一次。最终按问题宏平均。单证据题为 0 或 1，双证据题可为 0、0.5 或 1。
4. 每题 MRR 为第一条命中任意标准证据的倒数名次，无命中记 0。额外的 `all_gold_top5_rate` 衡量 Top-5 收齐全部标准证据的问题比例。
5. `mean_retrieval_ms` 包含顺序 HTTP 请求、服务端问题向量化及检索、响应接收、JSON 解析和排序。登录、前置检查和一次预热排除在外。它反映本机低并发热模型场景。
6. 请求失败记入错误数，召回和 MRR 记 0，失败耗时计入检索均值。全部题会继续执行，结果写盘后以非零退出码报告错误。

## 运行条件

本次后端运行在 WSL Ubuntu，Python 3.11，PostgreSQL + pgvector、Redis、Celery 和本地模型服务均运行。文本向量模型为 `shibing624/text2vec-base-chinese`，固定仓库 revision `183bb99aa7af74355fb58d16edf8c13ae7c5433e`，768 维，CPU，API 模型名为 `maxkb-embedding`。

切分参数为 `limit=1200`、`with_filter=true`。本次库中共 11 份文档、69 个段落，均完成向量化。检索模式为 `embedding`，相似度阈值为 `0.55`，`top_number=5`，未配置重排模型。v2 根据原页核验修正 q059，并统一已确认的公式、斜杠表示差异，三份方案全部重跑。题目文本、语料和检索阈值保持原值。

Python 评测脚本仅使用标准库，可在 Windows 或 WSL 下运行。服务端需要 web、Celery 的 `celery,model` 队列和 local_model 可用，模型管理中需有可用的向量模型。

## 复跑

在项目根目录的交互终端执行，脚本会提示输入 MaxKB 密码。也可通过 `MAXKB_TOKEN` 或 `MAXKB_PASSWORD` 环境变量提供凭据。

```powershell
python eval/prepare_corpus.py
python eval/run_eval.py --output eval/results/recheck.json
python -m unittest discover -s eval -p test_run_eval.py -v
```

首次导入会创建专用库，之后相同配置下再次执行会核对文档并继续等待向量化。一次只运行一个导入进程。若存在多个向量模型，使用 `--embedding-model-id <模型ID>` 指定。

当前机器也可使用已有的 WSL Python。

```powershell
wsl -d Ubuntu -- /opt/maxkb-dev/venv/bin/python eval/prepare_corpus.py
wsl -d Ubuntu -- /opt/maxkb-dev/venv/bin/python eval/run_eval.py --output eval/results/recheck.json
```

默认 API 地址为 `http://127.0.0.1:8080/admin/api`。其他部署使用 `--base-url` 和 `--workspace-id`。另建索引可用 `--config eval/.local-experiment.json`，评测时传同一路径。该本地配置含运行 ID，应保留在本机。默认 `.local.json` 已在 Git 中忽略。

`run_eval.py` 默认输出位置为 `results/baseline.json`。后续实验请显式指定不同的 `--output`，保留本次基线。每个结果文件记录参数和题集哈希，比较时同时核对语料、模型与题集。

对现有其他知识库也可直接评测。

```powershell
python eval/run_eval.py --knowledge-id <知识库ID> --output eval/results/other-kb.json
```

这种模式仍执行同一套关键句评分，语料和向量模型元数据为 `null`，实际导入情况由调用者核对。

## 端到端响应时间

在 MaxKB 中配置 DeepSeek 并创建或配置关联本评测库的简单应用后，可执行下列命令。它会对全部 60 题实际调用模型，产生对应 API 用量。

```powershell
python eval/run_eval.py --application-id <简单应用ID> --output eval/results/with-llm.json
```

每题新建会话，使用 `stream=false`，测量从提交消息到完整非空回答返回的时间，排除会话创建。结果写入 `end_to_end`，均值只统计成功请求并单列错误数。此项只测响应耗时，答案正确性和引用质量仍需单独核查。当前基线未执行该项。

## 验收

已通过 16 项回归检查，覆盖检索排序、来源与标题误命中、多证据评分、数学定界符、实际 Top-5 案例、OCR 字符区别、未知命令和版本差异提示。已通过 `ruff check eval` 和 Python 编译检查，另外独立重算了全部基线汇总指标。

本次交付收录了此前启动链路中的 `_ctypes` 内置模块兼容修复，避免没有 `__file__` 的 Python 构建在工具模块初始化时中断。

## 子块消融与新问题验证

Week 3 的 60 题消融中，标题前缀单独将 Recall@5 从 81.67% 提升至 89.17%，表格切分单独提升至 83.33%，两项共同开启时 MRR 从 0.6608 提升至 0.7028。E 与 Week 2 native v2 比较，Recall@5 为 56.67% → 58.33%。五组单轮耗时无明显变化，完整结果见 [消融报告](results/week3/README.md)。

另写的 20 题在同语料 A、D 索引上验证，Recall@5 为 45.00% → 67.50%，MRR 为 0.3917 → 0.4992。题集冻结后各跑一轮，包含作者先验暴露、近似题型配比及严格匹配边界的说明，见 [留出集报告](results/week3/holdout/README.md)。
