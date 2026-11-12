# L2 离线实验

`l2_threshold.py` 使用冻结的原问题作为答案候选锚点，以改写题和困难负例模拟语义复用。候选库在整个阈值扫描中固定，查询结果不回填。

## 当前数据

`l2_dataset.jsonl` 包含 10 个原问题锚点、20 个改写题和 20 个困难负例。开发集包含 6 个意图组、24 个查询，测试集包含其余 4 个意图组、16 个查询。同一意图组完整分配到一个集合。

本轮改写和标签由助手起草，`annotation_source=assistant_draft`，`human_reviewed=false`。用户已选择先用待审草案跑试验。人工确认问题与原答案的可复用关系后，可修订标签并更新 `human_reviewed`。正式运行可添加 `--require-human-review` 检查待审记录。

每条改写题用 `reusable_answer_ids` 列出可复用的候选答案 ID。困难负例使用空列表，表示冻结候选库中的答案均不足以直接回答该问题。原问题锚点仅进入候选库，精确重复问题由 L1 处理。

## 运行

加载 MaxKB 本机环境变量后，使用配置好的向量模型生成问题向量。

```bash
python eval/l2_threshold.py --model-id YOUR_EMBEDDING_MODEL_ID --plot
```

`--plot` 需要 matplotlib，可通过 `uv pip install matplotlib` 安装到评测环境。JSON 和 Markdown 输出使用 Python 标准库，MaxKB 模型调用使用项目已有依赖。

冻结向量后可以离线重现曲线。

```bash
python eval/l2_threshold.py --vectors eval/results/week4/l2/vectors.json --plot
python -m unittest discover -s eval -p 'test_*.py'
```

所有问题和候选锚点均使用 `embed_query`。冻结文件记录模型 ID、workspace 和模型实例指纹。输出记录评测脚本及数据集 SHA256。

## 指标和阈值选择

开发集扫描余弦相似度阈值。默认范围为 0.5–1.0，步长为 0.005。开发集选出满足目标误命中率、且接受率最高的阈值，同接受数时使用更高阈值。测试集仅运行锁定点。

- 接受率为接受的查询数除以查询总数。
- 误命中率为错误接受数除以接受数。
- 错误接受占全请求比例同时报告，方便检查分母。
- 接受数为 0 时，误命中率记为空，该阈值跳过选择。

默认目标误命中率为 1%，可以通过 `--max-false-hit-rate` 调整。当前数据量及人工标注状态使本轮结果适合作为探索性试验。输出包括阈值表、逐题最相似候选与分数、冻结向量，以及可选的 PNG 曲线。

本地试验结果见 [阈值报告](results/week4/l2/README.md)。q020 的一个困难负例询问六个函数各有多少子类别，与只列举六个函数的原问题相似度达到 0.9510，在锁定阈值 0.915 时被错误接受。
