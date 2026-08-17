---
name: lyric-aligner
description: Reconstruct, review, materialize and render multilingual canonical lyric subtitles for edited music mixes using fingerprinted TrackAssets, Source-to-Mix TimeWarp, confirmed-overlap dual-track timelines, confirmed-cut CUT_AWARE timelines, fail-closed cut+overlap composition, and immutable release lineage.
---

# Lyric Aligner

当前开发版本为 **v4.0.0a8 production-first**。`main` 已合入 a7 confirmed-cut rebuild；a8 新增同一 reviewed task 的 cut/overlap materialization composition。任何无法由现有证据安全组合的情况继续 review/BLOCK，**不得静默回退 v3.9，也不得人工拼 artifact**。

## 不可违反的原则

1. **Canonical lyric 是最终文字与顺序真源。** ASR、编辑器只提供 evidence。
2. **Source-to-Mix audio mapping 是主要时间真源。** 编辑器 SRT 时间不是默认权威。
3. `rate change != cut`；forward source-position discontinuity 才能进入 candidate-level cut review。
4. `confirmed_cut` 只确认物理 cut，仍必须经过 local cut locator → CUT_AWARE mapping → cut-aware canonical timeline。
5. line-LRC 只有整个可推断行区间都位于 source gap 才可整行删除；partial-line 一律 review。
6. confirmed overlap 保持左右两条独立 canonical cue stream，所有跨轨实际交集必须完整位于 exact confirmed region。
7. **a8 不重新跑 cut/overlap 声学，也不互相改写 a6/a7 materializer。** 两边先从同一个 `review_resolution` 独立物化，再通过第三层 composition stage 合并。
8. Cut + overlap 只有在以下两层都安全时才自动组合：
   - overlap mix interval 不穿过该 occurrence 的 localized cut boundary；
   - overlap delta canonical source interval 不与任何 confirmed source gap 相交。
9. overlap delta 缺 canonical source provenance 时不得自动组合；open source interval 不能证明未穿 gap 时继续 BLOCK。
10. `TrackAsset / TrackOccurrence / ResolvedAssetBinding` 确定后，下游不得重新猜 source/LRC/canonical selection。
11. Review Decision 必须 task-scoped + exact base-run-scoped；cut/overlap materializations 必须绑定同一个 source review artifact。
12. Final renderer 只接受 `ready_for_render + issues=[] + legacy_fallback_used=false`，并验证 exact task/profile/artifact lineage。
13. 所有 stage 都绑定 task fingerprint、algorithm version、calibration profile、upstream IDs、materialized SHA-256。
14. 所有实质性更新必须同步 owning docs；CI 不通过不得合并。

## 权威文档

- 运行：`references/v4-runtime-guide.md`
- 状态：`references/v4-status.md`
- 架构：`references/v4-implementation.md`
- 变更：`references/v4-change-record.md`
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

输出 `ready_for_render` 或 candidate-level `review_required`。

### 2. Review

```powershell
python scripts/v4_review.py template ...
python scripts/v4_review.py apply ...
```

Review Decision schema=`1.2`。主要 actions：

```text
transition candidate: resolved_clear | confirmed_overlap
timewarp discontinuity: confirmed_cut | rejected_requires_remap
generic blocked timewarp: confirmed_requires_rebuild
```

### 3. 单类 materialization

Confirmed overlap：

```powershell
python scripts/v4_recompose_overlap.py ...
```

Confirmed cut：

```powershell
python scripts/v4_rebuild_cut.py ...
```

如果 reviewed task 只有其中一类 issue，单个 materializer 清空剩余 issue 后可直接进入 renderer。

### 4. 同一任务同时有 confirmed cut + confirmed overlap

两条 materializer **都从同一个 reviewed run 启动**：

```text
review_resolution
 ├─ v4_rebuild_cut       → cut_rebuild
 └─ v4_recompose_overlap → overlap_recomposition
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

新 artifacts：

```text
combined_timeline_recomposition / canonical_timeline
combined_recomposition          / v4_combined_run
```

组合 stage：

- 要求 cut/overlap 两个 run 来自同一个 review artifact；
- 要求同一个 TrackAsset artifact、task fingerprint、profile；
- 用两边 `processed_issue_ids` 抵消已经物化的问题，真正未解决 issue 继续保留；
- cut-only occurrence 保留 cut timeline；
- overlap-only occurrence 保留 overlap timeline；
- 同一 occurrence 同时受 cut+overlap 影响时生成 combined timeline；
- overlap interval 穿 cut boundary 或 overlap delta source interval 穿 source gap 时 BLOCK。

### 5. Final Render / Release

`v4_render.py` 当前接受：

```text
production_orchestration
review_resolution
overlap_recomposition
cut_rebuild
combined_recomposition
```

`combined_recomposition` 同时验证 cut metadata、overlap metadata、combined metadata，以及 source cut/overlap timeline lineage。

```powershell
python scripts/v4_render.py ...
python scripts/v4_validate_release.py `
  ... `
  --algorithm-version "4.0.0a8" `
  --upstream-artifact "output/<任务>/v4/final/FINAL.render.artifact.json"
```

## Calibration / migration

a8 **不修改声学阈值**，继续使用：

```text
profile = production-bootstrap-2026-08-17-a7
```

但 algorithm version 已变为 `4.0.0a8`，因此 a7 artifacts 不能直接冒充 a8 artifact；需要按 a8 chain 重跑/重新物化。

## 当前仍 BLOCK 的边界

- overlap interval 与 localized cut boundary 相交；
- overlap delta canonical source interval 与 confirmed source gap 相交；
- overlap delta 缺 source provenance；
- line-LRC partial-line cut；
- timed token 本身被 cut 穿过；
- 任一 cut/overlap mapping 或 lineage 仍不确定；
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

## 后续优先级

1. real-task calibration / blind-test；
2. Editor Evidence + LanguageSpan final cue fusion；
3. 根据真实误差决定 Forced Alignment / ASR v2 / vocal local alignment；
4. 如真实任务证明有必要，再研究 cut boundary 与 overlap 同一区域的 joint acoustic composition。
