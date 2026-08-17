# Lyric Aligner v4 关键变更记录

> 所有实质性生产更新必须按 `documentation-contract.md` 在同一 PR 同步本文件。这里记录“改了什么、为什么、兼容/迁移、验证边界”，不把设计草案写成已实现事实。

## 2026-08-17 — Foundation：测量、多语言与发布可信性

关键能力：

- evaluator 从 bag-of-token 升级为 sequence WER、line exact P/R/F1、missing/extra/order、split/merge、onset/offset、cut、overlap IoU/track attribution；
- Editor Evidence 按语言分级，zh/en direct-text，ko/ja phonetic-hint，yue timing-hint，unknown/generic 不把编辑器文字当 canonical truth；
- strict SRT parser；
- FINAL SRT / audit / QA 严格绑定；
- ArtifactManifest 绑定 task fingerprint、algorithm version、config、upstream IDs、materialized SHA；
- release 拒绝跨 task/version/profile 或未固化 calibration override。

## 2026-08-17 — TrackAsset / Canonical Single Truth

关键代码：`assets/`、`text/canonical_lyrics.py`、`pipeline/context.py`。

- fail-closed TrackAsset/TrackOccurrence/ResolvedAssetBinding；
- artist/title + top1/top2 margin + 文件唯一占用，避免错绑 source/LRC；
- semantic identity 包含 source SHA、raw lyric SHA、same-timestamp canonical selection SHA；
- 普通 LRC、Enhanced LRC、QRC 共用同一 timestamp alternative identity；
- downstream 不得重新 fuzzy resolve source/LRC 或固定选 `alternatives[0]`；
- UTF-8 fail-closed，避免“成功解码乱码”。

## 2026-08-17 — Audio Mapping v2

关键代码：`audio/features.py`、`coarse_mapper.py`、`timewarp.py`、`fine_alignment.py`。

- HPSS harmonic 降低 click/percussive 干扰；
- Chroma CENS + MFCC 多特征检索；
- 每窗多候选、NMS、top1/top2 margin；
- 全局单调 source path，避免 repeated chorus 每窗独立跳 occurrence；
- AFFINE-first；BPM 只作 soft prior；
- residual/drift/feature support 证明 fixed-rate 不足时才升级 continuous PIECEWISE_RATE；
- local rate 可突然变化，`rate change != cut`；
- source-position jump 才产生 discontinuity/cut candidate；
- clean AFFINE 跳过 Fine，难例才局部高分辨率精修；
- Coarse/Fine 只计算当前 occurrence/局部搜索区间，避免每首重复处理整条长 mix。

## 2026-08-17 — Calibration / Documentation Contracts

- `V4CalibrationProfile` complete content 形成稳定 `profile_id`；
- TrackAsset artifact 嵌入 profile，下游通过 PipelineContext 恢复；
- 临时 CLI override 只能实验，release BLOCK；
- `documentation-contract.md + validate_docs_contract.py` 接入 CI；
- production/status/CLI/schema/architecture 变化必须同步 owning docs。

CI #241 曾因 CLI 已变但 runtime/workflow 文档未同步而正确 FAIL，证明文档门禁有效。

## 2026-08-17 — v4.0.0a3 Production-first（已合入 main）

合入方式：PR #2 squash merge，main commit：

```text
cfa43f4c854b699819cd3acb0cfea575cd1a04c8
```

- 新真实任务优先 v4；
- unresolved mapping/cut/transition → `review_required`；
- 禁止静默 fallback v3.9；
- 新增 `timeline/projector.py`、`pipeline/production.py`、`scripts/v4_run.py`；
- 形成 Asset → Coarse → Fine → Timeline → Transition 的 production-first reconstruction 链。

PR retarget 到 main 后，head `940f0fa...` 的 CI #267 SUCCESS：Python 3.10/3.12/3.14 full tests、synthetic `v4_run` E2E、Documentation Contract、Skill、privacy、environment、diff-check、ASR environment 全部通过。

## 2026-08-17 — v4.0.0a4 Package-native Final Render（已合入 main）

合入方式：PR #3 squash merge，main commit：

```text
236d9d717229147ee1d1a8755d712e54db47a751
```

### Final Timeline Composer

新增 `lyric_aligner/timeline/composer.py`：

- canonical projected timeline → final cue stream；
- occurrence-window clipping；
- bounded open/long-line duration；
- Enhanced/QRC word timing tail；
- short cue、same-occurrence anomaly、unconfirmed cross-track overlap 均 fail-closed。

### Package-native Renderer

新增 `scripts/v4_render.py`：

```text
Task Manifest
 → v4_run
 → ready_for_render
 → v4_render
 → FINAL.srt / FINAL.csv / FINAL.qa.json / FINAL.render.artifact.json
 → v4_validate_release
 → release.artifact.json
```

review-free 正常任务从此不再需要 v3.9 `build/finalize/qa`。

### Strict Release Binding

- v4 release 必须且只能有一个 `final_render` upstream；
- release algorithm version 必须等于 upstream algorithm version；
- QA profile id/version 必须与 upstream 一致；
- final-render artifact 记录的 SRT/CSV/QA size/SHA 必须逐一匹配当前实体文件；
- coordinated modification of all three final files 不能绕过旧 render artifact。

