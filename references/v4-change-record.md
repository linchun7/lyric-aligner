# Lyric Aligner v4 关键变更记录

> P3 前完整历史保存在 `references/archive/2026-08-19-pre-p3-v4-change-record.md`。当前生产设计基线见 `references/production-requirements.md`。

## 当前产品责任分层

```text
Standard = Text Repair V2.1
Smart    = Canonical Sequence Reconciliation + Anchor Timeline Repair（no-audio）
Pro      = Selective Audio Repair（局部 audio）
Max      = Full V4 Alignment（完整 audio）
```

共同 authority：

```text
Canonical lyric -> final text/order truth
Jianying timing / cue boundary -> strong but rebuttable prior
LRC line break -> grouping/onset evidence, not final subtitle segmentation authority
Timed canonical -> primary no-audio timing evidence for Smart
Source-to-Mix   -> primary acoustic timing truth for Pro/Max
ASR / forced    -> auxiliary acoustic evidence
```

旧 Partial Timeline Repair P1–P5 的 `proposal_only / automatic_timing_change_allowed=false / release_gate_eligible=false` 约束继续保持，但这些 flags **只约束该 calibration/P9 production bridge**。Smart/Pro 是独立的 staged production path；Pro v1/v1.1 只生成/执行局部 evidence，不因此获得自动 timing write-back 权限。

---

## 2026-08-19 — Smart v1.2.0 canonical sequence reconciliation

多轮真实生产复核确认 v1.1.x 仍有一个结构性退化：Smart 虽然复用了 Text Repair V2，但 severe-ASR cue 的 canonical span 仍先受 lexical similarity gate 控制；当 editor ASR 已错成另一句话时，正确的 1↔N/N↔N span 反而最难建立。若一首歌因此只有 3 个 A timing anchors，主 affine model 会按设计保持 `insufficient_anchors`，后续 ready-model text recovery 也无法启动，形成：

```text
severe ASR -> span/identity review -> A anchors 不足
-> timing model not ready -> text recovery 无法启动
-> 错误 editor ASR 原样进入 Smart SRT
```

v1.2.0 不降低 Text Repair 的 0.72 production floor / multi-span safety threshold，也不降低主 timing model 的四-A gate。新增 `timeline/sequence_reconcile.py`，把“恢复 canonical 文字 identity”与“授予 timing authority”正式拆开：

- 建立独立 text-only `SequenceProjectionModel`；无 exact hard rate prior 时至少需要 3 个 unique exact A text anchors + 1 个 `score>=0.92` 的 A/B strong anchor，并要求 source/mix 有效跨度、robust rate、median residual 与 inlier fraction 同时稳定；
- exact DAW hard rate 可缩小 text projection 的 rate uncertainty，但 BPM-derived 继续只做 soft plausibility，不能硬锁；
- 两个 model-consistent strong anchors 夹住 weak/review region 时，按完整 canonical gap + projected line onsets + editor cue starts 做 bounded sequence partition；允许 4 个 editor cues 对应 8 条 LRC lines 等结构，但 cue count/start/end 不变；
- partition 的边界目标是下一 canonical onset 对下一 editor cue start，而不是把 LRC 行换行当 subtitle boundary；文本长度 ownership 仅作为次级 penalty；
- outermost strong anchor 外侧允许 cautious frontier walk；当前 cue onset 不匹配、editor time 不单调、遇到另一 strong anchor 或下一 boundary 明显断裂时立即停止，不跳过 cut/ad-lib 去追更远 LRC；
- frontier 的 multi-line assignment 额外要求一定 editor/canonical text similarity，防止仅凭时间把多条歌词塞进弱 cue；
- Sequence Projection 只可覆盖 weak/review text；纯 Standard-safe、没有 review 的 segmentation 区域不进入该层；
- sequence-projected `MatchDecision.score` 强制 cap 到 0.91，因此即使最终文字与 canonical 完全一致，也不能成为 A/B timing anchor；主 timing model 的 anchor_count 不因本轮 text recovery 增加，杜绝循环自证；
- v1.1.2/v1.1.3 已有的 independently-ready four-A timing text recovery 仍先执行；Sequence Projection 只补 ready-model recovery 无法 bootstrap/覆盖的区域；
- report schema 继续 `smart-1.1`，policy id 升为 `smart-validation-policy-2026-08-19-v1.2.0`，新增 sequence reconciliation/model 统计；Pro 的 exact Smart binding 因共享 policy id 自动拒绝旧 v1.1.3 report。

