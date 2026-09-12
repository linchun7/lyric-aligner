# Lyric Aligner v4 关键变更记录

## 2026-09-10：prefix-v2 / acoustic 1.5 工程候选收敛

Source-clock authority 收紧至 1.1，补齐真实 selection/protocol 身份与 ledger summary 重放，拒绝不一致 pass 标志。audit/release CLI 改为四份显式 authority 输入并绑定 SHA。同输入真实重放仍保留原 7 个 authority tracks、7 个 final semantic FAIL；未改 SRT、未重跑 source/final-mix ASR。

修复可靠检索把区间外 onset 外推误授予 timing eligibility 的通用缺陷；producer 标记 projection domain，Pro 和 shadow consumers 重新核对窗口/预测坐标及原有门槛，旧证据 fail closed。新增 producer/consumer/shadow 回归，真实 R4 沿用 7 缓存、56 frozen targets 与原阈值，10 source onsets、0 eligible。prefix-v2 保持 projection 1.1/v2；a20 semantic 仍 7 首失败，不做成品 release 或 commit/push。见 [当前交接](oumei140-a20-source-clock-upgrade-handoff-2026-09-10.md)。

## 2026-09-09：Lexical Floor、canonical rebuttal 与 pre-gold timing validation

- 冻结 `676b37f` 的 editor-first / hybrid timing 决策，不以本轮文字下限升级重新打开 timing selector。产品优先级明确为 `Content correctness -> Structure/ownership correctness -> Timing non-regression -> Timing improvement`。
- Text Repair 报告升级为独立 lexical-floor 语义：保持旧 status 兼容，同时显式报告 trusted-region lexical mismatch、unresolved cue、unmatched canonical 与 `timeline_mutation_count=0`。新增 bounded semantic request/response shadow protocol；大模型只能对已经有限框定的 canonical span 做 accept/abstain/partition，所有 request/bundle/model identity 均 hash-bound，不能提出 timestamp。
- 新增 resolved-canonical production lexical audit：不再把整曲 raw LRC 机械当 final-mix coverage 真源，而是对已经解析出的 occurrence/canonical evaluation 做字符 ownership/coverage。KPOP130 真实 pre-display hybrid：786 canonical rows、12 occurrence、12,539 normalized characters，覆盖 12,539/12,539，lexical mismatch/gap/overlap/unowned 均为 0。
- 新增 `canonical-semantic-rebuttal`：canonical 是默认文字真源而非不可反驳；normalized lexical 变化与 presentation-only 修订分离。自动授权必须 high-confidence、满足独立 `supports_corrected_text` evidence-family 下限，且不能同时存在直接 `supports_canonical_text` 反证。若 evidence 携带 raw observation，程序重新计算 variant role，避免把 `but I don't` 之类非目标短语误计为 `no I don't`。授权后也只生成 timing-immutable、`publish_ready=false` shadow。
- KPOP130 Fever Pitch 双模型 source-audio 复核实际跑通 Qwen3-ASR 1.7B 与 faster-whisper large-v3-turbo。A 组 `know/no` 只有 Qwen 一处明确支持 `no`，Whisper未明确区分；B 组 `hear/heal` 出现 Qwen/Whisper直接分歧。因此两组 canonical rebuttal 均未授权。早期 probe 将 `but I don't` 误判为 `no I don't` 的 aggregate verdict 已被纠正，不进入 production authority。
- display policy 现在在加载阶段硬拒绝 normalized lexical 改字；显式 override 只能做空格、标点、大小写/排版等 normalized-equivalent presentation 修订。新增 viewer lexical audit 再核对实际 SRT：presentation-equivalent、deterministically recomputed strong-profanity mask、或已授权 canonical truth overlay 才允许。仅写一个 `strong_profanity_mask` reason 不能放行任意改字。
- KPOP130 当前 lexical-safe viewer：774 cues，15 条 presentation-only override、2 条 mask、0 条未授权 lexical 变化、6 条既有 shorten-only display-end trim；相对旧 viewer v3 所有 start/end 完全一致。3 个未授权 `know -> no` 被撤回，同时保留安全空格修复为 `know I don't`。正式 final structural audit：0 errors、1 个 long-hold warning、0 occurrence-window/content-end/overlap violation，duration min/median/P95/max=`349/1902.5/4395.3/7563ms`。
- 新增 timing decision pre-gold validation 链：按稳定 canonical identity 比较 frozen old final 与 hybrid，在读取人工 truth 前冻结 changed boundaries + deterministic unchanged controls，生成 selection lock、候选隐藏的音频 review 包、hash-bound response→gold ingestion 与 selector evaluator。人工可显式标 `invalid/unscorable` 并保留分母，不强迫猜值。KPOP130 development wiring 冻结 60 changed + 20 controls 共 80 case、80/80 非空音频片段；它只证明流程可运行，不是新的 blind/untouched timing 精度证据。
- 本轮 lexical/structural/display gate 不解除完整 semantic release gate。旧 fusion 仍是当前安全契约收紧前的 stale evidence；fresh independent semantic audio evidence 仍需后续重建，不能用本轮 lexical floor 通过冒充完整 release-ready。

## 2026-09-08：终轮瓶颈实验与合法区间收益分解

- 新增离线 `evaluation/interval_bottleneck.py`：在完整有序 editor 序列上求最小标注端点误差；同成本优先少改动。分别计算逐点、完整模型区间及可混合单边的合法上限，避免将整句替换与局部修复混为一谈。无标注 cue 固定，不能为 oracle 免费挪动邻句。
- 缺失候选、非法候选、缺失 gold cue 归属及无 gold 都有显式结果；误差只针对可评分端点，不能用部分覆盖推断全曲准确率。输出仅为诊断，既不授权写回也不训练 selector。
- 实测采用固定参考窗口、历史 final-mix 对照和待人工标注的新 final-mix 预测。实验模型与依赖保持在本地实验目录，既有生产模型、版本与历史产物保持原状；没有依据本轮历史 gold 接管生产默认。
- 回归覆盖：跨模型最优边不能冒充完整候选、未标注邻句约束、联合可行路径、缺失覆盖、同成本保留及穷举最优对照。完整检查结果记录于本轮本地实验报告。
- 兼容与回滚：新增独立离线 API，不修改既有 artifact schema 或 CLI。撤回该模块及其测试即可撤回本次持久代码变更；本地实验结果保留供复核。

2026-09-08 第九切片新增旧锚夹持的完整精确词序源锚，只进入显式joint实验。WALK882条重跑共同解码2→3对，恢复219/220，旧两对区间不变；但220与KEEP221仍重叠3017ms，全局采用0、成品时间/文字变化0。历史三首149行得到9个精确锚（4新增），新增8端点MAE165.75ms、最大312ms；这是源锚证据，不是成品或新盲测精度。无条件二段拆词的43076项词典遮蔽验证仅34.84%发音一致，未接入。隔离Python3.12全量1625项通过（4项可选FLOAT跳过）。全部正负实验登记见 references/accuracy-experiment-register-2026-09-08.md；本轮证据见 output/source_context_upgrade9_20260908/delivery_report.md。不封板。

2026-09-08 第八切片已实现显式相邻共同窗口与原子选择。同输入 WALK882条实跑触发4对，2对完成一次共享声学解码，另外2对因缺词dancefloor/缺右锚拒绝。两处内部候选重叠1929/862ms→0，但全局选择0，成品区间变化0，文字变化0；旧FW和旧HFA输出字节一致。这证明局部冲突机制可修复，不证明整段准确率提高；保留实验身份，不默认推广、不封板。下一步需获得长行内部可靠词级锚与可验证发音覆盖，不能靠放宽外部几何强行采用。工程与实测证据见 `output/source_context_upgrade8_20260908/delivery_report.md`。

2026-09-08 第七切片新增通用英文派生词典构建器及anchored-path显式manifest接入。原字典保留，1052项新增发音均来自既有尾撇号词形或固定CMU直接记录。WALK120完整882条重跑：13次推理/17完整候选（此前5），选择0，最终区间0变化。已有三首公开歌保持9/143覆盖和MAE45.222ms，未测到新精度收益。rank18同音频仅加英文提示，词覆盖0→165/203、锚0→3，但候选仍0/44。主要瓶颈为完整源上下文资格和相邻区间兼容；不加搜索预算、不改canonical、不默认推广。最终工程验证、失败日志与复核见 `output/source_context_upgrade7_20260908/delivery_report.md`。

2026-09-08 第六切片已完成显式实验 anchored-path-v1：源时间带进入解码DP，同53行开发回归端点MAE858.321→207.047ms、最大32160→1867ms。另三首新公开歌149行/143内部目标仅覆盖9行（8旧+1新增），两策略输出相同，条件MAE45.222ms、最大107ms；134个无候选，不能宣称普遍增益。隔离Python3.12全量1583项通过、4项可选音频测试跳过。WALK120最终路径重跑882条，HFA候选5、选择0、新增成品改动0。保留实验身份、不封板。完整失败、修正、冻结与最终证据见 `output/source_context_upgrade6_20260908/delivery_report.md`。

第五切片验收补记：隔离Python3.12全量1564项/187.086秒通过（4项可选音频测试跳过，已另验证），36项HFA/shadow定向隔离通过。首次7个测试私有路径依赖错误已改为临时夹具，生产实现不变；独立复核及真实882cue的相对证据引用/SRT回读通过。日志和输入快照均在本轮delivery_report所列目录保留。

2026-09-08 HFA 三行独立实验升级：新增受版本控制的 local sidecar、完整三行源区间与时钟校验、逐候选模型异常隔离、独立overlay及显式no-AP策略。AP插入使三个padding音素拓扑不一致的问题已有同输入反事实证明，no-AP单独标识并保持旧AP可读。报告紧凑schema消除全候选重复写入，保留实际源词、canonical、自哈希及最终目录相对SHA引用；补严格三行源顺序、direct-behavior哈希和无私有依赖测试。真实WALK120恢复1候选但因邻句冲突选择0，不能计新增成品精度；独立公开新歌仅2/169行合格，条件MAE462→192ms，尚有609ms误差。历史22行/44端点回归与新歌验证明确分开，失败尝试与更正均保留。完整证据、测试与限制见 `output/source_context_upgrade5_20260908/delivery_report.md`。

2026-09-08 新增alignment_contextual_segment_interval_ms及独立forced-contextual-segment-interval-2026-09-08-v1身份。要求目标前后均有canonical段，完整词序相等且目标/相邻词正时长、有限、不重叠；提取目标自身首词start/末词end，保留下一句前的停顿。旧alignment_full_sequence_boundary_ms等接口不变，新API不自动授权写SRT。真实SOFA三窗对照支持去除window-end伪边界；未更改vendor或历史产物。

2026-09-08 新增 source_sequence 实验解析器：完整v5候选格、canonical/observed/time三序非交叉最大链的全最优共识，仅恢复canonical重复包；保持旧选择、上下文资格及默认runner不变。新增source-sequence-1.0与独立策略ID，绑定实际source observation SHA；图预算、输入截断/身份冲突/旧锚冲突保守拒绝。新四曲137行验证零新增，不提升默认策略；Qwen声学替换也因实测退步被否决。

2026-09-08 第二切片补充目标内部 ASR 错误的有界恢复：新增字符路径共识 helper，实际词首尾与原邻域资格共同决定候选；孤立精确同文不再遮蔽上下文合格的容错位置。逐包计算预算耗尽后清空候选，避免尚未检查竞争位置就自动选择。新增 packet schema 1.2 与独立策略身份，shadow 策略升至 v4-bounded-target-search；保留历史产物与默认精确 API。新增 packet 及实际 SRT 回归，协议见 [原曲上下文与区间联合升级](source-context-shadow-upgrade.md)。

2026-09-08 新增原曲上下文 shadow 升级链路：source audio ASR 与 mix 时间基分开；完整歌词包、字符归属和缓存身份绑定；原子区间及路径联合优化；同入口新 schema 写出实际 shadow.srt/CSV 和 selected/final 配对报告。补齐 effective fine 的 artifact 链、实验/生产分派、缓存与 staging 分离、editor-preserved 内容偏移归属。最终 v2 支持有证据的一端与精确保留的另一端组合，并保持 CUT 片段、邻句顺序及每端来源；没有时间的字只参与文字定位。并发缓存原子发布，失败运行独立保留诊断且可重试；改时行重新生成 cue 身份，旧人工确认只存基线审计字段。未更改历史产品版本或赋予实验候选生产授权。三期真实实验字幕及隔离 1485 项回归通过，已有标注上的本次新增精度收益为 0。详见 [原曲上下文与区间联合升级](source-context-shadow-upgrade.md)。

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

继续开发 ASR 自动补识别：路由 v2 将显式缺首、缺尾和重复匹配歧义加入现有二次识别；合成 v2 保留首轮已有覆盖能力，第二轮不再无条件替换，并分别记录 executed/adopted。正式执行入口可通过 `--retry-model-id` 一次串联原窗口内的首轮→筛选→第二模型→比较，两模型仍是同一ASR证据家族。28个既有跨项目观察重放新增路由1个walk140缺尾窗口；同窗medium与扩窗turbo均未改善，原句首保留，扩窗未改为默认。此批新增可用边界0，不以“多执行一次”冒充精度提升。41项ASR定向回归通过，实际证据在 `output/accuracy_upgrade_20260907/edge_retry_v1/`。


2026-09-07 contextual 1.1.1 自然校准：同一评测器复核 24 窗口，旧/新起点绝对误差中位数 11.58/7.48ms、P90 1121.86/15.18ms；新最大误差 15.91ms。全部为已知校准数据，不能称未见曲精度。另预先排除旧校准、旧holdout、压力测试歌曲，确定7首新自然调速配对；独立RMS真值审计7/7通过，21窗口的contextual holdout按冻结算法/采样率/阈值全部通过：中位6.00ms、P90 12.54ms、最大16.86ms、0个>=500ms错配。这里仍是音频映射基准，不是歌词边界真值或生产final改善率。产物在 `output/accuracy_upgrade_20260907/contextual_fine_v3/`。工程回归1393 tests /168.133s通过，随后身份/采样率修复有37项定向回归及独立复核通过；未把局部工程验证写作整包封板。


上下文音频匹配继续开发：contextual-independent-fine-1.1 联合局部和两侧特征，使用三段分数中位数；逐帧精炼避免粗网格相位造成假消歧；强局部竞争者缺上下文时保留歧义。旧 Independent Fine 1.0 不变，新观察仍同一 percussive family，无歌词边界 authority。已接入 v4_run_independent_fine_benchmark.py 的 --observer contextual。

整体算法继续：修复FW首尾覆盖字段丢失导致半句误作整句、Qwen缺尾时句首一起丢失的共性问题。新增两个backend的fusion→semantic回归与FW双执行路径完整/缺首/缺尾矩阵；验证前复现3处失败，修复后通过。不是按歌名、语种硬编码。

