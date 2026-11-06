# PDF 路由校准

指标按实际完成的抽样页计算，页索引从 0 开始。时间预算为逐页检查的软预算。

生效配置为 `MINERU_SCAN_AVG_CHAR_THRESHOLD=100`，`MINERU_SCAN_LOW_PAGE_CHAR_THRESHOLD=50`，`MINERU_SCAN_LOW_PAGE_RATIO_THRESHOLD=0.3`，`MINERU_PDF_PROBE_TIMEOUT_SECONDS=5`。

预期路由独立取自清单、评测文字核验记录、来源记录或明确文件名，依据不明时留空。

## eval/assets/corpus

共 7 份 PDF。

| 文件名 | 页数 | 平均非空白字数 | 低文字页占比 | 路由结果 | 预期路由 | 实际探测页 | 原因 | 预期依据 |
| --- | ---: | ---: | ---: | --- | --- | --- | --- | --- |
| NIST_SP1300_小型企业网络安全框架入门指南_英文.pdf | 9 | 1767.11 | 0.00% | 原生 | native | 0, 1, 2, 3, 4, 5, 6, 7, 8 | native_default | 评测 source_text_checked |
| image_panels.pdf | 10 | 0.00 | 100.00% | MinerU basic | mineru | 0, 1, 2, 3, 4, 5, 6, 7, 8, 9 | low_text_avg | 清单扫描/图片标记 |
| scan_pages.pdf | 10 | 0.00 | 100.00% | MinerU basic | mineru | 0, 1, 2, 3, 4, 5, 6, 7, 8, 9 | low_text_avg | 清单扫描/图片标记 |
| 中卫市公开信息安全管理制度.pdf | 7 | 440.43 | 0.00% | 原生 | native | 0, 1, 2, 3, 4, 5, 6 | native_default | 评测 source_text_checked |
| 信阳农林学院_网络信息安全突发事件应急预案.pdf | 3 | 528.67 | 0.00% | 原生 | native | 0, 1, 2 | native_default | 评测 source_text_checked |
| 公共互联网网络安全突发事件应急预案_广州航海学院公开版.pdf | 12 | 587.58 | 0.00% | 原生 | native | 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11 | native_default | 评测 source_text_checked |
| 北方工业大学_网络与信息安全应急处置预案.pdf | 20 | 376.95 | 5.00% | 原生 | native | 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19 | native_default | 评测 source_text_checked |

## services/mineru/samples

共 1 份 PDF。

| 文件名 | 页数 | 平均非空白字数 | 低文字页占比 | 路由结果 | 预期路由 | 实际探测页 | 原因 | 预期依据 |
| --- | ---: | ---: | ---: | --- | --- | --- | --- | --- |
| business_policy_scan.pdf | 7 | 0.00 | 100.00% | MinerU basic | mineru | 0, 1, 2, 3, 4, 5, 6 | low_text_avg | 来源记录扫描标记 |

## 误判清单

共 8 份 PDF，8 份有预期标签，0 份留空。

### 电子版被判成 MinerU

无。

### 扫描件或图片 PDF 被判成原生

无。
