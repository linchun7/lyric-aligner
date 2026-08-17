# Lyric Aligner v4 关键变更记录

> 所有实质性生产更新必须按 `documentation-contract.md` 在同一 PR 同步本文件。这里只记录已进入代码的行为、兼容/迁移和验证边界。

## 2026-08-17 — 已合入 main 的 v4 主线

- a3 production-first reconstruction：`cfa43f4c854b699819cd3acb0cfea575cd1a04c8`；
- a4 package-native final render/release：`236d9d717229147ee1d1a8755d712e54db47a751`；
- a5 replayable review decisions：`a80a531d6933946484c54d3a589bc55b0cb9e94b`；
- a6 confirmed-overlap dual-track recomposition：`dfd840b3a6f893531cce8019aae53e803243f95c`。

### Foundation retained on main

- fail-closed TrackAsset / TrackOccurrence / ResolvedAssetBinding；
- canonical lyric single truth，包括 line LRC / Enhanced LRC / QRC exact original selection；
- HPSS harmonic + Chroma CENS + MFCC Source-to-Mix；
- global monotonic path + AFFINE-first / evidence-driven PIECEWISE_RATE；
- Selective Fine；
- candidate-level transition evidence/review；
- replayable Review Decision；
- confirmed-overlap 两路独立 canonical timeline recomposition；
- final SRT/audit/QA/release immutable artifact lineage；
- Documentation Contract / Skill / privacy / environment / multi-Python CI。

---

## 2026-08-17 — v4.0.0a7 Confirmed-cut Source Gap Rebuild（当前 PR #6）

目标：把人工确认的 source-position discontinuity materialize 成显式 source gap + retained TimeWarp segments + cut-aware canonical timeline，而不是把 blocked TimeWarp 布尔值改掉或直接删除歌词。

### 1. Version / profile

```text
package = 4.0.0a7
profile = production-bootstrap-2026-08-17-a7
```

新增 versioned `CutBoundaryConfig`。Bootstrap 当前使用 16kHz、约 0.8s context、50ms candidate step、harmonic Chroma/MFCC 双特征及 score/margin/feature-agreement/boundary-margin 门禁。数值尚未经过真实任务 calibration。

完整 profile 内容改变，因此 a6/a5 artifacts 不允许混入 a7 release。

### 2. Candidate-level source discontinuity

`v4_run.py` 对每个 effective TimeWarp forward source jump 单独生成：

```text
kind=timewarp_discontinuity
candidate_id
occurrence_id
mix_before/mix_after
source_before/source_after
```

Occurrence summary 固定记录 primary Coarse/Fine path + artifact provenance，后续 rebuild 不重新猜文件。

### 3. Review Decision schema 1.2

新增 discontinuity actions：

```text
confirmed_cut
rejected_requires_remap
```

两者都不会让 run 直接 ready。

`confirmed_cut` 冻结 exact discontinuity snapshot，写 `requires_timeline_rebuild=true`；`rejected_requires_remap` 表明“不是物理 cut”，但 failed mapping 仍必须 remap。

### 4. Local cut boundary locator

新增 `lyric_aligner/audio/cuts.py`。

Coarse `[mix_before,mix_after]` 只作为搜索窗口，不使用中点当 cut 真值。对候选切点分别要求：

```text
left context  -> source-before
right context -> source-after
```

并通过 per-side score/margin/feature agreement、non-ambiguous、boundary best-vs-separated-second margin。source gap 非 forward/过小也 BLOCK。

### 5. CUT_AWARE TimeWarp

`build_cut_aware_timewarp()` 生成：

```text
retained segment 0 (AFFINE/PIECEWISE_RATE)
explicit source gap
retained segment 1 (AFFINE/PIECEWISE_RATE)
[...]
```

每个 retained segment 使用原 alignment anchors + localized boundary anchors，重新运行现有 `select_timewarp()`。任一 segment blocked => rebuild BLOCK。

Artifact：

```text
cut_timewarp_rebuild / cut_aware_timewarp
```

### 6. Cut-aware canonical projection

新增 `lyric_aligner/timeline/cuts.py`。

#### line-LRC

安全规则已从“line start 在 gap 就整行删除”收紧为：

- **整个可推断行区间都位于 source gap**：才能自动 omit；
- line start 在 gap 内但可能延续到 gap 后：partial-line review；
- line interval 从 retained segment 穿过 gap：review；
- last/open line start 在 gap 内：review；
- 完整位于 retained segment：正常投影。

这样避免把可能仍可听到后半句的 line-LRC 整行误删。

#### Enhanced LRC / QRC

- complete token retained -> keep；
- complete token in gap -> omit；
- token 本身被 cut 穿过 -> review；
- 一行只剩部分完整 tokens -> canonical fragment，只来自规范歌词 token。

Artifact：

```text
cut_timeline_rebuild / canonical_timeline
```

### 7. `v4_rebuild_cut.py`

正式链：

```text
review_resolution
 + exact TrackAsset
 + exact primary Coarse/Fine
 + confirmed_cut
 → localize cut
 → cut_timewarp_rebuild
 → cut_timeline_rebuild
 → cut_rebuild / v4_cut_rebuilt_run
```

Confirmed candidate 必须重新对应 current effective TimeWarp 唯一 discontinuity；snapshot 与 current evidence 必须一致；primary mapping identity/lineage 必须与 reviewed run 和 TrackAsset 完全闭合。

Projection ambiguity 会变成新的 active `canonical_fragment` issue。

### 8. Renderer

`v4_render.py` 新增支持：

```text
cut_rebuild / v4_cut_rebuilt_run
cut_timeline_rebuild / canonical_timeline
```

必须 `remaining_issue_count=0`、`canonical_fragment_issue_count=0`，且 cut mapping/timeline/review/TrackAsset lineage 完整、`cut_aware=true`、projection_issues=[]。

### 9. Regression coverage

当前 PR 已新增：

- `test_v4_cut_review.py`；
- `test_v4_cut_mapping.py`；
- `test_v4_cut_timeline.py`：whole-gap omission vs partial-line review、word-timed fragments、token-cut BLOCK；
- `test_v4_cut_boundary_locator.py`：synthetic WAV physical cut locator；
- `test_v4_cut_rebuild_end_to_end.py`：artifact-level `review_resolution → v4_rebuild_cut → v4_render`；
- CLI bootstrap 加入 `v4_rebuild_cut.py`；
- 既有 overlap/review/render/release regressions 全保留。

### 10. CI history / current gate

PR #6 首轮 validate #362：

- ASR environment：SUCCESS；
- compileall：SUCCESS；
- Python 3.10/3.12/3.14 均在 Documentation Contract 阶段失败；
- unit tests 因 docs gate 未通过而没有执行。

根因是 a7 owning docs 当时没有实际提交到分支，而不是 cut algorithm failure。当前 PR 已补交 owning docs；必须以最新 head 重新跑完整 CI，不能用旧 head 作为验收。

### 11. 尚未完成

- confirmed cut + confirmed overlap 同任务的 unified stage composition；
- real private calibration / blind-test；
- Editor Evidence + LanguageSpan final cue fusion；
- Forced Alignment / ASR v2 由真实误差决定优先级。

## 验证纪律

任何“测试通过/可合并”结论必须绑定具体 latest head/CI。PR #6 保持 Draft，最新 head 的 Python 3.10/3.12/3.14、ASR、Documentation Contract、unit/E2E、Skill/privacy/environment/diff-check 全绿后才可合并。
