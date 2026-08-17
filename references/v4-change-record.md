# Lyric Aligner v4 关键变更记录

> 所有实质性生产更新必须按 `documentation-contract.md` 在同一 PR 同步本文件。这里只记录已经进入代码的行为、兼容/迁移与验证边界。

## 已合入 main

- a3 production-first reconstruction：`cfa43f4c854b699819cd3acb0cfea575cd1a04c8`；
- a4 package-native render/release：`236d9d717229147ee1d1a8755d712e54db47a751`；
- a5 replayable review：`a80a531d6933946484c54d3a589bc55b0cb9e94b`；
- a6 confirmed overlap：`dfd840b3a6f893531cce8019aae53e803243f95c`；
- a7 confirmed cut/CUT_AWARE：`096210fbdbb8a55ee908b592bba20b1244c2821f`；
- a8 cut+overlap composition：`5c458d8327d2641ba053423fff3066d7fdd8ba3b`；
- P1 strict calibration/blind framework：`1c6babe37067c217d14a7404aa0ed6a1c4779a00`；
- P1.1 private dataset scaffold/readiness：`ad6c403a56209e945a9a61a1eeab1a4bc3c204b4`。

P1.1 PR #9 head `3f0414bc9f5eebc39374cf1d9c8ba43e0d25f21c` 的 validate #457 在 ASR + Python 3.10/3.12/3.14 compile/docs/full unit-E2E/Skill/privacy/environment/diff-check 全绿后 squash merge。

---

## 2026-08-18 — P2 Editor Evidence + LanguageSpan Shadow

### 1. 目标

将 task manifest 已 fingerprint 绑定的 editor/Jianying `source_srt` 从“未使用输入”升级成**非权威、可审计、语言感知的 shadow evidence**，用于后续真实 calibration；不重新把 editor text/timing 设为 canonical 或 primary timing truth。

### 2. 新分层

新增：

```text
lyric_aligner/evidence/editor.py
scripts/v4_editor_evidence.py
```

Artifact：

```text
stage = editor_evidence_shadow
role  = editor_evidence
```

固定语义：

```text
mode = shadow_only
policy_calibrated = false
automatic_timing_change_allowed = false
```

P2 不修改 timeline、run status、renderer 或 FINAL.srt。

### 3. LanguageSpan 路由

复用 `lyric_aligner/text/language_spans.py`：

```text
en/zh -> direct_text
ko/ja -> phonetic_hint
yue   -> timing_hint
unknown/generic/und-han -> timing_hint
mixed -> per-span routing
```

Bootstrap trust 只用于候选排序，不是 auto threshold。

关键收紧：

- YUE text weight=0；
- KO 内置 Hangul romanization 只算 weak phonetic evidence；
- JA kana 可保守 romanize；
- JA Kanji 没有 vetted reading backend 时不猜，返回 `kanji_reading_unavailable`；
- mixed 不使用整行单一 language text score。

旧 `scripts/editor_evidence.py` 改为 package policy compatibility adapter，不能单独提高 trust。

### 4. Evidence score / privacy

每个 line/candidate 分离保存：

```text
timing_support_score
direct_text_support_score
phonetic_support_score
text_support_score
effective_text_weight
effective_timing_weight
rank_score_uncalibrated
suggested_onset_delta_ms
suggested_offset_delta_ms
best_candidate_margin_uncalibrated
```

同时保存 span mode/language/script 与 canonical/editor text SHA。Artifact 不输出 raw lyric/editor text。

### 5. Lineage

`v4_editor_evidence.py` 必须验证：

- exact task fingerprint / input hashes；
- source run artifact；
- source run algorithm/task identity；
- effective canonical timeline artifacts；
- timeline IDs 在 run upstream；
- occurrence/track identity；
- source_srt SHA 与 task manifest 一致。

可消费：

```text
production_orchestration
review_resolution
overlap_recomposition
cut_rebuild
combined_recomposition
```

发生 source_srt tamper 必须在 evidence stage 前 fail-closed。

### 6. Tests

新增：

- EN/ZH direct support；
- KO Latin phonetic evidence；
- JA kana support / Kanji no-guess；
- YUE text authority=0；
- EN+KO mixed span routing；
- timing/text candidate ranking；
- automatic timing change 永远 false；
- evidence privacy（无 raw lyric/editor text）；
- artifact-level task/run/timeline lineage；
- source_srt tamper failure。

### 7. Algorithm / profile

P2 shadow layer不改变 Source-to-Mix、timeline 或 calibrated acoustic thresholds：

```text
algorithm_version = 4.0.0a8
calibration_profile = production-bootstrap-2026-08-17-a7
editor shadow policy = editor-shadow-bootstrap-2026-08-18-v1
```

Editor policy 明确 `policy_calibrated=false`。

### 8. 不能宣称的事项

没有真实 private calibration/blind 数据时，不能声称：

- editor evidence 提高了固定百分比准确率；
- bootstrap weights 最优；
- suggested onset/offset delta 可以自动写回最终字幕。

下一步只有真实 P1 数据证明 editor evidence 对 reference boundary 有稳定增益后，才新增 calibrated boundary fusion。

## 验证纪律

P2 必须用 latest-head GitHub Actions 的 Python 3.10/3.12/3.14、ASR、docs、full unit/E2E、Skill/privacy/environment/diff-check 结果验收。Shadow test 通过不等于真实 accuracy 已提升。
