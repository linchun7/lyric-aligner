# Lyric Aligner v4 关键变更记录

> P3 前完整历史保存在 `references/archive/2026-08-19-pre-p3-v4-change-record.md`。当前生产设计基线见 `references/production-requirements.md`。

## 当前产品责任分层

```text
Standard = Text Repair V2.1
Smart    = Anchor Timeline Repair（no-audio）
Pro      = Selective Audio Repair（局部 audio）
Max      = Full V4 Alignment（完整 audio）
```

共同 authority：

```text
Canonical lyric -> final text/order truth
Jianying timing -> strong but rebuttable prior
Timed canonical -> primary no-audio timing evidence for Smart
Source-to-Mix   -> primary acoustic timing truth for Pro/Max
ASR / forced    -> auxiliary acoustic evidence
```

旧 Partial Timeline Repair P1–P5 的 `proposal_only / automatic_timing_change_allowed=false / release_gate_eligible=false` 约束继续保持，但这些 flags **只约束该 calibration/P9 production bridge**。Smart/Pro 是独立的 staged production path；Pro v1/v1.1 只生成/执行局部 evidence，不因此获得自动 timing write-back 权限。

---

## 2026-08-19 — Smart / Pro v1.1.1 repair-only收口

本轮只修复 review 中发现的生产 bug / 回归风险，不增加新算法、不放宽阈值、不开放 Pro timing write-back：

- Smart 对全部自动 timing repair 生成最终组合时间轴后再次检查 overlap；禁止新 overlap，也禁止扩大编辑器原本已有的 overlap；
- `bpm_derived` 不再与 `exact_daw` 一样硬锁 rate：DAW 精确倍率可继续作为 hard prior，BPM 推导值只做 soft plausibility；若与稳定 A-anchor rate 冲突，只阻止自动 mutation，不用软先验推翻已验证 preserve；
- Enhanced LRC 最后 token 的合法 `end_ms=None` 不再导致 Smart→Pro 基础 planner 在 source window 计算阶段崩溃；
- Pro v1.1 只接受当前 `smart-1.1` schema + 当前 Smart policy，旧 Smart artifact 必须重新跑 Smart，避免旧 `false-ready` 语义漏掉 Pro escalation；
- adaptive source window 现在至少覆盖 `mix query duration × 最大候选 slope + frame margin`，避免窗口短于 acoustic query 而产生零候选；
- acoustic region 只合并真正请求 `source_local_acoustic_match` 的 jobs，ASR-only job 不再无意义扩大 acoustic decode/feature region；
- `max_jobs` 现在约束最终 job 总数（包含 shadow competitor），summary 明确 primary/competitor/omitted 数量；
- Smart/Pro 所有 artifact output 增加统一路径碰撞 guard，禁止覆盖 source SRT、canonical lyrics、mix/source audio 或其他输出 artifact；
- Pro 只 hash/bind 当前 plan 实际使用到的 source audio，避免 40–60 分钟任务中对无关原曲做整文件 I/O。

新增 regression tests 覆盖 open-ended Enhanced LRC、组合 overlap、existing-overlap worsening、soft BPM prior、stale Smart rejection、acoustic window 最小长度、ASR-only region isolation、max-jobs total cap 与 artifact path collision。

## 2026-08-19 — Smart / Pro v1.1 daily-production hardening

Smart 新增 `timeline/smart_policy.py`，保留 v1 A-anchor affine engine，但修正 production semantics：

- `timing_model_not_ready`、无唯一 timed-canonical identity、C-grade identity 不再以普通 `preserve` 让任务看起来 `ready`，而是明确 `review` 并设置 `pro_escalation_required=true`；
- B-grade 仍不得建立 timing model，只允许由已经 ready 的 A-anchor model 做二次确认；
- timing repair 新增 no-new-overlap guard：原本不重叠的相邻 cue 不得被 Smart 自动修成新的 overlap；
- Smart report schema 升到 `smart-1.1`，新增 validated preserve / escalation 语义；
- rate prior 记录 provenance：`exact_daw / bpm_derived / anchor_estimated`，避免把 DAW 精确倍率与 BPM 推导值混成同等证据。

