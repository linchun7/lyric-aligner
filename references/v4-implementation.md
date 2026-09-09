# Lyric Aligner v4 实施记录与关键代码说明

## 完整字幕约束下的离线瓶颈评价

`evaluation.interval_bottleneck.evaluate_interval_bottleneck(cues, gold)` 接收完整 editor/旧 final 序列（包括未标注邻句）、已绑定 cue ID 的完整起止标注及各模型的原子区间候选。调用者负责音频、标注、cue 归属与完整序列的身份核验；该纯计算函数不能从时间自动推断歌词归属，也不估计漏词或错误文字的代价。

函数同时返回三种诊断：逐端点最优误差；只选择完整候选区间的非重叠序列最优误差；允许已有候选与 editor 的端点组合后、仍满足完整序列约束的最优误差。第三种显式标记混合来源，不表示某个模型确实输出过该组合，更不表示在线 selector 能选择它。动态规划使用 `(端点绝对误差, 改动 cue 数)` 排序，无标注 cue 只提供 editor 选项。输入基线必须有序、非负、正时长且不重叠；候选失败保留覆盖分母。gold 中找不到的 cue ID 返回 `incomplete_ownership`，没有可评分端点时误差为 null。

这是候选能力与选择瓶颈的测量工具。不能将其用于生成生产决策，不能将原子区间上限当作单边修复的上限，也不能将历史标注回放或波形注册精度作为独立 final-mix 字幕准确率。

同轮路径修正由合格canonical行的source区间生成词/音素状态时间带，SP状态自由，DP转移后屏蔽不合法的目的状态。这样正确锚点不再只是裁窗信息，而能阻止重复歌词跨长间奏串位。旧三行不加时间带；不同上下文策略有独立身份。无限时间带的递推须与绑定vendor结果一致，无可行路径或词序失配须隔离失败，不能返回人工修过的时间。

第六切片复用已有 `source_context_hubertfa.prepare_occurrence` 的完整 canonical lattice/sequence，以 `prepare_anchored_blocks` 为缺失目标确定最近合格外侧锚点；不重新计算局部重复 promotion。一次 variable-length canonical 音素路径经共享 contextual interval API 按多个内部 index 提取，再走旧投影与 interval DP。此增量不改 `source_packets`、`source_sequence` 或几何选择规则；adapter 仅在独立 block 策略下接受有固定资源上限的多行输入。完整协议见 [原曲上下文与区间联合升级](source-context-shadow-upgrade.md)。

alignment_contextual_segment_interval_ms(words, window_start_ms, segment_tokens, target_segment_index)返回内部canonical目标的(start_ms,end_ms)。两側上下文必需；与alignment_internal_boundary_ms（下一段onset）含义不同：本API的end为目标末词offset，不能跨停顿取下一句onset。重复文本由完整词序和segment位置绑定，不做substring搜索。FLOAT源探针保留原波形；新提取器尚未接默认shadow/production，稳定性不能替代gold精度。

实验 source_sequence.resolve_source_sequence(packets, source_observation_sha256=...) 读取完整bounded-v5包；每包envelope须携带同一实际观察SHA，返回稳定packet cache key/canonical domain/candidate_id的promotions，不修改输入。策略source-sequence-2026-09-08-v1-duplicate-only-all-optimal、schema source-sequence-1.0；O(V²)全最优共识，最多2048节点且最多2000000节点对。仅canonical duplicate可新增，旧选中保留；相同canonical域重复查询合并、部分相交拒绝。第三切片当时尚未接入普通shadow，历史四曲结果保留于 output/source_context_upgrade3_20260908/implementation_report.md；第十一切片已接入普通多语种source shadow v5，仍非生产默认授权。每个occurrence收集全部可解析target cue包（n_best=1024，检查截断），校验observer实际自哈希后运行；只有complete状态的对应duplicate候选经独立promotion记录进入原投影/几何选择，不篡改局部packet资格。未解析cue保留覆盖分母；不是额外构造全canonical行的lattice。新Gee和Whiplash的真实写出与消融见 output/source_context_upgrade11_20260908/，不将候选覆盖或终点移动量当成人工gold准确率。

2026-09-08 第二切片新增 `anchored_edit` 字符路径共识：前向/后向编辑距离检查所有代价至多最优加 1 的路径，只有一致的观察字符才获得归属。`source_packets` 在无上下文合格的精确目标时允许一个内部转录编辑段；真实首尾词边界、原邻域资格与重复位置歧义继续约束候选。逐包搜索预算与单矩阵上限进入策略身份，耗尽后丢弃全部暂存候选。runner 使用 v4-bounded-target-search，producer 清单绑定新增 helper；目标容错只是实验候选能力，不是声学精度声明。

2026-09-08 实现 source_observer → source_packets → effective mapping 投影 → interval sequence optimizer → shadow SRT/CSV → 回读/历史配对评价。新增 source 时间基与按完整录音复用的缓存；canonical cue 字符归属独立于 LRC 换行；完整起止与路径一起选择。新 contextual 模块按有效全曲/分段映射限制搜索，保持 bounded 特征的绝对坐标。具体协议和权威边界见 [原曲上下文与区间联合升级](source-context-shadow-upgrade.md)。

2026-09-08 H180最终SRT重生成对照：使用冻结输入和现有已验证证据重跑完整升级/QA/display入口，不是全音频ASR重识别。相对升级前8cue/5start/4end改变、文字0变化；旧24边界的start raw MAE59.17→30ms、end91.17→9.17ms。相对上一份最新声学层及显示层SRT均字节一致，本次新增成品收益0。QA结构通过但4个重建区间仍未完整验证，publish_ready=false。修复旧review重放误计新人工：new_human_annotations恒0，applied_existing_gap_review_records另记实际应用3条，reporting policy1.1；隔离23项相关测试和再次真实重生成通过。下一轮默认timing改写需先取得冲突相邻句末与连续哼唱段的可验证候选及独立收益，当前不为制造变化而继续调参。详见output/rerun_20260908/review-and-next-plan.md与comparison.json。

2026-09-08补识别可用性升级：cascade execution_strategy升级为bounded_first_pass_then_edge_retry_v2_retain_on_model_unavailable。新增AsrModelLoadError区分推理前加载失败；只有自动cascade启用保留首轮，独立P6仍严格失败，plan/audio/inference/evidence错误不降级。复用原composer验证与隐私逻辑，失败时selected保留、executed/adopted=0，second_pass_status在证据、artifact和终端显式输出，未执行轮不冒用策略身份。49项定向、隔离完整1425项回归（160.315秒）通过；CLI摘要后续delta另经39项定向和真实CLI验证。WALK140真实音频+离线空本地retry模型完成1/0/0验证、保留句首3004125ms且句尾未知，正式fusion及SHA回读通过，无模型下载/新人工/SRT修改。此为可用性改善，不计为声学精度增益。证据：output/accuracy_upgrade_20260908/retry_availability_v1/。

2026-09-08最终混音映射验证完成：事先按SHA选定KPOP110的3个occurrence，分别检查9个固定分位窗口与9个既有最低margin歧义窗口。正常9点与旧fine差异<=32ms；歧义组按已冻结margin>=0.05且无歧义条件仅6点合格，与旧fine差异<=36ms。Gee另3点相差1608–2012ms但全未满足冻结条件，不能据此替换时间轴。此为最终mix诊断，无独立边界真值，也不把单曲holdout授权外推到裁切缓冲；本轮没有接入默认mapper或改写SRT。证据：output/accuracy_upgrade_20260907/contextual_final_mix_v1/及contextual_final_mix_ambiguous_v1/。

2026-09-08单义繁简比对修复：真实新观察中的愛/裝与canonical爱/装未被NFKC归一，导致句首缺失及support低于0.72。word-match policy升级为bounded-lexical-match-2026-09-08-v6-opencc131-bijective，仅在ASR词段和text-support副本使用OpenCC1.3.1固定原始字表导出的3626对单义字符；排除所有正反向多义/竞争映射、转换链及不稳定NFKC，發髮乾幹後臺不合并。不改原文、display或CER使用的_normalize，无新增Python依赖。107份既有词段重放仅2份功能评分变化：0.5→0.625、0.714286→0.875，后者句首从缺失恢复；其余变化为规范化匹配哈希。旧日语验证样本本次仅作回归，不宣称新盲测。4字表/47ASR/11Qwen定向、独立复核及完整1421项回归（155.551秒）通过。已有正式计划的真实CLI重跑及artifact回读通过、携带v6身份；该较窄窗口已识别为简体，旧匹配器同样得分1.0，因此这次正式重跑只证明集成正确，不重复计为准确率增益。字表Apache-2.0许可与作者保留在lyric_aligner/text/data/opencc/，证据见output/accuracy_upgrade_20260907/bijective_han_v1/。

2026-09-08连续实跑结论：非词汇输入修复后，KPOP130/KPOP200/WALK140的12个区域全部正式生成候选，cue总数分别786→779、826→826、936→926，规范化歌词全文均一致；另H190的5个区域候选672→671。17份产物全部通过输入SHA、SRT/audit及artifact回读检查。相同fusion下四项目QA前后均未通过，因此没有整体提升为生产成品。对39个>=250ms起点分歧按事先固定哈希规则选11个：Whisper覆盖3个，Qwen仅对8个缺失点补测并新增覆盖3个；6个可比较点里候选相对观察4个更近、2个更远，另5个未知，不据此生成自动替换authority。当前工程回归1415项/154.644秒、17项定向与两次独立复核通过，无新增人工。详见output/accuracy_upgrade_20260907/cross_project_editor_regions_v1/，尤其smart_materialized/audio_proxy_combined_summary.json；本次新增可确认识别改善仍以日语独立录音对照为限，跨语种最终SRT精度尚不能封板。