同日追加：QA双向错误串用已复现并修复，共15项semantic定向测试通过，顶层验收条件不变。真实逐曲保留完成Training Season和舞娘；其余四曲保留并记录具体失败，不伪造歌词分配。单次升级入口已实跑。

2026-09-07 直接开发继续：新增 immutable-editor-canonical-stream-1.0 与正式按曲 materializer，单次升级入口可执行独立 editor_preservation job。恢复可信原始时间且修正 canonical 文本，禁止整曲遗漏、伪造嵌入 onset、继承旧 authority 或跨歌重叠。Training Season 实物 51→49 cues；9 项核心/lineage/入口测试通过。其余歌曲逐曲实跑结果另记本轮 review，未把 source ASR 低覆盖强行拟合成时钟。

## 2026-09-07 — LRC 时钟候选及重复 ASR 词段歧义

- Root Cause Evidence：WALK120 canonical42 的目标短句在同一真实 ASR 窗口出现两次（389860/397540ms）；旧 matcher 并列得分直接保留首次，误将前句尾部认作当前起点。复现测试先失败；修复后保留候选、唯一端点 null、fusion 不回退 segment envelope。相同得分的逆序隔离词段也不伪装为唯一匹配。
- 新增独立 LRC→source 时钟候选模块，不改既有声学 mapping；Training Season 51 cue 候选通过文本、其他歌曲、末尾窗口、重叠与 hash 复核。19 个外推边界、非人工真值及未取得生产授权均明确记录。
- Qwen 接入与 semantic 修复的隔离完整基线为 1356 tests / 161.339s；时钟及后续歧义修复另有 48 项定向检查，最终完整回归须消费最新源文件。

本批最终完整回归已使用 46 个与工作区 SHA 相同的修改文件：1362 tests / 158.691s，通过；48 项定向测试、环境/隐私/文档工作区契约/compileall 同步通过。实际 ASR v3 重跑九个未拟合/尾部窗口，修复保留了 canonical42 的两次匹配。冻结候选对七条可比较 onset 代理全部改善，中位 3796→284ms，最大 5765→853ms；另两条未知。`verification_v3.json` 和 `lyric_clock_unseen_validation.json` 保留证据及限制，未宣称封板。

## 2026-09-07 — 重复句起点检查修复与本地 Qwen 观察入口

- 修复 editor semantic matcher 重复消费同一 candidate/span 的根因。新增相邻重复、缺失重复、跨 cue 重复、合并 cue 四个回归；六项目现有 SRT 原样重放，重复起点复用 283→0，不改写历史 QA。
- 新增可选 `qwen3_asr` evidence 执行器与 CLI dispatch，默认 Whisper 不变；本地模型、独立转录后定位、缺失概率 null、首尾覆盖分别控制边界，fusion 仍 shadow-only 且只接受完整区间。
- 复核发现并修正“缺句尾却输出整句 end”的新接口问题；32 项 Qwen/fusion/semantic 定向测试通过，包含正式 fusion CLI。实际模型结果与最终 SRT 精度须分别报告。
- WALK120 冻结 12 个分布窗口，Whisper 与 Qwen 对部分英文句起点一致但不同于现有 Max；另做 source/mix 对照诊断。不能将 ASR 一致性当作真人真值，也不能据此整曲平移。

## 2026-09-07 — 连续非词汇人声无损显示合并

- 导入第四份核对，安全落实四个边界；原文件和不相容边界均保留。
- 新增 `vocalization_display.py` 与 `v4_group_vocalization_display.py`，合并已有核对覆盖的相邻非词汇重复行，保留文字、索引、两端与成员哈希。单次升级入口的 gap review job 自动生成显示派生结果，目录迁移后再次重放验证；显示与声学 QA 范围分开记录。
- 实际显示 SRT 781→777 cues，非空白字符序列不变；属于显示结构修复，没有声学精度或发布资格升级。
- 新增分组测试和 receipt 完整重放；不再要求重复标注无法区分的 Na 行内边界。

## 2026-09-07 — 试听交互修复与无需新增人工的配准实验

- 修复数字输入时强制补小数导致连续键入失败；增加独立选点前/后试听、可输入步长及预设步进；所有 gap 音频扩为前后各 30 秒。
- 修复非整数毫秒 clip origin 的未编辑导出；保留精确绝对时间，旧确认和文件不重写。
- 最新第三份人工导出仅两段确认，按最新状态导入；新增 3 个安全边界写回，不把未确认 Na 当作已确认。
- 增加近等速局部波形候选与真实支持区间；默认实验关闭。独立复核发现并修复候选替换坐标间接清除旧 review 的问题，原特征证据及生产 mapping/verdict 保持。
- 已知变换与真实 K-pop 成品实测记录在本轮报告；没有宣称最终 SRT 泛化精度封板。

## 2026-09-07 — 人工 gap 导入与 A/B 试听

- `v4_apply_gap_review.py` 消费 exact lock/audio/report 绑定的人工导出，仅对明确确认的 present 边界进行联合 geometry 写回；相交边界保留待核，不移动未确认邻句。
- gap receipt 重放原 1.1 确认链，核对完整依赖清单与任务/音轨身份，再逐边界进入 QA；runner 增加 `gap_review` 阶段。
- `v4_build_gap_ab_review.py` 提供离线 A/B 页面：扩展片段、单点前后与相邻句衔接试听、可调停顿、拖动编辑和绝对时间、关联邻句当前值。修改边界撤销确认，空值拒绝导出。

## 2026-09-07 — ASR 窗口隔离与有效词观察修复

- 重叠 ASR job 分配到不重叠批次，避免多 clip 解码回退后把重复片段混入其他 job。新执行身份为 `disjoint_window_batches_v2` / `per_job_bounded_words_v2`，不重写历史 evidence。
- 两种 canonical support 共用有效词边界；越界词不支持当前窗口，无效、倒序、重叠词不能跨越拼接，segment 全文仅保留原始诊断。
- 修复前实际证据存在倒置候选和越界候选；修复后的真实局部重跑仍有模型漏识别，不能把证据修复宣称为最终字幕或跨项目准确率已达标。

## 2026-09-07 — 已确认人工边界自动复用

- `human_boundary_reuse.py` / `v4_reuse_human_boundaries.py` 自动复用既有 human gold 到完全相同的 final mix 与歌词目标，兼容原 cue 的 internal split，只修改外侧 start/end。
- 只改超出原标注容差的边界，枚举起止组合并拒绝新增/加重 overlap；某一边冲突不会阻断另一边安全修正。
- 生成新 SRT/CSV/完整决策 artifact，分别记录 start/end confirmation id，保留旧 authority/status；不以通用 manual marker 掩盖未确认边界，不改变整包发布门禁。
- H180 实际写回 3 start + 2 end，781 cues、0 text change、0 新标注。这是人工确认的自动落实，不能算新模型的泛化准确率。

## 2026-09-07 — 成品边界配对诊断与新验收抽样

- 新增 `product_boundary_quality.py`：原始/容差外误差分栏、候选可达性、选择损失、实际写回差异、误改与大错统计、按 track 等权 bootstrap；缺失 selection/final 不算零误差。
- 新增 `v4_evaluate_product_boundaries.py`：复用已有 gold/lock validator，读取历史 observer predictions，核对完整 report 与实际 SRT 后按 original cue ownership 关联 split 外边界，禁止按新旧 cue 序号直接配对。
- 新增 `v4_build_outer_validation_pack.py`：预测前按 recording group 冻结 60 个 clip / 120 个待标注边界，8 个 calibration group、4 个 holdout group，输出空白标注表。
- 当前交付为诊断与验收准备；不更改 `4.0.0a19` 生产算法身份，不重写旧 artifact，不授予新 outer authority。真实测量与后续条件见 `accuracy-upgrade-review-2026-09-07.md`。

> 2026-08-22 PR #70 前的完整当前记录已无损归档到 `references/archive/2026-08-22-pre-max-authority-v4-change-record.md`。P3 前更早历史仍见 `references/archive/2026-08-19-pre-p3-v4-change-record.md`。生产设计基线见 `references/production-requirements.md`。

## 当前产品责任分层

```text
Standard = Text Repair V2.1
Smart    = Canonical Sequence Reconciliation + Anchor Timeline Repair v1.2.10（no-audio）
Pro      = Selective Audio Repair v1.2.7（局部 audio evidence + 自动 review 裁决；no SRT write-back）
Max      = Full V4 Alignment（完整 audio / heavy fallback）
```

共同 authority：

```text
Canonical lyric -> final text/order truth
Jianying timing / cue boundary -> strong but rebuttable prior
LRC line break -> grouping/onset evidence, not final subtitle segmentation authority
Timed canonical -> primary no-audio timing evidence for Smart
Source-to-Mix -> primary acoustic timing truth for Pro/Max
ASR / forced -> auxiliary acoustic evidence
```

---

## 2026-09-07 — fresh-production bounded ASR execution hardening

Seven-project fresh production exposed a runtime-only failure mode in release semantic evidence: `v4_execute_asr_evidence.py` correctly selected bounded final-mix windows, but the faster-whisper executor invoked `transcribe()` once per window against the long mix. On CPU this repeatedly paid long-audio decode/30-second-context overhead; two 30-minute direct-queue batches timed out before producing a complete project evidence artifact. No semantic threshold or anchor count was relaxed.

The executor now pre-decodes the final mix once and, when more than one ASR job is selected, groups concrete-language windows into a single multi-clip `clip_timestamps` call; auto-detected language remains isolated per occurrence so language detection cannot leak across songs. Returned segment/word timestamps must still overlap the original requested final-mix windows or execution fails closed. Single-job/injected-model behavior remains unchanged. ASR evidence/artifact provenance now records `execution_strategy=grouped_multi_clip_v1`; legacy/per-job execution remains identifiable as `per_job_clip_v1`.

Real large-v3-turbo CPU smoke on the same six KPOP110 release windows improved from `434.714s` (per-window predecoded execution) to `206.479s` grouped execution, with all segment/canonical-match timestamps remaining on the original final-mix timeline. CUDA capability was independently probed and is unavailable on the current host (`ctranslate2.get_cuda_device_count()=0`), so full fresh production uses CPU grouped execution plus bounded task sharding; the release requirement remains six anchors per track. Targeted verification after the grouped change: ASR executor `14/14`, second-pass execution `5/5`. Final verification on the frozen worktree passed `validate_skill=0`, full source suite `1269/1269`, and `compileall=0`; formal fresh ASR evidence must be generated only after this exact hardening state is committed and its Git identity is bound into the evidence artifacts.

The same run also exposed a legacy CLI bootstrap defect in `validate_multilingual_asr.py`; direct execution could import `task_contract` but then fail to resolve the repository package. The script now inserts the repository root into `sys.path`, matching other current production CLIs. This does not grant boundary authority: H180 revoked-gap ASR remained diagnostically unreliable and therefore stayed fail-closed.

---

## 2026-09-07 — reference-retime semantic evidence compatibility hardening

Fresh production exposed a stage-contract mismatch: `v4_render.py` has formally supported `reference_retime` since a11, but the a19 semantic evidence chain still rejected that run/timeline stage before release QA. This was a consumer compatibility omission, not a timing-algorithm change. Editor evidence, alignment planning, mix-ASR first/second pass and evidence fusion now accept `reference_retime` / `reference_timeline_retime`, preserving exact task/run/timeline artifact binding and all existing semantic-sync thresholds.

The change is deliberately narrower than "enable every backend": source forced-alignment execution/projection remains fail-closed for reference-retimed runs. The current forced projector derives mix timing from coarse/Fine/cut-aware Source-to-Mix provenance and does not yet explicitly compose the reference-retime transform; allowing it would risk projecting correct source word spans onto the pre-retime mix timeline. Release-grade reference-retime tasks therefore use the already-supported independent mix-ASR + reliable-editor fallback unless a future retime-aware forced projector is separately implemented and calibrated.

Targeted verification after the initial compatibility patch: reference-retime suite `14/14`, alignment planner `2/2`, semantic-sync CLI `2/2`, release semantic gate `9/9`; the pre-narrowing full source suite passed `1266/1266`. After restoring forced source projection to fail-closed and adding the explicit negative regression, final verification passed `py_compile=0`, reference-retime `15/15`, `validate_skill={"ok":true}`, and the full source suite `1267/1267`.

---

## 2026-09-06 — post-seal authority/provenance review hardening

封板复审发现 legacy recovery 的自动 gap insertion 在仅由 projected LRC 生成新区间时错误自授 `boundary_authority=manual_verified_interval`。华语青春180 a19 最终表中有 5 条受影响，因此旧 a19 seal 作为历史 provenance 保留，但整份最终 SRT 的 `publish_ready` 已撤销；独立 Human-Gold joint internal split authority 本身不因该旁路失效。生产代码现已移除自动 manual authority，并写入 `projected_lrc_gap_candidate_no_boundary_authority`，使这类新区间在没有后续独立 audio/manual authority 时由 QA fail-closed。正式失效记录见 `references/v4-boundary-authority-a19-invalidation-20260906.json`。

同轮补强 `v4_seal_boundary_authority_overlay.py`：seal 不再只分别 hash raw run 与 adjudication artifact，而是使用 seal 指定的两份 raw backend run、exact calibration suite、joint calibration 与 plan 重新执行正式 internal adjudication，并要求重放得到的 evidence / decisions / bundle 与待封板对象逐对象一致，从而闭合 raw-run → evidence provenance。

生产文档同时澄清 spread 语义：普通“两 backend 各自已获得 boundary-kind calibration”的 consensus 仍受 `250ms` spread gate；a19 `human_gold_joint_lexical_selector_v1` 是独立 Human-Gold 校准且在 blind gold 前冻结的 selector policy，因此不适用普通 consensus 的固定 spread gate，不能把两类 authority 混写。

精度口径补充：a19 文档中 `0.165/1.92` 帧与 `0.735/1.92` 帧是扣除 Human Gold ±50/100ms uncertainty 后的 **effective error**，不是 raw absolute error。12 个 joint gold 的 raw absolute error median=`65.5ms`、P90≈`112.2ms`、max=`114ms`；4 个 holdout raw median=`93ms`、P90≈`108.6ms`、max=`114ms`。因此结论仍是几帧级，但后续报告必须同时给 raw / gold uncertainty / effective 三种口径。

最终工程复验：targeted authority/reverify suite `17/17` 通过；source-bound R8 全量回归 `1264/1264`、`compileall=0`，445 个 Python/production-adapter source fingerprint 在测试前后均为 `59ffcf5462d5704d76ff0cf5f85995fba6639fc4a7af4ff7d4ca208867dcecde`。当前 7-release reverify 为 `6/7`，唯一失败是主动 fail-closed 的 H180 a19；其它 6 个 release 均重新通过 current manifest、exact final-SRT release→QA binding 与敏感词检查。机器验收见 `references/v4-postseal-hardening-verification-2026-09-06.json`。

