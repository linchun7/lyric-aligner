# Lyric Aligner v4 当前实施状态

更新日期：2026-08-17  
当前开发分支：`agent/v4-cut-rebuild`  
基线：`main` 已合入 v4.0.0a6（squash commit `dfd840b3a6f893531cce8019aae53e803243f95c`）  
当前开发版本：`4.0.0a7`  
TrackAsset schema：`1.1`

## 1. main 当前能力

main 已具备：

- fail-closed TrackAsset / canonical lyric single truth；
- harmonic Chroma/MFCC Source-to-Mix mapping；
- AFFINE-first / evidence-driven PIECEWISE_RATE；
- Selective Fine；
- candidate-level transition review；
- replayable Review Decision；
- confirmed-overlap 双路 canonical timeline recomposition；
- package-native renderer/release integrity。

PR #5 a6 latest head `2b93af319b4b4f32115b20644b2b2525f0f14ca0` 的 validate #349 在 Python 3.10/3.12/3.14 + ASR + docs/Skill/privacy/environment/diff-check 全绿后 squash merge 到 main `dfd840b3...`。

## 2. a7 当前目标

解决 remaining P0：**人工确认的 middle cut 如何真正变成可重放 Source-to-Mix mapping + canonical timeline**。

现有连续 TimeWarp 只能安全表达 AFFINE/PIECEWISE_RATE，不能把 forward source-position jump 当成连续映射。a7 新增：

```text
continuous segment
 → explicit source gap
 → continuous segment
```

称为 `CUT_AWARE` mapping。

## 3. Candidate-level TimeWarp discontinuity

`v4_run` 对每个 effective TimeWarp source-position jump 独立产生：

```text
kind=timewarp_discontinuity
code=source_position_discontinuity
candidate_id
occurrence_id
mix_before/mix_after
source_before/source_after
```

Occurrence summary 同时保存 exact primary Coarse/Fine payload + artifact provenance，后续 cut rebuild 不重新猜文件。

## 4. Review Decision 1.2

新增 discontinuity actions：

```text
confirmed_cut
rejected_requires_remap
```

`confirmed_cut`：

```text
status=confirmed
confirmed_discontinuity snapshot
requires_timeline_rebuild=true
仍 review_required
```

`rejected_requires_remap` 也保持 BLOCK，因为当前 mapping 已失败；“不是 cut”不能被解释为“mapping 没问题”。

## 5. Local cut boundary localization

新增 `lyric_aligner/audio/cuts.py`。

Coarse discontinuity 只限定 `[mix_before,mix_after]`。a7 不使用中点当 cut 真值，而是在局部区间用：

- 16kHz；
- HPSS harmonic；
- Chroma CENS + MFCC；
- bootstrap 0.8s context；
- bootstrap 50ms candidate step；
- source-position/slope local priors；
- per-side score/margin/feature agreement；
- boundary best-vs-separated-second margin。

同时要求左窗口匹配 source-before、右窗口匹配 source-after。证据不足、重复段歧义、source gap 非 forward/过小都 BLOCK。

## 6. CUT_AWARE TimeWarp

`build_cut_aware_timewarp()`：

1. 按 localized cut time 划分 retained mix segments；
2. 每段使用原 effective alignment anchors；
3. 用 localized source-gap 两侧添加 boundary anchors；
4. 每段独立运行现有 `select_timewarp()`；
5. 任一 segment blocked → 整次 rebuild 失败；
6. 输出 retained segments + explicit source gaps。

Artifact：

```text
stage=cut_timewarp_rebuild
role=cut_aware_timewarp
```

## 7. Cut-aware canonical timeline

新增 `lyric_aligner/timeline/cuts.py`。

### line-LRC

安全规则已收紧为：

- **整个可推断行区间都落在 source gap**：整行 omit；
- 行起点位于 gap 内但下一行时间越过 gap：partial-line ambiguity，review；
- 行区间从 retained segment 穿过 gap：review；
- 最后一行起点在 gap 内且没有 finite end：review；
- 完整位于 retained segment：正常投影。

