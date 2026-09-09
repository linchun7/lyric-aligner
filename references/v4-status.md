# Lyric Aligner v4 当前实施状态

## 2026-09-08 有界终轮实验：候选未升为生产默认

本轮执行调速参考音频对应检查及 SOFA / STARS × 原混音 / HTDemucs 分离人声对照。现有 editor/Smart 基线继续保留；没有独立 final-mix 人工答案与非 oracle 实际收益时，不将候选模型或逐点最优结果宣布为准确率终版。

新增 `evaluation.interval_bottleneck` 离线分解：完整候选区间 oracle、允许单边组合的合法 oracle、忽略几何关系的逐点 oracle 分开报告。未标注相邻 cue 固定，缺失候选保留分母，缺失 gold 归属显式报告。它不写字幕、不增加生产 gate、不改变模型默认值。模型运行和快照证据留在本地 `output/bottleneck_final_20260908/` 与 `output/bottleneck_fresh_final_20260908/`，不进入发布包；新 final-mix 人工标注仍是未完成项。

当前对外能力说明以[能力与使用边界](capabilities-and-limits.md)为准：维护可用，通用无人审核交付尚未证明；下文实验数字须按各自样本和产物层级理解。

> **当前执行阶段：editor-first / hybrid 封板维护（2026-09-09）。** 已把可证明的 editor timing 保真、canonical 结构完整性与 production materialization 接成完整链路；仍暂停无独立 final-mix truth 支撑的扩张式声学默认升级。下文 2026-09-08 的实验排期与“下一步”均保留为历史记录，不自动触发。


2026-09-08 第九切片新增旧锚夹持的完整精确词序源锚，只进入显式joint实验。WALK882条重跑共同解码2→3对，恢复219/220，旧两对区间不变；但220与KEEP221仍重叠3017ms，全局采用0、成品时间/文字变化0。历史三首149行得到9个精确锚（4新增），新增8端点MAE165.75ms、最大312ms；这是源锚证据，不是成品或新盲测精度。无条件二段拆词的43076项词典遮蔽验证仅34.84%发音一致，未接入。隔离Python3.12全量1625项通过（4项可选FLOAT跳过）。全部正负实验登记见 references/accuracy-experiment-register-2026-09-08.md；本轮证据见 output/source_context_upgrade9_20260908/delivery_report.md。不封板。

2026-09-08 第八切片已实现显式相邻共同窗口与原子选择。同输入 WALK882条实跑触发4对，2对完成一次共享声学解码，另外2对因缺词dancefloor/缺右锚拒绝。两处内部候选重叠1929/862ms→0，但全局选择0，成品区间变化0，文字变化0；旧FW和旧HFA输出字节一致。这证明局部冲突机制可修复，不证明整段准确率提高；保留实验身份，不默认推广、不封板。下一步需获得长行内部可靠词级锚与可验证发音覆盖，不能靠放宽外部几何强行采用。工程与实测证据见 `output/source_context_upgrade8_20260908/delivery_report.md`。

2026-09-08 第七切片新增通用英文派生词典构建器及anchored-path显式manifest接入。原字典保留，1052项新增发音均来自既有尾撇号词形或固定CMU直接记录。WALK120完整882条重跑：13次推理/17完整候选（此前5），选择0，最终区间0变化。已有三首公开歌保持9/143覆盖和MAE45.222ms，未测到新精度收益。rank18同音频仅加英文提示，词覆盖0→165/203、锚0→3，但候选仍0/44。主要瓶颈为完整源上下文资格和相邻区间兼容；不加搜索预算、不改canonical、不默认推广。最终工程验证、失败日志与复核见 `output/source_context_upgrade7_20260908/delivery_report.md`。

2026-09-08 第六切片已完成显式实验 anchored-path-v1：源时间带进入解码DP，同53行开发回归端点MAE858.321→207.047ms、最大32160→1867ms。另三首新公开歌149行/143内部目标仅覆盖9行（8旧+1新增），两策略输出相同，条件MAE45.222ms、最大107ms；134个无候选，不能宣称普遍增益。隔离Python3.12全量1583项通过、4项可选音频测试跳过。WALK120最终路径重跑882条，HFA候选5、选择0、新增成品改动0。保留实验身份、不封板。完整失败、修正、冻结与最终证据见 `output/source_context_upgrade6_20260908/delivery_report.md`。

第五切片最终工程验证：隔离 Python3.12 完整1564项/187.086秒通过（4项可选FLOAT音频测试跳过，已在音频环境另跑通过）；36项相关隔离测试、skill、实际dirty/untracked文档契约及独立复核通过。首轮因测试误依赖私有文件发生7错误，已只修夹具并保留失败日志。源码与真实字幕产物未在修夹具时改变。

2026-09-08 第五切片新增 HFA 三行 provider、独立 `hfa-only-overlay` 与 `lexical_only_no_ap`。历史 22 行/44 端点 FW→no-AP MAE 291.682→116.727ms、>500ms 9→3；新五首169行只覆盖2行/4端点，462→192ms、>500ms 2→1。均为合格源端点条件指标，不是整曲精确率。真实 WALK120 的882条字幕：Rihanna恢复1个双端候选，但与下句KEEP冲突2379ms，HFA选择0、成品改动0；原FW shadow与接入前字节一致。HFA台账534MB→241KB，完整源观察及最终相对引用核验通过。当前有可运行的新声学候选，尚无该节目新增成品收益，也不封板。最终验证见 `output/source_context_upgrade5_20260908/delivery_report.md`；更早“第五切片进行中”段落是阶段记录。

2026-09-08 新增带左右上下文的forced segment区间提取API，避免取序列末词终态；19项相关测试、三份真实TextGrid重放及隔离Python3.12全量1536项测试通过（179.418秒），独立复核通过实验范围。SOFA同目标三窗口结束时间波动3000→44.13ms，属于窗口稳定性改善，不是绝对准确率；默认pipeline未变。FLOAT无损前处理对照亦已完成，保留旧证据。

2026-09-08 第三切片保留全曲重复位置共识为实验模块，默认shadow策略及生产SRT不变。开发集182行17→24；预冻结新4首137行15→15，新增收益未推广。Qwen整曲/短窗均未胜过已有FW，拒绝接入。新增实验模块经独立复核及隔离Python3.12全量1529项测试通过（312.648秒）。当前不宣称封板或本轮最终SRT精度提升；证据见 output/source_context_upgrade3_20260908/implementation_report.md。

第二切片的独立公开源端对照完成：4 首/182 行，旧、新可选句数为 15/17，共同 15 句不变；新增两句的四个端点误差均 ≤187ms，未定位仍为165行，已有候选最大起点误差3378ms。该小样本结果支持候选覆盖改善，尚不能证明最终混音 SRT 精确率或封板；完整冻结协议与评价见 output/source_context_upgrade2_20260908/public_multiline_v1/。

2026-09-08 第二切片完成目标内部转录错误恢复，并修复孤立 exact、n-best 遮蔽及重复字符计算失控。三期完整 shadow 字幕选择 5/1/6 条、改变起点 4/1/4 和终点 5/1/6，文字不变；相对第一切片只新增 WALK120 两条字幕的时间变化。旧 24 边界新增精度收益仍为 0。隔离 Python 3.12 的 1519 项测试通过（213.629 秒），独立复核 PASS。当前策略仍为实验身份，完整实现、实际差异、哈希回读和公开多行检验记录在 output/source_context_upgrade2_20260908/；不据变化数宣称封板。

2026-09-08 原曲上下文到完整影子字幕链路已完成：整曲识别、容错邻句与精确目标字符归属、有效映射投影、兼容区间联合选择、独立首尾候选、SRT/CSV 回读和当前基线配对报告均接入统一入口。WALK120/H180/KPOP110 三期并行实跑分别选择 4/1/6 条字幕，起点变化 2/1/4、终点变化 4/1/6，文字变化 0；已有 24 个人工边界均未改变，历史起点/终点 MAE 仍为 30/9.17ms。因此本次可确认的是候选与实际写回能力，尚无已标注边界上的精度增益。修复并发缓存半写、失败 staging 重试、既有重叠下的起点逆序和改时后旧人工凭证残留。隔离 Python 3.12 全量 1485 项通过（150.712 秒），独立复核通过；实验身份不替换生产 final，也不声明封板。执行协议见 [原曲上下文与区间联合升级](source-context-shadow-upgrade.md)，证据位于 output/source_shadow_upgrade_20260908/。

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

七项目28个真实CLI观察完成，detected language为zh11/ko10/en7，未覆盖日语。按现有0.72支持阈值，新fusion保留5个完整区间和额外3个缺尾句首；3个旧逻辑会误作完整区间的观察已改为仅句首。无首高分误用在这28样本中计0，不伪报减少数。数据只证明本批词汇覆盖处理更可靠，不证明最终SRT精度或未见曲泛化；所有原始/重试选择和输入失败原因保留于general_edge_validation_v1。

