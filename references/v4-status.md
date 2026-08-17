# Lyric Aligner v4 当前实施状态

更新日期：2026-08-17  
整合基线：`codex/v4-v3.9-integration`  
当前 hardening：`agent/v4-integration-hardening` / Draft PR #2  
v4 package：`4.0.0a2`  
TrackAsset schema：`1.1`

> 本文件只描述**实际已实现/正在接线/尚未生产启用**。架构复盘见 `v4-architecture-review-2026-08-17.md`；legacy 最小接线任务见 `v4-legacy-rewire-handoff.md`。

## 1. 当前总体结论

### v3.9

**保留并冻结，不废弃。**

v3.9 的价值主要是 compatibility kernel / regression oracle / emergency fallback，而不是未来架构。它已经保留 middle-cut、分段变速、Enhanced LRC/QRC、overlap review 与成熟 QA gate；今后禁止继续把新算法堆进 monolith。

### v4

v4 是一次**架构层面的彻底重构**，但采用渐进替换，不采用一次性 rewrite。最终目标是 production truth 全部位于 `lyric_aligner/` package；`scripts/redo_karaoke_pipeline.py` 最终只剩薄兼容入口或归档 fallback。

当前 **TimeWarp / transition / LanguageSpan 仍为 shadow/calibration 能力**，尚未替换 v3.9 正式音频映射。

## 2. 本轮 deep review 发现并已开始修复的 P0

### 2.1 source audio 双真源

Codex 已接入 v4 TrackAsset，但 legacy `audio-align` 仍会再次调用旧 `find_source_audio()`。因此 v4 resolver 选中的 source 与实际波形对齐使用的 source 仍可能不同。

处理：新增 `ResolvedAssetBinding` 与 `legacy.bridge`；下一步本地 monolith 接线必须在 v4 mode 只使用 binding source path/hash。

### 2.2 canonical lyric 双真源

v4 `lyric-role-map` 可指定同时间戳第 N 行为 original，但 legacy `parse_lrc()` 仍固定选择 `alternatives[0]`。

处理：

- TrackAsset schema 1.1 将 exact canonical selection 纳入 semantic identity；
- 新增 `canonical_selection_sha256`；
- 新增统一 canonical lyric parser；
- Enhanced LRC/QRC 词级时间仍保留；
- v4 mode 下 legacy 必须使用 bridge 提供的 canonical lines，禁止重新选第一行。

### 2.3 semantic identity 不完整

旧 TrackAsset ID 只绑定 source hash + raw LRC hash。同一 LRC 文件如果 role override 从 alternative 0 改到 1，asset identity 不变。

已修复：`track_id/version_id` 现在同时包含 `canonical_selection_sha256`。因此修改原文选择会强制产生新 asset identity，旧 artifact 不可静默复用。

### 2.4 阈值散落

旧 alpha 把 resolver/coarse/fine/transition/TimeWarp bootstrap 数字散在函数与 CLI 默认值中。

已修复基础：新增 `V4CalibrationProfile` 与稳定 `profile_id`。Resolver/Coarse/Fine/Transition artifact 开始记录 profile version/id；Release Guard 可拒绝跨 profile 拼接。

### 2.5 v4 version 重复定义

legacy monolith 仍硬编码 `V4_ALGORITHM_VERSION = 4.0.0a1`，而 package 已进入 `4.0.0a2`。

处理策略：不降级 package；本地 rewire 必须删除第二个 v4 真源，统一从 package/bridge 读取版本。

## 3. 当前新增长期结构

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
    canonical_lyrics.py
    language_spans.py
  config.py
  domain.py
