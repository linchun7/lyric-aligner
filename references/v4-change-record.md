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

旧 Partial Timeline Repair P1–P5 的 `proposal_only / automatic_timing_change_allowed=false / release_gate_eligible=false` 约束继续保持，但这些 flags **只约束该 calibration/P9 production bridge**。Smart/Pro 是独立的 staged production path；Pro v1 只生成/执行局部 evidence，不因此获得自动 timing write-back 权限。

---

## 2026-08-19 — Pro / Selective Audio Repair v1 evidence bridge

新增 `lyric_aligner/alignment/selective_repair.py`：

- 只把 Smart report 中 `timing review` / `text review` cue 转成 Pro jobs；已经 preserve/repair 的正常 cue 不重复跑 audio；
- 每个 job 绑定 cue ordinal、canonical occurrence、source ordinal、canonical text SHA、Smart rate、editor cue time；plan 不保存 raw canonical text；
- mix window 默认仅 cue 前后各 2.5s（不足时扩到至少 4.5s）；
- 有 canonical identity 时按 LRC/逐字 token timing 建局部 source window，并请求 `source_local_acoustic_match + source_forced_alignment + mix_asr + word_timestamps`；
- 无 canonical identity 的 Smart review 只能先请求 bounded mix ASR，并明确计入 unmapped escalation；
- 输出计划统计未合并的总 mix audio 毫秒数，用于验证 Pro 是否真正只花局部计算量。

新增 `lyric_aligner/alignment/local_acoustic_match.py`：

- 复用 Full V4 已有 HPSS/Chroma CENS/MFCC retrieval，但只 decode Smart 选择的 mix/source bounded windows；
- 有 Smart rate 时默认只在 `rate ± 0.06` 的窄范围搜索；无 prior 才使用较宽但仍局部的 slope range；
- 从 local source match 反推出 canonical lyric onset 的 `predicted_mix_start_ms`，同时输出 score/margin/feature agreement/editor residual；
- Pro v1 **只输出 acoustic timing evidence，不直接修改 SRT**，等待 private real-song calibration/blind 决定自动写回门槛。

新增 `scripts/v4_pro_selective.py`：

- 必需输入 Smart report + Smart SRT + 同一组 timed canonical lyrics；
- 默认只生成 Pro plan，仍然 0 audio execution；
- 可选 `--mix-audio + --source-audio + --acoustic-out` 执行 bounded source↔mix evidence；
- 可选 `--asr-model-id + --asr-out` 只对 plan 内 mix windows 执行 faster-whisper；
- source language 可按 canonical filename/ordinal 提供，供局部 ASR routing 使用。

修正 mixed-language ASR routing：

- `text/language_spans.py` 新增 canonical-line 级 `asr_language_hint_for_text()`；
- `alignment/asr_executor.py` 优先使用 explicit job hint；没有 explicit hint 时根据当前 canonical line 重新判断；只有缺少 canonical text 时才回退 whole-track profile；
- 因此中文 track 中的纯英文 rap line 使用 `en`，真正中英 code-switch 返回 `None/auto`，不再被全曲 `zh` 强制覆盖；
- 韩文/日文 pure local line 同样分别得到 `ko` / `ja`；语言标签仍不决定是否进入 Max。

新增 synthetic/unit tests 覆盖 Smart→Pro only-unresolved selection、raw canonical privacy、英文 rap/local mixed-language hint、bounded acoustic timing evidence 与 no-mutation contract。

## 2026-08-19 — Smart / Anchor Timeline Repair v1

新增生产基线 `references/production-requirements.md`，明确真实任务以“规范歌词齐全、剪映时间轴大部分可信、中文为主、单曲通常单一匀速变速”为主路径。韩文/日文或整段外文不因语言标签自动进入 Max；有可靠 canonical identity 时仍先走 Smart，局部困难再升级 Pro，只有整体 mapping/timeline 广泛不可信时进入 Max。

新增 `lyric_aligner/timeline/anchor_repair.py`：

- Smart 不读取 audio；先复用 Text Repair V2.1 的 deterministic cue↔canonical span alignment；
- canonical timing 改用 `text/canonical_lyrics.py`，保留 line timestamp，并自动保留 Enhanced LRC / QRC 的 word/token timing；
- 逐字 timing 作为更细的 onset/boundary evidence，不直接强迫 SRT display segmentation 等同于逐字歌词；
- cue identity 分 A/B/C：仅 original editor text 与 canonical 唯一、exact、1:1、monotonic-safe 的 A anchor 可建立主 timing model；小修后的 B 只辅助，merge/split/gap/repeated/ambiguous 的 C 不建立模型；
- 每首歌默认拟合 `source_time = offset + rate * mix_time`；无 rate prior 时以 A anchors 的 robust pairwise median slope 建模；有 exact stretch ratio 时直接作为强 prior；由 BPM 推导时 `rate_prior = target_bpm / source_bpm`；
- 单 cue 判断使用 leave-one-out 独立模型，避免 cue 用自己的 timing 证明自己；
- v1 初始安全边界：`preserve <= 350ms`、`repair >= 900ms`、单次 auto shift `<= 8000ms`；这些不是公开准确率承诺，后续需用 private real-song blind 数据校准；
- interior 自动 timing repair 要求至少左右各 2 个独立 A anchors；歌曲首/尾只允许在有 rate prior 且单侧至少 3 个 A anchors 时自动外推；
- proposal 必须通过 monotonic/neighbor structural guard；重复歌词、非 A identity、模型不稳定、rate prior 冲突或支持不足进入 review，不猜；
- v1 只做 dominant affine model。少量同歌多 rate、局部 cut 等会表现为模型不稳定/冲突并升级，而不是为了 rare case 让所有普通歌曲走 piecewise heavy path；
- timing 修复暂按预测 start 整体平移 cue，保留原 cue duration；word/token end 尚不直接改写 SRT end，避免把 canonical karaoke segmentation 强加给剪映显示语义。

新增 `scripts/v4_smart_repair.py`：

- 输入 timed canonical lyrics + Jianying SRT；输出独立 Smart SRT 和 JSON report，禁止覆盖 source SRT；
- 支持 `--rate-prior SOURCE=RATIO`；
- 支持 `--target-bpm` + repeatable `--source-bpm SOURCE=BPM` 自动推导 target/source ratio；exact `--rate-prior` 优先；
- 生产文字阈值仍不得低于 Text Repair V2.1 的 0.72；NaN/Infinity 参数 fail closed；报告保持 strict JSON。

新增 synthetic tests 覆盖：isolated interior outlier、song-start one-sided + rate prior、repeated lyric 禁止 auto repair、Enhanced LRC word timing、日文 exact canonical 不强制 Max。

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

P3：`partial_repair_context.py` / `partial_repair_production.py` 验证 exact effective-run + fusion formal lineage，从 coarse/Fine/cut lineage派生 mapping；CUT_AWARE 只认 materialized cut lineage。

P4：`partial_repair_trust.py` / `partial_repair_trust_production.py` 复用 strict calibration + independent blind；formal decisions 绑定 exact lock/candidate/runtime/fusion identity，P9 CONFLICT 不得自动提升。

P5：`partial_repair_readiness.py` / `doctor_partial.py` 提供 read-only readiness。该链路继续固定：

```text
proposal_only = true
publish_ready = false
automatic_timing_change_allowed = false
release_gate_eligible = false
```