---

## 2026-09-06 — a19 Human-Gold joint internal boundary authority seal

Replacement Human Anchor V2 已完成 24/24 真人确认。六个单 backend start/end/internal scope 仍未取得 production authority，outer start/end 继续 `keep_editor`；未通过的 scope 不因 coverage 需求放宽 edge-clamp。internal 采用在最后一条 replacement gold 揭晓前已冻结的 `dual_aligner_nearest_lexical_ratio_prior_v1` joint selector：8 calibration + 4 holdout 全覆盖，calibration median 0.165 帧、P90/max 1.92 帧；holdout median 0.735 帧、P90/max 1.92 帧、catastrophic=0。joint artifact SHA=`84174bb8439cc3458acc1568ff60b579d6845be2d838327150420e7d24a2e1de`，并嵌入/验证 Human Gold、两份 source calibration 与 pre-human V6 full-blind scope，不能把事后 prediction 自签成 authority。

华语青春180 a19 对 a18 recovery report fresh 生成 152 个 internal boundary jobs。SOFA 144 aligned / 8 unavailable；HuBERTFA 141 aligned / 11 unavailable。HuBERTFA 的 `No duplicate groups` 不再拖垮整批：CLI orchestration 在 calibration contract 外对该精确异常做二分隔离，成功 subset 仍由原冻结 batch adapter/model 产生，只有 3 个 singleton 最终标 unavailable；`adapter_contract_revision` 与 Human-Gold calibration 保持一致。joint adjudication 允许 137/152 boundary 自动 split，materialization 646→781 rows、264 行为 `audio_verified_internal_split`，其余证据不足点 fail-closed keep unsplit。

用户明确指出的 40:28 text-ownership 错位在 timing materialization 后通过独立 hash-bound text-only correction 修复 cue 544/545，时间轴与 cue 数均不变；legacy QA 只在 status/segmentation_authority/evidence 三重绑定时承认 V4 internal authority，并对该明确 human repair marker 压制反向 Jianying heuristic。旧 `interval_text` regression 默认仍要求 exact single cue；只有 case 显式 `allow_authorized_internal_split=true` 时，才允许一组 gapless、同 original cue、完整 split_part、全部 audio-authorized 的 children 作为等价表示。最终 `scripts.test_redo_karaoke_pipeline` 78/78，seal/repair/isolation/legacy combined 90/90。

最终 corrected SRT SHA=`02b89d49db3f8cbc5886e9495804e3d0ee1b409ef0ed91e7333223e4c84f9f7a`；structural/text QA `publish_ready=true`、8/8 regression、0 review candidate、0 unverified timing mutation、0 lyric gap/duplicate/unexpected overlap。新增 `scripts/v4_seal_boundary_authority_overlay.py` 对 exact task/audio/gold/calibration/raw/adjudication/materialization/correction/QA/release 重新验链后生成 `boundary_authority_overlay_release_seal.json`，seal SHA=`8732cb718b47b0c5b3910236ec3dd4d2ee41d8ce0021c45a3d9af4c93cb94d53`。该 seal 明确不声称 full Max semantic-sync release，因为 recovery overlay 没有与之精确匹配的 current Max run+fusion。

## 2026-09-06 — Outer observer closeout + Expected-Loss 1.1 hardening

在 a19 internal authority seal 之后，继续用 replacement Human Anchor V2 的 **calibration-only outer gold view** 评估第三类/第四类 outer observer，但不把新模型结果倒灌进 gold。3A `wav2vec2_xlsr53_zh_ctc` 在 calibration coverage 仅 0.75，start median/P90 effective error=`3.96/26.10` 帧，end=`19.965/28.65` 帧，start/end 对 editor 均 0 胜，正式 `rejected_before_holdout`（verdict SHA=`3bdc70d29ff8fd19af67e4ef13b914d627d340417a280f3d73aa2e09468ddf4e`）。3B `mandarin_lyricalignment_asru2023_ctc` 虽 coverage=1.0，但 start P90/max=`46.5` 帧、end median/P90/max=`4.98/32.19/32.19` 帧，end top-K oracle P90 仍=`31.59` 帧，因此同样 `rejected_before_holdout`（verdict SHA=`0691ab8be87c50312536a272c26c7d2ce38d63893877db7b85859df81051cc18`）。两者都没有运行 holdout，也没有通过 wildcard/阈值 tuning 强行过门槛。

`boundary_risk` 与 `max_next_adjudication` 升级到 1.1。旧 aggregate holdout profile 不再足以让一个当前 candidate 自动覆盖 editor；新增 `BoundaryLocalSupport`，必须绑定 exact `boundary_kind + candidate_ms + local P90 + provenance`，且 production-authoritative profile 只接受 holdout population，editor/candidate population 与 catastrophic threshold 必须一致。当前 candidate 没有 local support、support 非 authoritative、support contradictory 或 support 绑定错 timestamp/kind 时都确定性 `keep_editor`。Max Next 继续只输出 `selection_recommendation_only_no_srt_mutation`，没有新增 production materializer；targeted risk/provenance/Max Next/sequence regression 为 45/45。

Independent Fine 另用与歌词 Human Gold 独立的真实 source↔DAW 调速素材建立 known-transform benchmark。8 首 calibration × 3 个远隔窗口共 24 case 的独立 RMS affine truth audit 全部 truth-usable；校准后冻结 selector `aligned + observer ambiguous=false + top1-top2 margin>=0.05`，冻结点 selected=`19/24`、8/8 songs、median/P90/max=`10.49/28.38/38.40ms`。之后才锁定 8 首 calibration 未使用歌曲，独立建立 RMS holdout truth，并在任何 prediction 前冻结 holdout protocol。首次且只运行一次 blind 24-case holdout：原始 24/24 aligned；冻结 selector 最终 selected=`16/24`、7 个 distinct pairs、median/P90=`12.04/17.60ms`，但 coverage=`66.67%`、max=`1040.14ms`、500ms catastrophic=`1/16`，所以正式 `failed_frozen_holdout_protocol`（verdict SHA=`fe7128936d48e7f2d8271f014933220f3c54451c073f202defbd415eddd07362`）。artifact 明确 `policy_retuning_from_holdout_allowed=false`，因此不再根据 holdout 改 margin；Independent Fine 保留 diagnostic/secondary evidence only，不获得 candidate-specific production local-support authority。

本轮 machine closeout：`references/v4-max-outer-observer-closeout-2026-09-06.json`，artifact SHA=`4439c76c2bd88a3bd244ba496be0fb63e351feb9bb837ad68c3c5901993425dd`。最终 production 语义不变：a19 internal joint authority 保留；outer start/end 无新增 observer authority，Expected-Loss 自动 fallback 继续关闭，editor timing 继续作为 strong prior；没有执行新的字幕 timing mutation。

## 2026-09-05 — Max 4.0.0a18 calibration-gated direct-final-mix boundary authority

华语青春180 的 edited-mix recovery 暴露出一类与普通 semantic sync 不同的 segmentation failure：一个可信 editor 长 cue 内包含多条 canonical lyric 时，旧 recovery 曾把 projected LRC onset 当成内部切分 authority，造成实际可见 cue 起点偏移 1–2 秒以上。a18 因此把 outer start/end 与 long-cue internal split 拆成独立 boundary kind，并固定 canonical lyric 只负责文字/顺序、editor boundary 是强但可推翻 prior、LRC timestamp 只能 routing；没有独立实际音频边界证据时，长 cue 必须保持 unsplit。

新增 deterministic lexical/window/profile trust chain 与两个 direct-final-mix production observer。SOFA Mandarin singing alignment、HuBERTFA forced alignment 的 human-gold batch、outer、internal 与 production batch adapter 共享 `full-sequence-alignment-core-1.0` logical identity，并绑定 exact backend profile/model revision、language、lexical contract、final-mix SHA 与 `editor_cue_plus_1500ms_clamped_to_mix_v1`。随后 production provenance 进一步加固为 `model_revision + implementation_revision + adapter_contract_revision`：`adapter_contract_revision` 已从“仅四个 sidecar adapter”扩展为完整 observer runtime contract，同时绑定 gold/boundary/internal/batch adapters 与 boundary/internal/batch executors、forced-alignment edge guard、window policy、lexical contract、human prediction 与 calibration core；任何这些实现发生变化，旧 blind prediction/calibration 都必须失去当前 production authority，禁止由 materializer 事后补签当前 contract。full 60-clip / 90-point pack 保留为 diagnostic population；生产 authority 改用其 pre-model deterministic 24-clip / 36-point projection，每个 start/end/internal 固定 8 calibration + 4 holdout、至少 4 tracks。raw confidence 不作为跨模型 authority，machine consensus 也始终只是 candidate/diagnostic。

自动 outer/internal adjudication 要求两个不同 backend ID、不同 correlation group 和所需 direct-final-mix family；large disagreement fail closed，不做平均。`v4_run_alignment_backend_evidence.py` 只生成 raw uncalibrated evidence，`v4_adjudicate_calibrated_alignment.py` 只绑定 exact six-scope calibration suite 并生成 hash-bound evidence/decisions/bundle，不写 SRT；outer structural confirmation 必须属于 exact plan 并写入 bundle，internal 禁止该输入。`v4_plan_internal_segmentation.py` 与 `v4_materialize_calibrated_alignment.py` 继续作为现有 core 的薄生产适配层，materialize 必须同时绑定 task/source/final-mix/report/plan/evidence/decisions 与 adjudication bundle SHA，并 fresh re-adjudicate 后才允许写新的 CSV/SRT。full diagnostic pack `human_boundary_gold_华语青春180_20260905_v4` 保持锁定，selection lock SHA=`8f2b06f3d6c66382f78a3b22c0d2d9f4e285bc7048b47d932f2e056723746633`。发现旧 blind scope 只绑定 gold adapter 而可被 materializer 事后写入当前 adapter contract 后，旧 v4 scopes 被降为历史证据；已用 schema `subtitle-machine-consensus-scope-1.2-full-adapter-contract` 重跑两 backend × 三 kind 的 v6 blind scopes，完整绑定当前 observer contract。数值 coverage 保持 SOFA start/end/internal=`24/30, 12/30, 30/30`、HuBERTFA=`27/30, 19/30, 30/30`；但使用当前 v6 blind scopes 与现 acoustic/consensus 聚合重建后，90-point records 已更新为 `records_sha256=a11e32394538979ea8fbfef9d82b58074ba5b78a7bb21ce6322cb0f8bf5f6367`，新的 candidate-only artifact 为 `machine_boundary_consensus_华语青春180_20260906_v6.json`、artifact SHA=`adfefd461884d3cf1c0a43d6ca40c3f87edd589981814b41af9fcdfb69192bfe`；其 artifact 内直接锁定六份 blind-scope artifact SHA、model/implementation/four-adapter/adapter-contract revision 与 code SHA，不再依赖外部说明补 provenance。production human-anchor v1 的 24/24 人耳操作随后发现一个 internal target 实际落在 locked clip 外，该题目被正式 invalidated；v2 只从同一 pre-model population 做 deterministic replacement，23 条合法人工结果原样继承，最后 1 条 replacement 必须重新真人确认。最新 v6 blind-scope reuse preflight 显示选中点 coverage 仍为 SOFA start/end/internal=`11/12, 3/12, 12/12`、HuBERTFA=`12/12, 5/12, 12/12`；end 已低于 75% production gate，因此 authority 保持关闭。在 v2 达到 24/24 并 materialize 36-point human gold 前，真实 `production_authority_ready` 继续为 false，且没有执行字幕 timing mutation。

华语青春180 的 viewer-level 对比还暴露出独立的 text-ownership regression：剪映把一条 canonical lyric 拆在相邻两个 cue 时，旧 recovery 可能把同一整条 canonical event 写进两格，形成连续重复；更复杂时某格还会同时带入下一 canonical event 的开头。a18 recovery 因此新增 exact editor-fragment ownership restore：只有相邻 editor 原文拼接后精确重建同一 canonical event，或三格原文精确重建两个连续 canonical events 时，才按 editor 原 cue ownership 重新分配 canonical text；不改 timing，不改变 canonical event 顺序，真实整句重复不触发。该规则已用通用 synthetic regression 覆盖两格、三格、跨 event spill 与真实重复 fail-closed。

## 2026-09-04 — Max 4.0.0a17 semantic timing release hard gate

华语青春180 的真实生产事故证明：artifact lineage、cue geometry、review closure、editor-reconciled segmentation authority 与 release manifest 可以全部自洽，但 canonical source timebase 仍可能与实际 edited audio 严重错位。事故样本《第一天》因手工分段调速却被错误按单一 BPM 比例缩放 LRC，导致 Max canonical projection 与最终 SRT 对 source editor/audio-derived timing witness 稳定晚约 12.3 秒；旧 release gate 没有任何“歌词是否真的在声音唱到这里时出现”的独立检查，因此 false-ready。

`4.0.0a17` 新增 `lyric_aligner.qa.semantic_sync` 与 `scripts/v4_audit_semantic_sync.py`，并把既有 forced-alignment / ASR / evidence-fusion 基础设施接入 release QA。正式 timing truth 不再由 editor/Jianying SRT 单独承担：planner 会为每首主动选取分散的 canonical semantic anchors，优先请求 canonical text 对实际 source audio 的 forced alignment，并经 source-to-mix 投影得到独立 mix-time 语义锚点；ASR executor 新增 canonical word-span 定位，只有 word-span support 足够且 editor witness 经文本覆盖证明可靠时，ASR 才可作为 fallback。forced/ASR 冲突、独立音频证据不足、editor-only 均 fail closed。默认每首独立音频锚点 median absolute onset error 必须 `<=1500ms`，且 `>2500ms` 的大误差比例 `<=25%`。

从 a17 起 `v4_validate_release.py` 强制要求 `--run`、`--semantic-sync-fusion` 与 `--semantic-sync-qa`；projection/final 任一层 failed、证据 basis 非 `forced_alignment` / `asr_plus_reliable_editor`、QA 缺失、hash stale/mismatch 都不得生成 ready release manifest。QA 精确绑定 source SRT、final mix audio、song list、run、evidence fusion、exact final SRT 与 final audit report。Synthetic regression 覆盖整体 +12 秒系统偏移、低质量 editor + 正确 forced alignment、低质量 editor + ASR-only 禁止放行、reliable editor + ASR fallback、forced/ASR 冲突以及重复副歌约束。真实事故回归中《第一天》55 个高置信 editor 对照锚点 median error `12.346s`、98.18% 超过 2.5 秒；该事故同时推动了独立音频语义证据成为正式 release authority gate，而不再把 editor 对照本身当最终真值。

