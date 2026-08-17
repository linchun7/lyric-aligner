# Lyric Aligner v4 关键变更记录

> 本文件只记录 v4 的实际代码变更、生产语义、验证与回滚。详细设计总览见 `references/v4-implementation.md`。

## 2026-08-17 — Milestone 0-A：Release Integrity

### 关键代码

- `lyric_aligner/srt.py`
- `lyric_aligner/qa/final_integrity.py`
- `lyric_aligner/contracts/artifacts.py`
- `scripts/v4_validate_release.py`
- `scripts/test_v4_release_integrity.py`

### 行为变化

- 坏 SRT block fail-closed；
- timeline end 使用 `max(cue.end_ms)`；
- FINAL SRT / audit CSV / QA 必须逐行、逐时间、逐正文一致；
- task fingerprint 与 algorithm version 必须一致；
- release artifact 绑定所有最终文件 SHA-256 和上游 lineage；
- artifact manifest 原子写出。

### 验证

已覆盖正文篡改、时间篡改、漏行、跨算法版本复用、manifest 篡改、坏 SRT、overlay 时间轴乱序。

---

## 2026-08-17 — Milestone 1-A：TrackAsset / TrackOccurrence / Canonical LRC Preflight

### 关键代码

- `lyric_aligner/domain.py`
- `lyric_aligner/assets/resolver.py`
- `lyric_aligner/assets/lyric_roles.py`
- `scripts/v4_resolve_assets.py`
- `scripts/test_v4_asset_resolver.py`
- `scripts/test_v4_lyric_roles.py`

### 行为变化

1. 曲目身份拆成：
   - `TrackAsset`：具体录音+canonical lyric 资产；
   - `TrackOccurrence`：该资产在本次 mix 中的一次出现。
2. LRC/source audio 不再“找不到就取最相似”：
   - top1 minimum score；
   - top1/top2 minimum margin；
   - artist+title 优先；
   - 同标题不同艺人主动降权；
   - 同一文件不能被两个不同 TrackAsset 静默复用。
3. 同时间戳 LRC 必须存在唯一 canonical original：
   - 单行可直接 original；
   - 已知语言可用 native script 确认唯一原文；
   - 无法区分 translation / romanization / pronunciation 时保持 `unknown`，不猜；
   - 两条都可能是 original 时 BLOCK。
4. `middle_cut=false|true|unknown` 只作为搜索策略先验，`true` 绝不等于 confirmed cut。

### Bootstrap 参数

```text
asset min_score = 0.76
asset min_margin = 0.08
```

这些参数尚未 calibration，不得作为长期固定生产阈值。

### 验证

Resolver + lyric-role foundation 组合：23 tests passed。

重点负向回归：无关单文件、live/studio 歧义、同标题不同艺人、generic 文件跨资产复用、同脚本双原文、auto 多候选。

---

## 2026-08-17 — Milestone 2-A：Affine-first TimeWarp

### 关键代码

- `lyric_aligner/audio/timewarp.py`
- `scripts/test_v4_timewarp.py`

### 核心模型

AFFINE：

```text
source_time = intercept + slope * mix_time
```

连续 PIECEWISE_RATE：

```text
source_time = intercept + base_slope * mix_time
            + Σ delta_slope_i * max(0, mix_time - breakpoint_i)
```

hinge basis 保证 breakpoint 处 source position 连续，只改变局部 slope。

### 模型选择

- 所有 occurrence 先走 AFFINE；
- AFFINE 解释充分时禁止升级；
- 只有 residual/drift/coverage 显示固定模型失败，且 piecewise 经复杂度惩罚后显著改善，同时有至少两个独立 feature family 支持，才采用 PIECEWISE_RATE；
- 当前最多两个 breakpoint，避免高自由度过拟合。

### BPM 语义

BPM 只进入弱 slope regularization，不锁 slope，不排除全局候选。错误 BPM prior 必须能够被真实 audio anchor 推翻。

### Cut 语义

