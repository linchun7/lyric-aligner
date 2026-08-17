# Lyric Aligner v4 生产运行手册

更新：2026-08-18  
主线算法版本：`4.0.0a8`  
Calibration profile：`production-bootstrap-2026-08-17-a7`

> main 已完成 production reconstruction、P1 strict calibration/blind、P1.1 dataset readiness、P2 editor shadow evidence。P3 新增局部 acoustic evidence planner/backend diagnostic/可选 faster-whisper executor；它们是 evidence，不替代 canonical lyric 或 Source-to-Mix。

## 1. 现有生产入口

```powershell
python scripts/v4_run.py ...
python scripts/v4_review.py template ...
python scripts/v4_review.py apply ...
python scripts/v4_recompose_overlap.py ...
python scripts/v4_rebuild_cut.py ...
python scripts/v4_compose_materializations.py ...
python scripts/v4_render.py ...
python scripts/v4_validate_release.py ...
```

Private dataset / calibration：

```powershell
python scripts/v4_dataset_readiness.py scaffold ...
python scripts/v4_dataset_readiness.py clone-candidate ...
python scripts/v4_dataset_readiness.py check ...
python scripts/v4_calibration_workflow.py evaluate ...
python scripts/v4_calibration_workflow.py select ...
python scripts/v4_calibration_workflow.py blind ...
```

Editor shadow：

```powershell
python scripts/v4_editor_evidence.py ...
```

## 2. 检查当前 acoustic backend

```powershell
python scripts/v4_alignment_backends.py
```

只检查 package/command discovery；不会加载模型。

要求 faster-whisper mix ASR capability：

```powershell
python scripts/v4_alignment_backends.py `
  --faster-whisper-model-id "<model-id>" `
  --require-capability mix_asr `
  --require-execution-ready
```

要求 external forced aligner：

```powershell
python scripts/v4_alignment_backends.py `
  --external-forced-aligner-command "<command>" `
  --require-capability source_forced_alignment `
  --require-execution-ready
```

缺 capability/配置时退出码 2。不要把 `available=true` 理解成模型已在 singing 上验证。

## 3. 生成 local alignment plan

只用 run/review issues：

```powershell
python scripts/v4_plan_alignment.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --run "output/<任务>/v4/<effective-run>.json" `
  --run-artifact "output/<任务>/v4/<effective-run>.artifact.json" `
  --out "output/<任务>/v4/alignment/plan.json" `
  --artifact-out "output/<任务>/v4/alignment/plan.artifact.json"
```

加入 P2 editor shadow：

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

Planner 默认：

```text
mix_context_ms = 1500
source_context_ms = 1000
editor_boundary_disagreement_ms = 500
editor_ambiguous_margin_max = 0.08
include_editor_missing = false
max_jobs = 200
```

这些是 bootstrap planning 参数，不是 final release 阈值。

Plan artifact：

```text
alignment_job_planning / alignment_plan
mode = plan_only
backend_execution_performed = false
```

`plan_truncated=true` 时说明 `max_jobs` 截断，不能把计划描述为完整覆盖。

## 4. 执行局部 faster-whisper evidence

真实环境已经安装 faster-whisper、模型可访问时：

```powershell
python scripts/v4_execute_asr_evidence.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --plan "output/<任务>/v4/alignment/plan.json" `
  --plan-artifact "output/<任务>/v4/alignment/plan.artifact.json" `
  --run "output/<任务>/v4/<effective-run>.json" `
  --run-artifact "output/<任务>/v4/<effective-run>.artifact.json" `
  --model-id "<faster-whisper-model-id>" `
  --device cpu `
  --compute-type int8 `
  --out "output/<任务>/v4/alignment/asr_evidence.json" `
  --artifact-out "output/<任务>/v4/alignment/asr_evidence.artifact.json"
```

只执行特定 jobs：

```powershell
... --job-id "<job-id>" --job-id "<job-id>"
```

Executor 只跑 plan 中请求 `mix_asr` 的 jobs；没有 ASR job 时不会加载模型。

每个 job 使用 local `clip_timestamps`，并启用：

```text
word_timestamps=true
condition_on_previous_text=false
vad_filter=false
```

语言：en/zh/ko/ja 显式 hint；yue/unknown/auto 不强制语言。

## 5. ASR evidence privacy

默认**不写原始 ASR text**，只写：

```text
text SHA
segment start/end
avg_logprob
no_speech_prob
compression_ratio
word start/end/probability/text SHA
detected language/probability
canonical local support score
```

只有在明确 private output 场景才加：

```powershell
--include-private-text
```

此时 artifact 会记录 `raw_private_text_included=true`。不要把带 raw text 的 output 放到公开仓库。

## 6. Lineage 失败时怎么办

Planner/Executor 都会拒绝：

- task input hash 变化；
- source run 不匹配；
- timeline artifact 不属于 run；
- editor evidence 来自另一个 run；
- plan artifact 来自另一个 task/run；
- plan canonical text SHA 与当前 timeline 不同；
- 请求 job ID 不存在。

遇到这些错误应从对应上游 stage 重跑，不手改 artifact ID/SHA。

## 7. Forced Alignment 当前如何用

当前版本**没有内置生产 forced aligner executor**。

Planner 会为有 source timestamp 的 line 请求 `source_forced_alignment` capability；`v4_alignment_backends.py` 可检查一个已配置 external command 是否存在。

如果没有明确选定/安装/验证 backend，结果应是 unavailable/unready，而不是自动回退到 ASR 或假装 forced alignment 完成。

## 8. GitHub Actions 真实边界

GitHub CI 可以验证 planner、artifact、backend discovery、fake faster-whisper executor contract，并在 ASR environment job 安装 `requirements-asr.txt` 检查 faster-whisper package。

当前 CI **不下载真实 Whisper model，也没有用户 private song/reference truth**，所以不能证明：

- real-song ASR word timing；
- large-v3/turbo 实际模型输出；
- WhisperX/SOFA/MMS 可用；
- 各语言真实准确率改善。

这些必须在真实模型/私有音频环境执行后记录。

## 9. 下一步

1. P3 planner/backend/executor latest-head CI 全绿后合入；
2. 用真实 private dataset 运行 P1 + P2/P3 shadow evidence；
3. 根据 error breakdown 决定 two-pass ASR、具体 forced-aligner adapter；
4. 只有 real blind gate 证明收益后，再做自动 boundary fusion/final evidence-family release gate。
