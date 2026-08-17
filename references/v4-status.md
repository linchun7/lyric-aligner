# Lyric Aligner v4 当前实施状态

更新日期：2026-08-18  
当前开发分支：`agent/v4-alignment-planner`  
当前 main：`2e96569189ac6eb16d987fb2f304403696bc809b`  
主线算法版本：`4.0.0a8`  
Calibration profile：`production-bootstrap-2026-08-17-a7`

## 1. 已进入 main

生产重建主链已完成：TrackAsset/canonical single truth、HPSS+Chroma/MFCC Source-to-Mix、AFFINE/PIECEWISE_RATE、Selective Fine、candidate review、confirmed overlap、confirmed cut/CUT_AWARE、partial-line fail-closed、cut+overlap safe composition、strict render/release。

P1 strict calibration/blind framework 已合入：

```text
1c6babe37067c217d14a7404aa0ed6a1c4779a00
```

P1.1 private dataset scaffold/readiness 已合入：

```text
ad6c403a56209e945a9a61a1eeab1a4bc3c204b4
```

P2 Editor Evidence + LanguageSpan Shadow 经 replacement PR #11 latest-head validate #484 全绿后已合入：

```text
2e96569189ac6eb16d987fb2f304403696bc809b
```

P2 当前仍是：

```text
editor_evidence_shadow
shadow_only
policy_calibrated=false
automatic_timing_change_allowed=false
```

即 editor evidence 不改 canonical text / Source-to-Mix / final timeline。

## 2. 当前 P3：Local Acoustic Evidence Planner / Backend / ASR

P3 目标是只对**局部不确定区域**补充可选声学证据，不把整条 40 分钟 mix 重新交给 ASR，也不替代 Source-to-Mix。

新增：

```text
lyric_aligner/alignment/backends.py
lyric_aligner/alignment/planner.py
lyric_aligner/alignment/asr_executor.py
scripts/v4_alignment_backends.py
scripts/v4_plan_alignment.py
scripts/v4_execute_asr_evidence.py
```

以及：

```text
references/local-acoustic-evidence.md
```

## 3. Backend capability registry

当前 registry：

```text
faster_whisper
  mix_asr
  word_timestamps

whisperx (optional)
  mix_asr
  word_timestamps
  ctc_alignment

external_forced_aligner
  source_forced_alignment
```

重要区分：

```text
available
!=
execution_ready
!=
validated_on_singing
```

- `available`：package/command 可发现；
- `execution_ready`：显式 model/config/command 也给齐；
- 即使 execution_ready，也不代表模型经过本项目真实歌曲 blind-test。

`v4_alignment_backends.py` 对缺少的 required capability 返回非零，不做假 fallback。

## 4. Alignment planner

Artifact：

```text
alignment_job_planning / alignment_plan
```

固定：

```text
mode = plan_only
backend_execution_performed = false
canonical_text_authority = canonical_lyrics_only
primary_timing_authority = source_to_mix_only
```

Job 来源：

1. source run active/review issues；
2. 可选 P2 editor shadow boundary disagreement；
3. 可选 editor candidate ambiguity；
4. editor 无 candidate 默认不建 job，避免 job flood，可显式开启。

Job 只保存：

- occurrence/track/line identity；
- canonical text SHA；
- bounded mix/source window；
- reason / evidence；
- requested capabilities；
- priority；
- deterministic job ID。

默认 context：mix ±1.5s、source ±1.0s；`max_jobs` 超限会明确 `plan_truncated=true`。

## 5. Optional faster-whisper local executor

`v4_execute_asr_evidence.py` 只执行 plan 中请求 `mix_asr` 的 jobs。

每个 job 使用其 `mix_window_ms` 转换成 local `clip_timestamps`，bootstrap 调用：

```text
word_timestamps = true
condition_on_previous_text = false
vad_filter = false
beam / temperature explicit
```

语言 hint：

```text
en/zh/ko/ja -> explicit
other/yue/auto -> None
```

因此不会把 Cantonese 强制成 Mandarin。

默认 ASR evidence **不保存 raw text**；只保存 hash、segment/word timing、probability、avg_logprob/no_speech/compression、detected language/probability 与 line-specific canonical support。

只有显式 `--include-private-text` 才允许 private output 带原始 ASR 文本，并写入 artifact evidence。

## 6. Lineage

Planner 验证：

- task fingerprint；
- source run artifact；
- effective canonical timeline artifact；
- timeline 必须属于 source run upstream；
- 可选 editor evidence 必须属于同一 source run。

ASR executor 再验证：

- task input / mix audio SHA；
- alignment plan artifact；
- source run artifact；
- canonical timeline；
- plan `canonical_text_sha256` 与当前 timeline 一致。

旧 plan 不允许套到新 mix/run/lyrics。

## 7. Forced Alignment 当前真实状态

**尚未接入具体 production forced-aligner backend。**

当前完成的是：

- `source_forced_alignment` capability contract；
- planner 可生成需要 forced alignment 的 local source windows；
- external command readiness 可真实检查。

当前没有假装 WhisperX/SOFA/MMS 已部署、已下载模型或已在 singing 上验证。

## 8. GitHub Actions 能/不能做

能真实验证：

- planner job selection/windows/determinism；
- backend package/command discovery contract；
- missing backend 非零返回；
- fake-model faster-whisper call contract；
- clip timestamps / word timestamp / privacy contract；
- artifact lineage；
- 既有 P0/P1/P1.1/P2 regressions。

ASR environment job可以安装并检查 `requirements-asr.txt` 的 faster-whisper package。

**当前 CI 明确不会下载/运行真实 Whisper model**，因此不能宣称：

- GitHub Actions 已实际用 large-v3/turbo 跑真实歌声；
- 真实 KO/JA/YUE word timing 达到某 MAE；
- WhisperX/forced aligner 当前 runner 可实际使用，除非 diagnostic 明确可用且另有真实执行记录；
- ASR/forced alignment 已提高固定百分比准确率。

这些需要有模型缓存/网络/算力和用户授权 private audio 的实际环境。

## 9. 当前测试状态

P3 代码与测试已经写入并精确重放到 P2 main，但**尚未经过本分支 latest-head GitHub Actions 全量验收**，所以当前不能宣称 P3 可合并。

新增测试覆盖：

- run issue / editor disagreement / ambiguity 生成 local jobs；
- job flood opt-in / max_jobs truncation；
- canonical text hash mismatch fail；
- backend readiness truthfulness；
- planner artifact E2E；
- required backend unavailable -> exit 2；
- fake faster-whisper local executor；
- raw text default omitted；
- YUE language hint=None；
- no ASR jobs -> model never loaded。

## 10. 尚未完成

- 真实 private calibration/blind dataset 填充与真实指标；
- P2 calibrated editor boundary fusion；
- 具体 forced-alignment production adapter + language/model/cache lineage；
- two-pass turbo -> large-v3 uncertain-window routing；
- vocal separation/local singing alignment；
- final multi-family Evidence Fusion release gate；
- same-region cut+overlap joint acoustic model。

> **当前正确表述：P0、P1、P1.1、P2 已进 main；P3 正在把局部 ASR/forced-alignment 规划与可选 faster-whisper 执行做成真实、可审计、缺 backend 就明确失败的 evidence layer。**
