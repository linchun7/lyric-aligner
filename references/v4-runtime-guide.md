# Lyric Aligner v4 生产运行手册

更新：2026-08-17  
适用开发版本：`4.0.0a8`  
Calibration profile：`production-bootstrap-2026-08-17-a7`

> v4 继续 production-first。a8 不修改声学阈值，而是在 a6 confirmed-overlap 与 a7 confirmed-cut 两条已验证 materialization 之上增加 fail-closed composition。不能安全组合就 review/BLOCK，不人工拼 artifact。

## 1. Reconstruction

```powershell
python scripts/v4_run.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --out-dir "output/<任务>/v4" `
  --git-commit "<commit>"
```

主链：

```text
Asset Resolution
 → Primary Coarse
 → AFFINE / PIECEWISE_RATE
 → Selective Fine
 → Canonical Timeline
 → Transition Evidence
 → ready_for_render | review_required
```

Forward source discontinuity 与 transition overlap 都是 candidate-level review evidence。

## 2. Review

```powershell
python scripts/v4_review.py template ...
python scripts/v4_review.py apply ...
```

Review schema=`1.2`。

主要 actions：

```text
transition: resolved_clear | confirmed_overlap
timewarp_discontinuity: confirmed_cut | rejected_requires_remap
generic timewarp: confirmed_requires_rebuild
```

## 3. 单类 confirmed overlap

```powershell
python scripts/v4_recompose_overlap.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --run "output/<任务>/v4/reviewed_run.json" `
  --run-artifact "output/<任务>/v4/reviewed_run.artifact.json" `
  --track-assets "output/<任务>/v4/assets/track_assets.json" `
  --asset-artifact "output/<任务>/v4/assets/track_assets.artifact.json" `
  --out-dir "output/<任务>/v4/overlap" `
  --out "output/<任务>/v4/recomposed_run.json" `
  --artifact-out "output/<任务>/v4/recomposed_run.artifact.json"
```

只有 exact confirmed region 内允许两首歌各自独立 SRT cue overlap。

## 4. 单类 confirmed cut

```powershell
python scripts/v4_rebuild_cut.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --run "output/<任务>/v4/reviewed_run.json" `
  --run-artifact "output/<任务>/v4/reviewed_run.artifact.json" `
  --track-assets "output/<任务>/v4/assets/track_assets.json" `
  --asset-artifact "output/<任务>/v4/assets/track_assets.artifact.json" `
  --out-dir "output/<任务>/v4/cuts" `
  --out "output/<任务>/v4/cut_rebuilt_run.json" `
  --artifact-out "output/<任务>/v4/cut_rebuilt_run.artifact.json"
```

Confirmed cut 必须经过 local boundary locator、CUT_AWARE mapping 和 cut-aware canonical projection；不能靠人工清 block。

## 5. 同一 reviewed task 同时有 cut + overlap

**必须让两条 materializer 从同一个 `reviewed_run.json / reviewed_run.artifact.json` 独立执行。**

```text
review_resolution
 ├─ v4_rebuild_cut          → cut_rebuilt_run
 └─ v4_recompose_overlap    → recomposed_run
```

然后：

```powershell
python scripts/v4_compose_materializations.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --cut-run "output/<任务>/v4/cut_rebuilt_run.json" `
  --cut-artifact "output/<任务>/v4/cut_rebuilt_run.artifact.json" `
  --overlap-run "output/<任务>/v4/recomposed_run.json" `
  --overlap-artifact "output/<任务>/v4/recomposed_run.artifact.json" `
  --track-assets "output/<任务>/v4/assets/track_assets.json" `
  --asset-artifact "output/<任务>/v4/assets/track_assets.artifact.json" `
  --out-dir "output/<任务>/v4/combined" `
  --out "output/<任务>/v4/combined_run.json" `
  --artifact-out "output/<任务>/v4/combined_run.artifact.json" `
  --git-commit "<commit>"
```

### 5.1 Identity gate

Cut/overlap 两个 materialized run 必须：

- same task fingerprint；
- same algorithm version；
- same profile/version；
- same TrackAsset artifact；
- same `source_review_artifact_id`；
- review/asset 均是两边 upstream。

### 5.2 Issue gate

Composition 使用两边 `processed_issue_ids` 抵消已经由另一条 materializer 解决的问题。两边若声称处理同一个 issue_id，或同一 unresolved issue snapshot 不一致，直接 BLOCK。

只有 combined remaining issues=0 才 `ready_for_render`。

### 5.3 Same-occurrence cut + overlap

以 cut-aware timeline 为 base，只加入 overlap-only delta lines。

必须同时满足：

```text
confirmed overlap interval 不包含 localized cut_mix_time
AND
overlap delta canonical source interval 不与 confirmed source gap 相交
```

缺 canonical source provenance 也 BLOCK。

新 timeline：

```text
combined_timeline_recomposition / canonical_timeline
```

新 run：

```text
combined_recomposition / v4_combined_run
schema = 1.4
```

## 6. Final Render

`v4_render.py` 接受：

```text
production_orchestration
review_resolution
overlap_recomposition
cut_rebuild
combined_recomposition
```

共同条件：

```text
status == ready_for_render
issues == []
legacy_fallback_used == false
```

Combined run 还要求：

- combined remaining_issue_count=0；
- cut canonical_fragment_issue_count=0；
- source review/cut/overlap artifacts 全在 upstream；
- cut+overlap same occurrence 使用 `combined_timeline_recomposition`；
- combined timeline upstream 到 exact source cut/overlap timelines；
- cross-track cue pair 仍完整落在 exact confirmed overlap region。

## 7. Release

```powershell
python scripts/v4_render.py ...
python scripts/v4_validate_release.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --final-srt "output/<任务>/v4/final/FINAL.srt" `
  --report "output/<任务>/v4/final/FINAL.csv" `
  --qa-json "output/<任务>/v4/final/FINAL.qa.json" `
  --algorithm-version "4.0.0a8" `
  --upstream-artifact "output/<任务>/v4/final/FINAL.render.artifact.json" `
  --out-manifest "output/<任务>/v4/final/release.artifact.json"
```

QA 可能记录：

```text
source_run_stage = production_orchestration | review_resolution | overlap_recomposition | cut_rebuild | combined_recomposition
confirmed_overlap_region_count
rebuilt_cut_occurrence_count
combined_recomposition_occurrence_count
```

## 8. Calibration / migration

a8 没有阈值变更：

```text
profile_version = production-bootstrap-2026-08-17-a7
algorithm_version = 4.0.0a8
```

Algorithm contract 改变，所以旧 a7 artifact 不可改字段后继续使用；应按 a8 lineage 重跑/重新物化。

## 9. 当前 BLOCK

- overlap interval 穿 localized cut boundary；
- overlap canonical source interval 穿 confirmed source gap；
- overlap delta 缺 source provenance；
- cut/overlap 任一 source mapping 仍 blocked；
- line-LRC partial-line cut；
- timed canonical token 被 cut 穿过；
- 其他 unresolved review issue。

## 10. 下一优先级

1. real private calibration / blind-test；
2. Editor Evidence + LanguageSpan final cue fusion；
3. 由真实误差决定 Forced Alignment / ASR v2；
4. 只有真实任务证明必要时再研究 same-region cut+overlap joint acoustic composition。

不能在 real blind-test 前宣称固定百分比的准确率提升。
