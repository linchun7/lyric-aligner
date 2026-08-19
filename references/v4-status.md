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

## 3. Smart — Anchor Timeline Repair v1.1.2

Smart 是日常主力 no-audio 模式：**大部分信剪映 timing，但 canonical lyric 始终是最终文字/顺序 truth。**

实现：

```text
lyric_aligner/timeline/anchor_repair.py
lyric_aligner/timeline/smart_policy.py
lyric_aligner/timeline/text_recovery.py
scripts/v4_smart_repair.py
```

### 3.1 Canonical timing / anchors

Smart 保留 line-level LRC timestamp、Enhanced LRC/QRC word/token timestamp。A/B/C identity 分级不变：只有 A-grade exact/unique/1:1 editor identity 可建立主 timing model；B/C 不得建立模型。

每首歌默认：

```text
source_time = offset + rate * mix_time
```

单倍率优先。v1.1.2 继续明确区分 rate authority：

```text
exact_daw     -> hard prior，可固定 rate
bpm_derived   -> soft plausibility，不硬锁 rate
anchor_estimated -> 无 hard prior 时由 A anchors robust estimate
```

BPM-derived 与稳定 A-anchor rate 冲突时，不用软先验重建模型；但该冲突会阻止自动 timing mutation。少量同歌多 rate / cut 仍升级而不是强迫普通任务进入重链路。

### 3.2 Canonical text recovery for severe ASR errors

v1.1.2 修正一个真实生产缺口：当 Jianying 把整句识别成完全不同的文字时，Text Repair V2 的相似度保护会正确进入 review，但旧 Smart 会把**已知错误的 editor text 原样留在输出 SRT**。这会把“timing/identity 仍需验证”错误地等同于“文字也不能修”。

现在 Smart 保留 Text Repair V2 原阈值，不通过降阈值解决。它先用原有高可信 A anchors 建立 ready affine model，然后只对满足以下全部条件的 interior text-review block 做第二阶段恢复：

- review block 两侧都有 `score >= 0.92` 的 single-line canonical text anchors；
- 两侧 anchors 属于同一首歌；
- 该歌曲 affine model 已由独立 A anchors 达到 `ready`；
- 两侧 anchor 自身 onset 与 affine model 在 750ms 内一致；
- 两侧 canonical anchors 之间的连续规范歌词可以完整、单调地分配到 review cue starts；
- 每个 review cue 的第一条 canonical onset 与 editor cue start 在 750ms 内一致；
- 每 cue 最多吸收 4 条连续 canonical lines，单个 recovery block 最多 8 cues；
- 不处理歌曲首尾单侧 block、不跨歌曲、不处理 canonical gap 为 0 的 unmatched ad-lib，也不处理 timing/model 不一致的 block。

满足条件时，Smart 用 canonical text 替换 editor ASR 乱码；**这只解决文字，不把低相似度 cue 升级为 A timing anchor，也不因此获得 timing 自动写回权限。** 多行 cue 的 timing identity 仍可继续 review / Pro escalation。

新增 report 字段：

```text
text_review_count_before_timing_recovery
text_timing_recovery_count
text_timing_recovery_block_count
text_review_count
```

### 3.3 Validation / overlap safety

v1.1 修正“没验证成功却看起来 ready”的缺口：

- `timing_model_not_ready` -> review / Pro escalation；
- 无唯一 timed canonical mapping -> review / Pro escalation；
- C-grade identity -> review / Pro escalation；
- B-grade 只能由已经 ready 的 A-anchor model 做二次确认，不能反向参与建模；
- report schema 保持 `smart-1.1`，policy id 更新到当前 v1.1.2 修复策略。

自动 repair 仍要求 leave-one-out independent model、足够左右 A anchors / rate-supported edge extrapolation、最大 shift guard。v1.1.1 在单条 no-new-overlap 之外增加**最终组合时间轴复检**：

- 不得制造新 overlap；
- 不得扩大编辑器原本已有的 overlap；
- 两条分别看似安全、组合后发生冲突时，相关 repair 全部降级 review。

