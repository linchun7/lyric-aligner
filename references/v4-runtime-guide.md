# Lyric Aligner v4 生产运行手册

更新：2026-08-18  
主线算法版本：`4.0.0a8`  
Calibration profile：`production-bootstrap-2026-08-17-a7`

> main 已完成 a8 的 reconstruction/review/overlap/cut/combined/render/release 主链。当前开发重点是 P1：把真实 calibration / blind-test 变成 split-isolated、candidate-locked、可审计流程。没有真实私有数据时，GitHub Actions 只能验证框架和 synthetic regressions，不能产生真实准确率。

## 1. 生产重建

```powershell
python scripts/v4_run.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --out-dir "output/<任务>/v4" `
  --git-commit "<commit>"
```

主链：

```text
Asset Resolution
 -> Primary Coarse
 -> AFFINE / PIECEWISE_RATE
 -> Selective Fine
 -> Canonical Timeline
 -> Transition Evidence
 -> ready_for_render | review_required
```

## 2. Review / Materialization

Review：

```powershell
python scripts/v4_review.py template ...
python scripts/v4_review.py apply ...
```

Confirmed overlap：

```powershell
python scripts/v4_recompose_overlap.py ...
```

Confirmed cut：

```powershell
python scripts/v4_rebuild_cut.py ...
```

同一 reviewed task 同时有可证明互不冲突的 cut + overlap：

```powershell
python scripts/v4_compose_materializations.py ...
```

Same-region cut+overlap、source-gap 冲突、partial-line ambiguity 等仍 fail-closed。

## 3. Final Render / Release

```powershell
python scripts/v4_render.py ...
python scripts/v4_validate_release.py ...
```

`v4_render.py` 可消费：

```text
production_orchestration
review_resolution
overlap_recomposition
cut_rebuild
combined_recomposition
```

共同要求：ready、无 active issues、无 legacy fallback、artifact lineage/profile/task fingerprint 全部一致。

## 4. P1 数据集准备

P1 严格数据集建议位于：

```text
private/datasets/<dataset-name>/
```

严格 schema=`1.1`。Manifest 至少应有：

```json
{
  "schema_version": "1.1",
  "dataset": "opaque-dataset-name",
  "dataset_revision": "2026-08-r1",
  "cases": [
    {
      "id": "case-0001",
      "source_group": "source-family-001",
      "split": "calibration",
      "language": "zh",
      "reference_srt": "reference/case-0001.srt",
      "predicted_srt": "predictions/candidate-a/case-0001.srt",
      "qa_json": "predictions/candidate-a/case-0001.qa.json",
      "audio_duration_seconds": 90,
      "expected_cuts": [{"time_ms": 30000}],
      "predicted_cuts": [{"time_ms": 30120}],
      "expected_overlaps": []
    }
  ]
}
```

规则：

- `id` / `source_group` / dataset 名称使用 opaque 值，不含曲名/艺人；
- 同一歌曲/版本家族的 clips 必须共享 source_group；
- 一个 source_group 只能属于 train/calibration/blind_test 中一个 split；
- baseline 与每个 candidate 可以有各自 prediction/QA 路径；
- reference、case metadata、expected annotations、dataset_revision 必须一致。

## 5. P1 严格入口

正式入口：

```text
scripts/v4_calibration_workflow.py
```

它负责：

```text
selected split evaluation
+ source_group isolation
+ immutable ground-truth identity
+ candidate revision/runtime identity
+ calibration selection
+ blind candidate lock
```

较低层的：

```text
scripts/evaluate_calibration_dataset.py
scripts/calibration_gate.py
```

用于诊断/测试，不应被拿来绕过 strict workflow 后宣称 blind-test 通过。

## 6. Calibration 阶段

目标：只使用 calibration split 选择一个 candidate。

重要纪律：在这个阶段 blind prediction/QA 可以完全不存在。

示意：

```powershell
python scripts/v4_calibration_workflow.py evaluate `
  --dataset "private/datasets/<name>/baseline.json" `
  --split calibration `
  --candidate-id baseline `
  --candidate-revision "<baseline-commit-or-build-id>" `
  --out "output/evaluation/baseline.calibration.json"

python scripts/v4_calibration_workflow.py evaluate `
  --dataset "private/datasets/<name>/candidate-a.json" `
  --split calibration `
  --candidate-id candidate-a `
  --candidate-revision "<candidate-commit>" `
  --out "output/evaluation/candidate-a.calibration.json"
