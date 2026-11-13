# 修复知识库绑定后的补跑

原批次的空知识库绑定失效原因及首次AI评阅保存在上级目录。补跑前已恢复Day3知识库关联，并在发布版本中核对knowledge_ids。已知可回答的机房陪同问题通过真实聊天烟测，期望文档来源覆盖1/1。

本批次沿用同一20题题集、temperature=0、max_tokens=512、reasoning_effort=none，两组各运行两遍。采集前的规则及校验代码已提交，相关提交为 `fd18364ea` 和 `567548e98`。每个原始JSON保存实际题集、规则、脚本和运行配置哈希。

本目录40条盲评表仅包含两组第1遍，使用新随机排列。Codex在冻结本批次AI评阅之前只阅读盲评表、题集及核验依据，随后再读取本目录mapping及分组回答。原批次的独立人工复核状态和本批次AI评阅分别记录。

## 结果入口

- [AI评阅分析](analysis.md)说明原批次失效、补跑效果、条款级诊断、第二遍稳定性及两条待复核项。
- [已填写盲评表](blind-review.md)保留40条判断和理由，2条得分待用户裁定。
- [分组统计](score-summary.md)和[逐题对照](question-comparison.md)由汇总脚本生成。
- collection-audit.json记录80次成功请求、L1为0及40次两组检索段落比较一致。
- passage-audit.json记录a05、a10目标条款的8次召回检查。
- second-round-review.json记录解盲后对5个首遍提高题的定性复核，第二遍正式分数保留为空。

```bash
python eval/score_answers.py \
  --review eval/results/week5/answer/repaired/blind-review.md \
  --mapping eval/results/week5/answer/repaired/mapping.json \
  --stability eval/results/week5/answer/repaired/stability.json \
  --output-dir eval/results/week5/answer/repaired
```
