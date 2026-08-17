---
name: lyric-aligner
description: Reconstruct, review, recompose and render multilingual canonical lyric subtitles for edited music mixes using fingerprinted TrackAssets, Source-to-Mix TimeWarp, confirmed-overlap dual-track timelines, confirmed-cut local boundary localization, cut-aware canonical projection, strict final SRT/audit/QA binding, and immutable release lineage.
---

# Lyric Aligner

当前开发架构为 **v4.0.0a7 production-first**。新真实任务优先 v4；不可可靠解释的 mapping、cut、transition/overlap 必须 review/BLOCK，**不得静默回退 v3.9**。

## 不可违反的原则

1. **Canonical lyric 是最终文字与顺序真源。** ASR、剪映/编辑器只提供 evidence。
2. **Source-to-Mix audio mapping 是主要时间真源。** 编辑器 SRT 时间不是默认权威。
3. 普通歌曲先 AFFINE；证据证明固定倍率不足时才升级 PIECEWISE_RATE。BPM 只作 soft prior。
4. `rate change != cut`。只有 source-position discontinuity 才产生 cut candidate。
5. **confirmed_cut 不等于直接删除歌词。** 人工只确认 source jump 是物理剪切；系统仍必须局部定位真实 mix cut boundary，并重建显式 source-gap mapping/timeline。
6. Cut boundary 不允许使用 coarse `(mix_before + mix_after)/2` 当真值。a7 使用 harmonic Chroma/MFCC 在 confirmed discontinuity 小区间细步长定位；证据不足继续 BLOCK。
7. CUT_AWARE mapping 由多个连续 AFFINE/PIECEWISE_RATE segment + 显式 source gaps 组成。任何 segment 仍 blocked，整次 cut rebuild 失败。
8. Confirmed source gap 内的 canonical lyric 只能按可证明程度处理：
   - 普通 line-LRC 的**整个可推断行区间**都位于 gap：可省略整行；
   - line-LRC 行从 gap 内开始但可能延续到 cut 后，或行区间穿过 gap：继续 review，禁止猜 surviving characters；
   - Enhanced LRC/QRC 有完整 token timing：只保留完整幸存 token，生成 canonical fragment；
   - cut 穿过某个 timed token：继续 review。
9. `TrackAsset / TrackOccurrence / ResolvedAssetBinding` 确定后，下游不得重新猜 source、LRC 或 same-timestamp original。
10. Transition/overlap 继续使用 candidate-level review；confirmed overlap 必须经过 `v4_recompose_overlap.py`，两路歌词保持独立。
11. Review Decision 必须 task-scoped + exact base-run-scoped；禁止口头确认后直接改 run JSON。
12. TimeWarp discontinuity candidate 独立 `candidate_id/issue_id`；`confirmed_cut` 或 `rejected_requires_remap` 都不会直接解除 BLOCK。
13. Final renderer 只能消费 effective `ready_for_render` 的 production/review/overlap/cut-rebuild run；任何 canonical fragment issue 都阻止 cut-rebuilt run 发布。
14. 最终 SRT、audit CSV、QA JSON 必须逐 cue 绑定并经过 release-integrity manifest。
15. 通用代码不得硬编码具体歌曲、cue、时间点、错词或任务名称。
16. 所有 stage 必须绑定 task fingerprint、algorithm version、calibration profile、upstream IDs、materialized SHA-256。
17. 所有实质性更新必须按 `references/documentation-contract.md` 同步 owning docs；CI 不通过不得合并。

## 权威文档

- 运行：`references/v4-runtime-guide.md`
- 状态：`references/v4-status.md`
- 架构/算法：`references/v4-implementation.md`
- 关键变更：`references/v4-change-record.md`
- 架构复盘：`references/v4-architecture-review-2026-08-17.md`
- 文档契约：`references/documentation-contract.md`
- 数据/盲测：`references/dataset-protocol.md`

## 标准生产流程

### 1. Reconstruction

```powershell
python scripts/v4_run.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --out-dir "output/<任务>/v4" `
  --git-commit "<commit>"
```

`v4_run` 会把每个 source-position jump 独立 materialize 为 `timewarp_discontinuity` candidate，并在 occurrence summary 中记录 exact primary Coarse/Fine path + artifact provenance。

### 2. Review

```powershell
python scripts/v4_review.py template ...
python scripts/v4_review.py apply ...
```

Review Decision schema 当前为 `1.2`。

TimeWarp discontinuity actions：

```text
confirmed_cut
 → status=confirmed
 → confirmed_discontinuity snapshot
 → requires_timeline_rebuild=true
 → 仍 review_required

