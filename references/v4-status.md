# Lyric Aligner v4 当前实施状态

更新日期：2026-08-18  
当前开发分支：`agent/v4-editor-evidence-shadow`  
当前 main：`ad6c403a56209e945a9a61a1eeab1a4bc3c204b4`  
主线算法版本：`4.0.0a8`  
Calibration profile：`production-bootstrap-2026-08-17-a7`

## 1. 已进入 main

生产重建主链已完成：TrackAsset/canonical single truth、HPSS+Chroma/MFCC Source-to-Mix、AFFINE/PIECEWISE_RATE、Selective Fine、candidate review、confirmed overlap、confirmed cut/CUT_AWARE、partial-line fail-closed、cut+overlap safe composition、strict render/release。

P1 calibration/blind framework 已合入：

```text
1c6babe37067c217d14a7404aa0ed6a1c4779a00
```

提供 split isolation、dataset/source-group identity、baseline/candidate revision+runtime lock、explicit gates、blind-test discipline 和 sequence/cue/cut/overlap/boundary metrics。

P1.1 private dataset scaffold/readiness 已经 validate #457 全绿并合入：

```text
ad6c403a56209e945a9a61a1eeab1a4bc3c204b4
```

可用：

```text
scripts/v4_dataset_readiness.py scaffold
scripts/v4_dataset_readiness.py clone-candidate
scripts/v4_dataset_readiness.py check
```

工具不会生成假 SRT、QA 或准确率。

## 2. 当前 P2：Editor Evidence + LanguageSpan Shadow

目标不是让剪映/编辑器重新拥有字幕时间，而是把 task 中已 fingerprint 绑定的 `source_srt` 变成**独立、可审计、按语言降权的辅助 evidence**。

新增：

```text
lyric_aligner/evidence/editor.py
scripts/v4_editor_evidence.py
```

Artifact：

```text
editor_evidence_shadow / editor_evidence
```

强制：

```text
mode = shadow_only
policy_calibrated = false
automatic_timing_change_allowed = false
```

因此 P2 当前不会改 canonical text、Source-to-Mix、canonical timeline 或 FINAL.srt。

## 3. LanguageSpan / editor trust

已有 `lyric_aligner/text/language_spans.py` 作为 script/span 路由基础。

P2 shadow policy：

```text
en  -> direct_text
zh  -> direct_text
ko  -> phonetic_hint
ja  -> phonetic_hint
yue -> timing_hint
generic/unknown/und-han -> timing_hint
mixed -> per-span routing
```

关键安全规则：

- 粤语 editor text weight = 0；没有 vetted Jyutping backend 时不拿普通汉字文本/普通话拼音冒充粤语读音；
- 韩文只把内置 Hangul romanization 当 weak phonetic evidence；
- 日文假名可保守 romanize；
- 日文 Han/Kanji 没有 vetted pronunciation backend 时返回 `kanji_reading_unavailable`，不猜读音；
- mixed 行按 span 处理，不能把整行 Latin/韩文/汉字混在一起给一个总语言标签。

旧 `scripts/editor_evidence.py` 已改成 compatibility adapter，不能提供高于 package policy 的 trust，避免两套 policy 真源。

## 4. Shadow evidence 输出

每个 canonical line 保存：

- canonical line index / text SHA；
- canonical mix start/end；
- span language/script/mode/text SHA；
- editor candidate cue number/start/end/text SHA；
- timing support；
- direct-text support（仅适用 span）；
- phonetic support（仅适用 span）；
- uncalibrated rank score；
- best-vs-second margin；
- suggested onset/offset delta；
- `automatic_timing_change_allowed=false`。

Evidence JSON 不输出 canonical/editor 歌词正文。

Bootstrap weights 只用于 shadow candidate 排序，**不是 calibrated production threshold**。

## 5. Artifact / lineage

`v4_editor_evidence.py` 验证：

- task manifest 与所有 inputs；
- source SRT SHA 属于 exact task fingerprint；
- source run artifact；
- run algorithm/task identity；
- 每个 effective canonical timeline artifact；
- timeline artifact 必须是 source run upstream；
- occurrence/track identity。

支持从以下 effective run 读取 timeline：

```text
production_orchestration
review_resolution
overlap_recomposition
cut_rebuild
combined_recomposition
```

若 source_srt 在 manifest 创建后被改动，task verification 必须先失败。

## 6. 当前测试

新增 regression：

- EN/ZH direct text；
- KO Latin phonetic hint；
- JA kana phonetic hint；
- JA Kanji pronunciation unavailable；
- YUE text authority=0；
- mixed EN+KO per-span routing；
- nearest editor cue ranking + no auto apply；
- evidence JSON 不泄露 raw lyric/editor text；
- artifact-level task/run/timeline lineage；
- source_srt tamper fail-closed。

P2 尚未经过 GitHub Actions 最新 head 全量验收，当前分支不能宣称可合并。

## 7. 真实数据限制

GitHub Actions 当前没有用户授权的真实歌曲 + 人工 reference truth，因此可以验证 P2 的 synthetic multilingual/evidence contracts，但**不能**证明：

- editor evidence 在真实 zh/en/ko/ja/yue 上提高了多少准确率；
- 哪个 text/timing weight 最优；
- 哪个 onset/offset delta 可以安全自动应用。

因此 P2 先 shadow-only。只有 P1 real calibration/blind 数据证明收益后，下一版才考虑 calibrated boundary fusion。

## 8. 后续

1. P2 shadow artifact CI 全绿并合入；
2. 用真实 private dataset 记录 editor evidence 与 reference boundary 的相关性；
3. 只有通过 calibration/blind gate 才增加 Editor Boundary Fusion；
4. Forced Alignment / ASR v2 仍由 real error breakdown 决定；
5. same-region cut+overlap joint acoustic model 继续 BLOCK。

> **当前正确表述：P0、P1、P1.1 已进入 main；P2 正在接入非权威 editor shadow evidence。未经真实校准，editor 建议绝不直接修改最终字幕。**
