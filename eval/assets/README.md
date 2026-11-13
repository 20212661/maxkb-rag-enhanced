# 公开评测语料来源

本目录清单覆盖 `corpus/` 中的全部 12 份文件，包括 10 份公开原件与 2 份由公开原件生成的图像 PDF。全部来源为政府、高校或 NIST 的公开发布页面和附件。2026-11-14 已逐文件核对本地内容及 Git blob 的 SHA-256，与原有清单全部一致，目录中的文件均有对应来源记录。

开发集使用的 11 份文件及页码映射记录于 [manifest.json](manifest.json)。回答评测使用的完整浙江制度原件记录于 [answer_manifest.json](answer_manifest.json)，用于来源与条款核验。

| 文件 | 发布方或公开托管方 | 原始来源 |
| --- | --- | --- |
| [image_panels.pdf](<corpus/image_panels.pdf>) | 新乡学院信息管理中心网站 | [公开页面或附件](https://imc.xxu.edu.cn/__local/F/26/81/EA73E6F0B3800D3F49835FD7F81_94437458_1534F8A.pdf) |
| [NIST_SP1300_小型企业网络安全框架入门指南_英文.pdf](<corpus/NIST_SP1300_小型企业网络安全框架入门指南_英文.pdf>) | 美国国家标准与技术研究院 NIST | [公开页面或附件](https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.1300.pdf) |
| [scan_pages.pdf](<corpus/scan_pages.pdf>) | 浙江财经大学东方学院 | [公开页面或附件](https://it.zufedfc.edu.cn/__local/1/80/2D/3E9443B67F1041C17EF3CA2F0DE_5EC7480C_DD562.pdf) |
| [中卫市公开信息安全管理制度.pdf](<corpus/中卫市公开信息安全管理制度.pdf>) | 中卫市人民政府网站 | [公开页面或附件](https://www.nxzw.gov.cn/zwgk/bmxxgkml/swsjkwyh/fdzdgknr_49861/wsyl_49877/201811/P020181121330110409821.pdf) |
| [信阳农林学院_网络信息安全突发事件应急预案.pdf](<corpus/信阳农林学院_网络信息安全突发事件应急预案.pdf>) | 信阳农林学院 | [公开页面或附件](https://www.xyafu.edu.cn/__local/5/FB/B6/17E24625866A836A357C767D47A_A0EFBCB6_16031.pdf) |
| [公共互联网网络安全突发事件应急预案_广州航海学院公开版.pdf](<corpus/公共互联网网络安全突发事件应急预案_广州航海学院公开版.pdf>) | 广州航海学院网站公开转载 | [公开页面或附件](https://netc.gzmtu.edu.cn/__local/6/A0/D2/0E434E09FFE8776973DD07BF388_4847BE57_357E2.pdf?e=.pdf) |
| [北方工业大学_网络与信息安全应急处置预案.pdf](<corpus/北方工业大学_网络与信息安全应急处置预案.pdf>) | 北方工业大学 | [公开页面或附件](https://cic.ncut.edu.cn/__local/F/22/78/AE81ED9E78F19298113C5CFFC35_87C1FFBE_856B0.pdf) |
| [河南水利与环境职业学院_附件 1：网络安全事件情况报告.docx](<corpus/河南水利与环境职业学院_附件 1：网络安全事件情况报告.docx>) | 河南水利与环境职业学院 | [公开页面或附件](https://xjzx.hwec.edu.cn/info/1111/1381.htm) |
| [河南水利与环境职业学院_附件 2：网络安全事件分类.docx](<corpus/河南水利与环境职业学院_附件 2：网络安全事件分类.docx>) | 河南水利与环境职业学院 | [公开页面或附件](https://xjzx.hwec.edu.cn/info/1111/1381.htm) |
| [河南水利与环境职业学院_附件 3：名词术语.docx](<corpus/河南水利与环境职业学院_附件 3：名词术语.docx>) | 河南水利与环境职业学院 | [公开页面或附件](https://xjzx.hwec.edu.cn/info/1111/1381.htm) |
| [河南水利与环境职业学院_附件 4：网络和信息系统损失程度划分说明.docx](<corpus/河南水利与环境职业学院_附件 4：网络和信息系统损失程度划分说明.docx>) | 河南水利与环境职业学院 | [公开页面或附件](https://xjzx.hwec.edu.cn/info/1111/1381.htm) |
| [浙江财经大学东方学院_网络安全管理制度汇编.pdf](<corpus/浙江财经大学东方学院_网络安全管理制度汇编.pdf>) | 浙江财经大学东方学院 | [公开页面或附件](https://it.zufedfc.edu.cn/__local/1/80/2D/3E9443B67F1041C17EF3CA2F0DE_5EC7480C_DD562.pdf) |

## 派生文档与示例数据

- `scan_pages.pdf` 从浙江财经大学东方学院公开制度中选取物理页 13、14、17、18、20、21、25、34、42、58，按原顺序栅格化，移除文字层，用于受控扫描件检索实验。原件哈希和页码映射保存在 `manifest.json`。
- `image_panels.pdf` 选取新乡学院网站公开《安全漫画海报24篇》的物理页 1、2、3、4、5、6、7、9、18、24，组成无文字层 PDF。原件哈希和页码映射保存在 `manifest.json`。
- [demo-data](../../demo-data/README.md) 使用中卫市公开制度原件及其七页受控扫描版本。原始链接、两份文件哈希与栅格化参数记录于 [demo-data/manifest.json](../../demo-data/manifest.json)。

原件保留发布方的署名与权利归属，派生文件保留来源与页码关系。冻结题集、检索评分器和评测结果继续按各周报告中的版本与哈希复现。
