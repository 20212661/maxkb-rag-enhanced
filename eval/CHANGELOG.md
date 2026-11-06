# 评测集与判定规则变更记录

## v2 · 2026-11-07

本版修正 q059 的标准句，并将归一化规则升级为 `normalization_version=2`。三份参与对比的 baseline、native、after 均使用本版题集和评分器重新请求 MaxKB 命中测试接口，各完成 60 题、接口错误 0。检索参数仍为 `limit=1200、with_filter=true、embedding、Top-5、similarity=0.55`。

| 项目 | 修改 | 核验依据 |
| --- | --- | --- |
| q059 标准句 | “直接打电话向发件人代表的人员确认”改为“直接电话向发件人代表的人员确认” | image_panels.pdf 第 9 页，对应原物理页 18。原图与 MinerU 正文均无“打”字，已逐字查看原页。 |
| q048 公式表示 | 支持带数学定界符的摄氏温度与 `\sim` 范围表示，统一为 `18°c~24°c` 一类形式 | 正确段落已在 Top-1，原 `18℃～24℃` 与 MinerU LaTeX 表达数值一致。 |
| q054 斜杠表示 | `∕`（U+2215）与 `/`（U+002F）统一，`∼` 统一为 `~` | 正确段落已在 Top-1，NFKC 对 U+2215 不作斜杠转换。 |

归一化继续执行 NFKC、大小写统一、去除空白与零宽字符。数学表达式支持 `$...$`、`$$...$$`、`\(...\)`、`\[...\]`，处理简单的 `\mathrm`、`\text`、`\operatorname` 字体包装与 `^{\circ}`、`^\circ`，映射 `\sim`、`\times`、`\leq`、`\geq`、`\pm`、`\div`、`\cdot`。含未知命令的数学表达式保留其命令和定界符。

q058 继续记为 OCR 错误，原图 `U盘` 被识别成 `∪盘`。两种字符保持不同。数值、否定词和其他标点也保留，判定仍按规范化后的文档名与正文关键句包含关系执行。

60 道题的 ID、题目文本、题型及文档定位均保持原值，数据内容仅修正 q059 的一个字。原文件的 CRLF 换行格式保留。

| 校验项 | v1 | v2 |
| --- | --- | --- |
| dataset.jsonl SHA-256 | `2b12e69612140c1d9c1e21d7b6034a1ff41beb349a4cba218626103d909da161` | `b862fb7342227a55d9607077ad61c2c79a6ee0ff58045dc634797788ef0be419` |
| run_eval.py SHA-256 | `2e1468da765849979c7d0409173fc2dcf405cdbebe57a228443709e76c3721fa` | `6f77e6013ff0cde1a4ee4c564635485506c615f9514c60c4f168b533631002d1` |
| normalization_version | 1，旧结果无显式字段 | 2，写入每份结果的 configuration |

v1 题集、评分器、三份结果、对比表和报告完整保存在 Git 提交 `5ff686f20`，可使用 `git show 5ff686f20:eval/results/after.json` 等命令读取。当前 `eval/results/baseline.json`、`native.json`、`after.json` 与 compare.md 均为 v2。

v2 的 baseline 与 native 逐题 Recall@1/5、MRR 和全部证据命中标记一致。v1 与 v2 的三组 Top-5 段落 ID 及顺序共 180 条逐一相同，评分变化仅发生在 Auto 的 q048、q054、q059，均从 0 变为 Top-1 命中。整体 Recall@5 从 v1 的 76.67% 更新为 81.67%，MRR 从 0.6108 更新为 0.6608，增量来自上述判定修正。

归一化相关回归检查包含已保存 Top-5 的实际例子、OCR 字符区别、错误数值、数学定界符和未知命令。compare.py 会提示两份结果的题集哈希或归一化版本差异。逐次重跑时间及顺序核验见 [修订审计](results/week2-audit.json)，最新指标见 [compare.md](results/compare.md)。
