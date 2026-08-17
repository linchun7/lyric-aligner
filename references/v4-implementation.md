# Lyric Aligner v4 实施记录与关键代码说明

> 当前主线算法仍为 `4.0.0a8`。P2/P3/P4 都是 evidence layer；canonical lyric 仍是 final text/order truth，Source-to-Mix 仍是 primary timing truth。

## 1. 当前分层

```text
lyric_aligner/
  alignment/
    backends.py      # P3 optional backend capability/readiness
    planner.py       # P3 bounded local evidence job planning
    asr_executor.py  # P3 optional bounded faster-whisper executor
  assets/
  audio/
  contracts/
  evidence/
    editor.py        # P2 editor shadow evidence
    fusion.py        # P4 uncalibrated multi-family shadow fusion
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
```

## 2. Authority graph

```text
Canonical lyric -> final text/order truth
Source-to-Mix  -> primary timing truth
TrackAsset     -> source/canonical identity truth
Editor SRT     -> P2 non-authoritative shadow evidence
ASR            -> P3 optional local acoustic evidence
Evidence Fusion-> P4 non-authoritative shadow diagnostic
```

禁止：

```text
ASR text -> final canonical lyric
Editor time -> silently replace timeline
P4 HIGH -> release approval
missing forced aligner -> fake fallback
```

## 3. P3 local acoustic evidence

### `alignment/backends.py`

区分：

```text
available
execution_ready
validated_on_singing
```

当前 capability：`mix_asr`、`word_timestamps`、`ctc_alignment`、`source_forced_alignment`。

### `alignment/planner.py`

从 run/review issue、P2 editor disagreement/ambiguity 构造 bounded local jobs，绑定 occurrence/line、canonical text SHA、mix/source windows、reason/capability、deterministic job ID。

Planner artifact：

```text
alignment_job_planning / alignment_plan
mode=plan_only
backend_execution_performed=false
```

### `alignment/asr_executor.py`

只执行请求 `mix_asr` 的 local jobs；faster-whisper lazy import/model init。每 job 使用 local `clip_timestamps`、word timestamps、`condition_on_previous_text=false`，默认不保存 raw ASR text。

P3 PR #12 validate #493 已全绿并合入 main `cd3420750c06a55fa1af7d6314ec56971e728928`。

## 4. P4 `evidence/fusion.py`

P4 首先从 effective canonical timeline 构造 exact line index：

```text
(occurrence_id, canonical_line_index)
 -> track/ordinal/language
 -> canonical_selection_sha
 -> canonical text SHA
 -> source/mix boundary
```

然后分别建立 editor / ASR auxiliary indexes。

### 4.1 Source family

始终存在：

```text
family=source_timeline
authoritative_for_primary_timing=true
```

### 4.2 Editor family

只消费 P2 `shadow_only` evidence；要求：

- `automatic_timing_change_allowed=false`；
- canonical text SHA 与当前 timeline 一致；
- 有 best cue + onset/offset delta 才形成 line boundary proposal。

### 4.3 ASR family

只消费 `backend=faster_whisper` 的 local evidence。只有带 `canonical_line_index` 且存在有效 segment interval 的 job 才形成 line proposal。

Occurrence-level job 没有 line identity，因此不会被强行归入某条歌词。

同 line 多 ASR jobs 时，bootstrap 先按 `canonical_text_support_score` 排序，再用 job ID deterministic tie-break。

## 5. P4 shadow state

默认 config：

```text
conflict_boundary_ms=500
```

比较 editor 与 ASR proposal：

```text
max(
  abs(editor_onset - asr_onset),
  abs(editor_offset - asr_offset)
)
```

Bootstrap states：

```text
LOW      no auxiliary boundary proposal
MEDIUM   exactly one auxiliary family
HIGH     editor + ASR 且 disagreement <= threshold
CONFLICT editor + ASR 且 disagreement > threshold
```

固定：

```text
shadow_level_calibrated=false
release_gate_eligible=false
automatic_timing_change_allowed=false
```

即使 HIGH 也只是未校准一致性标签。

## 6. P4 CLI / artifact

`scripts/v4_fuse_evidence.py` 输入：

```text
task manifest
source effective run + artifact
optional editor evidence + artifact
optional ASR evidence + artifact
```

Output：

```text
stage=evidence_fusion_shadow
role=evidence_fusion
```

Artifact upstream 包括：

- source run artifact；
- exact canonical timeline artifacts；
- supplied editor evidence artifact；
- supplied ASR evidence artifact。

Auxiliary payload `source_run_artifact_id` 必须与 current run artifact ID 完全一致，并且其 artifact upstream 也必须包含 current run。

## 7. P4 privacy

Fusion output 不保存 raw canonical/editor/ASR text。

保存：

```text
occurrence/track/line identity
canonical text SHA
source/editor/ASR boundaries
editor timing/margin metadata
ASR job/support/language metadata
family count
disagreement
shadow level
```

即使输入 ASR artifact 带 private raw text，P4 也不会复制正文。

## 8. P4 tests

Direct unit regressions：

- source-only LOW；
- one auxiliary MEDIUM；
- editor+ASR agreement HIGH；
- disagreement CONFLICT；
- HIGH 仍不可 release；
- text SHA mismatch fail；
- unknown auxiliary line fail。

Artifact E2E：

- exact task/run/timeline/editor/asr lineage；
- fusion upstream IDs 完整；
- output 不泄露 private text；
- cross-run auxiliary evidence fail-closed。

## 9. Forced Alignment 仍未假装完成

当前只有：

```text
source_forced_alignment capability
local source-window planning
external command readiness
```

具体 WhisperX/SOFA/MMS adapter 必须单独绑定 model ID/revision、language assets、source audio SHA、cache identity、license/runtime assumptions，并经过真实 private validation。

## 10. 校准路径

```text
P1 private ground truth
+ P2 editor shadow
+ P3 ASR/forced-alignment evidence
+ P4 shadow fusion
        ↓
calibration error analysis
        ↓
lock family admission / model / thresholds
        ↓
blind_test once
        ↓
only then consider calibrated boundary application / release gate
```

Synthetic CI 只能证明 determinism、lineage、privacy、安全契约，不能证明 real-song accuracy。

## 11. 下一阶段

- P5 two-pass ASR routing：第一遍 local evidence 弱/缺失时，才调度 accuracy pass；
- production forced-aligner adapter；
- private real calibration/blind；
- calibrated evidence-family release gate；
- same-region cut+overlap joint acoustic model。
