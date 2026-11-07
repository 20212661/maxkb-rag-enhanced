# 子块结构化消融实验

2026-11-07 使用 Week 2 v2 的 60 道题进行消融。五个独立知识库均重新保存段落、生成 Paragraph.chunks 并完成向量化，之后分别调用真实命中测试接口。五组共完成 300 题，接口错误 0。

## 结果

| 配置 | 输入段落来源 | 标题前缀 | 表格切分 | Recall@1 | Recall@5 | MRR | 平均检索耗时 | q039 名次 | q049 名次 |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| [A](A.json) | auto | 关 | 关 | 50.00% | 81.67% | 0.6608 | 122.71 ms | 6 | 21 |
| [B](B.json) | auto | 开 | 关 | 50.83% | 89.17% | 0.6944 | 128.08 ms | 1 | 1 |
| [C](C.json) | auto | 关 | 开 | 51.67% | 83.33% | 0.6789 | 110.28 ms | 7 | 5 |
| [D](D.json) | auto | 开 | 开 | 52.50% | 89.17% | 0.7028 | 126.29 ms | 1 | 1 |
| [E](E.json) | native | 开 | 开 | 29.17% | 58.33% | 0.4486 | 123.84 ms | 1 | 无匹配正文 |

A 与 Week 2 v2 Auto 的 60 题 Recall@1、Recall@5、倒数排名和全部证据命中标记逐一相同。D 的 q039、q049 均为第 1 名，其余题的 Recall@5 均达到或超过 Week 2 v2。

只开表格切分时，q049 从 A 的第 21 名升至 C 的第 5 名，q039 从第 6 名变为第 7 名。只开标题前缀的 B 已将两题均提升至第 1 名，B 与 D 的 Recall@5 相同，D 的 MRR 比 B 高 0.0083。当前数据中标题前缀贡献了主要召回提升，表格切分单独改善了 q049，并使表格子块保留完整表头和单元格结构。

## 逐题验收

| 检查 | 结果 |
| --- | --- |
| A 与 Week 2 v2 Auto 的逐题质量指标相同 | 60/60 通过 |
| D 的 q039、q049 进入 Top-5 | 2/2 通过，均为第 1 名 |
| D 其余题 Recall@5 不下降 | 58/58 通过 |
| D 的 Recall@5 下降题目 | 0 |

D 中改善的题目如下。跨段落题每题有两条标准证据，Recall 可为 0.5。

| 题目 | Week 2 v2 Recall@5 | D Recall@5 |
| --- | ---: | ---: |
| q015 | 0.00 | 1.00 |
| q022 | 0.00 | 0.50 |
| q027 | 0.50 | 1.00 |
| q030 | 0.50 | 1.00 |
| q039 | 0.00 | 1.00 |
| q049 | 0.00 | 1.00 |

原始逐题 Top-5、耗时和评分保存在 A–E.json。两道验收题还以 Top-1000、similarity=0 诊断具体排名，诊断结果位于 acceptance_queries。E 的 q049 输入只有原生图片引用，正文中没有标准关键句，名次记为无匹配正文。D 中原六题里的 q058 继续因 U/∪ 的 OCR 差异记失败。

## 条件与输入控制

题集、评分器与模型使用 Week 2 v2 版本。向量模型为 maxkb-embedding / text2vec-base-chinese，模型 ID 为 `42f63a3d-427e-11ef-b3ec-a8a1595801ab`，CPU、768 维。检索模式为 embedding，Top-5，similarity=0.55，无 reranker。段落沿用 limit=1200、with_filter=true 的冻结结果，子块默认 256 字符，标题前缀上限 100 字符。

这轮控制的是子块构造。A、B、C、D 使用同一份 Week 2 Auto 的 103 个段落，E 使用 Week 2 native 的 69 个段落。每组新建独立知识库，经正常 DocumentSerializers.Batch.batch_save 和 Celery 向量化任务重新生成子块、向量。构建进程以 eager 模式执行任务，使每组进程的配置同时作用于保存和向量化。命中测试由运行中的 Web API 提供。

输入复用冻结段落的 title、content 和原图片引用，原图片资产归属源库。这样各组的图片 URL 与正文一致，避免新建图片 UUID 改变向量输入。原 PDF 文件仍保存在源库。本轮的建库耗时覆盖保存及重新向量化，解析服务耗时见 Week 2 报告。

| 配置 | 段落数 | 子块向量数 | 保存及向量化耗时 |
| --- | ---: | ---: | ---: |
| A | 103 | 270 | 48.76 s |
| B | 103 | 270 | 41.55 s |
| C | 103 | 273 | 36.94 s |
| D | 103 | 273 | 40.94 s |
| E | 69 | 224 | 30.95 s |