通用识别可靠性升级：两个 ASR 后端统一首尾覆盖语义，faster-whisper 两种执行路径分别置空未知端点；fusion 对新覆盖证据只在双端完整时比较整句区间，缺尾时仍保留同一个ASR family的独立句首，semantic QA可使用它而不伪造句尾。正在按冻结选择做跨项目实跑，不能将局部歌曲修复当作整体精度结论。

六曲实际尝试完成：Training Season 51→49、舞娘38→40，另外四曲因跨歌曲边界或canonical流不完整保留基线。新单次升级入口实跑882→880且无新增人工。QA层错误串用已有双向red→green回归修复；新版实物仍12/12失败，因此不能据诊断修复宣称通过。

当前新增按曲 editor-preservation 实际写回与单次升级接入。Training Season 已从 Max 51 cues 恢复到原编辑器 49 cues，同时校正 canonical 文字；39 个真实 canonical 行起点有索引，10 个内嵌起点保留未知。非目标歌曲文字/时间不变。实物 lineage 与 9 项定向测试通过。整体 semantic QA 仍未通过；其旧 projection/final 错误耦合意味着失败曲数不能直接当作新 final 实测错误曲数。未声明发布或跨语种封板。

更新日期：2026-09-07
主线算法版本：`4.0.0a19`

直接续开发新增两项修复：editor semantic witness 采用 `editor-semantic-disjoint-onsets-1.0`，每个候选 span 只贡献一次起点；六项目重放消除 283 次重复起点复用，但这属于 QA 修复，不计为最终 SRT 精度提升。可选本地 `qwen3_asr` 已接入正式 ASR evidence CLI：仅对独立识别文本定位，缺失首尾分别保持未知，fusion 要求完整区间才比较两端。该入口仍是可选观察后端，尚非自动多模型救援，也未取得生产 timing authority。

WALK120 已生成 Training Season 51 cue 的歌词时钟候选。六处 source/mix 波形观察支持现有映射，source ASR 显示 LRC 自身相对所供音频存在递增偏差。`source-lyric-clock-candidate-1.0` 将 LRC 时钟与声学映射分离。冻结后的额外九条观察中，七条可用于唯一词段 onset 代理比较，全部改善，中位差 3796→284ms、最大差 5765→853ms；两条未知仍保留在报告内。与 editor 的 43 条匹配中位差 3899→181ms、严重偏差 33→0。上述均非人工真值；候选仍 experimental，19 个边界为外推，无生产授权。重复 ASR 匹配已保留并列候选，不再伪报唯一边界。最新完整代码检查为隔离环境 1362 tests / 158.691s。

最新第四份核对已处理：5/5 confirmed，安全写入四个边界，原子 QA 27 个已验证边界、4 条 interval 仍未验证。新增可重放的连续非词汇人声显示合并，实际显示 SRT 781→777 cues，保留全部歌词及原两端，不制造逐行声学边界。原子报告不改写，显示派生产物未声明发布通过。后续开发不等待用户常规试听。

单次升级入口现自动完成 gap review 后的显示派生及迁移后验证，实际结果位于 `output/accuracy_upgrade_20260907/human_gap_ab_v5/h180/`，声学版与显示版分别与上次已验证结果字节一致。每小时续跑已按用户要求暂停，本次直接执行。独立 ASR 时间定位试验发现部分高分匹配只覆盖半句，未据此引入新的整句边界 authority。

历史第三份 gap 核对为 2/5 confirmed，三条连续 Na 未确认；当时写回 1 start + 2 end，H180 QA 验证 26 个边界、4 条 interval 仍未验证。新版可选试听支持选点前/后单独播放、直接输入步长、预设步进和所有片段尽量扩到前后各 30 秒（遇原音频结尾裁止）；已修复键入文本被强制格式化及 44.1 kHz origin 导出问题。无需等待这些人工标注才能继续开发。

新增近等速波形配准实验，已跑真实 K-pop 音频的已知变换和成品候选验证；默认关闭，仅提供独立候选，保留 feature-only path/timewarp/verdict，未提升为生产边界 authority。候选相关性不等于最终 SRT 精度。最新产物与适用边界见本轮实施记录。

本轮最终复核补充：ASR 重叠窗口与窗口外文字支持已修复，新 evidence 使用 v2 执行策略。H180 局部确认值复用的结果不代表其他项目已通过；六个其他项目的现有 semantic QA 仍有覆盖或结构同步阻断，不能把“只剩 5 条 H180 gap”写成全项目结论。模型候选与真实精度的最新可宣称范围见本轮实施记录。

2026-09-07 准确率升级本地交付：已增加配对诊断、分曲抽样及 `exact-human-outer-boundary-reuse-1.2`。后者复用已经确认的 exact audio/lyric 边界，H180 已自动写回 3 个 start、2 个 end，新增人工 0；不授予模型或未确认边界权限。按用户后续要求继续使用旧 24 个片段开发，暂不要求新增 60 个标注。旧 gold 不充当新盲测，局部确认值应用不解除整包 release 阻断，也不代表跨项目封板。新增 `v4_upgrade_subtitles.py` 单次执行入口，已完成 7 项目 / 6041 cues 实跑及 H180 internal→确认值→评估串联；联合 geometry 使用 active interval frontier，覆盖非相邻嵌套重叠；receipt 1.1 已接通逐边界 QA，23 个确认边界通过，仍有原 5 条 gap 阻断。Max Next B/C 泛化写回仍未达到证据条件，不能视为该交接全部完成。详见 [本轮实施记录](accuracy-upgrade-review-2026-09-07.md)。
工程封板：`4.0.0a19 post-seal authority/provenance hardening`
当前工程验收：`references/v4-postseal-hardening-verification-2026-09-06.json`；旧 `references/v4-max-engineering-seal-2026-09-06.json` 仅保留为 post-seal review 前的历史工程 seal。

> PR #70 前的完整状态说明已无损归档到 `references/archive/2026-08-22-pre-max-authority-v4-status.md`。P3 前状态见 `references/archive/2026-08-19-pre-p3-v4-status.md`。生产基线见 `references/production-requirements.md`；Smart / Pro 细节见 `references/smart-pro-v1-1.md`。
>
> **2026-09-07 reference-retime semantic evidence compatibility hardening**：renderer 自 a11 已正式接受 `reference_retime` run，但 a19 的 editor/mix-ASR/planner/fusion consumer allowlist 曾遗漏该合法 stage，导致含 reference-retime 的 fresh Max run 在 semantic-sync 取证前被错误拒绝。当前只对与最终 mix timeline 直接一致的 editor evidence、mix ASR first/second pass、alignment planner 与 evidence fusion 补入 `reference_retime` / `reference_timeline_retime`；不改变任何 timing/evidence/release 阈值，也不重新授权 source forced-alignment。`v4_execute_forced_alignment.py` 与 `v4_project_forced_alignment.py` 继续对 reference-retime fail-closed，因为现有 source→mix projector 尚未显式组合 reference-retime 变换，禁止沿用原 coarse/Fine timewarp 冒充 retimed projection。
>
> **2026-09-06 post-seal review（当前最高优先级）**：a19 最终 SRT 的 runtime `publish_ready` 已撤销。复审确认 legacy automatic gap insertion 有 5 条仅由 projected LRC 定位的新 interval 被错误标成 `manual_verified_interval`，并因此绕过 unverified-timing QA；当前代码已移除该伪 authority，并用 `projected_lrc_gap_candidate_no_boundary_authority` 使其 fail-closed。a19 Human-Gold joint **internal split authority 本身仍有效**，但整份 H180 成品在这 5 条获得 exact independent audio/manual boundary authority 或被安全删除/重建并重新 QA+seal 前不得发布。正式失效记录：`references/v4-boundary-authority-a19-invalidation-20260906.json`。
>
> 同轮 seal hardening 已要求 raw backend runs 重新执行正式 adjudication，并逐对象匹配 sealed evidence/decisions/bundle；spread 规则也已澄清为“普通 individual-calibration consensus 执行 250ms gate，独立 Human-Gold joint selector 不继承该固定 gate”。下方 a19 `publish_ready=true` 与原工程 seal 均仅保留为**历史封板事实**，不再代表当前 runtime readiness。
>
> **2026-09-06 a19 sealed update（优先于下方 5.0 中保留的 a18 WIP 历史叙述）**：replacement Human Anchor V2 已完成 `24/24` 真人确认，pending=`0`。六个单 backend scope 的 start/end/internal 仍全部未取得 production authority，outer start/end 因此继续确定性 `keep_editor`，不得放宽 edge guard。internal 改由最后一条 replacement gold 揭晓前已冻结的 `dual_aligner_nearest_lexical_ratio_prior_v1` joint selector 独立校准；正式 artifact `private/_calibration/internal_selective_consensus_calibration_华语青春180_20260906_v1.json`（artifact SHA=`84174bb8439cc3458acc1568ff60b579d6845be2d838327150420e7d24a2e1de`）在 8 calibration + 4 holdout 上均 100% coverage，calibration median=`0.165` 帧、P90/max=`1.92` 帧，holdout median=`0.735` 帧、P90/max=`1.92` 帧、catastrophic=`0`。该 joint authority 只授权 internal，不倒灌为 SOFA/HuBERTFA 单模型 authority。
>
> 华语青春180 a19 fresh internal plan 共 `152` 个 boundary；SOFA=`144/152` aligned，HuBERTFA=`141/152` aligned。HuBERTFA production batch 对精确 `No duplicate groups` item-local failure 使用 observer-contract 外的二分隔离：成功点仍来自原冻结 batch adapter，3 个最终 singleton 仅标 unavailable；adapter contract 未变。joint adjudication 授权 `137/152` internal boundaries，materialization 从 `646` 行得到 `781` 行，其中 `264` 行带 `audio_verified_internal_joint_lexical_selector_v1`。用户明确指出的 40:28 text-ownership 错位随后通过 hash-bound text-only post-materialization correction 修复 `544/545`，`timing_changed_count=0`。最终 structural/text QA：`publish_ready=true`、8/8 regression、0 review candidate、0 unverified timing mutation、0 lyric gap/duplicate/unexpected overlap。最终 SRT 为 `output/华语青春180_v4_authority_a19_20260906/corrected/internal_direct_v2_textfix_v1/corrected.srt`，SHA=`02b89d49db3f8cbc5886e9495804e3d0ee1b409ef0ed91e7333223e4c84f9f7a`；release seal `output/华语青春180_v4_authority_a19_20260906/qa/boundary_authority_overlay_release_seal.json`，seal SHA=`8732cb718b47b0c5b3910236ec3dd4d2ee41d8ce0021c45a3d9af4c93cb94d53`。
>
> 该 seal 的 scope 是 **recovery baseline + Human-Gold-calibrated internal boundary overlay**，不是 full Max semantic-sync release：当前没有与这条 recovery overlay 精确匹配的 current Max `run+fusion`，因此不得冒充 `v4_audit_semantic_sync/v4_validate_release` 的 full semantic authority。`whenever you come whatever we talk` 仍保留 editor `16:06.833–16:15.033`，因为 SOFA 无所需英文 lexical coverage；用户此前给出的 onset 只是“约 16:07.000”，在 outer authority 未通过时不把近似人工点伪装成精确毫秒。

