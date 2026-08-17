# Lyric Aligner v4 关键变更记录

> 本文件记录 v4 已实际进入代码的关键行为、兼容/迁移语义与验证边界。所有实质性生产更新必须按 `documentation-contract.md` 在同一 PR 同步本文件。

## 2026-08-17 — Foundation：Evaluator / Editor Evidence

关键代码：

- `scripts/evaluate_dataset.py`
- `scripts/editor_evidence.py`
- `scripts/language_profiles.py`
- `lyric_aligner/text/language_spans.py`

变更：

- 修复只按 bag-of-token 统计导致错序仍高分的问题；
- 增加 sequence WER、line exact precision/recall/F1、missing/extra/wrong-order、split/merge；
- onset / offset 独立统计 MAE/P50/P90/P95；
- cut 支持实际时间容差匹配；
- overlap 增加 duration precision/recall/IoU 与 track attribution；
- Editor Evidence 按语言分级：zh/en direct-text，ko/ja phonetic-hint，yue timing-hint，unknown/generic 不把编辑器文字当 canonical truth；
- mixed-language 增加 span 级策略，避免一行内英文把整行韩/日/粤权重抬高。

## 2026-08-17 — Milestone 0：Release / Artifact Integrity

关键代码：

- `lyric_aligner/srt.py`
- `lyric_aligner/contracts/artifacts.py`
- `lyric_aligner/qa/final_integrity.py`
- `scripts/v4_validate_release.py`

变更：

- SRT 非空坏 block fail-closed；
- overlay/乱序输入 timeline end 使用 `max(cue.end_ms)`；
- FINAL SRT / audit CSV / QA 的 cue 数、顺序、时间、正文严格绑定；
- ArtifactManifest 记录 task fingerprint、algorithm version、normalized config、upstream artifact IDs、output size/SHA-256；
- 下游重新验证 materialized output hash，防止 manifest 生成后文件被改；
- release 拒绝跨 task、跨 v4 algorithm version、跨 calibration profile 拼装；
- 含未固化 CLI calibration override 的 artifact 不能发布。

## 2026-08-17 — Milestone 1：TrackAsset / Canonical Lyric Single Truth

关键代码：

- `lyric_aligner/domain.py`
- `lyric_aligner/assets/resolver.py`
- `lyric_aligner/assets/bindings.py`
- `lyric_aligner/assets/lyric_roles.py`
- `lyric_aligner/text/canonical_lyrics.py`
- `lyric_aligner/text/normalization.py`
- `lyric_aligner/io/text.py`

变更：

- 建立 `TrackAsset + TrackOccurrence + ResolvedAssetBinding`；
- source/LRC 匹配由 fail-open 改为 minimum score + top1/top2 margin + artist/title identity + 文件唯一占用；
- 同一歌曲重复 occurrence 可共享 TrackAsset，不同资产不得静默共用 generic 文件；
- TrackAsset schema 升为 `1.1`；
- semantic identity 包含 source SHA、raw lyric SHA、canonical same-timestamp selection SHA；
- 改变 `lyric-role-map` 原文选择会改变 `track_id/version_id`；
- 普通 LRC、Enhanced LRC、QRC 进入同一个 timestamp alternative identity space；
- downstream 不再应该重新猜 source/LRC 或固定取 `alternatives[0]`；
- metadata 不能被 override 成 canonical original；
- v4 文本路径默认 UTF-8 fail-closed，避免盲试编码“成功解出乱码”。

## 2026-08-17 — Milestone 2：Audio Mapping v2

关键代码：

- `lyric_aligner/audio/features.py`
- `lyric_aligner/audio/coarse_mapper.py`
- `lyric_aligner/audio/timewarp.py`
- `lyric_aligner/audio/fine_alignment.py`

变更：

### Coarse Retrieval

- mix/source 使用 HPSS harmonic 以降低强 click/percussive 干扰；
- 主检索证据为 Chroma CENS + MFCC；
- 每窗保留多个 source/slope 候选；
- top1/top2 margin + NMS 保留重复副歌歧义；
- 多窗口候选走全局单调 source path，而不是每窗独立 top1；
- BPM 只对 slope search 提供 soft prior，不删除全局候选。

### AFFINE-first TimeWarp

模型优先：

```text
source_time = intercept + slope * mix_time
```

只有 affine residual/drift/coverage 明显不足，并且更复杂模型在复杂度惩罚后显著改善、同时得到足够独立 feature family 支持，才接受连续：

```text
PIECEWISE_RATE
```

其 canonical serialized state 为：

```text
intercept
base_slope
breakpoints[]
slope_deltas[]
```

local rate 可突然改变；`1.08 → 1.17 → 1.43` 不等于 cut。

只有 source-position jump 超出连续倍率 envelope 才产生 discontinuity candidate；middle cut 永不自动 confirmed。

### Selective Fine Alignment

