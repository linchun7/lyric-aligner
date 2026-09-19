# Local Acoustic Evidence — Planner / Backend / ASR Executor

状态：P3 bootstrap / evidence-only  
日期：2026-08-18

## 1. 为什么不是“整条音频再跑一次大模型”

v4 已有 Source-to-Mix + canonical timeline。ASR/Forced Alignment 的职责是补充**局部不确定证据**，不是重新拥有歌词或整条时间轴。

P3 先将：

```text
run unresolved/review issues
+ optional P2 editor shadow disagreement
        ↓
small local evidence jobs
        ↓
backend capability check
        ↓
optional bounded executor
```

而不是：

```text
40min mix -> one huge ASR pass -> replace canonical timeline
```

## 2. Planner

正式入口：

```text
scripts/v4_plan_alignment.py
```

输入：

- task manifest；
- current effective v4 run + artifact；
- effective canonical timeline artifacts；
- 可选 P2 `editor_evidence_shadow` + artifact。

Planner 不执行模型，输出：

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

### Job 来源

1. Active run/review issue：transition/cut/mapping/fragment 等有明确 occurrence/interval 的不确定区域。
2. Editor shadow：
   - onset/offset 与 canonical timeline 差异超过 planner bootstrap 值；
   - best-vs-second editor candidate margin 过小；
   - 可选地把 editor 无 candidate 也加入（默认关闭，防止 job flood）。

Planner bootstrap 只决定“哪里值得收集更多证据”，不决定发布/准确率。

### Job 内容

不复制歌词正文，只保存：

```text
job_id
occurrence_id / track_id / line index
canonical_text_sha256
mix_window_ms
source_window_ms (if available)
language_profile
reasons / reason evidence
requested capabilities
priority
execution_state = planned_not_executed
```

默认 local context：mix ±1.5s，source ±1.0s；可 CLI 配置且全部进入 artifact config。

`max_jobs` 超限时 plan 明确 `plan_truncated=true`，不会静默假装完整。

## 3. Backend registry

入口：

```text
scripts/v4_alignment_backends.py
```

当前 registry：

```text
faster_whisper
  capabilities: mix_asr, word_timestamps

whisperx (optional package)
  capabilities: mix_asr, word_timestamps, ctc_alignment

external_forced_aligner
  capabilities: source_forced_alignment
```

### available != execution_ready

`available` 只表示 Python package / external command 可发现。

`execution_ready` 还要求显式 model ID / alignment model ID / external command 等运行前提。

即使 execution_ready=true，也**不代表**：

- 模型已在 singing 上经过本项目 blind test；
- license 已针对特定部署用途完成审核；
- 该语言/歌曲版本一定适用。

Backend CLI 可用 `--require-capability` + `--require-execution-ready` 做真实 fail gate。缺 backend 返回非零，不用假 fallback。

## 4. Faster-Whisper local executor

package：

```text
lyric_aligner/alignment/asr_executor.py
```

CLI：

```text
scripts/v4_execute_asr_evidence.py
```

执行器只处理 planner 中请求 `mix_asr` 的 jobs。每个 job 使用 exact `mix_window_ms` 转成 faster-whisper `clip_timestamps`。

Bootstrap 调用规则：

```text
word_timestamps = true
condition_on_previous_text = false
vad_filter = false
clip_timestamps = [start, end]
beam_size / temperature explicit
```

使用 clip timestamps 时不额外依赖 VAD 来修正歌声切片。

语言提示：

```text
en/zh/ko/ja -> explicit language hint
other/yue/auto -> language=None, let backend detect
```

避免把 Cantonese 强制当 Mandarin。

### 输出

```text
asr_evidence_local / asr_evidence
```

默认不保存原始 ASR 文本，只保存：

- observed text SHA；
- detected language / probability；
- segment start/end；
- avg_logprob / no_speech_prob / compression ratio；
- word start/end / probability / text SHA；
- line-specific canonical text support score（CLI 通过 exact run timeline 在内存计算）。

只有显式 `--include-private-text` 才把 ASR 原文写到**private task output**，artifact 同时记录 `raw_private_text_included=true`。默认应保持关闭。

## 5. Lineage

Planner 必须验证：

- task fingerprint；
- source run artifact；
- effective canonical timeline artifacts；
- timeline IDs 属于 run upstream；
- 可选 editor evidence artifact 属于同一个 source run。

ASR executor 再次验证：

- task inputs / mix audio SHA；
- plan artifact；
- source run artifact；
- canonical timeline artifact；
- plan `canonical_text_sha256` 与当前 timeline 文字一致。

因此旧 plan 不能静默套到新歌词、新 run 或新 mix 上。

## 6. GitHub Actions 真实边界

普通 Python test matrix 可以验证：

- planner job selection / window bounds；
- privacy hashes；
- artifact lineage；
- backend registry；
- fake-model faster-whisper call contract；
- missing backend fail behavior。

ASR environment job安装 `requirements-asr.txt`，因此可以验证 faster-whisper package 可 import。

**当前 CI 不下载/运行 Whisper model**，因此不能声称：

- 某 model ID 已在 GitHub Actions 实际完成歌声识别；
- word timing 在真实 K-pop/J-pop/粤语歌曲上达到某 MAE；
- large-v3 比 turbo 提升固定比例；
- WhisperX/外部 forced aligner 已在当前 runner 可执行，除非 backend diagnostic 明确显示可用且另有真实执行记录。

真实模型执行必须在有模型缓存/网络/算力和用户授权 private audio 的环境运行。

## 7. Forced Alignment

P3 当前只完成 capability contract / `source_forced_alignment` job planning 与 external backend readiness 检查，**没有把某个具体 forced aligner 硬编码成 production dependency**。

原因：不同语言的 dictionary/G2P/model availability 不同，singing 与 speech alignment 的真实收益还需要 P1 private error breakdown。

后续如果选择 WhisperX/其他 CTC/Singing backend，必须：

1. 建独立 backend adapter；
2. model/language identity 写入 artifact；
3. cache/source-audio hash 可重放；
4. calibration split 选模型/阈值；
5. blind_test 后才能升级为 final boundary evidence family。

## 8. 不变原则

- ASR 不生成 final canonical lyric；
- forced aligner 不改变 TrackAsset identity；
- planner/bootstrap threshold 不等于发布 threshold；
- 任何 model/backend 缺失都明确失败/不可执行，不伪造 evidence；
- 在 real blind-test 前不宣称固定准确率提升。