因此 a7 不使用“只要 line start 在 gap 就整行删除”的过度推断。

### Enhanced LRC/QRC

- complete token retained → keep；
- complete token in gap → omit；
- token 本身被 cut 穿过 → review；
- 一行剩部分完整 tokens → canonical fragment，文本仍来自规范歌词。

Artifact：

```text
stage=cut_timeline_rebuild
role=canonical_timeline
```

## 8. `v4_rebuild_cut.py`

输入必须是合法 `review_resolution` run，active issues 中存在 `confirmed_cut + requires_timeline_rebuild`。

它重新验证：

- candidate_id 必须对应 current effective TimeWarp 唯一 discontinuity；
- confirmed snapshot 与 current evidence 一致；
- exact primary Coarse/Fine occurrence/track/canonical/TrackAsset identity；
- primary artifacts 属于 reviewed-run lineage。

然后：

```text
localize cut boundary
 → CUT_AWARE mapping
 → cut-aware canonical timeline
 → cut_rebuild run
```

新 run stage：

```text
cut_rebuild / v4_cut_rebuilt_run
schema_version=1.3
```

Projection ambiguity 会转化成新的 active `canonical_fragment` review issues。只有 remaining issues=0 才 `ready_for_render`。

## 9. Renderer a7

`v4_render.py` 新增支持：

```text
cut_rebuild / v4_cut_rebuilt_run
```

额外要求：

- source-review lineage 完整；
- remaining_issue_count=0；
- canonical_fragment_issue_count=0；
- rebuilt mapping/timeline artifact IDs 都是 run upstream；
- timeline stage=`cut_timeline_rebuild`；
- `cut_aware=true`；
- projection_issues=[]；
- timeline upstream 到 exact cut mapping artifact。

QA/Final Render artifact 记录 `source_run_stage=cut_rebuild` 与 rebuilt cut occurrence count。

## 10. Calibration / migration

新增 `CutBoundaryConfig`，profile version：

```text
production-bootstrap-2026-08-17-a7
```

因此 a6/a5 profile/artifacts 不允许静默复用到 a7；生产任务需要从 a7 `v4_run` 重跑。

## 11. 当前测试 / CI 状态

当前分支已加入：

- `test_v4_cut_review.py`；
- `test_v4_cut_mapping.py`；
- `test_v4_cut_timeline.py`：whole-gap omission / partial-line review / token fragments；
- `test_v4_cut_boundary_locator.py`；
- `test_v4_cut_rebuild_end_to_end.py`：artifact-level `review_resolution → v4_rebuild_cut → v4_render`；
- CLI bootstrap 加入 `v4_rebuild_cut.py`；
- renderer cut-rebuild strict lineage/fragment gate。

PR #6 首轮 validate #362：ASR environment 成功；Python 3.10/3.12/3.14 在 unit tests 之前被 Documentation Contract 阻断。根因是 a7 owning docs 当时没有实际落到分支。当前 PR 已补回 owning docs，需以最新 head 重新跑完整 CI；在最新 head 全绿前不能声明 a7 可合并。

## 12. 当前仍未完成

### P0 — cut + overlap unified composition

同一任务同时含 confirmed cut + confirmed overlap 时，当前两个 materialization stage 尚不能自动组合成一个 ready run；必须继续 fail-closed。

### P1 — real-task calibration / blind-test

用真实私有任务评估 mapping residual、onset/offset、review density、cut/overlap P/R、runtime。

### P2 — Editor Evidence + LanguageSpan final cue fusion

只在 canonical/source mapping 后作为辅助边界 evidence。

### P3 — Forced Alignment / ASR v2

由真实误差数据决定是否成为下一瓶颈。

## 13. 当前正确表述

> **v4.0.0a7 正在把 confirmed middle cut 从人工事实升级为显式 source-gap TimeWarp + cut-aware canonical timeline；无法证明的部分歌词继续 fail-closed。PR #6 只有最新 head 完整 CI 全绿后才可合并。**
