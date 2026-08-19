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

Smart 的 exact canonical identity 与 affine timing math 本身不依赖中文 ASR，因此韩文/日文可以和中文一样走 Smart。

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

## 4. Pro / Selective Audio Repair v1

实现：

```text
lyric_aligner/alignment/selective_repair.py
lyric_aligner/alignment/local_acoustic_match.py
scripts/v4_pro_selective.py
```

Pro v1 已完成 Smart→局部音频 evidence bridge，但仍保持 `timing_mutation_performed=false`。这不是旧 Partial/P9 proposal chain 的延伸，而是新的 staged production path。

### 4.1 Smart review -> bounded jobs

`build_selective_repair_plan()` 只扫描 Smart `timing_decisions/text_decisions` 的 review cue：

```text
Smart preserve/repair -> no Pro job
Smart review          -> bounded Pro job
```

默认 mix window 为 editor cue 前后各 2500ms，太短时扩到至少 4500ms。mapped canonical occurrence 的 source window 以 line/token onset 为中心，默认向前 3500ms、向后 5000ms。

mapped job 包含：

```text
cue_ordinal
canonical_line_index
canonical_text_sha256
source_ordinal/source identity
mix_window_ms
source_window_ms
editor cue start/end
expected canonical source time
Smart model rate_prior
local asr_language_hint
requested_capabilities
```

plan 不保存 raw canonical text。执行 ASR/forced 时使用 `canonical_text_by_job_id()` 从同一 canonical 输入重新验证 SHA 后在内存中恢复文本。

没有单一 canonical identity 的 Smart review 不伪造 source mapping：它只能先执行 bounded `mix_asr + word_timestamps`，并计入 `unmapped_review_count`，必要时升级 Max/人工确认。

### 4.2 Bounded source↔mix acoustic evidence

`execute_local_source_match_jobs()` 复用既有：

```text
HPSS
-> Chroma CENS
-> MFCC
-> retrieve_coarse_window
```

但与 Full V4 broad coarse search 有三个关键区别：

1. mix 只 decode Smart 指定的几秒窗口；
2. source 只 decode canonical line 附近几秒窗口；
3. 有 Smart rate 时 slope 默认只搜索 `rate ± 0.06`，step 0.01。

没有 rate prior 时才使用 0.75–1.35、step 0.05 的 local fallback range。

retrieval 的 top candidate 给出 matched source start 和 estimated slope；由此将 canonical line/token onset 投回 mix：

```text
predicted_mix_start
= mix_window_start
+ (canonical_source_onset - matched_source_window_start) / estimated_slope
```

输出还保存 fused/chroma/mfcc score、feature agreement、margin、ambiguity、editor residual。

这些是 Pro 的独立 acoustic timing evidence；v1 不调用 SRT renderer，不直接 mutation。

### 4.3 Local language routing

`text/language_spans.py::asr_language_hint_for_text()` 根据当前 canonical line 的 language spans 产生本地 hint。

规则：

```text
one supported local language -> zh/en/ko/ja
mixed/code-switch/uncertain  -> None / ASR auto
```

`asr_executor.py::_job_language_hint()` 优先级：

```text
explicit job asr_language_hint
-> canonical line inference
-> whole-track language_profile only if canonical text unavailable
```

因此中文 track 的 all-English rap job 不再继承 `zh`；真正“中文 + English”同一行也不会被强行 pin 到任一语言。

这里故意不把 Han-only unknown 自动认成 zh：若 track language 未知且文字可能是日文汉字，保持 auto 比错误 pin 更安全。调用 Pro 时可按 source 提供 `zh/ja/ko/en` profile。

### 4.4 Pro CLI

`scripts/v4_pro_selective.py`：

```text
Smart report + Smart SRT + timed canonical
-> plan-out
```

可选执行：

```text
--mix-audio + --source-audio SOURCE=PATH + --acoustic-out
-> bounded source<->mix acoustic evidence

--mix-audio + --asr-model-id + --asr-out
-> bounded faster-whisper evidence only for planned jobs
```

source language/audio key 均可用 canonical filename 或 zero-based source ordinal。

external source forced alignment capability 已在 mapped job 计划中声明，但 standalone Pro CLI v1 尚未直接编排既有 external forced executor；这避免在还未完成 real-song calibration 前把 Pro orchestration 一次性做得过重。

### 4.5 Current authority boundary

Pro v1 可证明“只算哪里”和“局部声学证据如何产生”，但没有 public evidence 支持自动 write-back threshold。因此：

```text
Smart automatic repair -> only Smart's existing strict A-anchor policy
Pro acoustic/ASR       -> evidence only
Pro timing mutation    -> disabled pending private blind calibration
```

后续需要在真实歌曲上融合 Smart prediction + local source match + optional forced/ASR word timing，并测 false repair/false ready 后才开放自动写回。

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

这不与 Smart/Pro staged path 冲突，因为 authority 来源不同，不能互相提升。

## 7. Validation boundary

Public synthetic tests 可以证明：

- Standard frozen-timeline contract；
- Smart exact/unique anchor grading、leave-one-out、edge prior、word timing；
- Smart→Pro 只选择 unresolved cue；
- Pro plan 不泄漏 raw canonical text；
- English rap / code-switch local ASR routing；
- bounded acoustic result 能形成 independent predicted mix time 且不 mutation；
- Japanese/Korean 不因语言标签被强制 Max；
- strict JSON / overwrite / threshold mechanics；
- legacy P3/P4/P5 formal validation 仍保持原契约。

Public CI 不能证明真实歌曲 false-auto rate，也不能证明 Smart 350/900/8000ms 或 Pro 0.62/0.012 等常量是最终最佳生产阈值。扩大 B auto repair、piecewise auto repair、Pro evidence fusion 自动写回前，必须用 private real-song calibration + independent blind 数据验证。
