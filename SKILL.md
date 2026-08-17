---
name: lyric-aligner
description: Reconstruct and render multilingual canonical lyric subtitles for edited music mixes using fingerprinted TrackAssets, Source-to-Mix TimeWarp, selective fine alignment, transition evidence, canonical timelines, strict final SRT/audit/QA binding, and immutable release lineage.
---

# Lyric Aligner

当前默认架构为 **v4.0.0a4 production-first**。新真实任务优先进入 v4；不可可靠解释的 mapping、cut、transition/overlap 进入 review/BLOCK，**不得静默回退 v3.9**。

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
10. Final renderer 只能消费 `ready_for_render` 的 v4 run；`review_required` 不得生成可发布结果。
11. 最终 SRT、audit CSV、QA JSON 必须逐 cue 严格绑定，并经过 release-integrity manifest。
12. 通用代码不得硬编码具体歌曲、cue、时间点、歌词错词或任务名称。
13. 所有 stage 必须绑定 task fingerprint、algorithm version、calibration profile、upstream artifact IDs、materialized SHA-256。
14. 所有实质性/关键更新必须按 `references/documentation-contract.md` 同步 owning docs；CI 不通过不得合并。

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

### 3. 只有 `ready_for_render` 才运行 a4 renderer

```powershell
python scripts/v4_render.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --run "output/<任务>/v4/v4_run.json" `
  --run-artifact "output/<任务>/v4/v4_run.artifact.json" `
  --track-assets "output/<任务>/v4/assets/track_assets.json" `
  --asset-artifact "output/<任务>/v4/assets/track_assets.artifact.json" `
  --final-srt "output/<任务>/v4/final/FINAL.srt" `
  --report "output/<任务>/v4/final/FINAL.csv" `
  --qa-json "output/<任务>/v4/final/FINAL.qa.json" `
  --artifact-out "output/<任务>/v4/final/FINAL.render.artifact.json" `
  --git-commit "<当前commit>"
```

Renderer 从 canonical projected timelines 生成字幕，不从 Jianying/ASR 重新生成文字。它会：

- 裁剪到 occurrence 有效窗口；
- 对最后一行使用 profile 控制的有限持续时间；
- 限制超长 line-LRC 显示时间，避免歌词穿过长间奏常驻；
- 对 Enhanced LRC/QRC word timing 只增加 profile 控制的小尾部；
- 拒绝过短 cue；
- 拒绝未确认的跨曲 cue overlap；
- 同时生成 SRT、audit CSV、QA JSON 与 `final_render` artifact。

### 4. 最终 release integrity

```powershell
python scripts/v4_validate_release.py `
  --task-manifest "private/<任务>/qa/task_manifest.json" `
  --final-srt "output/<任务>/v4/final/FINAL.srt" `
  --report "output/<任务>/v4/final/FINAL.csv" `
  --qa-json "output/<任务>/v4/final/FINAL.qa.json" `
  --algorithm-version "4.0.0a4" `
  --upstream-artifact "output/<任务>/v4/final/FINAL.render.artifact.json" `
  --out-manifest "output/<任务>/v4/final/release.artifact.json"
```

只有 release guard 成功后，才把该 SRT 视为当前 v4 的可发布产物。

## a4 Render Calibration

`V4CalibrationProfile` 新增 `render`：

```text
minimum_cue_duration_ms
maximum_line_duration_ms
open_line_duration_ms
word_timing_tail_ms
```

这些是 bootstrap 参数，不是永久真理。真实任务只能通过新的 named profile 校准，不得隐式改代码默认值。

**a3 profile / TrackAsset artifacts 不能直接被 a4 renderer 静默复用。** a4 应重新运行 Asset Resolution / `v4_run`，得到包含完整 `render` profile 的新 artifact chain。

## review_required

当前 a4 renderer 仍不允许未解决 review issue 进入 final render。尤其：

- source discontinuity / middle-cut candidate；
- repeated occurrence ambiguity；
- transition overlap candidate / uncertain interval；
- blocked TimeWarp。

下一阶段将提供 task-scoped、fingerprinted、可重放 Review Decision artifact。确认 overlap 后还需要 transition-aware timeline composition，不能仅用一个布尔值绕过检查。

## 单 Stage CLI

`v4_resolve_assets.py`、`v4_coarse_align.py`、`v4_fine_align.py`、`v4_probe_transition.py`、`v4_profile.py` 主要用于诊断、calibration 和 artifact 重现。普通任务优先按 `v4_run → v4_render → v4_validate_release` 完整链执行。

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

1. Replayable Review Decision artifact（cut / overlap / boundary）；
2. confirmed-overlap transition timeline composition；
3. real-task calibration / blind-test；
4. Editor Evidence + LanguageSpan 进入最终 cue decision；
5. 根据真实误差再决定 Forced Alignment / ASR v2 / vocal local alignment。