运行顺序为 A、D、B、C、E，各配置顺序请求 60 题并排除预热。检索均值包含 HTTP 请求、问题向量化、检索和 JSON 解析。每个配置只有一轮正式结果，耗时差异仅作为本机运行记录。

| 校验项 | SHA-256 |
| --- | --- |
| dataset.jsonl | `b862fb7342227a55d9607077ad61c2c79a6ee0ff58045dc634797788ef0be419` |
| run_eval.py | `6f77e6013ff0cde1a4ee4c564635485506c615f9514c60c4f168b533631002d1` |
| 语料 manifest | `ee41f93d70274f79b3a3a02458158dec5a1378a0345ffd946c5d5c9d7e3bf11f` |
| A–D 段落输入 | `9c3fcbf395279a8af429a259a3f8957a675e85b2a5e50233c7cca66183a9a0af` |
| E 段落输入 | `dcb5812efcbd83df98d80ab7ea202f6892e871d0b3b33c8f2ec552f9fb4ee642` |

## 实现与配置

```dotenv
MAXKB_EMBEDDING_CHUNK_TITLE_PREFIX_ENABLED=true
MAXKB_EMBEDDING_CHUNK_TITLE_PREFIX_MAX_CHARS=100
MAXKB_TABLE_CHUNK_ENABLED=true
```

三个配置均有上述默认值。更改配置后需重启 Web 和相关 worker，再重新保存正文以生成 chunks 并重新向量化。仅刷新向量会使用数据库中已有的 chunks。

SQL 返回独立 title、content。chunk_data 对 source_type=1 子块拼接截断后的标题与换行，现有 normalize_for_embedding 再统一空白。Paragraph.chunks 保存正文子块，Paragraph.content 用于返回完整父段落。source_type=0 使用问题原文并生成一个向量。

PGVector 将实际向量输入保存到 Embedding.meta.embedding_text，首次索引和关键词重建使用同一文本。关键词重建只查询 source_type=1。历史记录缺少该元数据时，按 Embedding.id 排序，以相应 title+chunk 分词并记录 warning。历史记录数量与 chunks 数量不一致时标记分词失败，避免部分错配。回归检查核对了历史分词前后向量 ID 和数值的一致性。

TableChunkHandle 排在 MarkChunkHandle 前，入口选择首个支持的处理器。HTML 表格转换为 Markdown 子块，处理表头、caption、rowspan 和 colspan；无显式表头时生成列1、列2等中性列名并保留全部数据行。Markdown 表格支持对齐标记和转义竖线，代码围栏内示例交给普通切分器。按行分组，每块携带表头，超长行按字符继续拆分。表头及结构占满预算时保留完整表头，允许超过 256 的目标字数。异常或嵌套 HTML 表格回到普通切分处理。

段落编辑和创建、知识库表格导入、单个与批量文档保存、MinerU build_document 都通过共享 text_to_chunk。聊天记录入库和工作流的默认分块同样进入该入口，knowledge_write 显式提供的 chunks 继续由调用方指定。

## 检查与复跑

74 项后端回归通过，覆盖表格、标题、分词对齐、普通文件、上传保存、异步构建、转换器和 PDF 路由。16 项评测回归通过，修改范围 Ruff 检查通过。五份结果的 300 条评分均独立重算并与记录一致。

默认 worker 已重启，并额外通过正常 HTTP 批量保存、后台 Celery 向量化和关键词重分词的运行检查。测试表格生成 4 个带表头子块，实际向量输入包含标题，关键词重分词结果与首次索引一致。临时测试库已删除，记录见 [runtime-smoke.json](runtime-smoke.json)。

可在本机使用 eval/.local-week3-A.json 等已保存配置复跑命中测试。这些配置含实际知识库与文档 ID，按现有规则忽略 Git。

```sh
python eval/run_eval.py --config eval/.local-week3-A.json --output eval/results/week3/A-recheck.json
python eval/run_eval.py --config eval/.local-week3-D.json --output eval/results/week3/D-recheck.json
```

重建实验时，从各结果 configuration.chunking.source_knowledge_id 读取按 position 排序的 title、content，按照矩阵为新进程设置两个开关，经正常文档保存和向量化入口创建独立库。新进程生成的 chunks 应与对应配置一致，原图片引用及题集哈希可据此核对。机器可读汇总、验收结果与实现文件哈希见 [summary.json](summary.json)，实际子块及向量输入见 [chunk-examples.json](chunk-examples.json)。
