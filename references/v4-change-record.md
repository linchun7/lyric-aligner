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
- P4 evidence fusion shadow：`bc4e10760ffee2e5990ca580d5edbadd7d561eaf`；
- P5 ASR second-pass routing：`1abef200c3dbfe711dacf5432bb51ee7ac1bbe5d`。

P3 validate #493、P4 validate #517、P5 validate #530 均在 ASR + Python 3.10/3.12/3.14 compile/docs/full unit-E2E/Skill/privacy/environment/diff-check 全绿后合入。

---

## 2026-08-18 — P6 ASR Second-Pass Execution + Composite Evidence

### 1. 目标

P5 只规划 second-pass；P6 真正执行 P5 selected local jobs，并将结果与第一遍 evidence 合成一份完整 ASR evidence，供 P4 直接消费。

新增：

```text
lyric_aligner/alignment/asr_second_pass.py
scripts/v4_execute_asr_second_pass.py
references/asr-second-pass-execution.md
```

`lyric_aligner/alignment/__init__.py` 同步 export：

```text
ASR_COMPOSITE_SCHEMA_VERSION
AsrSecondPassExecutionError
execute_second_pass_and_compose
```

### 2. Zero-selection bug prevention

P3 手工 CLI 的 legacy 语义是未提供 `--job-id` 时不筛选；如果直接复用，P5 `selected_job_ids=[]` 有被误解释为“全执行”的风险。

P6 禁止该语义：

```text
selected jobs = []
-> execution_plan.jobs = []
-> model not loaded
-> second_pass_executed_job_count = 0
```

这是 P6 的硬 contract，不依赖调用方手工判断。

### 3. Exact original-window enforcement

每个 P5 selected job 必须在 original P3 `mix_asr` plan 中存在，并逐项匹配：

```text
occurrence_id
track_id
canonical_line_index
language_profile
mix_window_ms
source_window_ms
canonical_text_sha256
```

实际执行 plan 从 original P3 row 生成，不直接执行 P5 row，因此 P5 不能扩大 window。

### 4. Model lineage

P6 强制：

```text
first_pass config.model_id exists
P5 second_pass_model_id exists
P6 execution model == P5 second_pass_model_id
second model != first-pass model
```

模型 identity 不一致直接 fail-closed。

### 5. Composite evidence

Output 保持 P4 兼容：

```text
stage = asr_evidence_local
role  = asr_evidence
backend = faster_whisper
mode = composite_second_pass_evidence
policy_calibrated = false
scope_policy = reuse_exact_first_pass_local_windows
```

按 original P3 mix-ASR 顺序：

- P5 selected：second-pass result 替换 first-pass result；
- 未 selected：保留 first-pass result（若存在）；
- 没有 evidence 的 original job 不伪造空结果。

每条 job 标记：

```text
evidence_pass = first | second
evidence_model_id = actual model ID
```

### 6. Privacy hardening

Composite privacy 由**本次 P6** `include_private_text` 控制。

默认 false 时，即使 first-pass evidence 以前保存过私有原文，也必须从 retained rows 移除：

```text
observed_text
segments[].text
segments[].words[].text
```

只有 P6 本次显式 private-text opt-in 才允许 composite 保存 raw ASR text。

### 7. Artifact lineage

P6 artifact upstream：

```text
original alignment plan artifact
first-pass ASR artifact
P5 second-pass plan artifact
source run artifact
canonical timeline artifacts
```

Payload 绑定：

```text
source_plan_artifact_id
source_first_pass_artifact_id
source_second_pass_plan_artifact_id
source_run_artifact_id
mix_audio_sha256
selected_job_ids
```

P5 artifact 还必须 upstream exact plan + first-pass artifact；跨 task/plan/run 或 output hash 变化均 fail。

### 8. Tests

新增：

- fake-model selected job exact clip；
- unselected first result retained；
- selected first result replaced by second；
- empty selection = zero execution / no model load；
- changed P5 window fail；
- executor/P5 model mismatch fail；
- selected IDs/jobs mismatch fail；
- retained private first-pass text默认剥离；
- CLI empty-selection E2E 在无真实 Whisper model 环境成功；
- composite artifact 绑定 P5 plan。

### 9. GitHub Actions 真实边界

P6 CI 可以验证 zero-selection、fake-model API、artifact lineage/privacy 和所有既有 regression；但当前 workflow 不下载/运行真实 second-pass large model，也没有 private real-song truth。

因此不能声称：

- accuracy model 已在公共 Actions 上真实执行；
- large-v3 相对 turbo 的真实收益；
- second-pass composite 可自动修改 final timing/release。

P6 必须以 latest-head ASR + Python 3.10/3.12/3.14 全门禁为合并依据。