## 2026-09-04 — Edited-mix recovery legacy hardening（v3.9 compatibility identity）

华语青春180 的 P0 false-ready 事故在 a17 semantic release hard gate 之外，还暴露了一个现实恢复需求：已有较强 editor timing、实际调速/剪辑 source WAV 与 edited mix 的任务，如果 canonical LRC timebase 本身不可信，不能继续让错误 LRC 主导整首时间轴；但也不能把 editor SRT 升级成绝对真值，尤其韩语/日语识别较差时会形成新的系统性错误。因此 `redo_karaoke_pipeline.py` 仅作为显式 recovery/compatibility 工具补强，不重新成为默认生产入口，`ALGORITHM_VERSION` 继续保持冻结 legacy identity `3.9`，具体行为差异由 Git commit、task fingerprint 与 artifact lineage 绑定。

本轮维护统一复用共享 `is_title_like_intro()`，过滤首 2 秒 provider `artist - title` 身份行，并补全全角 `词：/曲：` 与纯 `男：/女：/合：` 角色 metadata；新增 task-bound `_canonical_text_corrections`、`_drop_cues` 与更严格 provenance/理由校验，canonical 源修订和不可验证 editor 短词不能再靠手改最终 SRT。QA 的 shared-LRC accidental duplicate gate 现在能识别“editor 两半、输出却变成整句+整句”的 reconstruction failure，并把邻接审查窗口扩到 1500ms，同时以两侧原观察均完整匹配来保护真实重复演唱；review CSV 改用 union fieldnames，避免候选字段差异导致 QA writer 崩溃。中文纯 vocalization 兼容集合补充常见 `耶/呜/哒`，无 canonical provenance 时按既有规则处理。

该 recovery path 的 authority 固定为：canonical lyric 负责文字/顺序，实际 source↔mix waveform/其它独立音频证据负责 timing truth，editor SRT 只作为强但可推翻 prior；手工分段调速、裁前奏或非线性 DAW 编辑禁止仅凭单一 BPM 比例缩放 LRC。华语青春180 重建回归中，旧版《第一天》约 +12.45s 系统错位被消除；640 个可追踪 editor anchors 的最终起点 global median/P95 absolute delta 均为 0ms，非零变化只发生在 6 个原长 cue 的显式拆分。独立 viewer-facing scan 又发现并关闭了 QA 原先漏掉的相邻重复分句，说明 release-ready 之外仍保留最终可见文本审计。

## 2026-09-04 — Max 4.0.0a16 early timed title-row metadata guard

华语青春180 WAV Max 终审暴露出一条 consumer-LRC 身份装饰行 `[00:01.337]潘玮柏、苏芮 - 我想更懂你` 被旧 canonical metadata 规则当成歌词并投进 timeline。根因是共享 `is_title_like_intro()` 仅识别首 1 秒内的 `artist - title`，而 provider 延迟及任务级时间缩放可把同类身份行推到 1–2 秒。

`4.0.0a16` 把该共享识别窗口保守扩到首 2 秒，同时继续要求字面带空格的 `artist - title` 形态；2 秒之后同形文本仍保留为 lexical content。canonical parser、lyric-role preflight 与 text repair 因共享 helper 同步获得修复。回归新增 1.5 秒身份行必须过滤，以及 2.1 秒同形文本必须保留，防止未来无界扩大 metadata heuristic。

## 2026-09-04 — Max 4.0.0a15 decodable terminal duration guard

真实华语青春180 MP3 生产暴露出一类压缩音频尾端异常：SoundFile/librosa 暴露的物理/容器时长为 `3024.8436667s`，但 ffprobe 与实际 bounded decode 的首个音频流都只到 `3017.7600000s`，差值 `7.0836667s`；这段额外区间在 SoundFile 侧只表现为数字 0。旧 content-extent 仅在 trailing digital-zero 至少 30 秒时自动缩短，因此把这 7 秒虚尾保留为可搜索 mix time，最后 occurrence 的 bounded decode 随后 hard fail。

`4.0.0a15` 不扩大 a12 的 terminal short-read 容差，也不把普通短静音视为可裁内容；而是在 `detect_audio_content_extent()` 增加独立 decodable-stream upper bound。物理/容器 `full_duration` 仍完整保留作 provenance；只有 ffprobe 的首个 audio stream duration 明确更短，且该终点之后 SoundFile 暴露的样本全部为数字 0 时，才把有效 `content_end` 收敛到该可解码音频流终点。若 ffprobe 终点早于仍存在的非零解码内容，则直接 fail closed，避免探测器误报造成真实节目截断。定向回归覆盖虚尾缩短、短真实数字静音保留、probe 与非零内容冲突拒绝，并用该真实 MP3 复测：`content_end=3017.76s`、bounded shortfall 从 `7.0836667s` 收敛为 `0s`。

## 2026-09-04 — Pro v1.2.7 automatic adjudication without SRT mutation

Pro 在现有 v1.2.6 selective planner / bounded evidence 路由之上新增 decision schema `1.1` 与 adjudication policy `pro-selective-decision-fusion-2026-09-04-v1.3`。该层不扩大 acoustic/ASR/forced 的证据搜索范围，只把已经执行的 evidence 进一步收敛成 `timing_resolution / text_resolution / resolution / manual_review_required / manual_review_mode`，形成明确的自动裁决支持，但不自动关闭 review。

当前 authority 为 `automatic_adjudication_no_srt_mutation`，scope 为 `decision_support_no_srt_mutation`。所有 timing/text review 均保留人工确认，`automatic_review_resolution_allowed=false`。证据足够时可把 timing review 收敛为 `candidate_confirmed_advisory` 或 `keep_editor_advisory`；text evidence 可收敛为 canonical text/occurrence support advisory；证据冲突、结构风险或证据不足则保留 investigate。这样不会把相关 acoustic evidence、ASR 高分或单一 canonical 绑定误当成足以自动解除 segmentation/identity/structure 风险的最终 authority。

`smart_candidate_supported` 现在可输出 `candidate_confirmed_advisory` 与 Smart proposed start/end，但仍要求人工确认；`smart_candidate_rebutted` 只输出 `keep_editor_advisory`；`smart_pro_conflict / pro_detected_anomaly / unvalidated` 继续进入人工队列。原因不变：Smart 与 local source↔mix retrieval 共享 canonical/LRC timeline，是相关证据而非独立 mix vocal-onset authority。因此 Pro v1.2.7 继续固定 `automatic_timing_change_allowed=false`、`automatic_text_change_allowed=false`、`timing_mutation_performed=false`，不会生成自动修改后的 Pro SRT。

## 2026-09-03 — Calibration-only ablation review

完成一轮只使用 calibration truth 的 Max 消融，未读取未观察 blind truth。325 个 coarse windows 上 Chroma-only 与当前 fused top-1 不同 `47/325 (14.46%)`，MFCC-only 不同 `28/325 (8.62%)`，因此双特征融合保留。K110 完整 no-Fine 全链在 composer 层保持 17/17 occurrence 可见 cue identity 完全一致，但 strict calibration reference 评分从 full-Fine 的 boundary MAE/P95 `16.265/7 ms` 退化到 `22.939/25 ms`；文本/行级指标不变。结论：Fine 对 timing tail quality 有明确贡献，本轮否决删除 Fine，也不基于当前有限 corpus 新增 skip/selective gate。

Piecewise TimeWarp 当前缺独立 `piecewise_rate` calibration 正例，不能用“现有样本都选 AFFINE”反推其无用；该能力继续保留。完整实验方法、指标与清理结论见 `references/ablation-review-2026-09-03.md`。

## 2026-09-03 — Legacy diagnostic helper retired

`scripts/karaoke_subtitle_pipeline.py` 的 1569 行 pre-v4 diagnostic/draft 实现已退役。仓库内没有当前代码、测试或权威工作流依赖该实现，且其旧 SRT/LRC/ASR authority 规则与当前 Standard/Smart/Pro/Max contract 不等价；继续保留可执行实现只会形成第二套入口。为避免旧命令静默生成 authority 不明的字幕，文件名保留为 fail-closed 迁移提示：`--help` 展示当前工作流映射，其余调用退出 2。历史实现仍可从 Git history 恢复，不做自动语义转发。

同时统一评估文档入口：`references/dataset-protocol.md` 不再把低层 `evaluate_dataset.py` 展示为生产验收命令，正式 calibration/blind-test 统一指向 `v4_calibration_workflow.py` strict contract；新增 `references/local-artifact-retention.md`，明确本地 `private/output/cache` 的 KEEP / ARCHIVE / REGENERABLE 边界，禁止按版本号或目录新旧直接删除 truth、release 或生产证据。

## 2026-09-03 — Structural truth-discovery closeout

在 r4 QA bridge 完成后，没有直接继续实现 `piecewise_rate / hard_cut / true_overlap` detector，而是先对当前 real corpus 做独立 truth-discovery。`piecewise_rate` 最强候选使用原曲与最终调速 stem 直接做局部 source↔adjusted rate 测量，不读取 Max timeline/Fine/TimeWarp：10/10 预设窗口均可靠，local-rate 总跨度 `0.0125`，最强相邻两-regime split 仅差 `0.005`，低于预先冻结的 `0.015` truth threshold，因此制作上“手工调 BPM”不能被扩大解释为 benchmark-level piecewise-rate truth。

`hard_cut` 最强候选已有历史人工确认 source omission，但新的 task-bound waveform-only 双 branch audit 在结果读取前固定窗口/步长/rate/score/dominance/dual-support 门槛；执行后稳定左右 dominance、abrupt switch、material-dual-support-absent 四项均未满足，并出现约 `0.40s` ambiguous dual-support。由于同曲重复结构可造成 branch 高相似，这既不证明 crossfade，也不证明 abrupt cut；该 evidence 只保留 confirmed omission 身份，不升级为 hard-cut truth，且不调 threshold 重跑。

`true_overlap` 则核对了当前 real corpus 全部 3 个 production `confirmed_overlap` decision；独立人工 rationale 分别明确写为 `Confirmed crossfade` / `Confirmed short crossfade` / `Confirmed crossfade`。因此它们继续构成 `crossfade` truth，不得为了补 taxonomy 覆盖而重标为 non-crossfade `true_overlap`。结论：当前 corpus 对这三类均仍是 truth gap；在出现新的独立制作/编辑 truth 之前，不实现、不调参对应 detector，研究优先级转为 event-driven real failure、regression/provenance/release-authority hardening。

## 2026-09-03 — Read-only structural evidence QA bridge

fresh-blind 通过的 `reorder / detached_tail` detector 没有直接进入 Max mutation path，而是先增加 `lyric_aligner/qa/structural_evidence.py` + `scripts/v4_audit_structural.py` 只读生产 QA bridge。输出固定 `authority=diagnostic_only`，同时明确 `automatic_timing_change_allowed=false`、`automatic_content_end_change_allowed=false`、`automatic_review_resolution_allowed=false`、`release_gate_eligible=false`、`publish_ready=false`；发现事件不会改变 production run、review 或 release。

`detached_tail` 只读取 task manifest 已绑定音频；`reorder` 只有在调用方显式提供 `v4-editor-source-map-1.0` 且 map 绑定同 task fingerprint、同 editor SRT SHA、`mapping_authority=source_occurrence_verified` 和 repository-relative 上游 source-mapping artifact path + 现场 SHA 校验时才运行。缺少 map 时明确 `not_run_missing_source_mapping_authority`，不会退回 raw-SRT heuristic；map 路径逃逸、artifact 缺失/SHA drift、cue-count/position 异常均 fail closed，上游 mapping artifact 同时进入 output collision protection。

六项真实 production regression：190 使用冻结 Smart `timing_decisions.source_ordinal` 作为 hash-bound mapping authority，仅报告 1 个已知 reorder、0 detached-tail；Walk120 不授予 reorder mapping，但报告 1 个已知 detached-tail；快乐健走140、KPOP110、KPOP130、KPOP200 均报告 0 structural event，且无 mapping authority 的 reorder 全部明确不运行。该阶段只证明 QA evidence 可安全泛化，不授予自动修复 authority。

## 2026-09-03 — P1 r4 fresh-blind structural detector evidence

在 typed-event metric contract 冻结之后，新增 evaluation-only `lyric_aligner/evaluation/structural_detectors.py`，只研究两个已有独立 truth 的正交结构事实，不接入 production Max authority。`reorder` 不再用“任意 SRT 文件顺序回跳”作为真值，而要求已有 source/occurrence mapping authority 的 editor cue 才能建立/触发 chronology frontier；unmapped overlay/口播不能单独生成 reorder。`detached_tail` 只检测后段长 exact-digital-zero gap 后重新出现的短孤立 active island。

r4 calibration 使用 2 个真实正例（190 reorder、Walk120 detached tail）和 3 个显式 negative controls，opaque SRT 仅作评估载体；真实 production SRT/audio/QA/Smart mapping 通过 SHA 绑定 input/truth。candidate revision `11b2443c59aa5a14b8b1c8950a9eaf0c103fc6f48d958711208bc7f3ad5c5183` 在 calibration 上得到 typed-event precision/recall/F1=`1/1/1`、negative clean rate=`1.0`、interval IoU=`1.0`，随后 selection payload `71c5f5ab9b40603a095b526dea016435ceffcff49a0183038a3d0f3c2ff38745` 被锁定。

在读取任何 blind metric 前，fresh blind policy 同样冻结。12 个一次性、隐藏 case（4 reorder / 4 detached-tail / 4 none）首次 materialize 后执行唯一一次 gate：overall precision/recall/F1=`1/1/1`、interval mean IoU=`0.999696`，两类 positive recall 均为 `1.0`，`none` clean-case rate=`1.0`，gate PASS；blind gate payload SHA=`53bef3698a1dc60da6d3aa605c4e126aea5cea231dc6b680ef95e8c044fcbe04`。该 r4 blind 自 observation 起永久不可用于继续调 threshold。此结果只证明 evaluation candidate 泛化，不直接修改 coarse/Fine/TimeWarp、transition/review/release authority。

## 2026-09-03 — P1 typed structural-event evaluation contract

在 taxonomy/reporting foundation 之后继续补齐“事件是否真的被检测到”的 strict gate 能力，而不是直接写新 detector。schema `1.1` 可选 `expected_structural_events / predicted_structural_events`：point event (`hard_cut / same_track_splice / sequential_transition`) 使用 `kind + time_ms`，interval event (`crossfade / true_overlap / piecewise_rate / reorder / detached_tail`) 使用 `kind + start_ms + end_ms`。point 采用单调 maximum-cardinality/minimum-total-error matching，interval 采用单调 maximum-cardinality/maximum-total-IoU matching。

