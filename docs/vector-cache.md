# 向量缓存 v1

文档向量存入 PostgreSQL 的 `embedding_vector_cache`。查询向量存入 Redis，TTL 为 600 秒。

## 配置

| 环境变量 | 默认值 | 含义 |
| --- | --- | --- |
| `MAXKB_EMBEDDING_CACHE_ENABLED` | `true` | 文档和关联问题向量的持久化缓存 |
| `MAXKB_QUERY_EMBEDDING_CACHE_ENABLED` | `true` | 检索查询向量缓存 |
| `MAXKB_EMBEDDING_CACHE_EPOCH` | `1` | 远端模型升级后的手动失效代次 |

模型实例创建时，固定模型 ID、workspace，以及 provider、模型名、规范化模型参数、调用模式和 epoch 的指纹。API Key、密码等凭据排除。实例存活期间始终使用自身指纹，数据库配置更新后仍持有旧实例的进程继续使用旧缓存空间。

远端同名模型升级后，增加 epoch，并重启 web、Celery 和 local_model 相关进程，使新实例捕获新代次。模型编辑和参数配置保存成功后，通过 `on_commit` 清除当前进程的 ModelManage 实例。跨进程主动失效留待 v2。

## 数据与调用

PG 联合唯一键由 workspace、模型 ID、实例指纹和文本 SHA256 组成。文本采用实际模型输入，包含当前标题前缀及已有归一化处理，并与 `Embedding.meta.embedding_text` 对齐。输入相同的前缀配置可复用缓存。

`vector_data` 使用小端 float32，记录维度及编码版本。批量查找、批内去重和缺失文本计算完成后，恢复输入顺序及重复项。每次保存仍生成当前文档的 Embedding、关键词索引和关联信息。

查询缓存保留调用点现有的预处理。`embed_query` 与 `embed_documents` 使用不同指纹。业务缓存接入知识库保存、命中测试、简单应用检索、工作流知识库检索和关联问题向量更新。

Redis single-flight 使用带持有者令牌的短租约，持有期间续租，释放时校验所有权。等待有上限，超时允许独立计算。PG 重复发布通过唯一约束幂等处理。缓存不可用时执行模型计算并记录告警。

## 验收复现

在项目根目录加载本机环境变量后运行以下命令。

```bash
python eval/verify_cache.py --phase vector --benchmark --config eval/.local-week3-D.json
```

脚本从冻结的 Week 3 D 父段落建立独立知识库，保留原图片引用。冷、热重建均调用知识库正常的 Celery 重建任务，以 eager 模式执行。计数器包裹实际 `embed_documents` 与 `embed_query` 方法。

向量化耗时包含缓存查找、模型计算、缓存编码与发布，模型调用耗时单独统计。重建总耗时还包含 Embedding 写入、关键词索引与状态更新。评测经过 Django 测试客户端、鉴权、中间件和实际命中测试视图，耗时包含进程内请求处理。

输出位于 `eval/results/week4/`，包括缓存关闭、开启首遍、开启第二遍的 60 题结果。逐题比较 Recall@1、Recall@5、倒数排名及各名次证据命中标记。运行时只清除本次实验模型的文档缓存及评测问题对应的查询缓存。

## 已知限制

- 远端模型的同名静默升级无法自动识别，使用 epoch 增量及进程重启失效。
- 仅修改凭据中的服务地址可能保留指纹，切换实际模型服务时也需增加 epoch。
- ModelManage 的配置更新钩子清理当前进程，其余进程依靠实例过期或重启刷新。
- 缓存按确定性的模型输入复用向量，模型的随机输出及不同批处理导致的浮点波动需要由模型配置和版本管理控制。
- PG 历史缓存保留到人工清理，自动容量淘汰与跨进程配置广播列入 v2。