跨项目局部恢复继续：原始editor扫描67个唯一文字区域，12个通过初步geometry，其中正式入口已在H190产生局部候选，其他保留原因逐项记录。真实KPOP200源cue129只有撇号：strict SRT接受但Smart lexical parser直接报empty text。新增editor-lexical-observation-input-1.0，仅派生文字匹配输入排除非词汇cue，按原ordinal插回并保留原文件/非目标内容；目标段自身含非词汇cue仍拒绝。源SHA、派生SHA、映射和策略进入报告及artifact，17项定向测试及独立复核通过；不是新增声学authority。证据见output/accuracy_upgrade_20260907/cross_project_editor_regions_v1/。

日语hint v3额外验证完成：冻结的另一批12条PJS录音，Qwen字符错误率17.91%→9.66%，终点覆盖9/12→12/12；起点覆盖仍8/12，仍有2个起点、1个终点原始偏差>=500ms。只作为同歌手新录音验证，不代表跨歌手或生产边界授权；验证结果不用于本策略调参。当前代码完整回归1413项/151.472秒通过，独立复核、skill/privacy及dirty docs契约通过。精确逐项结果与固定共同边界比较见output/accuracy_upgrade_20260907/public_singing_v1/verification_v3.json及evaluation_validation_before_validation_after.json。

日语提示v2初次真实对照：冻结12条PJS日语短歌、完整原音频输入，Qwen规范化字符错误率47.58%→17.81%，起点覆盖5/12→9/12，终点6/12→10/12，新终点最大原始偏差62.04ms；起点仍有2个>=500ms偏差。Whisper同样本字符错误率13.49%，起点10/12、终点12/12，但终点最大原始偏差2621.77ms。只适用于本批单歌手外部诊断，不是生产或模型训练排除证明。独立复核发现Unicode混语漏检，已以NFKC副本和未知Unicode字母保守处理修正为hint v3；10语言/45ASR/11Qwen定向测试通过。独立P6和cascade逐轮/逐job保留language及word-match policy，混合结果不再假借首轮身份。另12条未参与诊断的录音按相同哈希选择规则冻结，修复后才开始对照执行，无新增人工。公开来源与完整证据见output/accuracy_upgrade_20260907/public_singing_v1/SOURCES.md。

日语局部提示修复：未给整曲标签时，含假名的普通日语汉字混写行曾因und-han而丢失ja提示。新增local-script-hint-2026-09-07-v2-japanese-han，仅在auto/generic/unknown且有实际假名、无Latin/Hangul时解析同句Han为ja；不改变editor language_spans或可靠度，不覆盖已知其他语言或mixed。提示策略身份随FW/Qwen/cascade和正式artifact导出。9语言、44ASR、11Qwen定向测试通过；PJS外部固定样本的真实反事实复测正在完成。

局部恢复实跑完成：15个唯一文字区域中12个因与保留字幕冲突而排除，3个安全区域写回新候选；WALK120的Super Model局部5/8/12条分别恢复为5/7/9条，整包882→878，规范化全文保持一致。19个可证明原editor cue归属的行起点，相对该既有边界的中位差2452→0ms；这不是人工声学误差。通用QA的固定30个source cue匹配里15项改善、2项变差，且少匹配3项、P90未改善（重复副歌匹配有歧义），因此不能宣称全曲精度已通过。相同fusion下整包QA前后仍失败。完整回归1409项/163.777秒通过，两项独立复核通过，产物及逐项比较见output/accuracy_upgrade_20260907/editor_regions_v1/。

复用已有人工标注复测Qwen：12个outer片段覆盖11首歌、24个起止点，未新增人工；按原gold绑定的8/4分区与阈值评估，起点9/12有预测、有效误差中位50ms/P90 410ms，终点7/12、中位223ms/P90 516ms，均未通过校准。旧边界覆盖12/12，中位有效误差为0ms。这否定Qwen直接替换既有时间轴的方案；不是新的盲测，也不改阈值拟合这些结果。证据：output/accuracy_upgrade_20260907/untimed_lexical_v1/human_outer/evaluation_anchorpolicy.json。该阶段完整工程回归1403项/179.254秒通过。

局部编辑器恢复新增可选canonical_region：只接受两侧同时落在完整editor cue与canonical行边上的唯一精确文字块；baseline对应区域也必须连续且文字完整一致，拒绝与同曲保留字幕重叠。默认整曲模式保持原有行为，区域策略身份独立。正在实跑实际候选，不因此声明声学精度或封板。

2026-09-07 继续升级词汇/时间分离：WORD_MATCH_POLICY v5 在 canonical 匹配中保留零时长字的已观察文字，内部零时长不再截断整句；外层字无正时长时对应边界仍未知，非有限/逆序/越窗与重复歧义继续受限。固定29窗口回放中28项既有匹配和边界保持一致，Qwen扩窗观察从0.666667恢复为完整匹配1.0；正式Qwen CLI再次实跑得到相同候选。此为覆盖恢复，不能当作声学边界精度提升或final写回依据。证据：output/accuracy_upgrade_20260907/untimed_lexical_v1/。

同时修复Qwen在Windows中文环境路径下的nagisa原生模型加载失败：仅在Unicode安装路径时，将已安装包逐文件校验复制到进程临时ASCII目录；原安装及模型权重不变，不下载模型，输出运行兼容身份。11项Qwen测试及独立复核通过，真实CLI不再依赖手工PYTHONPATH。

ASR路由 `asr-second-pass-edge-coverage-2026-09-07-v2` 读取显式coverage/ambiguity。合成 `asr-second-pass-preserve-edges-2026-09-07-v2` 比较可用边界集合，缺失或歧义的第二轮不覆盖已有首轮边界；同一覆盖集合不因lexical分数更高替换时间。新增完整覆盖仍可改变原起点的具体数值，因此这是覆盖能力保护，不是时间误差单调不退化的证明。未知句尾的扩窗实测失败已保留，未修改planner默认窗口。


`contextual-independent-fine-1.1.1` 拒绝非有限特征、非正/非有限 slope 与非法采样参数；正常匹配评分规则保持 1.1。benchmark 在执行前固定实现、结束时拒绝实现变化；holdout 在读取媒体前双向核验 observer 及精确配置。既有 1.1 压力 holdout 产物与冻结代码保留，不改成 1.1.1 的证据。


上下文音频匹配继续开发：contextual-independent-fine-1.1 联合局部和两侧特征，使用三段分数中位数；逐帧精炼避免粗网格相位造成假消歧；强局部竞争者缺上下文时保留歧义。旧 Independent Fine 1.0 不变，新观察仍同一 percussive family，无歌词边界 authority。已接入 v4_run_independent_fine_benchmark.py 的 --observer contextual。

新fusion及旧fusion直读均不得把缺失首覆盖字段解释为onset。历史内容可读取，但需绑定词级文本重算/重融；forced证据不受此ASR覆盖规则影响。

通用 ASR edge coverage：`_best_asr` 对带覆盖标志的所有backend统一要求首尾完整；`_best_asr_onset` 独立选择已覆盖且非歧义的句首，并保留原job/backend/support，不能拼接不同job为完整区间。`_line_audio_anchor`消费句首记录，仍受ASR支持阈值、editor可靠性和独立family冲突条件约束。原始半句拒绝整区间，同时回收句首证据，避免以降低coverage作为唯一修复。

`semantic-layer-errors-1.0` 将 ASR/editor 的 projection/final disagreement 分别归入对应层；coverage、独立音频冲突继续共享，顶层仍要求两层同时通过。音频 authority policy v1 与阈值不变，CLI另输出 diagnostics_policy。双向回归证明旧错误串用；它只修复诊断，不提升字幕精度。

`timeline/editor_preservation.py` 的 `immutable-editor-canonical-stream-1.0` 保留编辑器 cue 数量、时间和字符归属，只在整曲 normalized canonical 流完整相等后恢复规范字形、空格及标点。单 cue/单 canonical 行且其他词完全相同的 1–2 字母后缀修正，单独留下文本证据。`v4_preserve_editor_occurrence.py` 校验 manifest、run/assets/timeline 哈希及身份，重算 Smart，不继承旧 boundary_authority；只给真正从 canonical 行起点开始的 cue 写 canonical_line_index，多行范围另存。`v4_upgrade_subtitles.py` 已消费该独立阶段，保持真实 artifact 路径，不替代 fresh QA。

`timeline/lyric_clock.py` 提供实验性稳健 LRC→source 时钟拟合，与既有 source→mix mapping 分开。输入为调用方预选的起点观察，重复坐标拒绝；输出 rate/offset、残差和外推范围，不含 calibration/production authority。实际候选脚本保留 SRT 文本、其他歌曲的块字节、完整输入 hash，并验证顺序、末尾窗口及新增重叠。拟合起点不代表声学终点获得验证。

ASR lexical matcher 对并列最佳的多个不同区间保留全部候选，返回未知唯一端点。FW/Qwen 均传播歧义，fusion 在 segment fallback 前排除此类区间。策略身份单独记录在 evidence 和 artifact normalized config；执行策略 v2 的窗口语义保持不变。