> **2026-09-06 outer-observer / Expected-Loss closeout**：a19 internal authority 不变；outer start/end 没有新增 production authority。普通话 speech CTC 3A（Wav2Vec2 XLSR53）在 calibration 即被拒绝，未运行 holdout；singing-specific 3B（Mandarin LyricAlignment ASRU 2023）同样在 calibration 被拒绝，未运行 holdout。Independent Fine 使用与歌词 Human Gold 独立的 source↔DAW 调速真值建立 24-case calibration，冻结 `ambiguous=false + margin>=0.05` 后才运行首次 blind 24-case holdout；按预测前冻结的 protocol 最终 selected=`16/24`、coverage=`66.67%`、median=`12.04ms`、P90=`17.60ms`，但 max=`1040.14ms`、500ms catastrophic=`1/16`，因此 `failed_frozen_holdout_protocol`，且禁止依据 holdout 再调阈值。`boundary_risk` / Max Next 已升为 `1.1`，aggregate holdout profile 之外强制 exact candidate-specific `BoundaryLocalSupport`，population/catastrophic-threshold/local candidate binding 任一不一致即 fail closed；当前没有 observer 获得这种 local production authority，也没有新增 production materializer，所以 outer Expected-Loss 自动 fallback 继续关闭、editor timing 继续作为 strong prior。机器封板见 `references/v4-max-outer-observer-closeout-2026-09-06.json`（artifact SHA=`4439c76c2bd88a3bd244ba496be0fb63e351feb9bb837ad68c3c5901993425dd`）。
>
## 1. 当前四档产品路径

```text
Standard -> Text Repair V2.1
Smart    -> Canonical Sequence Reconciliation + Anchor Timeline Repair v1.2.10（no-audio）
Pro      -> Selective Audio Repair v1.2.7（bounded audio evidence + automatic adjudication）
Max      -> Full V4 Alignment
```

共同事实：

- Canonical lyric 是最终**文字/顺序** truth；
- LRC 行换行不是最终 subtitle cue segmentation authority；
- Jianying/editor timing 与 cue boundary 是强但可推翻的先验；
- 只有更强的 token/word/audio boundary evidence 才能推翻可信 editor segmentation；
- Higher mode 可以增加证据、减少 review，但不能在没有更强反证时破坏 lower-mode 已安全成立的 text / cue ownership / timing；
- Canonical truth 构建前会过滤明确的 timed provider metadata：中英文制作/工程 credits 与严格的 multi-instrument section marker 不能进入 canonical text/order；普通含 instrument/credit-like 词汇的歌词仍保留，explicit selection 也不能把已判定 metadata 的行重新引入。
- Legacy `v3.9` 仍是兼容/事故恢复身份，不重新成为默认路径；2026-09-04 的 edited-mix recovery hardening 只补 fail-closed metadata、task-bound canonical correction/cue drop、shared-LRC accidental duplicate review 与 QA writer 安全。该路径同样要求“canonical 负责文字/顺序、实际音频负责 timing truth、editor 只是可推翻 prior”，禁止对手工分段调速/裁前奏素材仅按单一 BPM 比例统一缩放 LRC。

## 2. Standard

Standard = Text Repair V2.1，适用于 timing 已可信、只修文字：

- no audio；
- cue count / number / start / end 冻结；
- deterministic canonical text repair + bounded 1↔N / N↔1 / N↔N；
- canonical 连续内容相同但 LRC/editor 分句不同，保留 editor cue ownership；
- ambiguous/mixed/unsafe layout fail closed；
- production auto threshold `>=0.72`；report schema `2.1`。

## 3. Smart v1.2.10

Smart 是日常主力 no-audio 模式。当前 facade 使用 v1.2.10，并继续满足：

- canonical text/order authority；
- four-A primary timing model gate 不降低；
- BPM-derived 只做 soft plausibility，exact DAW 才可作为 hard prior；
- Sequence/BPM/A-bounded recovery 都是 text-only evidence，不倒灌成 A/B timing authority；
- split-line 内部 editor cue 只有在 exact normalized token partition + reliable later token timestamp 下才允许使用内部 boundary onset；否则 `segmentation_internal_boundary_unvalidated` 且无 timing proposal；
- manual actionable timing queue 与 Pro high-value budget subset 分离；actionable suspicion 不会因 ranking 消失；
- output path collision、overlap safety、stale-policy rejection 继续 fail closed。

## 4. Pro v1.2.7

Pro 只处理 Smart 明确 unresolved 的 bounded regions：

```text
timing review -> local source<->mix acoustic first
text/identity review -> bounded ASR + word timestamps
source identity needs help -> auxiliary forced alignment
unmapped review -> bounded ASR
```

当前 contract：

```text
automatic_timing_change_allowed = false
automatic_text_change_allowed = false
timing_mutation_performed = false
```

v1.2.7 在 v1.2.6 planner 之上新增 decision schema 1.1 / adjudication policy v1.3：authority 为 `automatic_adjudication_no_srt_mutation`，scope 为 `decision_support_no_srt_mutation`。证据可自动收敛为 `candidate_confirmed_advisory`、`keep_editor_advisory` 或 canonical text/occurrence support advisory，并把人工任务区分为 confirm-recommendation 与 investigate；但所有 timing/text review 仍保留人工确认，`automatic_review_resolution_allowed=false`。Pro 继续固定 `automatic_timing_change_allowed=false`、`automatic_text_change_allowed=false`、`timing_mutation_performed=false`。

Acoustic schema 1.4 同时审计 slope 与 source-start 搜索边界；命中/接近任一搜索边界的 optimum 只能作为 diagnostic，不参与 timing fusion。ASR 只在 canonical-local language 与已知 source language 一致时固定语言；code-switch/mixed/unknown/source-auto 保持 backend auto-detect。

## 5. Max — Full V4 Alignment

Max 是 heavy fallback，用于整体 timeline/mapping 不可信、复杂 cut/overlap/reorder 或 Smart/Pro 无法安全收敛的任务。当前 primary chain 包括 TrackAsset、coarse/Fine/TimeWarp、canonical projection、transition/cut/overlap/review 等完整 reconstruction evidence。

### 5.0 a18 calibration-gated direct-final-mix boundary authority

`4.0.0a18` 增加一条与既有 Max reconstruction/release gate 分离的 boundary-authority 链，专门关闭“可信 editor 长 cue 被未验证 LRC onset 拆错”的真实生产缺口。outer cue start/end 与 long-cue internal split 被视为不同 authority kind；canonical lyric 继续只决定文字/顺序，editor timing 继续是强但可推翻 prior，LRC timestamp 只能用于 routing/search，任何 projected LRC 时间都不能直接创建或移动字幕边界。

