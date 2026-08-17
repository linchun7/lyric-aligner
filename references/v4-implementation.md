# Lyric Aligner v4 实施记录与关键代码说明

> 只记录已经进入代码的生产能力、契约和迁移边界。当前开发版本：`4.0.0a7`。

## 1. 当前正式分层

```text
lyric_aligner/
  assets/       # TrackAsset / TrackOccurrence / fail-closed resolution
  audio/
    features.py
    coarse_mapper.py
    timewarp.py
    fine_alignment.py
    transition.py
    cuts.py      # confirmed-cut local boundary + CUT_AWARE mapping
  contracts/    # immutable artifact lineage
  pipeline/     # context / production planning
  review/       # replayable candidate-level review decisions
  text/         # canonical lyrics / normalization / language spans
  timeline/
    projector.py
    overlap.py
    cuts.py      # cut-aware canonical projection
    composer.py
  qa/           # final integrity
```

生产 CLI：

```text
v4_run.py
v4_review.py
v4_recompose_overlap.py
v4_rebuild_cut.py
v4_render.py
v4_validate_release.py
```

## 2. 不变的 single-truth 约束

- Canonical lyric 是最终文字真源；
- Source-to-Mix TimeWarp 是主要时间真源；
- TrackAsset identity 绑定 source SHA / raw lyric SHA / canonical selection SHA；
- ResolvedAssetBinding 后下游不得重新 fuzzy resolve source/LRC；
- ASR/Editor 不拥有 canonical text 权限。

## 3. Continuous TimeWarp 与 cut 的模型边界

普通 mapping：

```text
AFFINE
PIECEWISE_RATE
```

两者都要求 source position 随 mix time 连续。局部倍率改变不等于 cut。

Middle cut 会在连续 mix 时间上产生 forward source-position jump，因此不能通过给 continuous model 加一个 `confirmed=true` 来表达。

a7 新增：

```text
CUT_AWARE
  retained continuous segment 0
  explicit source gap
  retained continuous segment 1
  [...]
```

每个 retained segment 仍然使用普通 AFFINE/PIECEWISE_RATE。

## 4. Discontinuity candidate identity / Review 1.2

`audio/cuts.py::discontinuity_candidate_id()` 绑定：

```text
occurrence_id
mix_before_ms/mix_after_ms
source_before_ms/source_after_ms
```

`v4_run` 对每个 physical source jump 生成独立 `timewarp_discontinuity` issue，并在 occurrence summary 保存 primary Coarse/Fine exact provenance。

Review Decision schema=`1.2`：

```text
confirmed_cut
rejected_requires_remap
```

两者都不会让 run 直接 ready。

`confirmed_cut` materialize：

```text
status=confirmed
decision_action=confirmed_cut
requires_timeline_rebuild=true
confirmed_discontinuity={mix_before,mix_after,source_before,source_after}
```

## 5. Local cut boundary locator

`audio/cuts.py::locate_cut_boundary()`：

1. confirmed `[mix_before,mix_after]` 仅作为搜索区间；
2. 使用 exact source audio + local mix audio；
3. 提取 harmonic Chroma CENS + MFCC；
4. 在 bootstrap 50ms candidate step 上搜索 cut time；
5. left context 匹配 source-before 邻域；
6. right context 匹配 source-after 邻域；
7. per-side score / top1-top2 margin / feature agreement / non-ambiguous；
8. joint score 取较弱一侧，避免一侧极强掩盖另一侧错误；
9. best 必须明显优于时间上分离的 second；
10. top-left source_end / top-right source_start 形成 localized source-gap evidence。

证据不足、重复段歧义、source gap 非 forward/过小均 fail-closed。

Bootstrap 配置进入 `CutBoundaryConfig`，不散落隐藏常量。

## 6. CUT_AWARE segment fitting

`build_cut_aware_timewarp()`：

```text
alignment path + localized cut boundaries
        ↓
按 cut_mix_time 分 retained mix segments
        ↓
每段原 anchors + localized source boundary anchors
        ↓
select_timewarp(segment)
        ↓
AFFINE or PIECEWISE_RATE per segment
```

每段要求足够 unique anchors；任一 segment blocked，整个 cut rebuild 失败。

Serialized mapping 保存：

```text
kind=CUT_AWARE
mix_start/mix_end
segments[]:
  index
  mix_start/mix_end
  source_start/source_end
  mapping
  selection/diagnostics
cuts[]:
  candidate_id/issue_id
  cut_mix_time
  source_gap_start/source_gap_end
  localized evidence
```

