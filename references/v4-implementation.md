# Lyric Aligner v4 实施记录与关键代码说明

> 真实生产 workload 的 normative baseline 见 `references/production-requirements.md`。Smart / Pro v1.1 设计细节见 `references/smart-pro-v1-1.md`。

## 1. Responsibility graph

```text
Canonical lyric -> final text/order truth
Editor SRT      -> strong but rebuttable mix-time prior
Timed canonical -> Smart no-audio timing evidence
Source-to-Mix   -> Pro/Max primary acoustic timing truth
ASR / forced    -> auxiliary evidence
P9 fusion       -> legacy Partial shadow diagnostics
P4 trust lock   -> legacy Partial calibrated proposal eligibility
```

产品路径：

```text
Standard -> Smart -> Pro -> Max
```

禁止：

```text
ASR/forced text -> final canonical lyric
one cue -> prove its own timing model
text recovered from timing -> become a primary timing anchor
unvalidated preserve -> task ready
BPM-derived prior -> silently become exact DAW rate
combined repair -> worsen editor overlap
rate change -> implicit cut
foreign-language label -> automatic Max
boundary competitor -> direct timing mutation
output artifact -> overwrite any production input
rare piecewise case -> force all normal songs through heavy mapping
```

## 2. Standard / Text Repair V2.1

Standard 冻结 editor timeline，只做 deterministic canonical text repair。它不读取 audio，不改变 cue count/number/start/end；production text threshold floor = 0.72。

Text Repair V2 继续负责 lexical-first 的主文本匹配，包括 bounded 1↔N / N↔1 / N↔N segmentation span。它的 similarity、length-ratio、ambiguity、layout-boundary guards 不因 Smart v1.1.2 而降低。严重 ASR 乱码如果无法通过这些 lexical guards，会先进入 review，再由 Smart 的独立 timing evidence 决定是否能够恢复 canonical text。

## 3. Smart / Anchor Timeline Repair v1.1.2

核心文件：

```text
lyric_aligner/timeline/anchor_repair.py
lyric_aligner/timeline/smart_policy.py
lyric_aligner/timeline/text_recovery.py
scripts/v4_smart_repair.py
lyric_aligner/io/path_safety.py
```

### 3.1 Canonical representation

Smart 复用 `text/canonical_lyrics.py`，保留：

```text
CanonicalLine.time_ms
CanonicalLine.tokens[]
CanonicalToken.start_ms/end_ms
line_lrc / enhanced_lrc / qrc_word_timing
```

`parse_timed_canonical_files()` 同时提供 timing-rich occurrence 与 Text Repair canonical view。首 token onset 在合理 line-local 范围内可作为更细 anchor onset；token end 暂不直接强迫 SRT end。

### 3.2 Identity / model

Identity grade：

```text
A: exact + unique + 1:1 + unchanged
B: unique 1:1 + high similarity + safe small text repair
C: merge/split/gap/repeated/ambiguous/other
```

只有 A 可建立主 timing model。普通单曲仍使用：

```text
source_time = offset + rate * mix_time
```

v1.1.2 继续把 prior authority 落实到计算层：

- `exact_daw` 进入 `build_anchor_timing_plan()` 的 hard `rate_prior_by_source`；
- `bpm_derived` 不进入 hard-prior map，由 A anchors robust estimate rate；
- BPM-derived value 只在 `smart_policy.py` 做兼容性检查；相对差异超过 3% 时阻止自动 repair，但不让 soft prior 自己成为新的 timing truth；
- 没有 external hard prior 时继续使用 `robust_anchor_estimate`。

report 把实际模型 rate 来源与外部 prior 来源分开记录：

```text
rate_provenance
rate_prior_provenance
rate_prior_value
bpm_prior_relative_error
bpm_prior_compatible
```

### 3.3 Anti-circular + readiness

候选 A cue 必须 leave-one-out 后重新建 independent model，再判断自身 residual。

- `timing_model_not_ready` 不当普通 preserve；
- 无唯一 timed canonical mapping 不让任务假 ready；
- C-grade identity 不声明 timing validated；
- 以上统一进入 `review` + `pro_escalation_required`；
- B 不参与建模，只能被 already-ready A-anchor model 二次确认。

### 3.4 Severe-ASR canonical text recovery

`timeline/text_recovery.py` 只负责一个很窄的第二阶段职责：**当 Text Repair V2 因 editor ASR 严重错误而 lexical review，但 canonical order + 已独立建立的 ready affine model 已经可以证明中间歌词时，恢复 canonical text，而不是保留已知错误的 editor text。**

执行顺序固定：

```text
Text Repair V2 lexical plan
    -> original A-grade decisions only
    -> build initial affine timing model
    -> text_recovery revisits bounded review blocks
    -> materialize recovered canonical text
    -> rebuild final timing plan/report
```

