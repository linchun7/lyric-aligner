# Evidence Fusion Shadow — Source / Editor / ASR

状态：P4 bootstrap / diagnostic only  
日期：2026-08-18

## 1. 目标

P4 把已经存在的独立 evidence family 汇总到同一 canonical line 身份上：

```text
Source-to-Mix canonical timeline
+ P2 editor shadow evidence
+ P3 local ASR evidence
        ↓
uncalibrated shadow support state
```

P4 **不是**发布 confidence gate，也不自动修改字幕。

固定：

```text
mode = shadow_only
policy_calibrated = false
release_gate_eligible = false
automatic_timing_change_allowed = false
```

## 2. Evidence families

### Source timeline

始终存在，仍是 primary timing authority：

```text
family = source_timeline
authoritative_for_primary_timing = true
```

### Editor

来自 P2 `editor_evidence_shadow`，只在某 line 有 best editor cue + boundary delta 时形成 boundary proposal。

### ASR

来自 P3 `asr_evidence_local`。只使用带 `canonical_line_index` 且有有效 segment interval 的 local job 形成 boundary proposal。

Occurrence-level ASR job 没有 line identity，因此不会冒充某条歌词的边界 evidence。

## 3. Shadow levels

P4 输出：

```text
LOW
MEDIUM
HIGH
CONFLICT
```

这些名字只表示“当前有多少独立辅助 boundary family，以及它们是否互相冲突”，不是 calibrated accuracy。

Bootstrap 规则：

```text
LOW
  source timeline only

MEDIUM
  source + exactly 1 auxiliary boundary family

HIGH
  source + editor + ASR boundary proposal
  且 editor/ASR 最大 onset/offset 分歧 <= conflict_boundary_ms

CONFLICT
  editor + ASR 都有 proposal
  且分歧 > conflict_boundary_ms
```

即使 `HIGH`：

```text
shadow_level_calibrated = false
release_gate_eligible = false
automatic_timing_change_allowed = false
```

因此不能把 P4 `HIGH` 翻译为“可自动发布”。

## 4. ASR candidate selection

同一 canonical line 若存在多个 ASR local jobs：

- 只在 shadow fusion 内选择一个 boundary proposal；
- 优先 `canonical_text_support_score` 较高者；
- tie 用 job ID deterministic；
- 原始 ASR text 不进入 fusion output。

该选择同样未经 real calibration，不是 final model arbitration。

## 5. Conflict

Bootstrap `conflict_boundary_ms` 默认 500ms。

比较：

```text
max(
  abs(editor_onset - asr_onset),
  abs(editor_offset - asr_offset)
)
```

大于阈值 -> `CONFLICT`。

这是 shadow diagnostic threshold。真实 production conflict gate 必须由 P1 calibration/blind 数据定标。

## 6. Privacy

Fusion 不复制：

- canonical raw lyric text；
- editor raw text；
- ASR raw observed text。

保存：

- canonical line/track/occurrence identity；
- canonical text SHA；
- source/editor/ASR boundaries；
- editor margin/timing support；
- ASR job ID/canonical support/language probability；
- family count / disagreement / shadow level。

即使输入 ASR artifact 是 private-text opt-in，fusion output 仍不复制原文。

## 7. Artifact lineage

CLI：

```text
scripts/v4_fuse_evidence.py
```

Output：

```text
stage = evidence_fusion_shadow
role  = evidence_fusion
```

必须绑定：

- task fingerprint；
- exact source run artifact；
- exact effective canonical timeline artifacts；
- 可选 editor evidence artifact；
- 可选 ASR evidence artifact。

Editor/ASR payload 的 `source_run_artifact_id` 必须与当前 run 完全一致，且 artifact lineage 也必须 upstream 到 current run。

## 8. 何时才能变成 release gate

至少需要：

1. private calibration set；
2. 每语言/mode family coverage；
3. reference onset/offset truth；
4. 检查 HIGH/MEDIUM/CONFLICT 与真实 error 的相关性；
5. 校准 conflict threshold / family admission threshold；
6. 冻结 policy；
7. blind_test 一次；
8. 同时检查 line/cut/overlap correctness 不退化。

在此之前，不增加 `--apply-fusion-timing`，也不让 renderer 读取 shadow level 作为发布条件。