新增 public synthetic regression 覆盖：

- 3 个 A + 1 个 B 可恢复被 strong anchors 包围的 severe-ASR 4-cue/8-line canonical sequence；
- 同一案例 final Smart timing model 仍只有原始 3 个 A，保持 `insufficient_anchors`，证明 text projection 不倒灌 timing authority；
- 只有 2/3 个未确认 strong anchors 时 projection fail closed；
- 已由 Standard 安全建立的 editor segmentation 不被 Sequence Projection 重写；
- 既有 LRC-line-break segmentation regression、ready-model recovery、overlap/BPM/path/Pro stale-binding tests继续作为不回归门槛。

该重构的目标是把真实样本中旧轻量路径做对的“song identity + canonical order + editor cue order + timing projection”能力工程化，而不是将任何真实歌曲、cue、timestamp 或歌词写入生产代码/公开测试。

## 2026-08-19 — Smart v1.1.3 segmentation authority + song-edge text recovery

真实生产复核暴露两个可泛化缺口：

1. canonical LRC 与 editor SRT 的连续文字/顺序一致、但换行分组不同，较高模式不能把 LRC line break 错当成 final subtitle cue boundary，导致文字跨原本正确的 editor cue 搬移；
2. severe-ASR lyric 位于歌曲开头/结尾且前后夹有 editor-only ad-lib 时，v1.1.2 的 bilateral-only text recovery 会因缺少一侧紧邻强 anchor 而漏修，即使该歌曲已经有独立 ready affine model 且 lyric onset 与 editor cue 高度吻合。

本轮把这两类问题收口为 Smart v1.1.3 的通用生产合同，不写入歌曲/cue/timestamp hard-code，也不降低 Text Repair V2 阈值：

- 明确 `canonical lyric = text/order authority`，但 `canonical LRC line break != final subtitle cue segmentation authority`；
- Standard/Smart 对连续文字已经一致的 bounded span 保留 editor 原 cue ownership；没有更强 boundary evidence 时，高阶模式不得仅按 LRC 换行跨 cue 搬字；
- 新增能力单调性规则：Higher mode 可以增加证据、减少 review，但无更强反证时不得破坏 lower-mode 已安全成立的 text/cue ownership/timing；
- `timeline/text_recovery.py` 保留 v1.1.2 bilateral interior recovery，并增加严格的 song-edge one-sided recovery；
- one-sided recovery 只允许 source 首/尾 4 条 canonical rows，initial model 必须已由独立 A anchors `ready`；
- 可用一侧至少需要 2 条紧邻、canonical 连续、`score>=0.92` 的强 anchor，且各自与 model residual `<=750ms`；
- candidate predicted onset 与 editor cue start 使用更紧的 `<=500ms`；
- 只能跨过 `canonical_ordinal=None && canonical_span=None` 的真正 unmapped review cue，作为 editor-only ad-lib；最多 3 条；任何 weak mapped cue 都阻断 one-sided recovery；
- ad-lib 本身保持原文和 review；recovered lyric text 不升级为 A timing anchor，也不获得 timing 自动写回权限；
- Smart report 新增 `text_edge_timing_recovery_count` 与 `text_edge_timing_recovery_block_count`；schema 继续 `smart-1.1`，policy id 升为 v1.1.3；
- Pro 继续通过共享 `SMART_POLICY_ID` 精确绑定当前 Smart，因此旧 v1.1.2 report 自动 stale，必须重跑 Smart。

新增 public regression 使用**合成同构文本**覆盖 Standard/Smart segmentation preservation、song-start ad-lib + severe-ASR recovery、weak mapped 邻居不可跨越，以及 recovered text 不形成 circular timing proof。

## 2026-08-19 — Smart v1.1.2 severe-ASR text recovery

真实生产回归暴露出一个 Smart 文本层缺口：当 Jianying 把整句识别成几乎完全不同的文字时，Text Repair V2 的相似度/segmentation safety 会正确进入 review，但旧 Smart 会把该错误 editor text 原样留在输出；这把“timing/identity 尚未完全确认”错误地等同成“canonical text 也不能恢复”。

本轮不降低 Text Repair V2 阈值，也不引入 audio/ASR，而是在 `timeline/text_recovery.py` 增加严格的第二阶段 text recovery：