- slope abrupt change != cut；
- source-position jump 才产生 discontinuity candidate；
- `middle_cut=false` -> unexpected discontinuity + BLOCK；
- `middle_cut=true/unknown` -> review candidate + BLOCK；
- 永不自动 confirmed；
- backward source jump 默认 BLOCK。

### Bootstrap 参数

当前 residual/drift/improvement、`max_continuous_rate=2.0`、`min_excess_source_jump=1.5s` 均待真实 calibration。

### 验证

TimeWarp foundation 包含：固定倍率、错误 BPM prior、三段局部倍率 `1.08 -> 1.17 -> 1.43`、abrupt rate change、声明/未声明 middle jump、独立 feature family 门禁。

---

## 2026-08-17 — Milestone 2-B（第一部分）：HPSS Harmonic Coarse Retrieval

### 关键代码

- `lyric_aligner/audio/features.py`
- `scripts/test_v4_audio_features.py`

### 算法

1. 对 mix/source 运行 HPSS；
2. 主 coarse evidence 使用 harmonic 分量；
3. 提取 Chroma CENS + MFCC；
4. 对多个 source position + slope 候选做局部重采样比较；
5. 输出 top1/top2、margin、estimated slope、Chroma/MFCC score 和 feature agreement；
6. 相邻 source 峰使用 NMS，避免同一峰的微小平移冒充 top1/top2；
7. 重复 motif 若存在两个远距离高峰，应表现为低 margin/ambiguous，而不是假唯一匹配。

### BPM / slope 搜索

默认 slope grid 保留全局搜索范围，即使提供 BPM prior 也只在 prior 附近额外加密，不删除其他倍率。因此错误 BPM 不应排除真实解。

### Bootstrap 参数

当前代码包括以下初始值，全部待 calibration：

```text
slope range = 0.65 .. 1.80
slope step = 0.10
fusion = 0.78 * chroma + 0.22 * mfcc
chroma agreement threshold = 0.68
mfcc agreement threshold = 0.58
coarse min_score = 0.72
coarse min_margin = 0.035
```

### 合成声学验证

测试构造：

- 唯一旋律 source；
- 从 source 中截取真实片段；
- 使用 `librosa.effects.time_stretch(..., rate=1.20)`；
- 在 mix 叠加强 140 BPM click；
- coarse retrieval 仍需定位回正确 source 区域并恢复约 1.20 的 slope。

另一个测试在 source 中放置两次相同 motif，要求 top1/top2 margin 下降且 `ambiguous=true`。

### 当前状态

这一步已经证明 feature extractor/retriever 的代码路径和关键失败模式可工作，但**还未接入真实 40–60 分钟 production mix**，也还没有 real-data calibration。因此不得宣称已取得某个真实准确率提升百分比。

---

## 当前组合验证

2026-08-17，在恢复的 v3.9 工作树 + 当前 v4 package 上：

```text
python -m compileall -q lyric_aligner scripts
PYTHONPATH=.:scripts python -m unittest \
  scripts.test_v4_audio_features \
  scripts.test_v4_release_integrity \
  scripts.test_v4_accuracy_foundation \
  scripts.test_v4_asset_resolver \
  scripts.test_v4_lyric_roles \
  scripts.test_v4_timewarp \
  scripts.test_evaluate_dataset \
  scripts.test_redo_karaoke_pipeline \
  scripts.test_end_to_end
```

结果：**82 tests passed**。

---

## 下一步

1. 将 coarse retrieval 多窗口结果转换为 TimeWarp `AlignmentAnchor`，形成可运行的 source-to-mix coarse mapper；
2. 增加真实/合成 transition margin 与 TrackOccurrence activity；
3. 把 waveform NCC 作为另一个独立 feature family 融入，而不是删除；
4. 只对低 margin / AFFINE drift / cut / overlap 边界运行 fine alignment；
5. 在本地 Codex 恢复完整 v3.9 legacy 工作树并接线 v4 package；
6. 建立 calibration/blind-test 后重定所有 bootstrap 阈值。