生产 observer 由 versioned backend profile 固定为两个独立 direct-final-mix family：SOFA Mandarin singing alignment 与 HuBERTFA forced alignment。human-gold batch、outer single-boundary、internal single-boundary 与 production batch adapter 共享同一 `full-sequence-alignment-core-1.0` logical identity，但当前 production authority 不再只绑定逻辑版本或模型权重：每个 profile resolution 同时计算 `model_revision`、底层推理源码/字典/config 的 `implementation_revision`，以及完整 observer runtime 的 `adapter_contract_revision`。后者不仅绑定 gold/boundary/internal/batch 四个 sidecar adapter，也绑定真正消费这些 adapter 的 boundary/internal/batch executors、edge-clamp、window policy、lexical contract、human prediction 与 calibration core；因此不能把旧 blind prediction 或旧 calibration 用“当前 contract 值”补签成新实现结果。request/response 仍绑定 language、final-mix SHA、lexical contract 与 `editor_cue_plus_1500ms_clamped_to_mix_v1` window policy，raw run 另外绑定 exact plan SHA；任一模型、底层实现、observer runtime contract、窗口、canonical text 或 plan 变化都会使旧 calibration/evidence 失去当前 production authority。旧 schema artifact 可继续读取作历史/诊断，但缺少完整 runtime provenance 时即使统计 `passed=true` 也不能被当前生产链授权。

自动 authority 只来自严格 human-gold calibration。完整 60-clip / 90-boundary full pack 继续作为锁定 diagnostic population；production authority 使用从该 pre-model full pack 确定性投影出的 24-clip / 36-boundary human anchor，每个 `start/end/internal` 固定 12 点，其中 8 calibration + 4 holdout，至少 4 首不同歌曲，并继续执行既定 coverage/median/P90/max/catastrophic 门禁。machine consensus 可以在 human gold 之前对 full locked population 运行，但 authority 永远是 `machine_candidate_only_never_human_gold`，只用于候选/失败模式诊断，不能把模型输出倒灌成人工真值。outer/internal production mutation 仍至少需要两个不同 backend ID、不同 correlation group 的已校准 direct-final-mix evidence；简单的“两模型接近”本身不构成正确性证明。`v4_adjudicate_calibrated_alignment.py` 只生成 hash-bound evidence/decision/bundle，本身不写 SRT；`v4_materialize_calibrated_alignment.py` 仍必须重新绑定 task/source/final-mix/report/plan/evidence/decisions 与 exact adjudication bundle SHA，并 fresh re-adjudicate 后才允许 materialize。

**【a18 历史 WIP，已被页首 a19 sealed update supersede】** 当时软件路径保持 fail-closed，尚未获得任何 boundary production authority，`production_authority_ready=false`。正式 full diagnostic pack 仍是 `private/_calibration/human_boundary_gold_华语青春180_20260905_v4`：outer=30、internal=30、共 90 个 boundary points，selection lock SHA=`8f2b06f3d6c66382f78a3b22c0d2d9f4e285bc7048b47d932f2e056723746633`。其 production projection v1 完成 24/24 人耳操作后，又由真人备注暴露出一个更深的问题：`一吻天荒` 的一个 internal target 实际位于原锁定 clip 之外，因此该问题被标记 `target_boundary_outside_locked_clip`，v1 gold 仅保留历史证据，不再作为最终 production calibration truth。replacement pack `private/_calibration/human_boundary_anchor_20260905_v2` 只从同一 pre-model full benchmark 确定性替换这一无效问题，不读取任何 backend score；23 个合法人工确认原样继承，唯一 replacement case 需要重新人耳确认。internal 的 editor cue 现在只作为 reference，locked final-mix clip 才是 hard bound。真实 90-point blind diagnostic 还暴露了 start/end 的 padded-window edge collapse：应用 conservative edge-clamp guard 后 full blind scope coverage 为 SOFA start/end/internal=`24/30, 12/30, 30/30`，HuBERTFA=`27/30, 19/30, 30/30`。旧 blind scope 因未绑定完整 observer runtime contract 已降为历史证据；当前 v6 六个 scope 使用 `subtitle-machine-consensus-scope-1.2-full-adapter-contract` 重跑并绑定 full observer contract；新的 provenance-bound 聚合输出为 `private/_calibration/machine_boundary_consensus_华语青春180_20260906_v6.json`，90-point `records_sha256=a11e32394538979ea8fbfef9d82b58074ba5b78a7bb21ce6322cb0f8bf5f6367`、artifact SHA=`adfefd461884d3cf1c0a43d6ca40c3f87edd589981814b41af9fcdfb69192bfe`，并在 artifact 内直接绑定六份 blind-scope artifact SHA、model/implementation/four-adapter/adapter-contract revision 与 code SHA；authority 仍严格是 `machine_candidate_only_never_human_gold`。production anchor v2 的最新 v6 blind-scope reuse preflight 显示选中点为 SOFA start/end/internal=`11/12, 3/12, 12/12`、HuBERTFA=`12/12, 5/12, 12/12`；end 已低于 75% production coverage 门槛，因此不会通过放宽安全 guard 获得 authority。start 与 internal 仍等待 replacement human gold 完成后的正式 8+4 calibration；整个过程未执行字幕 timing mutation。

**【a18 历史 WIP，已被页首 a19 sealed update supersede】** 当时的 audit UI 封板：旧 60-clip Human Gold / UI 2.0 仅保留历史 diagnostic 数据与任务日志，不再保留活动启动入口；production review 只允许通过 replacement Anchor V2 启动链进入。当前启动链固定绑定 `127.0.0.1:8765`，端口占用时 fail closed，不再静默回退到 8766–8774；启动后必须反查监听 PID，并在线校验 `V4 boundary-authority / Anchor Pack V2 / Machine Consensus V6 / UI 3.2 / total=24 / complete=23 / pending_recheck=1 / remaining_unstarted=1` 后才允许打开浏览器。focused human-anchor suite 为 14/14 PASS。当前唯一未完成的人耳点为《一吻天荒》cue 628 的 internal boundary（`一转眼 忘了时间` → `丢了感觉 黑了世界`），因此 authority 继续保持关闭，直到该 replacement case 由真人保存并重新 ingest/calibrate。

同日对 v2 已确认 outer gold 的诊断进一步明确 production fallback 语义：editor baseline 在 8 个 calibration outer 上 start median/P90/max absolute error=`0/150/150ms`，end=`5/289/289ms`，明显优于当前两个全文 aligner 的歌唱末字结束表现；SOFA/HuBERTFA 的 end 低覆盖主要来自末音素被拉到 padded window 尾部后被 edge-clamp guard 正确拒绝。不得为了提高 coverage 放宽该 guard。现有 `v4_adjudicate_calibrated_alignment.py` 已支持 boundary-kind selective authority：某 kind 两个独立 backend 未同时通过 human-gold calibration 时，该 kind 不绑定 calibrated evidence 并确定性 `keep_editor`；`production_authority_ready=false` 因而表示“不是所有 kind 都具备自动改时能力”，不等价于“整条字幕生产链必须失败”。replacement human gold 已完成；正式结果如页首 a19 seal：internal joint selector 通过并仅授权 internal，outer start/end 仍未通过，因此继续保留 editor，不把 internal authority 倒灌到 outer。

2026-09-05 的剪映 viewer-level 对比进一步确认 `boundary_v18_diagnostic.srt` 也不能作为当前最佳底稿：其《笔记》段落把“回忆的画面/记录的语言”“载着我的想念/飞过了地平线”“你温暖的笑脸/还一如从前”等 editor 互补 fragment 都扩成两格相同的完整 canonical 行。该 artifact 已单独写入 `boundary_v18_diagnostic.HUMAN_QA_INVALIDATED.json`，只保留为回归证据。a18 recovery 新增 exact text-ownership restore：只有 editor 原文拼接后精确重建 canonical stream 时才自动把 canonical text 重新分回原 cue ownership；跨两个连续 canonical event 的三格 spill 也必须完整精确重建才允许处理，真实整句重复保持不变。未能安全恢复的 shared-event duplication 继续进入现有 high-review gate 并阻止 release。

### 5.0.1 a17 semantic timing release hard gate

`4.0.0a17` 把“字幕是否真的跟着最终音频唱词位置”提升为正式 release 必要证据，而不再只验证 artifact lineage、cue geometry 与 segmentation authority。新增 `v4_audit_semantic_sync.py`：优先使用 canonical text 对实际 source audio 的 forced alignment，再通过 source-to-mix 投影到 mix time，分别验证 Max canonical projection 与 exact final SRT；ASR 只有形成 canonical word-span 且 editor witness 本身经文本覆盖证明可靠时才可作为受约束 fallback，editor/Jianying SRT 永远只是 auxiliary witness，不能单独授予 release authority。默认每首独立音频锚点要求 median absolute onset error `<=1500ms`，且 `>2500ms` 的大误差比例不得超过 `25%`；证据覆盖不足或 forced/ASR 冲突均 fail closed。

