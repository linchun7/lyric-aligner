# 2026-09-08 准确率实验登记

本表把今天的第 1–9 切片、唯一交接文档以及今天较早的成品重跑证据放在同一处。它记录的是证据边界，不是把所有运行都排成成功/失败二元结论。

文中实验产物未写回 production，是因质量证据尚不足，不是要求用户重复授权本轮开发。“无 final SRT 授权”一类旧报告措辞应按这个技术边界理解。

## 判定口径

| 标签 | 含义 |
| --- | --- |
| **实测端点改善（条件性）** | 在相同端点或同一冻结 gold 上有 MAE/尾部指标改善。若对象是 source candidate，不能直接称为 final SRT 改善。 |
| **局部机制证据** | 候选覆盖、窗口稳定性、几何冲突或输入保真得到可复现变化，但没有独立端点真值或没有进入最终 SRT。 |
| **本范围无新收益** | 实验链路有效，但在所测分母中没有相对照的改善，或最终选择器安全地保持 KEEP。这只约束本实验范围，不等于该机制对所有歌曲都无效。 |
| **撤回/无效实验** | 协议、冻结身份、输入环境或比较分母不成立；其数字不进入算法收益汇总。有效的负结果另列，不因结果不好就称为无效。 |

“历史”表示人工标注或已经看过 gold 的项目，只能做回归；“公开新”表示该次运行时先冻结预测、后读取公开标注，模型训练数据是否重叠未知。前 20 首 Jamendo 英文素材在这些切片后都已开发可见，不能再当未来 blind。

## 第 1–9 切片登记