rejected_requires_remap
 → 不是物理 cut
 → 当前 mapping 仍不可发布
 → requires_timeline_rebuild=true
```

### 3. Confirmed overlap

沿用 a6：

```powershell
python scripts/v4_recompose_overlap.py ...
```

只有 exact confirmed regions 可允许跨 track SRT cue 时间重叠。

### 4. Confirmed cut rebuild

```powershell
python scripts/v4_rebuild_cut.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --run "output/<任务>/v4/reviewed_run.json" `
  --run-artifact "output/<任务>/v4/reviewed_run.artifact.json" `
  --track-assets "output/<任务>/v4/assets/track_assets.json" `
  --asset-artifact "output/<任务>/v4/assets/track_assets.artifact.json" `
  --out-dir "output/<任务>/v4/cuts" `
  --out "output/<任务>/v4/cut_rebuilt_run.json" `
  --artifact-out "output/<任务>/v4/cut_rebuilt_run.artifact.json" `
  --git-commit "<commit>"
```

每个 confirmed cut：

```text
exact primary Coarse/Fine path
 + exact confirmed discontinuity
 + local mix/source harmonic Chroma/MFCC
        ↓
50ms bootstrap candidate search
        ↓
validated local cut boundary
        ↓
continuous segment 1
explicit source gap
continuous segment 2 [ ... ]
        ↓
CUT_AWARE mapping
        ↓
cut-aware canonical timeline
```

产物：

```text
cut_timewarp_rebuild / cut_aware_timewarp
cut_timeline_rebuild / canonical_timeline
cut_rebuild          / v4_cut_rebuilt_run
```

只有所有 confirmed cuts 成功 materialize、其他 issue=0 且 canonical fragment issue=0 时，cut rebuilt run 才 `ready_for_render`。

### 5. Cut-aware canonical text

普通 line-LRC：

```text
entire inferred line interval inside source gap
 → whole line omitted

line starts inside gap but can extend beyond gap
or line interval intersects source gap
 → canonical_fragment review issue
 → 不输出猜测文本
```

Enhanced LRC/QRC：

```text
complete token inside retained segment → keep
complete token inside source gap       → omit
token itself crossed by cut            → canonical_fragment review issue
```

当一行只剩部分完整 token 时，输出文本是 canonical fragment，仍来自规范歌词，不使用 ASR 猜词。

### 6. Final Render / Release

`v4_render.py` 当前可消费：

```text
production_orchestration
review_resolution
overlap_recomposition
cut_rebuild
```

Cut run 额外要求：

```text
remaining_issue_count = 0
canonical_fragment_issue_count = 0
rebuilt_occurrence_count >= 1
cut mapping/timeline artifact IDs 均在 run lineage 中
```

`cut_timeline_rebuild` timeline 必须 `cut_aware=true` 且没有 projection issues。

```powershell
python scripts/v4_render.py ...
python scripts/v4_validate_release.py `
  ... `
  --algorithm-version "4.0.0a7" `
  --upstream-artifact "output/<任务>/v4/final/FINAL.render.artifact.json"
```

## Calibration / migration

a7 新增 `CutBoundaryConfig`，默认 profile version：

```text
production-bootstrap-2026-08-17-a7
```

Bootstrap cut locator：16kHz、0.8s 左右 context、50ms candidate step、Chroma/MFCC 双特征 evidence。具体阈值尚未真实校准。

由于完整 profile 内容改变：**a6/a5 artifacts 不允许与 a7 stage artifacts 混用**，升级后从 `v4_run` 重跑。

## 当前已知边界

- 普通 line-LRC partial-line cut 仍不能自动确定 surviving characters；
- timed token 本身被 cut 穿过仍需 review；
- 同一任务同时存在 confirmed overlap + confirmed cut 的 unified stage composition 尚未自动编排，必须保持 fail-closed；
- real private calibration / blind-test 尚未完成。

## 回归纪律

```powershell
python -m compileall -q lyric_aligner scripts
python scripts/validate_docs_contract.py
python -m unittest discover -s scripts -p "test_*.py"
python scripts/validate_skill.py .
python scripts/privacy_scan.py
python scripts/check_environment.py
git diff --check
```

CI 覆盖 Python 3.10 / 3.12 / 3.14，并单独检查 ASR 环境。

## 后续优先级

1. confirmed overlap + confirmed cut 的统一 stage composition；
2. real-task calibration / blind-test；
3. Editor Evidence + LanguageSpan 最终 cue fusion；
4. 根据真实误差决定 Forced Alignment / ASR v2 / vocal local alignment。
