# Lyric Aligner v4 实施记录与关键代码说明

> P3 前完整实施/架构记录见 `references/archive/2026-08-19-pre-p3-v4-implementation.md`。真实生产 workload 的 normative baseline 见 `references/production-requirements.md`。

## 1. Responsibility graph

```text
Canonical lyric -> final text/order truth
Editor SRT      -> strong but rebuttable mix-time prior
Timed canonical -> Smart no-audio timing evidence
Source-to-Mix   -> Pro/Max acoustic timing truth
ASR / forced    -> auxiliary acoustic evidence
P9 fusion       -> legacy Partial shadow diagnostics
P4 trust lock   -> legacy Partial calibrated proposal eligibility
```

产品计算路径：

```text
Standard -> Smart -> Pro -> Max
```

禁止：

```text
ASR/forced text -> final canonical lyric
one cue -> prove its own timing model
rate change -> implicit cut
foreign-language label -> automatic Max
P9 HIGH -> Smart timing authority
rare piecewise case -> force all normal songs through heavy mapping
```

## 2. Standard / Text Repair V2.1

Text Repair 是 frozen-timeline 文字修复层：不读取 audio，不改变 cue count/number/start/end。它继续负责 deterministic canonical matching、文字纠错、layout-preserving render 与 fail-closed ambiguity guards。

V2.1 的主要安全事实保持：

- timestamped metadata 二次过滤；
- same-file timed/untimed lyric body 混合 fail closed；
- cue/whitespace/line-break boundary insertion fail closed；
- coverage warning 与 cue review 分离；
- unique exact anchors 使用 O(n log n) stable Fenwick chain；
- production `auto-threshold >= 0.72`；
- render 后重新断言 frozen timeline signature。

过去“凡 timing 任务都进入 Source-to-Mix”不再是主产品顺序。现在先进入 Smart；Smart 无法证明的局部问题进入 Pro，只有 broad failure 才进入 Max。

## 3. Smart / Anchor Timeline Repair v1

实现：

```text
lyric_aligner/timeline/anchor_repair.py
scripts/v4_smart_repair.py
```

Smart 与 legacy Partial Timeline Repair 是平行责任，不要求先跑 P3/P4/P9，也不读取 audio。

### 3.1 Canonical representation reuse

仓库原有 `lyric_aligner/text/canonical_lyrics.py` 已有：

```text
CanonicalLine.time_ms
CanonicalLine.tokens[]
CanonicalToken.start_ms/end_ms
line_lrc / enhanced_lrc / qrc_word_timing
```

Smart 直接复用它，而不是继续依赖 Text Repair 内部会丢 timing 的简化 canonical representation。

`parse_timed_canonical_files()` 同时生成：

1. `TimedCanonicalOccurrence`：保留 source identity、line timestamp、tokens、timing format；
2. Text Repair `CanonicalLine` view：只用于复用 V2.1 span alignment。

因此 Standard 不需要改变 parser contract，Smart 又能利用逐字时间。

`TimedCanonicalOccurrence.anchor_time_ms` 默认 line timestamp；当存在首 token 且它位于 line timestamp 附近的合理窗口时优先使用首 token onset。word/token end 目前只保留为 evidence，不直接决定 SRT end。

### 3.2 Text identity -> timing observation

Smart 先调用 `build_repair_plan_v2()`，得到 cue/canonical span 与 score/action，再建立 timing observations。

Identity grade：

```text
A: exact + unique + 1:1 + unchanged
B: unique 1:1 + high similarity + safe small text repair
C: merge/split/gap/repeated/ambiguous/other
```

A 才可建立 timing model。B/C 不能因为 Smart model 后来吻合就升级成建模证据，从结构上避免 circular proof。

### 3.3 Per-song affine model

普通生产事实是单曲大多统一 time-stretch，所以 v1 先做：

```text
source_time = offset + rate * mix_time
```

若没有 prior：

- 对 A anchors 计算相隔至少 3s 的 pairwise slopes；
- 取 median 作为 robust rate；
- 以 `source - rate*mix` 的 median 得 offset；
- 按 750ms residual 做 inlier selection；
- 无 prior 时用 inliers 再 refine 一次。

若有 exact `rate_prior`：固定 rate，只 robust 估 offset。若 anchors 自己估出的 rate 与 prior 相差超过 3%，模型标记 `rate_prior_conflict`，不自动 timing repair。

BPM only 时：

```text
rate_prior = target_bpm / source_bpm
```

如果 DAW 提供 exact stretch ratio，应直接传 exact ratio，优先于 BPM 派生。

### 3.4 Model readiness

首版：

```text
minimum A anchors = 4
inlier residual threshold = 750ms
ready if median abs residual <= 450ms
      and inlier fraction >= 0.70
```