重要的 anti-circularity：

```text
review/recovered cue
    X-> build initial timing model
    X-> become A anchor because text was recovered
```

也就是说，第二阶段只能消费已经由独立 A anchors 建好的 model；它不能把自己的恢复结果倒灌回“证明这个 model 成立”的证据集合。最终 timing plan 即使重新计算，multi-line / low-sim recovered cue 仍是非 A identity，不获得新的 timing auto-write 权限。

一个 review block 只有同时满足以下条件才允许自动恢复文字：

1. block 是 interior block，左右都存在 `score >= 0.92`、single-line canonical span 的非-review text anchor；
2. 左右 anchors 属于同一 `source_ordinal`；
3. 该 source 的 initial affine model 已经 `ready`；
4. 左右 anchor 的 editor cue start 与 model 映射出的 canonical onset 各自误差 `<= 750ms`；
5. 两侧 anchors 之间存在非空、连续、同源 canonical gap；
6. review cue starts 单调，canonical predicted onsets 也单调；
7. canonical gap 能完整分配给全部 review cues，每个 cue 的首个 canonical onset 与其 editor start 误差 `<= 750ms`；
8. 单 cue 最多吸收 4 条连续 canonical lines，单 block 最多 8 cues；
9. 所有 canonical gap lines 必须被消费，不能静默丢行或剩行。

明确不自动恢复：

```text
歌曲首/尾只有单侧 anchor
cross-song block
canonical gap = 0 的 ad-lib / editor-only cue
initial affine model not ready
anchor/model 或 cue/model timing 不一致
non-monotonic cue/canonical order
超出 bounded span 上限
```

恢复后的 `MatchDecision.reason` 为：

```text
timing_model_confirms_canonical_sequence
```

Smart report 新增：

```text
text_review_count_before_timing_recovery
text_timing_recovery_count
text_timing_recovery_block_count
text_review_count
```

这把“文字是否能由 canonical truth 确定”和“该 cue timing 是否已经安全验证”拆成两个独立维度：文字可以先修正确，而 timing 仍可继续 review/Pro。

### 3.5 Final timeline overlap guard

`smart_policy.py` 有两层 guard：

1. `_creates_new_overlap()`：单条 proposal 不得制造原本不存在的新 overlap；
2. `_harden_combined_timeline()`：把当前仍为 `repair` 的所有 proposal 组合成最终 timeline，逐相邻 pair 比较：

```text
new_overlap_ms <= original_overlap_ms
```

如果组合后 overlap 增大，涉及该 pair 的自动 repair 全部降级 `review`。因此同时移动两条相邻 cue 时，不会因为“各自只对原始邻居检查”而漏掉组合冲突。

### 3.6 Artifact path safety

`io/path_safety.py::validate_separate_artifact_paths()` 对路径做 `expanduser().resolve(strict=False)` 后检查：

- output 不得等于任何 input；
- 两个 output 不得共享同一路径。

Smart CLI 在任何 artifact write 前一次性检查 source SRT、canonical lyrics、output SRT、report。

## 4. Pro / Selective Audio Repair v1.1.1

核心文件：

```text
lyric_aligner/alignment/selective_repair.py
lyric_aligner/alignment/selective_policy.py
lyric_aligner/alignment/local_acoustic_match.py
lyric_aligner/alignment/local_acoustic_v11.py
scripts/v4_pro_selective.py
lyric_aligner/io/path_safety.py
```

Pro 仍是 staged evidence path：

```text
Smart unresolved
-> reason-aware bounded plan
-> selected local acoustic / ASR / forced evidence
-> review/calibration
```

`timing_mutation_performed=false` 保持。

### 4.1 Exact Smart policy binding

`build_selective_repair_plan_v11()` 在进入 base planner 前验证：

```text
smart_report.schema_version == SMART_SCHEMA_VERSION
smart_report.policy_id      == SMART_POLICY_ID
```

因此 Smart policy 从 v1.1.1 更新为 v1.1.2 后，旧 Smart report 自动成为 stale artifact，必须重新执行当前 Smart；不需要 Pro 单独维护另一份 Smart policy 字符串。

### 4.2 Enhanced LRC compatibility bridge

Enhanced LRC 最后 token 合法地可能：

```text
CanonicalToken.end_ms = None
```

旧 base planner `_source_window()` 会把 token end 直接放入 `max()`。v1.1.1 在调用 base planner 时构建仅用于兼容窗口计算的 canonical view：对 open-ended token 临时使用 `start_ms` 作为 end；真实 token 结构随后仍由 v1.1 adaptive planner 使用，不修改 canonical truth。

### 4.3 Reason-aware capability routing

