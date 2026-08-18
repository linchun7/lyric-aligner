# Lyric Aligner v4 当前实施状态

更新日期：2026-08-18  
当前开发分支：`agent/v4-forced-evidence-fusion-shadow-v2`  
当前 main：`9ad6df4f04b396871f757422bcb35f1fa7676678`（P7）  
P8 PR：`#17`，head `14e36d4a815a97d070efa670747d0035f06b32c8`，已 ready for review，latest validate #593 排队中  
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
```

P3 validate #493、P4 #517、P5 #530、P6 #545、P7 #560 均全绿后合入。

## 2. P8：Forced Alignment Source-to-Mix Projection

P8 已实现，PR #17。输出：

```text
stage = forced_alignment_mix_projection
role = forced_alignment_mix_evidence
mode = forced_alignment_mix_projection
```

核心规则：

- `AFFINE` / `PIECEWISE_RATE` 复用 `mix_time_for_source()`；
- `CUT_AWARE` cross-gap/cross-cut line -> `unprojectable`，绝不 bridge；
- spans 独立投影；
- 只解析 forced evidence 实际引用 occurrences；
- relevant mapping/provenance 缺失 fail closed；
- projected evidence 不复制 canonical raw lyric。

旧 head validate #577 只因 documentation contract 失败，compile 已通过；docs 已补齐后形成 latest head `14e36d4...`，validate #593 当前排队。

## 3. 当前 P9：Forced Alignment Multi-Family Shadow Fusion

P9 在 P8 之上把 `forced_alignment_mix_evidence` 接入 P4 fusion，仍保持 shadow-only。

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

任意 pair 超过 `conflict_boundary_ms` -> `CONFLICT`。三 family 中即使两家一致，也不会以多数票掩盖第三家 outlier。

无冲突时：

```text
0 auxiliary family -> LOW
1 auxiliary family -> MEDIUM
>=2 auxiliary families -> HIGH
```

这些仍是 **uncalibrated shadow state**，不是生产 release confidence。

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

因此当前代码不会因为 editor/ASR/forced 三者看起来一致，就偷偷改 final timing。

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

Fusion artifact 把 source run、canonical timelines、editor、ASR、forced mix evidence 的实际 artifact IDs 全部写入 upstream lineage；forced evidence 若属于另一 source run、artifact hash 被篡改、canonical identity 漂移均 fail closed。

## 7. 测试范围

新增/扩展测试覆盖：

- forced-only -> MEDIUM shadow；
- editor+forced agreement -> HIGH shadow；
- ASR+forced disagreement -> CONFLICT；
- 三 family 任一 outlier -> CONFLICT；
- unprojectable forced 不计 family 且 reason 可审计；
- canonical hash/unknown line fail closed；
- unprojectable payload 不能夹带 mix boundary；
- CLI E2E 三 family artifact lineage；
- fusion 输出不泄露 private canonical/editor raw text。

## 8. 代码完成后仍必须在本地做的真实生产验证

公共 Actions 只能证明 deterministic protocol/math/lineage/privacy；不能证明真实歌声准确率。真正拿数据生产时必须：

1. 选择并安装真实 forced-aligner adapter/runtime；
2. 锁定 backend/package、model/checkpoint revision、language/G2P resources、device/runtime identity；
3. 用 private real-song calibration set 测 editor/ASR/forced/source-to-mix 每 family boundary error；
4. 用独立 blind set 冻结/验证阈值；
5. 数据证明收益前，不开启 auxiliary evidence 自动 timing mutation 或 release authority。

> **当前收口目标：P8 CI 全绿并合入 → P9 基于最新 main 跑全量 CI 并合入 → 更新本地 Codex production handoff。之后剩余的“准确率证明/阈值选择”必须由真实私有数据完成，不能在公共 synthetic CI 中伪造。**