Expected events 及其 `structural_event_tolerance_ms / structural_event_min_iou` 属于 truth-side identity；predicted events 永不进入 ground-truth SHA。prediction-only metadata、truth/scenario 不一致、`none` 配非空 truth、错误 shape、重复事件、非法时间/阈值全部 fail closed。strict evaluator 新增 aggregate `structural_event_precision / recall / f1`、FP/miss、point MAE、interval mean IoU；evaluation 输出仍只保留 aggregate/opaque case，不泄漏事件位置。

兼容验收：冻结 r3 calibration/blind SHA 分别仍为 `737e83697f1e577bbf9c8473e21b54ad304c33d1c6f09404fc45abe10853e330` / `2e9c49321ac3541d2d5f3fdb953ddbdecab1f0c09f3ed80a6249aae83bbdc886`，旧 case 的 typed-event annotation/clean counts 均为 0；剥离新增 `structural_event_*` 指标后，旧/新两 split 递归全等。另新增 privacy-safe coverage map `references/structural-benchmark-coverage.md`，确认下一阶段优先构造 `reorder / detached_tail` 的 typed calibration 与全新 locked blind，不复用已观察 r3 blind，不先实现 detector。

## 2026-09-03 — P1 structural benchmark taxonomy / reporting contract

结构算法继续研究前先补齐评估层，而不是继续调 ordinary timing threshold。既有 strict calibration / locked blind workflow 保持唯一方法学主线；dataset schema `1.1` 新增可选、truth-side `structural_scenarios`，canonical 类别固定为 `none / hard_cut / same_track_splice / crossfade / true_overlap / sequential_transition / piecewise_rate / reorder / detached_tail`。1.1 未标注 case 只在报表层归入 `structural:unspecified`；显式标签 canonical 排序后进入 ground-truth identity，非法值/重复值/`none` 混用 fail closed。schema `1.0` 不接受该字段，并保持旧 report shape，不新增 structural scope。

公共 metric engine 与 canonical `v4_calibration_workflow.py` 现在都可按 `structural:<scenario>` 输出 aggregate metrics，cut-boundary metrics 同样支持 structural scope；这使后续 calibration policy 能区分不同结构失败类型，同时保持 opaque case IDs / aggregate-only privacy contract。benchmark 专用 `language=synthetic` 仅在 generic metric engine 内映射为 `generic` tokenization，不扩 production language profile。该变化只扩 evaluation metadata/reporting，不修改 Max 算法版本、coarse/Fine/TimeWarp、transition threshold、review 或 release authority。

冻结 r3 兼容回归精确复现 calibration SHA `737e83697f1e577bbf9c8473e21b54ad304c33d1c6f09404fc45abe10853e330` 和 blind SHA `2e9c49321ac3541d2d5f3fdb953ddbdecab1f0c09f3ed80a6249aae83bbdc886`；去掉新增 structural-only report fields 后，历史 calibration / blind evaluation 与新评估递归全等。

## 2026-09-03 — Max a14 task-local semantic run configuration

真实回归验收暴露出一个与 alignment threshold 无关的可复现性缺口：旧任务的 `language_map / middle_cut_map / lyric_role_map / profile` 虽然会被 asset artifact 分别记录 SHA，但调用者仍可能在另一次 `v4_run.py` 中漏传某个 CLI 参数，从而让“同一 task manifest”在没有显式迁移动作的情况下进入不同 asset-resolution 语义。典型表现是已固定语言的任务因漏传 map 回到 `auto`，或更严格的同时间戳 lyric-role preflight 因漏传 role map 重新 BLOCK。

`4.0.0a14` 新增 task-local `qa/v4_run_config.json`（schema `v4-run-config-1.0`），与 raw-input `task_manifest.json` 分层。它绑定 exact task fingerprint，并记录 `profile / language_map / middle_cut_map / lyric_role_map` 每个非空文件的 repository-relative path、size 与 SHA-256，同时计算独立 `run_config_fingerprint_sha256`。`init_task.py` 默认创建该配置；旧任务可用 `scripts/init_v4_run_config.py` 建立/迁移，已有语义变化必须显式 `--replace`。

canonical `v4_run.py`、direct optimized、direct legacy 三个 public run entrypoint 都会在第一次 output mutation 前自动发现并验证 task-local config，再把缺失 semantic flags 展开给原 production parser。显式 CLI 与 config 漂移、绑定文件变更、task fingerprint 不匹配、config 为 null 却临时增加新语义输入全部 fail closed；没有 run config 的 legacy task 保持旧 CLI 兼容。run config 本身也进入 output-tree protected inputs。该变更不修改 TrackAsset 选择算法、coarse/Fine/TimeWarp、transition/review/release threshold；正式 asset artifact 仍以实际 semantic file SHA 记录 lineage。

## 2026-09-03 — Max a13 explicit detached-tail content extent closeout

走路带风120真实任务暴露出自动 trailing-zero content extent 的边界：主节目在 `2727.582s` 结束，随后连续 `279.594s` 为逐样本 exact digital zero，但文件在 `3007.176s` 后又包含约 `6.526s` 的孤立音频残片。因为物理文件末端并非完整 trailing zero，旧自动规则必须保守保留整个容器，进而把最后 occurrence 错误扩到 50 分钟附近并在空白区产生 coarse disconnect。

`4.0.0a13` 不扩大自动 silence/island 推断，而是增加可选、fingerprint-bound 的 `mix_content_extent` task input。该 JSON 必须绑定同一 task audio SHA、使用 `mix-content-extent-1.0` schema、提供正且有限的 `content_end_seconds` 和非空 reason，并且只能把自动 `content_end` 往前缩；任何延长、SHA 不一致、schema/数值异常都 fail closed。原 mix 文件、SHA 与物理 `mix_duration` 保持不变。optimized/legacy Max runner 共享同一 override 语义；无该 input 的任务完全保持 a12 行为。新增 task-fingerprint 与 shorten-only/SHA-binding regression。

## 2026-09-03 — Max a12 bounded mix decode closeout

真实长音频任务暴露出 coarse/Fine 子阶段仍会围绕当前 occurrence 反复解码远大于实际检索窗口的 mix 区间，并且压缩容器在物理文件尾端可能出现少量“声明时长略长于实际可解码样本”的 short-read。`4.0.0a12` 将 coarse/Fine 的 mix 输入统一改为 conservative bounded decode：只解码当前检索区间加固定 2 秒上下文，不改变 source feature、candidate pool、coarse/Fine/TimeWarp 阈值或 review/release authority。

尾端 short-read 只在请求本身已经到达物理文件尾、且缺失不超过 5ms（另保留历史 one-sample rounding tolerance）时允许 clamp 到真实可解码终点；中段 short-read、超过容差的尾差、没有覆盖请求区间的 decode 仍 hard fail。调用方必须继续使用真实 `effective_mix_end`，不得用零填充或虚构尾部 timing。新增 synthetic regression 覆盖小尾差、mid-file short-read、大尾差和 one-sample compatibility；coarse/Fine/end-to-end/interval/content-end 回归保持原语义。

## 2026-09-03 — Max a11 reference-retime renderer source-stage closeout

真实 KPOP200 再次验证了 a10 的 direct-review reference-retime 入口本身可用，但 renderer 在验证 `reference_retime` run 后仍无条件把 materialization source 当成 `overlap_recomposition`，导致合法的 `review_resolution -> reference_retime -> render` lineage 被错误要求提供不存在的 overlap metadata。`4.0.0a11` 只修正这一处 renderer 分支：reference-retimed run 后续 materialization validation 现在使用已在 reference-retime metadata 中严格验证过的 `source_run_stage`；review 来源不再虚构 overlap lineage，overlap 来源仍执行原有 overlap metadata / artifact identity 校验。coarse/Fine/TimeWarp、review decisions、retained-segment 映射、cut/overlap 判定、render composition 和 release gate 均未改变。新增 source-stage regression，review/overlap 两条路径都显式覆盖。

## 2026-09-02 — Max a10 direct-review reference-retime closeout

真实 KPOP200 复跑暴露出一个 production contract 缺口：任务没有 confirmed overlap，13 个 transition review 已全部闭合，但历史 waveform + lyric context 已确认单曲内部存在 source cut，需要用 `retained_segments` 删除 cut gap 内 canonical event 并截断跨 cut cue。现有 `v4_retime_reference.py` 的映射能力已经能正确完成该操作，却把 source stage 硬性限制为 `overlap_recomposition`，迫使无-overlap任务先制造一个没有语义的 overlap stage。

`4.0.0a10` 只扩展 lineage 入口，不修改 retained-segment 映射、coarse/Fine/TimeWarp、cut/overlap 判定或 release gate：reference-retime source stage 允许 `review_resolution` 或原有 `overlap_recomposition`。直接从 review 进入时仍要求 source run `ready_for_render`、issues 为空、非 legacy，并把 source run artifact 自身作为 source review artifact；overlap 路径仍校验 overlap metadata 的 source-review identity。`v4_render.py` 同步按两种 source stage fail-closed 验证，未知 stage、review identity 不一致仍拒绝。新增 source-stage contract regression，原 overlap/reference-retime/release/path-safety 回归保持通过。

## 2026-08-22 — Max coarse terminal coverage / transition activity closeout (#68)

Full V4 primary coarse 可在已证明至少三个连续 anchors、且不可连接区域只位于结构上有界的 terminal suffix 时保留 proven prefix 并继续 TimeWarp；leading/interior disconnect、超限 suffix、证据不足仍 fail closed。coarse artifact 记录 `path_coverage` 与 excluded terminal centers；Fine 只消费与 proven path 对齐的 prefix。

这项恢复**不确认**尾段 source activity、cut、crossfade 或 overlap，也不授予超出 proven coverage 的 canonical projection authority。`bounded_terminal_disconnect` 只允许内部 mapping 继续求解。

Shared-boundary transition probe 使用独立 `transition_activity` purpose：保留完整 retrieval windows，但不请求连续 TimeWarp，输出 `path_coverage.status=retrieval_only` / `timewarp.selection=NOT_REQUESTED`。transition score/margin/ambiguity/review authority 不变；retrieval-only artifact 不能被当作 primary mapping。

随后 authority review 进一步要求：当 bounded terminal recovery 排除了 suffix 时，canonical projection 不能依靠 affine extrapolation 在该 suffix 获得普通 timing authority。该边界已经进入 projection artifact/lineage，并保持 complete-path 行为不变。

## 2026-08-22 — Max render/release authority fail-closed closeout (#70)

对 #68 后完整 Max 路径的独立复核发现两个产品 authority 漏洞：

1. canonical timeline 可能正确地把 proven projection coverage 外的行留作 unresolved；这些行不能被静默丢失后仍生成“正常 final”；
2. 当前 `v4_render.py` 直接把 canonical timeline line materialize 成字幕 cue，而产品合同明确规定 LRC line break 不是最终 subtitle segmentation authority。

本轮只收紧下游 authority，不改 reconstruction、transition、acoustic、ASR、forced alignment、Smart/Pro 阈值或 mutation 权限：

- `lyric_aligner/timeline/composer.py` 检查 `projection_coverage.authority_omitted_line_count`；非整数、负数或 `>0` 都 fail closed。partial-prefix timeline 可继续作为 upstream evidence，但不能静默变成缺行的 final subtitle。
- 当前 canonical-line renderer 明确降为 evaluation-only；QA/stdout/final-render artifact 写入：

```text
publish_ready = false
segmentation_authority = canonical_line_evaluation_only
release_blocked_reason = editor_cue_reconciliation_required
```

- `scripts/v4_validate_release.py` 除原有 task/version/hash/upstream binding 外，必须看到唯一 final-render artifact 明确声明：

```text
normalized_config.segmentation_authority = editor_reconciled
```

否则 release fail closed。完成 transition/cut/overlap review、甚至 run 已是 `ready_for_render`，都不能替代 segmentation authority。

因此 #70 合并后的 Max 能力边界是：**完整 reconstruction/evidence + evaluation render 可用；production subtitle release 仍需独立 Editor-Cue Reconciliation。** 下一步是单独实现 evaluation-only reconciliation bridge，而不是通过降低 transition/acoustic threshold 绕过 gate。

Public regression 全部使用 generic synthetic fixtures：覆盖 omitted-line render block、malformed coverage、canonical evaluation render、release lineage/segmentation gate，以及 review/cut/overlap/combined 路径不会误获 publish authority。私有歌词、音频、cue 编号与真实时间戳不进入仓库。

## 2026-08-23 — Editor-Cue Reconciliation evaluation bridge

新增 `lyric_aligner/timeline/editor_cue_reconcile.py` 与 `scripts/v4_editor_cue_reconcile.py`，用于评估 #70 canonical-line evaluation render 能否在**不改变原 editor cue topology** 的前提下回填 canonical text/order。

首版刻意不重新解析或重建 Max timeline，而是消费已经经过 task/version/hash/upstream binding 的 `final_render` evaluation artifact，再与 task manifest 中 exact `source_srt` 对照。这样 canonical timing/occurrence lineage 仍只有 #70 render 一套真源。

结构判定固定为 fail-closed：

- canonical cue 完整 interval 被唯一 editor cue 包含 -> 该 ownership 可标 `resolved`；
- canonical cue 跨 editor boundary -> 涉及 cue 均 `still_review`；
- canonical cue 同时完整落入多个重叠 editor cue -> `still_review`；
- 同一 editor cue 内被分配的 canonical cues 彼此 overlap -> `still_review`，禁止静默压平成单 cue；
- 没有 canonical temporal evidence 的 editor cue -> `not_evaluable`；
- `rebutted` 保留为 schema 状态，但首版**永不自动产生**，直到以后有独立 token/word/audio boundary evidence。

stage 输出：

```text
stage = editor_cue_reconciliation_evaluation
segmentation_authority = editor_reconciliation_evaluation_only
production_authority_granted = false
```

即使 `full_topology_candidate=true`、所有 editor cue 均为 `resolved`，也只代表“现有 editor topology 与 canonical render 在结构上可兼容”的评估结果；它不修改 `v4_render.py`、不生成 production SRT、不改变 `v4_validate_release.py`，也绝不等价于 `editor_reconciled`。

额外记录 `editor_file_order_monotonic`。若 source SRT 文件顺序存在时间回退，单 cue 诊断仍保留，但 `full_topology_candidate=false`，避免复杂 reorder 在首版被误当成已闭环 production segmentation。

Public synthetic regression 覆盖唯一包含、1 editor cue 承载多条非重叠 canonical cue、跨边界、重叠 editor ambiguity、canonical overlap、无 evidence、非单调 file order、audit identity，以及 CLI 对 source render authority / QA / artifact lineage 的 fail-closed 检查。

## 2026-08-23 — Max artifact-writer path safety / strict QA types