Pro 新增 `alignment/selective_policy.py` 与 `alignment/local_acoustic_v11.py`：

- 从“mapped review 一律 ASR + acoustic + forced”改成 reason-aware routing：timing review 优先 local source↔mix acoustic；text/identity review 才优先 ASR；无逐字 timing 且 source-side identity 确实需要加强时才请求 forced alignment；
- 相邻 review cue 合并成 mix regions；声学执行对每个 region 只 decode / extract mix features 一次，但仍保留每条 cue 独立 source query；
- source window 优先利用 Enhanced LRC/QRC token timing；否则利用下一行 canonical onset 自适应，减少短句无意义宽搜并避免长 rap 被固定 5s 窗口截断；
- 歌曲首尾两行 timing review 可增加相邻歌曲的 shadow acoustic competitor，用于 join/crossfade 双源判断；competitor 明确 `shadow_evidence_only=true`，不能直接改 timing；
- `scripts/v4_pro_selective.py` 现在可显式调用既有 external forced-alignment protocol，仍属于 auxiliary evidence；
- Pro v1.1 继续 `timing_mutation_performed=false`，自动写回必须等待 private real-song calibration + independent blind。

新增测试覆盖：Smart 未验证状态升级、no-new-overlap guard、rate provenance、Pro reason-aware routing、边界双源 competitor、merged-region mix feature reuse。

## 2026-08-19 — Pro / Selective Audio Repair v1 evidence bridge

新增 `lyric_aligner/alignment/selective_repair.py`：

- 只把 Smart report 中 `timing review` / `text review` cue 转成 Pro jobs；已经 preserve/repair 的正常 cue 不重复跑 audio；
- 每个 job 绑定 cue ordinal、canonical occurrence、source ordinal、canonical text SHA、Smart rate、editor cue time；plan 不保存 raw canonical text；
- mix window 默认仅 cue 前后各 2.5s（不足时扩到至少 4.5s）；
- 有 canonical identity 时按 LRC/逐字 token timing 建局部 source window；v1.1 再按 failure reason 收窄实际 capability；
- 无 canonical identity 的 Smart review 只能先请求 bounded mix ASR，并明确计入 unmapped escalation；
- 输出计划统计局部 mix audio 毫秒数，用于验证 Pro 是否真正只花局部计算量。

新增 `lyric_aligner/alignment/local_acoustic_match.py`：

- 复用 Full V4 已有 HPSS/Chroma CENS/MFCC retrieval，但只 decode Smart 选择的 mix/source bounded windows；
- 有 Smart rate 时默认只在 `rate ± 0.06` 的窄范围搜索；无 prior 才使用较宽但仍局部的 slope range；
- 从 local source match 反推出 canonical lyric onset 的 `predicted_mix_start_ms`，同时输出 score/margin/feature agreement/editor residual；
- Pro **只输出 acoustic timing evidence，不直接修改 SRT**，等待 private real-song calibration/blind 决定自动写回门槛。

新增 `scripts/v4_pro_selective.py`：

- 必需输入 Smart report + Smart SRT + 同一组 timed canonical lyrics；
- 默认只生成 Pro plan，仍然 0 audio execution；
- 可选执行 bounded source↔mix evidence、faster-whisper 以及 v1.1 external forced alignment；
- source language 可按 canonical filename/ordinal 提供，供局部 ASR routing 使用。

修正 mixed-language ASR routing：

- `text/language_spans.py` 新增 canonical-line 级 `asr_language_hint_for_text()`；
- `alignment/asr_executor.py` 优先使用 explicit job hint；没有 explicit hint 时根据当前 canonical line 重新判断；只有缺少 canonical text 时才回退 whole-track profile；
- 因此中文 track 中的纯英文 rap line 使用 `en`，真正中英 code-switch 返回 `None/auto`，不再被全曲 `zh` 强制覆盖；
- 韩文/日文 pure local line 同样分别得到 `ko` / `ja`；语言标签仍不决定是否进入 Max。