```

新增关键职责：

- `ResolvedAssetBinding`：一个 occurrence 的 source/LRC/original 单一真源；
- `CanonicalLine/CanonicalToken`：统一普通 LRC、Enhanced LRC、QRC；
- `PipelineContext`：绑定 task fingerprint、v4 version、calibration profile、asset artifact、occurrence bindings；
- `legacy.bridge`：明确依赖方向只能 `legacy -> v4 contracts`；
- `V4CalibrationProfile`：阈值与算法代码解耦。

仍待建立/成熟：

```text
lyric_aligner/evidence/
lyric_aligner/timeline/
lyric_aligner/calibration/
lyric_aligner/cache/
```

因此当前结构是**正确的长期骨架，但不是“已经最优完成态”**。

## 4. Source→Mix 当前状态

已有：

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

关键语义保持：

- 大多数固定倍率歌曲停在 AFFINE；
- BPM 是 soft prior；
- rate change != cut；
- source-position jump 才产生 discontinuity/cut candidate；
- `middle_cut=true` 不等于 confirmed；
- clean AFFINE 默认不跑 fine。

### 本轮性能 hardening

此前每个 occurrence 都会对整条 40–60 分钟 mix 重做 HPSS/Chroma/MFCC。

已改：`build_coarse_timewarp()` 只对当前 `mix_start..mix_end` 搜索区间提取 mix 特征，然后把结果坐标恢复为全局时间。source 仍整曲提特征，因为需要全源检索。

这会显著降低长歌单重复特征计算；后续仍计划增加 hash/config keyed feature cache。

## 5. Artifact / Release lineage

当前 v4 stage 链：

```text
Task Manifest
 -> asset_resolution
 -> coarse_audio_alignment
 -> [fine_audio_alignment]
 -> [transition_probe]
 -> release
```

Resolver/Coarse/Fine/Transition 现在都可携带同一个：

```text
calibration_profile_version
calibration_profile_id
asset_artifact_id
```

`v4_validate_release.py` 支持重复传入 `--upstream-artifact`：

- 校验 task fingerprint；
- 校验 artifact_id；
- 收集 upstream IDs；
- 若 upstream 使用不同 calibration profile -> BLOCK；
- 最终 release manifest 绑定这些 upstream IDs。

## 6. 测量能力

Evaluator 已覆盖：

- sequence WER / line exact precision/recall/F1；
- missing/extra/wrong-order；
- split/merge；
- onset/offset MAE、P50/P90/P95；
- cut time-tolerance precision/recall；
- overlap duration precision/recall/IoU；
- track attribution accuracy。

这些指标是后续 TimeWarp 从 shadow 升 production 的门槛，不再用“看几个字幕觉得更准”代替 A/B。

## 7. 已验证拒绝的方向

HPSS harmonic 的 sample-domain waveform correlation 在 phase-vocoder 变速后不稳定；合成验证中正确 candidate 的相关分仍接近零。因此没有作为第三声学 family 上线。详见 `v4-experiments.md`。

## 8. 当前必须完成的下一步

### P0：legacy integration seam

任务书：`references/v4-legacy-rewire-handoff.md`

只允许接线，不允许加算法：

1. 删除 legacy 内独立 v4 version 真源；
2. 所有 v4 mode source audio 来自 `ResolvedAssetBinding`；
3. 所有 v4 mode canonical lyric 来自 `canonical_lines_for_ordinal()`；
4. build/refine/finalize/qa 统一收到同一个 v4 context；
5. v4 mode strict SRT + max timeline end；
6. lineage 从 asset 一直传播到 release；
7. pure v3.9 mode 完全保持原行为。

### P1：TimeWarp shadow A/B

P0 完成后：

- 同一 occurrence 同时生成 v3.9 mapping 与 v4 TimeWarp；
- build 暂时仍使用 v3.9；
- 收集 mapping residual、边界、cut/overlap、review density、runtime；
- calibration/blind-test 达标后按 occurrence gated adoption；
- 不允许一次切换所有歌曲。

### P2：Timeline / Evidence

之后再进入：

- TrackOccurrence active states `{A, B, A+B, silence}`；
- Editor/ASR/audio/forced-align EvidenceRecord；
- source-side Forced Alignment；
- mix vocal local refinement；
- canonical fragment。

## 9. “v4 已彻底完成”的退出条件

只有同时满足才可宣称 v4 正式替代 v3.9：

- production CLI 只调用 package orchestrator；
- monolith 不再决定 asset/canonical lyric/timewarp/timeline/release；
- stage artifact 全部有 version/profile/upstream lineage；
- real calibration + blind-test 完成；
- fixed-speed 普通歌曲不退化；
- rate-change/cut/overlap 独立指标达标；
- zh/en/ko/ja/yue/mixed 分层结果达标；
- v3.9 只作为 regression/fallback。

## 10. 当前发布状态

- `codex/v4-v3.9-integration`：建议作为冻结 v3.9 + 初始 v4 integration 基线，不废弃。
- `agent/v4-integration-hardening` / PR #2：继续 Draft，当前正在完成 P0 architecture hardening。
- `main`：暂不修改。

CI 与测试数字只以当前 head 的实际 GitHub Actions 结果为准；旧 head 的成功不自动继承到新提交。
