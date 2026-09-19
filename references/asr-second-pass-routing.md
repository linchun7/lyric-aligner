# ASR Second-Pass Routing — Bounded Accuracy Escalation

状态：P5 bootstrap / plan-only  
日期：2026-08-18

## 1. 目标

P5 只解决一个问题：第一遍 local faster-whisper evidence 弱或缺失时，哪些**已经存在的 local jobs**值得再跑一个更准确的第二模型。

P5 不重新发现整曲区域，也不扩大时间窗：

```text
P3 alignment plan local job
        ↓
first-pass local ASR evidence
        ↓
P5 weak/missing routing
        ↓
second-pass plan using exact same local window
```

固定：

```text
mode = second_pass_plan_only
policy_calibrated = false
backend_execution_performed = false
scope_policy = reuse_exact_first_pass_local_windows
```

P5 不修改 canonical lyric、Source-to-Mix、timeline、FINAL.srt 或 release gate。

## 2. 默认 weak-evidence signals

Bootstrap routing parameters：

```text
min_canonical_text_support = 0.65
min_avg_logprob = -0.75
max_no_speech_prob = 0.60
min_language_probability = 0.65
reroute_missing_segments = true
reroute_missing_line_support = true
max_jobs = 100
```

可能的 route reasons：

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

这些阈值只决定“要不要补一次证据”，不是 final confidence/release threshold。

## 3. Scope safety

Second-pass job 直接复制原 planner job：

```text
job_id
occurrence_id
track_id
canonical_line_index
mix_window_ms
source_window_ms
canonical_text_sha256
```

P5 不扩大 `mix_window_ms`，不生成 full-song ASR request，也不把 forced-alignment-only job 转成 ASR job。

## 4. Truncation priority

如果 weak jobs 超过 `max_jobs`，不能按歌词行号简单截断。

排序：

1. first-pass planner priority：`high > medium > low`；
2. weak-evidence severity class：missing evidence > missing segment/quality > low canonical support > other weak signals；
3. second-pass reason count；
4. ordinal / line / window / occurrence / job ID deterministic tie-break。

因此高优先级严重弱证据不会被较早行号的低优先级 job 挤掉。

`eligible_second_pass_job_count_before_truncation` 与 `second_pass_plan_truncated` 必须显式输出。

## 5. Model lineage

CLI 必须从 first-pass evidence 读取：

```text
config.model_id
```

且要求：

```text
second_pass_model_id != first_pass_model_id
```

这是为了避免“名义 two-pass，实际同一模型重复跑”。当前 contract 不自动判断模型质量高低；运营侧应把第一遍设为 fast model，把第二遍设为 accuracy model。

例如：

```text
first pass  -> faster-whisper large-v3-turbo
second pass -> faster-whisper large-v3
```

模型是否真正可用仍由 runtime/backend 环境决定；P5 planner 本身不会下载或执行模型。

## 6. Artifact lineage

CLI：

```text
scripts/v4_plan_asr_second_pass.py
```

Output artifact：

```text
stage = asr_second_pass_planning
role  = asr_second_pass_plan
```

必须绑定：

- task fingerprint；
- exact P3 alignment plan artifact；
- exact first-pass ASR evidence artifact；
- first-pass evidence 的 `source_plan_artifact_id`；
- same source run identity；
- first/second model IDs。

First-pass artifact hash 被改动、来自另一个 plan/run、或出现 plan 中不存在的 first-pass job 都必须 fail-closed。

## 7. 执行边界

P5 当前**只生成第二遍计划，不执行第二模型**。

真正执行第二遍时，应复用现有 P3 bounded executor，并只传：

```text
selected_job_ids
second_pass_model_id
same original local windows
```

不能把 second-pass planner 解释成模型已运行。

## 8. GitHub Actions 能 / 不能做

CI 可以验证：

- weak/missing routing；
- exact-window reuse；
- priority-aware truncation；
- same-model rejection；
- plan/first-pass lineage；
- artifact privacy；
- deterministic routing；
- P0-P4 regressions。

CI 当前不下载/运行真实 second-pass large model，也没有 private real-song truth，因此不能证明：

- large-v3 比 turbo 在本项目真实歌声上改善多少；
- bootstrap weak thresholds 最优；
- second-pass evidence 可以自动修改最终 timing。

这些必须通过授权 private calibration + blind_test 验证。
