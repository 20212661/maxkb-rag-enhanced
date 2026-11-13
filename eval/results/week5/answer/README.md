# 来源注入回答评测

## 输入与评分

使用 `eval/answer_set.jsonl` 的20题。评分规则保存在 `eval/answer_rubric.md`，在真实回答请求前提交。所有评分列由人工填写。

题目的verification记录 `eval/assets/corpus/` 中的源文件路径、SHA-256和条款位置。`eval/assets/answer_manifest.json` 对应当前Day3应用的10份原始文档。新增浙江财经大学东方学院完整原件供回答题核验，60题检索开发集仍使用原有 `eval/assets/manifest.json`。

## 运行

使用Python 3.11和项目根目录。先将应用temperature设为0并发布，记录原值供实验后恢复。deepseek-flash的其他参数沿用现有配置，非思考模式，最大输出512 token。两组使用同一个实验发布版本。

在启动web的WSL终端设置运行环境，然后通过 `python main.py dev web` 启动。

```bash
export MAXKB_QA_L1_CACHE_ENABLED=false
export MAXKB_EMBEDDING_CACHE_ENABLED=false
export MAXKB_QUERY_EMBEDDING_CACHE_ENABLED=false
export MAXKB_PROMPT_INCLUDE_DOCUMENT_NAME=false
python main.py dev web
```

在另一个WSL终端使用相同环境配置导出运行清单。清单由操作者确认与web启动环境一致，脚本通过HTTP核对当前发布版本，并检查实际聊天记录中的L1命中。服务器现有HTTP接口未提供全局配置读取，清单的来源在JSON结果中明确记录。

```bash
export APP_ID=01a1ac08-2a6a-7923-8dc9-265736917a83
export STATE_FILE=eval/.local-answer-off.json
python - <<'PY'
import json
import os
from pathlib import Path
import main
os.environ.setdefault('SERVER_NAME', 'web')
main.django.setup()
from application.models import ApplicationVersion
from common.cache.model_profile import enabled
from models_provider.models import Model
version = ApplicationVersion.objects.filter(application_id=os.environ['APP_ID']).order_by('-create_time').first()
model = Model.objects.get(pk=version.model_id)
state = {
    'application_id': str(version.application_id),
    'published_version_id': str(version.id),
    'model_id': str(version.model_id),
    'model_name': model.model_name,
    'model_params_setting': version.model_params_setting,
    'model_setting': version.model_setting,
    'knowledge_setting': version.knowledge_setting,
    'knowledge_ids': version.knowledge_ids,
    'prompt_include_document_name': enabled('PROMPT_INCLUDE_DOCUMENT_NAME'),
    'qa_l1_cache_enabled': enabled('QA_L1_CACHE_ENABLED'),
    'embedding_cache_enabled': enabled('EMBEDDING_CACHE_ENABLED'),
    'query_embedding_cache_enabled': enabled('QUERY_EMBEDDING_CACHE_ENABLED'),
}
Path(os.environ['STATE_FILE']).write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
PY
python eval/run_answer_eval.py run --tag prompt-off \
  --application-id "$APP_ID" --runtime-config "$STATE_FILE"
```

管理HTTP鉴权沿用 `MAXKB_TOKEN` 或 `MAXKB_PASSWORD`，交互终端也可输入密码。脚本从管理API获取应用匿名访问凭据并走公开聊天接口。凭据保留在进程内存中。

将来源开关设为true并重启web，使用同样的导出步骤生成 `eval/.local-answer-on.json`，然后运行开启组。每组自动运行两遍，每题新建会话，JSON逐题落盘。tag重复时使用新的tag以保留已有结果。

```bash
python eval/run_answer_eval.py run --tag prompt-on \
  --application-id "$APP_ID" --runtime-config eval/.local-answer-on.json
python eval/run_answer_eval.py blind \
  --runs eval/results/week5/answer/prompt-off.json eval/results/week5/answer/prompt-on.json
```

## 输出口径

- `prompt-off.json`、`prompt-on.json`各保存两遍原始回答，包含引用段落、文档名、会话及消息ID、完整响应耗时、框架token、缓存标记、题集和评分规则哈希、运行配置。
- 响应耗时从发送消息计到完整非流式回答返回。会话创建和消息记录完成等待另记。
- `blind-review.md`包含两组第1遍的40条回答，随机混合，评分列为空。
- `mapping.json`保存盲评编号到组别、题目、消息ID的映射及随机种子。
- `stability.json`、`stability.md`比较各组两遍回答。相似度低于0.75、数字变化、引用文档变化或请求失败会触发复核标记。此标记仅描述差异。
- “期望来源是否被检索到”按引用文档名计算全部、部分、未检索到及覆盖数。引用列表包含的文档仍需结合具体段落和资料预算检查。
- 实验结束后恢复应用原temperature和临时缓存配置，重新发布及启动web。

## 检索回归

使用Week 3 D冻结索引、60题开发集和原评分器，关闭文档向量缓存、查询向量缓存及L1，通过真实HTTP命中测试接口重跑。逐题比较Recall@1、Recall@5、倒数排名、完整证据覆盖、各排名命中索引和状态。验收结果写入本目录的 `retrieval-regression.json`，原始结果写入 `retrieval-cache-off.json`。
