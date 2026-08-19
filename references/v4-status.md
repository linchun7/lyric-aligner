# Lyric Aligner v4 当前实施状态

更新日期：2026-08-19  
主线算法版本：`4.0.0a9`

> P3 前完整历史状态见 `references/archive/2026-08-19-pre-p3-v4-status.md`。真实生产设计基线见 `references/production-requirements.md`。Smart / Pro v1.1 细节见 `references/smart-pro-v1-1.md`。

## 1. 当前四档产品路径

```text
Standard -> Text Repair V2.1
Smart    -> Anchor Timeline Repair（no-audio）
Pro      -> Selective Audio Repair（局部 audio evidence）
Max      -> Full V4 Alignment
```

共同事实：Canonical lyric 是最终文字/顺序 truth。Jianying timing 是强先验但可被充分独立证据推翻。Smart 使用 timed canonical + editor majority anchors 建 no-audio timing evidence；Pro/Max 才引入 acoustic Source-to-Mix。

韩文、日文或整段外文不因语言本身自动进入 Max。只要 canonical identity 与 timing anchors 足够，Smart 仍可成立；局部难点升级 Pro；只有整体 mapping/timeline 广泛不可信、anchor coverage 太弱或复杂结构无法局部解决时进入 Max。

## 2. Standard — Text Repair V2.1

已在 main 成立。适用于“规范歌词可信、剪映时间轴冻结、只修文字”：

- 不读取 audio；
- 不改变 cue count / number / start / end；
- deterministic 文字纠错与 bounded segmentation span；
- ambiguous/mixed/unsafe layout 情况继续 fail closed；
- production `auto-threshold >= 0.72`；
- report schema `2.1`。

## 3. Smart — Anchor Timeline Repair v1.1

Smart 是日常主力 no-audio 模式：**大部分信剪映，只推翻少量能被多重独立证据证明错误的 timing。**

实现：

```text
lyric_aligner/timeline/anchor_repair.py
lyric_aligner/timeline/smart_policy.py
scripts/v4_smart_repair.py
```

### 3.1 Canonical timing / anchors

Smart 保留 line-level LRC timestamp、Enhanced LRC/QRC word/token timestamp。A/B/C identity 分级不变：只有 A-grade exact/unique/1:1 editor identity 可建立主 timing model；B/C 不得建立模型。

每首歌默认：

```text
source_time = offset + rate * mix_time
```

单倍率优先；DAW exact stretch ratio 优先于 BPM-derived `target_bpm / source_bpm`；无 prior 时由 A anchors robust estimate。少量同歌多 rate / cut 仍升级而不是强迫普通任务进入重链路。

### 3.2 v1.1 validation semantics

v1.1 修正“没验证成功却看起来 ready”的缺口：

- `timing_model_not_ready` -> review / Pro escalation；
- 无唯一 timed canonical mapping -> review / Pro escalation；
- C-grade identity -> review / Pro escalation；
- B-grade 只能由已经 ready 的 A-anchor model 做二次确认，不能反向参与建模；
- report schema = `smart-1.1`，新增 `pro_escalation_required` 与 validated-preserve 统计。

自动 repair 仍要求 leave-one-out independent model、足够左右 A anchors / rate-supported edge extrapolation、最大 shift guard。v1.1 额外增加 **no-new-overlap**：原 SRT 不重叠的相邻 cue 不得被 Smart 自动修成新的 overlap。

### 3.3 Rate provenance

report/model 现在区分：

```text
exact_daw
bpm_derived
anchor_estimated
```

这使后续 calibration 可以分别评估不同 prior 的可信度，不再把三个来源当作同一种 evidence。

## 4. Pro — Selective Audio Repair v1.1

Pro 是 Smart unresolved 的局部声学层，仍保持：

```text
timing_mutation_performed = false
```

实现：

```text
lyric_aligner/alignment/selective_repair.py
lyric_aligner/alignment/selective_policy.py
lyric_aligner/alignment/local_acoustic_match.py
lyric_aligner/alignment/local_acoustic_v11.py
scripts/v4_pro_selective.py
```

### 4.1 Reason-aware routing

v1 的 mapped review 曾同时请求 acoustic + ASR + forced。v1.1 改为按原因花计算：

- timing review + canonical identity -> local source↔mix acoustic first；
- text/identity review -> bounded ASR + word timestamps；
- 无 word-timed canonical 且 source-side identity 确实需要加强 -> external forced alignment；
- 已有 Enhanced LRC/QRC word timing 时避免重复 forced；
- unmapped review -> bounded ASR only，不伪造 source identity。

### 4.2 Merged local regions

相邻 Smart review cue 会被分配到 merged mix region。`local_acoustic_v11.py` 每个 region 只 decode / extract 一次 mix features，但每个 cue 仍保持独立 source window、canonical identity 与 retrieval result。

report/plan 同时记录：

```text
planned_mix_audio_ms_unmerged
planned_mix_audio_ms_merged
region_count
region_merge_saved_ms
```

### 4.3 Adaptive source windows

优先使用 word/token timing；没有逐字 timing 时利用下一 canonical line onset 推导窗口，减少短句宽搜并避免长 rap 被固定窗口截断；最后一行使用 bounded fallback。

### 4.4 Song-boundary dual-source evidence

位于歌曲首/尾两行的 timing review 可增加相邻歌曲 competitor：

```text
shadow_evidence_only = true
boundary_role = previous_source | next_source
```

competitor 只用于 join/crossfade 双源声学判断，不直接生成 timing mutation。

### 4.5 ASR / forced

mixed-language per-line routing继续有效：中文歌纯英文 rap -> `en`，code-switch -> auto，韩/日 pure line -> `ko/ja`。

`scripts/v4_pro_selective.py` 现在已能显式调用现有 external forced-alignment protocol；forced 仍是 auxiliary source-side evidence，canonical lyric 仍是最终文字/顺序 authority。

## 5. Max — Full V4 Alignment

Full V4 继续作为 heavy fallback：coarse/Fine/cut/transition/overlap/ASR/forced/P9 等完整链路用于整体 timing 不可信、复杂结构或 Smart/Pro 无法安全收敛的任务。

Max 不再是“只要需要改 timing 就先跑”的默认路径。

## 6. Legacy Partial Timeline Repair P1–P5

旧 formal calibration/P9 proposal chain 不被 Smart/Pro 替换，继续固定：

```text
proposal_only = true
publish_ready = false
automatic_timing_change_allowed = false
release_gate_eligible = false
```

Smart/Pro 不借用 P9 HIGH 或 P4 trust lock 获得新的自动修复权限，也不会反向提升旧 Partial chain authority。

## 7. 当前验证边界与下一步

Public CI 负责证明 deterministic contracts、Smart escalation/overlap safety、Pro reason routing/region reuse/boundary competitor、mixed-language routing、external forced orchestration compatibility、Python/ASR environment 与 legacy tests。

Private real-song calibration + independent blind 仍是 Pro 自动写回前的关键 gate：

```text
Smart + Pro real-song calibration/blind
-> false timing repair / false ready / acoustic false-match
-> Pro evidence fusion / confidence threshold
-> only then consider automatic Pro timing writeback
-> evidence-triggered piecewise only if real failures justify it
```
