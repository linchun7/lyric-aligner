# Lyric Aligner v4 关键变更记录

> 所有实质性生产更新必须按 `documentation-contract.md` 在同一 PR 同步本文件。这里只记录已进入代码的行为、兼容/迁移和验证边界。

## 2026-08-17 — 已合入 main 的 v4 主线

- a3 production-first reconstruction：`cfa43f4c854b699819cd3acb0cfea575cd1a04c8`；
- a4 package-native final render/release：`236d9d717229147ee1d1a8755d712e54db47a751`；
- a5 replayable review decisions：`a80a531d6933946484c54d3a589bc55b0cb9e94b`；
- a6 confirmed-overlap dual-track recomposition：`dfd840b3a6f893531cce8019aae53e803243f95c`；
- a7 confirmed-cut Source-to-Mix / canonical timeline rebuild：`096210fbdbb8a55ee908b592bba20b1244c2821f`。

a7 PR #6 latest head `a69c76e91a30ad9b73747cd0fdf3320d0a9132d9` 的 validate #390 在 ASR + Python 3.10/3.12/3.14 全套 compile/docs/unit/E2E/Skill/privacy/environment/diff-check 全绿后 squash merge。

---

## 2026-08-17 — v4.0.0a8 Cut + Overlap Materialization Composition（当前开发）

目标：解决同一 reviewed task 同时存在 `confirmed_cut` 与 `confirmed_overlap` 时，两条已经各自验证过的 materialization 如何安全合成一个 renderable run；禁止人工拼 manifest，也不让 a6/a7 materializer 相互重写。

### 1. Version / calibration

```text
algorithm = 4.0.0a8
profile   = production-bootstrap-2026-08-17-a7
```

a8 不修改声学阈值，因此 calibration profile identity 保持 a7。算法/contract 已变化，所以 a7 artifacts 不能静默冒充 a8 artifacts。

### 2. 新 architecture layer

新增：

```text
lyric_aligner/timeline/composition.py
scripts/v4_compose_materializations.py
```

推荐链：

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

Cut/overlap 两个 materializer 保持独立、可单独回归。

### 3. New artifacts / schema

同一 occurrence 同时受 cut + overlap 影响时生成：

```text
stage = combined_timeline_recomposition
role  = canonical_timeline
```

Run-level：

```text
stage = combined_recomposition
role  = v4_combined_run
schema_version = 1.4
```

Combined timeline artifact upstream 到 exact：

- TrackAsset artifact；
- source review artifact；
- source cut_rebuild artifact；
- source overlap_recomposition artifact；
- source cut timeline artifact；
- source overlap timeline artifact。

### 4. Materialization identity gates

Composition 要求 cut/overlap 两个 run：

- task fingerprint 相同；
- algorithm version 相同；
- calibration profile/version 相同；
- exact TrackAsset artifact 相同且为两边 upstream；
- exact `source_review_artifact_id` 相同且为两边 upstream；
- payload/artifact materialized hash、stage、role 全部合法。

### 5. Issue-set composition

Cut materializer 只处理 cut issue，Overlap materializer 只处理 overlap issue。a8 读取两边 `processed_issue_ids`：

- cut processed issue 从 overlap active set 抵消；
- overlap processed issue 从 cut active set 抵消；
- 两边不能声称处理同一个 issue_id；
- 同一个 remaining issue snapshot 若不一致则 BLOCK；
- 真正未解决 issue 保留。

只有 combined `remaining_issue_count=0` 才 `ready_for_render`。

### 6. Cut-aware base + overlap-only delta

同 occurrence 同时被两边修改时，以 **cut_timeline_rebuild 结果为 base**，只从 overlap timeline 提取带 `overlap_region/candidate/recomposed` provenance 的 delta lines。

这样不会把 overlap materializer 的旧 primary timeline 整体覆盖到 cut-aware timeline 上。

### 7. 两层 fail-closed safety

#### 7.1 Mix-time boundary

Confirmed overlap interval 不能包含 localized cut boundary：

```text
overlap.start <= cut_mix_time <= overlap.end
→ TimelineCompositionError
```

同一区域需要 joint acoustic model，a8 不猜。

#### 7.2 Source-time gap

Overlap delta 必须保留 canonical `source_start_ms/source_end_ms`：

- finite source interval 与 confirmed source gap 相交：BLOCK；
- 缺 source_start provenance：BLOCK；
- open source interval 不能证明位于 gap 之后：BLOCK。

这阻止 overlap materialization 重新带回 a7 已确认删除的 canonical source。

### 8. Renderer changes

`v4_render.py` 新增 `combined_recomposition / v4_combined_run`，仍走同一套 final SRT/audit/QA/release 路径。

Combined mode：

- 同时验证 cut metadata、overlap metadata、combined metadata；
- cut/overlap materializer 原本各自残留另一类 issue 是允许的；
- combined metadata 自己必须 `remaining_issue_count=0`；
- cut `canonical_fragment_issue_count=0` 仍是硬门禁；
- cut+overlap same occurrence 只能使用 `combined_timeline_recomposition`；
- combined timeline 必须 upstream 到 declared source cut/overlap timeline IDs；
- composer 对 cross-track cue pair 的 exact confirmed-region 检查不变。

QA/Final Render 增加：

```text
source_run_stage = combined_recomposition
combined_recomposition_occurrence_count
```

### 9. Regression coverage

新增：

- `test_v4_cut_overlap_composition.py`
  - disjoint overlap 可叠加到 cut-aware base；
  - overlap 穿 cut boundary BLOCK；
  - overlap canonical source interval 穿 source gap BLOCK；
  - 缺 source provenance BLOCK；
  - canonical-selection mismatch BLOCK。
- `test_v4_combined_recomposition_end_to_end.py`
  - 构造合法 cut/overlap artifacts；
  - 执行 `v4_compose_materializations → v4_render → v4_validate_release`；
  - cut 删除歌词不得复活；
  - 两路 overlap cue 必须实际相交；
  - QA source stage/counts 正确。
- CLI bootstrap 加入 `v4_compose_materializations.py`。

### 10. Known boundary / next work

仍明确 BLOCK：

- overlap interval 与 localized cut boundary 相交；
- overlap source interval 与 confirmed source gap 相交；
- source provenance 不足；
- line-LRC partial cut / timed token cut 等 a7 已知 ambiguity。

P0 结构 composition 完成后，下一优先级转为 **real-task calibration / blind-test**；再根据真实误差决定 Editor Evidence/LanguageSpan、Forced Alignment/ASR v2 的收益顺序。

## 验证纪律

a8 只有 latest head 在 Python 3.10/3.12/3.14、ASR、Documentation Contract、unit/E2E、Skill/privacy/environment/diff-check 全绿后才可合并。不能用 a7 的 #390 代替 a8 验收。
