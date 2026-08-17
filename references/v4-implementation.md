# Lyric Aligner v4 实施记录与关键代码说明

> 只记录已经进入代码的生产能力、契约和迁移边界。当前开发版本：`4.0.0a6`。

## 1. 当前正式分层

```text
lyric_aligner/
  assets/       # TrackAsset / TrackOccurrence / fail-closed resolution
  audio/        # features / Coarse / TimeWarp / Fine / Transition
  contracts/    # immutable artifact lineage
  io/           # strict task text input
  pipeline/     # PipelineContext / production planning
  qa/           # final integrity
  review/       # replayable candidate-level review decisions
  text/         # canonical lyrics / normalization / language spans
  timeline/
    projector.py # source→mix canonical projection
    composer.py  # final cue composition
    overlap.py   # confirmed-overlap timeline recomposition primitives
```

生产 CLI：

```text
v4_run.py
v4_review.py
v4_recompose_overlap.py
v4_render.py
v4_validate_release.py
```

## 2. 不变的 single-truth 约束

- Canonical lyric 是最终文字真源；
- Source-to-Mix TimeWarp 是主要时间真源；
- TrackAsset semantic identity 绑定 source SHA、raw lyric SHA、canonical same-timestamp selection SHA；
- ResolvedAssetBinding 后下游不得重新 fuzzy resolve source/LRC；
- ASR/Editor 不拥有 canonical text 权限。

## 3. Source-to-Mix / TimeWarp

```text
HPSS harmonic
 → Chroma CENS + MFCC
 → multi-candidate retrieval
 → global monotonic path
 → AFFINE first
 → evidence-driven PIECEWISE_RATE
 → Selective Fine only for difficult local windows
```

BPM 仅作 soft prior。`rate change != cut`；只有 source-position discontinuity 产生 cut candidate。Middle cut 不自动 confirmed。

## 4. Transition candidate identity

相邻 A/B 仍在：

```text
boundary ± transition.search_margin_seconds
```

共享窗口取证，但 a6 将 review 粒度从“整条边界”提升为**单候选区间**。

`transition_candidate_id()` 的 canonical identity：

```text
candidate_type
left_occurrence_id
right_occurrence_id
start_ms
end_ms
```

毫秒化后 canonical JSON SHA-256。

Transition output：

```text
overlap_candidates[]:
  candidate_id
  type=cross_track_overlap_candidate
  start/end
  occurrences
  left_score/right_score

uncertain_intervals[]:
  candidate_id
  start/end
  reason
```

因此同一 A→B window 中多个分离 candidate 可以独立 review。

## 5. `v4_run` a6 transition contract

`v4_run` schema 当前 `1.1`。

Candidate-level issue：

```text
transition_overlap
  code=cross_track_overlap_candidate
  candidate_id
  exact interval
  left/right scores

transition_ambiguity
  code=ambiguous_source_occurrence
  candidate_id
  exact interval
```

每个 transition summary 还 materialize exact provenance：

```text
left_coarse_path
left_coarse_artifact_path
right_coarse_path
right_coarse_artifact_path
transition_path
transition_artifact_path
```

这避免 confirmed-overlap stage 重新按文件名/目录猜 source evidence。

## 6. Review Decision 1.1

`REVIEW_DECISION_SCHEMA_VERSION = 1.1`。

Candidate-level transition issue identity 包含 `candidate_id`。Legacy `kind=transition` 仅保留已有回归兼容，新 a6 production run 使用 `transition_overlap/transition_ambiguity`。

`resolved_clear`：只移除当前 candidate issue。

`confirmed_overlap`：

```text
status=confirmed
decision_action=confirmed_overlap
requires_recomposition=true
confirmed_interval=[start,end]
```

并仍保持 active issue / `review_required`。

TimeWarp 仍只有 `confirmed_requires_rebuild`。

Transition summary 的人工结果现在使用：

```text
review_resolutions[]
```

而不是单一 resolution，支持同边界多个 candidate。

## 7. `timeline/overlap.py`

### `ConfirmedOverlapRegion`

字段：

```text
candidate_id
left_occurrence_id
right_occurrence_id
start_ms
end_ms
issue_id
region_id
```

`region_from_issue()` 只接受正式 confirmed-overlap issue，并要求非空 issue_id/candidate_id 与合法 interval。

### Strict region clipping

`clip_projected_result_to_region()`：

- line start/end 裁剪到 exact confirmed interval；
- token start/end 同样裁剪；
- region 外行不进入 overlap timeline；
- 不恢复未确认歌词区间。

### Primary + overlap merge

`merge_primary_with_overlap_lines()`：

- 保留 primary canonical lines；
- 同 canonical_line_index + 同 text + 时间连续/重叠时合并；
- 新 overlap-only canonical line 作为独立 line 加入；
- occurrence window 只扩展到 confirmed regions；
- result 记录 region/candidate IDs。

## 8. `v4_recompose_overlap.py`

输入必须是 stage=`review_resolution` 的 reviewed run，且 active issues 中存在 `confirmed_overlap + requires_recomposition`。

### 8.1 Candidate ↔ Transition evidence

对每个 region：

- 找唯一 A→B transition summary；
- 读取 summary materialized 的 transition payload/artifact；
- candidate_id 必须在 original overlap/uncertain candidate 集合中唯一出现；
- candidate interval 与 confirmed interval 毫秒级一致；
- occurrence pair 一致；
- transition artifact 必须属于 reviewed-run lineage。