从 a17 起，`v4_validate_release.py` 强制要求 `--run`、hash-bound `--semantic-sync-fusion` 与 `--semantic-sync-qa`；QA 必须同时声明 canonical projection sync 与 final sync `passed=true`，并精确绑定 task fingerprint、source SRT、final mix audio、song list、run、evidence fusion、final SRT 与 final audit report SHA-256。每首 release evidence basis 只能是 `forced_alignment` 或 `asr_plus_reliable_editor`；editor-only、insufficient、family conflict、stale 或任一 hash mismatch 都不得生成 ready release manifest。该 gate 专门防止“内部证据链完全自洽，但 canonical lyric timebase 本身不对应 source/edited audio”的 false-ready。

### 5.0.2 a16 early timed title-row metadata guard

`4.0.0a16` 修复 consumer LRC 中稍晚出现的 timed `artist - title` 身份行泄漏进 canonical lyric 的问题。共享 `is_title_like_intro()` 仍只接受带空格的字面 `artist - title` 形态，但识别窗口由首 1 秒保守扩到首 2 秒，以覆盖 provider 延迟以及任务级时间缩放造成的轻微后移；2 秒之后同形文本继续按普通 lexical content 保留。该共享规则同时约束 canonical parser、lyric-role preflight 与 text repair，避免某一路径单独漏滤。

### 5.0.3 a15 decodable terminal duration guard

`4.0.0a15` 修复压缩成品音频的“容器/解码器声明时长长于实际可解码音频流”问题。`detect_audio_content_extent()` 继续保留 SoundFile 解出的物理/容器 `full_duration` 作 provenance，但会用 `ffprobe` 的首个 audio stream duration 作为独立上界证据：只有在该终点之后的 SoundFile 样本全部为数字 0 时，才允许把有效 `content_end` 缩到可解码音频流终点；若 ffprobe 终点与实际非零解码内容冲突，则 hard fail，绝不静默误裁。该规则不扩大 a12 的 5ms bounded-decode 容差，也不把普通短静音当作可裁内容。

### 5.0.4 a14 task-local semantic run config

`4.0.0a14` 起，新任务由 `init_task.py` 同时创建 `qa/v4_run_config.json`。它不替代 raw-input `task_manifest.json`，而是单独绑定后补且会改变 Max asset-resolution 语义的 `profile / language_map / middle_cut_map / lyric_role_map`。config 自身绑定 exact task fingerprint，并为每个非空语义文件记录 repository-relative path、size 与 SHA-256，再生成独立 `run_config_fingerprint_sha256`。

`v4_run.py`、direct optimized 与 direct legacy 三个 public run entrypoint 都会在第一次 output mutation 前自动发现 task-local config、验证 task/file identity，并把缺失的语义参数展开到既有 production parser。调用者显式参数与 config 路径不一致、config 记录 null 却临时塞入新 map、被绑定文件内容变化或 config 绑定了另一 task 时全部 fail closed。不存在 config 的 legacy task 保持原显式 CLI 兼容；旧任务可用 `scripts/init_v4_run_config.py` 有意识迁移，语义变化必须显式 `--replace`。

该层解决“同一 task 因调用者漏传 language/role map 而得到不同 raw run”的可复现性问题，不修改 Source-to-Mix、Fine、transition、review 或 release threshold。正式 asset artifact 继续记录实际 profile/map SHA，因此 production lineage 仍由真实语义输入身份约束；workers/resume/out-dir/git metadata 属于执行策略，不进入 run config。

### 5.1 Primary coarse terminal coverage

#68 允许结构上有界的 terminal disconnect 保留已证明 prefix：

- 断点前至少三个连续 anchors；
- 只允许 terminal suffix；
- suffix 上限由现有 window/step 结构决定；
- leading/interior disconnect、超限、证据不足仍 hard fail；
- `path_coverage` 记录 selected/excluded coverage；
- excluded suffix 不获得 affine extrapolation timing authority。

Shared-boundary transition activity 使用 retrieval-only purpose，保留完整 windows 但不生成 TimeWarp。该机制不确认 transition/outro/cut/overlap，也不改变 transition review threshold。

Max run 同时区分物理 `mix_duration` 与保守 `content_end`。自动缩短有两类严格证据：其一是尾部至少 30 秒解码样本**精确为数字 0**；其二是 a15 起 `ffprobe` 已独立证明首个音频流更早结束，且该终点之后 SoundFile 只暴露数字 0 帧。普通 fade/近静音/底噪不会被裁掉；若 ffprobe 终点与非零解码内容冲突则 hard fail。`content_end` 只约束最后 occurrence 的 production interval/terminal clamp，完整容器时长继续保留作 provenance。

`4.0.0a13` 起，若任务存在经过 QA 明确证明的 detached export tail（例如主节目结束后出现长数字零区间，再出现短小孤立音频残片），可把 `mix_content_extent` JSON 作为可选 task input 纳入 fingerprint。该 override 必须绑定同一 audio SHA、提供非空 reason，并且**只能缩短**自动 `content_end`，不能延长；未提供该输入的任务保持原自动判定。该机制保留原音频文件与物理时长，不通过复制/截短 mix 绕过 provenance。

`4.0.0a12` 起 coarse/Fine 只解码当前请求的 mix interval 加 2 秒上下文，而不是把长 mix 作为完整工作波形。压缩容器到达物理尾端时，若实际可解码终点与声明终点只差不超过 5ms，可保守 clamp 到真实终点；中段 short-read、更大的尾差以及未覆盖请求区间的 decode 仍 hard fail。该优化不改变 retrieval window、candidate pool、TimeWarp、review 或 release authority。

### 5.2 Projection/content-integrity gate

若 canonical timeline 报告：

```text
projection_coverage.authority_omitted_line_count > 0
```

则 composer 拒绝 render。被 proven coverage 排除的 canonical 内容可以留作 unresolved evidence，但不能被静默丢失后生成“完整 final”。malformed/negative omitted count 同样 fail closed。

### 5.3 Evaluation render != production release

当前 `scripts/v4_render.py` 仍直接 materialize canonical timeline lines，因此它现在被明确定位为 **evaluation renderer**：

```text
publish_ready = false
segmentation_authority = canonical_line_evaluation_only
release_blocked_reason = editor_cue_reconciliation_required
```

`ready_for_render` 只表示 reconstruction/review 已足以生成结构/评估输出，**不等于 publish_ready**。

V4 release validator 必须看到唯一、hash-bound 的 final-render artifact 明确声明 `editor_reconciled`，并且 production authority 必须在三层一致：

```text
final_render.normalized_config.segmentation_authority = editor_reconciled
final_render.evidence.segmentation_authority          = editor_reconciled
final_render.evidence.publish_ready                   = true
exact QA.segmentation_authority                       = editor_reconciled
exact QA.publish_ready                                = true
```

artifact evidence 或 QA 只要仍有非空 `release_blocked_reason`，release 也必须失败。不能出现“config 已 production，但 evidence/QA 仍 evaluation-only”的半升级状态。

没有这组一致 authority 时，production release 必须失败。人工清完 transition/cut/overlap review 也不能自动获得该 authority。

### 5.4 Editor-Cue Reconciliation evaluation bridge

已新增首版 evaluation-only bridge：

```text
lyric_aligner/timeline/editor_cue_reconcile.py
scripts/v4_editor_cue_reconcile.py
```

它只消费 #70 的 `canonical_line_evaluation_only` final-render artifact，并与 task manifest 中 exact source/editor SRT 对照；不重新推导 Max timeline，不修改 editor cue count/number/start/end，也不生成 production SRT。

逐 editor cue 状态：

```text
resolved       -> canonical interval(s) 完整落入唯一 editor cue，且同 cue 内 canonical material 不互相 overlap
still_review   -> canonical 跨 editor boundary、落入多个重叠 editor cue，或同 editor cue 内 canonical material overlap
rebutted       -> schema 保留；首版不自动产生
not_evaluable  -> 没有 canonical temporal evidence
```

输出 stage：

```text
editor_cue_reconciliation_evaluation
segmentation_authority = editor_reconciliation_evaluation_only
production_authority_granted = false
```

`full_topology_candidate=true` 仍**不**等于 production authority。它只表示在当前 evaluation render 下，所有 canonical cue 可以不改变 editor cue topology 地获得唯一 ownership，并且 editor SRT 文件时间顺序单调；保留 editor topology 的 production materializer 仍需独立实现。

