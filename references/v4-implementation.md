# Lyric Aligner v4 实施记录与关键代码说明

> 只记录已经进入代码的生产能力、契约和迁移边界。当前开发版本：`4.0.0a8`。

## 1. 当前分层

```text
lyric_aligner/
  assets/       # TrackAsset / TrackOccurrence / fail-closed resolution
  audio/        # features / coarse / TimeWarp / Fine / transition / cuts
  contracts/    # immutable artifact lineage
  pipeline/     # context / production planning
  review/       # replayable candidate-level review decisions
  text/         # canonical lyrics / normalization / language spans
  timeline/
    projector.py
    overlap.py
    cuts.py
    composition.py   # a8 cut+overlap materialization composition
    composer.py
  qa/           # final integrity
```

生产 CLI：

```text
v4_run.py
v4_review.py
v4_recompose_overlap.py
v4_rebuild_cut.py
v4_compose_materializations.py
v4_render.py
v4_validate_release.py
```

## 2. Single-truth 不变约束

- Canonical lyric 是最终文字真源；
- Source-to-Mix TimeWarp 是主要时间真源；
- TrackAsset identity 绑定 source/raw lyric/canonical selection；
- ResolvedAssetBinding 后不得重新 fuzzy resolve；
- ASR/Editor 不能替换 canonical text；
- Artifact lineage 必须闭合到 exact task/profile/upstream materialization。

## 3. a7 基础：cut 与 overlap 已独立 materialize

### Overlap

`v4_recompose_overlap.py` 从 reviewed run 的 exact confirmed-overlap candidate 出发，利用 boundary-local mapping 重投影左右 canonical lyrics，生成：

```text
overlap_timeline_recomposition
overlap_recomposition
```

### Cut

`v4_rebuild_cut.py` 从 exact confirmed discontinuity 出发，执行 local cut localization、CUT_AWARE segment fitting、cut-aware canonical projection，生成：

```text
cut_timewarp_rebuild
cut_timeline_rebuild
cut_rebuild
```

两条 materializer 都只处理自己负责的 issue，其他 reviewed issue 原样保留。

## 4. a8 为什么增加第三层 composition

同一 reviewed task 同时有 confirmed cut + confirmed overlap 时，不让两条 materializer 互相消费/重写对方输出。采用：

```text
review_resolution
 ├─ cut_rebuild
 └─ overlap_recomposition
          ↓
combined_recomposition
```

优点：

- a6/a7 仍可独立回归；
- composition 只处理已物化事实；
- 不重复跑声学；
- 不需要人工拼 manifest；
- 可对 cut/overlap 冲突做独立 fail-closed 判断。

## 5. `timeline/composition.py`

### 5.1 Cut boundary / source gap extraction

从 cut-aware timeline 的 `cuts[]` 读取：

```text
cut_mix_time
source_gap_start
source_gap_end
```

要求边界唯一递增、source gaps 正向且互不交叉。

### 5.2 Overlap delta extraction

只提取 overlap timeline 中带以下任一 provenance 的 line：

```text
overlap_region_id(s)
overlap_candidate_id(s)
overlap_recomposed=true
```

不把 overlap materializer 的整个 primary timeline 覆盖到 cut-aware base。

### 5.3 Mix-time disjointness

对同 occurrence 的 confirmed overlap region：

```text
region.start_ms <= cut_boundary_ms <= region.end_ms
```

成立则 `TimelineCompositionError`。原因是 cut 与双轨叠唱发生在同一局部声学区域，需要 joint model；a8 不做猜测性合成。

### 5.4 Source-time disjointness

每条 overlap delta 必须携带 canonical source provenance。

Finite interval `[source_start_ms, source_end_ms)` 与任一 confirmed source gap 相交则 BLOCK。

Open interval 只有在其 start 已位于所有 relevant gap 之后才可接受；否则无法证明未穿过被删 source。

### 5.5 Merge

安全检查通过后：

```text
cut-aware timeline base
 + overlap-only delta lines
 → existing merge_primary_with_overlap_lines()
 → combined cut-aware result
```

保留 `cut_aware=true`、`cuts[]`，并增加 combined diagnostics。

## 6. `v4_compose_materializations.py`

### Input contract

```text
cut_rebuild / v4_cut_rebuilt_run
overlap_recomposition / v4_recomposed_run
track_assets / asset_resolution
```

两边必须：

- same task fingerprint；
- same algorithm version；
- same calibration profile/version；
- same source review artifact ID；
- same TrackAsset artifact；
- review/asset 均在两边 upstream lineage。

### Issue-set algebra

记：

```text
C = cut_rebuild.processed_issue_ids
O = overlap_recomposition.processed_issue_ids
```

要求 `C ∩ O = ∅`。

Combined remaining set：

```text
(cut_active - O) ∪ (overlap_active - C)
```

同 issue_id 在两边都仍 active 时 snapshot 必须完全一致，否则 BLOCK。

### Occurrence selection

```text
cut-only      → cut timeline
overlap-only  → overlap timeline
untouched     → 两边必须引用同一 timeline artifact
cut+overlap   → combined_timeline_recomposition
```

### New artifacts

```text
combined_timeline_recomposition / canonical_timeline
combined_recomposition          / v4_combined_run
```

Combined timeline upstream：TrackAsset、review、cut run、overlap run、source cut timeline、source overlap timeline。

Combined run schema=`1.4`，保存 source run artifact IDs、new combined timeline IDs、combined occurrence count、remaining issue count。

## 7. Renderer a8

`v4_render.py` stage whitelist：

```text
production_orchestration
review_resolution
overlap_recomposition
cut_rebuild
combined_recomposition
```

Renderer 对 combined mode 同时加载 cut/overlap/combined metadata。

重要区别：单独 cut/overlap run 要求其自己的 `remaining_issue_count=0`；combined run 中原 materializer 可以仍记录“另一类 issue 尚未由自己处理”，最终只要求：

```text
combined_recomposition.remaining_issue_count = 0
cut_rebuild.canonical_fragment_issue_count = 0
```

Same-occurrence cut+overlap 必须：

```text
timeline_stage = combined_timeline_recomposition
cut_rebuilt = true
overlap_recomposed = true
combined_recomposed = true
```

并验证 source cut/overlap timeline artifact IDs 都是 declared upstream。

Final composer 的 cross-track exact confirmed-region pairwise 检查保持不变。

## 8. Algorithm / calibration identity

```text
algorithm_version = 4.0.0a8
calibration_profile = production-bootstrap-2026-08-17-a7
```

a8 没改声学阈值，因此 profile 不改；algorithm contract 改变，所以 a7 artifacts 不能作为 a8 artifact 复用。

## 9. Regression

新增：

- `test_v4_cut_overlap_composition.py`：mix/source 双层 disjointness、source provenance、canonical identity；
- `test_v4_combined_recomposition_end_to_end.py`：artifact-level composition → render → release，验证 cut lyric 不复活且两路 overlap cue 实际相交；
- CLI bootstrap 新增 `v4_compose_materializations.py`。

Existing a6 overlap、a7 cut、review、render、release regressions 全部继续运行。

## 10. Explicit architectural boundary

a8 只解决**可证明互不冲突**的 cut+overlap materialization composition。

以下仍不自动化：

- overlap interval 穿 localized cut boundary；
- overlap canonical source interval 穿 confirmed source gap；
- source provenance 缺失；
- 需要同时解释 cut 与叠唱的同区域声学。

这类问题只有在 real-task blind-test 证明有足够占比/价值后，才值得新增 joint acoustic stage。
