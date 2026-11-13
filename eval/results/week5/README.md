# Week 5 真实 LLM 缓存对照

2026-11-13 使用 DeepSeek 官方 API 的 `deepseek-flash` 完成 12 题对照。模型在本机 MaxKB 中保存为 `DeepSeek Flash`。本轮使用非思考模式 `reasoning_effort=none`、temperature 0.2、max_tokens 256，三个阶段采用相同配置。

模型标识与模式参数依据 DeepSeek 的 [模型列表](https://api-docs.deepseek.com/api/list-models/) 和 [Chat Completions 接口](https://api-docs.deepseek.com/api/create-chat-completion/)。

## 结果

| 配置 | 请求数 | 业务模型调用 | 响应 P50 | 响应 P95 | 首字 P50 | 首字 P95 | 供应商总 token |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| L1 关闭 | 12 | 12 | 1242.58 ms | 3811.98 ms | 1035.12 ms | 3631.90 ms | 24751 |
| L1 开启首遍 | 12 | 12 | 1143.38 ms | 1503.78 ms | 952.16 ms | 1186.67 ms | 24500 |
| L1 开启第二遍 | 12 | 0 | 27.02 ms | 28.32 ms | 26.99 ms | 28.27 ms | 0 |

关闭组的 12 次请求共使用 24751 token。第二遍的同一批问题由 L1 返回，业务模型调用及 token 消耗均为 0。首遍同样执行 12 次模型调用，其耗时与关闭组的差异按原始数据保留。

供应商用量由 LangChain 回调中的 API usage 读取，关闭组和首遍的 12 条均有用量，且与框架统计合计一致。最大输出分别为 192、103 token，均低于本轮 256 token 上限。命中组的回答与证据 ID 均与首遍逐题一致，36 条消息 ID 全部不同。

## 口径

- 本轮为单次本地实测，12 题均使用流式响应。
- 总响应时间从调用 ChatSerializers 开始，到响应迭代结束，包含实际 DeepSeek HTTPS 请求、本地检索、记录处理。首字为首个非空可见内容。
- 关闭组首个请求为 6294.5 ms，保留在原始统计中，P95 会受到首请求开销和网络波动影响。
- 第二遍使用完全相同的问题，命中率由该重复负载构造产生。
- 本轮检索使用 Week 3 D 索引，Top-K 为 5、相似度阈值为 0.55、最大证据字符数为 5000，文档向量缓存和查询向量缓存均关闭。
- 回答质量单独保留待复核状态。首遍的 q007、q049 出现拒答，计时结果覆盖这些回答。
- 结果中的执行 commit、脚本与数据集 SHA256、检索与模型参数于运行后补录，已显式标记 `metadata_added_post_run=true`。

原始结果见 [l1-api.json](l1-api.json)。其中包含逐题回答、证据 ID、token 用量及消息 ID。

## 复现

在现有评测环境保存有效的 Flash 模型后运行。

```bash
python eval/verify_l1.py --model-id YOUR_FLASH_MODEL_ID \
  --output eval/results/week5/l1-api.json
```

实验临时应用及会话已清理，本机保存的 DeepSeek Flash 模型可继续在应用设置中选用。API Key 通过 MaxKB 标准模型接口加密保存于数据库。

## 简历表述

> 实现带知识库版本失效、权限与引用校验的 L1 精确问答缓存。在 12 题 DeepSeek Flash API 单轮对照中，命中后的响应 P50 从 1.243 s 降至 27.0 ms，业务模型调用从 12 次降至 0，关闭组对应 token 用量为 24751。
