---
name: lyric-aligner
description: Reconstruct, review, recompose and render multilingual canonical lyric subtitles for edited music mixes using fingerprinted TrackAssets, Source-to-Mix TimeWarp, transition evidence, replayable review decisions, confirmed-overlap dual-track timelines, strict final SRT/audit/QA binding, and immutable release lineage.
---

# Lyric Aligner

当前开发架构为 **v4.0.0a6 production-first**。新真实任务优先进入 v4；不可可靠解释的 mapping、cut、transition/overlap 必须 review/BLOCK，**不得静默回退 v3.9**。

## 不可违反的原则

1. **Canonical lyric 是最终文字与顺序真源。** ASR、剪映/编辑器只提供 evidence。
2. **Source-to-Mix audio mapping 是主要时间真源。** 编辑器 SRT 时间不是默认权威。
3. 普通歌曲先 AFFINE；声学证据证明固定倍率不足时才升级 PIECEWISE_RATE。BPM 只作 soft prior。
4. `rate change != cut`。只有 source-position discontinuity 才产生 cut candidate；Middle cut 永不自动 confirmed。
5. `TrackAsset / TrackOccurrence / ResolvedAssetBinding` 确定后，下游不得重新猜 source、LRC 或 same-timestamp original。
6. 相邻歌曲 nominal start 是 prior，不是硬声学边界。左右 source 必须可在共享 transition window 独立取证。
7. overlap candidate 不等于 overlap truth；同一 A→B 边界的每个候选区间必须有独立 `candidate_id/issue_id`，不得“一次确认整条边界”。
8. Review Decision 必须 task-scoped + exact base-run-scoped；禁止口头确认后直接修改 `v4_run.json`。
9. transition candidate `resolved_clear` 可解除该候选 review；`confirmed_overlap` 必须先经过 `v4_recompose_overlap.py`，不能只把 BLOCK 改成 false。
10. confirmed overlap 的左右 canonical stream 必须保持独立；**不得把两首歌词拼成一行**。
11. overlap recomposition 必须使用原 Transition artifact 的 LEFT/RIGHT boundary mapping；左右 coarse 必须与 exact occurrence/track/canonical-selection/TrackAsset artifact 一致，不能交换。
12. boundary mapping ambiguous/complex 时允许 Selective Fine；Fine 后仍 blocked 就继续 BLOCK，人工确认 overlap 不覆盖坏 mapping。
13. Final composer 只允许跨 track cue 在 exact confirmed-overlap region 内重叠；任何越界交集仍 BLOCK。
14. blocked TimeWarp/middle-cut 问题不能用 `resolved_clear` 强行放行，必须重建 mapping/timeline。
15. Final renderer 只能消费 effective `ready_for_render` 的 production/review/overlap-recomposition run。
16. 最终 SRT、audit CSV、QA JSON 必须逐 cue 绑定并经过 release-integrity manifest。
17. 通用代码不得硬编码具体歌曲、cue、时间点、错词或任务名称。
18. 所有 stage 必须绑定 task fingerprint、algorithm version、calibration profile、upstream IDs、materialized SHA-256。
19. 所有实质性更新必须按 `references/documentation-contract.md` 同步 owning docs；CI 不通过不得合并。

## 权威文档

- 运行：`references/v4-runtime-guide.md`
- 状态：`references/v4-status.md`
- 架构/算法：`references/v4-implementation.md`
- 关键变更：`references/v4-change-record.md`
- 架构复盘：`references/v4-architecture-review-2026-08-17.md`
- 文档契约：`references/documentation-contract.md`
- 数据/盲测：`references/dataset-protocol.md`

## 标准生产流程

### 1. 初始化 Task Manifest

```powershell
python scripts/init_task.py `
  --task "任务名" `
  --source-srt "private/任务名/input/source.srt" `
  --audio "private/任务名/input/mix.wav" `
  --song-list "private/任务名/input/songs.txt" `
  --lyrics-dir "private/任务名/input/lyrics" `
  --source-audio-dir "private/任务名/input/source-audio"
```

BPM 文件可选，不是正确 Source-to-Mix 的必要条件。

### 2. Reconstruction

```powershell
python scripts/v4_run.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --out-dir "output/<任务>/v4" `
  --git-commit "<commit>"
```

链路：

```text
Asset Resolution
 → Primary Coarse
 → AFFINE / PIECEWISE_RATE
 → Selective Fine
 → Canonical Timeline
 → Shared-boundary LEFT/RIGHT Coarse
 → Transition Probe
 → ready_for_render | review_required
