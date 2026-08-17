# Lyric Aligner v4 生产运行手册

更新：2026-08-18  
主线算法版本：`4.0.0a8`  
Calibration profile：`production-bootstrap-2026-08-17-a7`

> main 已完成 production reconstruction、P1/P1.1、P2 editor shadow、P3 local acoustic evidence、P4 shadow fusion。P5 新增 **second-pass ASR plan-only routing**；它只决定哪些原 local jobs 值得换一个 accuracy model 再跑，不扩大到整曲，也不自动修改字幕。

## 1. 现有生产 / evidence 入口

```powershell
python scripts/v4_run.py ...
python scripts/v4_review.py ...
python scripts/v4_recompose_overlap.py ...
python scripts/v4_rebuild_cut.py ...
python scripts/v4_compose_materializations.py ...
python scripts/v4_render.py ...
python scripts/v4_validate_release.py ...
```

Calibration / dataset：

```powershell
python scripts/v4_dataset_readiness.py scaffold ...
python scripts/v4_dataset_readiness.py clone-candidate ...
python scripts/v4_dataset_readiness.py check ...
python scripts/v4_calibration_workflow.py evaluate ...
python scripts/v4_calibration_workflow.py select ...
python scripts/v4_calibration_workflow.py blind ...
```

Editor / fusion：

```powershell
python scripts/v4_editor_evidence.py ...
python scripts/v4_fuse_evidence.py ...
```

## 2. P3 第一遍 local ASR

Backend check：

```powershell
python scripts/v4_alignment_backends.py
```

Local plan：

```powershell
python scripts/v4_plan_alignment.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --run "output/<任务>/v4/<effective-run>.json" `
  --run-artifact "output/<任务>/v4/<effective-run>.artifact.json" `
  --editor-evidence "output/<任务>/v4/editor/editor_evidence.json" `
  --editor-evidence-artifact "output/<任务>/v4/editor/editor_evidence.artifact.json" `
  --out "output/<任务>/v4/alignment/plan.json" `
  --artifact-out "output/<任务>/v4/alignment/plan.artifact.json"
```

第一遍 faster-whisper evidence：

```powershell
python scripts/v4_execute_asr_evidence.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --plan "output/<任务>/v4/alignment/plan.json" `
  --plan-artifact "output/<任务>/v4/alignment/plan.artifact.json" `
  --run "output/<任务>/v4/<effective-run>.json" `
  --run-artifact "output/<任务>/v4/<effective-run>.artifact.json" `
  --model-id "mobiuslabsgmbh/faster-whisper-large-v3-turbo" `
  --device cpu `
  --compute-type int8 `
  --out "output/<任务>/v4/alignment/asr_first.json" `
  --artifact-out "output/<任务>/v4/alignment/asr_first.artifact.json"
```

模型 ID 只是示例配置；实际可用性必须由 runtime/backend 明确确认。

## 3. P5 生成第二遍 ASR 计划

```powershell
python scripts/v4_plan_asr_second_pass.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --plan "output/<任务>/v4/alignment/plan.json" `
  --plan-artifact "output/<任务>/v4/alignment/plan.artifact.json" `
  --first-pass-evidence "output/<任务>/v4/alignment/asr_first.json" `
  --first-pass-artifact "output/<任务>/v4/alignment/asr_first.artifact.json" `
  --second-pass-model-id "Systran/faster-whisper-large-v3" `
  --out "output/<任务>/v4/alignment/asr_second_plan.json" `
  --artifact-out "output/<任务>/v4/alignment/asr_second_plan.artifact.json" `
  --git-commit "<commit>"
```

P5 只 plan，不执行模型。

Output：

```text
stage = asr_second_pass_planning
role  = asr_second_pass_plan
mode = second_pass_plan_only
policy_calibrated = false
backend_execution_performed = false
scope_policy = reuse_exact_first_pass_local_windows
```

## 4. 默认 routing 参数

```text
min_canonical_text_support = 0.65
min_avg_logprob = -0.75
max_no_speech_prob = 0.60
min_language_probability = 0.65
reroute_missing_segments = true
reroute_missing_line_support = true
max_jobs = 100
```

可覆盖：

```powershell
--min-canonical-text-support 0.65
--min-avg-logprob -0.75
--max-no-speech-prob 0.60
--min-language-probability 0.65
--max-jobs 100
```

这些是未校准 routing 参数，不是 final confidence/release threshold。

## 5. 哪些情况进入第二遍

可能的 reasons：

```text
missing_first_pass_evidence
missing_segments
missing_segment_quality
missing_canonical_text_support
low_canonical_text_support
low_avg_logprob
high_no_speech_probability
low_language_probability
```

第一遍证据足够好的 job 不进入第二遍。

## 6. Scope / priority 安全

P5 **不扩大时间窗**。每个 second-pass row 复用原 planner 的：

```text
job_id
mix_window_ms
source_window_ms
canonical_text_sha256
```

当 eligible jobs > `max_jobs`：

```text
planner priority high > medium > low
then evidence severity
then reason count
then deterministic identity
```

检查：

```text
eligible_second_pass_job_count_before_truncation
second_pass_job_count
second_pass_plan_truncated
priority_counts
reason_counts
```

`second_pass_plan_truncated=true` 时不能把计划描述为完整覆盖。

## 7. Model lineage

First-pass evidence 必须有：

```text
config.model_id
```

且：

```text
second_pass_model_id != first_pass_model_id
```

否则 CLI 失败。这样避免同模型重复跑却被称为 accuracy escalation。

推荐运行思路是 fast first pass / accuracy second pass，例如 turbo -> large-v3；但模型是否真的更优必须由 private real-song evaluation 决定。

## 8. 真正执行第二遍

P5 当前不提供独立 second-pass executor。正确执行方式是：读取 `selected_job_ids`，再调用现有 P3 `v4_execute_asr_evidence.py`，使用 second-pass model ID，并**只传这些 job IDs**。

概念：

```powershell
python scripts/v4_execute_asr_evidence.py `
  ... `
  --model-id "<second-pass-model-id>" `
  --job-id "<selected-job-id-1>" `
  --job-id "<selected-job-id-2>" `
  ...
```

不得因为 P5 生成计划就声称 second model 已执行。

## 9. Lineage 失败时怎么办

P5 会拒绝：

- task input hash 变化；
- plan artifact 不匹配；
- first-pass artifact output hash 不匹配；
- first-pass evidence 来自另一个 plan；
- first-pass artifact 没绑定 exact plan；
- first-pass/source-run identity 不同；
- first-pass evidence 出现原 plan 不存在的 mix_asr job；
- first-pass model ID 缺失；
- second-pass model ID 与 first-pass 相同。

不要手改 artifact ID/SHA 绕过。

## 10. GitHub Actions 能 / 不能做

CI 可以验证：routing、priority-aware truncation、exact-window reuse、model lineage、artifact lineage、privacy、P0-P4 regressions。

CI 当前不会下载/运行真实 second-pass large model，也没有 private real-song/reference truth，所以不能证明：

- large-v3 相对 turbo 的真实收益；
- bootstrap routing thresholds 最优；
- second-pass evidence 可自动改 final timing。

这些必须通过授权 private calibration + blind_test。
