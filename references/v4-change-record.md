# Lyric Aligner v4 关键变更记录

> 所有实质性生产更新必须按 `documentation-contract.md` 在同一 PR 同步本文件。这里只记录已经进入代码的行为、兼容/迁移与验证边界。

## 已合入 main

- a3 production-first reconstruction：`cfa43f4c854b699819cd3acb0cfea575cd1a04c8`；
- a4 package-native render/release：`236d9d717229147ee1d1a8755d712e54db47a751`；
- a5 replayable review：`a80a531d6933946484c54d3a589bc55b0cb9e94b`；
- a6 confirmed overlap：`dfd840b3a6f893531cce8019aae53e803243f95c`；
- a7 confirmed cut/CUT_AWARE：`096210fbdbb8a55ee908b592bba20b1244c2821f`；
- a8 cut+overlap composition：`5c458d8327d2641ba053423fff3066d7fdd8ba3b`；
- P1 strict calibration/blind：`1c6babe37067c217d14a7404aa0ed6a1c4779a00`；
- P1.1 private dataset readiness：`ad6c403a56209e945a9a61a1eeab1a4bc3c204b4`；
- P2 editor shadow evidence：`2e96569189ac6eb16d987fb2f304403696bc809b`；
- P3 local acoustic evidence：`cd3420750c06a55fa1af7d6314ec56971e728928`；
- P4 evidence fusion shadow：`bc4e10760ffee2e5990ca580d5edbadd7d561eaf`。

P3 validate #493 与 P4 validate #517 均在 ASR + Python 3.10/3.12/3.14 compile/docs/full unit-E2E/Skill/privacy/environment/diff-check 全绿后合入。

---

## 2026-08-18 — P5 ASR Second-Pass Routing

### 1. 目标

把 P3 第一遍 local faster-whisper evidence 中**弱或缺失的原 local jobs**路由到第二遍 accuracy-model 计划。

P5 不重新扫描整曲、不扩大 window、不自动运行模型。

### 2. 新增模块 / CLI

```text
lyric_aligner/alignment/asr_routing.py
scripts/v4_plan_asr_second_pass.py
references/asr-second-pass-routing.md
```

`lyric_aligner/alignment/__init__.py` 同步 export routing contract。

Artifact：

```text
stage = asr_second_pass_planning
role  = asr_second_pass_plan
```

固定：

```text
mode = second_pass_plan_only
policy_calibrated = false
backend_execution_performed = false
scope_policy = reuse_exact_first_pass_local_windows
```

### 3. Weak-evidence signals

Bootstrap reasons：

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

默认参数：

```text
min_canonical_text_support = 0.65
min_avg_logprob = -0.75
max_no_speech_prob = 0.60
min_language_probability = 0.65
max_jobs = 100
```

这些只用于 evidence routing，不是发布阈值。

### 4. Exact local scope

Second-pass job 复用原 planner job 的：

```text
job_id
occurrence/track/line identity
mix_window_ms
source_window_ms
canonical_text_sha256
```

不扩大到整曲。`source_forced_alignment`-only job 不会被转成 ASR job。

### 5. Priority-aware truncation

早期 draft 按 line index 排序会导致 `max_jobs` 可能挤掉高优先级严重弱证据，已修复。

现在按：

1. first-pass planner priority：high > medium > low；
2. severity class：missing evidence > missing segment/quality > low canonical support > other；
3. reason count；
4. deterministic identity tie-break。

输出 `eligible_second_pass_job_count_before_truncation` 和 `second_pass_plan_truncated`，不能静默截断。

### 6. Model lineage

First-pass evidence 必须记录 `config.model_id`。

默认要求：

```text
second_pass_model_id != first_pass_model_id
```

防止同模型重复跑却伪称 two-pass escalation。当前 contract 不判断模型强弱，只绑定身份；运营可配置 turbo -> large-v3。

### 7. Lineage / fail-closed

CLI 验证：

- task fingerprint/input hash；
- exact alignment plan artifact；
- exact first-pass ASR artifact；
- first-pass `source_plan_artifact_id`；
- first-pass artifact upstream 包含 plan；
- same source run identity；
- first-pass evidence 不包含 plan 中不存在的 mix_asr jobs。

Artifact 被改动、跨 task/plan/run、same-model escalation 都必须失败。

### 8. Tests

新增/收紧：

- weak/missing evidence routing；
- good evidence skip；
- missing segment quality reroute；
- exact local window reuse；
- forced-only job skip；
- priority-aware max_jobs truncation；
- invalid priority fail；
- extra foreign first-pass job fail；
- first/second model lineage；
- same-model second pass fail；
- first-pass artifact tamper fail。

### 9. GitHub Actions 真实边界

CI 只验证 routing/lineage/privacy/contracts，不下载/运行真实 second-pass large model，也没有 private real-song truth。

因此不能声称 large-v3 相对 turbo 的真实收益，不能把 bootstrap thresholds 升级成 production confidence，也不能把 second-pass plan 解释成模型已执行。

P5 必须以 latest-head ASR + Python 3.10/3.12/3.14 全门禁为合并依据。
