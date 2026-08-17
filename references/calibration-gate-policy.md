# Calibration / Blind Gate Policy

本文件定义 gate policy 的**结构**，不把示例数值当成真实阈值。真实阈值必须由 private calibration 数据、任务风险和 baseline 分布确定。

## 1. 基本结构

```json
{
  "schema_version": "1.0",
  "policy_id": "calibration-policy-r1",
  "split": "calibration",
  "gates": [
    {
      "scope": "overall",
      "metric": "line_exact_recall",
      "direction": "higher",
      "max_regression_abs": 0.0
    }
  ],
  "ranking": [
    {
      "scope": "overall",
      "metric": "onset_p95_ms",
      "direction": "lower"
    }
  ]
}
```

Blind policy 使用相同 gate 结构，但：

```json
{
  "schema_version": "1.0",
  "policy_id": "blind-policy-r1",
  "split": "blind_test",
  "gates": []
}
```

Blind 不需要 ranking，因为 candidate 已在 calibration 阶段锁定。

## 2. Gate fields

每个 gate 必须显式声明：

```text
scope
metric
direction = higher | lower
max_regression_abs
```

可选：

```text
min_candidate
max_candidate
min_improvement_abs
```

### `max_regression_abs`

必须显式填写，即使允许退化为 0，也不能省略。目的不是强制“任何指标都绝不下降”，而是让每一项允许的 trade-off 可审计，禁止把容忍度藏在代码里。

### `direction`

```text
higher: 越高越好，例如 recall/F1/within-rate
lower:  越低越好，例如 MAE/P95/review density/runtime
```

## 3. Scope

当前严格 workflow 支持：

```text
overall
language:<language-code>
```

例如：

```json
{
  "scope": "language:ko",
  "metric": "line_exact_recall",
  "direction": "higher",
  "max_regression_abs": 0.0
}
```

真实数据量太少的语言不要伪装成有统计意义；应在报告中明确 sample size，并考虑扩大数据集或只作为诊断项。

## 4. Calibration ranking

只有**先通过全部 gates**的 candidate 才进入 ranking。

Ranking 是按 policy 中顺序做确定性 lexicographic comparison。例如：

```json
"ranking": [
  {
    "scope": "overall",
    "metric": "line_exact_recall",
    "direction": "higher"
  },
  {
    "scope": "overall",
    "metric": "onset_p95_ms",
    "direction": "lower"
  },
  {
    "scope": "overall",
    "metric": "review_density_per_minute",
    "direction": "lower"
  }
]
```

不要用 blind_test 结果修改 ranking 顺序。

## 5. Cut metrics 的配套门禁

`cut_boundary_mae_ms / p95` 只统计 matched cuts。因此不能只写：

```text
cut_boundary_p95_ms 越低越好
```

还应同时 gate：

```text
cut_recall / cut_f1 / cut_boundary_match_count（或覆盖率）
```

否则“只匹配最容易的少数 cut”会得到虚假的漂亮边界误差。

## 6. 建议的第一版 policy 设计方式

不要先拍脑袋写数字。正确流程：

1. 先用当前 main/a8 作为 baseline 跑 calibration；
2. 看 baseline 指标分布和 hard-case breakdown；
3. 区分 hard safety gate 与 optimization metric；
4. safety 指标设成“不允许明显退化”；
5. 次要效率指标可以允许小幅 trade-off，但必须显式写 `max_regression_abs`；
6. 用 calibration 固定 policy 后再选 candidate；
7. blind policy 在看到 blind 结果前冻结。

## 7. 常见 hard gates

下面只是**候选项清单，不是默认数值**：

```text
line_exact_recall
line_exact_f1
missing_line_rate
wrong_order_rate
onset_p95_ms
offset_p95_ms
cut_recall
cut_f1
cut_boundary_p95_ms
overlap_recall
overlap_f1
track_attribution_accuracy
fragment correctness
review density
```

真正使用哪些，应由真实任务中“错了会不会直接产出错误字幕/漏字幕/错歌”的风险决定。

## 8. Blind-test纪律

Blind gate 的 policy 必须在首次读取 blind prediction 指标前固定。`selection.json` 已锁定：

```text
candidate_id
candidate_revision
runtime identity
calibration evaluation SHA
calibration policy SHA
```

Blind candidate 任一身份不同，必须重新回到 calibration；不能说“只是修了一个小 bug，还是同一个 candidate”。
