# Lyric Aligner v4 当前实施状态

更新日期：2026-08-18  
当前开发分支：`agent/v4-asr-second-pass-routing`  
当前 main：`bc4e10760ffee2e5990ca580d5edbadd7d561eaf`  
主线算法版本：`4.0.0a8`  
Calibration profile：`production-bootstrap-2026-08-17-a7`

## 1. 已进入 main

生产重建主链已完成：TrackAsset/canonical single truth、HPSS+Chroma/MFCC Source-to-Mix、AFFINE/PIECEWISE_RATE、Selective Fine、candidate review、confirmed overlap、confirmed cut/CUT_AWARE、partial-line fail-closed、cut+overlap safe composition、strict render/release。

已合入的 v4 增量：

```text
P1    strict calibration/blind framework
      1c6babe37067c217d14a7404aa0ed6a1c4779a00

P1.1  private dataset scaffold/readiness
      ad6c403a56209e945a9a61a1eeab1a4bc3c204b4

P2    editor/Jianying multilingual shadow evidence
      2e96569189ac6eb16d987fb2f304403696bc809b

P3    local acoustic evidence planner/backend/faster-whisper executor
      cd3420750c06a55fa1af7d6314ec56971e728928

P4    shadow multi-family evidence fusion
      bc4e10760ffee2e5990ca580d5edbadd7d561eaf
```

P3 validate #493、P4 validate #517 都在 ASR environment + Python 3.10/3.12/3.14 compile/docs/full unit-E2E/Skill/privacy/environment/diff-check 全绿后合入。

## 2. 当前 P5：ASR Second-Pass Routing

目标：第一遍 local ASR evidence 弱或缺失时，只把**原 P3 planner 已经选择的 local jobs**送入第二遍 accuracy-model 计划。

新增：

```text
lyric_aligner/alignment/asr_routing.py
scripts/v4_plan_asr_second_pass.py
references/asr-second-pass-routing.md
```

Package export 同步到 `lyric_aligner/alignment/__init__.py`。

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

因此 P5 当前**只规划，不执行第二模型**；不修改 canonical text、Source-to-Mix、timeline、P4 fusion 或 FINAL.srt。

## 3. P5 weak-evidence routing

Bootstrap signals：

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

默认 planning 参数：

```text
min_canonical_text_support = 0.65
min_avg_logprob = -0.75
max_no_speech_prob = 0.60
min_language_probability = 0.65
max_jobs = 100
```

这些阈值只决定“是否值得补第二遍 evidence”，不是 final confidence/release threshold。

## 4. Exact local scope

P5 不重新搜索整曲，也不扩大 window。Second-pass row 复用原 planner job 的：

```text
job_id
occurrence_id
track_id
canonical_line_index
mix_window_ms
source_window_ms
canonical_text_sha256
```

`scope_policy=reuse_exact_first_pass_local_windows` 写入 payload/artifact。

Forced-alignment-only jobs 不会被 P5 转成 ASR jobs。

## 5. Priority-aware truncation

修复了早期版本按 line number 截断的风险。

如果 eligible jobs > `max_jobs`，顺序为：

1. first-pass planner priority：high > medium > low；
2. evidence severity：missing evidence > missing segment/quality > low canonical support > other weak signals；
3. second-pass reason count；
4. ordinal/line/window/occurrence/job ID deterministic tie-break。

输出：

```text
eligible_second_pass_job_count_before_truncation
second_pass_job_count
second_pass_plan_truncated
priority_counts
reason_counts
```

## 6. Model lineage

P5 CLI 要求 first-pass evidence 必须记录：

```text
config.model_id
```

同时默认强制：

```text
second_pass_model_id != first_pass_model_id
```

防止“名义 two-pass、实际同一模型重复跑”。当前 contract 不判断哪个模型更强；运营建议仍是 fast first-pass / accuracy second-pass，例如 turbo -> large-v3。

## 7. Lineage / privacy

P5 验证：

- task fingerprint/input hashes；
- exact P3 alignment plan artifact；
- exact first-pass ASR artifact；
- first-pass `source_plan_artifact_id`；
- first-pass artifact upstream 包含 exact plan；
- same source run identity；
- first-pass evidence 中不能出现原 plan 不存在的 mix_asr job。

Output 不包含 raw canonical lyric text，也不复制 ASR raw text。

First-pass artifact 被改动、来自另一个 task/plan/run 时必须 fail-closed。

## 8. P5 tests

新增/收紧：

- good first-pass job 不 reroute；
- weak evidence reroute；
- missing first-pass evidence reroute；
- missing segments/line support explicit；
- missing segment quality 不能默认当作 good；
- forced-only job 不转 ASR；
- extra foreign first-pass job fail；
- `max_jobs` 优先保留 high-priority severe job；
- invalid priority fail；
- exact local window reuse；
- first/second model ID lineage；
- same-model second pass fail；
- first-pass artifact tamper fail。

P5 尚未经过本分支 latest-head GitHub Actions，因此当前不能宣称已可合并。

## 9. 真实限制 / 未完成

GitHub Actions 仍不会下载/运行真实 second-pass large model，也没有 private real-song/reference truth，因此不能证明：

- large-v3 相对 turbo 的真实歌声收益；
- 这些 weak thresholds 最优；
- second-pass evidence 可以自动改 final timing。

仍未完成：

- 用户授权 private calibration/blind dataset 的真实填充与指标；
- production forced-aligner adapter；
- second-pass **execution orchestration**（P5 当前只 plan）；
- vocal separation/local singing alignment；
- calibrated multi-family release gate；
- same-region cut+overlap joint acoustic model。

> **当前正确表述：P0/P1/P1.1/P2/P3/P4 已进入 main；P5 正在把第一遍弱 local ASR evidence 安全路由到不同 accuracy model 的同一 local window，当前只生成计划。**