| 切片 / 实验 | 数据身份与分母 | 端点证据与分类 | 对最终 SRT 的实际结果 | 证据 |
| --- | --- | --- | --- | --- |
| **1：source-context shadow** | 历史 H180：12 个片段、24 个已确认外边界；另有 WALK/H180/KPOP110 三个 occurrence 的完整 shadow（882/781/1158 cues）。公开日语 PJS：12 条、24 端点，已开发可见。 | 三期 shadow 的候选选择为 4/1/6 条，产生 7 个起点和 11 个终点变化；H180 24 边界 MAE 保持 start 30ms、end 9.17ms。PJS 新策略选中 0/24，11 个可比旧端点时间不变。**局部机制证据 + 本范围无新收益**。 | 三期均为 `experimental_only`；变化数不能当正确率。未取得新的 final SRT 精度收益。 | [交付报告](../output/source_shadow_upgrade_20260908/implementation_report.md) · [架构重评](../output/architecture_reassessment_20260908/recommendation.md) |
| **2：bounded target rescue** | 公开 JamendoLyrics 四首，182 行，0 行剔除；当时是预测先于 gold 的公开新集。另有三期历史 shadow 搜索 49/37/84 个目标。 | 旧/新选中 15/17 行，共同 15 行时间不变，新增加 2 个德语目标；4 个新增端点误差为 start 123/187ms、end 115/41ms，均 ≤200ms。**实测端点改善（source candidate，条件性）**：证明候选覆盖增加，不证明 final mix。 | 历史 shadow 选择 5/1/6；这些是实验字幕，不是生产写回。公开四首已成为开发可见数据。 | [实现与公开证据](../output/source_context_upgrade2_20260908/implementation_report.md) · [公开评估](../output/source_context_upgrade2_20260908/public_multiline_v1/evaluation_report.json) |
| **3：duplicate-only source sequence** | 可见开发集 182 行；新公开 holdout 四首 137 行，预测先于 gold、0 排除。 | 开发集选中 17→24，新增 7 行的事后端点误差 38–372ms；holdout 仍 15→15，15 个 raw source candidate 的 start/end MAE 663.1/339.3ms、最大 2361ms、13/30 端点 >500ms，两策略完全相同。**本范围无新泛化收益**；开发集增加只说明结构性恢复可能发生。 | 未接入默认 shadow/production，final SRT 0 变化。 | [切片报告](../output/source_context_upgrade3_20260908/implementation_report.md) · [最终 holdout 评估](../output/source_context_upgrade3_20260908/heldout_multiline/sequence_evaluation.json) · [独立验收](../output/source_context_upgrade3_20260908/independent_review.md) |
| **4：FLOAT 输入 + contextual interval** | 历史 Mandarin 诊断，无新 gold。两行 final-target 对照与三行 internal-target 对照各 3 个窗口。 | PCM16 诊断裁剪 10,689 个样本（约 1.85%），FLOAT readback 误差为 0；三行 internal target 的窗口端点 spread 为 start 73.15ms、end 44.13ms，而两行 final target 的 end spread 为 3000ms且 3/3 撞边。**局部机制证据**，`accuracy_measured=false`。 | 没有 final SRT 授权或精度收益。 | [切片实现报告](../output/source_context_upgrade4_20260908/implementation_report.md) · [配对窗口结果](../output/source_context_upgrade4_20260908/right_context_results.json) |
| **5a：英语三行 HuBERTFA（rank 3–7）** | 公开新英文 5 首，共 242 行；只有 22 行/44 端点满足完整条件，零覆盖歌曲保留。另有 rank 3–5 replication：146 行、34 端点。 | 44 端点 FW MAE 291.682→HFA 116.068ms，>500ms 9→3；start 433.727→87.5ms，end 149.636→144.636ms。独立 replication 34 端点 334.265→138.147ms，28 改善、6 变差。**实测端点改善（source candidate，覆盖窄且有尾部回退）**；Cortez line 6 end 223→626ms、HILA line 1 end 128→820ms 是反例。 | WALK/H180 实际 HFA 候选没有被几何选择；H180 no-AP 1 个完整候选、最终选择 0，SRT 0 变化。没有 final mix 精度声明。 | [交付报告](../output/source_context_upgrade5_20260908/delivery_report.md) · [英语 replication](../output/source_context_upgrade5_20260908/english_evidence_summary.md) · [独立复核](../output/source_context_upgrade5_20260908/context_accuracy_review.md) |
| **5b：中文 SOFA full-context / FLOAT H180** | 历史 16 个语义可解释端点（来自 24 个旧边界中的可比子集）；H180 781 cues。 | SOFA MAE 130.312ms，current final 3.75ms；1 胜、14 负、1 平。FLOAT 只证明输入不被削波，句尾仍受窗口边缘约束。**有效负结果 / 局部机制证据**，不是对所有 singing 输入否定。 | H180 重跑仅复现既有结果，未增加 final SRT 精度。 | [上下文准确率复核](../output/source_context_upgrade5_20260908/context_accuracy_review.md) · [历史回归](../output/source_context_upgrade5_20260908/historical_regression_report.md) |
| **5c：rank 8–12 AP/no-AP** | 公开新英文 5 首，共 169 行，仅 2 行/4 端点合格，3 首零覆盖。 | FW MAE 462ms；AP 与 no-AP 都为 192ms，4 个端点完全一致，>500ms 2→1 的差别来自极小分母。**本范围无新收益**，不能用来证明 AP 策略普遍无效或有效。 | no-AP WALK 实跑 1 个完整候选但与 KEEP 冲突，最终选择 0。 | [交付报告](../output/source_context_upgrade5_20260908/delivery_report.md) · [no-AP 评估](../output/source_context_upgrade5_20260908/public_no_ap_holdout/evaluation_report_classified.json) |
| **6a：anchored-block → anchored-path** | 新公开 Jamendo 五首，229 行、219 个内部目标；其中 53 个新增目标用于同一开发集重放。 | 初始 block：53 新目标端点 MAE 858.321ms、p95 1867ms、最大 32160ms、19 个 >500ms；15 行 raw FW 配对平均误差 362.167→2291.9ms，构成有效负结果。加入 source time-band 后同一 53 行 MAE 858.321→207.047ms、最大 32160→1867ms、>500ms 19→15。**实测 source 端改善，但属于已见开发集重放，不是新泛化证据**。 | WALK120 882 cues：HFA 完整候选 1→5，最终选择仍 0，final 区间/文字变化 0。 | [第六切片报告](../output/source_context_upgrade6_20260908/delivery_report.md) · [block 评估](../output/source_context_upgrade6_20260908/public_block_holdout/evaluation_report.json) · [路径诊断](../output/source_context_upgrade6_20260908/path_anchor_probe/results.json) |
| **6b：fresh ranks 18–20 path comparison** | 公开新三首，149 行、143 个内部目标；两首 0 覆盖，只有 9 个完整目标，134 个缺失。 | anchored-block/path 都是 9 行、18 端点，MAE 45.222ms、最大 107ms且完全相同；新增 1 行为 outer-only、误差约 25/28ms。共同 8 行 raw FW→HFA 125.5625→47.5625ms，但全是旧三行目标，不能归因于 path 新增能力。**本范围无新 path 增益**。 | 两变体最终输出相同；WALK 882 cues 仍 0 选择、0 final SRT 变化。 | [路径评估 v2](../output/source_context_upgrade6_20260908/public_path_holdout/path_variant_evaluation_report_v2.json) · [最终复核](../output/source_context_upgrade6_20260908/final_independent_review.md) |
| **7：派生词典与英文 language hint** | WALK 历史 882 cues；rank18 是已开发可见的公开素材，44 个内部目标。 | 派生词典新增 1052 项，完整候选 5→17，但选择 0；三首公开回放仍 9/143、MAE45.222ms。rank18 auto→`en` 只改语言参数：字数 22→217、canonical lexical coverage 0/203→165/203、合格锚 0→3，但候选仍 0/44，未跑 HFA、未读 gold。**局部机制证据 / 本范围无端点收益**；语言标签不是可靠度。 | WALK final 区间/文字变化 0。MFA 官方词典只提供转换研究证据，没有直接接入 production。 | [第七切片报告](../output/source_context_upgrade7_20260908/delivery_report.md) · [覆盖审计](../output/source_context_upgrade7_20260908/coverage_audit.md) · [hint 对照](../output/source_context_upgrade7_20260908/english_hint_probe/english_hint_comparison.json) · [MFA 词典核查](../output/source_context_upgrade7_20260908/mfa_dictionary_probe/summary/mfa_dictionary_probe_summary.md) |
| **8：共同窗口 + atomic adjacent pair** | 历史 WALK120，882 cues；4 个相邻冲突对触发，2 对完成共同解码，另 2 对因缺词/缺外锚拒绝。 | 209/210 重叠 1929→0ms，215/216 重叠 862→0ms（新间隔109ms）。这是几何/共同解码机制证据；全局选择 0，不能由重叠消失推导正确。**局部机制证据 / 本范围无 final gain**。 | joint overlay 与旧 HFA 输出的 882 条文字/区间一致，final SRT 0 变化；产物 `publish_ready=false`。 | [第八切片报告](../output/source_context_upgrade8_20260908/delivery_report.md) · [joint 准备](../output/source_context_upgrade8_20260908/walk_joint_v1/joint_preparation.json) · [joint 结果](../output/source_context_upgrade8_20260908/walk_joint_v1/joint_pair_outcomes.json) |
| **9：closed-compound dictionary probe** | 固定 Upgrade 7 派生词典；94,514 个长度≥6的 closed token，43,076 个恰有一个两组件唯一拆分。 | 15,009/43,076（34.84%）组件音素串与完整词精确一致，28,067/43,076（65.16%）不一致。该统计是**无 ASR 条件的词典一致性可行性**，不是音频错误率，也没有生成 production candidate。**局部机制证据**：只说明不能把任意复合词直接拆开；有声学支持时仍可另做受约束候选。 | 无 final SRT 变化、无 gold 读取、无 production 接入；不能用 65.16% 推断声学复合词一定错误。 | [compound probe](../output/source_context_upgrade9_20260908/compound_probe.json) |