```

然后使用明确的 calibration policy 选择：

```powershell
python scripts/v4_calibration_workflow.py select `
  --baseline "output/evaluation/baseline.calibration.json" `
  --candidate "output/evaluation/candidate-a.calibration.json" `
  --policy "private/datasets/<name>/calibration-policy.json" `
  --out "output/evaluation/selection.json"
```

Selection artifact 锁：

```text
candidate_id
candidate_revision
algorithm_version
calibration_profile_version
calibration_profile_id
calibration evaluation SHA
policy SHA
selection payload SHA
```

## 7. Gate policy

示意：

```json
{
  "schema_version": "1.0",
  "policy_id": "calibration-r1",
  "split": "calibration",
  "gates": [
    {
      "scope": "overall",
      "metric": "line_exact_recall",
      "direction": "higher",
      "max_regression_abs": 0.0
    },
    {
      "scope": "overall",
      "metric": "boundary_p95_ms",
      "direction": "lower",
      "max_regression_abs": 50.0
    },
    {
      "scope": "overall",
      "metric": "cut_recall",
      "direction": "higher",
      "max_regression_abs": 0.0
    },
    {
      "scope": "overall",
      "metric": "cut_boundary_p95_ms",
      "direction": "lower",
      "max_regression_abs": 100.0
    }
  ],
  "ranking": [
    {"scope": "overall", "metric": "line_exact_recall", "direction": "higher"},
    {"scope": "overall", "metric": "boundary_p95_ms", "direction": "lower"}
  ]
}
```

这些只是 policy 格式示例，不是项目已经校准出的真实阈值。真实阈值应由 calibration 数据确定并版本化。

## 8. Blind-test 阶段

只有 selection artifact 生成之后，才生成/读取 blind predictions。

```powershell
python scripts/v4_calibration_workflow.py evaluate `
  --dataset "private/datasets/<name>/baseline.json" `
  --split blind_test `
  --candidate-id baseline `
  --candidate-revision "<baseline-revision>" `
  --out "output/evaluation/baseline.blind.json"

python scripts/v4_calibration_workflow.py evaluate `
  --dataset "private/datasets/<name>/candidate-a.json" `
  --split blind_test `
  --candidate-id candidate-a `
  --candidate-revision "<exact-selected-revision>" `
  --out "output/evaluation/candidate-a.blind.json"

python scripts/v4_calibration_workflow.py blind `
  --baseline "output/evaluation/baseline.blind.json" `
  --candidate "output/evaluation/candidate-a.blind.json" `
  --selection "output/evaluation/selection.json" `
  --policy "private/datasets/<name>/blind-policy.json" `
  --out "output/evaluation/blind-gate.json"
```

Blind gate 会拒绝：

- candidate ID 与 selection 不同；
- candidate revision 不同；
- algorithm/profile/runtime identity 不同；
- baseline/candidate blind ground truth 不同；
- policy gate 不通过。

## 9. P1 指标

除现有 sequence/cue/onset/offset/cut/overlap 指标外，P1 增加 cut boundary：

```text
cut_boundary_match_count
cut_boundary_mae_ms
cut_boundary_p50_ms
cut_boundary_p90_ms
cut_boundary_p95_ms
cut_boundary_within_250ms_rate
cut_boundary_within_500ms_rate
```

不要单独追求低 cut-boundary MAE。必须同时看 cut recall/coverage，防止“只命中容易 cut”。

## 10. GitHub Actions 能做 / 不能做

### 可以做

- Python 3.10 / 3.12 / 3.14 contract tests；
- synthetic split-isolation tests；
- candidate lock tests；
- synthetic cut boundary metrics；
- strict calibration -> selection -> blind gate E2E；
- privacy scan / docs / environment / diff checks；
- ASR dependency environment check。

### 当前做不到且不得伪造

如果真实私有音频、人工 reference SRT、真实 cut/overlap truth 没有提供给 runner，GitHub Actions **不能**产生真实：

- 歌曲级准确率；
- 中文/英文/韩文/日文/粤语真实分组指标；
- 真实 cut/overlap P/R；
- 真实 cue boundary MAE/P95；
- 真实 runtime/review-density 生产结论。

这些必须在有授权 private dataset 的环境实际运行后再汇报。

## 11. 当前后续顺序

1. PR #8 framework 全绿并合并；
2. 建立第一版真实 private calibration / blind_test；
3. 根据真实 error breakdown 决定 P2 Editor Evidence + LanguageSpan；
4. 再判断 Forced Alignment / ASR v2 是否值得进入生产；
5. same-region cut+overlap joint acoustic model 只在真实数据证明价值后考虑。

在 real blind-test 前，不宣称固定百分比准确率提升。
