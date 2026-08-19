# Partial Timeline Repair V1 私有真实数据评估协议

更新：2026-08-19

> 本协议只评估 `partial_timeline_repair_preview` 的 timing 建议，不授予自动改时或 release authority。所有评估输出固定 `releaseable=false`、`automatic_timing_change_allowed=false`。

## 1. 目标

Partial Timeline Repair V1 只在用户显式选中的少量 cue 上生成 Source-to-Mix timing preview。公共 CI 可以验证 AFFINE / PIECEWISE_RATE / CUT_AWARE 数学、cut fail-closed 与 selected-only 写入，但不能证明真实歌曲上的 timing 建议准确。

真实 promotion 前必须使用私有人工终稿作为 mix-time truth，并分 calibration / blind_test 两阶段评估：

```text
calibration -> 观察失败类型/风险桶，冻结候选规则
blind_test  -> 独立验证，不再针对 blind 样本调规则
```

## 2. Dataset manifest

```json
{
  "schema_version": "1.0",
  "dataset": "private-partial-timing",
  "dataset_revision": "r1",
  "split": "calibration",
  "cases": [
    {
      "id": "case-001",
      "language": "zh",
      "risk_buckets": ["global_rate", "weak_vocal"],
      "preview_report_json": "reports/case-001.preview.json",
      "truth_json": "truth/case-001.truth.json"
    }
  ]
}
```

`split` 只允许 `calibration` 或 `blind_test`。Case ID 必须唯一。`language` 可用 `zh/en/ko/ja/...`；`risk_buckets` 建议覆盖：

```text
global_rate
piecewise_rate
cut
near_cut
repeated_chorus
weak_vocal
rap
multilingual
editor_bad_timing
```

## 3. Human truth

Truth 不需要歌词文本，只记录该 preview 已选 cue 的人工最终 mix-time 边界：

```json
{
  "schema_version": "1.0",
  "cues": [
    {
      "cue_number": 128,
      "truth_start_ms": 12340,
      "truth_end_ms": 15670
    }
  ]
}
```

Truth cue 集合必须与对应 preview report 的 selected decisions **完全一致**。这样不能通过少报困难 cue 人为美化指标。

## 4. 执行

```powershell
python scripts/v4_evaluate_partial_timeline.py `
  --dataset "private/partial-timing/calibration.dataset.json" `
  --error-threshold-ms 250 `
  --out "private/partial-timing/calibration.report.json"
```

`--error-threshold-ms` 只是评估容差，默认 250ms；它不会改变 preview 生成规则，也不会改变 production threshold、release gate 或 timing authority。

## 5. 关键指标

### 基础覆盖

```text
selected_cue_count
proposed_count / proposal_rate
review_count / review_rate
unchanged_count / unchanged_rate
```

### Proposal 误差

```text
proposal_onset_mae_ms
proposal_offset_mae_ms
proposal_line_max_error_p50_ms
proposal_line_max_error_p90_ms
proposal_line_max_error_p95_ms
proposal_within_threshold_rate
```

### 失败模式

```text
bad_proposal_count/rate
```

建议后的最大边界误差仍大于评估阈值。

```text
unnecessary_proposal_count/rate
```

人工真值显示原剪映 timing 已在阈值内，但工具仍提出改时建议。这是未来自动化前必须压低的扰动风险。

```text
harmful_proposal_count/rate
```

建议后的最大边界误差比原始剪映 timing 更大。即使仍在容差内，也要单独统计。

```text
missed_needed_change_count
```

工具判断 `unchanged`，但人工真值显示原 timing 已超阈值。

```text
review_needed_change_count
```

工具选择 review，且人工真值确认原 timing 的确需要修。这不是自动错误，但会影响人工工作量与 coverage。

报告同时按 `language`、`risk_bucket`、`case` 分桶，避免整体平均数掩盖韩语/说唱/cut/重复副歌等局部风险。

## 6. Privacy / safety

Evaluator 可以读取私有 preview report 中的歌词字段，但**评估输出不复制 raw subtitle/canonical lyric text**。输出只包含 dataset identity、split、风险分类、计数与误差统计。

Evaluator 会拒绝：

- preview `releaseable != false`；
- preview `automatic_timing_change_allowed != false`；
- preview 未声明 `subtitle_text_unchanged=true`；
- 非 `partial_timeline_repair_preview` 输入；
- preview selected cue 与 truth cue 集合不完全一致；
- duplicate cue/case identity；
- 非法/非正 timing interval。

## 7. Promotion gate 建议

PR #31 本身不定义自动 promotion 数值门槛。第一轮真实 calibration 应先观察分布，尤其关注：

1. `harmful_proposal_count` 是否为 0；
2. `bad_proposal_count` 是否为 0；
3. `unnecessary_proposal_rate` 是否足够低；
4. proposal P95 是否明显优于原 editor timing；
5. global-rate / piecewise-rate / cut / repeated chorus / rap / multilingual 是否存在单独失败桶；
6. calibration 规则冻结后，独立 blind_test 是否保持同样结果。

只有 blind-test 证明收益后，才应另开 PR 讨论 authoritative partial timing write-back。不得直接把本 evaluator 的 calibration 报告当成 release 授权。