### 3.4 Output safety

Smart CLI 现在统一检查 `source SRT + canonical lyrics` 与 `output SRT + report` 的真实解析路径：任何 output-input 或 output-output 碰撞都在写文件前 fail closed，落实“原始输入永不覆盖”。

## 4. Pro — Selective Audio Repair v1.1.1

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

### 4.1 Exact Smart contract

Pro v1.1.1 只接受当前：

```text
schema_version = smart-1.1
policy_id      = current Smart production policy
```

旧 Smart artifact 必须重跑 Smart；不能让旧版本中“model not ready 但 preserve”的语义漏过 Pro escalation。Smart policy id 更新后 Pro 仍通过共享常量要求 exact-current artifact。

### 4.2 Reason-aware routing

- timing review + canonical identity -> local source↔mix acoustic first；
- text/identity review -> bounded ASR + word timestamps；
- 无 word-timed canonical 且 source-side identity 确实需要加强 -> external forced alignment；
- 已有 Enhanced LRC/QRC word timing 时避免重复 forced；
- unmapped review -> bounded ASR only，不伪造 source identity。

Enhanced LRC 最后 token 合法的 `end_ms=None` 现已兼容，不会再在基础 Pro planner 的 source-window 计算阶段崩溃。

### 4.3 Local region / source-window fixes

acoustic region 现在只由真正请求 `source_local_acoustic_match` 的 jobs 合并；ASR-only jobs 保持自己的 bounded window，不再扩大 acoustic decode region。

source window 仍优先利用 word/token timing 或下一 canonical onset，但还必须满足 acoustic search 的最低数学长度：

```text
source_window_duration
>= mix_query_duration × max_candidate_slope + frame_margin
```

因此不会因为 adaptive window 太短而产生 `coarse retrieval produced no candidates` 的伪失败。

plan 现在明确记录：

```text
job_count
primary_job_count
boundary_competitor_job_count
boundary_competitor_omitted_due_to_max_jobs
acoustic_region_count
planned_acoustic_mix_audio_ms_unmerged / merged
```

`max_jobs` 约束最终 jobs 总数，包括 shadow competitors。

### 4.4 Song-boundary / ASR / forced authority

歌曲首尾 dual-source competitor 仍是：

```text
shadow_evidence_only = true
```

mixed-language per-line routing继续有效：中文歌纯英文 rap -> `en`，code-switch -> auto，韩/日 pure line -> `ko/ja`。

external forced alignment 仍是 auxiliary source-side evidence，canonical lyric 仍是最终文字/顺序 authority。

### 4.5 Output/I/O safety

Pro CLI 在写任何 plan/acoustic/ASR/forced artifact 前检查：

- Smart report / Smart SRT；
- canonical lyrics；
- mix audio；
- source audios；
- 所有 output artifacts。

任何路径碰撞 fail closed。Acoustic/forced 阶段也只 hash/bind 当前 plan 真正需要的 source ordinal，避免对未使用原曲做整文件 I/O。

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

Public CI 需要证明：

- Smart escalation + final overlap safety；
- severe-ASR text review 只有在 bilateral canonical anchors + ready timing model 同时成立时才可恢复 canonical text；
- recovery 不降低 Text Repair threshold、不把 recovered text 变成 A timing anchor；
- exact DAW hard prior / BPM-derived soft prior 语义；
- Enhanced LRC open-ended token 可进入 Pro；
- Pro 拒绝 stale Smart artifact；
- adaptive source window 足够支持 planned slope search；
- ASR-only region 不扩大 acoustic decode；
- max-jobs、path collision、source-I/O 收口；
- Python/ASR environment 与 legacy tests 全部继续通过。

Private real-song calibration + independent blind 仍是 Pro 自动写回前的关键 gate：

```text
Smart + Pro real-song calibration/blind
-> false timing repair / false ready / acoustic false-match
-> Pro evidence fusion / confidence threshold
-> only then consider automatic Pro timing writeback
-> evidence-triggered piecewise only if real failures justify it
```