## 今天较早的成品结果

第九切片最终WALK验证：共同请求2→3对，恢复219/220，内部间隔534ms；但新220与KEEP221冲突3017ms，全局采用0，882条最终字幕仍不变。旧两对候选区间不变。隔离全量1625项通过（4项可选FLOAT跳过）。因此分类为“局部机制证据 / 本范围无final gain”，见[第九切片交付](../output/source_context_upgrade9_20260908/delivery_report.md)。

第九切片补充：旧锚夹持的完整精确词序已实现为独立实验源锚。已有三首公开歌（历史回归，149行/143内部目标）获得9个精确锚，其中4个新增、5个已属于旧锚；新增4锚的8端点MAE165.75ms、最大312ms、>500ms为0/8。这是额外可用源证据，尚不是HFA目标或最终SRT准确率。全分母和预测冻结见[历史精确锚回归](../output/source_context_upgrade9_20260908/public_exact_anchor_probe.md)。WALK结果以本轮最终报告为准。

| 事项 | 分母与结果 | 正确解释 | 证据 |
| --- | --- | --- | --- |
| **既有人工确认值自动复用** | H180 781 cues；12 个起点 + 12 个终点，共 24 个历史边界。相对旧基线 start MAE 59.17→30.00ms、end MAE 91.17→9.17ms；最终写回 3 个 start、2 个 end，文字 0。 | 这是把已存在的真人确认自动落实到 exact final mix，属于真实 final SRT 改善，但不是新算法在未见歌曲上的准确率提升；24 点已参与开发，不能称 blind。 | [集成交付核验](../output/accuracy_upgrade_20260907/integrated_verification.json) · [最新整合产物说明](../output/architecture_reassessment_20260908/recommendation.md) |
| **H180 2026-09-08 完整重跑** | 声学层 781、显示层 777；与上一次最新交付 SRT 字节一致。历史 24 边界 start/end MAE 30/9.17ms 不变，新产品准确率收益 0。 | 这是重放稳定性和统计口径修正，不是新声学推理收益；不能拿“没有变化”证明未来所有输入都无升级空间。 | [重跑对照](../output/rerun_20260908/review-and-next-plan.md) · [对比 JSON](../output/rerun_20260908/comparison.json) |

