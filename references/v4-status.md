# Lyric Aligner v4 当前实施状态

更新日期：2026-08-17  
当前分支：`agent/v4-integration-hardening`  
PR：#2，当前直接目标 `main`  
v4 package：`4.0.0a3`  
TrackAsset schema：`1.1`

> 本文件只描述当前真实实现状态。设计与实现细节见 `v4-implementation.md`，真实运行方式见 `v4-runtime-guide.md`，关键变更见 `v4-change-record.md`，文档门禁见 `documentation-contract.md`。

## 1. 当前总策略：production-first

v4 不再以“长期 shadow + v3.9 运行时 fallback”为目标。

新真实任务应尽早进入 v4：

```text
真实任务
  ↓
v4 Source-to-Mix / transition / timeline
  ↓
明确证据足够 → 继续
证据不足 → review_required
```

**不确定性不能静默回退 v3.9。**

v3.9 仍存在于 Git 历史和过渡代码中，可用于源码比较、回归分析或仓库级 rollback；但不再维护为第二套正式生产算法，也不再继续给 monolith 增加新能力。

## 2. a3 已接管的生产事实

### Asset / occurrence identity

唯一真源：

- `TrackAsset`
- `TrackOccurrence`
- `ResolvedAssetBinding`

下游不得重新 fuzzy resolve source/LRC。

TrackAsset identity 包含 source SHA、raw lyric SHA、same-timestamp canonical selection SHA。改变 canonical original 选择会改变 `track_id/version_id`，旧 artifact 不可静默复用。

### Canonical lyric

统一 parser 支持 line LRC、Enhanced LRC、QRC、same-timestamp alternatives、word/token timing。

canonical text 不来自 ASR/Jianying，也不允许下游重新选择第一行。

### Source-to-Mix TimeWarp

```text
HPSS harmonic
 → Chroma CENS + MFCC
 → multi-candidate retrieval
 → top1/top2 margin
 → monotonic global path
 → AFFINE first
 → evidence-driven PIECEWISE_RATE
 → selective Fine Alignment
```

生产语义：

- 普通固定倍率歌曲优先停在 AFFINE；
- BPM 只是 soft prior；
- local rate change != cut；
- source-position jump 才产生 discontinuity；
- middle cut 永不自动 confirmed。

### Canonical Timeline

`lyric_aligner/timeline/projector.py` 把 canonical source timestamps 通过 effective TimeWarp 投影到 mix timeline。

Fine 被采用时优先用 Fine mapping，否则用 Coarse。blocked TimeWarp 不能进入正常 render 前置状态。

### Transition / overlap evidence

`lyric_aligner/pipeline/production.py` 严格区分：

- primary occurrence interval；
- shared transition search interval。

相邻歌曲边界不是硬 end。当前 a3 profile 在 nominal boundary 两侧各搜索 10 秒，让 LEFT/RIGHT source 在同一 mix 区间独立取证。

共享窗口不代表 overlap 已确认；强双侧 evidence 或重复 occurrence ambiguity 都会进入 review/BLOCK。

## 3. 默认 v4 真实任务入口

```text
scripts/v4_run.py
```

运行：

```bash
python scripts/v4_run.py \
  --task-manifest private/<task>/qa/task_manifest.json \
  --out-dir output/<task>/v4
```

自动执行：

1. Asset Resolution；
2. 每首 Primary Coarse；
3. Selective Fine；
4. Effective TimeWarp；
5. Canonical Timeline Projection；
6. 每个相邻边界双侧共享窗口 Coarse；
7. Transition probe；
8. 聚合 `v4_run.json` + `production_orchestration` artifact。

状态：

```text
ready_for_render
review_required
```

并明确记录：

```json
"legacy_fallback_used": false
```

## 4. `ready_for_render` 不是最终发布

当前 a3 尚未完成 package-native final SRT composer/renderer，因此：

```text
ready_for_render != publish_ready
```

它只表示当前 v4 mapping / transition / canonical timeline 没有 unresolved review issue。

下一阶段仍需：

- final timeline composer；
- SRT renderer；
- review decision artifact；
- Editor Evidence / LanguageSpan cue fusion；
- final render → release guard 原生接线。