否则 `insufficient_anchors / insufficient_rate_evidence / unstable / rate_prior_conflict`，保持 editor timing 并升级 review/Pro，而不是猜。

### 3.5 Leave-one-out outlier decision

A cue 自身若参加全局模型，不能用该模型直接证明自己错误。因此判断 A candidate 时：

```text
remove candidate
-> refit independent A-anchor model
-> predict candidate source timestamp back to mix time
-> compare editor start
```

v1：

```text
abs residual <= 350ms -> preserve
350..900ms            -> preserve（不足以推翻 editor）
>= 900ms               -> repair candidate
> 8000ms shift          -> review
```

真正 auto repair 还需：

- interior：左侧至少 2 个、右侧至少 2 个独立 A anchors；
- start/end extrapolation：必须有 rate prior + 单侧至少 3 个 A anchors；
- proposed timing 通过 structural guard，不逆序、不越过邻居的基本时间结构。

v1 保留原 cue duration，只整体平移 start/end。这样不把 canonical karaoke token duration 直接强加给字幕显示时长。

### 3.6 Piecewise behavior

同一首歌多速度段是 minority case。v1 **不自动拟合 piecewise**；single-rate model 被证据推翻时会 unstable/conflict 并升级。

未来应先从 private real-song failures 证明需求，再增加：

- shared-offset break；
- piecewise-rate segment；
- cut-aware local handling。

原则仍是 `Affine first; piecewise on evidence`。

### 3.7 Multilingual behavior

Smart 的 exact canonical identity 与 affine timing math 本身不依赖中文 ASR，因此韩文/日文可以和中文一样走 Smart。语言仅在未来 Pro acoustic escalation 才影响 ASR/forced backend routing。

所以 40 分钟大量韩日文并不自动等于 Max。若 canonical lyrics 和 editor matching 足够，Smart 可以不听音频完成大部分工作；只有多数 cue identity/timing 都无法建立、或局部 acoustic escalation 仍大量失败时才需要 Max。

### 3.8 CLI/report

`scripts/v4_smart_repair.py`：

- source SRT 与 output SRT 必须不同路径；
- canonical files 按 mix/song 顺序传入；
- `--rate-prior SOURCE=RATIO` 支持 exact ratio；
- `--target-bpm` + `--source-bpm SOURCE=BPM` 支持 BPM-derived prior；
- exact rate prior 对同 source 优先；
- production text threshold 不得低于 0.72；
- non-finite numeric args fail closed；
- diagnostic Infinity 在 JSON 输出层正规化为 null，report 始终 strict JSON。

report schema：`smart-1.0`。

## 4. Pro / Selective Audio Repair direction

下一阶段应把 Smart unresolved cue groups 转成 bounded acoustic jobs，而不是让用户重新跑全程：

```text
Smart review/escalation
-> bounded mix window
-> expected source position from Smart model
-> narrow rate search around prior/model
-> source<->mix local acoustic match
-> canonical-constrained forced alignment
-> ASR only where useful
```

混合语言的 ASR job 必须携带 per-span/per-job language hint：中文 span `zh`，英文 span `en`，code-switch/uncertain 用 auto，而不是整首 track language 强制覆盖。

## 5. Max / Full V4

Full V4 继续保留 coarse/Fine/cut/transition/overlap/ASR/forced/P9 等完整能力，处理 broad untrusted timeline 与复杂 source identity。它从“所有 timing 任务默认入口”调整为最后一档 heavy fallback。

## 6. Legacy Partial Timeline Repair P1–P5

旧 P1–P5 formal proposal chain 不删除：

- P1 explicit cue trust + structural candidate guards；
- P2 P9/editor/canonical identity bridge；
- P3 exact effective-run/fusion formal lineage；
- P4 strict calibration + independent blind trust lock/decision artifact；
- P5 Doctor/readiness。

该链路继续固定：

```text
proposal_only = true
publish_ready = false
automatic_timing_change_allowed = false
release_gate_eligible = false
```

这不与 Smart 的 deterministic A-anchor timing policy冲突，因为两者 authority 来源不同，不能互相提升。

## 7. Validation boundary

Public synthetic tests可以证明：

- Standard frozen-timeline contract；
- Smart exact/unique anchor grading；
- leave-one-out interior repair；
- start/end rate-prior requirement；
- repeated lyric fail-closed；
- Enhanced LRC token preservation；
- Japanese exact canonical Smart path；
- strict JSON / overwrite / threshold mechanics；
- legacy P3/P4/P5 formal validation 仍保持原契约。

Public CI 不能证明真实歌曲 false-auto rate，也不能证明当前 350/900/8000ms 常量是最终最佳生产阈值。扩大 B auto repair、piecewise auto repair 或更激进 timing mutation 前，必须用 private real-song calibration + independent blind 数据验证。
