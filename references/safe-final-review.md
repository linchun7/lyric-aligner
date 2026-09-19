# Safe Final review / 最低下限字幕审定

状态：正常生产的低成本审定工具，不是新的产品模式。实现为 `lyric_aligner/review/safe_final_review.py`；文字仍只由 [reviewed canonical splices](reviewed-canonical-splices.md) 物化，Gap Rescue 当前只读。UI 唯一默认 3.2。

## 输入和调用

扫描复用 splice `PLAN.json`，允许 `proposals=[]`。`floor` 指向最新已审定安全成品，不能退回原始 Smart 丢掉既有修复；历史字段 `smart_srt/smart_report` 也可绑定真正 Standard 输出。报告必须有匹配的 `output_srt_sha256`、完整 cue decisions；扫描还要求 `inputs.canonical_lyrics` 的顺序/name/hash 与 plan 的原始 canonical 前缀一致，`canonical_line_count` 必须吻合。额外参考歌词只能追加，不能替换旧 ordinal 空间。

```powershell
python scripts/v4_review_safe_final.py scan --plan PLAN.json --out CANDIDATES.json
# 涉及漏字幕的时间审定时，先绑定最终混音。只计算流式哈希，不解码：
python scripts/v4_review_safe_final.py scan --plan PLAN.json --final-mix mix.wav --out CANDIDATES_WITH_MIX.json
python scripts/v4_review_safe_final.py verify-gaps --pack CANDIDATES_WITH_MIX.json --review GAP_REVIEW.json --out GAP_QA.json
```

JSON 输出均为 create-only；输入、旧成品、旧审定包不得覆盖。扫描不跑 ASR，不生成 SRT，不授予词义、occurrence 或 timing authority。

## GPT 的文字审定

候选包含完整 cue、前后 cue、实际时间空档、Smart/Standard mapping、canonical 原文/ordinal/重复 occurrence、Latin 字符位置及原有韩文罗马化提示。目录只保存一份完整 canonical，避免每条重复整首歌词。相似度只是拼写接近的提示，不是发音真值或自动替换阈值；当前 mapping 也可能错误，邻近行不自动成为新归属。

GPT 必须将韩语 Latin 候选区分为真实英文、韩英混唱、英文衬词、韩语伪英文、hallucination、occurrence 不确定。先看原 cue 完整语义和前后真实空档，再检查 canonical 对应遍次、音节和必须保留的英文；不同窗口/hint/decode 的同一 ASR 只是一组相关观察。必要时才使用局部无歌词提示的 `faster-whisper large-v3-turbo` 观察。不得硬编码旧歌曲替换表。

中文先确认 canonical 版本和 occurrence，再修同音错字、漏字、多字、近音词。editor segmentation 是强 prior，canonical 行可跨多个 cue，长 cue 也可跨多行歌词；只确认中间词段就提交 partial splice，不能顺带改整句、跨 cue 搬字或移动时间。严格双射简繁比较只用于匹配；最终 display 保持 canonical 字形，不自动 OpenCC 改写。

`equal_controls` 仅指比较等价，不能解释成 ownership 已通过：baseline 仍为 review 的等价 cue 会继续出现在候选中。真实英文和已经修复的韩文不是为了增加覆盖率而改写的目标。当前 scanner 不自动输出 apply 决定；只提交 GPT 已确认的字符片段 plan 和逐条 review，使用原有 materializer。充分的 task-bound 语义/上下文审定可以作为保留观察，不要求每条新增音频；哈希只能证明观察未被替换，不能证明审定正确。

## 最终语义敏感词审查（每个真实 Safe Final 必跑）

这一步位于歌词文字/归属审定、Gap review 和普通 QA 之后，只处理 viewer-facing 敏感词显示，不改变 canonical truth。先冻结准备发布的完整 SRT：

```powershell
python scripts/v4_review_safe_final.py sensitive-pack --source-srt PRE_FINAL.srt --song-list private/<task>/input/songs.txt --out SENSITIVE_PACK.json
```

