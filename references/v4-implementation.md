# Lyric Aligner v4 实施记录与关键代码说明

> 当前主线算法仍为 `4.0.0a8`。P2-P6 都是 evidence/diagnostic 层；canonical lyric 仍是 final text/order truth，Source-to-Mix 仍是 primary timing truth。

## 1. 当前分层

```text
lyric_aligner/
  alignment/
    backends.py       # P3 backend capability/readiness
    planner.py        # P3 bounded local evidence jobs
    asr_executor.py   # P3 bounded faster-whisper execution
    asr_routing.py    # P5 weak first-pass -> second-pass plan
    asr_second_pass.py# P6 execute selected second pass + composite
  evidence/
    editor.py         # P2 editor shadow
    fusion.py         # P4 multi-family shadow fusion
  assets/ audio/ contracts/ evaluation/ pipeline/ review/ text/ timeline/ qa/
```

关键 evidence CLI：

```text
v4_editor_evidence.py
v4_alignment_backends.py
v4_plan_alignment.py
v4_execute_asr_evidence.py
v4_fuse_evidence.py
v4_plan_asr_second_pass.py
v4_execute_asr_second_pass.py
```

## 2. Authority graph

```text
Canonical lyric -> final text/order truth
Source-to-Mix  -> primary timing truth
TrackAsset     -> source/canonical identity truth
Editor SRT     -> P2 auxiliary shadow evidence
First ASR      -> P3 optional local acoustic evidence
Fusion         -> P4 uncalibrated diagnostic
Second routing -> P5 plan-only escalation
Second execute -> P6 composite ASR evidence
```

禁止：

```text
ASR text -> final canonical lyric
Editor time -> silently replace timeline
P4 HIGH -> release approval
P5 plan -> pretend model executed
P6 empty selection -> execute all jobs
missing forced aligner -> fake fallback
```

## 3. P5 foundation

P5 output：

```text
mode = second_pass_plan_only
policy_calibrated = false
backend_execution_performed = false
scope_policy = reuse_exact_first_pass_local_windows
selected_job_ids = [...]
```

P5 绑定 first/second model IDs、exact original alignment plan 和 first-pass ASR artifact；routing threshold 仍未校准。

## 4. P6 `alignment/asr_second_pass.py`

### 4.1 Original plan is the execution authority

P6 首先索引 original P3 `mix_asr` jobs。

对每个 P5 selected job，验证：

```text
occurrence_id
track_id
canonical_line_index
language_profile
mix_window_ms
source_window_ms
canonical_text_sha256
```

与 original row 完全相同。

随后实际 execution plan 由 original rows 重建：

```python
execution_plan["jobs"] = [original[job_id] for job_id in selected_order]
```

因此 P5 row 不能改变实际执行窗口。

### 4.2 Empty selection

关键：

```text
selected_order = []
execution_plan.jobs = []
```

传入 P3 `execute_faster_whisper_jobs()` 后会在 model factory 前返回：

```text
model_loaded = false
job_count = 0
jobs = []
```

所以 P6 不复用 P3 CLI `_filter_jobs([], no_ids) -> whole plan` 的手工选择语义。

### 4.3 Second model validation

要求：

```text
P5 second_pass_model_id == P6 FasterWhisperExecutionConfig.model_id
first_pass config.model_id != second model
```

First-pass jobs 还必须属于 original `mix_asr` plan；foreign job 失败。

### 4.4 Composite merge

按 original planner 顺序：

```text
if second result exists:
    use second
    evidence_pass=second
    evidence_model_id=<second model>
elif first result exists:
    retain first
    evidence_pass=first
    evidence_model_id=<first model>
else:
    no fabricated row
```

输出统计：

```text
first_pass_input_job_count
first_pass_retained_job_count
second_pass_selected_job_count
second_pass_executed_job_count
job_count
model_loaded_second_pass
```

## 5. Composite privacy

`_apply_output_privacy()` 以 P6 **当前** `include_private_text` 为准。

默认 false：

```text
pop observed_text
pop segments[].text
pop segments[].words[].text
```

该处理同时作用于 second rows 和 retained first rows，避免第一遍 private-text opt-in 泄漏到默认 composite。

Canonical raw text只在内存中作为本地 canonical similarity input，不写入 artifact。

## 6. `v4_execute_asr_second_pass.py`

输入：

```text
task manifest
original alignment plan + artifact
first-pass ASR evidence + artifact
P5 second-pass plan + artifact
source effective run + artifact
```

验证：

- exact task input hashes；
- original plan belongs to source run；
- first-pass belongs to exact plan + source run；
- P5 plan belongs to exact plan + exact first-pass + source run；
- first/P5 artifacts bind required upstream IDs；
- canonical timeline artifacts are exact source-run upstream；
- original plan canonical text SHA matches current canonical timeline；
- P6 model ID matches P5 second model。

## 7. P6 artifact

为了直接兼容 P4：

```text
stage = asr_evidence_local
role = asr_evidence
backend = faster_whisper
mode = composite_second_pass_evidence
```

Artifact upstream：

```text
plan artifact
first-pass artifact
P5 second-pass plan artifact
source run artifact
canonical timeline artifacts
```

Normalized config 保存：

```text
first_pass_model_id
second_pass_model_id
execution device/compute/beam/temperature
include_private_text
scope_policy
source artifact IDs
mix audio SHA
```

## 8. P4 compatibility

P4 的 ASR family 依赖 `backend=faster_whisper` + `jobs[]` 的 line/segment/support/language 字段。P6 composite 保留这些字段，因此可以直接替代 first-pass ASR artifact 作为 P4 输入。

P4 不把 `evidence_pass` 当成新的 authority，只作为审计元数据。

## 9. Tests

Package fake-model：

- only selected job executes；
- exact `clip_timestamps`；
- good first job retained；
- weak selected job replaced；
- zero selected -> no factory/model load；
- changed P5 window fail；
- wrong second model fail；
- selected IDs/jobs mismatch fail。

CLI E2E：

- synthetic task/run/timeline/plan/first/P5 artifacts；
- P5 empty selection；
- deliberately nonexistent accuracy model ID；
- command still succeeds because model must not load；
- composite retains first result；
- retained first raw observed/segment/word text stripped；
- P5 artifact appears in output upstream。

## 10. CI / real runtime boundary

GitHub Actions can prove the execution contract with fake model and zero-selection path. It currently does not download/run a real accuracy Whisper model and has no private real-song reference truth.

因此 Actions 不能证明模型真实歌声收益，也不能把 P5/P6 变成 final timing/release authority。

## 11. 下一阶段

优先剩余：

1. production forced-aligner adapter；
2. private real-song calibration/blind 数据；
3. local vocal separation / singing refinement；
4. calibrated evidence-family boundary application/release gate；
5. same-region cut+overlap joint model。
