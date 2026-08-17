# Lyric Aligner v4 当前实施状态

更新日期：2026-08-18  
当前开发分支：`agent/v4-asr-second-pass-execution`  
当前 main：`1abef200c3dbfe711dacf5432bb51ee7ac1bbe5d`  
主线算法版本：`4.0.0a8`  
Calibration profile：`production-bootstrap-2026-08-17-a7`

## 1. 已进入 main

生产重建主链已完成：TrackAsset/canonical single truth、HPSS+Chroma/MFCC Source-to-Mix、AFFINE/PIECEWISE_RATE、Selective Fine、candidate review、confirmed overlap、confirmed cut/CUT_AWARE、partial-line fail-closed、cut+overlap safe composition、strict render/release。

已合入增量：

```text
P1    strict calibration/blind framework
      1c6babe37067c217d14a7404aa0ed6a1c4779a00
P1.1  private dataset scaffold/readiness
      ad6c403a56209e945a9a61a1eeab1a4bc3c204b4
P2    editor/Jianying multilingual shadow evidence
      2e96569189ac6eb16d987fb2f304403696bc809b
P3    local acoustic planner/backend/faster-whisper executor
      cd3420750c06a55fa1af7d6314ec56971e728928
P4    shadow multi-family evidence fusion
      bc4e10760ffee2e5990ca580d5edbadd7d561eaf
P5    bounded ASR second-pass routing
      1abef200c3dbfe711dacf5432bb51ee7ac1bbe5d
```

P3 validate #493、P4 validate #517、P5 validate #530 均在 ASR environment + Python 3.10/3.12/3.14 compile/docs/full unit-E2E/Skill/privacy/environment/diff-check 全绿后合入。

## 2. 当前 P6：ASR Second-Pass Execution + Composite Evidence

P5 已经能决定哪些第一遍 weak/missing local jobs 需要 accuracy-model；P6 负责**真正执行这些 selected jobs，并与第一遍结果合成一份完整 ASR evidence**。

新增：

```text
lyric_aligner/alignment/asr_second_pass.py
scripts/v4_execute_asr_second_pass.py
references/asr-second-pass-execution.md
```

Package export 同步到 `lyric_aligner/alignment/__init__.py`。

最终 output 为了直接兼容 P4，继续使用：

```text
stage = asr_evidence_local
role  = asr_evidence
backend = faster_whisper
mode = composite_second_pass_evidence
policy_calibrated = false
scope_policy = reuse_exact_first_pass_local_windows
```

P6 不改变 canonical lyric、Source-to-Mix、canonical timeline、FINAL.srt 或 release gate。

## 3. P6 最关键安全规则：空选择绝不执行全部

历史 P3 CLI 的手工 `--job-id` 模式中，“没有 ID”意味着不筛选；若直接复用会有风险：P5 `selected_job_ids=[]` 可能被误解为“执行全部”。

P6 从 contract 层彻底分离：

```text
P5 selected jobs = []
-> execution_plan.jobs = []
-> second-pass executor job_count = 0
-> model_loaded_second_pass = false
```

因此 0 selected 永远是 0 execution，不会退化成 full plan/full mix。

## 4. Exact-window / identity validation

P6 对每个 P5 selected job 与 original P3 plan 对比：

```text
occurrence_id
track_id
canonical_line_index
language_profile
mix_window_ms
source_window_ms
canonical_text_sha256
```

必须完全相等。P6 实际执行时使用 original P3 plan row，而不是信任 P5 row 直接执行。

这保证第二遍不能扩大 local window。

## 5. Composite 结果

对 original P3 mix-ASR job 顺序：

```text
selected by P5 -> 用 second-pass result 替换 first-pass result
not selected   -> 保留 first-pass result（如果存在）
```

每条 result 增加：

```text
evidence_pass = first | second
evidence_model_id = actual model id
```

统计：

```text
first_pass_input_job_count
first_pass_retained_job_count
second_pass_selected_job_count
second_pass_executed_job_count
model_loaded_second_pass
job_count
```

P4 可直接把 composite 当作 `--asr-evidence` 使用，不需要另一套 fusion protocol。

## 6. Model lineage

P6 强制：

```text
first_pass config.model_id exists
P5 second_pass_model_id exists
P6 --model-id == P5 second_pass_model_id
second model != first-pass model
```

避免执行错误模型或同模型重复跑后误标成 accuracy escalation。

## 7. Privacy 收紧

Composite 的隐私等级由**本次 P6 参数**决定，而不是继承第一遍。

默认不带 `--include-private-text` 时，即使 first-pass evidence 原先保存过 raw text，retained first-pass jobs 也必须删除：

```text
observed_text
segments[].text
segments[].words[].text
```

Second-pass output 同样不保存 raw text。

只有本次 P6 明确 `--include-private-text` 才允许 composite 保存私有 ASR 正文。

## 8. Artifact lineage

P6 artifact upstream 必须包含：

```text
original alignment plan artifact
first-pass ASR artifact
P5 second-pass plan artifact
source run artifact
exact canonical timeline artifacts
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

跨 task/plan/run、artifact output hash 变化、P5 plan 绑定错误都 fail-closed。

## 9. P6 tests

已新增：

- fake-model：只执行 selected exact local clip；
- unselected good first-pass result retained；
- selected weak result 被 second-pass replace；
- empty selection = zero model load / zero second execution；
- P5 window 被改写 -> fail；
- executor model 与 P5 model 不一致 -> fail；
- selected IDs 与 jobs 不一致 -> fail；
- CLI empty-selection E2E 在没有真实 Whisper model 时也能成功；
- retained first-pass private observed/segment/word text 默认全部剥离；
- composite artifact 绑定 P5 second-pass plan。

P6 尚未经过本分支 latest-head GitHub Actions，因此当前不能宣称已可合并。

## 10. 仍未完成 / GitHub Actions 真实边界

仍未完成：

- 用户授权 private calibration/blind dataset 的真实填充与指标；
- production forced-aligner adapter + model/language/cache lineage；
- vocal separation/local singing alignment；
- calibrated multi-family boundary application/release gate；
- same-region cut+overlap joint acoustic model。

GitHub Actions 当前可以验证 P6 的 zero-selection、fake-model API、lineage/privacy/contracts，但**不会下载/运行真实 accuracy Whisper model**。因此不能声称：

- second-pass large-v3 已在 Actions 中真实跑过；
- large-v3 对真实歌曲比 turbo 提升多少；
- P5/P6 可以自动修改 final timing。

这些必须由 private/local runtime + 授权 real-song calibration/blind-test 完成。

> **当前正确表述：P0/P1/P1.1/P2/P3/P4/P5 已进入 main；P6 正在把 P5 selected local jobs 安全执行并合成可直接给 P4 使用的完整 ASR evidence。**