pack 包含完整 cue 序列和 source SRT hash。`strong_profanity`、`shot/kill/sexy/damn` 等规则只生成 hint，`automatic_replacement_allowed=false`；已经存在的 `b*` 等 mask 也会作为提示，避免不知情地重复处理。GPT 必须完整阅读所有 cue、结合歌曲名/歌词语境和前后字幕做语义判断，不能把 hint 当成替换规则。

Review 顶层必须绑定 `pack_sha256`、`source_srt_sha256`，声明 `reviewer_type=model`、实际 `reviewer_model`、`full_scan_completed=true`，并令 `scanned_cue_count` 严格等于 pack 的 `cue_count`。所有 hint cue 必须明确决定一次；GPT 还可以为未命中的上下文风险主动增加 MASK/REVIEW。每条决定只允许：

- `keep`：高置信确认原文在当前歌词/标题/混唱/ad-lib 语境应保留；
- `mask`：高置信确认需要发布层规避，只列出原 `expected_text` 中精确存在的 `mask_terms=[{text,occurrence}]`；
- `review`：证据不足，阻断最终成品，不能静默保留或机械屏蔽。

`mask` 不接受自由 `display_text`。程序按 exact term + occurrence 找到原词，再统一物化为“首字符 + `*`”，因此不能借敏感词层改歌词。`keep`/`mask` 必须 `confidence=high`；任一候选漏审、hash/text/timing 不匹配、term 不存在/重叠、REVIEW 未解决都会 fail closed。

最小 review 结构如下；`timing` 与 `expected_text` 必须从 pack 原样复制，不能凭记忆重写：

```json
{
  "schema_version": "semantic-sensitive-review-1.0",
  "kind": "semantic-sensitive-decisions",
  "pack_sha256": "<SENSITIVE_PACK.json sha256>",
  "source_srt_sha256": "<pack.source_srt.sha256>",
  "reviewer_type": "model",
  "reviewer_model": "<实际模型>",
  "full_scan_completed": true,
  "scanned_cue_count": 576,
  "decisions": [
    {
      "cue_number": 12,
      "timing": "00:00:10,000 --> 00:00:12,000",
      "expected_text": "example bitch lyric",
      "action": "mask",
      "mask_terms": [{"text": "bitch", "occurrence": 1}],
      "reason": "结合当前歌词语境，高置信需要发布层规避",
      "confidence": "high"
    }
  ]
}
```

```powershell
python scripts/v4_review_safe_final.py sensitive-finalize --pack SENSITIVE_PACK.json --review SENSITIVE_REVIEW.json --out-srt FINAL.srt --out-audit FINAL.sensitive.json
```

成功输出必须保持输入 SRT 的 cue count、number、start、end 完全一致；audit 记录模型身份、完整扫描声明、每个 mask 前后文本和精确词段。**只有该 finalizer 输出才是正常任务的正式 Safe Final。** Best-Safe/旧 display-policy 的 `strong_profanity_v1` 不再是未来正常任务的默认自动写回路径，仅保留显式历史兼容与候选发现用途。

## Gap Rescue 的窄边界

候选要求：相邻两个 baseline 非 review、完整一行 exact anchor → 正的真实 SRT 空档 → 同曲连续未覆盖 canonical 行 → 后锚点。还检查任意较早长 cue 是否占用该空档，以及别的 mapping 是否已经 claim 中间歌词。候选保留 canonical 字符 span、明确的 ordinal 区间、前后身份、重复锚点提示和 task-bound lineage。

这只能定位“可能漏字幕”，不能断言实际唱过。GPT 必须区分 `missing_subtitle`、`not_sung`、`instrumental`、`cut`、`overlap`、`occurrence_uncertain`、`version_mismatch`、`uncertain`。不扫描前后无锚点区域，不推断 partial-line gap，不将 canonical 时间码投影成混音时间；这些是明确的覆盖边界，不通过降低锚点门槛扩大覆盖。

