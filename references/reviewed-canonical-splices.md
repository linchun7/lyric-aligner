# 已审定 canonical 字符片段修复

该入口让未来任务复用已验证的局部修复落地方式，避免为每批曲目再写一个带cue号/歌词的renderer。它复用`text_repair`的parser、normalization、renderer与`anchor_repair`的canonical parser；没有新模型、检索器、DP或自动阈值。schema由`lyric_aligner/review/canonical_splices.py`定义为`reviewed-canonical-splices-1.0`，与Smart算法版本及成品修订号分开。

## 最低保底流程

1. 固定本次 Standard/Smart 及最新已交付安全 floor；仅处理 baseline 明确 review 的位置，不丢弃已确认韩文修复。通用候选由 [Safe Final review](safe-final-review.md) 扫描，不从脚本相似度直接产生 apply。
2. 从实际失败中定位可确认的字符片段，结合canonical、原字幕音节、**当前floor**前后文和实际外部观察审定。已有证据足够则复用，只有候选需要时才局部用音频。
3. 以canonical文件、行ordinal及字符半开区间表述建议。一个cue可以取同一行的半句、跨行的几个片段，或只改中间一个词；这不允许重新分段或改时间。
4. 审定前查看完整前后字幕及实际时间空档。cue号相邻并不意味着音频歌词连续，空档不设自动通过阈值。对重复副歌要核对当前实录，不凭另一遍相似句代替。
5. 对措辞和归属分别写理由，引用已绑定外部观察。只确认部分则标记`partial_text`，保留其余文字及review。只有任务审定可采用，机器运行不会产生语义PASS。
6. 执行到新目录；检查改前/改后及完整keep台账，再纳入本次最低保底成品。新反证出现时从父floor恢复相应cue；共用字内边界的组一起回滚。

```text
python scripts/v4_apply_reviewed_text_splices.py --plan PLAN.json --inspect
python scripts/v4_apply_reviewed_text_splices.py --plan PLAN.json --review REVIEW.json --out-dir NEW_DIR
```

`--inspect`只输出JSON诊断上下文，供既有审定流程使用，不是另一个听审页面。需要人工听审时继续复用既有完整页面并显示字幕；不熟悉语言的听审不冒充可靠Gold。

## 数据契约

JSON 均为 UTF-8。文件引用恰含 `path` 和 `sha256`，相对路径按 PLAN 所在目录解析。共用 `load_floor_context` 负责 inspector、scanner 和 materializer 的基线读取；文件 SHA 复用流式 `_sha256_file`，不把整个音频读入内存。所有输入/证据在加载和最终写出前均校验，plan/review 的最初 hash 不得在写出时偷偷刷新。旧 artifact 保持原 schema，不改写历史。

`smart_report` 必须为真正 `smart-1.1` 或 Standard `2.2 / text_only_preserve_timeline` 报告；后者还要求 timing/cue_count unchanged 为 true。报告的 `output_srt_sha256` 必须等于所绑定 baseline SRT 的实际 hash，decisions 必须完整且 cue ordinal 唯一。Standard 允许原有 untimed canonical parser。floor 与 baseline 的 cue 编号/时间/拓扑必须相同。reviewed splice 可以引用另经审定的歌词文字出处，但不能据此宣称 baseline occurrence 已确定；基于 ordinal 的自动候选扫描另行严格校验原始 canonical 顺序和版本。每个 edit 内以及同一 cue 按字符位置排序后的全部 edits，其 canonical segments 必须同源、单调且不重叠；display 不得自动繁简转换改写 canonical。

PLAN必需字段：`schema_version`、`floor`、`smart_srt`、`smart_report`、`canonical`、`proposals`。`canonical`是有序文件引用数组，行ordinal按既有parser连接后的顺序确定；中文/英文/韩文/日文使用同样的字符区间，不转字节offset。补充另一份同曲正字资料时，必须在任务审定中核对原罗马字、版本和实录，添加文件本身不证明版本相同。

每个proposal恰含：

- `cue_ordinal`：0起始位置；`cue_number`：原SRT编号字符串，不补跳号。
- `timing`、`before`：与当前floor逐字一致。
- `edits`：`span: [start,end]`是当前cue文字的原字符区间；`segments`中每段含`canonical_ordinal`和`span`。不得输入自由`after`文本。
- `observations`：真实外部观察的文件引用数组。绑定观察只证明出处，不能证明识别结果正确。

段落可选`display`只允许与canonical规范化词义等价的空格、标点、大小写显示差异。多段以空格连接；不增加/删除显示换行。不允许重叠替换、负索引、布尔索引、重复cue、自由文字或时间覆盖。未改部分原样保留；无改动任务逐字节复制floor，有改动时使用原有renderer的UTF-8/LF输出约定。

REVIEW必需字段：`schema_version`、`plan_sha256`、非空`reviewer`、`decisions`。每条decision恰含`cue_ordinal`、`decision`（`apply`/`keep`）、`scope`（`whole_text`/`partial_text`）、`wording_reason`、`ownership_reason`、`evidence_indices`。apply要求两个非空理由和至少一条外部证据索引；所有proposal必须有且只有一条决定。plan变动后旧review失效。

输出`FINAL.srt`与`QA.json`。QA记录所有cue的apply/keep台账、父floor、plan/review哈希、改前改后与审定范围；固定`automatic_lexical_authority=false`、`independent_gold_accuracy_measured=false`。`whole_text`是审定者的判断，不是软件检出的声学事实。缺失、过期、冲突或未授权输入在写入前报错，原floor不动；禁止覆盖已有输出目录。

## 已验证范围与禁止推论

真实R2/R3/R4的150处历史采用（含英语原位错词、韩英混合、跨行短句、词内cue分界、罗马字对应正字）经本入口回放，11组成品与原发布文件逐字节一致。这证明兼容旧修复，不是150条新增收益，更不是未知歌曲独立holdout。R5的18处采用使用同一入口，65条全部逐条保留理由。

不推广“ASR命中canonical就整行覆盖”：296条会吃掉前一句尾词；372/518等长cue会遗漏多行；574条单凭相邻字幕会选错副歌；语种强制、翻译式输出或片尾幻觉也不授权修改。通用代码只执行被审定的精确片段，这些语义/声学反例仍需任务判断。尚无独立证据证明未知歌曲可自动安全放行，因此本轮新增**自动词义规则为零**。不为扩大自动覆盖继续调ownership/DP。
