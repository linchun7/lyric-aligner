# Lyric Aligner v4 当前实施状态

更新日期：2026-08-17  
整合基线：`codex/v4-v3.9-integration`  
当前 hardening：`agent/v4-integration-hardening` / Draft PR #2  
v4 package：`4.0.0a2`  
TrackAsset schema：`1.1`

> 本文件只描述实际已实现、仍在 shadow/calibration、以及尚未完成的生产接线。架构复盘见 `v4-architecture-review-2026-08-17.md`；legacy 接线任务见 `v4-legacy-rewire-handoff.md`。

## 1. 总体决策

### v3.9：保留并冻结

v3.9 不因相对 v3.8 的可见准确率提升有限而删除。它保留 middle-cut review/apply、分段变速、Enhanced LRC/QRC、overlap review 和成熟 QA gate，因此继续作为：

- compatibility kernel；
- regression oracle；
- emergency fallback。

今后禁止继续向 `scripts/redo_karaoke_pipeline.py` 堆新算法。

### v4：彻底重构，但渐进替换

v4 的“彻底”是最终 production truth 全部迁入 `lyric_aligner/` 的类型化领域模型、版本化 calibration profile、stage artifacts 和 orchestrator；不是一次性把 5000+ 行 monolith 机械搬家。

当前 TimeWarp / transition / LanguageSpan 仍为 shadow/calibration，不替换 v3.9 正式映射。

## 2. Deep review 已修复/正在修复的关键问题

### 单一资产真源

Codex 初始接线仍存在：v4 resolver 先选 source，legacy `audio-align` 后续又 `find_source_audio()`。

已建立：

- `TrackAsset` schema 1.1；
- `ResolvedAssetBinding`；
- source/LRC 实体 hash 重验证；
- `legacy.bridge`。

本地 monolith rewire 后，v4 mode 必须只使用 binding source path/hash。

### 单一 canonical lyric 真源

旧 legacy `parse_lrc()` 会固定拿同时间戳第一行，与 `lyric-role-map` 可能冲突。

已建立：

- `canonical_selection_sha256`；
- canonical selection 纳入 `track_id/version_id`；
- `CanonicalLine/CanonicalToken`；
- 普通 LRC / Enhanced LRC / QRC 统一 parser；
- QRC 与 Enhanced LRC role preflight；
- metadata 不能被 override 为 original；
- shared `text/normalization.py`，避免 text 层反向依赖 assets 层。

因此修改 same-timestamp original 选择会改变资产语义身份，旧 artifact 不可静默复用。

## 3. 可升级结构

当前：

```text
lyric_aligner/
  assets/
    resolver.py
    bindings.py
    lyric_roles.py
  audio/
    features.py
    coarse_mapper.py
    timewarp.py
    fine_alignment.py
    transition.py
  contracts/
    artifacts.py
  legacy/
    bridge.py
  pipeline/
    context.py
  qa/
    final_integrity.py
  text/
    normalization.py
    canonical_lyrics.py
    language_spans.py
  config.py
  domain.py
```

仍待成熟：

```text
lyric_aligner/evidence/
lyric_aligner/timeline/
lyric_aligner/calibration/
lyric_aligner/cache/
```

当前结构是长期正确骨架，但不是“已经最优、以后不再调整”的完成态。

## 4. Calibration profile / 可复现性

新增 `V4CalibrationProfile` 与稳定 `profile_id`。

生产规则：

1. Asset Resolution 可通过 `--profile <json>` 输入一个完整 profile；
2. 完整 profile 嵌入 `track_assets.json`；
3. 后续 Coarse/Fine/Transition 从 `PipelineContext` 自动恢复同一 profile；
4. profile 内容改变 -> profile ID 改变；
5. 临时 CLI 调参可以做实验，但必须记录为 `calibration_overrides`；
6. Release Guard 遇到任何未固化进 profile 的 override -> BLOCK。

可用：

```bash
python scripts/v4_profile.py --write-default profile.json
python scripts/v4_profile.py --validate profile.json
```

这避免“阈值已经改了，artifact 仍声称是默认 profile”的假复现。

## 5. Source→Mix 当前链路

```text
HPSS harmonic
 -> Chroma CENS + MFCC
 -> multi-candidate retrieval
 -> NMS + top1/top2 margin
 -> monotonic global path
 -> AFFINE first
 -> PIECEWISE_RATE only when justified
 -> selective Fine Alignment
```