- clean/high-confidence AFFINE 默认跳过；
- ambiguous / blocked / complex 才进入高分辨率局部搜索；
- Fine 只计算 coarse windows 覆盖的 mix 局部区间，不对整条 40–60 分钟 mix 重算高分辨率特征。

### 性能

Coarse 同样只计算当前 occurrence/transition 搜索区间，避免每首歌重复对整条 mix 做 HPSS/Chroma/MFCC。

## 2026-08-17 — Milestone 3：Calibration / Pipeline Contracts

关键代码：

- `lyric_aligner/config.py`
- `lyric_aligner/pipeline/context.py`
- `scripts/v4_profile.py`

变更：

- 建立完整 `V4CalibrationProfile`；
- profile complete content 形成稳定 `profile_id`；
- TrackAsset artifact 嵌入完整 profile，后续 stage 从 `PipelineContext` 自动恢复；
- threshold 变化必须体现在 profile identity，不能只改函数默认值；
- 临时 CLI override 被标记为实验参数并阻断 release。

`4.0.0a3` 当前 profile version：

```text
production-bootstrap-2026-08-17-a3
```

Transition profile 包含：

- min score / margin；
- min overlap duration；
- search margin；
- minimum feature agreement；
- merge gap。

`scripts/v4_probe_transition.py` 已把这些字段真正传入算法和 artifact，避免“profile 写了但运行没生效”。

## 2026-08-17 — Milestone 4：Documentation Contract

关键代码/文档：

- `references/documentation-contract.md`
- `scripts/validate_docs_contract.py`
- `scripts/test_docs_contract.py`
- `.github/workflows/validate.yml`

变更：

- 所有实质性生产语义变化必须同步 `v4-change-record.md`；
- production/status 变化必须同步 `v4-status.md`；
- CLI/workflow 变化必须同步 runtime/SKILL/workflow；
- schema/contract 和架构职责变化必须同步 owning implementation/architecture docs；
- 契约以 PR base..head 完整 diff 执行，不允许靠最后一个 commit 或无关 Markdown 绕过。

CI #241 曾在 compileall/ASR 环境已通过的情况下，因为 CLI 更新没有同步 runtime/workflow 文档而正确 FAIL；该失败证明门禁生效，不应降低规则来“做绿 CI”。

## 2026-08-17 — Milestone 5：v4.0.0a3 Production-first

这是本轮策略变化最大的版本。

### 生产策略

- 新真实任务优先 v4；
- unresolved mapping/cut/transition 进入 `review_required`；
- **禁止静默 fallback v3.9**；
- v3.9 只作为 Git 历史/比较/仓库级 rollback 点，不再维护为第二套运行时生产算法；
- 新算法禁止继续堆进 `redo_karaoke_pipeline.py`。

### Timeline 真源

新增：

- `lyric_aligner/timeline/projector.py`

能力：

- 对 AFFINE / continuous PIECEWISE_RATE TimeWarp 做 source→mix 反演；
- 将 CanonicalLine/CanonicalToken 投影到 global mix timestamp；
- applied Fine 优先，否则用 Coarse；
- blocked TimeWarp 不能进入正常 timeline render 流程。

### Production Plan

新增：

- `lyric_aligner/pipeline/production.py`

严格区分：

1. primary occurrence interval；
2. shared transition evidence interval。

primary interval 用 nominal start 划分主单曲 timeline；相邻边界额外让左右 source 都搜索同一 `boundary ± profile.search_margin_seconds` 区间。

共享窗口只表示“允许两首都取证”，不表示已确认 overlap。

### 一键生产入口

新增：

- `scripts/v4_run.py`

当前执行：

```text
Task Manifest
 → Asset Resolution
 → Primary Coarse per occurrence
 → Selective Fine
 → Effective TimeWarp
 → Canonical Timeline Projection
 → Shared-boundary LEFT/RIGHT Coarse
 → Transition Probe
 → v4_run.json / production_orchestration artifact
```

状态：

- `ready_for_render`
- `review_required`

并明确：

```json
"legacy_fallback_used": false
```

### 当前未完成边界

`ready_for_render` **不是** `publish_ready`。a3 仍需实现：

- package-native final timeline composer / SRT renderer；
- review decision artifact；
- Editor Evidence / LanguageSpan 最终 cue fusion；
- final render → release guard 原生接线；
- real private calibration/blind-test。

Forced Alignment / ASR v2 继续后置，由真实任务数据决定投入优先级。

## 验证规则

任何测试数量、CI 绿灯都必须绑定具体 commit/head。旧 head 的成功不能继承到新代码。

当前 a3 合入 `main` 前必须满足：

- Documentation Contract PASS；
- Python 3.10 / 3.12 / 3.14 全量 unittest PASS；
- ASR environment PASS；
- skill validation / privacy scan / environment / `git diff --check` PASS；
- `v4_run.py --help` bootstrap PASS；
- production plan / timeline / transition profile tests PASS。

在这些条件完成前，本文件不声明当前 head 已可合并。