在进入私有 Max review/render/reconciliation 校准前，对公开 CLI 做输入所有权复核，确认 `v4_review.py`、`v4_render.py`、`v4_validate_release.py` 原先没有统一 output-path collision gate。误填输出参数时，理论上可覆盖 task input、run/artifact、timeline evidence 或 final 文件。

本轮只收紧 artifact writer 安全边界，不改任何 Smart/Pro/Max 算法、threshold、review action 或 segmentation/release authority：

- 新增共享 `protected_task_input_paths()`：保护 task manifest、所有 file inputs，并把 manifest directory input 展开到每个 fingerprinted 文件成员；
- `v4_review.py` 的 template/apply 在写入前保护 task inputs、run/run artifact、decisions，并保证多个输出互不重合；
- `v4_render.py` 在第一次 materialization 前同时保护 task inputs、run/TrackAssets/asset artifact，以及 run 实际读取的每个 canonical timeline/timeline artifact；四个 render outputs 必须 pairwise distinct；
- `v4_editor_cue_reconcile.py` 复用同一 shared task-path contract；
- `v4_validate_release.py` 保护 final SRT/audit/QA、所有 upstream artifacts 与 task inputs，release manifest 不得覆盖任何输入；
- release/reconciliation 的 `review_candidate_count` 不再使用 Python `int(...)` 宽松强转，`false`、float、string、null 都不能冒充整数 0；
- release upstream artifact 与 `normalized_config` 必须确实是 JSON object，畸形 artifact 受控 fail closed。

这些变更不产生新的 timing/text/segmentation authority。`canonical_line_evaluation_only`、`editor_reconciliation_evaluation_only` 和 `editor_reconciled` 三层语义保持不变。

CLI 安全契约集中到 `references/v4-cli-contract.md`，并加入文档同步 owner 集合。

## 2026-08-23 — Max release authority consistency hardening

继续复核 production release gate 时发现：`v4_validate_release.py` 已要求 `final_render.normalized_config.segmentation_authority=editor_reconciled`，但没有同时验证同一个 final-render artifact 的 `evidence` 与其 hash-bound QA 是否声明相同 production authority。若未来 materializer 产生内部自相矛盾的 artifact，单看 config 可能形成 false-ready。

本轮只收紧 release consistency，不新增任何 production authority：

- final-render `normalized_config.segmentation_authority` 仍必须是 `editor_reconciled`；
- final-render `evidence.segmentation_authority` 也必须是 `editor_reconciled`，且 `evidence.publish_ready=true`；
- exact hash-bound QA 同样必须声明 `segmentation_authority=editor_reconciled` 与 `publish_ready=true`；
- artifact evidence 或 QA 仍携带非空 `release_blocked_reason` 时 release fail closed；
- 任一层缺失、evaluation-only、not-publish-ready 或彼此矛盾都不能进入 release manifest。

当前 canonical-line renderer 和 reconciliation evaluator 均继续是 evaluation-only，因此行为保持 blocked；本变更只保证未来 production materializer 必须在 config/evidence/QA 三层形成一致、可审计的 authority contract。

## 2026-08-23 — Max cut/overlap/combined materializer output-tree safety

继续审查 review 后的 Max writer chain 时发现，`v4_rebuild_cut.py`、`v4_recompose_overlap.py`、`v4_compose_materializations.py` 会在 `--out-dir` 动态创建 Fine/mapping/timeline artifact，但此前没有像 review/render/release 一样的 output-tree ownership gate。错误的 `--out-dir` 可包住 task input 或已存在的 coarse/Fine/transition/timeline provenance，并在后续 `mkdir`/子进程/materialization 时污染或覆盖输入。

本轮只改变 CLI 文件所有权边界，不改变 materializer 算法：

- output tree 与所有 protected input 双向不相交；
- task input subtree、直接 run/artifact、TrackAssets，以及输入 payload 中递归声明的全部 `*_path` lineage 都在首次 `mkdir`/子进程/write 前保护；
- 三条公开 `v4_*.py` 变成薄安全 entrypoint；原 cut/overlap/combined implementation 以 blob-identical `_v4_*_impl.txt` internal source resource 保存，由通过 preflight 的 wrapper 以非 `__main__` 名称加载，不暴露第二个 `v4_*_impl.py` CLI；
- `--help` 保持原行为，原 E2E 仍实际执行相同实现 blob。

该修复不改变 review decisions、cut/overlap detection、mapping、timeline reconstruction、render/release authority；只防止 materializer 在取得不安全 filesystem ownership 后再开始写入。

## 2026-08-23 — V4 orchestration output-tree ownership gate

最终 P0/P1 收口扫描发现，顶层 `v4_run.py` 会在证明 output-tree ownership 之前进入 `OutputRunLock`，而 direct `v4_run_optimized.py` / `v4_run_legacy.py` 也会先创建 cache/session/stage 目录。若 `--out-dir` 落入已 fingerprint 的 task input subtree，这些 orchestration 写入本身就可能先污染受保护输入。

本轮把同一双向 output-tree gate 前移到三条公开 run entrypoint 的第一次写操作之前：保护 task manifest、所有 manifest-bound input roots/subtrees，以及显式 profile/language/middle-cut/lyric-role config inputs；output tree 既不能位于这些输入内，也不能反向包住它们。canonical `v4_run.py` 必须在创建 `.v4-run.lock` 前完成检查，direct optimized/legacy entrypoint 也必须在 cache/session/stage `mkdir` 前完成检查。

为避免安全修复混入 orchestration 算法 diff，legacy 与 optimized 原实现继续以 blob-identical internal source resource 保存：

```text
legacy    a20afb27ca7030033e86618cebea6414eea36ceb
optimized c7838ac50ab2b2202ee93bda5bd22801ec5d8d9a
```

公开 regression 必须覆盖 canonical / optimized / legacy 三种直接调用，在 unsafe output 位于 fingerprinted input subtree 时证明输出目录和 `.v4-run.lock` 均未被创建；同时覆盖 output tree 反向包住 task inputs 和显式 config input 的情况。该变更只收紧 filesystem ownership，不改变 alignment、evidence、render、release authority 或 Smart/Pro 策略。

## 2026-08-23 — V4 primary-stage writer ownership gate

继续从 production orchestrator 向下枚举真实 child process 后，确认 `v4_resolve_assets.py`、`v4_coarse_align.py`、`v4_fine_align.py`、`v4_probe_transition.py` 仍可被直接执行，并在没有 ownership preflight 的情况下写 `--out` / `--artifact-out`；coarse 还会写显式或由 production layout 推导出的 feature-cache tree。仅保护顶层 run 因而不足以阻止 direct stage CLI 污染已 fingerprint 的 task input 或 upstream lineage。

本轮把相同 fail-closed ownership contract 放到四条 public stage entrypoint 的原实现首次 write 之前：

- task manifest、全部 manifest-bound input roots/subtrees 与 direct upstream/config inputs 均受保护；
- TrackAssets/coarse 等 JSON 输入中递归声明的 `*_path` provenance 也进入 protected input 集合；
- `--out` 与 `--artifact-out` 必须 pairwise distinct，且不得覆盖/进入 protected input；
- coarse `--feature-cache-dir`（包括默认推导 cache）按动态 output tree 处理，与 protected inputs 双向不相交；
- 普通 `--out`/`--artifact-out` 只拥有各自文件，不把合法 stage artifact 共处的整个父目录误判成 writer-owned tree。

为避免安全修复混入 asset/coarse/Fine/transition 算法变化，四份 production implementation 直接复用原 Git blob：

```text
resolve_assets    162b1d9dfc25b3ae2e5995d0e790c47dbcc931f8
coarse_align      735c9aa1a98607953206aedbe1264f7680b5c145
fine_align        005ba2744ba299ded2eed4c7ee7a8c9511448706
probe_transition  eabf2b2f10f67d1057adab992b395ee562a1f8c4
```

Public regression 使用 generic synthetic task，直接调用四条 CLI，证明 unsafe output 在任何 stage artifact 被创建前即失败；另覆盖 coarse cache 进入/包住 task input 以及两个固定输出重合。该修复不改变 TrackAsset resolution、Source-to-Mix、Fine、transition score/margin、TimeWarp、readiness 或 release authority。

## 2026-09-01 — Max terminal interval float serialization fix

真实私有任务暴露出一个通用 orchestration 边界 bug：production plan 的最后一个 occurrence 会把 `primary_end` 设为 exact mix duration，但 `_coarse_command()` 原先固定格式化为 6 位小数。若 duration 在第 7 位小数触发向上舍入，序列化后的 `--mix-end` 会极小幅超过真实音频长度，随后被 coarse 的严格 `mix_end > mix_duration` gate 正确拒绝为 `invalid occurrence mix interval`。

修复只改变 run orchestration 的 CLI 浮点序列化：`--mix-start/--mix-end` 使用 Python round-trip float representation，不调整 coarse/Fine/TimeWarp 阈值、mapping 逻辑或任何 authority。新增 generic synthetic regression，专门覆盖“固定 6 位格式会向上越过 terminal duration”的情况；真实任务名称、音频时长和时间戳不进入 public test。

## 2026-09-01 — Max TimeWarp drift diagnostics honor robust inliers

真实私有任务进一步暴露出一个通用诊断一致性问题：robust TimeWarp fitting 已经通过 inlier mask 排除 gross retrieval outlier，但 early/middle/late drift diagnostics 原先仍对全部 residual 直接求位置桶均值。这样已被 robust fit 明确认定为 outlier 的少量错误 retrieval 仍会二次进入 drift authority gate，可能阻断一个其余 inlier 高覆盖、低残差且速率稳定的 affine mapping。

修复不改变 robust inlier threshold、drift threshold、piecewise threshold、feature threshold 或 cut authority。位置 drift 现在优先仅由对应桶内 robust inlier residual 计算；如果一个位置桶完全没有 inlier，则退回该桶全部 residual，继续 fail closed，不把无证据区域伪装成零 drift。新增 generic synthetic regression：仅前部少量 gross outlier、其余连续 anchors 保持稳定 affine 时，不应因为已排除 outlier 再次触发 false drift block。真实任务名称、时间戳、BPM 和音频数据不进入 public regression。

## 2026-09-01 — Canonical timed-credit / instrument-section filtering hardening

真实生产任务暴露出 canonical LRC 预处理的一个通用内容完整性缺口：已有中文制作信息过滤较完整，但英文 timed credits（publisher、instrument/session performer、recording/mixing/mastering、Dolby Atmos 等）仍可能被 TrackAsset/canonical parser 当作歌词；另外，provider 偶尔会把仅由多个乐器名称组成的纯器乐段落标签写成普通时间行。这两类非歌词文本如果进入 canonical timeline，会污染后续 Max render/reconciliation，即使声学 mapping 本身正确也无法产出可信字幕。

本轮只收紧共享 canonical metadata grammar，不改变 source-to-mix、TimeWarp、Fine、transition、cue segmentation 或 release authority：

- 中英文 metadata 继续走同一 `is_metadata_text()` 真源，TrackAsset lyric-role preflight 与 canonical parser 保持一致；
- 英文 credit 只按明确的 production-role grammar 过滤，例如 publisher、`<role> by:` / `<role>:`、recording/mixing/mastering/Dolby Atmos 工程字段；
- 无冒号的 `mixed/mastered/recorded at ...` 仅在后文包含明显 studio/mastering/recording 场所语法时过滤，避免误删普通歌词如 `Mixed at midnight ...`；
- 裸纯器乐标签只在整行由两个及以上乐器名称通过 `and` / `&` / `/` / `+` 连接时过滤，不因普通歌词中出现 bass/drums/guitar 等词而删除；
- explicit TrackAsset selection 仍不能把已判定为 metadata 的行重新引入 canonical truth。

Public regression 全部使用 generic synthetic fixtures，覆盖英文制作 credits、multi-instrument section marker，以及 instrument/credit-like 普通歌词的反例。相关 canonical / lyric-role / asset-resolver / text-repair + 文档版本 identity 回归共 67 项通过；真实任务仅用于 private QA，不把曲名、歌词、人员、时间戳或音频事实写入 public algorithm/test。

同轮审计还发现当前权威 `v4-status.md` / `v4-runtime-guide.md` 曾标为 `4.0.0a9`，但当前 HEAD、`origin/main`、运行时 `__version__` 与真实 artifact 均为 `4.0.0a8`。Git 历史证明 a9 曾存在于一个未进入当前主线的 transition-activity 条件化提交，而当前 legacy/optimized 实现也没有该 a9 行为，因此本轮把**当前权威文档**纠回真实 a8，历史 archive 保持不变；新增 generic identity regression，要求两份权威文档的主线版本始终等于 `lyric_aligner.__version__`，防止后续再次漂移。

兼容性：合法歌词与既有中文 metadata 规则保持不变；受影响的只是此前误进入 canonical truth 的明确 provider metadata。回滚点为本变更前 Git commit；Max artifact 继续通过 `git_commit` + task fingerprint 区分 lineage，算法版本不因这次 parser hardening 单独改号。

## 2026-09-02 — Narrow confirmed-overlap interval + minimum-duration cue repair

私有 Max 成品链路暴露出两个通用下游问题，均在不降低既有门槛的前提下收紧处理：

- transition review 的 `confirmed_overlap` 原先只能把整个候选 review interval 视为重组区。候选窗口通常刻意较宽，用它直接物化会把没有双源证据的前后片段一并扩成 overlap。现在 review decision 可选携带 `confirmed_interval=[start,end]`，且必须严格包含于原候选区间；未提供时保持旧行为。`resolved_clear` 等其他 action 不能携带该字段。overlap materializer 同步接受该候选子区间，并独立验证它不得越出 transition artifact 的原 candidate interval；因此 review 与 materialization 两层都 fail closed。transition activity coarse 继续只承担边界活动/lineage 证据，即使其 `timewarp.selection=NOT_REQUESTED` / `path=[]` 也不会被错误送入 Fine 或冒充连续 TimeWarp；歌词 timing 仍来自已经验证的 primary canonical timeline，confirmed region 只扩展该 timeline 的 occurrence window。若 primary timeline 带 `bounded_terminal_disconnect` projection authority，则任何越过 `mix_end_ms` 的 overlap 扩展直接拒绝。这样 reviewer 可把宽候选缩到证据实际支持的 crossfade 子区间，同时保持 Source-to-Mix 与 projection authority 分层，不改变 transition detector 本身的 score/margin/ambiguity 阈值。
- canonical timeline 偶尔会产生低于 renderer `minimum_cue_duration_ms` 的极短 cue。renderer 不降低全局 250 ms 门槛，而是先利用真实相邻空白扩展；仍不足时，只在相邻 cue 保持同一最小时长的前提下重分配边界。若邻居没有足够 temporal capacity 仍 fail closed。若歌词主体起点位于 occurrence authority window 之外、只剩不足最小时长的 clipped leading fragment，则省略该残片而不是显示闪字；confirmed-overlap recomposition 仍可在有证据时重新引入对应内容。

