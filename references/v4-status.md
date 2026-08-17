# Lyric Aligner v4 当前实施状态

更新日期：2026-08-17  
当前分支：`agent/v4-integration-hardening`  
Draft PR：#2  
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

v3.9 仍存在于 Git 历史和整合提交中，可用于源码比较、回归分析或仓库级 rollback；但不再要求维护为第二套正式生产算法，也不再继续给 monolith 增加新能力。

## 2. a3 已经开始接管的生产事实

### 2.1 Asset identity

唯一真源：

- `TrackAsset`
- `TrackOccurrence`
- `ResolvedAssetBinding`

下游不得重新 fuzzy resolve source/LRC。

TrackAsset identity 包含：

- source recording SHA-256；
- raw lyric SHA-256；
- same-timestamp canonical selection SHA-256。

改变 canonical original 选择会改变 `track_id/version_id`，旧 artifact 不可静默复用。

### 2.2 Canonical lyric

统一 parser 支持：

- line LRC；
- Enhanced LRC；
- QRC；
- same-timestamp alternatives；
- word/token timing。

canonical text 不来自 ASR/Jianying，也不允许下游重新选择第一行。

### 2.3 Source-to-Mix TimeWarp

当前链路：

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

- 大多数普通固定倍率歌曲停在 AFFINE；
- BPM 只是 soft prior；
- local rate change != cut；
- source-position jump 才产生 discontinuity；
- middle cut 永不自动 confirmed。

### 2.4 Canonical Timeline

已新增：

```text
lyric_aligner/timeline/projector.py
```

它把已确认 canonical LRC/Enhanced LRC/QRC source timestamp 通过当前有效 TimeWarp 投影到 mix timeline。

Fine 被实际采用时优先用 Fine mapping，否则用 Coarse mapping。TimeWarp blocked 时不能把该 timeline 当成可继续渲染的正式结果。

### 2.5 Transition / overlap evidence

已新增生产计划层：

```text
lyric_aligner/pipeline/production.py
```

它严格区分：

- primary occurrence interval；
- shared transition search interval。

相邻歌曲边界不是硬 end。默认 profile 在 nominal boundary 两侧各搜索 10 秒，让 LEFT/RIGHT source 在同一 mix 区间独立取证。

共享搜索窗口 **不代表 overlap 已确认**。只有双方都有强、非歧义 evidence 才生成 overlap candidate；重复副歌等 ambiguity 会直接 review/BLOCK。

## 3. 默认 v4 真实任务入口

已新增：

```text
scripts/v4_run.py
```

运行：

```bash
python scripts/v4_run.py \
  --task-manifest private/<task>/qa/task_manifest.json \
  --out-dir output/<task>/v4
```

它自动执行：

1. Asset Resolution；
2. 每首 Primary Coarse；
3. Selective Fine；
4. Effective TimeWarp；
5. Canonical Timeline Projection；
6. 每个相邻边界双侧共享窗口 Coarse；
7. Transition probe；
8. 聚合 `v4_run.json` + `production_orchestration` artifact。

输出状态只有：

```text
ready_for_render
review_required
```

并明确记录：

```json
"legacy_fallback_used": false
```

## 4. `ready_for_render` 不是最终发布

当前 a3 **尚未完成 package-native final SRT composer/renderer**。

因此：

```text
ready_for_render
≠
publish_ready
```

它只表示：当前 v4 mapping/transition/timeline 阶段未留下 unresolved review issue。

最终字幕生产仍需要下一阶段完成：

- timeline composer；
- final SRT renderer；
- review decision artifact；
- Editor Evidence / LanguageSpan cue fusion；
- final render → release guard 一键接线。

## 5. Calibration / 可复现性

`V4CalibrationProfile` 当前 profile version：

```text
production-bootstrap-2026-08-17-a3
```

包含：

- asset resolver；
- coarse；
- fine；
- transition；
- timewarp。

Transition profile 已包含：

- `min_score`
- `min_margin`
- `min_overlap_seconds`
- `search_margin_seconds`
- `minimum_feature_agreement`
- `merge_gap_seconds`

完整 profile hash 形成 `calibration_profile_id`。

临时 CLI threshold override 只允许实验；最终 release 必须拒绝未固化 override。

## 6. Artifact lineage

当前生产链 artifact 可覆盖：

```text
Task Manifest
 → asset_resolution
 → coarse_audio_alignment(s)
 → [fine_audio_alignment]
 → transition_probe(s)
 → canonical_timeline_projection(s)
 → production_orchestration
```

主要身份字段：

- task fingerprint；
- v4 algorithm version；
- calibration profile version/id；
- canonical selection identity；
- asset artifact ID；
- upstream artifact IDs；
- materialized output SHA-256。

## 7. 文档同步契约

已建立并接入 CI：

- `references/documentation-contract.md`
- `scripts/validate_docs_contract.py`
- `scripts/test_docs_contract.py`

实质性生产变更必须在同一 PR 同步 owning docs；否则 CI 失败。

2026-08-17 的 CI #241 已验证该门禁有效：compileall 和 ASR environment 通过，但因当时 CLI 变化尚未同步 runtime/workflow 文档，Python 3.10/3.12/3.14 都在 Documentation Contract 阶段被正确阻断。当前分支随后已更新运行文档，必须以新的 head CI 结果为准，不能引用 #241 作为成功结果。

## 8. 当前仍需完成才能面向 main 合并

合并前最低要求：

1. 当前 head Documentation Contract 通过；
2. Python 3.10 / 3.12 / 3.14 全量 unittest 通过；
3. ASR environment 通过；
4. `validate_skill.py` / privacy scan / environment / diff-check 通过；
5. `v4_run.py --help` bootstrap 测试通过；
6. production plan / timeline / transition profile 测试通过；
7. PR 文档准确描述 a3 production-first，而不是旧 shadow/fallback 策略。

达到以上条件后，可将 PR #2 retarget 到 `main` 做最终 merge review。

## 9. 合入 main 后的下一阶段

优先级：

### P0 — Final Timeline Composer / Renderer

让 v4 timeline 真正生成最终 cue/SRT，同时保持 fail-closed review。

### P1 — Real-task calibration

用真实私有任务记录：

- mapping residual；
- onset/offset；
- review density；
- cut false positive / false negative；
- overlap precision/recall；
- runtime。

然后生成新的 named profile，而不是直接改 bootstrap 常量。

### P2 — Evidence fusion

将 Editor Evidence + LanguageSpan 真正接入最终 cue scoring。

### P3 — Forced Alignment / ASR v2

只有真实数据证明边界仍是主要误差来源时，再扩大这部分投入。

## 10. 当前完成度表述

当前可以说：

> **Lyric Aligner v4.0.0a3 已进入 production-first 阶段，开始实际负责 Source-to-Mix mapping、selective fine alignment、transition evidence 和 canonical timeline reconstruction。**

当前不能说：

- v4 已是 stable；
- v4 已完成最终字幕全链；
- 当前 calibration profile 已经最优；
- 真实任务准确率已经提升固定百分比；
- review candidate 可以自动当作 cut/overlap 真相。