```

Transition output 现在以**候选区间**为 review 粒度，每个 overlap/ambiguity interval 都有稳定 `candidate_id`。

### 3. Review

```powershell
python scripts/v4_review.py template `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --run "output/<任务>/v4/v4_run.json" `
  --run-artifact "output/<任务>/v4/v4_run.artifact.json" `
  --out "private/<任务>/qa/review_decisions.json"
```

人工填写每个 item 的 `decision={action,rationale}` 后：

```powershell
python scripts/v4_review.py apply `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --run "output/<任务>/v4/v4_run.json" `
  --run-artifact "output/<任务>/v4/v4_run.artifact.json" `
  --decisions "private/<任务>/qa/review_decisions.json" `
  --out "output/<任务>/v4/reviewed_run.json" `
  --artifact-out "output/<任务>/v4/reviewed_run.artifact.json"
```

当前语义：

```text
resolved_clear
 → 只清除该 candidate issue

confirmed_overlap
 → issue.status=confirmed
 → confirmed_interval=[start,end]
 → requires_recomposition=true
 → 仍 review_required

confirmed_requires_rebuild (TimeWarp)
 → requires_timeline_rebuild=true
 → 仍 review_required
```

### 4. Confirmed overlap 必须重组 timeline

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

该阶段对每个 confirmed candidate：

```text
exact Transition candidate
 + LEFT boundary Coarse/Fine
 + RIGHT boundary Coarse/Fine
 + Canonical lyrics
       ↓
confirmed interval 内重新 source→mix 投影
       ↓
LEFT overlap canonical timeline
RIGHT overlap canonical timeline
       ↓
与两侧 primary timelines 合并
```

输出的新 occurrence timeline stage：

```text
overlap_timeline_recomposition
```

新 run artifact stage：

```text
overlap_recomposition
```

只有 confirmed-overlap issues 全部成功 materialize，且没有其他 active issue 时，recomposed run 才可 `ready_for_render`。

### 5. Final Render

`v4_render.py` 可消费：

```text
production_orchestration
review_resolution
overlap_recomposition
```

但都必须 `status=ready_for_render`、`issues=[]`、`legacy_fallback_used=false`。

```powershell
python scripts/v4_render.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --run "output/<任务>/v4/recomposed_run.json" `
  --run-artifact "output/<任务>/v4/recomposed_run.artifact.json" `
  --track-assets "output/<任务>/v4/assets/track_assets.json" `
  --asset-artifact "output/<任务>/v4/assets/track_assets.artifact.json" `
  --final-srt "output/<任务>/v4/final/FINAL.srt" `
  --report "output/<任务>/v4/final/FINAL.csv" `
  --qa-json "output/<任务>/v4/final/FINAL.qa.json" `
  --artifact-out "output/<任务>/v4/final/FINAL.render.artifact.json"
```

Confirmed overlap 会以**两个独立、可时间重叠的 SRT cues**存在，不拼文本。Composer 会检查所有实际相交 cue pair；只有交集完整位于对应 confirmed region 内才允许。

### 6. Release Integrity

```powershell
python scripts/v4_validate_release.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --final-srt "output/<任务>/v4/final/FINAL.srt" `
  --report "output/<任务>/v4/final/FINAL.csv" `
  --qa-json "output/<任务>/v4/final/FINAL.qa.json" `
  --algorithm-version "4.0.0a6" `
  --upstream-artifact "output/<任务>/v4/final/FINAL.render.artifact.json" `
  --out-manifest "output/<任务>/v4/final/release.artifact.json"
```

只有 release guard 成功后，才视为当前 v4 可发布产物。

## Calibration

a6 不调整 bootstrap calibration 数值，继续使用 a4 profile 内容/version；变化属于 algorithm/review/timeline contract。由于 algorithm version 升级，新 a6 production chain 必须重跑，不能混用 a5 stage artifacts。

## 单 Stage CLI

`v4_resolve_assets.py`、`v4_coarse_align.py`、`v4_fine_align.py`、`v4_probe_transition.py`、`v4_profile.py` 主要用于诊断、calibration 和 artifact 重现。

`v4_review.py` 与 `v4_recompose_overlap.py` 是正式生产 contract，不是绕过 QA 的 override 工具。

## 回归纪律

每次实质性改动至少运行：

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

## 当前后续优先级

1. confirmed TimeWarp/middle-cut mapping + timeline rebuild；
2. real-task calibration / blind-test；
3. Editor Evidence + LanguageSpan 最终 cue fusion；
4. 根据真实误差决定 Forced Alignment / ASR v2 / vocal local alignment。
