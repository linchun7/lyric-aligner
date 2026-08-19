# Lyric Aligner v4 实施记录与关键代码说明

> 真实生产 workload 的 normative baseline 见 `references/production-requirements.md`。Smart / Pro v1.1 细节见 `references/smart-pro-v1-1.md`。

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
unvalidated preserve -> task ready
rate change -> implicit cut
foreign-language label -> automatic Max
boundary competitor -> direct timing mutation
rare piecewise case -> force all normal songs through heavy mapping
```

## 2. Standard / Text Repair V2.1

Standard 冻结 editor timeline，只做 deterministic canonical text repair。它不读取 audio，不改变 cue count/number/start/end；production text threshold floor = 0.72。

## 3. Smart / Anchor Timeline Repair v1.1

核心文件：

```text
lyric_aligner/timeline/anchor_repair.py
lyric_aligner/timeline/smart_policy.py
scripts/v4_smart_repair.py
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

无 prior 时由 A anchors robust estimate；DAW exact ratio 或 BPM-derived prior 可固定/加强 rate。single-rate 不成立时升级，不自动转 heavy piecewise。

### 3.3 Anti-circular + v1.1 readiness

候选 A cue 必须 leave-one-out 后重新建 independent model，再判断自身 residual。

v1.1 新增 production semantics：

- `timing_model_not_ready` 不再当“普通 preserve”；
- 无唯一 timed canonical mapping 不再让任务假 ready；
- C-grade identity 不声明 timing validated；
- 以上统一进入 `review` + `pro_escalation_required`；
- B 不参与建模，只能被已经 ready 的 A-anchor model 二次确认。

### 3.4 Timing mutation safety

v1 原有左右 anchor、edge prior、shift limit、monotonic/neighbor guard继续有效。`smart_policy.py` 再加 no-new-overlap guard：如果 editor 原本两 cue 不重叠，Smart repair 不能制造新 overlap。

### 3.5 Rate provenance

CLI/report 区分：

```text
exact_daw     -> --rate-prior
bpm_derived   -> --target-bpm + --source-bpm
anchor_estimated -> no external rate prior
```

report schema = `smart-1.1`。

## 4. Pro / Selective Audio Repair v1.1

核心文件：

```text
lyric_aligner/alignment/selective_repair.py
lyric_aligner/alignment/selective_policy.py
lyric_aligner/alignment/local_acoustic_match.py
lyric_aligner/alignment/local_acoustic_v11.py
scripts/v4_pro_selective.py
```

Pro 仍是 staged evidence path：

```text
Smart unresolved
-> reason-aware bounded plan
-> selected local acoustic / ASR / forced evidence
-> review/calibration
```

`timing_mutation_performed=false` 保持。

### 4.1 Base identity bridge

`selective_repair.py` 继续负责：

- 只选择 Smart review cue；
- 绑定 cue/canonical/source identity、canonical SHA、bounded windows、Smart ready rate；
- plan 不保存 raw canonical text；
- unmapped cue 不伪造 source identity。

### 4.2 Reason-aware capability routing

`selective_policy.py` 在 base plan 上收窄能力：

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

这样避免普通 timing 疑点同时支付 ASR + acoustic + forced 三套成本。

### 4.3 Adaptive source windows

`_adaptive_source_window()`：

1. 有 token timing：以 token extent + small context 建 source window；
2. 无 token：利用下一 canonical onset 推导 lyric span；
3. 最后一行：bounded fallback。

目标是短句少搜、长 rap 不截断。

### 4.4 Merged mix regions

相邻 Pro jobs 按 `region_merge_gap_ms` 合并成 region，job 保持独立：

```text
job.mix_window_ms          -> cue-specific retrieval query
job.region_mix_window_ms   -> shared decode/feature scope
job.region_id              -> reuse identity
```

`local_acoustic_v11.py` 每个 region 只做一次 mix decode + harmonic feature extraction，然后对该 region 内多个 cue 分别做 source retrieval。

### 4.5 Song-boundary dual-source competitor

当 timing-review occurrence 位于一首歌首/尾两行，planner 可从相邻 source 生成一个 acoustic-only competitor：

```text
shadow_evidence_only = true
boundary_competitor_for_job_id = <primary>
boundary_role = previous_source | next_source
```

competitor 只用于判断 join/crossfade local window 更支持哪一首 source，不直接产生 mutation authority。

### 4.6 Mixed-language ASR

per-line language hint继续优先于 whole-track profile：纯英文 rap in Chinese track -> `en`；真实 code-switch -> auto；纯韩/日 -> `ko/ja`。

### 4.7 External forced alignment integration

`scripts/v4_pro_selective.py` v1.1 现在可调用现有：

```text
ExternalForcedAlignmentConfig
execute_external_forced_alignment_jobs()
```

CLI 通过显式 command/backend/model identity 配置，不隐式寻找 backend。Pro 根据同一 canonical + source audio 生成严格 source binding；forced response仍受既有 protocol/version/hash/window validation约束。

Forced evidence 是 auxiliary source-side evidence，不改变 canonical text authority，也不直接改 SRT。

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
- no-new-overlap auto repair guard；
- rate provenance；
- Pro reason-aware routing；
- merged-region mix feature reuse；
- boundary competitor只为 shadow evidence；
- existing forced protocol可由 standalone Pro orchestration调用；
- mixed-language routing、privacy、Python/ASR environment与 legacy tests不回归。

Public CI仍不能证明真实歌曲 false-auto / false acoustic match。Pro evidence fusion 与自动 timing writeback必须等 private real-song calibration + independent blind。
