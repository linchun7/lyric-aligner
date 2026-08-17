# Lyric Aligner v4 生产运行手册

更新：2026-08-17  
适用版本：`4.0.0a5`

> 新真实任务采用 **v4 production-first**。不确定 mapping/cut/transition 必须 review/BLOCK，不静默回退 v3.9。a5 在 a4 原生 renderer/release 之上增加 task-scoped、base-run-scoped、可重放 Review Decision。

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

BPM 文件可选；BPM 不是 Source-to-Mix 正确映射的必要输入。

## 2. Reconstruction：`v4_run`

```powershell
python scripts/v4_run.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --out-dir "output/<任务>/v4" `
  --git-commit "<commit>"
```

可选：`--profile`、`--language-map`、`--middle-cut-map`、`--lyric-role-map`。

执行链：

```text
Asset Resolution
 → Primary Coarse
 → AFFINE first / PIECEWISE_RATE fallback
 → Selective Fine
 → Canonical Timeline Projection
 → Shared-boundary LEFT/RIGHT evidence
 → Transition Probe
 → ready_for_render | review_required
```

`legacy_fallback_used` 必须为 `false`。

Primary interval 使用 nominal start 分割单曲主 timeline；但 nominal start 不是真实声学硬边界。相邻歌曲另外在 profile 控制的共享 transition window 独立取证。

## 3. `ready_for_render`：直接进入 renderer

没有 review issue 时，直接使用原始：

```text
v4_run.json
v4_run.artifact.json
```

进入第 6 节的 `v4_render`。

## 4. `review_required`：生成 Review Decision template

```powershell
python scripts/v4_review.py template `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --run "output/<任务>/v4/v4_run.json" `
  --run-artifact "output/<任务>/v4/v4_run.artifact.json" `
  --out "private/<任务>/qa/review_decisions.json"
```

模板会把当前 `v4_run.issues[]` 规范化为 task-scoped deterministic `issue_id`。`issue_id` 不依赖可读 reason 文案，因此同一逻辑 issue 的描述文字调整不会改变 identity；但它包含 task fingerprint，所以不同任务不会共享 issue ID。

决策文件同时绑定：

```text
schema_version
task_fingerprint_sha256
algorithm_version
base_run_artifact_id
review_items[]
```

每个 `review_item` 包含：

```text
issue_id
issue snapshot
allowed_actions
decision = null | {action, rationale}
```

**base_run_artifact_id 是强绑定。** 即使下次同一个任务、同一个边界又生成相同逻辑 issue，旧 decision 文件也不能自动套到一个新的 production run artifact。

## 5. 应用 Review Decision：`v4_review apply`

人工填写 `decision.action` 与非空 `rationale` 后：

```powershell
python scripts/v4_review.py apply `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --run "output/<任务>/v4/v4_run.json" `
  --run-artifact "output/<任务>/v4/v4_run.artifact.json" `
  --decisions "private/<任务>/qa/review_decisions.json" `
  --out "output/<任务>/v4/reviewed_run.json" `
  --artifact-out "output/<任务>/v4/reviewed_run.artifact.json" `
  --git-commit "<commit>"
```

当前 a5 支持的安全语义：

### Transition：`resolved_clear`

适用于人工确认 transition candidate 是误报/没有实际 overlap。

```text
issue resolved
→ effective_blocked=false
→ 如果没有其他 active issue，reviewed_run.status=ready_for_render
```

这里不会改写原 transition evidence；reviewed run 只新增 `review_resolution`，保留原 `blocked` 事实与人工覆盖原因。

### Transition：`confirmed_overlap`

```text
issue.status=confirmed
requires_recomposition=true
reviewed_run.status 仍为 review_required
```

**不能**因为“人工已经确认 overlap”就直接放行 renderer。下一阶段必须生成 transition-aware 双路 canonical timeline。

### TimeWarp：`confirmed_requires_rebuild`

```text
issue.status=confirmed
requires_timeline_rebuild=true
reviewed_run.status 仍为 review_required
```

blocked TimeWarp / middle-cut 问题**没有** `resolved_clear` action。因为这类 run 在 mapping blocked 时可能根本没有可用 canonical timeline，不能靠人工一句“没问题”绕过重建。

### Review artifact lineage

`reviewed_run.artifact.json`：

- stage=`review_resolution`；
- outputs 同时绑定 `v4_reviewed_run` 与原 decision JSON；
- upstream 包含 base production run artifact；
- 同时继承 base run 的 TrackAsset/coarse/fine/timeline/transition upstream artifact IDs；
- normalized config 记录 decision schema、base run artifact ID、decision source SHA-256、profile identity。