保持原则：

- 普通固定倍率歌曲停在 AFFINE；
- BPM 只是 soft prior；
- rate change != cut；
- source position jump 才产生 discontinuity candidate；
- `middle_cut=true` 不等于 confirmed；
- clean AFFINE 默认跳过 fine。

### 长歌单性能 hardening

Coarse 不再对整条 40–60 分钟 mix 每首重算 HPSS/Chroma/MFCC，只计算 occurrence/transition 搜索区间，再恢复全局 mix 坐标。

Fine 也只计算 coarse windows 覆盖的局部高分辨率 mix 特征，不再对整小时做 16kHz/hop256 特征。

下一步仍需要 source/mix feature cache，以 hash + profile/backend config 为 key，减少同一 source 在 coarse/fine/transition 中重复提取。

## 6. Artifact / Release lineage

当前 v4 stage：

```text
Task Manifest
 -> asset_resolution
 -> coarse_audio_alignment
 -> [fine_audio_alignment]
 -> [transition_probe]
 -> release
```

主要 artifact 记录：

```text
task fingerprint
algorithm version
calibration profile version/id
canonical selection identity
asset artifact ID
upstream artifact IDs
normalized effective config
output hashes
```

Release Guard：

- 拒绝跨 task；
- 拒绝 artifact ID/hash 不一致；
- 拒绝不同 calibration profile 拼接；
- 拒绝未固化进 profile 的 CLI calibration overrides。

## 7. Evaluator

已覆盖：

- sequence WER；
- line exact precision/recall/F1；
- missing/extra/wrong-order；
- split/merge；
- onset/offset MAE/P50/P90/P95；
- cut time-tolerance precision/recall；
- overlap duration precision/recall/IoU；
- track attribution accuracy。

这些指标是 TimeWarp 从 shadow 升 production 的门槛。

## 8. 当前生产接线还差什么

`references/v4-legacy-rewire-handoff.md` 已给本地 Codex 精确任务。

P0 只做 adapter seam：

- 删除 legacy 内第二个 v4 version 真源；
- v4 mode source 只从 `ResolvedAssetBinding`；
- v4 mode canonical lyrics 只从 `canonical_lines_for_ordinal()`；
- prepare/audio-align/build/refine/finalize/qa 共享同一 v4 context；
- strict SRT + max timeline end；
- asset/profile lineage 贯穿 release；
- 不传 v4 参数时 pure v3.9 行为保持不变。

禁止在这一步：

- TimeWarp production cutover；
- Forced Alignment；
- 新 ASR 模型；
- 调 bootstrap 阈值。

## 9. 下一阶段

### P1 TimeWarp shadow A/B

同一 occurrence 同时生成 v3.9 mapping 与 v4 TimeWarp，build 仍先用 v3.9。收集：

- mapping residual；
- onset/offset；
- cut/rate-change；
- overlap；
- review density；
- runtime。

blind-test 达标后按 occurrence gated adoption，不能一次性全切。

### P2 Timeline / Evidence

之后再建立：

- TrackOccurrence active state `{A,B,A+B,silence}`；
- EvidenceRecord / DecisionRecord；
- Editor/ASR/audio evidence fusion；
- source-side Forced Alignment；
- mix vocal local refinement；
- canonical fragment。

## 10. v4 完成定义

只有同时满足才可宣布 v4 正式替代 v3.9：

- production CLI 只调用 package orchestrator；
- monolith 不再决定 asset/canonical lyric/timewarp/timeline/release；
- stage artifacts 全部有 version/profile/upstream lineage；
- real calibration + blind-test 完成；
- fixed-speed 普通歌曲不退化；
- rate-change/cut/overlap 独立指标达标；
- zh/en/ko/ja/yue/mixed 分层指标达标；
- v3.9 只作为 regression/fallback。

## 11. 当前分支策略

- `codex/v4-v3.9-integration`：保留，作为冻结 v3.9 + 初始 v4 integration 基线；
- `agent/v4-integration-hardening` / PR #2：继续 Draft，完成 P0 architecture hardening；
- `main`：暂不修改。

CI/测试状态必须以 PR 当前 head 为准，旧 head 的成功不能自动继承。