- 第一阶段仍只用原有高可信 A anchors 建 ready affine model；review cue 不参与建模；
- 只处理被两个高可信 single-line canonical text anchors 包围的 interior review block；
- 两侧 anchors 必须属于同一歌曲，并各自与 ready affine model 在 750ms 内一致；
- 两侧 anchors 之间的 canonical line gap 必须完整、连续、同源，并能按 predicted LRC onset 单调分配到 review cue starts；
- 每个 cue 的第一 canonical onset 与 editor start 必须在 750ms 内；每 cue 最多 4 canonical lines、每 block 最多 8 cues；
- canonical gap 为 0 的 ad-lib、歌曲边界/单侧 block、跨歌 block、模型不 ready 或 timing 不匹配的 block 继续 review；
- recovery 只替换文字，不把低相似度 cue 提升为 A anchor，也不授予新的 timing auto-write 权限；multi-line recovered cue 仍可保留 timing review / Pro escalation。

Smart report 新增：`text_review_count_before_timing_recovery`、`text_timing_recovery_count`、`text_timing_recovery_block_count`。schema 继续 `smart-1.1`，policy id 升到 v1.1.2，确保旧 artifact 不被 Pro 当成当前 Smart 结果。

## 2026-08-19 — Smart / Pro v1.1.1 repair-only收口

本轮只修复 review 中发现的生产 bug / 回归风险，不增加新算法、不放宽阈值、不开放 Pro timing write-back：

- Smart 对全部自动 timing repair 生成最终组合时间轴后再次检查 overlap；禁止新 overlap，也禁止扩大编辑器原本已有的 overlap；
- `bpm_derived` 不再与 `exact_daw` 一样硬锁 rate：DAW 精确倍率可继续作为 hard prior，BPM 推导值只做 soft plausibility；若与稳定 A-anchor rate 冲突，只阻止自动 mutation，不用软先验推翻已验证 preserve；
- Enhanced LRC 最后 token 的合法 `end_ms=None` 不再导致 Smart→Pro 基础 planner 在 source window 计算阶段崩溃；
- Pro v1.1 只接受当前 `smart-1.1` schema + 当前 Smart policy，旧 Smart artifact 必须重新跑 Smart；
- adaptive source window、ASR-only region isolation、final `max_jobs`、artifact path collision 与 only-needed source hash/bind 收口。

## 2026-08-19 — Smart / Pro v1.1 daily-production hardening

Smart 新增 `timeline/smart_policy.py`，保留 v1 A-anchor affine engine，但把未验证 preserve 改为 review/Pro escalation，B-grade 只允许由 already-ready A model 二次确认，新增 no-new-overlap guard 与 rate provenance。

Pro 新增 reason-aware routing、merged-region mix feature reuse、adaptive source window、shadow boundary competitor 与 external forced-alignment bridge；仍保持 `timing_mutation_performed=false`。

## 2026-08-19 — Pro / Selective Audio Repair v1 evidence bridge

新增 `lyric_aligner/alignment/selective_repair.py`：只把 Smart unresolved cue 转成 bounded Pro jobs，绑定 cue/canonical/source/Smart-rate/hash provenance；mix/source window 局部化，无 canonical identity 时仅请求 bounded mix ASR。

新增 `local_acoustic_match.py`：复用 Full V4 HPSS/Chroma CENS/MFCC retrieval，但只处理局部窗口；有 Smart rate 时窄 slope 搜索；输出 acoustic timing evidence，不直接修改 SRT。

新增 `scripts/v4_pro_selective.py`，并将 mixed-language ASR routing 改为 canonical-line 级 language hint；中文歌曲中的英文 rap 不再被 whole-track `zh` 强制覆盖，语言标签也不决定是否进入 Max。

## 2026-08-19 — Smart / Anchor Timeline Repair v1

新增生产基线 `references/production-requirements.md`，明确真实任务以“规范歌词齐全、剪映时间轴大部分可信、中文为主、单曲通常单一匀速变速”为主路径。

新增 `lyric_aligner/timeline/anchor_repair.py`：Smart 不读 audio；复用 Text Repair identity；保留 LRC/Enhanced LRC/QRC timing；仅 original exact/unique/1:1 A anchor 建主 affine model；无 prior 时 robust pairwise median rate，有 exact stretch ratio 时 hard prior；candidate cue 使用 leave-one-out 独立模型；v1 timing 自动修复保守并保持 affine-first。

## 2026-08-19 — Text Repair V2.1 hardening