Public regression 使用 synthetic timeline/review fixtures，覆盖窄 overlap 子区间越界拒绝、错误 action 拒绝、内部极短 cue 邻接重平衡、clipped leading sliver 省略、无足够 donor capacity 时继续阻断；并复跑 timeline composer、review decisions、overlap recomposition、projection/render guard 与 overlap end-to-end。真实任务歌曲、歌词、时间戳与音频不进入 public test。

## 2026-09-02 — Editor topology rebuttal production materializer

私有长混剪的 Editor-Cue Reconciliation 实测表明，某些 editor SRT 并不是可保真的完整 cue topology：canonical timed stream 中会出现与任何 editor cue 都没有时间交集的歌词。此时若强制“不移动/不新增 editor cue”，会必然丢失 canonical truth。项目因此新增 `scripts/v4_materialize_editor_reconciled.py`，但只开放一个窄、可证明的 production rebuttal path。

materializer 必须消费 exact hash-bound canonical evaluation render 与 `editor_cue_reconciliation_evaluation`，要求 editor file order 单调、`full_topology_candidate=false`、至少一个 `no_editor_temporal_overlap` witness、reconciliation assigned/unassigned/status 计数闭合，并且 canonical audit 全部来自 `line_lrc / enhanced_lrc / qrc_word_timing` 显式 timing。普通 `canonical_interval_crosses_editor_boundary` 不能单独触发 rebuttal。成功时 final SRT/audit 与 evaluation SRT/audit exact byte-identical；只有新 QA 与新 `final_render` artifact 获得 `editor_reconciled` / `publish_ready=true`，原 evaluation artifact 保持不变。

Release gate 没有增加例外：production final-render 的 normalized config、artifact evidence 与 exact QA 仍必须三层一致，之后仍由 `v4_validate_release.py` 正常验证。Synthetic regression 覆盖成功 materialize→release、仅 boundary-crossing 继续阻断、unsupported timing format 阻断、reconciliation 内部计数不闭合时阻断；同时复跑 editor reconciliation CLI、release lineage、overlap E2E 与 projection/render guard。真实任务内容不进入 public tests。

## 2026-09-02 — Production display policy / model-reviewed presentation layer

真实成品复核确认需要把“canonical lyric truth”与“平台最终展示”严格分层：规范歌词可能包含高置信 typo/标点问题，同时平台发布还可能要求把明确强脏词做显示打码；line-LRC 又只有行起点，少数歌词会被 `next_line_start` 被动拉成极端长挂字幕。这些展示修订都不应回写 canonical text/order truth，也不重新推导 Max/Fine mapping 或 segmentation authority。

本轮新增 `lyric_aligner/text/display_policy.py` 与 `scripts/v4_apply_display_policy.py`。display stage 只允许消费已经 `editor_reconciled`、`publish_ready=true` 的 production final-render，并冻结 cue count/number/start、occurrence、track 与 canonical-line identity。显式模型修订必须绑定 exact task fingerprint 与 `occurrence_id + track_id + canonical_line_index + expected_text`，只有 `confidence=high` 才可 materialize；expected text 不一致、override 未命中或命中不唯一均 fail closed。输出 audit 同时保存 `canonical_text` / `display_text`、source/display start/end、policy identity、reviewer 与 reason，并重新计算 viewer-facing `text_sha256/cue_id`。

新增窄 `strong_profanity_v1` 自动显示 profile：明确强脏词（例如 `fuck/fucking`）按首字母加星号显示为 `f*`，但 canonical 原文保持不变。`sexy`、`shot`、`bullet`、`trigger`、`fire`、`kill`、`damn` 等语境相关歌词不会自动改写，需模型/人工按具体上下文判断，避免过度净化。

同一 display policy 还可显式启用 `trim_extreme_unknown_end_v1`：只接受 `source_end_basis=next_line_start`，只在源 duration 达到 integer trigger 后，把 viewer-facing end shorten-only 为 `start + max_display_hold_ms`；max hold 必须严格小于 trigger。start 永不移动、end 永不延长，`open_end` 与显式 timing authority 不受影响，原 source timing 必须保存在 audit 中。这是对“未知 end 的展示上限”建模，不把它冒充 vocal-end 检测。

Display stage 生成新的、hash-bound 的 `stage=final_render` production artifact，并以上一层 production final-render 为 upstream；既有 `v4_validate_release.py` 不增加任何例外，发布时仍要求 exactly one final-render 与三层 `editor_reconciled` / `publish_ready=true` authority 一致。Public regression 只使用 synthetic lyrics，验证强脏词窄打码、expected-text mismatch / 非 high-confidence / unmatched override fail-closed、start/identity 冻结、极端未知 end 只缩不伸、`open_end` 不受影响，以及 display materialization 后仍能通过原 release validator。真实歌词与真实任务 timing 审计只保存在 private task policy/QA。

## 2026-09-02 — Trailing digital-zero content extent + recoverable editor file order

真实长混剪导出暴露了两个与声学阈值无关的通用边界。第一，容器末尾可能附带数百秒纯数字零；若仍把物理 duration 作为最后 occurrence 的搜索终点，会无意义扩大 terminal window。新增 `lyric_aligner/audio/content_extent.py`：只在尾部 digital-zero run 至少 30 秒时把 `content_end` 缩到最后一个非零样本之后，普通 fade/近静音/底噪全部保留；`mix_duration` 继续作为物理 provenance。production plan 只用 `content_end` 限制最后 occurrence 与 end clamp。Public regression 覆盖长数字零尾、短零尾不裁、content_end 不得早于 nominal start/超过物理 duration。

第二，editor SRT 偶尔只是文件块顺序错位，而各 cue 的真实时间区间并不重叠。Reconciliation 现在同时记录原始 `editor_file_order_monotonic` 与窄 `editor_file_order_recoverable_nonoverlap_reordering`：只有每一个相邻 inversion 都满足 `right.end_ms <= left.start_ms` 才可恢复；任一 inversion 时间重叠仍 fail closed。Topology rebuttal materializer 可接受这类明确非重叠的文件顺序错误，但 `full_topology_candidate` 仍要求原始 file order 单调。Public regression 覆盖 recoverable inversion 与 overlapping inversion 拒绝，避免把“文件行顺序错误”误当成“时间 topology 冲突”。

## 2026-09-02 — Read-only final candidate audit

多次私有任务验收重复实现了相同的 final SRT 几何检查，因此抽出 `lyric_aligner/qa/final_candidate_audit.py` 与 `scripts/v4_audit_final.py`。该工具 strictly diagnostic-only：不写 production artifact、不修改字幕、不授予 authority；在 exact SRT/report + publish-ready QA 基础上，从同 task run/timeline 读取 occurrence windows、`content_end` 与 confirmed-overlap regions，统一检查 final file order、非正 cue、occurrence/content-end 越界、same-occurrence overlap 与未确认 cross-occurrence overlap，并报告短 cue/长驻留分布。长驻留只告警；已确认 overlap 只有 cue 交集完整落入同 TrackOccurrence pair 的 confirmed region 才合法。

Audit output 也受 fail-closed path ownership 保护：task/direct 输入以及 run 递归声明的所有 `*_path` lineage（包括实际 timeline）都不能被 `--out` 覆盖。Synthetic core regression 覆盖 clean candidate、window 越界、confirmed/unconfirmed overlap、same-occurrence overlap、长驻留 warning、非单调 final order 与 count mismatch；真实 production final 另做 private smoke，证明通用 audit 可直接替代任务专属结构审计。

## 2026-09-02 — Prepared-stem splice candidate：calibration 通过、fresh blind 淘汰

Private r2 calibration 显示 raw Max 普通歌词 timing/text 已接近 production truth，但同曲内部手工 splice 仍可能被单一 Fine source trajectory 吞掉。prepared-stem candidate 尝试使用剪辑前调速/预处理单曲 stem 扫描多 lag mode，并用局部双源 waveform OLS 验证 same-track splice。真实 calibration 中它能无需人工 lag 重建一例约 6 秒 source-offset handoff，自动 crossover 与 production truth 相差 170 ms，且在保持 prediction SRT/QA 完全不变时把 `cut_precision/recall` 从 `0/0` 提升到 `1/1`。

候选曾以 commit `1dbf82b` 临时进入 public，以便把 calibration selection 绑定真实 revision。随后审计发现原 r2 blind manifest 的结构标签已经暴露，原 blind split 作废并 quarantine；新建 r3 fresh blind，在 selection 前用 deterministic seed 锁定 8 个未见 structural case，并在 candidate 锁定后首次 materialize prediction。blind gate 的预设要求为 cut precision=1.0、recall>=0.75，同时 timing/text/review 零回归。实际 fresh blind 中 candidate 没有产生任何 cut prediction，`cut_precision=0`、`cut_recall=0`，gate fail。

按照 blind-test 纪律，不允许在看到该结果后继续调 threshold 或 fixture 以挽救 candidate。因此 public prepared-stem core/CLI/test 被撤回，项目继续维持现有 Max review/recomposition authority；private calibration/blind 产物保留为负证据。该结论只说明当前实现缺乏已证明的泛化能力，不否定 prepared stem 在特定人工复核中的辅助价值。

## 2026-09-02 — a9：抑制亚阈值 backward retrieval jitter 的假结构阻断

真实华语男声190 a8 复跑暴露了一个 TimeWarp 判定不对称：forward source jump 只有在超过连续速率包络且 `excess_source_jump >= min_excess_source_jump` 时才升级为结构 discontinuity，但 backward source jump 原实现对任何负 delta 都直接 block。结果两个整体 affine/Fine 质量良好的 occurrence 仅因尾部低-margin ambiguous retrieval 出现 96 ms / 276 ms 小幅回摆，就整首进入 `AFFINE_WITH_DISCONTINUITY_REVIEW`。

a9 让 backward jump 与已有结构阈值使用同一最小幅度语义：`abs(source_delta) < min_excess_source_jump` 视为局部 retrieval jitter，不生成 discontinuity；达到或超过阈值的 backward reorder 仍保持 fail-closed block。默认阈值继续为 1.5 秒，没有修改 calibration profile、forward cut envelope、piecewise selection 或 review authority。Public regression 覆盖亚阈值 backward jitter 被忽略，以及 >=1.5 秒 backward jump 继续阻断。

## 冻结与回滚

Smart/Pro production freeze tag 继续固定在：

```text
prod-smart-v1.2.5-pro-v1.1.4-20260821
56841c40d6a90101efe1da568e2d5c2e5e67a0a2
```

Max #68/#70 与后续 reconciliation evaluation / CLI safety / release consistency maintenance 不移动该 tag，不改变冻结 Smart/Pro 的行为。回滚依赖 Git commit/tag + artifact lineage，不维护第二套静默 fallback。

## 2026-09-03 — 封板维护

补充 runtime base 的 `soundfile` direct dependency 与环境预检文档；加强 docs-contract 对环境脚本和 requirements 变更的防漂移约束；TrackAssets manifest、task manifest/QA JSON 与 task-local run config 统一复用 shared `atomic_write_json()`，消除固定 `.tmp` 的并发竞争；canonical evaluation render 的 SRT/audit CSV 改为同目录临时文件 + `fsync` + `os.replace`，避免中断留下截断正式输出；订正 task-path safety 注释；明确历史 freeze tag 与旧 change-record 的边界。无算法阈值、timing/text/release authority 改动。

## 2026-09-07 集成准确率升级

新增单次升级入口连接现有 calibrated materializer、旧人工边界自动复用与 final SRT 配对评估；human reuse policy 更新为 1.1，改用整条时间轴动态规划解决相邻边界冲突。保留产品版本 a19、旧 schema 及历史 artifact 身份。真实 7 项目 6041 cues 回归：H180 3 start + 2 end，0 text；其余 6 份 SRT 字节一致。独立串联验证 H180 646→781 cues；重复执行 0 新变化。该改进复用旧标注，不是新模型盲测通过或全产品质量封板。


### 2026-09-07 自动续作复核修正

修复人审复用到 QA 的断链：新增 receipt 1.1 fresh replay 和逐边界 QA scope。修复独立评估缺少 final task identity、最终 SRT 仅比较可重算 hash、QA 输出可能覆盖 receipt、搬迁后 review 路径失效，以及 DP 漏检非相邻嵌套重叠。policy 1.2 保留活动区间 frontier。真实最终 QA 识别 23 个确认边界，清除先前误增的 4 条风险，原 5 条未授权 gap 继续阻断。SOFA/HuBERT 对 gap 重新实测仍未形成完整可授权区间，不将失败候选强行转为生产结果。
# 2026-09-09 欧美140 Latin lexical floor 1.1

真实英文140运行暴露字符归一化相同仍可能 false-ready。增加 Latin word-boundary floor，mid-word newline/cue split fail closed；Smart 使用 mapped trusted-region，不要求 raw LRC 全覆盖。30-screening 集合 old30->new0（screening，不是人工gold）；unresolved 196->176，A 9->91，新增82 A均为 original editor raw normalized exact+unique 的 presentation-only replace，raw lexical mismatch A=0；timing mutation/repair=0；无human timing gold，不宣称 timing accuracy 提升；当前 review_required，Pro未重跑。

# 2026-09-08 FLOAT 源音观察实验

新增 opt-in FLOAT ASR decode，保持默认身份兼容；真实整曲时钟一致，减少中间整数饱和失真，尚未证明识别准确率收益。完成 781 cue shadow 回读、15 项含真实音频的针对性测试、隔离 Python 3.12 全量 1543 项（4 项可选音频测试跳过，已在音频环境单独通过）、独立 cache 复核。证据在 `output/source_context_upgrade5_20260908/float_implementation_report.md`。

## 2026-09-08 第十切片：剪映局部时间轴复用闭环

修复 `v4_preserve_editor_occurrence.py` 两处作用域错误：region 请求在唯一 whole-cue 区域确定后检查其跨 occurrence 边界，整段请求仍保持原检查；新产物的 canonical_content_start/end 从区域局部坐标转换为完整绑定歌词坐标，同时验证所选歌词为连续且与绑定原文一致。既有 schema/policy 标识不改，历史产物不重写。

真实 WALK 唯一 Smart 区域 [4,70) 重建 66→60 cues，完整 SRT 882→876，816 个非目标 cue 原文与时间保持，归一化全文流一致，60/60 下游 ownership 可解析；相同语义边界修改 55 start、55 end。旧 HFA 30 可比端点的平均分歧 2297.667→238.2ms；joint HFA 12 端点 2511.417→162.417ms。两组相关且不是人工真值，不能宣布全部准确或封板。

声学连续窗口 oracle 17 目标端点 MAE 138.147→143.765ms；同文本同配置外侧位置对照仅5目标，99.8→96.2ms，均不支持推广长窗口默认。完整证据与限制见 `output/source_context_upgrade10_20260908/`，实验登记同步保留正负结论。

