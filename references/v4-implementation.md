# Lyric Aligner v4 实施记录与关键代码说明

> 当前主线算法仍为 `4.0.0a8`。P2/P3 都是 evidence layer，不改变 canonical lyric / Source-to-Mix 的基础 authority，也不修改 calibrated acoustic profile。

## 1. 当前分层

```text
lyric_aligner/
  alignment/
    backends.py      # optional backend capability/readiness
    planner.py       # bounded local evidence job planning
    asr_executor.py  # optional bounded faster-whisper executor
  assets/            # TrackAsset / occurrence / resolution
  audio/             # HPSS/chroma/MFCC / mapping / cuts
  contracts/         # immutable artifact lineage
  evidence/
    editor.py        # P2 editor shadow evidence
  evaluation/        # P1 strict calibration/blind + readiness
  pipeline/
  review/
  text/
  timeline/
  qa/
```

关键 P3 CLI：

```text
v4_alignment_backends.py
v4_plan_alignment.py
v4_execute_asr_evidence.py
```

## 2. Authority graph

```text
Canonical lyric -> final text/order truth
Source-to-Mix  -> primary timing truth
TrackAsset     -> source/canonical identity truth
Editor SRT     -> P2 non-authoritative shadow evidence
ASR/Forced Alignment -> P3 optional local acoustic evidence
```

P3 不允许：

```text
ASR text -> final canonical lyric
ASR word time -> silently replace timeline
missing forced aligner -> fake fallback
```

## 3. `alignment/backends.py`

Backend registry 不加载模型，只检查 discovery + explicit prerequisites。

Capabilities：

```text
mix_asr
word_timestamps
ctc_alignment
source_forced_alignment
```

当前 entries：

```text
faster_whisper
whisperx (optional)
external_forced_aligner
```

每个 `BackendStatus` 分离：

```text
available
execution_ready
missing_execution_requirements
capabilities
discovery/detail
```

这允许 CI/用户明确知道“包不存在”“包在但没 model ID”“external command 不存在”，而不是把所有状态压成一个 bool。

## 4. `alignment/planner.py`

Planner 先建立 exact canonical line index：

```text
(occurrence_id, canonical_line_index)
 -> track/ordinal/language
 -> canonical_selection SHA
 -> canonical text SHA
 -> mix/source timestamps
```

Job 来源：

### 4.1 Run issue

读取 active/review issue 的 occurrence + interval；生成高优先级 local evidence job。

### 4.2 Editor boundary disagreement

P2 `suggested_onset/offset_delta_ms` 超过 bootstrap planner threshold 时生成高优先级 line job。

### 4.3 Editor ambiguity

P2 best-vs-second uncalibrated margin 低于 planner bootstrap threshold 时生成中优先级 line job。

### 4.4 Editor missing

默认不生成，防止没有 editor match 时全曲 job flood；显式 config 才开启。

### 4.5 Dedup / identity

同 occurrence/line/window 的多原因合成同一个 job；job ID 绑定：

```text
occurrence
line index
mix/source windows
requested capabilities
reasons
canonical text SHA
```

## 5. Planner artifact

```text
stage=alignment_job_planning
role=alignment_plan
mode=plan_only
backend_execution_performed=false
```

Artifact upstream 至少包括：

- source run artifact；
- exact effective timeline artifact IDs；
- 如果使用 P2，则包括 exact editor evidence artifact。

Planner 输出本身不含 raw canonical lyric text。

## 6. `alignment/asr_executor.py`

### 6.1 Lazy backend

`faster_whisper` 只在真正有 `mix_asr` job 且开始 execute 时 import/model-init。

Planner/backend diagnostic/unit test 不触发模型加载。

### 6.2 Local clip

每 job 的 `mix_window_ms` 转成：

```text
clip_timestamps=[start_s,end_s]
```

同时：

```text
word_timestamps=true
condition_on_previous_text=false
vad_filter=false
```

这样一个 job 的上下文边界由 planner 显式控制，避免 previous text 串入另一首歌/另一区域。

### 6.3 Language

```text
en/zh/ko/ja -> explicit hint
other/yue/auto -> None
```

不把粤语强行视为中文普通话。

### 6.4 Evidence output

默认不保存 raw ASR text。

Segment：

```text
start/end
text SHA
avg_logprob
no_speech_prob
compression_ratio
```

Word：

```text
start/end
text SHA
probability
```

另外保存 detected language/probability 与 canonical local support score。

显式 `include_private_text=true` 才保留 raw observed/segment/word text。

## 7. `v4_execute_asr_evidence.py`

Executor 不信 plan 里的 canonical text（plan 本来就没有正文），而是重新从 exact run timeline 取 canonical text，在内存中验证：

```text
sha256(current canonical text)
==
plan canonical_text_sha256
```

再用于 local ASR support scoring。

Executor artifact：

```text
stage=asr_evidence_local
role=asr_evidence
```

upstream：plan + source run + exact canonical timelines。

Config 记录 model/device/compute/beam/temperature/private-text flag/mix audio SHA。

## 8. Forced Alignment contract

当前 P3 不选择一个“默认神模型”。

只定义：

```text
capability=source_forced_alignment
source_window_ms
external readiness check
```

具体 WhisperX/SOFA/MMS/其他 backend 必须独立 adapter，并绑定：model ID/revision、language assets、source audio SHA、cache identity、license/runtime assumptions。

在这些条件未完成前，forced alignment 是 planned/unavailable，而不是已执行。

## 9. Tests

Synthetic/unit：

- bounded job planning；
- issue/editor reason routing；
- job truncation；
- text identity conflict；
- package/command readiness；
- required backend missing nonzero；
- artifact-level planner E2E；
- fake faster-whisper model exact call kwargs；
- privacy default；
- language hints；
- no jobs/no model loading。

Fake model 只证明 executor API contract，不证明真实 Whisper model/singing accuracy。

## 10. P1/P2/P3 calibration path

正确升级顺序：

```text
P1 private ground truth
+ P2 editor shadow
+ P3 ASR/forced-alignment evidence
        ↓
calibration error analysis
        ↓
select models/policies/thresholds
        ↓
blind_test once
        ↓
only then promote an evidence family into automatic boundary fusion
```

不能根据 synthetic unit tests 直接把 P2/P3 升级为 final authority。

## 11. Explicit boundaries

当前仍未完成：

- real-song private calibration/blind results；
- calibrated editor timing application；
- production forced-aligner adapter；
- two-pass ASR routing；
- vocal separation/local singing alignment；
- multi-family final evidence fusion gate；
- same-region cut+overlap joint model。