Text Repair 继续 frozen-timeline text-only：不读 audio，不改变 SRT cue count/number/start/end。parser/metadata、mixed timed/untimed、layout-boundary review、unmatched canonical、O(n log n) unique exact anchors、production threshold floor 0.72、schema 2.1 与 timeline signature assertion全部收口。

## 2026-08-19 — Partial Timeline Repair P1–P5

P1–P5 的 calibration/P9 proposal/readiness chain 继续固定：

```text
proposal_only = true
publish_ready = false
automatic_timing_change_allowed = false
release_gate_eligible = false
```

## 2026-08-20 — Smart v1.2.1 editor cue ownership guard

真实剪映回归发现：canonical 文字已经修正时，line-LRC 分句差异仍可能让 Sequence 层把可识别短语跨相邻 cue 搬移，或把同一边界短语重复到前后两个 cue。v1.2.1 新增 `timeline/ownership_guard.py` 作为最终 text materialization 前的保守 guard：只允许把 2–6 个已由原 editor 识别证明归属的边界字符搬回原 cue；普通边界移动必须保持相邻 cue 合并后的 canonical 文字流完全不变；仅在明确重复副本位于边界两侧时允许删除一份短重复。guard 不改变 cue 数、编号或 timing，也不产生 A/B timing anchor。

## 2026-08-20 — Smart v1.2.2 BPM-validated text recovery

真实 0-audio 生产复核进一步暴露：重复歌词或 severe-ASR 区域即使有准确的“原 BPM → 成片 BPM”信息，也不能把 `bpm_derived` 直接升级成 timing hard prior；但如果多个**独立安全 baseline text anchors**已经证明固定 BPM rate 与 editor/canonical onset 投影一致，这个 rate 可以作为额外的**文字 identity 证据**，帮助减少 false-review。

v1.2.2 新增 `timeline/bpm_sequence_reconcile.py`，只处理已经存在 canonical claim 的 1:1 `review` cue，不改变 Text Repair 阈值、cue count/number/start/end，也不增加 timing mutation authority：

- `bpm_derived` 仍是 soft prior；至少 3 个 baseline-safe text anchors、有效 source/mix 跨度、稳定 offset/residual、inlier fraction、pairwise rate 与 BPM rate 一致后，BPM text projection 才可 `ready`；
- recovery 必须保持 source/canonical occurrence 单调，并要求 candidate onset 与 BPM projection 高度一致；内部 cue 要有前后 inlier bracketing，歌曲前缘只允许极窄 one-sided recovery；
- 已有邻 cue 对同一 canonical occurrence 的 claim、明显 split-continuation 风险、下一条 lexical canonical 已落入当前 cue、pure vocalization 等情况一律 fail closed；
- optional `哦/啊/耶/oh/yeah` 等只允许在**去掉边缘 vocalization 后剩余 editor 文字精确等于 canonical**时裁掉；纯 vocalization cue 不能凭 BPM 被填成歌词；
- BPM-recovered decision score 继续 cap 在 B-grade 以下，不能反向成为 A/B timing anchor；
- report 新增 `text_bpm_projection_recovery_count`、`text_bpm_projection_vocalization_trim_count`、`text_bpm_projection_models`；schema 仍为 `smart-1.1`，policy id 升到 `smart-validation-policy-2026-08-20-v1.2.2`；
- 真实歌曲/BPM/cue/timestamp/歌词只作 private calibration，public regression 使用同构 synthetic 数据，不写真实内容 hard-code。

目标不是“有 BPM 就自动改”，而是在**BPM 已被现有安全文本证据验证**时，为 repeated lyric / severe-ASR review 增加一层低权限、可审计的文字恢复证据，同时保持 v1.2.1 ownership guard 与现有 timing gate 不变。

## 2026-08-20 — Smart v1.2.2 adjacent lexical ownership hardening

生产级重跑发现，单条 LRC 的 1:1 BPM recovery 仍可能遇到合法的 editor/LRC 分句差异：当前 editor cue 已经清楚识别到上一条 canonical 的尾部或下一条 canonical 的开头。如果仅为了让该 cue 等于单条 LRC 而自动替换，会删除 editor 已经提供的真实相邻歌词 ownership。

本轮在 `timeline/bpm_sequence_reconcile.py` 增加低权限 fail-closed guard：当当前 cue 的 normalized 开头与上一 lexical canonical 的尾部存在至少 2 字连续重合，或 normalized 结尾与下一 lexical canonical 的开头存在至少 2 字连续重合时，BPM 单行 recovery 不再自动替换该 cue，继续保留 review。该 guard 不新增 canonical claim、不改变 cue/timing、不提升 timing authority；真实歌曲 failure 只转写为 synthetic regression。