私有长混剪验证暴露了另一类可严格证明的情况：editor SRT 可能只是稀疏/错误识别出来的时间子集，存在完整 timed canonical cue 与任何 editor cue 都没有时间交集。此时“不移动/不新增 editor cue”与“canonical lyric 完整性”在逻辑上不能同时成立。`v4_materialize_editor_reconciled.py` 因此只增加一个窄 production path：它必须消费 exact hash-bound canonical evaluation render + `editor_cue_reconciliation_evaluation`；要求 `full_topology_candidate=false`、至少一个 `no_editor_temporal_overlap` canonical witness、reconciliation assigned/unassigned/status 计数闭合，并且最终 audit 每一行都来自显式 timed `line_lrc / enhanced_lrc / qrc_word_timing`。editor file order 正常情况下仍要求单调；若存在相邻逆序，只有所有 inversion 都满足 `right.end_ms <= left.start_ms`、即文件顺序错位但时间区间互不重叠时，evaluation 才标记 `editor_file_order_recoverable_nonoverlap_reordering=true`，rebuttal materializer 才可继续。任一逆序存在时间重叠仍 fail closed；`full_topology_candidate` 仍只允许单调文件顺序。满足这些条件时，reconciliation 结论为全局 `rebutted`，exact canonical SRT/audit 可被提升为 `editor_reconciled` production segmentation；否则继续 fail closed。

该 materializer 不修改 canonical text/timing，也不把普通 `canonical_interval_crosses_editor_boundary` 当成 topology rebuttal 证据。它生成新的 production QA/final-render artifact，并在 `normalized_config`、artifact `evidence` 与 exact QA 三层同时声明 `editor_reconciled` / `publish_ready=true`；原 canonical evaluation artifact 仍保持 evaluation-only。`v4_validate_release.py` 不做例外处理，仍按既有三层 production-authority contract 验证。

### 5.5 Artifact-writer path safety

Max 下一步实际会使用的 review/materializer/render/reconciliation/release CLI 共享 fail-closed 输入所有权 contract：

- task manifest 与所有 manifest-bound files 都是 protected inputs；directory input 保护整棵 subtree；
- review 保护 run/run artifact/decisions；
- cut/overlap/combined materializer 在首次 `mkdir`、Fine 子进程或 write 前保护直接输入及 run payload 递归声明的全部 `*_path` lineage；`--out-dir` 与这些输入必须双向不相交；
- materializer 的公开 `v4_*.py` 是唯一支持的 CLI entrypoint；原算法 blob 以 `_v4_*_impl.txt` internal source resource 保存并由安全 wrapper 加载，不作为第二套 CLI；
- render 额外保护 TrackAssets/asset artifact，以及 run 实际读取的每个 canonical timeline/timeline artifact；四个 render outputs 必须彼此不同；
- reconciliation 保护 canonical evaluation SRT/audit/QA/final-render artifact；
- release manifest 不能覆盖 final SRT/audit/QA、upstream artifact 或任一 task input。

所有 collision 在第一次 materialization 前 fail closed。该机制只保护文件 ownership，不改变 review、cut/overlap、timing、text、segmentation 或 release authority。

Release/reconciliation 对 `review_candidate_count` 要求真正 JSON integer `0`；render eligibility 的 review/cut/overlap/combined count 同样不能靠 Python coercion。完整 CLI 规则见 `references/v4-cli-contract.md`。

### 5.6 Production orchestration output-tree safety

`v4_run.py`、`v4_run_optimized.py` 与 `v4_run_legacy.py` 现在也受同一 output-tree ownership contract 约束，而且检查发生在 orchestration 的第一次写操作之前：

- canonical `v4_run.py` 在 `OutputRunLock` 创建 output directory / `.v4-run.lock` 前检查；
- direct optimized entrypoint 在 `cache/`、verified-input session 或 stage directory 创建前检查；
- direct legacy entrypoint 在 `assets/primary/transitions/timelines` 创建前检查；
- task manifest、全部 manifest-bound input roots/subtrees，以及显式 profile/language/middle-cut/lyric-role config 都是 protected inputs；
- `--out-dir` 不得位于 protected input 内，也不得反向包住 protected input。

Legacy/optimized orchestration implementation blob 保持不变，仅由安全 public wrapper 在 preflight 后进入。该 gate 不改变算法或任何 readiness/release authority；它只阻止 run orchestration 自己污染已经 fingerprint 的输入树。

### 5.7 Production display policy

Production timing/segmentation authority 与 viewer-facing presentation 现在明确分层。`scripts/v4_apply_display_policy.py` 只消费已经 `editor_reconciled`、`publish_ready=true` 的 production final-render；cue count/number/start、occurrence identity、track identity 与 canonical line identity 全部冻结。默认不改 timing；只有显式启用 `trim_extreme_unknown_end_v1` 时，允许对 `next_line_start` 推导出的极端长未知 end 做 shorten-only display trim，绝不延长 end 或移动 start。

Canonical lyric 继续作为文字/顺序 evidence truth，不因平台敏感词处理或模型高置信 typo 修订而被覆盖。显式模型修订必须 task-bound，并精确绑定 `occurrence_id + track_id + canonical_line_index + expected_text`；只有 `confidence=high` 才可 materialize，原文不匹配、override 未命中或命中不唯一均 fail closed。输出 audit 同时保留 canonical/display 两层文字、source/display start/end 与 policy/reviewer/reason provenance。

内置 `strong_profanity_v1` 只自动处理明确强脏词（例如 `fuck/fucking -> f*`）。语境相关词如 `sexy`、`shot`、`bullet`、`kill`、`damn` 不自动改写，必须经模型/人工语境判断。`trim_extreme_unknown_end_v1` 只接受 `source_end_basis=next_line_start`，且 `max_display_hold_ms` 必须严格小于 source-duration trigger；`open_end` 和显式 timing 不可被该规则改写。display stage 生成新的 hash-bound `final_render`，继续保持三层 `editor_reconciled` / `publish_ready=true`，随后仍由原 `v4_validate_release.py` 正常验收；release gate 不增加例外。

`4.0.0a10` 补齐无-overlap confirmed-cut 的正式 reference-retime 路径：`scripts/v4_retime_reference.py` 仍只接受已完全 resolved、`ready_for_render`、非 legacy 的 source run，但 source stage 可为 `review_resolution` 或原有 `overlap_recomposition`。直接从 `review_resolution` 进入时，source review artifact 必须就是 source run artifact 自身；从 overlap 进入时仍验证 overlap metadata 中的 source-review identity。`4.0.0a11` 补齐 renderer 的对应 source-stage 分支：reference-retimed run 不再无条件按 overlap materialization 验证，而是沿已验证的 `source_run_stage` 继续；review 来源无需不存在的 overlap metadata，overlap 来源仍保持原严格校验。retained-segment 的删行/截断/fail-closed 语义不变。

### 5.8 Diagnostic final-candidate audit

新增 `lyric_aligner/qa/final_candidate_audit.py` + `scripts/v4_audit_final.py`，把此前各私有任务重复做的成品结构审计抽成通用只读 QA。它不会生成 production artifact、不会修改 SRT、不会授予任何 authority；只在 SRT/report exact binding 与 publish-ready QA 基础上检查 final file order、cue duration 分布、occurrence-window containment、`content_end`、以及 same/cross-occurrence overlap。跨 occurrence overlap 必须完整位于 run 已物化的 confirmed-overlap region 才允许；长驻留只产生 presentation warning。audit output 同样受 task/direct/run-declared `*_path` 输入保护，不能覆盖实际 timeline 等 lineage input。

### 5.9 Private calibration baseline

2026-09-02 首轮 r1 private benchmark 已用于验证 workflow，但随后确认其 reference/prediction authority 混合了旧人工 segmentation 与已人工闭合 production 结果，因此只保留为 exploratory history，不再用于正式 candidate selection。

正式基线已升级为 `2026-09-02-r2-auto`：仍为 8 个 opaque case（4 calibration / 4 blind_test）、8 个 source_group 严格隔离，但 reference 固定为已验收 pre-display production SRT，prediction 固定为 raw `v4_run` per-occurrence timeline 按 authoritative occurrence window 物化，禁止应用人工 review、overlap recomposition、reference-retime、editor reconciliation 或 display override。该 baseline 已生成独立 lock；blind prediction/QA 仍未 materialize，blind metrics 仍未读取。

r2 calibration aggregate 为 `unit_f1=0.999221`、`line_exact_f1=0.999167`、`cue_text_exact_match_rate=1.0`、`boundary_mae_ms=17.982`、`boundary_p95_ms=6.0`。4/4 calibration case 的 raw Max SRT 基本已与 production truth 重合，但每个 case 仍保留 1 个 review candidate，`publish_ready_rate=0`；该 calibration 中被人工确认的结构事件在 raw Max 阶段仍有 `cut_recall=0` / `overlap_recall=0`。因此当前主要瓶颈已从普通歌词 timing 转为结构事件与 review authority。