### a4 CI / merge

早期 Actions #283/#285/#303 首次尝试因账户 Billing/Actions spending limit 在 runner 分配前失败，`runner_id=0 / steps=[]`，不是代码失败。

账单恢复后对最新 head `a9ab9bc5...` 重新执行 #303；最终：

- Python 3.10 / 3.12 / 3.14 全绿；
- synthetic run→render→release E2E 全绿；
- Documentation Contract / Skill / privacy / environment / diff-check 全绿；
- ASR environment 全绿。

随后才 squash merge 到 main。

## 2026-08-17 — v4.0.0a5 Replayable Review Decision（当前开发）

目标：把 `review_required` 的人工判断从“口头结论/直接改 JSON”升级为 task-scoped、base-run-scoped、fingerprinted、可重放 artifact。

### New review layer

新增：

```text
lyric_aligner/review/
  decisions.py
scripts/v4_review.py
```

### Issue identity

Review template 对 `v4_run.issues[]` 生成 deterministic `issue_id`。

Transition identity：

```text
task fingerprint
kind=transition
code=transition_overlap_or_ambiguity
left_occurrence_id
right_occurrence_id
```

TimeWarp identity：

```text
task fingerprint
kind=timewarp
code=effective_mapping_blocked
occurrence_id
```

可读 reason 不参与 hash，避免文案变化破坏逻辑 identity；task fingerprint 参与 hash，防止跨任务复用。

### Base-run scope

Review template 还强制绑定：

```text
algorithm_version
base_run_artifact_id
```

即使新 run 产生相同 logical issue_id，旧 decision 文件也不能直接 replay 到另一个 production-run artifact。

### Review template

```powershell
python scripts/v4_review.py template ...
```

输出每个 issue 的：

```text
issue_id
issue snapshot
allowed_actions
decision=null
```

### Review apply

```powershell
python scripts/v4_review.py apply ...
```

重新验证：

- Task Manifest；
- production-run artifact task/version/stage/materialized hash；
- exact base-run artifact ID；
- issue snapshot；
- allowed action；
- non-empty rationale；
- decision file 必须包含全部 base issues。

生成：

```text
reviewed_run.json
reviewed_run.artifact.json
```

artifact stage=`review_resolution`，同时冻结 reviewed-run 与 decision JSON，并继承 base production run 的所有 upstream IDs。

### Safe action semantics

#### Transition `resolved_clear`

- 人工确认 overlap/ambiguity candidate 为误报；
- issue 从 active set 移除；
- 原 transition `blocked` evidence 保留；
- reviewed run 新增 `review_resolution`；
- 若无其他 issue，可 `ready_for_render`。

#### Transition `confirmed_overlap`

- issue.status=`confirmed`；
- `requires_recomposition=true`；
- 继续 `review_required`。

确认 overlap 不等于可以发布；必须等待 transition-aware 双路 timeline composition。

#### TimeWarp `confirmed_requires_rebuild`

- issue.status=`confirmed`；
- `requires_timeline_rebuild=true`；
- 继续 `review_required`。

blocked TimeWarp **没有** `resolved_clear` action，避免没有合法 timeline 时被人工布尔 override 放行。

### Renderer integration

`v4_render.py` 现在接受：

```text
production_orchestration / v4_production_run
review_resolution      / v4_reviewed_run
```

但 reviewed run 只有在：

```text
status == ready_for_render
issues == []
remaining_issue_count == 0
legacy_fallback_used == false
```

且 base-run identity + inherited TrackAsset/timeline lineage 全部一致时才允许 render。

QA/Final Render artifact 记录：

```text
source_run_stage = production_orchestration | review_resolution
```

### a5 calibration / migration

a5 不修改 calibration 数值，继续使用 a4 profile 内容/version；变化是 algorithm/review contract，所以 package version 升为 `4.0.0a5`。a4 stage artifacts 不得和 a5 artifacts 混入同一 release，a5 生产任务应重跑整条 stage chain。

### Regression coverage

新增/扩展：

- `test_v4_review_decisions.py`；
- `test_v4_review_cli.py`；
- `test_v4_render_end_to_end.py` 增加 synthetic `review_resolution → v4_render`；
- CLI bootstrap 加入 `v4_review.py`。

重点验证：

- issue ID 稳定性与 task 隔离；
- transition clear 可解除 review；
- confirmed overlap 仍需 recomposition；
- TimeWarp 不能 `resolved_clear`；
- decision 不可跨 base run；
- snapshot tamper BLOCK；
- review artifact 保留 transitive reconstruction lineage；
- reviewed-run renderer 与原 production renderer 生成同一 canonical SRT。

### Still next

- confirmed-overlap transition-aware 双路 timeline composition；
- confirmed TimeWarp/middle-cut mapping/timeline rebuild；
- real private calibration/blind-test；
- Editor Evidence + LanguageSpan final cue fusion；
- Forced Alignment / ASR v2 由真实误差决定优先级。

## 验证纪律

任何“测试通过/可合并”结论必须绑定具体 head/CI。旧 head 绿灯不能继承到新代码；runner 未启动不能称为代码失败或通过；review decision 也不能替代声学/时间轴重建本身。