因此 Review Decision 不会切断 reconstruction lineage。

## 6. Final Render：只接受 effective `ready_for_render`

`v4_render.py` 现在允许两种 run artifact：

```text
production_orchestration / v4_production_run
review_resolution      / v4_reviewed_run
```

但两者都必须满足：

```text
status == ready_for_render
issues == []
legacy_fallback_used == false
```

Reviewed run 还必须满足：

- `review_resolution.base_run_artifact_id` 存在；
- base run artifact ID 位于 review artifact upstream；
- review artifact normalized config 的 base-run identity 与 reviewed payload 一致；
- `remaining_issue_count == 0`；
- TrackAsset 与每个 canonical timeline artifact 仍直接位于 review artifact upstream lineage。

命令：

```powershell
python scripts/v4_render.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --run "output/<任务>/v4/reviewed_run.json" `
  --run-artifact "output/<任务>/v4/reviewed_run.artifact.json" `
  --track-assets "output/<任务>/v4/assets/track_assets.json" `
  --asset-artifact "output/<任务>/v4/assets/track_assets.artifact.json" `
  --final-srt "output/<任务>/v4/final/FINAL.srt" `
  --report "output/<任务>/v4/final/FINAL.csv" `
  --qa-json "output/<任务>/v4/final/FINAL.qa.json" `
  --artifact-out "output/<任务>/v4/final/FINAL.render.artifact.json" `
  --git-commit "<commit>"
```

没有 review 时，上面的 `--run/--run-artifact` 换回 `v4_run.json/v4_run.artifact.json` 即可。

QA 会记录：

```text
source_run_stage = production_orchestration | review_resolution
```

## 7. Final Timeline Composer

最终文字只来自 canonical projected timeline，不从 Jianying/ASR 重新生成歌词。

当前 bootstrap `render` profile：

```text
minimum_cue_duration_ms = 250
maximum_line_duration_ms = 12000
open_line_duration_ms = 5000
word_timing_tail_ms = 120
```

语义：

- cue 裁剪到 occurrence 有效窗口；
- 超长 next-line gap 不让上一句穿过整段间奏；
- 最后一行使用有限 open-line duration；
- Enhanced LRC/QRC 词级 end 只增加短 tail；
- 过短 cue BLOCK；
- 未经 transition-aware recomposition 的跨曲 overlap BLOCK。

这些值属于 calibration profile，不是永久真理。

## 8. Release Integrity

```powershell
python scripts/v4_validate_release.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --final-srt "output/<任务>/v4/final/FINAL.srt" `
  --report "output/<任务>/v4/final/FINAL.csv" `
  --qa-json "output/<任务>/v4/final/FINAL.qa.json" `
  --algorithm-version "4.0.0a5" `
  --upstream-artifact "output/<任务>/v4/final/FINAL.render.artifact.json" `
  --out-manifest "output/<任务>/v4/final/release.artifact.json"
```

Release Guard 要求：

1. 恰好一个 `final_render` upstream；
2. requested algorithm version = upstream version；
3. QA profile id/version = upstream profile identity；
4. final-render artifact 记录的 SRT/CSV/QA size + SHA-256 必须匹配当前实体文件；
5. SRT/audit 逐 cue 严格绑定；
6. QA `passed/structurally_valid/fully_reviewed/publish_ready=true` 且 review count=0。

## 9. a4 → a5 迁移

a5 **没有修改 calibration 数值**；继续使用 a4 的 profile 内容/version。变化是 algorithm/review contract，因此：

- a4 algorithm artifacts 不能与 a5 artifacts 混入同一 release；
- 新 a5 任务应从 `v4_run` 重跑，得到一致的 a5 algorithm_version；
- 不要手工改旧 artifact 的 algorithm_version。

## 10. 单 Stage CLI

用于诊断/calibration/artifact 重现：

- `v4_resolve_assets.py`
- `v4_coarse_align.py`
- `v4_fine_align.py`
- `v4_probe_transition.py`
- `v4_profile.py`

`v4_review.py` 是正式 review contract，不是绕过 QA 的 override 工具。

## 11. 当前下一步

1. confirmed-overlap transition-aware 双路 timeline composition；
2. confirmed TimeWarp/middle-cut 的 mapping/timeline rebuild；
3. 真实私有任务 calibration / blind-test；
4. Editor Evidence + LanguageSpan 最终 cue fusion；
5. 根据真实误差决定 Forced Alignment / ASR v2。

不能宣称当前 bootstrap profile 已最优，也不能宣称真实准确率已提高固定百分比。真实任务数据必须通过 evaluator/calibration 得出结论。