首个 transition Fine-anchored 多尺度自动降噪候选已在 calibration 阶段淘汰：2/2 已确认真实 overlap 均被错误建议为 sequential clear；进一步加入 aligned dual-source STFT/NNLS mixture-gain 证据后，clear 与 overlap 的分数仍明显重叠，无法形成安全阈值。该实验未进入 production/public code，也未触碰 blind。后续不得通过继续堆同源 retrieval 阈值来自动 clear transition；新 candidate 应由 r2 calibration 的结构事件 error breakdown 驱动。

第二个 candidate 使用用户工作流中的 prepared stem 做 same-track splice 正诊断。它在真实 calibration 上能自动发现一例约 6 秒 source-offset handoff，并把 `cut_precision/recall` 从 `0/0` 提升到 `1/1`，因此曾以 commit `1dbf82b` 进入 public candidate。随后发现原 r2 blind manifest 的结构标签已经在人工审计时暴露，原 4 个 blind case 被永久降级为 quarantine，不再用于正式 gate；重新锁定的 r3 fresh blind 在 candidate selection 前固定 8 个未见 synthetic structural case，并在 prediction materialize 后首次执行 gate。结果 candidate 在 fresh blind 上 `cut_precision=0`、`cut_recall=0`，未达到预先设定的 precision=1.0 / recall>=0.75 门禁。按 blind 纪律不再针对该结果调阈值，prepared-stem public core/CLI/test 撤回；private A/B 证据保留用于避免重复走同一路线。

P1 结构 benchmark 现在复用上述 strict workflow，并把 case-level 结构真值显式标准化为 `structural_scenarios`。schema `1.1` 支持 `none / hard_cut / same_track_splice / crossfade / true_overlap / sequential_transition / piecewise_rate / reorder / detached_tail`；schema `1.0` 不接受该字段且保持旧 report shape。1.1 显式标签 canonical 排序后进入 ground-truth identity，未标注 1.1 case 只在 report 中归入 `structural:unspecified`，因此不会重写既有锁。strict evaluator 同时输出 `language:*` 与 `structural:*` aggregate scope；这些标签只用于 evaluation/gating，不增加任何生产 timing authority。

冻结 r3 复放继续得到 calibration SHA `737e83697f1e577bbf9c8473e21b54ad304c33d1c6f09404fc45abe10853e330`、blind SHA `2e9c49321ac3541d2d5f3fdb953ddbdecab1f0c09f3ed80a6249aae83bbdc886`；去除新增 structural-only report 字段后，新旧两 split 评估递归全等。

P1 已进一步增加 evaluation-only typed structural-event contract。point event 为 `hard_cut / same_track_splice / sequential_transition`，按 `time_ms` 容差做 maximum-cardinality/minimum-error matching；interval event 为 `crossfade / true_overlap / piecewise_rate / reorder / detached_tail`，按区间 IoU 做 maximum-cardinality/maximum-IoU matching。truth 使用 `expected_structural_events`，prediction 使用 `predicted_structural_events`；typed prediction 若没有预先冻结的 expected list（负例也必须显式为空）立即 fail closed。expected events、point tolerance 和 interval IoU threshold 进入 ground-truth identity，prediction 不进入。strict evaluator 现在可在 overall/language/structural scope 输出 event precision/recall/F1、FP/miss、point MAE 和 interval mean IoU，且不把 event 位置写入公开 evaluation。

冻结 r3 在 typed-event 层再次精确复现同一 calibration/blind SHA；历史 r3 没有 event truth 字段，因此 `structural_event_annotation_case_count=0`、`clean_case_count=0`，不会被误当成新 event-level clean truth。剥离新增 `structural_event_*` 指标后，新旧 r3 评估仍递归全等。

随后按该方法学完成 r4 `reorder / detached_tail / none` 研究闭环。真实 calibration 使用独立 production/QA truth，并把评估文本载体改为 opaque SRT；`reorder` detector 只允许已有 source/occurrence mapping authority 的 editor cue 建立或触发 chronology frontier，unmapped overlay/口播不能单独获得 reorder authority；`detached_tail` detector 只读取长 exact-zero gap 后重新出现的短孤立 active island。5-case calibration 在预先固定 gate 下得到 typed-event precision/recall/F1=`1/1/1`、3/3 negative controls clean、interval IoU=`1.0`，candidate revision 锁为 `11b2443c59aa5a14b8b1c8950a9eaf0c103fc6f48d958711208bc7f3ad5c5183`。

candidate selection 与 blind policy 均在读取 blind metrics 前冻结；随后首次且唯一一次 materialize 12 个 fresh locked blind case（4 reorder / 4 detached-tail / 4 none）并执行 gate。结果 overall typed-event precision/recall/F1=`1/1/1`、interval mean IoU=`0.999696`、`structural:reorder` recall=`1.0`、`structural:detached_tail` recall=`1.0`、`structural:none` clean-case rate=`1.0`，blind gate PASS。该 r4 blind 从首次 observation 起永久禁止用于后续 threshold tuning；private case-level truth/prediction 继续不进入 public repo。该 fresh-blind 结果本身仍只证明 evaluation 泛化，不自动授予 Max timing/review/release authority。

fresh-blind 通过后只提升到新的只读 `scripts/v4_audit_structural.py` QA bridge，而不是直接接入 Max mutation authority。`detached_tail` 可直接对 task-manifest 绑定音频做 diagnostic；`reorder` 必须额外提供 `v4-editor-source-map-1.0`，并要求 `mapping_authority=source_occurrence_verified`、同 task fingerprint、同 editor SRT SHA，以及 repository-relative 的上游 source-mapping artifact path + 现场 SHA 复核；缺 authority 时明确 `not_run_missing_source_mapping_authority`。该 bridge 固定 `diagnostic_only`，并显式禁止 automatic timing/content_end/review resolution、release-gate eligibility 和 publish-ready。真实六项任务回归得到：190 唯一 1 个 reorder、Walk120 唯一 1 个 detached-tail；快乐健走140、KPOP110、KPOP130、KPOP200 均 0 structural event，且无 source map 的任务 reorder 均明确不运行。该结果只建立生产 QA evidence，不授予自动修复 authority。

后续对剩余 `piecewise_rate / hard_cut / true_overlap` 三类做了独立 truth-discovery closeout，结论均是不足以冻结新 truth，而不是继续调 detector。`piecewise_rate` 最强真实候选改用原曲与最终调速 stem 直接比对，完全不读取 Max timeline/Fine/TimeWarp：10/10 预设时间窗可靠，局部 rate 总跨度 `0.0125`，最强相邻两段只差 `0.005`，未达到预先固定的 `0.015` 多-regime truth 门槛。`hard_cut` 最强候选虽有历史人工确认 source omission，但新的 task-bound waveform-only branch audit 无法稳定区分 cut 两侧 source branch，也不能排除约 `0.40s` 的 ambiguous dual-support，因此不把 omission 强行升级为 hard-cut truth。现有真实 `confirmed_overlap` 共 3 处，独立人工 rationale 全部明确为 crossfade/short crossfade，因此继续属于 `crossfade`，不得重标为 non-crossfade `true_overlap`。当前 corpus 因而没有新的 evidence-supported structural detector target；除非获得新的独立制作/编辑 truth 或出现新的真实 production failure，否则优先保持 regression/provenance/release-authority 稳定，不增加结构 heuristic 复杂度。

## 6. Legacy Partial Timeline Repair

旧 P1–P5 bridge 继续固定：

```text
proposal_only = true
publish_ready = false
automatic_timing_change_allowed = false
release_gate_eligible = false
```

Smart/Pro/Max 不借用 legacy P9/P4 flags 获得新的 mutation authority。

## 7. 验证与隐私边界

Public CI 必须继续证明：

- lower-mode segmentation monotonicity；
- Smart text recovery 不提升 timing authority；
- Pro no-write contract；
- Max bounded terminal coverage 只缩小/记录 authority，不扩张；
- omitted canonical lines 不能静默 render；
- canonical-line Max output 不能通过 production release gate；
- production release 的 final-render config/evidence/exact QA authority 必须一致；
- reconciliation evaluator 不移动 editor boundaries、不自动产生 `rebutted`、不授予 production authority；
- Max artifact writers/materializers 不覆盖 task/upstream/lineage inputs，动态 output tree 不得包住输入；
- canonical / optimized / legacy 三个 run entrypoint 在 output tree 与 task/config inputs 相交时必须在首次写入前失败；
- malformed release/evaluation QA types fail closed；
- artifact/task/version/hash lineage 完整；
- Python/ASR environment 与 legacy regressions 不回归。

真实任务失败模式只能转化成 generic synthetic regression；不得把私有歌曲名、歌词、cue 编号、真实时间戳或音频写入 production algorithm/public tests。

## 8. 历史 Smart/Pro 基线 freeze tag（不代表当前 selector）

当前 production selector 为 Smart v1.2.10 / Pro v1.2.7。以下旧 tag 仅作为历史 production baseline，必须保持不动：

