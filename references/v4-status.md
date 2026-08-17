# Lyric Aligner v4 当前实施状态

更新日期：2026-08-17  
开发分支：`agent/v4-accuracy-foundation`  
Draft PR：#1  
v4 package：`4.0.0a1`

> 本文件只描述**实际已实现/已测试**与**尚未完成**。详细设计见 `v4-implementation.md`，实际命令见 `v4-runtime-guide.md`，变更记录见 `v4-change-record.md`，声学实验见 `v4-experiments.md`。

## 1. 当前结论

v4 已经有可运行的独立 package/stage。真实 v3.9 legacy baseline 已恢复到本地集成分支；生产 CLI 已接入 fail-closed asset identity 和最终 release integrity，但 TimeWarp、transition 和 LanguageSpan 仍处于 shadow/证据阶段，**尚未替换 legacy 音频映射主路径**。

已实现：

1. sequence-aware evaluator；
2. zh/en/ko/ja/yue/auto/generic Editor Evidence foundation；
3. strict SRT / final integrity / Artifact lineage；
4. `TrackAsset + TrackOccurrence`；
5. fail-closed LRC/source resolver；
6. canonical LRC role preflight + 显式任务级 role override；
7. UTF-8 default fail-closed 文本策略；
8. Affine-first TimeWarp；
9. HPSS harmonic + Chroma CENS + MFCC coarse retrieval；
10. 多窗口 monotonic source path；
11. selective high-resolution fine alignment；
12. 相邻歌曲 transition/overlap review evidence；
13. 行内 `LanguageSpan`；
14. 可直接运行的 v4 stage CLI。

PR #1 仍然不能作为生产替代合并：v4 TimeWarp、transition 和 LanguageSpan 尚未完成真实私有 calibration，也尚未成为 legacy `audio-align/build/finalize/qa` 的权威决策来源。

## 2. 关键实现

### 2.1 发布完整性

关键代码：

- `lyric_aligner/srt.py`
- `lyric_aligner/contracts/artifacts.py`
- `lyric_aligner/qa/final_integrity.py`
- `scripts/v4_validate_release.py`

能力：

- 坏 SRT block 立即失败；
- overlay SRT 时间轴终点使用 `max(cue.end_ms)`；
- FINAL SRT / audit CSV / QA 的数量、顺序、时间、正文严格绑定；
- task fingerprint / algorithm version 严格一致；
- ArtifactManifest 绑定配置、upstream IDs、输出 SHA-256；
- 下游消费时重新校验磁盘实体文件 size/hash，防止 manifest 生成后文件被修改。

### 2.2 输入身份

关键代码：

- `lyric_aligner/domain.py`
- `lyric_aligner/assets/resolver.py`
- `lyric_aligner/assets/lyric_roles.py`
- `lyric_aligner/io/text.py`
- `scripts/v4_resolve_assets.py`

能力：

- 一个具体录音+歌词版本 = `TrackAsset`；
- 在本次 mix 中一次出现 = `TrackOccurrence`；
- 同歌重复出现可复用 asset，但 occurrence_id 不同；
- LRC/source 匹配使用 minimum score + top1/top2 margin + artist/title identity + 文件唯一占用；
- 同一时间戳多条歌词无法唯一确认原文时 BLOCK；
- 可通过任务级 `--lyric-role-map` 显式指定 canonical original，不让算法猜；
- language/middle-cut/lyric-role map 的 SHA-256 写入 asset artifact；
- 默认 UTF-8，legacy encoding 必须显式处理。

显式 role override 规范：`references/v4-lyric-role-overrides.md`。

### 2.3 Source→Mix 音频映射

关键代码：

- `lyric_aligner/audio/features.py`
- `lyric_aligner/audio/coarse_mapper.py`
- `lyric_aligner/audio/timewarp.py`
- `lyric_aligner/audio/fine_alignment.py`
- `scripts/v4_coarse_align.py`
- `scripts/v4_fine_align.py`

链路：

```text
HPSS harmonic
  -> Chroma CENS + MFCC
  -> 每窗多个 source/slope 候选
  -> top1/top2 margin + NMS
  -> 跨窗 monotonic global path
  -> AlignmentAnchor
  -> AFFINE first
  -> 必要时 PIECEWISE_RATE
  -> 仅不确定/复杂区 Fine Alignment
```

关键语义：

- 大多数歌曲保持 AFFINE 快路径；
- BPM 只是 soft prior，不能硬锁 slope；
- `1.08 -> 1.17 -> 1.43` 可表示为连续 PIECEWISE_RATE；
- abrupt slope change != cut；
- source position jump 才是 middle-cut/discontinuity candidate；
- `middle_cut=true` 只改变搜索策略，仍必须 review；
- clean AFFINE 默认不跑 fine alignment。