## 2026-09-08 第十一切片：普通多语种顺序消歧

普通source shadow复用现有source_sequence全最优重复消歧，策略升级v5。完整目标packet lattice与observer自哈希绑定，promotion独立记录，不改变局部资格、声学搜索、模型、阈值或几何规则。Gee相对同轮旧v4输出仅新增1 end（53948→53798ms），1157条其它cue不变；Whiplash同候选消融仅新增1 end（56294→55209ms），785条其它cue不变。两处都是写出变化，无独立gold，不宣称准确率收益。详细对照见 output/source_context_upgrade11_20260908/gee_comparison.json、whiplash_ablation.json。

基础CI的4项FLOAT测试因缺av条件跳过，音频环境已补跑4/4成功。现有CI asr-environment新增显式import av/numpy/soundfile及test_float_decode，依赖缺失先失败。未添加新依赖或命令。

## 2026-09-08 第十二切片：逐段语种检测实测

新增默认关闭的 source_asr.multilingual=true（language=null），原生逐段检测、source-observer-1.2 独立缓存；旧默认和1.0/1.1缓存身份不变。Whiplash同turbo/音频对照：auto 5候选/5采用，整曲en 0/0，逐段自动11/10；新模式相对输入写出8 start、10 end，文字不变。这是覆盖和写出变化，没有独立端点gold，不能声称准确率提升。

Al James独立公开词起点诊断：严格唯一上下文匹配92/312，220保留null；新旧共同92起点全部一致，MAE539.966ms、p95 1543.249ms、max5880ms无变化。旧en与新auto+multilingual同时改变两个控制，不能称单因素；归因附加更正保留原报告和收据。无word-end真值、不是blind，不推广默认、不宣布封板。逐段模式开头误识别未恢复，并丢失auto的第36条候选（end55209退回56294ms）；相对auto共11条cue时间变化，不能称无损收益。三组固定对照完整记录于output/source_context_upgrade12_20260908/。

## 2026-09-08 维护收敛修复

按用户确认停止扩张式升级，改为维护既有链路。普通与shadow作业入口拒绝误拼/未知配置，防止source_config未生效或human_confirmation被当无证据跳过；39份历史作业字段检查兼容。区域恢复同时读取完整canonical字符范围与旧行索引，修复WALK已恢复字幕再次同区域处理报missing/noncontiguous的问题；字符范围按已选文字顺序验证连续性，支持旧/新混合输入，不靠查找相同歌词猜重复位置。保留现有source候选、DP代价、几何规则、生产默认与历史artifact身份。

当前优先级见[维护收敛执行约定](maintenance-convergence-2026-09-08.md)，真实修前失败、修后重放与验证收据存output/maintenance_convergence_20260908/。工程恢复可用性不等于新增声学准确率；不以测试通过或候选数量宣布封板。

## 2026-09-08 本地版本固化

以maintenance-20260908标记已验证的维护与显式实验源码快照；产品版本和旧prod tag保持，算法扩张暂停。保留范围、验证限制与独立目录回滚方法见[本地交付说明](local-delivery-2026-09-08.md)。

## 2026-09-08 能力说明与源码上传准备

新增能力与使用边界说明，区分工程评分、辅助修复成熟度与未知的整体识别正确率；同步SKILL和状态入口。此次文档更新不改算法、生产版本或历史产物，不上传私有任务数据及代码。源码推送不等于字幕成品发布或新的准确率封板。


## 2026-09-09 editor-first / hybrid production 封板升级

- 新增任务级 `editor_preservation.scope=all_occurrences` 与事务式 batch：每个 occurrence 重复恢复 exact、unique、neighbor-compatible 的 immutable editor 区域直到稳定；Smart immutable observation 同批缓存，真实 KPOP130 输出与未缓存/逐段链逐字节一致。
- 修复旧 topology rebuttal 的全局替换风险：单个 `no_editor_temporal_overlap` witness 不再授权整份 canonical evaluation timing。production 改为 exact-bound hybrid，要求 preservation 至少真实恢复一次 editor、完整 canonical character coverage、无 gap/overlap，并同时绑定 source render/reconciliation/preservation lineage。
- 修复三类真实长项目兼容缺陷：reference-retimed 相对路径不再依赖 cwd；crossfade 下 occurrence 全局非连续不再阻断内部安全 region；纯符号/音乐标记等 nonlexical cue 原样保留且不进入文字 region matcher。
- display policy 支持 hybrid split/merge ownership：全局 mask 与 shorten-only timing 继续工作，显式 override 仍要求唯一单行 identity；KPOP130 viewer display 774 cues 实跑通过，15 explicit overrides、2 masks、6 end trims，仍精确绑定 hybrid production artifact。
- KPOP130 historical development 8 条边界从旧 FRESH_FINAL MAE 584.9375ms 降至最终 viewer display 501.6875ms；该集已参与开发，不称 blind。六个真实任务合计 85 occurrences，其中 31 个有严格安全恢复、41 restore stages、713->641 region cues；该数字只表示恢复覆盖，不表示总体准确率。
- 产品版本继续为 `4.0.0a19`，新能力由独立 policy/mode/artifact lineage 标识；不改写历史 a19 artifact，不把此前未获独立收益的 SOFA/STARS/Qwen/HFA 等实验模型提升为生产默认。
- 最终三层封板核对：KPOP130 viewer display v3 structural audit `passed=true`、errors=0、window/content-end violation=0、confirmed/unconfirmed overlap=0；仅有10条 `long_display_holds` warning，最大7563ms且无>=8000ms extreme hold。该结果属于 viewer structural QA，不等同 semantic/release gate。
- 当前 semantic/release gate 仍 fail closed：projection editor witness 2/12 track fail，independent-audio final layer 12/12 fail，`audio_anchor_count=0`。formal fusion `01575459...` 是 `e22f10d` safety tightening 前旧证据，缺少当前 ASR 起点资格字段；需 fresh independent audio evidence/fusion，不能复用旧 semantic QA，也不能把 materializer `publish_ready=true`写成完整 release-ready。
# 2026-09-09 — adjacent positional transition adjudication

Introduced `adjacent-transition-positional-v1-conservative` as a bounded
second-stage evidence path. It narrows retrieval around source positions
predicted by accepted adjacent primary mappings; it is not a global threshold
relaxation. Only clear sequential transitions may be recommended automatically;
possible overlap remains human review.

## 2026-09-12 Best-Safe 1.0 产品层

- 保持 Standard / Smart / Pro / Max 证据 authority 不变，新增 `best-safe-evidence-selector-1.0` 产品 selector：Max 逐曲 semantic gate 通过才晋级，否则保留原始 Smart-safe；未授权跨曲 timing regression 自动回退。
- Best-Safe 从原始 Smart 直接重放，不引入 Smart+ 中间架构。task-bound GPT-5.6 Sol text proposal 绑定 task、Smart SRT/report 与全部 canonical lyric SHA，只能引用连续 review cue + exact canonical gap；free-form text/timing 禁止进入 proposal，最终复用完整既有 text-region policy（resolved bracket / region similarity / length ratio / multi-line observed coverage / safe word partition）决定是否物化。Max semantic-BLOCK 区域内 canonical 与 Smart 实质冲突且缺少独立反证时保留 Smart。
- 新增 canonical identity 优先的 Smart 曲目归属，修复秒级 songs.txt 边界下 crossfade 首句误归。自动 canonical presentation 仅恢复显式 `*` mask；普通撇号、空格、词边界等只允许经 task-bound normalized-equivalent model display override 修改。默认执行 `strong_profanity_v1`。
- 欧美经典140 fresh 任务正式 Best-Safe 为 1020 cues：8 首 semantic-pass 曲使用 Max、7 首 BLOCK 曲使用 Smart-safe。34 个 BLOCK-track 文字 review 区域中，14 个进入 text proposal；完整 verifier 后仅 4 区域 / 8 cues 接受、10 拒绝，另 20 区域 / 49 cues 由模型主动保留 Smart；最终 30 区域 / 71 cues 作为显式残余风险保守留在 Smart。重复吟唱/ad-lib 次数无独立 cue-level ownership 时不自动 rescue。canonical 显式 mask 自动恢复 9 cues，确定性敏感词 mask 额外处理 1 cue，task-bound display override 9 cues。transition #4→#5 根据 `resolved_clear` 将左端裁到 797000ms；0 未授权跨曲 overlap。Max Release 仍因 `2/4/10/11/13/14/15` semantic BLOCK 而不可发布。
- Best-Safe output tree 使用统一 path-safety，受保护输入目录不能被输出污染；QA/artifact 明确区分 Best-Safe `publish_ready` 与 Max `release_ready`。

## 2026-09-12 Best-Safe 1.1 Smart timing floor

- 用户人工抽查直接否定 Best-Safe 1.0 的 whole-track Max timing promotion：Toxic `You're toxic I'm slippin' under` 人工真值为 `00:01:56:25 @30fps ≈ 116833ms`，Smart 正好为 `116833ms`，1.0 吸收 Max 后为 `116404ms`，提前约429ms/13帧。第3、5首大量改动且人工表现较差；第7首虽大改但较可信，证明“变化幅度”只能是风险指标，不能单独判断准确率。
- 1.1 改为 `best-safe-smart-timing-floor-1.1 / 1.1.0`：Smart cue 数、顺序、start/end 是默认不可静默退化的 product floor；Max track semantic PASS 只进入候选列表，不再允许整首 timing/topology、split/merge/add/delete 写入。transition authorization 默认仅诊断，不能为拼接候选静默裁 Smart。
- 新增 boundary-level timing truth/promotion 契约：truth 绑定 task fingerprint + Smart SRT SHA + `track/source cue/start|end/truth/tolerance`；promotion 绑定冻结 Smart boundary 并必须引用独立 truth。当前自动 timing authority 仅开放 `human_truth`；未来机器 timing promotion 必须先有独立 calibration + boundary verifier，证明相对 Smart 的期望误差更低。`unsupported_timing_change_count=0` 和全部 truth PASS 是 `publish_ready` 必要条件。
- 新增文字 cue-ownership floor：多 cue text adjudication 在无 timing authority 时不得插入、删除或跨 cue 搬移 Latin token，只允许 per-cue token ownership 不变的同位词形/错词替换。欧美140的自动 text rescue 因此从4 regions/8 cues 收紧到2 regions/4 cues；12 proposal 被 verifier 拒绝，32 regions/75 cues 明确保留 Smart。
- 欧美140 Best-Safe 1.1 正式产物恢复为923 cues；逐 cue 相对 Smart timing diff=0，human truth 116833ms 误差0。随后把旧1.0未覆盖的 Max-pass 曲 Smart review 一并纳入，总计70个 review 区域全部显式记账；代码再增加完整 coverage invariant，Smart report 的155个 `review` cues 必须由 proposal/keep-Smart 155/155 唯一覆盖，否则 fail closed。最终 ledger 为16 proposals + 54 model keep-Smart；deterministic + cue-ownership verifier 接受4 regions/6 cues、拒绝12，66 regions/149 cues 保守留 Smart。最终24 cues 有经过各自门禁的文字/display变化，残留可 mask 强敏感词0、英文粘词0。最终独立审计 PASS，SRT SHA=`adc5f26b10ffd95da3d039482b9a942312075b12cf5c586823f04d41768212bb`。

## 2026-09-12 P1 boundary-level promotion shadow

- 在 P0 production tag `prod-v4.0.0a20-best-safe-v1.1.0-20260912` 冻结之后，新增纯 evaluation 的 `boundary_promotion_shadow.py` / `v4_boundary_promotion_shadow.py`，不改变该 tag、Smart/Best-Safe production authority 或任何 task artifact。
- P1 复用既有 `timing_decision_pack` / candidate-blind review / decision validation，不再引入新 ASR/forced model。`freeze-selection` 必须在人工 truth 前完整冻结 selector id/revision/code SHA、每个 boundary 的 KEEP/PROMOTE、frozen Smart/candidate ms、独立 evidence family/correlation group 与 gate policy；漏记、重复、stale、同 correlation group 自证、rehash 后 identity/count 漂移全部 fail closed，Smart==candidate 的 deterministic unchanged control 不得伪装成 promotion 补数量或 track 门槛。
- 首轮 shadow preregistration 要求至少24个有效 truth、4个独立 track、8个实际 promotion 且覆盖4个 track；新 >500ms catastrophic error=0、>100ms harmful rate<=5%、P90 不退化、mean/track-equal gain 为正、track-bootstrap 95% CI 下界非负。intended partition 必须在人工 review 前冻结；只有预先冻结为新的 `blind|holdout` review 才能 PASS，development/calibration 结果不能事后重标，只作诊断。
- 无论 shadow gate PASS/BLOCK，当前实现固定 `production_authority_granted=false`、`production_writeback_permitted=false`，不能生成 Best-Safe `timing_promotions`。如果新 untouched truth 真正证明净收益，仍需另开 production authority schema/code/test/docs 变更；否则继续 Smart/Best-Safe timing floor。
- Qwen/SOFA/HuBERTFA/STARS 进入 dormant experimental：重型权重已清理，源码保留用于历史复现；后续不得因为代码仍存在就自动下载/恢复 production。重新启用前必须有新的 production failure、预注册实验和 untouched truth。
- P1 封板审计进一步关闭 pre-gold lineage 漏洞：frozen `selection_payload_sha256` 与 intended partition 必须在人工 review 前一起写入 candidate-blind review manifest；人工 raw response 绑定该 manifest，Gold ingestion 必须使用相同 partition 并继承 manifest hash + selection hash + partition。P1 evaluate 会由 frozen pack/selection/partition 确定性重建 exact manifest，验证全部 case/clip/text/instructions、拒绝 candidate/private 字段泄漏，再调用 review validator 从 raw response 重算期望 Gold 后逐字段比对。因此旧 Gold 不能事后换绑新的 selector，development/calibration response 不能重标 blind/holdout，response 后手改 Gold/partition 也 fail closed。该 artifact hash 链不冒充外部可信时间戳，真正 blind campaign 仍必须保存原始 response、禁止事后重写整套 artifact。
- P1 selection/evaluation JSON 统一使用 shared path-collision guard、new-output-only 与 atomic JSON writer，evaluate 的 pack/selection/review-manifest/review-response/gold 全部作为受保护输入。最终代码快照 P1 focused `22/22`、timing-decision review `11/11`、Best-Safe focused `20/20`、完整工程 `1908 tests / OK`；compile、Skill、privacy、dirty docs-contract（19 changed / 5 substantive / 0 issue）、diff-check 与静态 production-reference 审计全部通过。未发现 Smart/Best-Safe/Pro/Max/release 生产路径导入 P1，也没有 `production_authority_granted=true` / production writeback 路径。