## 撤回、无效或不能合并的数字

这些条目不应进入上表的算法收益平均数；它们说明证据链哪里不能用。

| 条目 | 原因与处理 | 证据 |
| --- | --- | --- |
| Phase 3 的第一次 sequence freeze | 代码修复后在读取 gold 前作废；只用 v2 freeze/evaluation。v1 保留作审计，不得与 v2 合并。 | [Phase 3 报告](../output/source_context_upgrade3_20260908/implementation_report.md) |
| Phase 4 的两行 final-target 窗口对照 | 目标是窗口末端，end 随 padding 机械增加 3000ms；它不能作为准确率结果。三行 internal-target 对照才是有效的稳定性机制证据。 | [right_context_results.json](../output/source_context_upgrade4_20260908/right_context_results.json) |
| Phase 6 path v1 / v2 请求身份 | 运行中发现 decoder 相对路径拼写与 adapter 要求不符；v2 改为绝对路径并重新冻结，request SHA 从旧值变化。v2 有效，但不能声称与 v1 请求字节相同。 | [path holdout 交付](../output/source_context_upgrade6_20260908/public_path_holdout/path_variant_evaluation_report_v2.json) · [执行阶段收据](../output/source_context_upgrade6_20260908/public_path_holdout/execution_phase_receipt.json) |
| Phase 6 raw FW 9 行 vs HFA 8 行 | 两集合不是同一目标，不能直接把 125.778→47.563ms 写成配对改善；正确共同集合是 8 行、125.5625→47.5625ms，且全为旧三行目标。 | [v2 评估报告](../output/source_context_upgrade6_20260908/public_path_holdout/path_variant_evaluation_report_v2.json) |
| Phase 7 `walk_lexicon_v1` 与初次 v2 | v1 在缺少 faster-whisper 的测试环境运行，0 候选不是算法结果；初次 v2 被 adapter 的固定词典绑定拒绝。修正后的 v3 才是有效媒体结果。 | [Phase 7 执行记录](../output/source_context_upgrade7_20260908/execution_notes.md) |
| Phase 3 Qwen full/window probes | 这是有效的负实验而非无效运行：同 34 端点 FW MAE 267.9ms，短窗 Qwen 1806.0ms；13 个局部合格端点 Qwen 1135.5ms vs FW 189.6ms，且有 4 个 >500ms 无健康标记。因此拒绝自动替换这两个配置；不能外推成所有 Qwen/所有歌曲都无效。 | [Phase 3 实施报告](../output/source_context_upgrade3_20260908/implementation_report.md) · [强制对齐报告](../output/source_context_upgrade3_20260908/forced_source_probe/report.md) |