### 2.4 歌曲交界

关键代码：

- `lyric_aligner/audio/transition.py`
- `scripts/v4_probe_transition.py`

`nominal_start_ms` 是搜索先验，不是上一首硬 end。两首相邻 TrackOccurrence 在同一 transition window 都出现强、非歧义 source evidence 时，只生成：

```text
cross_track_overlap_candidate
status=review
```

重复副歌造成的 low-margin 高分只进入 uncertain/BLOCK，不自动冒充叠唱。

### 2.5 行内混合语言

关键代码：

- `lyric_aligner/text/language_spans.py`

示例：

- ko+en：韩文 span `phonetic_hint`，英文 span `direct_text`；
- ja+en 同理；
- yue+en：粤语 Han span `timing_hint`，英文 span `direct_text`；
- 未知 Latin 不自动叫英文；
- 未知 Han 不自动叫普通话。

目前该策略尚未全面接入 legacy sequence/boundary score。

## 3. Evaluator 当前指标

`evaluate_dataset.py` 现在覆盖：

### 文本/顺序

- sequence-aware edit / `sequence_wer`；
- line exact precision/recall/F1；
- missing/extra/wrong-order line；
- split/merge count。

### 边界

使用单调 DP group alignment，支持有限 one-to-many / many-to-one：

- onset MAE / P50 / P90 / P95；
- offset MAE / P50 / P90 / P95；
- onset/offset 250ms、500ms 命中率；
- legacy combined boundary MAE/P95 保留。

### Cut / Overlap

- 新 cut 评估按真实 `time_ms + tolerance` 匹配，不要求共享人为 ID；
- legacy cut IDs 仅兼容；
- overlap duration precision/recall；
- overlap IoU；
- overlap event precision/recall；
- track attribution accuracy。

协议详见 `references/dataset-protocol.md`。

## 4. 当前验证

本地恢复 v3.9 baseline + 当前 v4 package/production asset-release 接线的组合回归：

```text
128 tests passed
27.61s
```

包含：

- legacy v3.9 middle-cut / variable-speed / overlap / end-to-end；
- evaluator；
- release false-ready / artifact hash drift；
- asset 错绑与歧义；
- LRC role ambiguity / explicit override；
- CP949 非显式编码；
- AFFINE / wrong BPM prior / PIECEWISE_RATE；
- `1.08 -> 1.17 -> 1.43`；
- rate change vs cut；
- 强 140 BPM click；
- repeated motif ambiguity；
- global coarse path；
- selective fine alignment；
- transition overlap review；
- mixed-language span；
- v4 CLI repo-root bootstrap。

一个合成 fine fixture（1.20× time-stretch + 140 BPM click）中：

```text
coarse source-center median error ≈ 38ms
fine   source-center median error ≈ 20ms
refined affine slope ≈ 1.204
```

这只是合成 fixture，不代表真实歌曲总体准确率。

## 5. 已验证拒绝的方案

HPSS harmonic 的直接 sample-domain waveform correlation 在 1.20× phase-vocoder time-stretch 后，即使 source 候选正确，相关分也只有约 `0.0008`。因此没有提交为通用第三声学 family。

详见 `references/v4-experiments.md`。

## 6. Bootstrap 参数

当前 score/margin/slope/residual/fine/transition 阈值均为**初始保守值**，没有 real-data calibration。不得将这些数字解释成生产最优阈值。

## 7. 下一步关键任务

### 必须由本地 Git/Codex 完成的迁移

1. 将恢复的真实 v3.9 legacy 工作树形成独立 commit/tag；
2. 再从该基线把 legacy CLI 接到 v4 package；
3. 禁止 3.8/3.9/v4 artifact 静默混用；
4. 跑私有真实 calibration/blind-test。

### 我们继续开发的算法方向

1. 把 Editor Evidence + LanguageSpan 接入实际 production scoring；
2. 让 transition margin 自动调度相邻 occurrence；
3. fine alignment 输出可供歌词边界优化使用的局部 timing evidence；
4. 使用真实 blind-test 重定 bootstrap 阈值；
5. 之后再评估 source-side Forced Alignment；
6. ASR v2、双模型和人声分离继续后置。

## 8. 发布状态

PR #1 必须继续保持 **Draft**。当前新模块可用于私有任务并行试跑/收集指标，但不能描述为已经替换 v3.x 正式生产管线。