## 2026-08-20 — Smart v1.2.2 report / diagnostic semantics hardening

本轮不扩大 Smart 自动修复范围、不改变 cue/timing，也不改变 v1.2.2 policy authority；只修正生产 report 的可解释性与诊断语义：

- BPM compatibility 只在 primary timing model 确实拥有 anchor-derived / explicit rate evidence 时评估；`rate_source=none` 的 `1.0` placeholder 不再产生假 `bpm_prior_compatible=false`；
- 保留 legacy `text_replacement_count`，同时新增 `text_decision_replacement_count`、`text_materialized_change_count`、`text_semantic_change_count`，区分 decision 级替换、最终 SRT 显示文本变化和 normalized 语义变化；
- 新增 mapped/unmapped text review、text/timing review reason counts、text/timing 独立 status 与独立 Pro escalation flags；
- timing review 新增 with/without concrete proposal 计数，避免把“证据不足、原 timing 未被独立验证”误读为“存在 542 个已知错误 timing”；
- public regression 覆盖 placeholder BPM conflict、统计口径与 review 分类；不写真实歌曲/cue/timestamp/歌词 hard-code。

## 2026-08-21 - Smart v1.2.3 bounded BPM canonical stream recovery

- Private production sampling confirmed all 12 deliberately high-risk v1.2.2 BPM auto-recoveries, including zero-lexical-similarity mapped cues.
- Add a bilateral bounded-stream text-only path on top of the existing v1.2.2 mapped 1:1 BPM recovery.
- The new path uses only BPM models validated by baseline-safe anchors and only regions fully bracketed by same-source inlier anchors.
- It can recover consecutive mapped reviews and cautiously recover an unmapped interior cue only when its assigned canonical text retains minimum lexical support.
- It preserves every already-resolved lower-mode cue normalized-exactly, allows one canonical row to span multiple editor cues, and never treats LRC row count as subtitle cue count.
- Pure vocalization, cross-source claims, cut/frontier regions, boundary insertions, short low-information cues, and low-similarity unmapped asides remain review.
- Recovered text remains below B timing authority and cannot create timing anchors.

## 2026-08-21 - Smart v1.2.4 production acceptance hardening

- A private 578-cue rerun exposed three generic gaps in the newly added v1.2.3 bounded-stream tier; no real song/cue/lyric identifiers are committed.
- Treat `canonical_span=None` and zero-width `[x,x]` spans as the same unmatched state so production-shaped Text Repair output can enter the intended bounded unmapped path.
- A mapped review may not expand its canonical span into adjacent rows; only truly unmapped cues may acquire a new canonical span from bounded-stream evidence.
- Multi-cue bounded recovery now fails closed when the target gap contains Latin text because the current character-owner renderer preserves editor whitespace and is not token-boundary-aware. Existing mapped 1:1 BPM recovery remains available for English/mixed lyrics.
- Add production-shaped synthetic regressions for zero-width unmatched semantics, mapped-span ownership, and Latin bounded fail-closed behavior.

## 2026-08-21 - Smart v1.2.4 maintenance review fixes

本轮不新增 Smart 功能、不放宽任何 text/timing gate，只收口已实现行为：

- `text_mapped_review_count / text_unmapped_review_count` 与生产 unmatched 语义统一：`canonical_span=None` 和 zero-width `[x,x]` 都统计为 unmapped；
- `ownership_guard` 的 boundary move 与 duplicate-drop 统一限制在至少一侧来自现有 Sequence reconciliation 的相邻 pair，避免 guard 对普通 baseline pair 获得额外删字权限；同时删除 `_eligible_pair()` 中不可达的旧条件；
- primary timing `models[].status` 保持兼容，但 report 增加 `prediction_ready` 与 `status_semantics=prediction_readiness_not_auto_repair_authority`，明确 `ready` 只表示模型可用于 prediction，不等于单独授权 timing mutation；
- bounded-stream 的 unmapped recovery counter 经复核现有实现已经只在候选通过全部 gate 后累计，因此不改 production logic，只增加 fail-closed regression，锁定 rejected candidate 不计数；
- policy id、schema、cue/timing authority 与所有现有恢复阈值保持 v1.2.4 不变。
