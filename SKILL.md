---
name: lyric-aligner
description: Reconstruct, review and render multilingual canonical lyric subtitles for edited music mixes using fingerprinted TrackAssets, Source-to-Mix TimeWarp, selective fine alignment, transition evidence, canonical timelines, replayable review decisions, strict final SRT/audit/QA binding, and immutable release lineage.
---

# Lyric Aligner

当前默认开发架构为 **v4.0.0a5 production-first**。新真实任务优先进入 v4；不可可靠解释的 mapping、cut、transition/overlap 进入 review/BLOCK，**不得静默回退 v3.9**。

## 不可违反的原则

1. **Canonical lyric 是最终文字与顺序真源。** ASR、剪映/编辑器只能提供 evidence。
2. **Source-to-Mix audio mapping 是主要时间真源。** 编辑器 SRT 时间不是默认权威。
3. 普通歌曲先 AFFINE；只有声学证据证明固定倍率不足才升级 PIECEWISE_RATE。
4. BPM 只允许作为 soft prior。
5. `rate change != cut`。只有 source-position discontinuity 才能产生 cut candidate。
6. Middle cut 永不自动 confirmed；未解决 cut/overlap/mapping conflict 必须 BLOCK。
7. `TrackAsset / TrackOccurrence / ResolvedAssetBinding` 确定后，下游不得重新猜 source、LRC 或 same-timestamp original。
8. 相邻歌曲 nominal start 是 prior，不是硬声学边界；transition 必须允许左右 source 在共享窗口取证。
9. overlap candidate 不等于 overlap truth；两路歌词不得未经确认合并成一行。
10. **Review Decision 必须 task-scoped + base-run-scoped。** 不允许口头确认后直接改 `v4_run.json` 或 artifact。
11. transition `resolved_clear` 可以解除 review block；`confirmed_overlap` 必须继续 BLOCK，直到生成 transition-aware 双路 timeline。
12. blocked TimeWarp/middle-cut 问题不能用 `resolved_clear` 强行放行，必须重建 mapping/timeline。
13. Final renderer 只能消费 `ready_for_render` 的 production run 或合法 `review_resolution` run。
14. 最终 SRT、audit CSV、QA JSON 必须逐 cue 严格绑定，并经过 release-integrity manifest。
15. 通用代码不得硬编码具体歌曲、cue、时间点、歌词错词或任务名称。
16. 所有 stage 必须绑定 task fingerprint、algorithm version、calibration profile、upstream artifact IDs、materialized SHA-256。
17. 所有实质性/关键更新必须按 `references/documentation-contract.md` 同步 owning docs；CI 不通过不得合并。

## 权威文档

- 运行：`references/v4-runtime-guide.md`
- 状态：`references/v4-status.md`
- 架构/算法：`references/v4-implementation.md`
- 关键变更：`references/v4-change-record.md`
- 架构复盘：`references/v4-architecture-review-2026-08-17.md`
- 文档契约：`references/documentation-contract.md`
- 数据/盲测：`references/dataset-protocol.md`
- LRC role override：`references/v4-lyric-role-overrides.md`

`references/workflow.md` / `references/change-record.md` 主要保存 schema 2.0 与历史迁移信息；v4 新任务以 runtime guide 为准。

## 标准生产流程

### 1. 初始化 task manifest

```powershell
python scripts/init_task.py `
  --task "任务名" `
  --source-srt "private/任务名/input/source.srt" `
  --audio "private/任务名/input/mix.wav" `
  --song-list "private/任务名/input/songs.txt" `
  --lyrics-dir "private/任务名/input/lyrics" `
  --source-audio-dir "private/任务名/input/source-audio"
```

`--bpm-changes` 可选。BPM 不是正确结果的必要条件。

### 2. 跑 v4 reconstruction

```powershell
python scripts/v4_run.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --out-dir "output/<任务>/v4" `
  --git-commit "<当前commit>"
```

可选任务配置：`--profile`、`--language-map`、`--middle-cut-map`、`--lyric-role-map`。

链路：

```text
Asset Resolution
 → Primary Coarse
 → AFFINE / PIECEWISE_RATE
 → Selective Fine
 → Canonical Timeline
 → Shared-boundary Transition Evidence
 → ready_for_render | review_required