```text
prod-smart-v1.2.5-pro-v1.1.4-20260821
56841c40d6a90101efe1da568e2d5c2e5e67a0a2
```

后续 Max 工程不得移动或重写该历史 tag；它不定义当前 Smart/Pro selector。

### 2026-09-03 封板维护结论

本轮封板维护不改变算法阈值、timing/text authority 或 release authority。只补齐 `soundfile` direct dependency 与环境预检，把 TrackAssets、task manifest/QA JSON 与 task-local run config 统一到 shared atomic writer，把 canonical evaluation render 的 SRT/audit CSV 改为中断安全的原子落盘，并订正文档/注释漂移。既有 structural closeout 与消融结论继续有效；除非出现新的真实 production failure 或新的独立 structural truth，不重新打开已经否决的 heuristic 路线。
# 2026-09-08 第五切片进展

FLOAT source decode 已实现并完成整曲、完整 shadow SRT 和隔离全量验证，保持显式实验选项。中文完整上下文 SOFA 历史回归仍劣于 current final，不接生产。新的独立英文 HuBERTFA 上下文实验出现局部源端改善，正在同协议扩大未见歌曲复验；目前不能声明最终字幕准确率提高或封板。执行证据位于 `output/source_context_upgrade5_20260908/`，以唯一交接最新记录为准。

### 2026-09-08 第十切片结论

已完成完整链路归因并修复 editor 区域恢复的跨界检查范围与 canonical 字符坐标。真实 WALK 新 SRT 876 cues（旧882），相同语义边界55 start+55 end变化，816非目标cue保持；声学一致性显著改善，但该区域无人工gold，仍不封板。声学长窗口 oracle 与matched对照不支持作为默认升级。新MUSDB素材仅提供词起点gold；独立新歌36行packet只选3行，严格唯一上下文起点评估92/312词MAE539.966ms，不具备通用自动边界可靠性证明。

最终产物为 `output/source_context_upgrade10_20260908/editor_smart_region_v2/4_70/`。初版editor_smart_region/4_70的字幕文字时间相同，但ownership元数据局部坐标错误，由v2取代；保留原证据，不手改历史artifact。所有本轮验收以delivery_report及最终验证收据为准。

## 2026-09-08 第十一切片：普通多语种顺序消歧

普通多语种source shadow v5已接通现有全最优顺序解析。Gee 84目标中候选6→7，真实新增1个终点；Whiplash 114目标中5条采用声学候选，其中1条来自顺序promotion。移除promotion的同候选真实优化/写出消融确认它仅增加1个终点。不能把end提前150/1085ms叫作误差降低；没有这两点人工gold，仍非封板版本。

原剪映KPOP110/130/200的长谐音cue和空洞已逐文件hash统计，支持按区域多证据判断，反对整份依赖剪映或按语种硬编码可靠度。参见 output/source_context_upgrade11_20260908/research_and_plan.md。

## 2026-09-08 第十二切片：逐段语种检测实测

新增默认关闭的 source_asr.multilingual=true（language=null），原生逐段检测、source-observer-1.2 独立缓存；旧默认和1.0/1.1缓存身份不变。Whiplash同turbo/音频对照：auto 5候选/5采用，整曲en 0/0，逐段自动11/10；新模式相对输入写出8 start、10 end，文字不变。这是覆盖和写出变化，没有独立端点gold，不能声称准确率提升。

Al James独立公开词起点诊断：严格唯一上下文匹配92/312，220保留null；新旧共同92起点全部一致，MAE539.966ms、p95 1543.249ms、max5880ms无变化。旧en与新auto+multilingual同时改变两个控制，不能称单因素；归因附加更正保留原报告和收据。无word-end真值、不是blind，不推广默认、不宣布封板。逐段模式开头误识别未恢复，并丢失auto的第36条候选（end55209退回56294ms）；相对auto共11条cue时间变化，不能称无损收益。三组固定对照完整记录于output/source_context_upgrade12_20260908/。


## 2026-09-09 editor-first batch + hybrid production（当前）

当前默认升级方向已从“发现 editor 风险后整体重建”收敛为**局部 editor timing/topology 保真 + canonical 结构完整性**。`subtitle-upgrade-job-1.0` 的 `editor_preservation.scope=all_occurrences` 会在整份任务内逐 occurrence 重复寻找 `exact canonical stream + unique occurrence + compatible neighbors` 的完整 editor 区域，直到稳定；无可行区域保持原结果。选择不读取人工 gold、不按语言/曲风硬编码可靠度，也不赋予模型 timing authority。crossfade/overlap 导致同 occurrence 在全局 audit 中非连续时，whole-occurrence 模式仍拒绝，但 auto region 可处理内部连续安全区；纯符号/音乐标记等 nonlexical editor cue 不参与文字匹配并作为 retained content 原样保留。

旧 topology-rebuttal 的“出现一个 `no_editor_temporal_overlap` witness 就把整份 canonical evaluation SRT 直接升为 production”已撤销。现在 `v4_materialize_editor_reconciled.py` 只接受 hybrid path：canonical evaluation/reconciliation 先证明 editor topology 确实漏内容，再消费**精确绑定该 evaluation 字节**的 `editor_preservation_batch` 产物；要求至少一次真实 editor restore、`model_timing_authority_used=false`，并按 `canonical_content_start/end` 验证每个 occurrence 的归一化 canonical 字符流 100% 连续覆盖、无 gap/overlap/越界。最终 SRT 复制 preservation 结果而不是 canonical evaluation，因此“补漏句”和“保住可信 editor timing”可以同时成立。

KPOP130 真实长混剪已贯通整条链：canonical evaluation 786 cues，经 7 个 restore stage、5/12 occurrences 的 editor 恢复后为 774 cues（63 个 baseline-region cues -> 51 个 editor cues）；reconciliation 有 28 个 `no_editor_temporal_overlap` witnesses，hybrid production 保持 786/786 canonical identity coverage，并获得 `editor_reconciled / publish_ready=true`。随后同一 display policy 在 774 cues 上正常应用：17 条 viewer text 变化（15 条显式 override + 2 个强脏词 mask）和 6 个 shorten-only display end trim，source hybrid artifact 仍精确绑定。已有 8 条 historical development gold 从旧 FRESH_FINAL MAE 584.9375ms 降至最终 viewer display 501.6875ms；7 条不变，唯一变化的“整个城市播着爱的主打歌”起点误差 1000->40ms、终点 481->109ms。该 8 条已参与开发，**不是 blind 泛化证明**。

跨项目结构恢复也通过真实运行：KPOP110 1158->1155（3/17 occurrences，3 restore stages，8->5 cues）；WALK120 882->866（7/14，10 stages，253->237）；WALK140 936->925（4/16，5 stages，76->65）；H190 672->642（10/12，12 stages，308->278）；KPOP200 826->826（2/14，4 stages，5->5）。连同 KPOP130，6 个任务共 85 occurrences，其中 31 个至少恢复一个严格安全区域，共 41 个 restore stages，713 个 baseline-region cues 被 641 个 immutable editor cues 替换。这里只证明跨项目**安全恢复覆盖与稳定性**，不能把 cue 数或恢复数量换算成总体准确率。

工程兼容同时补齐：reference-retimed run 中的仓库相对 timeline/artifact path 统一以 repository root 解析，避免依赖调用者 cwd；nonlexical cue 不再使 region matcher 全批失败；display 层允许 split/merge 后的多行 canonical ownership 继续执行全局 mask/timing policy，但显式 line-bound override 仍只允许唯一单行 identity，冲突字段 fail closed。产品身份继续保留 `4.0.0a19`，新行为由 `immutable-editor-all-occurrences-batch-1.0`、`immutable-editor-auto-region-1.0`、`hybrid_editor_preservation_after_editor_topology_rebuttal` 及 artifact config/lineage 明确区分，不批量改写历史 a19 artifact。
本次最终封板核对分三层记录：hybrid production/materializer QA 已确认当前 materializer 的 editor-first 结构与 lineage 行为；viewer final structural audit（KPOP130 display v3）`passed=true`，`errors=0`、window violation=0、content-end violation=0，confirmed/unconfirmed overlap 均为0。该 audit 仅有 `long_display_holds` 10 条 warning（duration min 349 / median 1902.5 / p95 4395.3 / max 7563ms，0 条 >=8000ms extreme hold）。semantic/release gate 仍未通过：projection editor witness 2/12 track fail；当前 independent-audio final layer 12/12 fail，`audio_anchor_count=0`。所用 formal fusion SHA `01575459...` 属于 current safety tightening `e22f10d` 之前的旧证据，不含 `canonical_start_covered` / `canonical_match_ambiguous` 等当前 ASR 起点资格字段；当前 gate 的 fail-closed 是预期行为，旧 semantic QA 不得复用为新 final 的 release authority。完整 release-ready 仍需 fresh independent audio evidence/fusion 并通过 semantic gate；`publish_ready=true` 只描述 materializer 层，不等同完整 release-ready。