## 2026-08-19 — Smart / Anchor Timeline Repair v1

新增生产基线 `references/production-requirements.md`，明确真实任务以“规范歌词齐全、剪映时间轴大部分可信、中文为主、单曲通常单一匀速变速”为主路径。韩文/日文或整段外文不因语言标签自动进入 Max；有可靠 canonical identity 时仍先走 Smart，局部困难再升级 Pro，只有整体 mapping/timeline 广泛不可信时进入 Max。

新增 `lyric_aligner/timeline/anchor_repair.py`：

- Smart 不读取 audio；先复用 Text Repair V2.1 的 deterministic cue↔canonical span alignment；
- canonical timing 改用 `text/canonical_lyrics.py`，保留 line timestamp，并自动保留 Enhanced LRC / QRC 的 word/token timing；
- 逐字 timing 作为更细的 onset/boundary evidence，不直接强迫 SRT display segmentation 等同于逐字歌词；
- cue identity 分 A/B/C：仅 original editor text 与 canonical 唯一、exact、1:1、monotonic-safe 的 A anchor 可建立主 timing model；
- 每首歌默认拟合 `source_time = offset + rate * mix_time`；无 rate prior 时以 A anchors 的 robust pairwise median slope 建模；有 exact stretch ratio 时直接作为强 prior；由 BPM 推导时 `rate_prior = target_bpm / source_bpm`；
- 单 cue 判断使用 leave-one-out 独立模型，避免 cue 用自己的 timing 证明自己；
- v1 初始安全边界：`preserve <= 350ms`、`repair >= 900ms`、单次 auto shift `<= 8000ms`；
- interior 自动 timing repair 要求至少左右各 2 个独立 A anchors；歌曲首/尾只允许在有 rate prior 且单侧至少 3 个 A anchors 时自动外推；
- v1 只做 dominant affine model；少量同歌多 rate、局部 cut 等升级而不是强迫普通歌曲走重链路。

## 2026-08-19 — Text Repair V2.1 hardening

Text Repair 继续保持 frozen-timeline text-only 责任：不读 audio，不改变 SRT cue count / number / start / end。

- canonical parser 在移除 LRC/QRC 时间标签后再次过滤 metadata；
- 同一 canonical 文件 timed/untimed lyric body 混合 fail closed；
- 字符插入落 cue/whitespace/line-break boundary 时 review；
- unmatched canonical line 与 cue-level review 分离；
- unique exact anchor chain 从 O(n²) 改为 O(n log n) Fenwick；
- production `auto-threshold` floor = 0.72；
- report schema = 2.1，并在输出后重新断言 timeline signature 完全不变。

## 2026-08-19 — Partial Timeline Repair P1–P5

P1：`partial_repair.py` 建立 explicit trusted/untrusted/unknown、trusted hard lock、Source-to-Mix-only candidate、AFFINE/PIECEWISE_RATE/CUT_AWARE 与 neighbor/overlap guards。

P2：`partial_repair_evidence.py` 保持 P9 shadow-only；P9 HIGH 不自动 trusted，CONFLICT 不自动 untrusted；editor cue 必须唯一 canonical identity。

P3：`partial_repair_context.py` / `partial_repair_production.py` 验证 exact effective-run + fusion formal lineage，从 coarse/Fine/cut lineage 派生 mapping；CUT_AWARE 只认 materialized cut lineage。

P4：`partial_repair_trust.py` / `partial_repair_trust_production.py` 复用 strict calibration + independent blind；formal decisions 绑定 exact lock/candidate/runtime/fusion identity，P9 CONFLICT 不得自动提升。

P5：`partial_repair_readiness.py` / `doctor_partial.py` 提供 read-only readiness。该链路继续固定：

```text
proposal_only = true
publish_ready = false
automatic_timing_change_allowed = false
release_gate_eligible = false
```
