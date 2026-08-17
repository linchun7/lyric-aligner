# Lyric Aligner v4 实施记录与关键代码说明

> 当前主线算法仍为 `4.0.0a8`。P2/P3/P4/P5 都是 evidence/diagnostic 层；canonical lyric 仍是 final text/order truth，Source-to-Mix 仍是 primary timing truth。

## 1. 当前分层

```text
lyric_aligner/
  alignment/
    backends.py      # P3 backend capability/readiness
    planner.py       # P3 bounded local evidence job planning
    asr_executor.py  # P3 bounded faster-whisper executor
    asr_routing.py   # P5 weak first-pass -> bounded second-pass plan
  assets/
  audio/
  contracts/
  evidence/
    editor.py        # P2 editor shadow evidence
    fusion.py        # P4 multi-family shadow fusion
  evaluation/
  pipeline/
  review/
  text/
  timeline/
  qa/
```

关键 evidence CLI：

```text
v4_editor_evidence.py
v4_alignment_backends.py
v4_plan_alignment.py
v4_execute_asr_evidence.py
v4_fuse_evidence.py
v4_plan_asr_second_pass.py
```

## 2. Authority graph

```text
Canonical lyric -> final text/order truth
Source-to-Mix  -> primary timing truth
TrackAsset     -> source/canonical identity truth
Editor SRT     -> P2 auxiliary shadow evidence
ASR            -> P3 optional local acoustic evidence
Fusion         -> P4 uncalibrated shadow diagnostic
Second-pass routing -> P5 plan-only evidence escalation
```

禁止：

```text
ASR text -> final canonical lyric
Editor time -> silently replace timeline
P4 HIGH -> release approval
P5 second-pass plan -> pretend model executed
missing forced aligner -> fake fallback
```

## 3. P3/P4 基础

P3 已提供：

- backend truthfulness：`available != execution_ready != validated_on_singing`；
- local planner；
- bounded faster-whisper executor；
- exact task/run/timeline/model lineage；
- default raw-ASR-text privacy。

P4 已提供：

- source/editor/ASR line-level shadow fusion；
- LOW/MEDIUM/HIGH/CONFLICT uncalibrated states；
- `release_gate_eligible=false`；
- cross-run/unknown-line fail-closed。

## 4. P5 `alignment/asr_routing.py`

### 4.1 Input contracts

```text
alignment_plan.mode == plan_only
alignment_plan.backend_execution_performed == false
first_pass_evidence.backend == faster_whisper
```

P5 只遍历原 alignment plan 中请求 `mix_asr` 的 jobs。`source_forced_alignment`-only jobs 不会被改造成 ASR jobs。

### 4.2 First-pass quality snapshot

每个 first-pass job 读取：

```text
canonical_text_support_score
language_probability
segments[].avg_logprob
segments[].no_speech_prob
```

聚合：

```text
segment_count
avg_logprob
max_no_speech_prob
segment_quality_complete
```

缺 `avg_logprob/no_speech_prob` 不能默认当作 0 或“质量良好”；明确产生 `missing_segment_quality`。

### 4.3 Bootstrap route reasons

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

Config：

```text
min_canonical_text_support=0.65
min_avg_logprob=-0.75
max_no_speech_prob=0.60
min_language_probability=0.65
reroute_missing_segments=true
reroute_missing_line_support=true
max_jobs=100
```

这些参数未校准，只用于 evidence escalation。

## 5. Exact scope reuse

P5 不重新定位音频，也不修改 original planner window。

每个 selected row 原样携带：

```text
job_id
occurrence_id
track_id
ordinal
canonical_line_index
language_profile
mix_window_ms
source_window_ms
canonical_text_sha256
```

并固定：

```text
scope_policy = reuse_exact_first_pass_local_windows
execution_state = second_pass_planned_not_executed
```

这保证 second pass 不扩散成整曲 ASR。

## 6. Priority-aware max_jobs

旧 draft 按 line index 排序会在 `max_jobs` 时错误保留“较早但低优先级”的 job，现已修复。

排序键：

```text
1. first_pass_priority: high > medium > low
2. second_pass_severity_rank
   missing evidence
   > missing segment/quality
   > low canonical support
   > other weak signals
3. -len(second_pass_reasons)
4. ordinal / line / window / occurrence / job_id
```

`second_pass_severity_rank` 只是 deterministic routing class，不是 calibrated confidence score。

## 7. Model lineage

`scripts/v4_plan_asr_second_pass.py` 要求 first-pass payload：

```text
config.model_id
```

并强制：

```text
second_pass_model_id != first_pass_model_id
```

输出/artifact 同时绑定 first/second model ID，避免同模型重复跑被误标成 accuracy escalation。

## 8. P5 artifact

```text
stage = asr_second_pass_planning
role  = asr_second_pass_plan
```

Upstream：

```text
exact alignment plan artifact
exact first-pass ASR evidence artifact
```

Payload 还绑定：

```text
source_run_artifact_id
source_plan_artifact_id
source_first_pass_artifact_id
first_pass_model_id
second_pass_model_id
selected_job_ids
```

First-pass evidence 出现不在 original plan 的 mix_asr job，或跨 plan/run/task，必须 fail-closed。

## 9. Second-pass execution 仍未伪造

P5 当前**不实现/不声称第二模型已经执行**。

实际执行应复用 P3 bounded executor，并只传 P5 `selected_job_ids` + `second_pass_model_id`；executor 仍使用 original P3 plan 的 exact local window。

把 plan artifact 当成 ASR output 是错误的。

## 10. Tests

Direct unit：

- good evidence skip；
- weak/missing evidence reroute；
- missing segment quality reroute；
- forced-only skip；
- foreign job fail；
- priority-aware truncation；
- invalid priority fail；
- nonfinite thresholds fail。

Artifact E2E：

- task/plan/first-pass lineage；
- exact local window reuse；
- first/second model IDs；
- same-model rejection；
- first-pass artifact tamper rejection；
- raw lyric privacy。

## 11. Calibration / next path

```text
P3 first-pass local ASR
-> P5 weak-evidence second-pass plan
-> P3 executor with accuracy model + selected job IDs
-> P4/future fusion as another evidence source
-> private calibration/blind
```

还需要：

- second-pass execution orchestration artifact；
- production forced-aligner adapter；
- private real-song error breakdown；
- calibrated family admission/release gate；
- vocal-separation/local singing refinement；
- same-region cut+overlap joint model。

GitHub CI 只能验证 routing/lineage/privacy/contracts，不证明 second-pass large model 的真实歌声收益。
