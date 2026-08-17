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
- Coarse/Fine 只计算当前 occurrence/局部搜索区间，避免每首重复处理整条 40–60 分钟 mix。

## 2026-08-17 — Calibration / Documentation Contracts

- `V4CalibrationProfile` complete content 形成稳定 `profile_id`；
- TrackAsset artifact 嵌入 profile，下游通过 PipelineContext 恢复；
- 临时 CLI override 只能实验，release BLOCK；
- `documentation-contract.md + validate_docs_contract.py` 接入 CI；
- production/status/CLI/schema/architecture 变化必须同步 owning docs。

CI #241 曾因 CLI 已变但 runtime/workflow 文档未同步而正确 FAIL，证明文档门禁有效。

## 2026-08-17 — v4.0.0a3 Production-first（已合入 main）

合入方式：PR #2 squash merge，main commit `cfa43f4c854b699819cd3acb0cfea575cd1a04c8`。

### 策略

- 新真实任务优先 v4；
- unresolved mapping/cut/transition → `review_required`；
- 禁止静默 fallback v3.9；
- v3.9 只作为 Git 历史/比较/仓库级 rollback 点；
- 新算法不再继续堆入 legacy monolith。

### Timeline / Transition / Orchestration

新增：

- `timeline/projector.py`：canonical source timestamps → mix timeline；
- `pipeline/production.py`：primary interval 与 shared transition search interval 分离；
- `scripts/v4_run.py`：Asset → Coarse → Fine → Timeline → Transition 的生产入口。

相邻 A/B 在 `boundary ± profile.search_margin_seconds` 同一 mix 窗口独立取证；搜索窗口重叠不等于 overlap 已确认。

状态：

```text
ready_for_render
review_required
```

并固定：

```json
"legacy_fallback_used": false
```

### a3 最终验收

PR 已 retarget 到 main 后，head `940f0fa...` 的 CI #267 SUCCESS：Python 3.10/3.12/3.14 full tests、synthetic `v4_run` E2E、Documentation Contract、Skill、privacy、environment、diff-check、ASR environment 全部通过。

## 2026-08-17 — v4.0.0a4 Package-native Final Render（当前开发）

目标：让 review-free 任务不再需要 v3.9 `build/finalize/qa`，从 v4 canonical timeline 直接生成最终 SRT/audit/QA/release chain。

### 版本 / Profile

- package：`4.0.0a4`；
- profile：`production-bootstrap-2026-08-17-a4`；
- 新增 `RenderConfig`：

```text
minimum_cue_duration_ms = 250
maximum_line_duration_ms = 12000
open_line_duration_ms = 5000
word_timing_tail_ms = 120
```

这些是 bootstrap 参数，必须由真实 calibration 验证。

### Final Timeline Composer

新增：`lyric_aligner/timeline/composer.py`。

关键语义：

- 只消费 canonical projected timeline；
- 不从 Editor/ASR 生成最终歌词文本；
- cue 裁剪到 occurrence window；
- last/open line 使用有限显示时长；
- next-line gap 很长时使用 maximum line duration，避免上一句穿过长间奏常驻；
- Enhanced LRC/QRC word timing 仅增加短 profile tail；
- 过短 cue BLOCK，而不是擅自拉长；
- same-occurrence 异常重叠 BLOCK；
- 未确认 cross-track overlap BLOCK。

### Package-native Renderer

新增：`scripts/v4_render.py`。

只允许：

```text
run.status == ready_for_render
run.issues == []
legacy_fallback_used == false
```

并重新验证 task/version/profile、run artifact、TrackAsset artifact、timeline artifacts 和 upstream lineage。

输出：

```text
FINAL.srt
FINAL.csv
FINAL.qa.json
FINAL.render.artifact.json
```

Audit CSV 逐 cue 记录 canonical line/occurrence/track provenance、cue_id 和 text hash；QA 只有在完全 review-free 且 composer 无异常时才可 `publish_ready=true`。

现有 `v4_validate_release.py` 再验证 final SRT/audit/QA，成功生成 `release.artifact.json` 才构成当前正式发布完整性链。

### a3 → a4 迁移

profile complete content 已变化，因此：

> a3 TrackAsset/profile artifacts 不允许直接给 a4 renderer 静默复用。

必须从 Asset Resolution / `v4_run` 重跑 a4 chain。禁止手工修改旧 artifact 补 `render` 字段。

### 新测试

- `test_v4_timeline_composer.py`：open line、长间奏、word tail、window clipping、短 cue、未确认 cross-track overlap；
- `test_v4_render_end_to_end.py`：纯合成 WAV/LRC/Task Manifest，真实 subprocess 执行：

```text
v4_run → v4_render → v4_validate_release
```

并验证 final SRT/audit/QA/release lineage。

### a4 尚未完成

- Review Decision artifact；
- confirmed overlap 的双路 transition-aware timeline；
- confirmed/rejected middle cut 决策重放；
- Editor Evidence + LanguageSpan 最终 cue fusion；
- real private calibration / blind-test。

Forced Alignment / ASR v2 继续以后续真实误差数据决定优先级。

## 验证纪律

任何“测试通过/可合并”结论必须绑定具体 head/CI。旧 head 绿灯不能继承到新代码。当前 a4 分支在完整 Python 3.10/3.12/3.14 + ASR + docs/Skill/privacy/environment/diff-check 全绿前，不声明可合入 main。
