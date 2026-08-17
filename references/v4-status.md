# Lyric Aligner v4 当前实施状态

更新日期：2026-08-18  
当前开发分支：`agent/v4-calibration-blind-test`  
当前 main：v4.0.0a8，squash commit `5c458d8327d2641ba053423fff3066d7fdd8ba3b`  
当前算法版本：`4.0.0a8`  
Calibration profile：`production-bootstrap-2026-08-17-a7`  
TrackAsset schema：`1.1`  
Review Decision schema：`1.2`

## 1. main 已完成能力

截至 a8，main 已完成主要生产重建链：

- fail-closed TrackAsset / canonical lyric single truth；
- harmonic HPSS + Chroma CENS/MFCC Source-to-Mix mapping；
- AFFINE-first / evidence-driven PIECEWISE_RATE；
- Selective Fine；
- candidate-level transition/TimeWarp review；
- confirmed-overlap 双路 canonical timeline recomposition；
- confirmed middle-cut local boundary localization；
- CUT_AWARE retained segments + explicit source gaps；
- line-LRC partial-cut fail-closed；
- Enhanced LRC/QRC canonical fragments；
- cut + overlap 在可证明互不冲突时的 `combined_recomposition`；
- final SRT/audit/QA/release strict artifact lineage。

a8 PR #7 head `d70101412549817d6ef3c474ee9babc647c9dfe9` 的 validate #407 在 ASR + Python 3.10/3.12/3.14 compile/docs/full unit-E2E/Skill/privacy/environment/diff-check 全绿后 squash merge 到 `5c458d83...`。

## 2. 当前开发目标：P1 Calibration / Blind Test

PR #8 不改声学阈值，目标是把“真实数据调参与盲测”从文档约定升级为机器可拒绝的流程。

正式入口：

```text
scripts/v4_calibration_workflow.py
```

核心链：

```text
calibration split only
  -> baseline/candidate evaluation
  -> explicit gate policy
  -> deterministic passing-candidate ranking
  -> immutable selection artifact
  -> candidate + baseline identity frozen
  -> blind_test first evaluation
  -> blind non-regression / acceptance gate
```

## 3. Dataset isolation / identity

严格 dataset schema `1.1` 使用：

```text
dataset_revision
source_group
```

规则：

- 每个 case 必须有 opaque `source_group`；
- 同一 source_group 不得跨 train/calibration/blind_test；
- calibration 阶段只读取 calibration prediction/QA；
- blind prediction/QA 可以在候选锁定前完全不存在；
- selected split ground truth 由 reference-SRT SHA、case metadata、expected cut/overlap/occurrence annotations 等形成 SHA-256 identity；
- prediction 内容不进入 ground-truth identity，因此 baseline/candidate 可使用不同输出但必须共享同一真值。

## 4. Candidate / baseline identity lock

P1 不只锁 candidate 名字，还锁：

```text
candidate_id
candidate_revision
algorithm_version
calibration_profile_version
calibration_profile_id
```

选定 split 内各 case 的 `FINAL.qa.json` 必须报告一致 runtime identity。

Calibration selection artifact 同时冻结：

- baseline candidate id/revision/runtime identity；
- selected candidate id/revision/runtime identity；
- calibration evaluation SHA；
- policy SHA；
- selection payload SHA。

Blind_test 若 candidate 或 baseline 在 calibration 后换了 commit/profile/runtime identity，直接拒绝。

## 5. Evaluation / gate metrics

保留现有 sequence-aware evaluator：

- sequence unit edit metrics；
- line exact precision/recall/F1；
- missing / extra / wrong-order；
- split / merge；
- onset / offset MAE、P50/P90/P95；
- cut precision/recall/F1；
- overlap precision/recall/IoU；
- track attribution；
- review density；
- runtime / publish-ready rate。

P1 新增 matched-cut boundary：

```text
cut_boundary_expected_count
cut_boundary_predicted_count
cut_boundary_match_count
cut_boundary_reference_coverage
cut_boundary_prediction_coverage
cut_boundary_mae_ms
cut_boundary_p50_ms
cut_boundary_p90_ms
cut_boundary_p95_ms
cut_boundary_within_250ms_rate
cut_boundary_within_500ms_rate
```

Cut matching 使用最大匹配数优先、总绝对误差最小的单调 DP；不再使用可能降低 match cardinality 的局部 greedy nearest-pair。

Cut boundary error 必须与 cut recall/coverage 一起看；不能用“只命中少数容易样本”制造低 MAE。

## 6. Gate policy

Gate policy 显式声明：

```text
scope
metric
direction = higher | lower
max_regression_abs
optional min_candidate / max_candidate
optional min_improvement_abs
calibration ranking
```

P1 还拒绝 negative / non-finite tolerance 以及无效 ranking direction。代码不内置“为了通过而调松”的隐藏全局阈值。Calibration 只在通过全部 gates 的候选之间按 policy ranking 选择。

## 7. Privacy

公开/可提交 evaluation artifacts 只允许：

- aggregate metrics；
- opaque case/source-group/dataset IDs；
- candidate/runtime/profile identities；
- SHA-256。

真实音频、歌词、reference/prediction SRT、逐 case 原始证据继续位于 private 数据目录，不进入公开仓库。

## 8. PR #8 最终 CI

Latest head：

```text
60bd697477c418c52b044810ecab8ce5dbeea018
```

validate #445：

- ASR environment：SUCCESS；
- Python 3.10：compileall / Documentation Contract / full unit-E2E / Skill / privacy / environment / diff-check 全绿；
- Python 3.12：同上，全绿；
- Python 3.14：同上，全绿。

此前 #431 只因 owning docs 缺失在 unit tests 前失败；#443 在 249 tests 中仅暴露一个低层兼容 gate 的 selection self-identity 字段名 bug。Latest head 已修复为 `selection_payload_sha256`（同时兼容旧实验字段）并由 #445 全量验证。

## 9. 当前仍未完成

### P1-B — real private dataset / real blind numbers

仓库当前没有可公开使用的真实私有音频/人工真值数据。因此不能伪造：

- 真实 cue boundary MAE；
- cut/overlap P/R；
- 真实语言分组准确率；
- runtime / review density 的生产结论。

框架合并后必须用用户授权的 private 数据真正运行。

### P2 — Editor Evidence + LanguageSpan final fusion

尚未进入 final production cue fusion。仓库已有 `language_spans.py` 基础，下一步重点是：editor evidence artifact、语言可靠度权重、与 canonical/source mapping 的边界融合，而不是重新做脚本识别。

目标仍为：zh/en direct text；ko/ja phonetic hint；yue/unknown text 降权或禁用；mixed per-span routing。

### P3 — Forced Alignment / ASR v2

尚未作为正式生产依赖。应由真实 P1 error breakdown 决定优先级，而不是先换更大 Whisper。

### Explicit BLOCK — same-region joint cut+overlap

Overlap 与 localized cut 落在同一声学区域时继续 BLOCK；只有真实数据证明频率/收益足够高时才做 joint acoustic model。

## 10. 当前正确表述

> **main 已完成 a8 的生产重建闭环；P1 calibration/blind-test framework 在 PR #8 latest head `60bd6974...` 已通过 validate #445 全量门禁，可合并。没有真实私有数据前，不宣称固定百分比准确率提升。**