review JSON 只有一个小契约，与候选包共享 `safe-final-review-1.0`：

```json
{
  "schema_version": "safe-final-review-1.0",
  "kind": "gap-decisions",
  "pack_sha256": "<实际候选包 SHA256>",
  "reviewer": "<实际审定者>",
  "decisions": [{
    "gap_id": "<包内 gap_id>",
    "classification": "uncertain",
    "text_confirmed": false,
    "ownership_confirmed": false,
    "reason": "<本段的具体证据与未确认问题>",
    "timing": null,
    "evidence": []
  }]
}
```

每个 gap 必须恰好记录一次。确认文字或归属必须引用真实保留的 `{path,sha256}` evidence。若记录时间，先同时确认 text/ownership 并冻结 final mix；`timing` 为 `{start_ms,end_ms,basis,observation}`，basis 仅可为 `human_final_mix`、`source_to_mix_mapping`、`local_asr_observation`，observation 也必须列入 evidence。开始/结束必须完整落在原 gap 内，且不得为腾位置缩短、移动、重切已有 cue。

verifier 重建候选并核对完整包、全部输入/音频/观察 hashes、身份、occurrence 范围、几何区间和完整 ledger。stale 输入、修改过的 pack、重复/遗漏决定、伪造 authority 字段、canonical timestamp 或越界时间直接拒绝。QA 的 `contract_valid=true` **只代表契约自洽**，不代表歌词实际唱过，更不代表时间真值。

| 审定状态 | 当前行为 |
| --- | --- |
| 版本/cut/occurrence 等未解决 | keep floor |
| text 或 ownership 未确认 | text_or_ownership_review |
| text + ownership 确认，时间未确定 | timing_review |
| text + ownership 确认，时间区间/证据已记录 | timing_authority_unavailable |

**所有状态均为 `production_writeback_permitted=false`、`subtitle_mutation_count=0`。没有 Gap SRT writer。** 当前合成测试的正确区间验证不称为“真实补全成功”。P1 shadow、track-level PASS、ASR word timestamp 均不得绕过这一点。

## 开放插入前唯一值得继续的最小任务

缺口不是新 pipeline，而是未接入“新增 cue 的原始 final-mix 边界真值”。最小输入为 2 首独立未参与这次开发的混音：合计 6 个确实漏字幕的内部 gap，另加 6 个 cut/instrumental/错误 occurrence 控制。每个候选先冻结 audio/floor/canonical/pack；由理解该语种的审核者在原 UI 3.2 导出前后边界原始响应，明确歌词遍次、实际开唱/停唱和不确定项，保留两侧未改 cue。

最小实现为小 sibling writer：读取现有 raw response，而非信任布尔值或 ASR时间；绑定 candidate/audio/response SHA 和 exact canonical span；只有 text/ownership/timing 三者同时确认才在真实空档插入。输出 QA 逐条列出插入及原有 cue identity/time/text 全等检查，失败时整次不写输出。无需 selector、数据库、新 UI 或自动 timing promotion。

验收：6 个真漏字幕均由独立复核确认文字/遍次和起止边界；6 个控制零插入；原 cue 的编号映射、文字和 timing 全部不变；零重叠；任一 stale/hash/版本/时间越界拒绝。边界真值无法一致确认的样本排入 review，不放宽门槛。出现任何错误新增或原 cue 变动，writer 保持关闭并回到本只读契约。

未知歌曲的自动 lexical 泛化另行验证：4 首此前未参与开发的 K-Pop，每首冻结 10 个 GPT 拟改 pseudo-English cue 和 10 个保持的真实英文控制；语言熟练审核者对词义和归属做 candidate-blind 复核，记录每个 changed/unchanged 字符。零错误修改、零 English 控制误伤仅允许继续评估，不能凭这 80 条宣称总体错误率为零或直接扩大自动权限。高确定性的单任务审定不必等待该批评估。