## 当前可引用的结论

1. **能称为端点改善的范围很窄。** Phase 2 的 2 个新增公开 source targets、Phase 5 的 44 个英语 source endpoints、Phase 6 的 53 行 path replay 都有量化变化，但它们不是同一个分母，也不是 final SRT；应分别引用，不能合并成“总体正确率提升”。
2. **能称为 final SRT 改善的证据来自既有人工确认复用。** H180 的 3 start + 2 end 写回确实降低了已知 24 边界误差，但这是确认值的自动应用，不是今天新算法跨歌曲泛化。
3. **今天各次 WALK 实跑没有新增成品收益。** 第 5–8 切片都保留 KEEP 或 0 选择，最终字幕区间/文字没有变化；这证明当前候选覆盖、发音和相邻几何仍是瓶颈，不证明所有可能的声学模型或未来数据都没有价值。
4. **下一步证据门槛是新分母上的 final SRT 配对收益。** 需要在冻结候选后读取未参与调参的、带真实最终混音边界的多句样本，报告 canonical full denominator、coverage、start/end MAE、p95、max、>500ms 和新增伤害；仅增加候选数、测试数、gate 或局部稳定性不能替代该门槛。

登记依据： [唯一 Max 交接](next-stage-max-expected-loss-handoff-2026-09-07.md)、[第六切片报告](../output/source_context_upgrade6_20260908/delivery_report.md)、[第七切片报告](../output/source_context_upgrade7_20260908/delivery_report.md)、[第八切片报告](../output/source_context_upgrade8_20260908/delivery_report.md)。

## 第十切片：归因、负实验与真实 editor 恢复

- **排除的主因**：WALK 696–736s五窗15patch，现有source→mix相对波形仅2.28–2.83ms差，未见足以解释2–3s冲突的整体偏移；不代表所有未采样点或歌词端点正确。
- **未成立的升级**：三首历史歌146canonical/140internal，17旧HFA已覆盖目标的连续oracle上下文MAE138.147→143.765ms；5个同文本同配置matched目标true外侧96.2 vs FW外侧99.8，旧三行HFA56.1。两个对照不支持默认长窗口。通过实际projector/DP/materializer写出的17目标局部SRT全部可采用，误差仍略差，证明放行本身不会创造准确率。
- **有效的工程修复及成品变化**：局部editor恢复不再受未选边缘跨界cue误挡；非零区域字符ownership写回完整绑定canonical坐标。Smart[4,70)将66→60，整份WALK882→876；55共同start+55共同end变化，816外部cue不变，60/60下游ownership正确。旧HFA30边界平均分歧2297.667→238.2ms、joint12边界2511.417→162.417ms；均为proxy，相关样本不合并，不计人工gold准确率。
- **新独立数据限制**：取得并checksum验证MUSDB Al James官方312词起点，36行独立canonical；无官方词尾。全曲FW306词，sourcepacket仅3/36行选中。初版252“唯一匹配”说法撤回：其中160来自至少一侧重复等词块。v2严格唯一上下文92/312起点MAE539.966ms、>500ms25、max5880ms；这是评测修正，不是算法变差/改善。
- **事实更正**：旧reconciliation按时间错配，不证明原editor文字错误；实际raw存在3个unique regions。Rihanna ordinal4此前根本未进editor_batch，不能把其他ordinal全文失败原因套用。初版editor_smart_region metadata缺陷由v2重建解决，旧产物保留。