## 5. Calibration / 可复现性

当前 profile version：

```text
production-bootstrap-2026-08-17-a3
```

包含 asset resolver、coarse、fine、transition、timewarp。

Transition profile 已实际控制：

- `min_score`
- `min_margin`
- `min_overlap_seconds`
- `search_margin_seconds`
- `minimum_feature_agreement`
- `merge_gap_seconds`

完整 profile 内容形成 `calibration_profile_id`。临时 CLI threshold override 只允许实验；最终 release 必须拒绝未固化 override。

## 6. Artifact lineage

当前链可产生：

```text
Task Manifest
 → asset_resolution
 → coarse_audio_alignment(s)
 → [fine_audio_alignment]
 → transition_probe(s)
 → canonical_timeline_projection(s)
 → production_orchestration
```

主要身份：task fingerprint、algorithm version、calibration profile、canonical selection、upstream artifact IDs、materialized output SHA-256。

## 7. 文档同步契约

已接入 CI：

- `references/documentation-contract.md`
- `scripts/validate_docs_contract.py`
- `scripts/test_docs_contract.py`

实质性生产变更没有同步 owning docs 时直接失败。

CI #241 曾因 CLI 变化未同步 runtime/workflow 文档而正确阻断；随后文档已按真实 a3 行为更新，规则没有被降低。

## 8. 合成端到端验证

新增：

```text
scripts/test_v4_run_end_to_end.py
```

它使用完全合成、无真实版权素材的：

- 12 秒 WAV source；
- 同源 mix；
- 虚构 LRC；
- schema 2.0 Task Manifest；

真实 subprocess 执行 `v4_run.py`，验证：

- Asset Resolution；
- Coarse/TimeWarp；
- canonical timeline；
- `ready_for_render`；
- `legacy_fallback_used=false`；
- production orchestration artifact lineage。

这不是 mock stage 测试。

## 9. 当前面向 `main` 的最终验收

PR #2 已从中间 integration base retarget 到 `main`。

`main` 是当前 head 的严格祖先：retarget 时 compare 为 `ahead 145 / behind 0`，GitHub 重新计算后 `mergeable=true`。

head `9c6c2303c1fb74c722741e712a20e925daa83a28` 的 CI #265 已成功，包括：

- Python 3.10 / 3.12 / 3.14 full unittest discovery；
- synthetic `v4_run` E2E；
- Documentation Contract；
- compileall `lyric_aligner + scripts`；
- Skill validation；
- privacy scan；
- environment validation；
- `git diff --check`；
- 独立 ASR environment。

但 #265 启动时 PR 尚未 retarget `main`。本次状态文档更新会触发新的 head CI，使 Documentation Contract 与完整测试矩阵以 **`main` 为 PR base** 再执行一次。只有该最新 head 全绿，才作为最终 merge 依据。

## 10. 合入 main 后下一阶段

### P0 — Final Timeline Composer / Renderer

让 v4 timeline 真正生成最终 cue/SRT，同时保持 fail-closed review。

### P1 — Replayable Review Decision

把 cut / overlap / boundary 等人工确认做成 task-scoped、fingerprinted、可重放 artifact，而不是口头/临时修改。

### P2 — Real-task calibration

记录 mapping residual、onset/offset、review density、cut/overlap 指标与 runtime，然后生成新的 named profile。

### P3 — Evidence fusion

将 Editor Evidence + LanguageSpan 真正接入最终 cue scoring。

### P4 — Forced Alignment / ASR v2

只有真实数据证明边界仍是主要误差来源时，再扩大这部分投入。

## 11. 当前完成度表述

可以说：

> **Lyric Aligner v4.0.0a3 已进入 production-first 阶段，实际负责 Source-to-Mix mapping、selective fine alignment、transition evidence 和 canonical timeline reconstruction。**

不能说：

- v4 已是 stable；
- v4 已完成最终字幕全链；
- 当前 calibration profile 已最优；
- 真实任务准确率已提升固定百分比；
- review candidate 可以自动当作 cut/overlap 真相。
