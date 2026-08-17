# Lyric Aligner v4 实验与负结果记录

> 目的：记录会影响架构决策的实验，包括**失败/拒绝方案**。失败结果不得只留在聊天或临时 notebook，避免后续 AI 重复走同一条弯路。

## 2026-08-17 — HPSS Harmonic + Chroma/MFCC Coarse Retrieval

### 假设

强 140 BPM click 会污染以瞬态/节奏为主的匹配；先做 HPSS，再主要利用 harmonic Chroma CENS / MFCC，应比纯原始波形更适合当前变速歌曲。

### 合成条件

- 构造多音高 source；
- 从 source 中截取片段；
- `librosa.effects.time_stretch(..., rate=1.20)`；
- mix 上叠加强 140 BPM click；
- coarse slope 候选包含 1.0/1.1/1.2/1.3/1.4；
- 另一个 fixture 将同一 motif 在 source 中重复两次。

### 结果

- 变速+click 条件下能够定位回正确 source 区域，并恢复约 1.20 的 slope；
- source 中存在两个重复 motif 时，top1/top2 margin 会明显下降并标记 `ambiguous`，不会宣称唯一位置。

### 结论

**保留。** 已进入 `lyric_aligner/audio/features.py` 与 `coarse_mapper.py`。

---

## 2026-08-17 — Selective Fine Alignment

### 假设

大多数 clean AFFINE 不应承担高分辨率开销；只有 coarse blocked / ambiguous / piecewise 等难例才在已知 source 附近做小范围高分辨率搜索。

### 合成条件

- source clip 起点不是 coarse candidate step 的整数倍；
- 1.20× time-stretch；
- 强 140 BPM click；
- coarse candidate step 0.5s；
- fine candidate step 0.05s、hop 256。

### 结果

该 fixture 中：

```text
coarse source-center median error ≈ 38ms
fine   source-center median error ≈ 20ms
refined affine slope ≈ 1.204
```

### 结论

**保留，但仅 selective。** 该数字只属于合成 fixture，不代表真实歌曲总体收益。

---

## 2026-08-17 — 直接 Harmonic Waveform Correlation 作为第三证据

### 假设

为了保留 legacy waveform NCC 的优点，尝试在 Chroma/MFCC 排出少量候选后，对 HPSS harmonic waveform 做 source segment 重采样 + 小 lag normalized cross-correlation，作为第三声学 evidence family。

### 合成条件

使用与 coarse 验证相同的：

- 同一 source 片段；
- `librosa.effects.time_stretch(rate=1.20)`；
- 强 140 BPM click；
- 已知候选已接近正确 source 位置和 slope。

### 实测

正确候选的 harmonic time-domain correlation 约：

```text
0.0008
```

远低于可作为稳定证据的水平。

### 原因判断

phase-vocoder/time-stretch 会改变相位与局部时域波形结构。即使 musical content 和时间位置正确，直接 sample-domain correlation 也可能近似失效。HPSS 可以弱化 click，但不能恢复被 time-stretch 改写的相位关系。

### 结论

**拒绝进入 v4 通用 coarse/fine evidence。此次实验代码未提交。**

重要约束：

- 不因为 legacy 存在 waveform NCC 就默认它适合 v4 的所有变速素材；
- v4 当前继续使用 harmonic Chroma/MFCC；
- 如果需要第三声学 family，优先研究 CQT/spectral embedding、vocal/phonetic evidence 或其他对 time-stretch 更稳定的表征；
- waveform evidence 未来如保留，应限定在经过 calibration 证明有效的特定条件，而不能作为全局高权重证据。

---

## 实验记录规则

以后以下情况必须在本文追加：

- 新 feature/model/backend 被采用或拒绝；
- 合成表现好但真实数据退化；
- 某阈值/权重经 calibration 修改；
- 某 evidence family 被证明高度相关、不能视为独立证据；
- Forced Alignment / ASR / source separation backend 的 A/B 结果。