```

`legacy_fallback_used` 必须为 `false`。

### 3. 如果是 `review_required`，先生成 Review Decision template

```powershell
python scripts/v4_review.py template `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --run "output/<任务>/v4/v4_run.json" `
  --run-artifact "output/<任务>/v4/v4_run.artifact.json" `
  --out "private/<任务>/qa/review_decisions.json"
```

模板中的每个 review item 包含：

- task-scoped deterministic `issue_id`；
- issue snapshot；
- 当前允许的 action；
- `decision=null` 待人工填写。

决策文件同时绑定：

```text
task_fingerprint_sha256
base_run_artifact_id
algorithm_version
```

因此旧任务/旧 run 的 review 不能自动套到新 production run。

### 4. 应用 Review Decision

编辑 `review_decisions.json` 后：

```powershell
python scripts/v4_review.py apply `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --run "output/<任务>/v4/v4_run.json" `
  --run-artifact "output/<任务>/v4/v4_run.artifact.json" `
  --decisions "private/<任务>/qa/review_decisions.json" `
  --out "output/<任务>/v4/reviewed_run.json" `
  --artifact-out "output/<任务>/v4/reviewed_run.artifact.json" `
  --git-commit "<当前commit>"
```

当前安全语义：

```text
transition + resolved_clear
  → issue resolved
  → 若没有其他 issue，可 ready_for_render

transition + confirmed_overlap
  → issue status=confirmed
  → requires_recomposition=true
  → 继续 review_required

timewarp + confirmed_requires_rebuild
  → requires_timeline_rebuild=true
  → 继续 review_required
```

**禁止：** 对 blocked TimeWarp 使用 `resolved_clear`，或对 confirmed overlap 只把 BLOCK 改成 false。

### 5. 只有 `ready_for_render` 才运行 renderer

`--run/--run-artifact` 可以指向：

- 原始 `v4_run.json + production_orchestration artifact`；
- 或合法 `reviewed_run.json + review_resolution artifact`。

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
  --git-commit "<当前commit>"
```

Renderer 从 canonical projected timelines 生成字幕，不从 Jianying/ASR 重写 canonical lyric。Review artifact 必须保留 base production run、TrackAsset、timeline 的完整 upstream lineage。

### 6. 最终 release integrity

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

只有 release guard 成功后，才把该 SRT 视为当前 v4 的可发布产物。

## Calibration

当前 a5 **继续使用 a4 的 RenderConfig/profile calibration 内容**；a5 的变化是 review contract，不是阈值校准。由于 algorithm version 已升级，生产 artifact chain 仍应按 a5 重跑，不得把 a4 stage artifact 混入 a5 release。

`RenderConfig`：

```text
minimum_cue_duration_ms
maximum_line_duration_ms
open_line_duration_ms
word_timing_tail_ms
```

这些是 bootstrap 参数，不是永久真理。真实任务只能通过新的 named profile 校准，不得隐式改代码默认值。

## 单 Stage / Review CLI 定位

`v4_resolve_assets.py`、`v4_coarse_align.py`、`v4_fine_align.py`、`v4_probe_transition.py`、`v4_profile.py` 主要用于诊断、calibration 和 artifact 重现。

`v4_review.py` 不是“人工跳过 QA”的工具，而是将人工结论变成 fingerprinted、base-run-scoped、可重放 artifact 的唯一正常入口。

## Calibration / 回归纪律

真实任务发现问题后：

1. 记录匿名 failure/regression case；
2. 区分通用算法、profile、语言策略或任务级明确事实；
3. 通用修复必须加测试；
4. profile 改动生成新 version/profile_id；
5. blind-test 不得参与调参；
6. 同步文档并通过 Documentation Contract。

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

1. confirmed-overlap transition-aware 双路 timeline composition；
2. confirmed middle-cut / mapping problem 的 timeline rebuild；
3. real-task calibration / blind-test；
4. Editor Evidence + LanguageSpan 进入最终 cue decision；
5. 根据真实误差再决定 Forced Alignment / ASR v2 / vocal local alignment。
