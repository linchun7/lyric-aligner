# Lyric Aligner v4 生产运行手册

更新：2026-08-18  
主线算法版本：`4.0.0a8`  
Calibration profile：`production-bootstrap-2026-08-17-a7`

> main 已完成 production reconstruction、P1/P1.1、P2 editor shadow、P3 local acoustic evidence、P4 shadow fusion、P5 second-pass routing。P6 新增 **second-pass execution + composite ASR evidence**，仍不修改 canonical lyric 或 final timeline。

## 1. 主链与 evidence 入口

```powershell
python scripts/v4_run.py ...
python scripts/v4_review.py ...
python scripts/v4_recompose_overlap.py ...
python scripts/v4_rebuild_cut.py ...
python scripts/v4_compose_materializations.py ...
python scripts/v4_render.py ...
python scripts/v4_validate_release.py ...
```

Dataset/calibration：

```powershell
python scripts/v4_dataset_readiness.py scaffold ...
python scripts/v4_dataset_readiness.py clone-candidate ...
python scripts/v4_dataset_readiness.py check ...
python scripts/v4_calibration_workflow.py evaluate ...
python scripts/v4_calibration_workflow.py select ...
python scripts/v4_calibration_workflow.py blind ...
```

Editor/fusion：

```powershell
python scripts/v4_editor_evidence.py ...
python scripts/v4_fuse_evidence.py ...
```

## 2. 第一遍 local ASR

```powershell
python scripts/v4_alignment_backends.py
python scripts/v4_plan_alignment.py ...
python scripts/v4_execute_asr_evidence.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --plan "output/<任务>/v4/alignment/plan.json" `
  --plan-artifact "output/<任务>/v4/alignment/plan.artifact.json" `
  --run "output/<任务>/v4/<effective-run>.json" `
  --run-artifact "output/<任务>/v4/<effective-run>.artifact.json" `
  --model-id "<fast-first-pass-model>" `
  --device cpu `
  --compute-type int8 `
  --out "output/<任务>/v4/alignment/asr_first.json" `
  --artifact-out "output/<任务>/v4/alignment/asr_first.artifact.json"
```

默认不带 `--include-private-text`。

## 3. P5 第二遍规划

```powershell
python scripts/v4_plan_asr_second_pass.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --plan "output/<任务>/v4/alignment/plan.json" `
  --plan-artifact "output/<任务>/v4/alignment/plan.artifact.json" `
  --first-pass-evidence "output/<任务>/v4/alignment/asr_first.json" `
  --first-pass-artifact "output/<任务>/v4/alignment/asr_first.artifact.json" `
  --second-pass-model-id "<accuracy-model>" `
  --out "output/<任务>/v4/alignment/asr_second_plan.json" `
  --artifact-out "output/<任务>/v4/alignment/asr_second_plan.artifact.json"
```

检查：

```text
mode = second_pass_plan_only
backend_execution_performed = false
scope_policy = reuse_exact_first_pass_local_windows
```

## 4. P6 执行第二遍并合成完整 ASR evidence

```powershell
python scripts/v4_execute_asr_second_pass.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --plan "output/<任务>/v4/alignment/plan.json" `
  --plan-artifact "output/<任务>/v4/alignment/plan.artifact.json" `
  --first-pass-evidence "output/<任务>/v4/alignment/asr_first.json" `
  --first-pass-artifact "output/<任务>/v4/alignment/asr_first.artifact.json" `
  --second-pass-plan "output/<任务>/v4/alignment/asr_second_plan.json" `
  --second-pass-plan-artifact "output/<任务>/v4/alignment/asr_second_plan.artifact.json" `
  --run "output/<任务>/v4/<effective-run>.json" `
  --run-artifact "output/<任务>/v4/<effective-run>.artifact.json" `
  --model-id "<accuracy-model>" `
  --device cpu `
  --compute-type int8 `
  --out "output/<任务>/v4/alignment/asr_composite.json" `
  --artifact-out "output/<任务>/v4/alignment/asr_composite.artifact.json" `
  --git-commit "<commit>"
```

`--model-id` 必须等于 P5 plan 的 `second_pass_model_id`，且不能等于 first-pass model ID。

## 5. Empty-selection 行为

如果 P5 输出：

```text
selected_job_ids = []
jobs = []
```

仍然可以安全运行 P6。

预期：

```text
model_loaded_second_pass = false
second_pass_selected_job_count = 0
second_pass_executed_job_count = 0
first_pass_retained_job_count = <已有第一遍 jobs>
```

P6 不会把空列表解释成“跑全部”。

## 6. Composite 输出

```text
stage = asr_evidence_local
role = asr_evidence
backend = faster_whisper
mode = composite_second_pass_evidence
policy_calibrated = false
scope_policy = reuse_exact_first_pass_local_windows
```

每个 job：

```text
evidence_pass = first | second
evidence_model_id = <实际模型>
```

未升级 job 保留 first-pass；P5 selected jobs 使用 second-pass result 替换。

随后 P4 直接改用 composite：

```powershell
python scripts/v4_fuse_evidence.py `
  ... `
  --asr-evidence "output/<任务>/v4/alignment/asr_composite.json" `
  --asr-evidence-artifact "output/<任务>/v4/alignment/asr_composite.artifact.json"
```

## 7. Exact-window 安全

P6 对 P5 selected job 与 original P3 plan 比较：

```text
occurrence_id
track_id
canonical_line_index
language_profile
mix_window_ms
source_window_ms
canonical_text_sha256
```

任何变化直接失败。实际执行使用 original P3 row 构造 clip。

## 8. Privacy

默认 P6 不保存 raw ASR text。

即使 first-pass 文件曾用 `--include-private-text`，P6 默认 composite 也会删除 retained first-pass 中：

```text
observed_text
segments[].text
segments[].words[].text
```

只有 P6 本次明确 `--include-private-text` 才允许 composite 保存 raw ASR text。

## 9. Artifact lineage

P6 output artifact upstream：

```text
original alignment plan
first-pass ASR
P5 second-pass plan
source run
canonical timelines
```

任一 artifact output hash 不匹配、跨 task/plan/run、P5 未绑定 exact first-pass，都失败。

## 10. GitHub Actions 能 / 不能做

CI 可以真实验证：

- fake-model selected execution；
- exact local clip；
- empty selection 不加载模型；
- composite replace/retain；
- model/plan/window lineage；
- retained private text stripping；
- zero-selection CLI E2E；
- P0-P5 regression。

CI 当前**不能**证明：

- real accuracy-model 已在公共 runner 成功加载/执行；
- large-v3 对真实歌曲比 turbo 提升多少；
- second-pass composite 可自动修正 final timing。

真实 selected-job execution 需要 private/local runtime 具备相应模型下载/缓存/硬件环境；真实收益需 private calibration/blind-test。

## 11. 尚未完成

优先剩余：

1. production forced-aligner adapter + model/language/cache/license lineage；
2. private real-song calibration/blind 数据填充和误差分析；
3. vocal separation / local singing alignment（仅高风险窗口）；
4. calibrated evidence-family boundary application/release gate；
5. same-region cut+overlap joint acoustic model。