### 8.2 Boundary coarse exact identity

`_effective_boundary_mapping()` 不只验证 artifact 合法，还验证：

```text
coarse.occurrence_id == expected side occurrence
coarse.track_id == exact ResolvedAssetBinding.track_id
coarse.canonical_selection_sha256 == binding canonical selection
coarse.upstream_asset_artifact_id == supplied TrackAsset artifact
coarse artifact upstream contains supplied TrackAsset artifact
```

LEFT/RIGHT mapping 因此不能交换。

此外 Transition artifact 必须：

```text
upstream contains supplied TrackAsset artifact
upstream contains exact LEFT boundary coarse artifact
upstream contains exact RIGHT boundary coarse artifact
```

左右 coarse artifact 必须是两个不同 artifact。

### 8.3 Selective Fine on boundary mapping

若 `should_run_fine_alignment(coarse)`：

```text
v4_fine_align.py
 → Fine payload/artifact
 → revalidate occurrence/track/canonical selection
```

随后：

```text
effective_timewarp(coarse, fine)
```

若仍 blocked，则 confirmed-overlap recomposition 失败。人工确认 overlap 不能覆盖坏 Source-to-Mix mapping。

### 8.4 Canonical overlap projection

每侧：

```text
ResolvedAssetBinding canonical lyrics
 + effective boundary TimeWarp
 + ProjectionWindow(confirmed region)
 → project_binding_timeline()
 → strict region clip
```

再与 primary timeline 合并。

### 8.5 Artifacts

每个受影响 occurrence 生成：

```text
stage=overlap_timeline_recomposition
role=canonical_timeline
```

upstream 包含：

- exact TrackAsset artifact；
- source primary timeline artifact；
- review_resolution artifact；
- boundary coarse/Fine artifacts；
- transition artifact。

最终 run：

```text
stage=overlap_recomposition
role=v4_recomposed_run
schema_version=1.2
```

processed confirmed-overlap issue IDs 从 active issues 移除，其他问题保留。remaining issue count=0 才 `ready_for_render`。

## 9. Final Composer confirmed-region gate

`compose_canonical_timelines(..., confirmed_overlap_regions=...)`。

Same-occurrence cue overlap仍直接 BLOCK。

Cross-track overlap 不再一律禁止，但每一个实际 pairwise intersection 都执行：

```text
pair={occA,occB}
intersection=[max(starts), min(ends)]
```

必须存在 exact occurrence pair 的 confirmed region 且：

```text
intersection.start >= region.start
intersection.end <= region.end
```

才允许。

实现检查**全部实际相交 cue pair**。排序后只检查相邻 cue 会漏掉“一个长 cue 同时覆盖另一轨多条短 cue”的情况，a6 已有专门 regression。

最终仍输出两个独立 cues；不拼歌词文本。

## 10. Renderer a6 run stages

`v4_render.py` 接受：

```text
production_orchestration / v4_production_run
review_resolution       / v4_reviewed_run
overlap_recomposition   / v4_recomposed_run
```

Overlap run 额外要求：

- non-empty overlap_recomposition metadata；
- source_review_artifact_id 位于 upstream；
- normalized config 与 payload source review ID 一致；
- remaining_issue_count=0；
- non-empty confirmed_overlap_regions。

Occurrence timeline stage 允许：

```text
canonical_timeline_projection
overlap_timeline_recomposition
```

原 task/profile/TrackAsset/canonical-selection/materialized hash 检查全部保留。

QA / Final Render artifact 记录：

```text
source_run_stage
confirmed_overlap_region_count
```

## 11. Release contract

`v4_validate_release.py` 规则不变：exact one final_render upstream、requested version/profile 一致、FINAL SRT/CSV/QA 与 render artifact size/SHA 一致、SRT/audit 逐 cue 绑定、QA ready flags 全 true。

## 12. Calibration / version migration

当前 calibration 内容仍为：

```text
production-bootstrap-2026-08-17-a4
```

a6 未调整阈值，只改变 algorithm/review/timeline contract。package version=`4.0.0a6`，因此 a5 artifacts 不可混入 a6 release；生产任务从 a6 `v4_run` 重跑。

## 13. a6 regression

- `test_v4_transition.py`：stable overlap/ambiguity candidate_id；
- `test_v4_review_decisions.py`：same-boundary multi-candidate independent issue/action；
- `test_v4_overlap_recomposition.py`：issue materialization、region clip、timeline merge、confirmed-region composer gate、non-adjacent pairwise overlap；
- `test_v4_overlap_lineage.py`：swapped occurrence/track/canonical/asset identity BLOCK；
- `test_v4_overlap_end_to_end.py`：artifact-level real `v4_recompose_overlap → v4_render`；
- CLI bootstrap 增加 recomposition CLI；
- 现有 reconstruction/review/render/release regressions继续保留。

Artifact-level overlap E2E 有意不依赖“合成音频是否恰好超过 bootstrap overlap threshold”，而是构造合法 fingerprinted coarse/transition/review/timeline lineage，直接测试 a6 新 stage 的确定性 contract。

## 14. 尚未实现

### Confirmed TimeWarp / middle-cut rebuild

`confirmed_requires_rebuild` 仍只冻结人工事实。下一里程碑需要重建 effective mapping、cut intervals 与 canonical timeline artifact。

### Real-task calibration

当前 a6 不能宣称真实准确率提升固定百分比；需用私有真实任务 evaluator/blind-test 验证。