Source jump 不再被隐藏进一个连续公式。

## 7. Cut-aware canonical projection

`timeline/cuts.py` 只消费 CUT_AWARE mapping + exact canonical TrackAsset。

### 7.1 Segment source lookup

Canonical source timestamp 只允许落在某个 retained source segment。位于 explicit source gap 的 timestamp 视为被剪掉，但是否能删除文字取决于 lyric timing 粒度。

### 7.2 line-LRC

Line end 只能由下一 canonical line start 推断，因此采用保守规则：

- **整个可推断 line interval 都在 source gap**：可确定整行被删除，omit；
- line start 在 gap 内但 inferred end 超过 gap：partial-line ambiguity，review；
- line interval 从 retained segment 穿过 gap：review；
- last/open line start 在 gap 内：没有 finite end，review；
- line 完整在同一个 retained segment：正常 inverse projection。

这条规则避免“只看 line start 就整行删掉”的错误推断。

### 7.3 Word-timed canonical lyric

Enhanced/QRC token 逐 token：

- complete token retained：投影；
- complete token in gap：省略；
- token interval 被 gap boundary 穿过：review；
- 幸存 tokens 按 retained segment 分组；
- 一行只剩部分完整 tokens 时生成 `canonical_fragment` rows，正文只来自原 canonical token text。

## 8. `v4_rebuild_cut.py`

### Input contract

必须是：

```text
stage=review_resolution
status=review_required
active confirmed_cut issue(s)
```

同时要求 supplied TrackAsset artifact 是 reviewed-run upstream。

### Primary mapping provenance

Occurrence summary 必须 materialize：

```text
coarse_path / coarse_artifact_path
fine_path / fine_artifact_path (optional)
primary_interval
```

Rebuild 对 Coarse/Fine 重新验证：

```text
occurrence_id
track_id
canonical_selection_sha256
upstream TrackAsset artifact
reviewed-run lineage
```

### Candidate replay

confirmed candidate_id 必须重新映射到 current effective TimeWarp 的唯一 discontinuity；confirmed snapshot 与该 discontinuity 四个坐标完全一致，否则 BLOCK。

### Outputs

Per occurrence：

```text
cut_timewarp_rebuild / cut_aware_timewarp
cut_timeline_rebuild / canonical_timeline
```

Run-level：

```text
cut_rebuild / v4_cut_rebuilt_run
schema=1.3
```

Projection ambiguity 不会被吞掉，而会形成新的 deterministic `canonical_fragment` active issues。

## 9. Renderer a7

`v4_render.py` run stage whitelist：

```text
production_orchestration
review_resolution
overlap_recomposition
cut_rebuild
```

Cut rebuild 必须：

```text
remaining_issue_count=0
canonical_fragment_issue_count=0
rebuilt_occurrence_count>=1
mapping/timeline artifact IDs ∈ run upstreams
```

`cut_timeline_rebuild` 还必须：

```text
cut_aware=true
projection_issues=[]
cut_mapping_artifact_id non-empty
cut_mapping_artifact_id ∈ timeline upstreams
cut_mapping_artifact_id ∈ run upstreams
```

原 task/profile/TrackAsset/canonical-selection/materialized hash gate 不变。

## 10. Profile migration

Default profile=`production-bootstrap-2026-08-17-a7`，新增完整 `cut_boundary` 区段。因此 a6/a5 profile/artifacts fail-closed，不静默补字段。

## 11. Tests

当前分支新增：

- `test_v4_cut_review.py`；
- `test_v4_cut_mapping.py`；
- `test_v4_cut_timeline.py`：whole-gap omission / partial-line review / token fragments；
- `test_v4_cut_boundary_locator.py`：synthetic WAV physical cut locator；
- `test_v4_cut_rebuild_end_to_end.py`：artifact-level `review_resolution → v4_rebuild_cut → v4_render`；
- CLI bootstrap 加 `v4_rebuild_cut.py`；
- 既有 overlap/review/render/release tests 保留。

## 12. 已知架构边界

`v4_rebuild_cut.py` 与 `v4_recompose_overlap.py` 当前都是从 `review_resolution` 启动的独立 materialization stage。同一任务同时 confirmed cut + confirmed overlap 时尚不能自动组合为一个 ready run，必须 fail-closed；下一里程碑做 unified stage composition，而不是人工拼 artifact。

Real private calibration/blind-test 也尚未完成，因此 bootstrap cut locator 参数不能被描述为最优阈值。
