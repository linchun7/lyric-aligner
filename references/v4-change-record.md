# Lyric Aligner v4 关键变更记录

> 记录 v4 **实际已提交代码**的关键行为、测试、兼容性与未完成边界。详细接口和公式见 `references/v4-implementation.md`。

## 2026-08-17 — Milestone 0-A：Release Integrity

关键代码：

- `lyric_aligner/srt.py`
- `lyric_aligner/qa/final_integrity.py`
- `lyric_aligner/contracts/artifacts.py`
- `scripts/v4_validate_release.py`
- `scripts/test_v4_release_integrity.py`

关键变化：

- 坏 SRT block fail-closed；
- timeline end 使用 `max(cue.end_ms)`；
- FINAL SRT / audit CSV / QA 逐行、逐时间、逐正文严格绑定；
- task fingerprint 与 algorithm version 必须一致；
- release artifact 绑定最终文件 SHA-256、配置和 upstream lineage；
- manifest 原子写出；
- `v4_validate_release.py` 先重新验证 task manifest 中所有原始输入内容，再允许 release。

已覆盖正文篡改、时间篡改、漏行、跨算法版本复用、manifest 篡改、坏 SRT、overlay 时间轴乱序。

---

## 2026-08-17 — Foundation：Sequence-aware Evaluator / Editor Evidence

关键代码：

- `scripts/evaluate_dataset.py`
- `scripts/editor_evidence.py`
- `scripts/language_profiles.py`
- `scripts/validate_multilingual_asr.py`

关键变化：

- 增加 sequence-aware error、line exact precision/recall/F1、missing/extra line；
- `zh/en` 可用 direct editor text；
- `ko/ja` 编辑器文字仅 phonetic hint；
- `yue` 默认 timing hint；
- `auto/generic` 走 ASR language detection，编辑器文字不具备 canonical 决策权。

状态：策略/评估层已实现；Editor Evidence 尚未全面接入 legacy 生产打分。

---

## 2026-08-17 — Milestone 1-A：TrackAsset / TrackOccurrence / Canonical LRC Preflight

关键代码：

- `lyric_aligner/domain.py`
- `lyric_aligner/assets/resolver.py`
- `lyric_aligner/assets/lyric_roles.py`
- `lyric_aligner/io/text.py`
- `scripts/v4_resolve_assets.py`
- `scripts/test_v4_asset_resolver.py`
- `scripts/test_v4_lyric_roles.py`
- `scripts/test_v4_text_encoding.py`

### 资产身份

- `TrackAsset` 表示具体录音 + canonical lyric 版本；
- `TrackOccurrence` 表示该资产在当前 mix 中的一次出现；
- 同一首歌重复出现可以共用 asset，但 `occurrence_id` 必须不同；
- 不同艺人/版本不能因为标题相同而串证据。

### fail-closed resolver

旧的“找不到就选最像文件”被替换为：

- top1 minimum score；
- top1/top2 minimum margin；
- Artist+Title 精确命中优先；
- 同标题不同艺人主动降权；
- 同一 LRC/原曲文件不能被两个不同 TrackAsset 静默复用；
- 选择过程保留 top1/top2/margin 审计数据。

Bootstrap：

```text
asset min_score = 0.76
asset min_margin = 0.08
```

尚未 calibration。

### canonical LRC role

- 每个 timestamp group 必须有唯一 original；
- 单行组可直接 original；
- 已知 `ko/ja/zh/yue/en` 可用 native script 确认唯一原文；
- 无法区分 translation / romanization / pronunciation 的同行保持 `unknown`，不猜；
- 两条都可能是 original 或 `auto` 多候选无法判断 -> BLOCK。

### 文本编码

v4 新生产路径默认 UTF-8 fail-closed。CP949/Shift-JIS/其他 legacy encoding 必须显式声明或预先转换；禁止 UTF-8 失败后盲猜 GB18030，因为“成功解码成错误脚本”比直接失败更危险。

### Task/Artifact lineage

`v4_resolve_assets.py` 现在：

- 必须读取 schema 2.0 task manifest；
- song list / lyrics dir / source audio dir 的路径和内容必须与 manifest 一致；
- `track_assets` 写入 task fingerprint、algorithm version、song-list SHA-256 和 resolver config；
- 同时产生 `asset_resolution` stage ArtifactManifest。

`middle_cut=false|true|unknown` 只控制搜索策略；`true` 绝不等于 confirmed cut。

---

## 2026-08-17 — Milestone 2-A：Affine-first TimeWarp

关键代码：

- `lyric_aligner/audio/timewarp.py`
- `scripts/test_v4_timewarp.py`

AFFINE：

```text
source_time = intercept + slope * mix_time
```

连续 PIECEWISE_RATE：

```text
source_time = intercept + base_slope * mix_time
            + Σ delta_slope_i * max(0, mix_time - breakpoint_i)
```

模型选择：

- 所有 occurrence 先走 AFFINE；
- 固定模型解释充分时禁止升级；
- 只有 residual/drift/coverage 明显失败，且 piecewise 经复杂度惩罚后显著改善，并得到至少两个独立 feature family 支持，才接受 PIECEWISE_RATE；
- 当前最多两个 breakpoint，防止高自由度过拟合。

BPM 只作弱 slope prior；错误 BPM 必须允许被真实 anchor 推翻。

Cut 语义：

- abrupt slope change != cut；
- source-position jump 才是 discontinuity candidate；
- `middle_cut=false` -> unexpected discontinuity + BLOCK；
- `middle_cut=true/unknown` -> review + BLOCK；
- 永不自动 confirmed；
- backward source jump 默认 BLOCK。

