# Lyric Aligner v4 生产运行手册

更新：2026-08-17  
适用版本：`4.0.0a6`

> 新真实任务采用 **v4 production-first**。不确定 mapping/cut/transition 必须 review/BLOCK，不静默回退 v3.9。a6 在 a5 replayable review 之上增加 candidate-level confirmed-overlap 双路 canonical timeline recomposition。

## 1. Task Manifest

```powershell
python scripts/init_task.py `
  --task "任务名" `
  --source-srt "private/任务名/input/source.srt" `
  --audio "private/任务名/input/mix.wav" `
  --song-list "private/任务名/input/songs.txt" `
  --lyrics-dir "private/任务名/input/lyrics" `
  --source-audio-dir "private/任务名/input/source-audio"
```

BPM 可选，只作为 soft prior。

## 2. Reconstruction：`v4_run`

```powershell
python scripts/v4_run.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --out-dir "output/<任务>/v4" `
  --git-commit "<commit>"
```

执行：

```text
Asset Resolution
 → Primary Coarse
 → AFFINE / PIECEWISE_RATE
 → Selective Fine
 → Primary Canonical Timeline
 → Shared-boundary LEFT/RIGHT Coarse
 → Transition Probe
 → ready_for_render | review_required
```

### a6 Transition candidate 粒度

Transition 不再只生成“整条 A→B 边界一个 review issue”。每个分离 overlap/ambiguity interval 都有：

```text
candidate_id
left_occurrence_id
right_occurrence_id
interval_start / interval_end
```

`candidate_id` 绑定 candidate type、左右 occurrence 和毫秒级 interval。同一边界上两个分离候选必须独立 review。

`v4_run` 的 transition summary 还记录 exact：

```text
left_coarse_path / artifact
right_coarse_path / artifact
transition_path / artifact
```

这些是 confirmed-overlap 重组的正式 provenance，不能重新搜索文件。

## 3. Review：`v4_review`

生成模板：

```powershell
python scripts/v4_review.py template `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --run "output/<任务>/v4/v4_run.json" `
  --run-artifact "output/<任务>/v4/v4_run.artifact.json" `
  --out "private/<任务>/qa/review_decisions.json"
```

应用：

```powershell
python scripts/v4_review.py apply `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --run "output/<任务>/v4/v4_run.json" `
  --run-artifact "output/<任务>/v4/v4_run.artifact.json" `
  --decisions "private/<任务>/qa/review_decisions.json" `
  --out "output/<任务>/v4/reviewed_run.json" `
  --artifact-out "output/<任务>/v4/reviewed_run.artifact.json"
```

Review Decision schema 当前为 `1.1`。Candidate-level transition issue identity 包含 `candidate_id`，因此：

- `resolved_clear` 只清当前 candidate；
- 同一 A→B 的其他 candidate 仍 active；
- `confirmed_overlap` 写入 exact `confirmed_interval`，并保持 `review_required + requires_recomposition=true`；
- TimeWarp 仍只有 `confirmed_requires_rebuild`，不能 `resolved_clear`。

## 4. Confirmed overlap：`v4_recompose_overlap`

只在 reviewed run 中存在正式 `confirmed_overlap` issue 时执行：

```powershell
python scripts/v4_recompose_overlap.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --run "output/<任务>/v4/reviewed_run.json" `
  --run-artifact "output/<任务>/v4/reviewed_run.artifact.json" `
  --track-assets "output/<任务>/v4/assets/track_assets.json" `
  --asset-artifact "output/<任务>/v4/assets/track_assets.artifact.json" `
  --out-dir "output/<任务>/v4/overlap" `
  --out "output/<任务>/v4/recomposed_run.json" `
  --artifact-out "output/<任务>/v4/recomposed_run.artifact.json" `
  --git-commit "<commit>"
```

### 4.1 证据验证

每个 confirmed region 必须同时满足：

1. 非空 review `issue_id`；
2. `candidate_id` 在原 Transition artifact 中唯一存在；
3. confirmed interval 与原 candidate 毫秒级一致；
4. occurrence pair 与 Transition evidence 一致；
5. Transition artifact 是 reviewed run upstream；
6. Transition artifact 明确 upstream 到 supplied TrackAsset artifact；
7. LEFT boundary coarse 与 LEFT exact occurrence/track/canonical-selection 一致；
8. RIGHT boundary coarse 与 RIGHT exact occurrence/track/canonical-selection 一致；
9. 两份 coarse artifact 都由同一个 supplied TrackAsset artifact 派生；
10. Transition artifact 明确 upstream 到这两份 boundary coarse artifact；
11. 左右 coarse artifact 必须是两个不同 artifact，不能交换/复用同一份。

