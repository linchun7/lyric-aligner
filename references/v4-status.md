# Lyric Aligner v4 当前实施状态

更新日期：2026-08-17  
当前开发分支：`agent/v4-cut-overlap-composition`  
当前 main：v4.0.0a7，squash commit `096210fbdbb8a55ee908b592bba20b1244c2821f`  
当前开发版本：`4.0.0a8`  
TrackAsset schema：`1.1`  
Review Decision schema：`1.2`

## 1. main 已完成能力

截至 a7，main 已具备：

- fail-closed TrackAsset / canonical lyric single truth；
- harmonic Chroma/MFCC Source-to-Mix mapping；
- AFFINE-first / evidence-driven PIECEWISE_RATE；
- Selective Fine；
- candidate-level transition review；
- replayable Review Decision；
- confirmed-overlap 双路 canonical timeline recomposition；
- candidate-level TimeWarp discontinuity review；
- local confirmed-cut boundary localization；
- CUT_AWARE retained segments + explicit source gaps；
- line-LRC partial-cut fail-closed 与 Enhanced/QRC canonical fragments；
- `cut_rebuild → v4_render → release` strict lineage；
- package-native final SRT/audit/QA/release integrity。

a7 PR #6 latest head `a69c76e91a30ad9b73747cd0fdf3320d0a9132d9` 的 validate #390 在 ASR + Python 3.10/3.12/3.14 全套 compile/docs/unit/E2E/Skill/privacy/environment/diff-check 全绿后 squash merge 到 `096210fb...`。

## 2. a8 当前目标

解决此前最后一个 P0 结构缺口：**同一 reviewed task 同时存在 confirmed cut + confirmed overlap 时，如何安全组合两套已验证 materialization。**

不让 a6 overlap materializer 与 a7 cut materializer 互相改写，而增加第三层 composition：

```text
same review_resolution
 ├─ v4_rebuild_cut          → cut_rebuild
 └─ v4_recompose_overlap    → overlap_recomposition
              ↓
   v4_compose_materializations
              ↓
      combined_recomposition
              ↓
          v4_render
```

## 3. 新 composition 层

新增：

```text
lyric_aligner/timeline/composition.py
scripts/v4_compose_materializations.py
```

新 timeline stage：

```text
combined_timeline_recomposition / canonical_timeline
```

新 run stage：

```text
combined_recomposition / v4_combined_run
schema_version = 1.4
```

## 4. 组合前提

Cut/overlap 两个 materialized run 必须：

- task fingerprint 相同；
- algorithm version 相同；
- calibration profile/version 相同；
- supplied TrackAsset artifact 相同；
- `source_review_artifact_id` 完全相同；
- source review artifact 同时位于两边 upstream；
- 两边 artifact materialized hash/role/stage 都合法。

组合 stage 不重新 fuzzy resolve source/LRC，也不重新跑声学。

## 5. Issue 集合语义

同一 review 中可能同时有：

```text
cut issue
transition overlap issue
```

`cut_rebuild` 处理 cut、保留 overlap；`overlap_recomposition` 处理 overlap、保留 cut。

a8 使用两边 `processed_issue_ids` 做集合抵消：

- cut processed ID 从 overlap active issues 中去除；
- overlap processed ID 从 cut active issues 中去除；
- 两个 materializer 若声称处理同一 issue_id：BLOCK；
- 同一 unresolved issue 在两边 snapshot 不一致：BLOCK；
- 真正仍未解决的 issue 原样进入 combined run。

只有 combined remaining issues=0 才 `ready_for_render`。

## 6. 同 occurrence cut + overlap 安全规则

如果一个 occurrence 同时被 cut rebuild 和 overlap recomposition 修改，combined timeline 以 **cut-aware timeline 为 base**，只合并 overlap materializer 新增/扩展的 overlap delta lines。

必须同时通过两层安全检查：

### Mix-time safety

Confirmed overlap interval 不能穿过该 occurrence 的 localized cut boundary。

```text
overlap.start <= cut_mix_time <= overlap.end
 → BLOCK
```

这类同区域 cut+overlap 需要 joint acoustic composition，a8 不猜。

### Source-time safety

Overlap delta 必须携带 canonical `source_start_ms/source_end_ms` provenance，并且 source interval 不能与任何 confirmed `source_gap_start/source_gap_end` 相交。

- finite interval 与 source gap 相交：BLOCK；
- 缺 `source_start_ms`：BLOCK；
- open source interval 无法证明不穿 gap：BLOCK。

这防止 overlap materializer 把 a7 已确认删除的 canonical source 重新带回最终字幕。

## 7. Renderer a8

`v4_render.py` 当前开发支持：

```text
production_orchestration
review_resolution
overlap_recomposition
cut_rebuild
combined_recomposition
```

Combined run：

- 同时验证 cut metadata + overlap metadata + combined metadata；
- old cut/overlap materializer 的 `remaining_issue_count` 可以各自仍包含另一类 issue；
- **combined metadata 自己必须 remaining_issue_count=0**；
- cut `canonical_fragment_issue_count` 仍必须 0；
- same-occurrence cut+overlap 只能使用 `combined_timeline_recomposition`；
- combined timeline 必须 upstream 到 exact source cut timeline + source overlap timeline；
- final composer 仍只在 exact confirmed overlap regions 允许跨轨 cue overlap。

QA 新增：

```text
source_run_stage = combined_recomposition
combined_recomposition_occurrence_count
```

## 8. Tests（当前分支待 CI）

已加入：

- `test_v4_cut_overlap_composition.py`
  - disjoint region 可组合；
  - overlap 穿 cut boundary BLOCK；
  - overlap delta source interval 穿 source gap BLOCK；
  - 缺 source provenance BLOCK；
  - canonical selection mismatch BLOCK。
- `test_v4_combined_recomposition_end_to_end.py`
  - 合法 cut/overlap artifact lineage；
  - `v4_compose_materializations → v4_render → v4_validate_release`；
  - cut 删除歌词不得复活；
  - 两路 confirmed overlap cue 必须实际重叠；
  - QA source stage/counts 正确。
- CLI bootstrap 加入 `v4_compose_materializations.py`。

最新 head 完整 CI 通过前不能声明 a8 可合并。

## 9. Calibration / migration

a8 不改声学参数，继续使用：

```text
production-bootstrap-2026-08-17-a7
```

但 package algorithm version 为 `4.0.0a8`，因此 a7 artifact 不能静默冒充 a8 artifact。升级任务需要按 a8 algorithm lineage 重新物化。

## 10. 当前仍未完成

### P1 — real-task calibration / blind-test

真实任务评估 mapping residual、cue onset/offset、cut P/R + boundary MAE、overlap P/R/IoU、track attribution、fragment correctness、review density/runtime。

### P2 — Editor Evidence + LanguageSpan final cue fusion

zh/en direct text、ko/ja phonetic hint、yue/unknown 降权/禁用文本；只作为 canonical/source mapping 后的辅助证据。

### P3 — Forced Alignment / ASR v2

由真实误差数据决定是否成为下一瓶颈。

### Explicit BLOCK — same-region joint cut+overlap

Overlap interval 穿 localized cut boundary 或 overlap source interval 穿 confirmed source gap 时，a8 明确 BLOCK；未来只有真实数据证明该类占比/价值足够高时再设计 joint acoustic model。

## 11. 当前正确表述

> **main 的 a7 已完成 confirmed cut rebuild；a8 正在增加 fail-closed cut+overlap materialization composition。a8 不改变声学阈值，也不允许 overlap 恢复已剪掉 source。最新 head CI 全绿前不合并。**
