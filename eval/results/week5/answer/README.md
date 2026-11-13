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

## 2026-11-13验收记录

题集、脚本和评分规则在真实回答请求前提交为 `9b1309a08`。采集后曾通过amend合并结果，本次已通过reflog恢复该原始提交，将采集结果与发现问题的live-application-test原始记录另作第四次提交。两组结果均保存题集和评分规则原始SHA-256，运行期间保持一致。

| 检查 | 实测结果 |
| --- | --- |
| 模型与参数 | deepseek-flash，temperature=0，max_tokens=512，reasoning_effort=none |
| 正式回答请求 | 两组各20题、各两遍，共80次，80次完成 |
| 会话与消息 | 80个不同会话ID、80个不同消息ID |
| L1命中 | 0次 |
| 盲评表 | 40条，均来自第1遍，评分与备注共6列全部留空 |
| 稳定性比较 | 40对，其中12对触发差异复核标记 |
| 后端相关测试 | 19项通过 |
| eval目录测试 | 31项通过 |
| 本次改动Python文件Ruff | 通过 |

正式采集前额外执行1次真实聊天接口烟测。稳定性标记用于定位文本、数字或引用变化，人工评分记录保留空白。验收汇总见 [acceptance.json](acceptance.json)，评分入口见 [blind-review.md](blind-review.md)。

| 60题检索指标 | 实测结果 | 与Week 3 D的逐题比较 |
| --- | --- | --- |
| Recall@1 | 52.50% | 一致 |
| Recall@5 | 89.17% | 一致 |
| MRR | 0.7028 | 一致 |
| 六个验收字段差异数 | 0 | 60题全部完成，接口错误0 |

工作流由单元回归覆盖正常执行、历史恢复、重排输入和排名记录，真实HTTP回答实验使用简单应用。实验完成后已恢复应用temperature=0.2，文档向量缓存、查询向量缓存及L1恢复开启，默认文档名注入开启，web已重启并响应请求。

运行清单由本机控制脚本根据实际启动web的环境和数据库发布版本导出，脚本另外通过HTTP核对发布版本并读取消息记录。本次原始结果中的token来自框架消息记录。

## 评分后汇总

`eval/score_answers.py`读取已填写的盲评表、组别映射和稳定性记录，汇总显式填写的得分。待复核得分单列并从已决平均分分母排除。脚本开发及测试使用合成数据，在本轮评分前提交。

```bash
python eval/score_answers.py
python -m unittest discover -s eval -p test_score_answers.py -v
```

默认输出本目录中的 `score-summary.json`、`score-summary.md` 和 `question-comparison.md`。分组均分按四类题分别统计，逐题表包含两组得分、变化、期望来源覆盖、两遍文字是否完全一致及差异复核标记。全部期望文档在引用中且得分低于1的项计入“检索到但生成出错”，期望文档部分或全部缺失的项计入“来源有缺失”，知识库外题的来源覆盖记不适用。该拆分使用文档名代理，具体条款和资料预算需要结合证据审查。