### 4.2 Boundary mapping

对 confirmed interval 的左右 source 分别：

```text
Boundary Coarse
  ├─ clean → effective Coarse TimeWarp
  └─ ambiguous/complex → Selective Fine → effective Fine/Coarse TimeWarp
```

如果 Fine 后 effective TimeWarp 仍 blocked，**confirmed overlap 仍不能发布**。

### 4.3 Canonical 双路重投影

每一侧使用 exact TrackAsset canonical lyrics：

```text
canonical source timestamps
 + boundary-local effective TimeWarp
 + exact confirmed interval
        ↓
re-projected overlap lines
        ↓
strict clip to confirmed interval
```

然后与该 occurrence 的 primary canonical timeline 合并。只扩展到 confirmed region，不恢复未确认区间。

新 occurrence timeline artifact stage：

```text
overlap_timeline_recomposition
```

新 run artifact stage：

```text
overlap_recomposition
```

Processed confirmed-overlap issues 从 active issue set 移除；其他 issue 原样保留。只有 remaining issues=0 才变成 `ready_for_render`。

## 5. Final Render

`v4_render.py` 当前接受：

```text
production_orchestration / v4_production_run
review_resolution       / v4_reviewed_run
overlap_recomposition   / v4_recomposed_run
```

共同条件：

```text
status == ready_for_render
issues == []
legacy_fallback_used == false
```

Overlap run 还必须绑定 exact source review artifact，并带非空 `confirmed_overlap_regions`。

### Cross-track cue 语义

最终 SRT 保留左右两首的**独立 cue**。不把歌词拼成一行。

Composer 对所有实际相交 cue pair 做验证，不只检查排序后的相邻 cue：

```text
cross-track cue intersection
  → exact occurrence pair
  → intersection 必须完整落在某个 confirmed region 内
  → 是：允许时间重叠
  → 否：BLOCK
```

因此 long cue 与后续第二/第三条跨轨 cue 的越界交集也不会漏检。

## 6. Final outputs / Release

```powershell
python scripts/v4_render.py ...
python scripts/v4_validate_release.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --final-srt "output/<任务>/v4/final/FINAL.srt" `
  --report "output/<任务>/v4/final/FINAL.csv" `
  --qa-json "output/<任务>/v4/final/FINAL.qa.json" `
  --algorithm-version "4.0.0a6" `
  --upstream-artifact "output/<任务>/v4/final/FINAL.render.artifact.json" `
  --out-manifest "output/<任务>/v4/final/release.artifact.json"
```

QA/Final Render artifact 会记录：

```text
source_run_stage = production_orchestration | review_resolution | overlap_recomposition
confirmed_overlap_region_count
```

Release Guard 的 SRT/CSV/QA hash、profile/version、exact final_render upstream 规则不变。

## 7. Calibration / migration

a6 未修改 bootstrap calibration 数值，继续使用 `production-bootstrap-2026-08-17-a4` profile 内容。变化属于 algorithm/review/timeline contract，因此：

- a5 artifacts 不可与 a6 artifacts 混入同一 release；
- a6 任务应从 `v4_run` 重跑；
- 不要手工修改旧 artifact 的 algorithm_version/schema/candidate_id。

## 8. 当前仍 BLOCK 的问题

- confirmed TimeWarp / middle-cut：等待 mapping/timeline rebuild；
- overlap boundary mapping 在 Fine 后仍 blocked；
- overlap interval 不能对应唯一原 Transition candidate；
- 任一跨轨 cue overlap 越出 confirmed region；
- 其他 unresolved transition/timewarp issue。

## 9. 单 Stage CLI

`v4_resolve_assets.py`、`v4_coarse_align.py`、`v4_fine_align.py`、`v4_probe_transition.py`、`v4_profile.py` 用于诊断/calibration/artifact 重现。

`v4_review.py` 与 `v4_recompose_overlap.py` 属于正式生产 contract，不是人工绕过 QA 的工具。

## 10. 下一优先级

1. confirmed TimeWarp/middle-cut mapping + timeline rebuild；
2. real private calibration / blind-test；
3. Editor Evidence + LanguageSpan 最终 cue fusion；
4. 根据真实误差决定 Forced Alignment / ASR v2。

不能宣称 bootstrap profile 已最优，也不能宣称真实准确率已提升固定百分比。