2026-09-07 直接开发补充：`qa/semantic_sync.match_track_semantics` 成功匹配后消费完整候选 span，避免重复句无限重用同一个起点；merged cue 不凭文字包含关系产生第二个精确 onset。策略身份为 `editor-semantic-disjoint-onsets-1.0`。`alignment/qwen_asr_executor.py` 通过正式 ASR CLI 提供可选本地观察：每个窗口独立解码、canonical 只在识别后评分、零时长/越界/逆序词形成无效边界屏障、首尾词覆盖独立判断；`evidence/fusion.py` 保留真实 backend 并只消费两端覆盖的 Qwen 区间。Qwen ASR 与 observed-text aligner 不算两个独立 family。缺尾回归同时覆盖 executor 和 fusion，避免将前缀终点解释为整句结束。

人工 gap 路径：`v4_apply_gap_review.prepare` 校验锁定输入与原 receipt，`apply_reviews` 只接受结构化 bool true + present，联合解决已确认相邻边界的联动；`replay_gap_receipt` 再比较完整输入角色、解析路径和 task/audio/source 身份，严格读回最终 SRT/report 后供 QA 消费。QA 输出保护同样采用重放发现的完整依赖。未确认相邻 cue 不被自动裁剪。A/B UI 通过共享编辑值更新邻句播放起点，播放计时不修改边界；数字框被清空或不合法时不能导出隐藏旧值。

`v4_upgrade_subtitles.run_job` 在 gap review job 中自动调用连续人声显示 materializer，结果放在 `display/`。声学 CSV/SRT 继续作为 QA 输入，显示结果单独进入 `display_derivative` 摘要；目录迁移后再次运行完整显示重放，避免依赖路径失效。无 gap review 的 job 不加载或执行该阶段。

2026-09-07 ASR 窗口修复：`asr_executor.py` 在语种/occurrence 分组内按区间分配不重叠批次，每个解码批次只向自身 job 分派结果。`_bounded_words` 为两个支持分提供共同的时间检查；局部 matcher 不跨无效或倒序词拼接，整体文本支持遇到中间屏障返回未知。窗口外 segment 全文仍可用于诊断，但不支持当前 job 的 canonical text。重叠窗口可能增加 backend 调用次数，以免将跨次解码混成一次可靠观察。执行策略记录为 v2，历史 v1 产物保留读取兼容。

> 真实生产 workload 的 normative baseline 见 `references/production-requirements.md`。当前状态见 `references/v4-status.md`，Smart / Pro 兼容约束见 `references/smart-pro-v1-1.md`。

## 1. Responsibility graph

```text
Canonical lyric -> final text/order truth
Editor SRT      -> strong but rebuttable mix-time + display-segmentation prior
Timed canonical -> Smart no-audio sequence/timing evidence
Source-to-Mix   -> Pro/Max primary acoustic timing truth
ASR / forced    -> auxiliary evidence
P9 fusion       -> legacy Partial shadow diagnostics
P4 trust lock   -> legacy Partial calibrated proposal eligibility
```

产品路径：

```text
Standard -> Smart -> Pro -> Max
```

跨模式 authority contract：

```text
canonical lyric text/order = authority
canonical LRC line break   = grouping/onset evidence, not subtitle cue authority
trusted Jianying cue       = display segmentation strong prior
word/token/audio evidence  = may rebut editor boundary when independently strong
```

更高模式可以增加证据、减少 review，但没有更强反证时不得破坏较低模式已经安全成立的文字、cue ownership 或 timing。

禁止：

