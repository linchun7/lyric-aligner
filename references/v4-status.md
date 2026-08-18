# Lyric Aligner v4 当前实施状态

更新日期：2026-08-18  
当前开发分支：`agent/v4-forced-evidence-fusion-shadow-v2`  
当前 main：`00585a07b658ffea93509c4ed1a4b129deafd0a3`（P8 已合入）  
当前 PR：`#19` P9 Forced Alignment Multi-Family Shadow Fusion  
主线算法版本：`4.0.0a8`  
Calibration profile：`production-bootstrap-2026-08-17-a7`

## 1. 已进入 main

生产重建主链已完成：TrackAsset/canonical single truth、HPSS+Chroma/MFCC Source-to-Mix、AFFINE/PIECEWISE_RATE、Selective Fine、candidate review、confirmed overlap、confirmed cut/CUT_AWARE、partial-line fail-closed、cut+overlap safe composition、strict render/release。

```text
P1    strict calibration/blind framework
P1.1  private dataset scaffold/readiness
P2    editor/Jianying multilingual shadow evidence
P3    local acoustic planner/backend/faster-whisper executor
P4    shadow multi-family evidence fusion
P5    bounded ASR second-pass routing
P6    ASR second-pass execution + composite evidence
P7    external source forced alignment
P8    forced alignment source-to-mix projection
```

P3 validate #493、P4 #517、P5 #530、P6 #545、P7 #560 均全绿后合入。P8 latest head `47b071de9efec69a55dfb84f016927d70a85e51a` 的 `fast-core` #1 已完成 compile、documentation contract、完整 unit/E2E、Skill、privacy、diff-check 全绿；随后 PR #17 合入 main，merge commit `00585a07b658ffea93509c4ed1a4b129deafd0a3`。P8 的旧 full-validate 队列包含 superseded 中间提交，CI 已增加 concurrency、feature-branch 去重、job timeout 与 bounded ffprobe setup，避免后续重复堆积。

## 2. P8：Forced Alignment Source-to-Mix Projection

P8 正式输出：

```text
stage = forced_alignment_mix_projection
role = forced_alignment_mix_evidence
mode = forced_alignment_mix_projection
```

核心规则：

- `AFFINE` / `PIECEWISE_RATE` 复用现有 `mix_time_for_source()`；
- `CUT_AWARE` cross-gap/cross-cut line -> `unprojectable`，绝不 bridge；
- spans 独立投影；
- 只解析 forced evidence 实际引用 occurrences；
- relevant mapping/provenance 缺失 fail closed；
- projected evidence 不复制 canonical raw lyric；
- P7 source-ms evidence 禁止直接与 editor/ASR mix-ms evidence 比较。

## 3. 当前 P9：Forced Alignment Multi-Family Shadow Fusion

P9 在 P8 之上把 `forced_alignment_mix_evidence` 作为第三个独立 auxiliary timing family 接入 P4 fusion，仍保持 shadow-only。

代码：

```text
lyric_aligner/evidence/fusion.py
scripts/v4_fuse_evidence.py
scripts/test_v4_forced_evidence_fusion.py
scripts/test_v4_evidence_fusion_end_to_end.py
```

CLI 新增：

```text
--forced-mix-evidence
--forced-mix-evidence-artifact
```

P9 head `74dce0be12826c7b281d71e1d56ce349a42e5336` 的 `fast-core` #2 已完成 compile、documentation contract、完整 unit/E2E、Skill、privacy、diff-check 全绿。本文档更新后会以新的 latest head 对已经包含 P8 的 main 再跑 PR validation，再决定合入。

## 4. P9 family / conflict semantics

可用 auxiliary boundary families：

```text
editor
asr
forced_alignment
```

Forced family 必须来自 P8 mix-time projection，且 canonical line/track/text hash identity 与当前 timeline 一致。`unprojectable` forced line 保留 diagnostic reason，但不计为可用 family。

所有可用 auxiliary pair 都做 onset/offset 最大分歧检测：

```text
editor ↔ asr
editor ↔ forced_alignment
asr ↔ forced_alignment
```

任意 pair 超过 `conflict_boundary_ms` -> `CONFLICT`。即使三 family 中两家一致，也不会用多数票掩盖第三家 outlier。

无冲突时：

```text
0 auxiliary family -> LOW
1 auxiliary family -> MEDIUM
>=2 auxiliary families -> HIGH
```

这些仍是 **uncalibrated shadow state**，不是 production release confidence。

## 5. Authority / release boundary

P9 固定：

```text
canonical_text = canonical_lyrics_only
primary_timing = source_to_mix_only
forced_alignment = auxiliary_shadow_family_mix_time
policy_calibrated = false
release_gate_eligible = false
automatic_timing_change_allowed = false
```

因此 editor/ASR/forced 的一致性不会自动改写 authoritative timeline。

## 6. P9 diagnostics / lineage

每行新增：

```text
editor_forced_boundary_disagreement_ms
asr_forced_boundary_disagreement_ms
max_auxiliary_boundary_disagreement_ms
```

summary 新增：

```text
forced_alignment_line_counts = projected / unprojectable / absent
```

Fusion artifact 绑定 source run、canonical timelines、editor、ASR、forced mix evidence 的实际 artifact IDs；cross-run evidence、artifact hash 篡改、unknown canonical line、track/text identity 漂移均 fail closed。

## 7. 本地 Codex 生产 handoff

`SKILL.md` 与 `references/v4-runtime-guide.md` 已更新为 P9 工作流。真实任务建议按以下顺序：

```text
1. task + canonical LRC + source audio + edited mix/editor SRT
2. v4_run / review / cut-overlap materialization -> authoritative effective run
3. editor evidence
4. ASR first-pass + bounded second-pass
5. external source forced alignment（需要时）
6. P8 forced source->mix projection
7. P9 shadow fusion，优先处理 CONFLICT / unprojectable / missing family
8. render + validate_release（仍以 authoritative canonical timeline 为准）
9. 把人工 ground truth 写入 private calibration/blind 数据集
10. calibration 选型/阈值冻结后再用 blind_test 验证
```

## 8. 代码收口后仍必须由真实数据完成的部分

公共 synthetic CI 只能证明 deterministic protocol/math/lineage/privacy，不能证明真实歌声准确率。真正生产时必须：

1. 选择并安装实际 forced-aligner adapter/runtime；
2. 锁定 backend/package、model/checkpoint revision、language/G2P resources、device/runtime identity；
3. 用 private real-song calibration set 测 Source-to-Mix/editor/ASR/forced 每 family boundary error、coverage 与 conflict；
4. 用独立 blind set 冻结并验证阈值；
5. 数据证明收益前，不开启 auxiliary evidence 自动 timing mutation 或 release authority。

> **当前收口目标：P9 对已包含 P8 的最新 main 再跑 latest-head validation 并合入。之后不再凭 synthetic 数据继续放宽 authority；下一阶段应直接在本地 Codex + 私有真实歌曲数据上做 calibration/blind。**