```text
timing review + mapped canonical
    -> source_local_acoustic_match

text/identity review
    -> mix_asr + word_timestamps

no word timing + source identity needs reinforcement
    -> source_forced_alignment

unmapped review
    -> mix_asr + word_timestamps only
```

如果 Smart v1.1.2 已经通过 canonical order + timing model 安全恢复了文字，但该 cue 仍有 timing review，Pro 只按剩余 timing reason 路由，不应因为已解决的 text review 再重复请求 text-ASR evidence。

### 4.4 Adaptive source-window minimum

`_adaptive_source_window()` 先按 token extent / next onset / final fallback 建语义窗口，然后计算当前 acoustic query 真正要求的最低 source span：

```text
minimum_source_span_ms
= ceil(mix_query_duration_ms * max_candidate_slope) + 750ms
```

- 有 ready Smart rate：`max_candidate_slope = rate + slope_radius`；
- 无 ready rate：使用 `LocalAcousticMatchConfig.no_prior_max_slope`。

窗口不足时向两侧补齐（0 边界不足部分补到右侧）。这样 `retrieve_coarse_window()` 不会因为 source bundle 比 query×slope 更短而产生零候选。

### 4.5 Acoustic-only region reuse

`_assign_regions()` 只对包含 `source_local_acoustic_match` 的 jobs 做 merge。ASR-only jobs 获得自己的 local region，不参与 acoustic region 宽度计算。

这保持：

```text
job.mix_window_ms        -> cue/backend-specific query
acoustic region          -> only shared acoustic decode/features
ASR-only window          -> does not widen acoustic work
```

plan 新增/明确：

```text
job_count
primary_job_count
boundary_competitor_job_count
boundary_competitor_omitted_due_to_max_jobs
max_jobs_applies_to = total_jobs_including_shadow_competitors
acoustic_region_count
planned_acoustic_mix_audio_ms_unmerged
planned_acoustic_mix_audio_ms_merged
```

shadow competitor 只使用 `max_jobs - primary_job_count` 的剩余额度，最终 `len(plan.jobs) <= max_jobs`。

### 4.6 Song-boundary / language / forced authority

boundary competitor 继续：

```text
shadow_evidence_only = true
```

per-line language hint继续优先于 whole-track profile。External forced alignment 仍使用显式 command/backend/model identity，response 继续受既有 protocol/version/hash/window validation 约束。

### 4.7 Pro path and source-I/O safety

Pro CLI 在写 plan 前已经知道所有可选 source/mix inputs，因此一次性运行 path collision guard，覆盖：

```text
smart report / smart SRT
canonical lyrics
mix audio
source audios
plan / acoustic / ASR / forced outputs
```

Acoustic evidence执行时先从 plan 提取真正请求 `source_local_acoustic_match` 的 source ordinals，只向 executor 传这些 source paths，并只 hash 它们。Forced binding 同理只为 `source_forced_alignment` jobs 建 binding/hash。

## 5. Max / Full V4

Max 保留 coarse/Fine/cut/transition/overlap/ASR/forced/P9 等完整能力，只处理 broad untrusted timeline 与复杂 source identity。Smart/Pro 日常链路不能因为 rare case 被迫退化成 Max 的成本结构。

## 6. Legacy Partial Timeline Repair P1–P5

旧 formal proposal chain继续固定：

```text
proposal_only = true
publish_ready = false
automatic_timing_change_allowed = false
release_gate_eligible = false
```

Smart/Pro 与旧 P9/P4 authority 来源独立，不能互相提升。

## 7. Validation boundary

Public tests应证明：

- Smart no-audio / A-anchor / leave-one-out contracts；
- insufficient/unvalidated timing 必须 Pro escalation；
- severe-ASR low-sim text 只有在 bilateral canonical anchors + independently-ready affine model + compatible LRC onset 同时成立时才恢复；
- text recovery 不降低 Text Repair V2 threshold、不处理 edge/cross-song/ad-lib、不把 recovered cue 升为 A timing anchor；
- final combined overlap 不得新增/扩大 overlap；
- exact DAW hard prior 与 BPM-derived soft prior；
- Enhanced LRC open-ended final token 可进入 Pro；
- stale Smart report 被 Pro fail closed；
- acoustic source window 长度覆盖 planned slope search；
- ASR-only jobs 不扩大 acoustic region；
- final max-jobs cap；
- Smart/Pro artifact path collision fail closed；
- existing forced protocol可由 standalone Pro orchestration调用；
- mixed-language routing、privacy、Python/ASR environment与 legacy tests不回归。

Public CI仍不能证明真实歌曲 false-auto / false acoustic match。Pro evidence fusion 与自动 timing writeback必须等 private real-song calibration + independent blind。