Bootstrap：residual/drift/improvement、`max_continuous_rate=2.0`、`min_excess_source_jump=1.5s` 均待 calibration。

---

## 2026-08-17 — Milestone 2-B：Harmonic Coarse Retrieval + Global Path

关键代码：

- `lyric_aligner/audio/features.py`
- `lyric_aligner/audio/coarse_mapper.py`
- `scripts/test_v4_audio_features.py`
- `scripts/test_v4_coarse_mapper.py`

### 单窗多特征检索

1. mix/source 运行 HPSS；
2. coarse 主证据使用 harmonic 分量；
3. 提取 Chroma CENS + MFCC；
4. 对 source position + slope 候选进行局部重采样匹配；
5. 保存多个候选，而不仅是 top1；
6. 输出 top1/top2、margin、estimated slope、Chroma/MFCC score、feature agreement；
7. source 近邻峰用 NMS，避免同一峰的小平移伪装成 top1/top2；
8. 重复 motif 的远距离双峰必须表现为低 margin/ambiguous。

### BPM/slope 搜索

BPM prior 只在其附近增加更密候选，**不会删除全局 slope grid**，所以错误 BPM 不会把正确倍率排除在搜索空间外。

### 多窗口全局路径

`coarse_mapper.py` 不让每个窗口独立采用 top1，而是在每窗多个候选上运行动态路径选择：

- source 时间默认单调向前；
- backward jump 默认不允许；
- transition 同时考虑 acoustic emission 与局部 observed rate；
- 允许局部 rate 改变，不要求全曲固定 slope；
- `middle_cut=false` 时大 forward jump 不允许被路径吞掉；
- `middle_cut=true/unknown` 可保留 jump 路径，但后续 TimeWarp 会产生 review/BLOCK discontinuity。

选出的整曲 path 自动转换为：

```python
AlignmentAnchor(
    mix_time,
    source_time,
    confidence=fused_score,
    feature_scores={"chroma": ..., "mfcc": ...},
)
```

随后直接进入 `select_timewarp()`。

### Bootstrap 参数

```text
slope range = 0.65 .. 1.80
slope step = 0.10
fusion = 0.78 * chroma + 0.22 * mfcc
chroma agreement threshold = 0.68
mfcc agreement threshold = 0.58
coarse min_score = 0.72
coarse min_margin = 0.035
```

全部待 calibration。

### 合成声学验证

已测试：

- 从 source 截取真实片段；
- `time_stretch(rate=1.20)`；
- mix 叠加强 140 BPM click；
- 故意提供错误 BPM prior=1.05；
- 多窗口 coarse path 仍需单调定位正确 source，并让 TimeWarp 拟合 slope 接近 1.20。

另有 source 重复两次相同 motif 的测试，必须显示低 top1/top2 margin 并 `ambiguous=true`。

### 当前边界

该链路已经可运行：

```text
HPSS/Chroma/MFCC
 -> multi-candidate retrieval
 -> monotonic global path
 -> AlignmentAnchor
 -> Affine-first TimeWarp
```

但尚未替换 legacy `audio-align/build/finalize`，也尚未在真实 40–60 分钟私有歌单上 calibration，因此不得声称真实准确率已提升某个百分比。

---

## 当前组合验证

在恢复的 v3.9 工作树 + 当前 v4 package 上：

```text
85 tests passed
```

组合包括：

- v3.9 legacy middle-cut / variable-speed / overlap / end-to-end；
- v4 release integrity；
- sequence-aware evaluator；
- Editor Evidence foundation；
- TrackAsset resolver；
- LRC role preflight；
- strict multilingual encoding；
- TimeWarp；
- harmonic coarse retrieval；
- global coarse path。

---

## 下一步

1. 增加可直接运行的 coarse-align CLI 和 stage artifact；
2. transition margin / TrackOccurrence activity / 双曲 overlap evidence；
3. 把 legacy waveform NCC 作为独立 evidence family 融入，而不是删除；
4. 只对 low-margin / AFFINE drift / cut / overlap boundary 做 fine alignment；
5. 本地 Codex 恢复完整 v3.9 legacy 工作树并接线 v4 package；
6. 私有 calibration/blind-test 后重定全部 bootstrap 阈值。
# 2026-08-17：本地 v3.9 baseline 恢复与 v4 production safety wiring

- v3.9 legacy 生产脚本、测试和文档以独立 baseline commit 恢复；v4 集成在该 baseline 之上进行，保留 middle-cut review/apply、连续分段变速、Enhanced LRC/QRC、叠唱 review/confirmed interval 和发布门禁。
- `redo_karaoke_pipeline.py prepare` / `audio-align` 新增可选 v4 asset mode：必须同时提供 fingerprinted `track_assets.json` 和 asset artifact，严格校验 schema、task fingerprint、v4 algorithm version、artifact ID 和 materialized output hash 后，才按 occurrence ordinal 使用 canonical LRC 路径。
- 不提供 v4 asset 参数时保留 v3.9 legacy 兼容入口；提供任一而不提供另一项会 fail-closed，避免半接线或跨任务资产混用。
- legacy `qa` 在 `publish_ready=true` 时自动创建 release ArtifactManifest，严格绑定 FINAL SRT、审计 CSV、QA JSON、task fingerprint 和算法版本；不可发布的 QA 不会产生 release manifest。
- 此轮不把 v4 TimeWarp/transition/LanguageSpan 直接升格为终稿权威来源。它们仍需通过私有 A/B 与 calibration 后才可改变 v3.9 映射或发布决策。