证据：[第十切片交付报告](../output/source_context_upgrade10_20260908/delivery_report.md)、[真实SRT对照](../output/source_context_upgrade10_20260908/editor_final_comparison.json)、[oracle对照](../output/source_context_upgrade10_20260908/oracle_diagnostic/diagnostic_report.md)、[matched对照](../output/source_context_upgrade10_20260908/oracle_diagnostic/matched_nonoracle_report.md)、[新歌评测更正](../output/source_context_upgrade10_20260908/fresh_data/onset_uniqueness_correction.md)。

## 2026-09-08 第十一切片：普通多语种顺序消歧

有效工程升级：把既有all-optimal source_sequence接到普通多语种FW路径，补全此前只有英语HFA可用的能力。Gee同一source观察84目标，6→7条候选；新旧整期1158cue只改变pos26 end 53948→53798。Whiplash完整114目标、375候选、1全局promotion；同候选移除全局promotion后真实optimizer/materializer消融确认pos36 end 56294→55209，其余785cue不变。没有独立边界gold，两个数字是位移，不是MAE改善。

Whiplash第一次local_model_unavailable是模型路径拼写错误，不计算法负结果；第二次producer变动中止，不计可交付结果；冻结代码后v3有效，280观察词，部分水印式ASR幻觉和78个partial_context体现未解决问题。历史运行与失效原因保留于本轮source_probe，不覆盖作假。

验证：1633项全套（隔离Python3.12、4项音频依赖跳过），补充音频环境4项全通过。详见本轮verification.json及delivery_report.md；这里的测试成功不是识别准确率证明。

补充跨权重诊断：相同Whiplash源音频，已有medium全曲301词，7条候选6条采用；turbo280词，5条候选采用。两者开头都出现重复非canonical内容；共同target59/60/61的source end分歧为420/160/740ms，medium对新增pos36没有合格同目标支持。不同权重来自同一ASR家族，不能将共同候选位置当作边界一致，更不能凭候选变多默认替换模型。证据：本轮source_probe/whiplash_model_crosscheck.json。

## 2026-09-08 第十二切片：逐段语种检测实测

新增默认关闭的 source_asr.multilingual=true（language=null），原生逐段检测、source-observer-1.2 独立缓存；旧默认和1.0/1.1缓存身份不变。Whiplash同turbo/音频对照：auto 5候选/5采用，整曲en 0/0，逐段自动11/10；新模式相对输入写出8 start、10 end，文字不变。这是覆盖和写出变化，没有独立端点gold，不能声称准确率提升。

Al James独立公开词起点诊断：严格唯一上下文匹配92/312，220保留null；新旧共同92起点全部一致，MAE539.966ms、p95 1543.249ms、max5880ms无变化。旧en与新auto+multilingual同时改变两个控制，不能称单因素；归因附加更正保留原报告和收据。无word-end真值、不是blind，不推广默认、不宣布封板。逐段模式开头误识别未恢复，并丢失auto的第36条候选（end55209退回56294ms）；相对auto共11条cue时间变化，不能称无损收益。三组固定对照完整记录于output/source_context_upgrade12_20260908/。