```text
ASR/forced text -> final canonical lyric
LRC line break -> silently resegment trusted editor cues
higher mode -> regress lower-mode safe result without stronger evidence
one cue -> prove its own timing model
sequence/timing/BPM-recovered text -> become a primary timing anchor
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

Text Repair V2 负责 lexical-first 主文本匹配，包括 bounded 1↔N / N↔1 / N↔N span。similarity、length-ratio、ambiguity、layout-boundary guards 不因 Smart 升级而降低。

`text_repair._assign_targets()` 以 editor 原字符 ownership 为主做 canonical edit script。连续 editor/canonical 文本已经一致而仅 LRC 换行不同的 span，必须保持 editor cue segmentation；LRC 行边界本身不拥有跨 cue 搬字权限。

严重 ASR 乱码如果 lexical evidence 不够，会进入 review；Smart 可以用独立 sequence/timing/BPM-validated text evidence继续处理，但不得通过降低 Text Repair threshold 来制造更多 false auto。

## 3. Smart / Sequence Reconciliation + Anchor Timeline Repair v1.2.10

核心文件：

```text
lyric_aligner/timeline/anchor_repair.py
lyric_aligner/timeline/smart_policy.py
lyric_aligner/timeline/text_recovery.py
lyric_aligner/timeline/sequence_reconcile.py
lyric_aligner/timeline/bpm_sequence_reconcile.py
lyric_aligner/timeline/ownership_guard.py
scripts/v4_smart_repair.py
lyric_aligner/io/path_safety.py
```

Smart report schema 继续 `smart-1.1`；当前 policy id：

```text
smart-validation-policy-2026-08-21-v1.2.4
```

### 3.1 Canonical representation

`parse_timed_canonical_files()` 复用 `text/canonical_lyrics.py`，同时提供：

```text
TimedCanonicalOccurrence.time_ms
TimedCanonicalOccurrence.tokens[]
CanonicalToken.start_ms/end_ms
line_lrc / enhanced_lrc / qrc_word_timing
RepairCanonicalLine text view
```

首 token onset 在合理 line-local 范围内可成为更细 timing onset；token end/LRC line break 不直接强迫 final SRT end 或 cue segmentation。

### 3.2 Primary timing identity / affine model

主 timing identity grade：

```text
A: exact + unique + 1:1 + unchanged
B: 1:1 + high similarity + safe text repair
C: merge/split/gap/repeated/ambiguous/sequence/BPM-recovered/other
```

只有 A 可建立 `SongTimingModel`。普通单曲仍优先：

```text
source_time = offset + rate * mix_time
```

- `exact_daw` -> hard rate prior；
- `bpm_derived` -> soft plausibility only；
- 无 hard prior -> A anchors robust pairwise median rate；
- candidate 自动 timing repair 使用 leave-one-out / independent support，不能用自身证明自身；
- B 只能被 already-ready A model 二次确认，不得建立 primary model；
- C 永不建立 primary model。

`SongTimingModel.status == "ready"` 只说明这个 affine model 的证据已足以用于 prediction。它本身不授予某个 cue 自动 timing write-back 权限；cue 仍必须逐项通过 identity、residual、shift、BPM conflict、overlap 等 gate。为避免 report 误读，primary timing model payload 保留 legacy `status`，同时增加：

```text
prediction_ready = (status == "ready")
status_semantics = prediction_readiness_not_auto_repair_authority
```

### 3.3 v1.1.x ready-model text recovery

`text_recovery.py` 保留两条更强 no-audio text recovery：

1. bilateral interior：ready four-A timing model + 左右同源 strong text anchors + complete canonical gap + compatible onsets；
2. narrow song-edge：ready model + one-sided consecutive strong anchors + edge scope + tighter onset guard，并只允许真正 unmapped editor-only ad-lib 作为透明层。

成功 reason：

```text
timing_model_confirms_canonical_sequence
timing_model_confirms_song_edge_canonical
```

它们仍在 Sequence Projection 之前执行，因为 independently-ready timing model 是更强证据。

### 3.4 为什么需要 Sequence Projection

真实 severe-ASR 会出现 bootstrap deadlock：

```text
editor text 错成另一句话
-> lexical span similarity 低 / mapping 错
-> 只有 3 个真正 A anchors
-> primary timing model 按四-A gate不 ready
-> ready-model text recovery 无法启动
-> 错误 editor text 被原样 materialize
```

放宽 lexical threshold 或四-A timing gate都会增加 false repair / circularity。v1.2.0 因此新增**只用于文字 identity 的独立投影**，不改变 timing gate。

### 3.5 SequenceProjectionModel

`sequence_reconcile.build_sequence_projection_models()` 从 baseline text decisions 建 text-only affine projection。

无 exact hard prior：

```text
>= 3 unique/exact/1:1/unchanged A text anchors
>= 4 total A/B strong text anchors
source span >= 8000ms
mix span    >= 8000ms
robust pairwise rate in [0.5, 2.0]
median abs residual <= 450ms
750ms inlier fraction >= 0.75
```

有 exact hard rate prior 时可在 `>=2 A strong anchors` 下使用 hard rate + median offset。BPM-derived prior不进入 hard map。

这个 model 的唯一职责：**判断 canonical sequence 在 editor 时间轴上的大致投影位置，从而恢复文字 identity。** 它不是 `SongTimingModel`，也不能授权 timing repair。

### 3.6 Anti-circularity：sequence text 永远不变 timing anchor

`sequence_reconcile._projected_decision()` 将所有 sequence-projected decision 的 score cap 到 `0.91`：

```text
score < 0.92
=> anchor_repair._decision_grade() 最多 C
=> 不可能成为 A/B timing anchor
```

因此典型目标行为是：

```text
baseline: 3 A + 1 B
text-only Sequence Projection: ready
severe-ASR text: safely recovered
final primary timing model: anchor_count 仍然 = 3
final timing status: 仍可能 insufficient_anchors / review
```

文字可以确定而 timing 仍 unresolved；这是刻意设计，不是失败。

### 3.7 Strongly bounded canonical sequence reconciliation

对 model-consistent strong anchors 按 editor cue 顺序排序。只有**相邻 strong anchors 同 source**且中间至少存在一个 review 时，才尝试 bounded sequence reconciliation。

设：

```text
left strong anchor
editor weak/review cues...
right strong anchor
```

两侧 canonical ordinal 唯一确定完整 gap。`_partition_bounded_region()` 不使用乱码 lexical similarity作为入场门槛，而是把 gap 的连续 canonical rows 分给现有 editor cues。

约束：

- strong anchors 自身 residual <= 750ms；
- block 最多 16 cues；
- canonical row count 必须在 `[cue_count, 4*cue_count]`；
- editor starts 与 projected onsets 单调；
- 每 cue 第一 projected onset 与 editor start <=1300ms；
- assigned last onset 不得明显越过 editor cue end；
- canonical gap 必须完整消费，不能丢行/回退/跨 source。

partition cost：

```text
current cue first-onset error
+ next canonical onset vs next editor cue start boundary error
+ small text-length ownership penalty
```

关键点是**用“下一条 canonical onset 是否对得上下一 editor cue start”决定 1↔N 分配**。例如 8 条短 LRC lines 可以稳定分入 4 个较长 editor cues；不会因为 LRC 有 8 行就创建 8 个 SRT cues。

成功 reason：

```text
sequence_projection_confirms_bounded_canonical
```

### 3.8 Frontier walk

如果 severe-ASR 位于最前/最后 strong anchor 外侧，`reconcile_text_from_sequence_projection()` 可沿 source canonical 顺序向外逐 cue 处理。

保守停止条件：

- current first projected onset 与 cue start >900ms；
- editor time 非单调；
- 遇到另一 model-consistent strong anchor；
- 没有可接受 candidate；
- 已证明当前 cue 后，下一 canonical onset 与下一 editor cue start boundary delta >1600ms。

frontier multi-line assignment 还要求 editor/canonical similarity >=0.42；这是为了防止在只有单侧序列约束时，仅凭 timing 把多条 canonical line 塞入一个弱 cue。

成功 reason：

```text
sequence_projection_confirms_frontier_canonical
```

这个 stop rule 是 cut/ad-lib 安全边界：允许恢复 break 之前已经被证明的 lyric cue，但禁止越过 break 继续追更远 LRC。

### 3.9 Segmentation authority

Sequence Projection 不处理纯 Standard-safe、无 review 的 region。因此已有可信 editor cue ownership不会因为新层存在而被重新分句。

对 severe-ASR bounded region，canonical 决定完整字符/行顺序；projection 只决定这些 canonical rows 应归属哪个**已有** editor cue。cue number/start/end 不改变。

要真正移动 editor cue boundary，仍需要 Enhanced/QRC word evidence 或 Pro/Max audio evidence；line-LRC 不足以授权。

### 3.10 Final timing / overlap safety

Sequence reconciliation 后重新调用 `build_anchor_timing_plan()`。sequence/BPM decisions 是 C grade，不增加 A model anchors。

`smart_policy.py` 的 timing hardening继续：

- `timing_model_not_ready` / no unique mapping / C identity -> review/Pro escalation；
- soft BPM conflict 阻止 mutation；
- 单 cue repair不得制造新 overlap；
- 所有 repairs 合成后逐相邻 pair 检查：`new_overlap_ms <= original_overlap_ms`；否则相关 repair全部降 review。

### 3.11 BPM-validated text-only recovery（v1.2.2 + v1.2.3/v1.2.4 hardening）

`bpm_sequence_reconcile.py` 解决的是：已知每首歌通常以固定 BPM 变速到成片，但 repeated lyric / severe-ASR 导致 unique A anchor 不足时，怎样安全利用该信息帮助**文字**而不提升 timing 权限。

`bpm_derived` 仍不是 hard rate。每个 source 的 BPM text projection 要进入 `ready`，至少要求：

```text
>= 3 baseline-safe 1:1 text anchors
750ms 固定-rate inlier >= 3
inlier fraction >= 0.75
median abs residual <= 300ms
mix/source span >= 8000ms
anchor canonical order strictly monotonic
pairwise anchor-estimated rate 与 BPM rate relative error <= 2.5%
```

baseline-safe anchor 只接受原 Text Repair 已安全成立的：

```text
canonical_content_matches_source_segmentation
high_confidence_span_preserving_match
```

BPM mapped 1:1 recovery 只考虑当前仍为 `review` 且已经有**单一 canonical occurrence claim** 的 cue。候选还必须通过：

- projected onset 与 editor cue start 的紧阈值；
- interior 前后 inlier bracketing，或极窄 strict leading-edge 条件；
- adjacent cue 不得同时 claim 同一 canonical occurrence；
- split-continuation 风险阻断；
- 下一 lexical canonical 不得已经明显落入当前 cue；
- pure vocalization cue 不得被填成 lexical lyric。

此外，单条 BPM recovery 不能把 LRC line break 当成 editor ownership 真源。`_adjacent_lexical_overlap_risk()` 在 materialize 前检查当前 editor cue 的 normalized 文本：若 cue 开头已经包含上一 lexical canonical 的至少 2 字连续尾部，或 cue 结尾已经包含下一 lexical canonical 的至少 2 字连续前缀，则说明 editor 已经识别到跨 LRC 行的真实片段，该 cue 必须继续 review，禁止用单条 canonical 覆盖并删除相邻歌词。该 guard 只收紧 auto-recovery，不新增 mapping，也不改变 timing authority。

v1.2.3 bilateral bounded-stream tier 可在同源、双侧 baseline-safe BPM inlier anchors 之间处理 bounded review region；它必须保持所有 lower-mode resolved cue 不变，unmapped cue 需要最低 lexical 支持，mapped review 不得扩大现有 canonical span。v1.2.4 还规定 multi-cue bounded tier 遇到 Latin/mixed target gap fail closed，直到 display renderer 具备 token-boundary-aware repartition。

生产 unmatched 语义统一为：

```text
canonical_span is None -> unmapped
canonical_span == (x, x) -> unmapped
positive-width canonical span -> mapped
```

bounded-stream 的 `bounded_stream_unmapped_cue_count` 只统计最终真正 materialize 的 unmatched recovery；candidate 在 vocalization、lexical floor、span ownership、Latin layout 或 inherited ownership guard 等任一环节被拒绝时，不得计数。

可选 vocalization trim 仅允许：editor 文字去掉边缘 `哦/啊/耶/oh/yeah/...` 后，normalized text **精确等于** canonical。此时只去掉多余 vocalization，不改变 canonical ownership。纯 vocalization 继续保留 review/供生产策略处理。

BPM-recovered decision 继续保持低权限：score cap 在 B-grade 以下，不得成为 primary timing anchor，也不得反向使自身 projection ready。

成功 report 字段：

```text
text_bpm_projection_recovery_count
text_bpm_projection_vocalization_trim_count
text_bpm_bounded_stream_cue_count
text_bpm_bounded_stream_region_count
text_bpm_bounded_stream_unmapped_recovery_count
text_bpm_projection_models
```

`text_bpm_projection_models` 与 `text_sequence_projection_models` 都是 text-only diagnostics，不能与 primary timing `models` 混用。

### 3.12 v1.2.1 editor cue ownership guard + v1.2.4 maintenance scope

`timeline/ownership_guard.py` 位于全部 text recovery 之后、SRT text materialization 之前。输入是原 editor cues、当前 text decisions 与 replacements。它只检查相邻 cue 边界：若原 editor 可识别文本能证明 2–6 字短语属于另一侧，而当前 Sequence reconciliation 结果把该短语搬错，则可在相邻 cue 之间搬回；若同一短语被重复到边界两侧，则只允许删除一份已证明的短重复副本。普通搬移必须保持 pair-combined normalized lyric stream 完全不变。输出 decision 固定低于 B-grade，并清除 canonical span 以阻止其成为 timing anchor。

v1.2.4 maintenance 明确 guard 的修改权限：**boundary move 和 duplicate-drop 都只在 `_eligible_pair()` 成立时运行，而 `_eligible_pair()` 要求相邻 pair 至少一侧 reason 以 `sequence_projection_confirms_` 开头。** 普通 Standard/baseline pair 不因为碰巧出现 2–6 字边界重复就获得删字路径。旧 `_eligible_pair()` 中在前置 return 后永远不可达的条件已删除，不改变实际允许集合。

### 3.13 Report

既有字段：

```text
text_review_count_before_timing_recovery
text_timing_recovery_count
text_timing_recovery_block_count
text_edge_timing_recovery_count
text_edge_timing_recovery_block_count
text_review_count
```

v1.2.0 新增：

```text
text_sequence_reconciled_cue_count
text_sequence_reconciled_region_count
text_sequence_resolved_review_count
text_sequence_frontier_cue_count
text_sequence_frontier_run_count
text_sequence_projection_models
```

v1.2.2+ 新增/保持：

```text
text_bpm_projection_recovery_count
text_bpm_projection_vocalization_trim_count
text_bpm_bounded_stream_cue_count
text_bpm_bounded_stream_region_count
text_bpm_bounded_stream_unmapped_recovery_count
text_bpm_projection_models
text_mapped_review_count
text_unmapped_review_count
```

`text_mapped_review_count / text_unmapped_review_count` 必须使用与 production BPM layer 相同的 zero-width unmatched 语义。Primary timing `models[]` 保留 legacy `status`，并额外暴露 `prediction_ready/status_semantics`，避免把 model readiness 误读为 cue mutation authority。

### 3.14 Artifact path safety

`io/path_safety.py::validate_separate_artifact_paths()` 继续在任何 artifact write 前拒绝 output-input / output-output 路径碰撞。

## 4. Pro / Selective Audio Repair v1.2.7

Pro 仍是 staged evidence path：

```text
Smart unresolved
-> reason-aware bounded plan
-> selected local acoustic / ASR / forced evidence
-> fail-closed automatic adjudication (decision queue only)
-> residual manual review/calibration
```

v1.2.7 允许 `decision_support_no_srt_mutation` 范围的自动 adjudication，但不自动关闭 review；`automatic_review_resolution_allowed=false`、`timing_mutation_performed=false`、`automatic_timing_change_allowed=false`、`automatic_text_change_allowed=false` 继续保持。

### 4.1 Exact Smart policy binding

`build_selective_repair_plan_v11()` 必须验证：

```text
smart_report.schema_version == SMART_SCHEMA_VERSION
smart_report.policy_id      == SMART_POLICY_ID
```

当前 policy 为 v1.2.10（由 `lyric_aligner.timeline.smart_current` 稳定 facade 选择）；旧 policy report 自动 stale，必须重跑 Smart。

### 4.2 Reason-aware routing

```text
timing review + mapped canonical -> source_local_acoustic_match
text/identity review             -> mix_asr + word_timestamps
no word timing + source identity needs reinforcement -> source_forced_alignment
unmapped review                  -> mix_asr + word_timestamps only
```

如果 Smart 已安全恢复 text、但 timing 仍 review，Pro 只按剩余 timing reason 路由，不重复做已解决 text review。

Pro 只能处理 Smart 明确 unresolved 的 cue；因此 Smart false-ready 不会被 Pro 自动兜底，segmentation/sequence monotonicity contract 必须在 Smart 自身 CI。

### 4.3 Existing hardening

继续保持：Enhanced LRC final token `end_ms=None` compatibility、adaptive source window、ASR-only region isolation、final `max_jobs` cap、shadow competitors、per-line language hint、only-needed source hash/bind 与 path safety。

## 5. Max / Full V4

Max 保留 coarse/Fine/cut/transition/overlap/ASR/forced/P9 等完整能力，只处理 broad untrusted timeline 与复杂 source identity。

Max 也必须遵守 segmentation authority：line-LRC 本身不能强迫 final subtitle cue boundary；推翻可信 editor segmentation 需要更强 word/token/audio evidence。

### 5.0 Task-local semantic run configuration

`lyric_aligner/contracts/run_config.py` 负责 `v4-run-config-1.0`。raw `task_manifest.json` 继续只绑定原始任务输入；run config 绑定后补 semantic config：`profile / language_map / middle_cut_map / lyric_role_map`。每个非空角色记录 repository-relative path、size、SHA-256，config 本身绑定 task fingerprint 并生成 deterministic `run_config_fingerprint_sha256`。

`init_task.py` 默认创建 all-null 或指定语义输入的 config；`init_v4_run_config.py` 用于旧任务/后续迁移。已有 config 与新语义不同必须显式 `--replace`，避免“重复运行初始化命令”暗中改变 production semantics。

三个 public Max run wrapper 在进入 output lock/cache/stage write 前运行 `expand_run_config_argv()`，把 config 中非空角色自动展开为原生产 parser 已支持的 flags；wrapper-only `--run-config` 同时进入 output-tree protected inputs，完成 preflight 后由 `strip_run_config_control_argv()` 移除。原 optimized/legacy implementation blob 不需要理解新的 control file。不存在 config 时保留历史显式 flags 行为。

该层不新增 timing/text authority。TrackAsset artifact 仍记录实际 profile/map SHA，因此两个 path 不同但内容/角色相同的 config 在 production evidence 上仍由实际 semantic content 身份决定。

### 5.1 Canonical role preflight contract

`assets/lyric_roles.py` 与 `text/canonical_lyrics.py` 必须对 consumer-LRC 的 non-lyric timestamp groups 使用同一过滤语义。role preflight 在建立 TrackAsset `canonical_selection` 前：

- 先移除 Enhanced/QRC timing markup 并执行 shared `clean_text()`；
- `is_metadata_text()` 命中的 credits/role labels，以及首 2 秒内 `is_title_like_intro()` 命中的严格 `artist - title` title row 标记为 metadata；
- 清洗后为空的 timing-only group、以及 metadata-only group不进入 `canonical_selection`；
- 同一 timestamp 的 metadata + lexical lyric 可以保留原 alternative index，并选择唯一 lexical original；
- 两个或更多真正 lexical alternatives 仍必须由唯一 language-native identity 或 explicit original-index override 解决，否则 `LyricRoleError` fail closed；
- override 不能把 metadata 重新引入 canonical truth。

TrackAsset schema 保持 `1.1`，`canonical_selection_sha256` 继续只绑定实际 selected lexical originals。role summary 的 `ignored_blank_group_count` / `ignored_metadata_group_count` 是诊断字段，不授予任何 reconstruction、source identity 或 timing authority。

### 5.2 Coarse path coverage contract

`audio/coarse_mapper.py` 的 retrieval evidence 与 selected path 是两个不同集合：`windows` 始终保存 interval 内全部 retrieval rows，`path` 只保存从首窗开始、由 monotonic DP 连续证明的 prefix。正常情况两者等长；若只在 terminal boundary 失去连接，builder 最多允许排除 `ceil(window_seconds / step_seconds)` 个 trailing rows，且 prefix 至少为 TimeWarp 所需的三个 anchors。

Payload 的 additive `path_coverage` 记录 `status`、retrieved/selected/excluded counts、derived maximum 与 excluded mix centers。算法不允许 leading skip、interior skip 或断后重连。Fine 只消费 `windows[:len(path)]`，并逐点验证 mix center；transition probe 仍读取完整 `windows`，所以被排除的 boundary retrieval evidence 不会消失。该 coverage contract 不改变 calibration profile、TimeWarp gate 或 transition/cut/overlap authority。

Coarse CLI 另外用 fingerprinted `purpose` 分离职责：`primary_timewarp`（默认）执行上述 DP/TimeWarp；`transition_activity` 只做共享边界的 per-source retrieval，返回全部 `windows`、空 `path`、`path_coverage.status=retrieval_only` 与 `timewarp.selection=NOT_REQUESTED`。transition activity 的 downstream consumer 只能是读取 raw window strength/ambiguity 的 probe，不能送入 Fine、timeline projection 或当作 Source-to-Mix mapping。purpose 同时写入 payload、normalized config 和 artifact evidence，因此 resume 不会跨 purpose 复用。

### 5.3 Calibration-gated direct-final-mix boundary authority

Max 的 boundary-authority 子系统把 outer `start/end` 与 long-cue `internal` split 作为独立 authority kind。核心职责分布在 `alignment/production_backend_profiles.py`、`alignment/boundary_executor.py`、`alignment/internal_executor.py`、`alignment/batch_executor.py`、`evaluation/human_boundary_anchor.py`、`evaluation/human_gold_prediction.py`、`evaluation/production_calibration.py`、`timeline/boundary_refinement.py` 与 `timeline/internal_segmentation.py`。canonical lyric 只提供文字/顺序与 lexical contract；editor boundary 是强但可推翻 reference；LRC onset 只能 routing/search，不能直接创建或移动 production boundary。

锁定的 full benchmark 固定为 60 clips / 90 boundary points，仅承担 diagnostic population。production authority 使用从该 pre-model population 确定性投影的 24 clips / 36 points，每个 `start/end/internal` 12 点并固定为 8 calibration + 4 holdout。machine consensus 可以在人耳 gold 之前对 full population 运行，但 authority 永远是 candidate/diagnostic；机器输出、模型一致或 acoustic onset 都不能写回 human gold。人工题目本身若被证明 target 不在 locked clip 内，必须以预先锁定 population 中的 deterministic replacement 替换，旧 gold 只保留历史证据。

SOFA 与 HuBERTFA 都直接读取 final mix。对 outer start/end，`padded_window_edge_clamp_reason()` 会把“预测极靠 padded window 边缘且已远离 editor reference”的明显 lexical-state collapse 降为 unavailable；internal 不应用该 guard。locked final-mix clip 是 hard bound，editor owning cue 对 internal 只作 reference。缺失预测降低 calibration coverage，不能通过裁剪到 editor/window 边缘伪造成有效点。

production calibration scope 同时绑定 `model_revision`、底层推理依赖的 `implementation_revision` 与完整 observer runtime 的 `adapter_contract_revision`。该 contract revision 不是只哈希四个 sidecar adapter，而是把 gold/boundary/internal/batch adapters 与 boundary/internal/batch executors、forced-alignment edge guard、window policy、lexical contract、human prediction 和 calibration core 一起纳入；因此 blind prediction 只能在生成时的完整 observer contract 与当前 contract 完全一致时用于 production calibration，不能由 materializer 事后补签当前值。旧 artifact 可以继续读取做历史统计，但缺任何当前 runtime provenance 时不得取得 production authority。真正的 timing mutation 仍要求同一 boundary kind 的两个独立 backend/correlation group 都通过 human calibration，并在具体点形成符合 spread/identity/plan/audio lineage 的独立 evidence；suite 通过本身不会直接修改字幕。

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

Public tests必须证明：

- Standard/Smart 不因 LRC 行换行不同跨可信 editor cue 搬字；
- Smart primary timing four-A/leave-one-out contracts不变；
- 3A+1B 只能建立 text-only Sequence Projection，不能提升 primary timing anchor count；
- severe-ASR bounded canonical sequence 可在 projection 稳定时恢复；
- projection 证据不足/不稳必须 fail closed；
- BPM-derived rate 只有被多个 baseline-safe anchors 验证后才可提供 text-only recovery，不能成为 timing hard prior；
- pure vocalization、split continuation、重复 occurrence/邻 cue 冲突等必须 fail closed；
- editor cue 已识别上一 canonical 尾部或下一 canonical 前缀时，BPM 单行 recovery 必须 fail closed，不得删除该相邻歌词 ownership；
- optional vocalization trim 只能在剩余文字精确等于 canonical 时执行；
- BPM/sequence recovered text 不能增加 A/B primary timing anchor；
- frontier 遇到 timing break/cut/ad-lib 必须停止；
- ownership guard 不得修改非 Sequence reconciliation pair；
- zero-width canonical review span 必须按 unmapped 报告，rejected bounded candidate 不得计入 unmapped recovery；
- primary timing model `ready` 必须在 report 中明确为 prediction readiness，而非自动 mutation authority；
- existing ready-model bilateral/song-edge recovery继续成立；
- insufficient/unvalidated timing 必须 Pro escalation；
- final combined overlap 不新增/扩大；
- exact DAW hard prior 与 BPM-derived soft prior；
- Enhanced LRC / stale Smart / acoustic source-window / ASR-only region / max-jobs / path collision / forced protocol / multilingual routing继续不回归；
- Max role preflight 与 canonical parser 对 metadata/title/role-label/blank-only groups保持一致，同时真实 lexical ambiguity 继续 fail closed。

Public CI 不能证明真实歌曲 false-auto。每次 private real-song failure 应抽象成同构 synthetic regression，禁止歌曲、cue、timestamp、BPM 或真实歌词 hard-code 到 production algorithm/public test。

### 3.15 v1.2.2 report / diagnostic semantics hardening

`smart_policy.py` 的 report 层增加只读诊断，不改变 text/timing mutation gate：

- `_bpm_prior_compatibility()` 跳过 `rate_source=none/invalid` 的 placeholder model，只比较真正有 timing-rate evidence 的 source；
- `_text_materialization_counts()` 从实际 materialized text-only SRT 计算 exact display change 与 normalized semantic change，避免把 `MatchDecision.action` 误当成最终文件 diff；
- review reason counts 与 mapped/unmapped text review 直接由最终 decisions 汇总；zero-width canonical span 与 `None` 一样属于 unmapped；
- timing review 按 `proposed_start_ms/proposed_end_ms` 是否存在拆成 concrete proposal 与 no-proposal 两类；后者表示当前 no-audio 证据不足，不能被解释为已知 timing 错误；
- `text_status/timing_status` 与 `pro_text_escalation_required/pro_timing_escalation_required` 是 strict overall status 的可解释分解，旧字段继续兼容；
- timing model payload 的 `prediction_ready/status_semantics` 只澄清 legacy `status`，不新增 timing 权限。

### Smart v1.2.3 BPM bounded canonical stream

`timeline/bpm_sequence_reconcile.py` may consume a complete lexical canonical gap between adjacent same-source BPM inlier anchors and repartition that stream across the existing editor cues. `_assign_targets` is used only inside a region that has passed BPM projection, bilateral-anchor, source-consistency, length, vocalization/ad-lib, boundary-insertion, short-cue, unmapped lexical-floor, and lower-mode immutability guards. Canonical row boundaries remain non-authoritative: one canonical row may intersect more than one editor cue. The resulting decisions use `sequence_projection_confirms_bpm_bounded_stream`, remain C-grade/below B timing authority, and cannot feed timing model construction.

### Smart v1.2.4 bounded-stream production guards

`timeline/bpm_sequence_reconcile.py` normalizes absent and zero-width canonical claims into one unmapped semantic state. The v1.2.3 bilateral stream path is further constrained so a previously mapped review cannot expand beyond its existing canonical span; this prevents canonical correctness at region level from overriding editor cue ownership. Until token-boundary-aware Latin rendering exists, the new multi-cue bounded tier rejects gaps containing Latin text; the older mapped 1:1 BPM text path remains unchanged. Maintenance review keeps the corresponding report/counter semantics aligned and narrows final ownership mutation back to the reconciliation pairs that justify it.

### Smart v1.2.4 final-acceptance ownership invariant

`ownership_guard` has two distinct permissions. `boundary_move` remains Sequence-reconciliation-only. `duplicate_drop` may run for a Sequence pair **or** when at least one adjacent cue has a materialized upstream `replace` whose normalized `output_text` exactly equals the current working text and differs from the original editor cue. The duplicate must still be 2-6 normalized characters, be present on both sides, be assigned to only one side by the original editor recognition, improve pair similarity, and leave both cues non-empty. This keeps the guard from acting as a free-standing baseline editor while preserving Text Repair duplicates that the guard historically removed.

### Pro v1.1.2 reason-aware selection budget fix

`build_selective_repair_plan_v11()` 不再把 legacy base planner 的 pre-routing `max_jobs` 截断当成最终 candidate 集。它先用扩大到本任务 cue 数的内部 planning config 建完整 unresolved pool，再读取 Smart `timing_decisions[].proposed_start_ms/proposed_end_ms` 与 text-review 状态，对 primary jobs 赋 selection tier：

```text
0 text_review_with_timing_proposal
1 text_review / timing_review_with_proposal
2 timing_review_without_proposal
```

按 tier、cue ordinal、job id 稳定排序后才应用用户原始 `max_jobs`。只有被选中的 primary job 才能占用 shadow boundary competitor slot。Plan summary 记录 candidate/deferred/tier counts；后端路由、局部 acoustic/ASR/forced 算法和证据阈值完全不变。

### Pro v1.1.3 primary budget vs. shadow evidence

The reason-aware selector first chooses primary unresolved cues under `max_jobs`. Only after that selection, `_boundary_competitor()` may add a dual-source shadow job for a selected primary near a source boundary. Shadow jobs do not consume the primary budget, cannot select new unresolved cues, and retain evidence-only/no-timing-mutation semantics.

### Smart v1.2.6 role normalization and product semantics

`text/normalization.py` is consumed by the shared canonical parser **before canonical lines and ordinals are established**; it is not a post-Smart timing cleanup. It filters explicit role words, separated casts and explicit parenthesized roles, but a bare CJK name/short colon line fails closed as lexical because a surname list cannot establish metadata identity. `timeline/smart_policy_v126.py` separately wraps the frozen v1.2.5 result, applies `final_text_recovery.py` after timing is frozen, and reports the validated/suspected/unvalidated timing split.

### Smart v1.2.7 anchored cross-script recovery

`timeline/smart_policy_v127.py` wraps the frozen v1.2.6 artifact after timing finalization. `final_text_recovery.py` may replace a mapped 1:1 review only when the preceding resolved decision ends exactly at the candidate occurrence, both occurrences share a source, and editor/canonical text are narrow cross-script vocalizations. Ordinary lexical text, missing adjacency and boundary movement fail closed. The layer never rebuilds timing.

Smart actionable hypotheses are separately stratified by local model strength (`>=6` inliers, `>=0.80` inlier fraction, median absolute residual `<=250ms`) and text-identity value. These fields rank Pro evidence acquisition; they are explicitly not vocal-onset probabilities.

### Pro v1.2.2 value selection and decision fusion

`alignment/selective_policy.py` computes `timing_proposal_abs_shift_ms` from Smart proposal vs. editor start. Primary order is actionable text+timing/actionable timing with strong local models before weak/unknown models and then descending absolute shift, followed by text review, display-tolerance timing suspicion and timing-unvalidated. `alignment/local_acoustic_v11.py` schema 1.2 records `acoustic_shift_ms = predicted_mix_start_ms - editor_start_ms`; its gate is explicitly unadjudicated retrieval evidence.

`alignment/selective_fusion.py` binds the exact current Smart and Pro policy and produces decision support only. Smart and acoustic hypotheses that agree in direction/magnitude become supported; a local match inside product display tolerance can rebut a materially different Smart hypothesis; large disagreement stays conflict. The agreement is explicitly labelled correlated canonical-timeline evidence, not independent vocal-onset evidence, so resolved-text timing support/conflict stays medium. A supported one-to-one canonical occurrence can independently support cross-script text identity and enter the smallest high queue, but neither text nor timing is automatically written. The decision summary includes exact positions and preserves `timing_mutation_performed=false`.

### Smart v1.2.8 / Pro v1.2.3 production-safety correction

`timeline/smart_policy_v128.py` leaves v1.2.7 decisions unchanged but restores two separate product counts: every actionable timing suspicion remains in `manual_timing_review_candidate_count`, while `timing_high_value_pro_candidate_count` is only a budget-priority subset. Any actionable suspicion prevents `product_status=ready`.

`alignment/local_acoustic_v11.py` schema 1.3 records the exact slope interval and detects an optimum at/near either endpoint using half a search-grid step. Retrieval success remains visible, but only an explicit interior optimum receives `timing_fusion_evidence_eligible=true`. Fusion fails closed on boundary-limited or legacy unqualified artifacts: they cannot support/rebut Smart or become Pro-only timing anomaly authority. Search remains local/bounded and score/margin thresholds are unchanged. Forced alignment remains auxiliary and never sets independent mix vocal-onset evidence true.

### Smart v1.2.9 / Pro v1.2.4 contextual role correction

Real production acceptance showed that unconditional bare-CJK fail-closed behavior retained genuine ensemble member labels and polluted canonical ordinals. The shared parser now builds a same-file role context before selecting canonical lines. Explicit multi-person rows directly prove exact CJK members. An otherwise unlisted bare member is inferred only after at least four distinct proved bare labels establish an ensemble grammar, the candidate repeats, and every occurrence is followed by lexical text within two seconds. This removes no generic single lexical label and does not use a surname list. Standard, Smart and Max preflight consume the same context rule. Smart policy advances to v1.2.9 and Pro binding to v1.2.4; timing/acoustic authority is unchanged.

### Smart v1.2.10 / Pro v1.2.6 split-line, budget and local-search correction

Smart v1.2.10 enables a version-scoped `anchor_repair.py` guard; historical v1.2.9 and earlier entry points keep their frozen behavior. The guard treats a line-LRC onset as authority only for the first editor cue in a multi-cue-to-one-canonical span. For an internal cue it requires the concatenated editor span and canonical token stream to match exactly after normalization, and the editor boundary must land at a strictly later, line-local reliable token onset. Otherwise the decision is `segmentation_internal_boundary_unvalidated` with no proposed timing. This removes repeated-line-onset false suspicions without changing the rendered SRT or granting new timing mutation authority.

`selective_policy.py` consumes `timing_high_value_pro_candidate_positions` as a first-budget key while retaining all actionable suspicions in the independent manual queue. For ASR, planner and executor distinguish an absent override from explicit `asr_force_auto_detect`: only canonical-local language that agrees with the known source language is pinned. Cross-language local lines, mixed/unknown and source-auto remain backend auto-detection because the bounded timing-search window may contain adjacent vocals. Pro v1.2.6 acoustic schema 1.4 additionally records the valid source-start search interval and removes timing-fusion authority when the winning source position hits or approaches either local boundary. Automatic text/timing mutation remains disabled.

### Pro v1.2.7 automatic adjudication without mutation

`alignment/selective_fusion.py` 将 Pro decision artifact 升级为 schema `1.1` / policy `pro-selective-decision-fusion-2026-09-04-v1.3`，而 selective planner 继续保持 `smart-to-pro-reason-aware-2026-08-22-v1.2.6`。新层只消费已经执行并通过原有 identity/boundary gate 的 evidence，不改变 region、ASR、forced、acoustic search 或 Smart budget 语义。

裁决按 timing/text 两轴分离。Timing 不自动关闭任何 review：`smart_candidate_supported` 生成 `candidate_confirmed_advisory` 和 Smart proposed start/end；`smart_candidate_rebutted` 生成 `keep_editor_advisory`；segmentation/conflict/anomaly/unvalidated 继续 investigate。Text evidence 也只收敛为 canonical text/occurrence support advisory，不自动把 Smart text review 标成 resolved。原因是 timing/text review 都可能携带 segmentation、identity、neighbor-support、shift-limit 或 structure 风险，相关 acoustic/ASR evidence 不能独立解除这些约束。

该 authority 仅为 `automatic_adjudication_no_srt_mutation`，scope 固定 `decision_support_no_srt_mutation`；`automatic_review_resolution_allowed=false`，所有 review 仍保留人工确认。`automatic_timing_change_allowed=false`、`automatic_text_change_allowed=false`、`timing_mutation_performed=false` 与 `independent_vocal_onset_evidence_used=false` 均继续保持。

### Max outer Expected-Loss 1.1 + candidate-specific local support

`timeline/boundary_risk.py` 的 Expected-Loss fallback 与精确 boundary authority 分离。aggregate Human-Gold holdout profile 只能说明 estimator 在某个人群上的总体风险，不能证明当前单条 candidate 合理；因此 1.1 新增 `BoundaryLocalSupport`，精确绑定 `support_id / boundary_kind / candidate_ms / estimated_p90_error_ms / contradictory / provenance_sha256`。production-authoritative local support 必须有有效 SHA provenance，并与当前 candidate timestamp/kind 完全一致；缺失、stale、contradictory 或绑定错位均 fail closed。`risk_provenance.py` 同时强制 editor/candidate risk profile 来自同一 Human Gold、records、selection lock、final audio、boundary kind 与 population；production fallback 只接受 holdout population，catastrophic threshold 也必须一致。

`timeline/max_next_adjudication.py` 1.1 仍只生成 `selection_recommendation_only_no_srt_mutation`。Tier A precise、Tier B calibrated-better、Tier C rescue、Tier D structural ambiguity 都经过同一个下游 global/semantic veto；即使推荐自动 selection，`production_mutation_allowed=false`。本轮没有增加消费 Max Next 的 production materializer，也没有让 aggregate profile 直接变成 SRT write-back authority。

Independent Fine 的 local-support 研究使用独立于歌词 Human Gold 的 known-transform benchmark：真实 source FLAC 与 DAW 调速 WAV 先由 broadband RMS affine audit 验证稳定 slope/intercept，再将 truth 与 Independent Fine prediction 物理分离。calibration 只允许在未看 holdout 前冻结 selector；holdout pair selection、RMS truth audit、acceptance protocol 都在首次 prediction 前 hash-bound。最终冻结 selector `ambiguous=false + margin>=0.05` 在 blind holdout 上因 coverage 66.67%、max 1040.14ms、catastrophic 1/16 失败，所以该 observer 继续 `evaluation_only_never_direct_timing_authority`。holdout artifact 明确禁止从 holdout 反调 threshold；production outer Expected-Loss 因没有 candidate-specific authoritative local support 而保持关闭。

### 维护说明（2026-09-03）

TrackAssets、task manifest/QA JSON 与 task-local run config 统一使用 shared `atomic_write_json()`；canonical evaluation render 的 SRT/audit CSV 使用同目录临时文件、`fsync` 与原子 replace。它们只提高 crash/interruption/concurrent-write safety，不改变 schema、rendered content、asset selection、semantic fingerprint 或 authority；runtime base direct dependency 明确含 `soundfile`。这不是新算法能力。
# 已确认外边界自动复用（2026-09-07）

`timeline/human_boundary_reuse.py` 的职责是把已存在的 human gold 应用到同一 final mix 和同一 lexical target，不训练模型、不授予泛化 authority。输入先由现有 editor-risk/gold validator 核对 lock、gold 和 exact boundary identity。原 cue 如被内部拆分，只允许改第一段 start 与最后一段 end；内部边界、文字及整行 authority 均保持。

数据流：`manifest + selection/gold + baseline CSV/SRT → exact-input validation → confirmed-boundary subset/geometry → new CSV/SRT + decision artifact → exact readback`。

`scripts/v4_reuse_human_boundaries.py` 负责源文件哈希、task fingerprint、旧 report/source cue 关联、output-tree ownership、staging 与成品读回；核心 policy 负责容差内保留、起止组合可行性以及 per-boundary confirmation id。`joint_boundary_geometry.py` 为每行生成最多四种起止组合，用动态规划在整条 timeline 上最大化已确认修正数，平局再比较纠偏距离。约束包括正时长、start 顺序、所有可能受影响的行对 overlap 不增加；活动区间 frontier 保留非相邻嵌套 cue，超过 4096 状态明确拒绝且不输出部分修改；相邻修正可互相使能，结果与 gold 遍历顺序无关。该求解器不产生音频 authority，不能把普通模型 proposal 直接当作授权输入。此附加产物不清除 baseline 的其他 release 问题；通用 row-wide manual marker 不参与这条复用路径。

## 单次准确率升级入口（2026-09-07）

`scripts/v4_upgrade_subtitles.py` 消费版本化 job JSON，按顺序调用已有 calibrated materializer、exact human reuse、paired product evaluation，最后输出 `final.srt / final.csv / upgrade.artifact.json`。每个 calibrated stage 必须提供与其输入 report 精确匹配的 plan/evidence/decisions/bundle，仍由现有生产 materializer 重算授权。普通 timing 缺证据时直接保留，不生成新人工标注请求。display audit 核对实际 display 字段；recovery report 核对原字段，避免把显示长尾修改误计为 acoustic 改进。

整个输出在独立 staging 下完成再重命名。子 artifact 保持原签名，父 artifact 的 `stage_path_relocation` 说明 staging→最终目录映射。复用 policy artifact 同时绑定 geometry 实现 SHA。该入口不包含尚未通过验证的 Max Next B/C 模型升级，也不代替全包发布 QA。


### Receipt 1.1 与逐边界 QA 消费

新 materialization receipt 的 `input_files` 使用相对 receipt 所在目录的角色路径及 SHA，支持整包同层搬迁；原 `inputs` 作为历史定位信息保留。`scripts/verified_boundary_receipt.py` 验证输入角色/hash、manifest/source/audio、gold/lock，重算 `prepare_reuse()`，核对 decisions 与最终 CSV 的全字段，并严格读回最终 SRT 的数量、顺序、文字、起止。仅当前值落在原 human confirmation 容差内的 exact 边界交给 QA，不能用 metadata 字符串代替重放。

`redo_karaoke_pipeline.py qa --boundary-confirmations` 消费上述 scope；只免除已确认的具体边界差异，另一边未获授权仍需 review。新增输入在第一次输出前执行碰撞保护，覆盖 receipt、五种输入角色、lock/gold 声明依赖以及 QA/review/release 三类输出。旧 receipt 1.0 可保留读取为历史证据，但没有这条新的 QA 授权路径。

升级 job 的可选 `qa` 提供已存在的 `audio_alignment / manual_overrides / regression_cases` 后自动执行最终 QA，并把结果和 review CSV 留在成品包。QA 的 review 路径改为最终目录；若生成 release artifact，随后重建它的 QA hash。父 upgrade artifact 的 `qa` 是实测结果，`publish_ready` 仅在真实最终 QA 明确通过时成立；没有 QA 不授予发布状态。发布 artifact 路径指向最终目录并重算自身身份；此发布状态不等于跨项目/未见曲目的精确率证明。带 display policy 的成品 audit 仅支持评估/原样保留；acoustic materializer 必须使用其 pre-display canonical report/SRT，配置不匹配在首次写入前拒绝。


## 连续非词汇人声显示组（2026-09-07）

连续非词汇人声另使用 `reviewed-vocalization-display-group-1.0`：保持原子报告，在独立显示派生文件中合并至多四个相邻重复行，继承两端，不重新划分音素。确定性检查同曲、连续 canonical、重复形式、核对覆盖、间隔、时长与其他同时字幕；receipt 保存成员哈希和两端来源位置，并重放完整输入及输出。显示修复不授予新的声学或发布资格。

## 局部波形配准实验（2026-09-07）

`audio/waveform_alignment.py` 实现 `local-waveform-registration-1.0`，仅针对近等速直接波形对应。三个非重叠 patch 分别要求归一化相关 >=0.90、局部候选峰值 margin >=0.10，再要求局部坐标拟合最大残差 <=3ms。返回每个 patch 的 mix/source 支持区间；未采样间隙、窗外重复版本及歌词边界不在该证据含义内。

`refine_coarse_mapping` 的 `waveform_refinement` 默认为 false；显式启用时把结果放入独立 `waveform_candidates`，不替换 feature path 或 timewarp，不继承旧 Chroma/MFCC 分数用于新坐标。这保留了可自动测量的新算法候选，也防止在尚无正式生产验证时意外解除原 review。新模型/策略需验证坐标对应、结构与最终 SRT 改善后才可正式接入；本轮没有改写历史 artifact 身份。
# 2026-09-08 源音 FLOAT 解码切片

`audio/float_decode.py` 提供显式 FLOAT 重采样和超范围整曲统一增益，`SourceObservationConfig.decode_policy` 在 shadow job 可选开启；默认 legacy observer 1.0 缓存兼容，FLOAT 为 observer 1.1。禁止 FLOAT 自定义 loader 混入同一缓存，解码语义改动必须更新策略 ID。实跑 H180 完整 781 cue shadow，既有 24 人工端点测量未变化，唯一变化 cue 无 gold，尚无精度收益证明。详见 `output/source_context_upgrade5_20260908/float_implementation_report.md`。
# 2026-09-08：独立三行音素观察实现增量

`alignment/source_context_hubertfa.py` 从完整 canonical 与 source-observer 证据准备三行完整窗口，使用共享 contextual interval 校验器提取真实目标双端；`scripts/source_context_hubertfa_adapter.py` 在独立 Python 环境执行本地 ONNX，绑定模型与实际 vendor 文件，显式 CPU4/1，原生 FLOAT 读取并核对源时钟，逐记录隔离推理异常。`v4_shadow_upgrade.py` 分别保留 FW 选择与 HFA 实验 overlay，HFA 不获得生产 authority。此处是新增职责说明，既有身份和历史产物不批量迁移。具体契约见 [原曲上下文与区间联合升级](source-context-shadow-upgrade.md)。
# 2026-09-08 英文发音前端的确定性补全

`lyric_aligner.text.english_lexicon` 将已有HFA词典与可选固定CMUdict生成新的完整词典和逐条来源清单。保留base字节，先由现有lexical tokenizer确认直接词形，再对缺失词处理末尾撇号别名或CMU直接记录；多个不同发音不选首条，未支持音素不映射猜测。不接入神经G2P或歌曲专用规则。模型、canonical、时间带解码及候选选择保持现有契约，变更以新字典内容哈希体现。
# 2026-09-08：相邻目标共同解码与原子选择

第九切片新增 `alignment/source_exact_anchors.py`：完整 canonical 行至少5词，在完整 canonical/observed 流各唯一命中，逐词概率0.7–1、时间有效单调、邻词间隔≤1500ms，并由最近旧 qualified 锚在 canonical/source 双域夹持。所有命中先参与唯一性计数，不能按概率先过滤重复；非单词观察保留屏障。精确锚只供实验 joint 窗口与声学时间带；旧 packet 资格和全局区间几何不变。请求绑定原观察/文本哈希与逐词证据，decoder 分别核验旧 packet 锚和新精确锚身份。当前规则中的概率阈值不是经过校准的准确率。

`alignment/source_joint_context.py` 从完整 source packet lattice 与原始 cue 的完整单行 canonical ranges 构造两个相邻目标共用的新 anchored-path 请求。外锚严格位于两个目标之外，窗口内完整文本与全部合格时间带参与一次推理；不拼接旧响应、不跳过缺词。`scripts/source_joint_overlay.py` 由旧候选的相邻几何冲突触发，验证两个目标的同响应完整区间、occurrence 范围及覆盖该区间的映射检查。`optimize_interval_sequence(..., atomic_proposals=...)` 在原 DP 状态中保持成对选用决定；缺省参数不改变原选择行为。内部、外部几何均继续约束，因此局部解码成功不保证整段采用。

### 2026-09-08 第十二切片实现

`source_observer` 增加默认关闭的 `multilingual` 配置，严格布尔与 language=None 校验；启用时转发 faster-whisper 原生逐段检测参数，缓存隔离为 source-observer-1.2，记录初始检测的适用范围。默认 transcribe kwargs 和历史缓存 config 键不变。实现不新增语言可靠度规则，也不改变 source packet、顺序消歧或投影选择器。固定三组真实对照见 `output/source_context_upgrade12_20260908/protocol.md`；是否有效以配对实测为准。

## 2026-09-08 维护收敛修复

按用户确认停止扩张式升级，改为维护既有链路。普通与shadow作业入口拒绝误拼/未知配置，防止source_config未生效或human_confirmation被当无证据跳过；39份历史作业字段检查兼容。区域恢复同时读取完整canonical字符范围与旧行索引，修复WALK已恢复字幕再次同区域处理报missing/noncontiguous的问题；字符范围按已选文字顺序验证连续性，支持旧/新混合输入，不靠查找相同歌词猜重复位置。保留现有source候选、DP代价、几何规则、生产默认与历史artifact身份。

当前优先级见[维护收敛执行约定](maintenance-convergence-2026-09-08.md)，真实修前失败、修后重放与验证收据存output/maintenance_convergence_20260908/。工程恢复可用性不等于新增声学准确率；不以测试通过或候选数量宣布封板。


## 2026-09-09 editor-first batch / hybrid materialization 实施

`v4_preserve_editor_occurrence.py` 现将“可恢复 editor 区域”限定为完整、唯一 canonical 字符流与完整 editor cue 的交集，并在写出前复核 baseline ownership、邻接重叠、cue 顺序、输入 hash 与 artifact lineage。`canonical_content_start/end` 是 split/merge 后的主归属坐标；旧单行 `canonical_line_index` 继续可读。region 模式允许 occurrence 因 crossfade 在全局 audit 中被其它歌曲 cue 插入，但最终选中的 target positions 仍必须是一个连续完整区域。nonlexical source cue 不进入 exact stream matcher，仍保留为不可无证据删除的原字幕内容。

`scripts/v4_preserve_editor_batch.py` 将上述选择扩展为任务级事务：按 run 中 occurrence 顺序循环，每首歌反复选择最大可行区域，restore 后继续寻找下一块，直到 KEEP；任何异常发生在临时目录，不发布半成品。报告使用 `editor-preservation-batch-materialization-1.0 / immutable-editor-all-occurrences-batch-1.0`，记录 input/final SRT 与 audit SHA、stage/restore/occurrence 计数和每轮候选原因，且固定 `publish_ready=false / fresh_product_QA_required`。同一 batch 内 editor SRT + canonical bindings 的 Smart observation 按 source SHA + assets artifact ID + Smart policy identity 缓存；baseline、ownership、邻接与输出完整性不缓存。KPOP130 缓存前后 SRT/CSV SHA 逐字节一致。

`v4_materialize_editor_reconciled.py` 不再把 topology rebuttal 当成“canonical LRC timing 全局优于 editor”的证明。新 hybrid contract 同时消费 canonical evaluation/reconciliation 与 exact-bound preservation batch：前者证明存在 editor topology 无法表达的 canonical 内容，后者恢复能严格证明的 editor timing。production 前逐 occurrence 重建 normalized canonical character stream，preserved rows 必须按 character span 连续覆盖全文且不得 gap/overlap；至少有一个真实 restore，且 preservation 不得使用模型 timing authority。production artifact 的 upstream 同时绑定 source render、reconciliation 与 preservation，并记录 `hybrid_editor_preservation_after_editor_topology_rebuttal`。

repository-relative timeline/artifact 路径经 `task_contract.resolve_repository_path()` 统一从 repo root 解析，绝对历史路径继续兼容，相对路径禁止逃逸仓库；planner/editor/ASR/fusion/forced consumers 共用该语义。display materializer 则把全局 mask/shorten-only timing policy 与显式 line override 分开：multi-line ownership 可正常执行前两者，只有唯一单行 canonical identity 才能命中显式 override。

真实验收见当前状态页。KPOP130 从 canonical evaluation 到 hybrid production 再到 viewer display 全链通过，并在 8 条 historical development gold 上保持 584.9375->501.6875ms；KPOP110/WALK120/WALK140/H190/KPOP200 提供跨任务结构覆盖，不作为 blind accuracy。该升级不新增声学默认、不放宽现有 source/reference/final-mix 时间基保护。
最终验收必须分开读取三层：hybrid production/materializer QA 只证明 editor-first 的结构与 lineage；viewer final structural audit 只证明最终展示几何/内容边界；semantic/release gate 仍须由 fresh independent audio evidence/fusion 授权。KPOP130 display v3 的 viewer audit 为 `passed=true`、errors/window/content-end/overlap 均为0，但这不覆盖 semantic gate；多值 `canonical_line_indices` 仅表示 split/merge ownership，不会把 materializer `publish_ready=true`提升为完整 release-ready。
