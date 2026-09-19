# 2026-09-07 准确率升级：实测方向与实施状态

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


上下文匹配压力验证：八首真实录音按歌曲分为4 calibration/4 untouched holdout，每首clean/local_decoy/time_stretch/repeated_context四场景。v1校准暴露网格相位假margin和局部权重压过上下文，失败保留；v2在首次holdout前冻结代码/脚本/协议，校准和holdout各改善5个>20ms场景、0个>100ms恶化、全部完整重复保持歧义。holdout12个非重复场景max16ms，仅证明构造映射压力测试，不是自然歌曲歌词边界精度、盲测生产校准或authority。正继续现有24个真实调速校准配对验证。方法背景参考 [FMP audio matching](https://www.audiolabs-erlangen.de/resources/MIR/FMP/C7/C7S2_AudioMatching.html)；本项目三段中位数规则为本次实现，并非该文献的已验证结论。

七项目28个真实CLI观察完成，detected language为zh11/ko10/en7，未覆盖日语。按现有0.72支持阈值，新fusion保留5个完整区间和额外3个缺尾句首；3个旧逻辑会误作完整区间的观察已改为仅句首。无首高分误用在这28样本中计0，不伪报减少数。数据只证明本批词汇覆盖处理更可靠，不证明最终SRT精度或未见曲泛化；所有原始/重试选择和输入失败原因保留于general_edge_validation_v1。

目标校正：单曲修复只作案例，整体升级按跨项目识别证据可靠性、最终SRT误差、正确cue被误改率和自动处理覆盖率评价，不能用测试数或拒绝数量冒充准确率。首尾覆盖通用修复同时减少假整句边界与回收有效句首；冻结跨项目selection后调用正式CLI，复用本地模型，无额外人工标注。

实际新成品与既有混音 ASR 对照：9条观察中6条同时有唯一ASR onset和真实canonical起点可比较，6/6改善，中位差3216→421ms；最大残差1016ms。另3条因歧义、缺首或嵌入cue保留未知。记录在 editor_mix_asr_comparison.json。该代理指标不算新盲测或人工真值，不证明声学终点。

本次完成六曲保留尝试：Training Season和舞娘成功，其余WANNABE跨界且跨语种文字失配，Don’t Start Now/Vogue/Super Model存在缺失或重复canonical内容，未强行分配。组合成品在editor_batch_v1/3/final.srt，舞娘40cue的39条editor语义匹配均0ms差；此为基线恢复证明。单次升级实跑editor_upgrade_v1也成功。QA层串用根因：asr_fallback_editor_projection_disagrees和final_disagrees原来共用shared_errors，污染另一层；两个回归修复前失败、后通过。新semantic.layered.qa.json仍12/12失败，未放宽覆盖门槛或顶层AND。

2026-09-07 持续实跑补充：Training Season editor-preserved v2 已生成 49 cues，保持原始 source_srt 时间，修正 deep→deeper 并恢复 canonical 字间空格。49/49 editor witness 时间一致只证明恢复成功，不证明声学精确率。39 个可证明 canonical onset 有索引，10 个嵌入 onset 留空。独立复核通过全部输入输出 hash、artifact 身份和字符归属。整包 semantic QA 仍不通过；既有 projection/final 错误耦合另列限制，不能将12失败曲直接解释为新final真实错误数。

其他五首 source-clock 诊断：舞娘、WANNABE、Vogue、Super Model 均不足4条高词汇支持的观察，未拟合；Don’t Start Now 的4条只能提供局部试验拟合，不能外推全曲或授予 timing authority。下一步实际按曲保留编辑器与canonical校正；无新增人工标注。

冻结候选的后续结果：九条额外观察（含尾部 43/46/50），七条可作唯一 onset 代理，中位差 3796→284ms，最大 5765→853ms，七条全部改善；canonical42 重复短句已标歧义，canonical50 匹配不足，两个未知没有消失。editor 43 个可匹配起点的中位差 3899→181ms、严重偏差 33→0。实际候选没有重新拟合。完整检查 1362 tests 通过；模型代理与 editor witness 均不等于人工精度，因此不提升为已封板 final。

已从取证进入真实候选：`lyric_clock_candidate.srt` 改变 Training Season 51 cue，其他歌曲保持原样，无新增相邻重叠。源音频六处三锚波形映射与现有映射差 1.5–3.5ms，LRC 对 source ASR 偏差却为 1.1–5.54s，故校正的是歌词时钟而非音频配准。候选 5 个 source 观察拟合后，对先前 4 个 mix 观察的起点差中位数 4010.5→81.5ms；source/mix Whisper 相关，不是独立真人精度。额外 6 行在候选冻结后观察，发现重复短句的首次匹配错误，已修复唯一边界误报；尾部验证仍继续。该候选尚有 19 个外推边界，不作为已封板 final。

本次继续执行进展：修复重复句 QA 起点复用，六项目共 283 次复用降为 0（仅检查器改善）；增加正式可选本地 Qwen ASR evidence 入口，尚非自动救援。WALK120 的 12 个预选窗口中，“Now is your time” 两个 ASR 起点约 379.3/379.5 秒，当前 Max 为 374.195 秒，提示实际数秒级问题；模型共识不是独立人工真值。Smart 文本候选保留 editor 时间，目标短语横跨/嵌入 cue，严格完整短语配对计数为 0，不能宣称配对 MAE 改善。证据位于 `output/accuracy_upgrade_20260907/walk120_rescue_v1/`；source/mix 对照继续定位偏差来源。Qwen observed-text forced alignment 存在零时长词与部分覆盖，已保留未知端点，不作插值补齐或伪造高置信概率。

状态：诊断、候选实测、旧标注复用、联合 geometry、单次升级入口及 7 项目 SRT 回归已实施。按用户后续要求，继续利用旧 24 个片段开发，新增 60 个片段不再作为继续开发的前提；尚未满足跨项目准确率封板条件。

## 第四份核对与自主结构修复（最新）

最新第四份核对原字节保存到 `private/gap_boundary_review_ab_20260907_v6/human-gap-review.received.json`；5/5 confirmed。三条 Na 均覆盖几乎同一完整演唱段，不能解释为三个独立句子的精确边界。按原入口安全写入 2 start + 2 end，27 个边界通过 QA、4 条原子 interval 仍未验证；产物为 `output/accuracy_upgrade_20260907/human_gap_ab_v4/h180/`。后续自主开发，不把常规试听作为继续条件。

新增 `reviewed-vocalization-display-group-1.0`：对已有核对覆盖连续显示间隔的相邻、同曲、连续 canonical 非词汇重复行进行无损合并。最多四行，保留所有文字和原两端，不均分音素，不使用标注平均值。填充间隔不得覆盖其他同时字幕；未核对间隔不自动填充。

实际 H180 显示 SRT 为 `output/accuracy_upgrade_20260907/human_gap_ab_v4/display/display.srt`，781→777 cues，消除四个内部显示切分。Ah 保留 20 个字符，Na 保留 40 个词并按四行呈现；全文件非空白字符序列逐字相同，邻近正常歌词未被吞并。配对证据为同级 `display_comparison.json`。

新 materializer 消费完整重放的 gap receipt 和 exact CSV/SRT，记录成员行哈希、canonical 索引、两端来源位置及上游输入；验证器重推分组并完整匹配输出 SRT。原声学报告不改写，显示派生产物仍为 publish_ready=false，不绕过整包语义/声学验收。独立复核对伪造资格、声学声明、输入、成员和任务身份均拒绝。

针对之前的快速演唱识别失败，另对同一组音频窗口执行独立 Qwen3-ASR 对照，不向模型提供 canonical 歌词。该转写模型与此前测试的 Qwen3-ForcedAligner 不同；官方性能不能直接移作本项目结论。[官方实现与评测](https://github.com/QwenLM/Qwen3-ASR)。

本次已完成原速 Qwen3-ASR-0.6B/1.7B 的同窗实跑，以及 0.75 倍保音高的 Qwen0.6B/Whisper 对照。两种降速方案均不采用，出现无关转写；Qwen1.7B 不能整体替换 Whisper，但在部分原失败窗口有互补。相同字符子串诊断中，一条目标从 0.444→0.889，两条重复目标从 0.588→0.778；这些数值不是词时间置信度，不授予边界或 release authority。Qwen 的六条均未包含完整规范目标，首个含口播的长窗还遗漏了后续歌词。原始输出、原音频窗哈希、脚本及配对结果保留在 `output/accuracy_upgrade_20260907/qwen_asr_probe_v1/`。

后续自动开发优先验证“原速 Qwen1.7B 补充 Whisper 失败窗口”的局部路径：对实际转写文本取得独立时间证据，显式保留 backend 身份及缺失的置信度，不能伪装成 faster-whisper 或直接把字符匹配分当作音频概率。暂不扩大新模型下载，复用已有权重；不再重复本轮已排除的降速试验。当前没有新的模型 timing authority，也未改生产模型。

本轮隔离验证 **1340 tests OK / 190.306s**，主工作区与隔离验证代码 SHA 一致；compileall、validate_skill、privacy_scan、实际 worktree 文档契约及 diff 检查通过。显示派生产物完整重放与无损字符检查、独立安全复核通过。证据为 `output/accuracy_upgrade_20260907/human_gap_ab_v4/verification.json`。用户随后要求关闭定时续跑，现有每小时 heartbeat 已暂停，改为当前任务直接执行。

随后完成三条独立转写的时间定位：两条取得有界连续词段，另一条因零时长词无法取得连续匹配。第二条高分只覆盖句首，不能作为完整句尾证据；不据此替换生产模型。原始结果见 `qwen_asr_probe_v1/observed_alignment.json`。单次升级入口现对 gap review 自动附带生成 `display/display.srt` 及可重放 receipt，摘要单独记录显示版本；声学 final/QA 与显示结果分别保留真实适用范围。

直接执行批次 `human_gap_ab_v5/h180/` 已完成，显示 777 cues 与 v4 派生版字节一致，声学版与 v4 也字节一致；27 个确认边界通过、4 条原子 interval 未验证。最新完整回归 **1340 tests OK / 171.115s**，32 个相关代码文件在主工作区与隔离验证目录的 SHA 全部一致；另 28 项相关测试、迁移后显示重放、环境、编译、隐私与文档契约检查通过。核验记录 `output/accuracy_upgrade_20260907/human_gap_ab_v5/verification.json`。这些验证证明自动串联没有改变已验证结果，不证明未知歌曲的边界准确率，也不解除现存跨项目语义/声学验收缺口。

## 第三份核对、试听输入修复与自动配准实验（历史）

最新 `human-gap-review-ab.json` 已按原字节归档于 `private/gap_boundary_review_ab_20260907_v4/human-gap-review.received.json`。其中 2/5 confirmed，三条 Na 为未确认；按最新显式状态处理，不沿用第二份的确认来掩盖当前不确定性。新产物 `output/accuracy_upgrade_20260907/human_gap_ab_v3/h180/` 写入 1 start + 2 end，781 cues、0 text change；QA 验证 26 个边界、4 条 interval 仍未验证，publish_ready=false。整段连续啊的起点跨越未确认邻句，未强行写入或合并歌词事件。

最新版可选试听页为 `private/gap_boundary_review_ab_20260907_v6/index.html`。任意选点支持单独听到此点、从此点开始、点前后 A/B；步长可直接连续输入（0.001–10 秒）或选 0.01/0.05/0.1/0.5/1 秒预设，并有前进一步/后退一步。五段均按前后各 30 秒从 final mix 扩展，遇原音频首尾裁止；前四段约 63–65 秒，末段受成品结尾限制为 40.270 秒；单侧试听长度可选至 60 秒。保留原编辑值及旧导出，扩大音频不冒充新增确认。此页不是继续算法开发的前置条件。

输入根因有运行过的红/绿证据：旧 DOM 在键入 `1` 后重写为 `1.000`，打断多位数字和小数输入；现只格式化拖动/步进值，不重写键入文本。独立复核另复现 44.1 kHz 非整数毫秒 clip origin 与三位显示值不等导致未编辑内容无法导出；修复后显示精度与内部精确值兼容，保留原绝对整数毫秒。DOM 事件、播放控制器及模板测试通过，不等同真实浏览器音频实播验收。

自动算法工作新增 `local-waveform-registration-1.0`：对近等速 source/mix 的三个局部片段做归一化相关，检查峰值唯一性及坐标一致性。记录真实支持区间，不把未采样间隙称作无剪辑区域。它不依赖歌词语言，也不定义连续 Na 的行内分界。

原型接入复核实证：直接用波形坐标替换 feature points 会让旧 blocked mapping 变共线而意外放行，同时把旧 Chroma/MFCC 分数挂到新坐标。已撤回这种替换；`refine_coarse_mapping(..., waveform_refinement=True)` 仅输出独立 `waveform_candidates`，原 path/timewarp/verdict 完全保持。默认关闭实验，未授予新 SRT timing authority。该修正有先失败后通过的回归。

三首真实 K-pop 音频的已知 crop/gain/DC 变换共 9 个点，波形候选接受 7 点，另两点保留旧值；9 点候选 MAE 为 2.667ms，旧特征定位 MAE 为 9.444ms，最大误差均为 13ms。这是开发用已知变换，不是独立盲测；比较见 `output/accuracy_upgrade_20260907/waveform_probe_v2/known_transform_results.json`。真实混音另测 9 窗，6 窗产生候选，3 窗回退；真实混音没有这些点的独立人工真值，不能把相关性和候选位移当作成品精度改善。实验输出位于同目录 `real_fine_results.json`；v1 是发现接入缺陷前的历史原型证据，不能用于发布。

旧 24 段足以继续做既有录音回归，不要求先新标 60 段。已知音频变换可自动验证映射算法；跨语言的词识别、重复 occurrence、演唱起止及最终 SRT 误差仍需各自证据。本轮没有把局部实验或已知确认应用包装成跨项目精度封板。

本次最终验证：隔离工作区 **1332 tests OK / 195.530s**（Python 3.14.6），与主工作区全部本轮代码 SHA 一致；compileall、validate_skill、privacy_scan、包含未跟踪文件的实际 worktree 文档契约及 diff 检查通过。UI 与波形接入独立复核 PASS。验收摘要为 `output/accuracy_upgrade_20260907/human_gap_ab_v3/verification.json`；尚未实播验收浏览器音频，未提交、推送或发布。

## 用户核对导入与 A/B 试听（历史第二份导出）

用户先后提供两份 human-gap-review 导出，原字节均归档在 `private/gap_boundary_review_20260907/`；以第二份 5/5 confirmed 为准，保留备注及未能落实的边界。新增 gap 导入器验证相同音频、锁定 clip、baseline report/SRT、歌词身份与原确认 receipt；逐边界应用，不修改未确认邻句。

当前成品为 `output/accuracy_upgrade_20260907/human_gap_v2/h180/final.srt`。本次写回 4 start + 4 end；相对 fresh 原成品累计 7 start + 6 end，781 cues、0 text change。完整确认链重放得到 31 个有效边界，QA 待核从 5 降到 2：gap0 起点会与前句新增 609ms 重叠，gap3 终点会与后句新增 223ms 重叠。不能仅凭重复的啊/Na 文字合并不同 LRC 事件或移动邻句。保持 publish_ready=false。

新增 A/B 核对页为 `private/gap_boundary_review_ab_20260907_v4/index.html`。默认各听 1.5 秒，中间停顿 0.4 秒，可调整；支持单点前后试听及本句结尾到下一句起点试听。拖动进度条/波形同步所选起点或终点，显示片段秒数与整曲绝对时间；正常播放不修改边界。相邻句使用同页实时编辑值，任何修改撤销该段确认，空白/不合法数字不能带着隐藏旧值导出。

gap0/1/3 扩为约 19–21 秒上下文：两处结构冲突加上用户备注的结束点不准确问题；gap2/4 保持同一原 clip，保留已有确认，无需重复试听。新页面导出绑定新 lock，旧页面、导出和生产产物均保留。页面 payload 与全部五个音频 SHA 已核对；A/B 时序、停顿、取消、失败、邻句联动、空字段和模板文本注入均有自动回归。浏览器音频交互仍未实播验收，沿用已披露的本地浏览器访问限制，不将控制器测试冒充浏览器验收。

复核修正了三项可复现问题：gap receipt 顶层身份必须与重放 manifest 一致；完整依赖清单必须与重放发现的路径一致，QA 输出保护不能依赖自报的可删字段；模板采用一次替换，用户备注中的占位符字面量不会被解释为脚本。架构及安全复核通过。

本轮最终隔离验证：**1323 tests OK / 147.037s**（Python 3.14.6）；Node 控制器测试包含在全套测试中。compileall、validate_skill、privacy_scan、实际 worktree 文档契约和 diff 检查通过；日志及输入/产物摘要记录在 `output/accuracy_upgrade_20260907/human_gap_v2/verification.json`。精度封板仍未通过，且未进行浏览器实播验收或公开发布。

## 持续复核后的当前结论（优先于下方早期阶段记录）

最新 H180 集成产物位于 `output/accuracy_upgrade_20260907/integrated_v5/h180/`。receipt 1.1 / policy 1.2 将旧确认值与实际 QA 接通：23 个边界具备精确重放证据，3 start + 2 end 写回，原 5 条 gap 仍未确认。没有将这些局部确认扩成整行 authority。QA 输出路径和 release artifact 随 staging 提升到最终目录，配置 QA 时父级如实使用实际验收结果；没有 QA 时不声明 ready。

独立复核发现并修正：嵌套区间不能只检查相邻 cue；评估必须绑定原任务；确认 receipt 必须重放原始输入并严格对照最终 SRT/CSV；QA 输出不得覆盖确认链输入。联合 geometry 改为 active interval frontier，受限状态空间超限时拒绝整次修改。

另一次真实 ASR 证据审计发现 H190 有 2 个倒置匹配、41 个越界匹配、129 处 segment 起点倒序；其他五项目各有 3–6 个越界匹配。根因是重叠 clip 解码会回到更早时间，旧 executor 又把这些重复 segment 分派到所有相交 job。已改为不重叠批次及批次内分派，并使两种文本支持共用连续有效词边界。旧源码的倒序与重叠批次回归先运行失败，修复后通过；新增窗口、invalid/round-zero 词和文本分数绕行回归。原始证据审计及真实模型对照在 `output/accuracy_upgrade_20260907/asr_window_repair_v1/`。

SOFA 与 HuBERTFA 已对 H180 五条 gap 做真实局部取证。SOFA 的十个 gap 边界中九个不可用；HuBERTFA 出现 edge clamp 及单项 `No duplicate groups`，逐项隔离仍不能提供完整可靠区间。因此这些缺口不能通过修改状态字段自动解决。五处离线试听资料位于 `private/gap_boundary_review_20260907/`，未填入人工确认；JS 语法检查通过，浏览器交互未验收：本地 HTTP 服务启动被自动审批拒绝，工具仅返回 `blocked by policy`，离线浏览器工具也禁止 file 协议。未绕过限制。

其他六项目的现有 semantic QA 阻断为：KPOP110 16/17 曲、Walk120 13/14、KPOP130 6/12、Walk140 11/16、H190 2/12、KPOP200 14/14。每曲六候选不等于六个有效锚点；H190 扩到 175 个任务后仍有两曲不足。没有证据把采样预算直接定义成代码缺陷，也不能放宽比例要求冒充产品改善。H190 六个问题候选的新 turbo 实跑已消除倒置/越界，但两个快速演唱目标仍发生重复字幻觉，不能把修复算成这两句的识别成功。

这些事实意味着：旧 24 段可用于回归、同音频确认值复用，不能证明六个其他项目或新歌曲的准确率。新 60 段仍不是继续开发前提；只有要宣称未见素材的精度时才需要独立真值。五条 gap 的实际演唱存在性和边界需要真人确认或新的可靠独立音频证据。未提交、推送或发布，未改变旧产物。

最终候选对照：本机缓存的 faster-whisper medium 对同六条执行完毕，现有 0.72 匹配门槛下 turbo 为 1/6、medium 为 0/6；没有选择 medium 替换生产模型。按最终代码重读两份原始词观察，全部候选满足正时长及窗口约束；该读回是诊断记录，没有重写模型 authority artifact。

最终验证：从 HEAD `311bc7f` 创建 detached worktree，仅复制本轮拥有的 23 个源码/文档路径；Python 3.14.6 全套 **1314 tests OK / 130.431s**，日志 `本地 cache 中的 lyric-seal-verify-20260907-asr-final-tests.log`。本地主工作区的 compileall、validate_skill、privacy_scan、check_environment（含 ASR）、实际未提交路径文档契约及 git diff --check 全部通过。ASR 修复独立复核 PASS；此前 geometry、receipt 与 runner 的架构/安全/组合复核已完成，剩余未覆盖项是其他 CI Python 版本、浏览器交互以及真实未见素材准确率。测试不能替代后者。最终汇总证据为 `output/accuracy_upgrade_20260907/final_verification_v2.json`。

## 后续开发：零新增人工的确认值复用

`v4_reuse_human_boundaries.py` 已将已确认的 outer gold 自动应用到 exact H180 final mix 上。该路径严格匹配已验证的 task manifest、音频/source SRT、selection lock、旧 report SHA、歌词目标及当前 SRT/report，不把模型预测标成真人结果。

结果：781 cues，文字、内部切分均保持不变；3 个 start、2 个 end 实际写回。对旧 12 个 start / 12 个 end 标注，原始 MAE 分别从 59.167→30.0ms、91.167→9.167ms；end 最大误差从 695→50ms。此为已知人工确认的成品落实，不是未见歌曲上的模型效果提升。

外边界可在原 cue 被内部拆分后继续复用，只作用于第一/最后子 cue。起点与终点分别保存 exact record id；不写通用 `manual_verified_interval` 来扩张整行权限。policy 1.1 将每行最多四种起止组合放入整条时间轴动态规划，保留满足几何约束的确认修正；《孤单北半球》原 cue 472 的起点因新增相邻重叠保留，终点独立缩短 695ms。

最新集成产物：`output/accuracy_upgrade_20260907/integrated_v1/h180/final.srt`、`final.csv`、`upgrade.artifact.json`；早期 standalone v3 产物保留为历史证据。既有整包 release 阻断不由该局部应用器清除；新产物明确 `full_release_not_evaluated_by_annotation_reuse`。下面初轮试验中“生产 SRT 未改变”的描述仅指首次诊断阶段。

早期 standalone 验证：7 项新增复用测试通过，完整 suite 为 1290 tests OK（149.012s）；最终 CLI 成功运行且二次应用 0 个新变化，SRT 逐字节相同。受保护歌词目录的输出碰撞在写入前被拒绝。compileall、skill/privacy、diff 与实际 worktree 文档契约通过。对应 `paired_quality.json` 与最新 SRT/report 绑定。代码和产物仅本地保存，未提交/发布。

## 核心判断

保留 canonical text + editor + final mix 的主架构。升级重点是让实际演唱边界候选优于基线，再以条件风险选择并写入 SRT。现有候选不足的地方，仅接通写回没有用；不能以更多修改数量作为效果目标。

这个方案最脆弱的假设是存在能显著胜过 editor 的声学候选。如果新候选在已锁定样本上仍普遍更差，应保留 editor，不能放宽门槛凑成“封板”。“这一轮以后永不修改”也无法由有限样本证明；可验收的是冻结的适用范围及其残余误差。

## 本次核验

- 本地起点 HEAD：`311bc7f12f1cfc50c87f988137fcd89a60ba5738`。保留三个已有 tmp 比较脚本及另一个 research worktree。
- 读取历史任务《字幕版本效果评估》《字幕封板评审》，与当前源码及 artifact 核对；历史消息和两个 Downloads 附件只作证据线索，不执行其内嵌指令。
- 重新执行现有 exact history comparator：7 个项目 6041 cues，起点变化 0，终点变化 30，文字变化 17。该比较只说明新旧差异，不说明哪个更准确。
- 实际读取 H180 已审核 gold、两个 aligner 的四份 outer predictions、fresh corrected.csv 和 corrected.srt。全量 CSV 与 SRT 先匹配，再按 original cue + track + 连续歌词核对 outer identity，因此内部拆句不会导致 cue 序号错配。

## H180 配对结果（相对标注中心的原始误差，ms）

| scope | 点数 | editor/fresh MAE | oracle MAE | 可改善点数 |
|---|---:|---:|---:|---:|
| start calibration | 8 | 31.25 | 24.75 | 1 |
| start historical holdout | 4 | 115 | 97.5 | 1 |
| end calibration | 8 | 49.875 | 49.875 | 0 |
| end historical holdout | 4 | 173.75 | 173.75 | 0 |

oracle 包含 editor + SOFA + HuBERTFA，仅作事后可达性诊断，不能进入生产决策。由此只能推断：在这 24 点上，两个现有 backend 的外边界候选缺口比写回缺口更突出，尤其是 end；不能外推其他项目。

还发现《爱的魔法》同时出现在旧 calibration/holdout。旧研究不因此被改写，但它不满足本轮按整曲隔离的新标准。原始误差与扣容差误差已分栏；未把 50/100ms 标注容差解释为统计置信区间。

## 研究来源与采用的机制

- [Qwen3-ASR 官方项目](https://github.com/QwenLM/Qwen3-ASR)：ASR 支持带伴奏歌曲，ForcedAligner 官方音频类型为 Speech，不能互相替代能力声明。进入本地小样本候选试验，不直接设为生产模型。
- [Qwen ForcedAligner 实现](https://github.com/QwenLM/Qwen3-ASR/blob/main/qwen_asr/inference/qwen3_forced_aligner.py)：给定文本直接预测 token 时间，包含非单调时间修整。试验保留原始时间输出和是否修整，避免将插值时间冒充直接边界证据。
- [WhisperX alignment 实现](https://github.com/m-bain/whisperX/blob/main/whisperx/alignment.py)：局部窗口的 token/CTC 对齐、词表覆盖与失败保留原片段。借鉴局部取证和显式 unavailable；不为同一缺口再引入完整通用转写主干。
- [HuBERTFA 推理实现](https://github.com/wolfgitpr/HubertFA/blob/main/infer.py)：复核现有对齐家族的实现路径；两个 backend 的一致并不等于两个独立概率证明，继续按联合误差实测。

新增本地模型使用隔离 Python 3.12 环境，不修改生产依赖。Qwen 权重锁定 revision `c7cbfc2048c462b0d63a45797104fc9db3ad62b7`，模型 SHA `47831d0e82f96b20e9034dba01a075ee06436654719f6a68289e49f1b65ce0e7`；从官方镜像获取时仍要求与该 SHA 一致。试验不向外部服务上传素材。

### Qwen 真实试验结果

已在 CPU float32 上完成两种历史回归试验，每种 12 个 clip。推理脚本只读取锁定 selection 与对应文本/音频，不读取 gold；评估器随后单独读取 gold。

| 方案 | 可用 start | start 胜过 editor | 可用 end | end 胜过 editor | 新增 >500ms 错误 |
|---|---:|---:|---:|---:|---:|
| 单句 ±1500ms 窗口 | 1/12 | 0 | 5/12 | 1 | 1 |
| 同曲相邻句上下文 | 4/12 | 0 | 4/12 | 1 | 0 |

单句试验在推理前固定规则：非单调修整或距离窗口边缘 ≤80ms 的结果不作为可用候选。原始结果中 11/12 clip 的首 token 起点为 0；上下文试验用于诊断这个失败机制，目标 token 被修整时仍拒绝。两种方案是不同候选生成实验，全部属于已揭晓历史数据，不能用于宣布新的 holdout authority。

上下文虽增加可用起点，4 个仍全部比 editor 差；可用终点 3/4 更差。小样本不能证明 Qwen 在所有歌曲均无价值，但已足够否定“直接替换生产模型”的方案。记录分别在 `qwen_historical_probe_v2/`、`qwen_context_probe_v1/` 与 `h180_with_qwen.json`、`h180_qwen_context_quality.json`，均位于 `output/accuracy_upgrade_20260907/`。推理耗时约 20.5s/28.0s，不包含环境、下载及模型加载。

## 本轮已实施

1. 产品配对诊断：candidate 可达性、selection regret、实际 materialization gap；普通 MAE/尾部、误改率、track 等权 bootstrap、旧 partition 重叠提示。
2. 严格 SRT/report 读回；没有 selector 输入时 selection/writeback 保持 unknown，不造出一个“零损失”结果。
3. 新 KPOP130 验收包：12 个原始录音 group，60 个真实 final-mix clip，120 个空白起止标注。整曲分割为 8/4，抽样没有消费模型预测。它是按当前成品 cue 抽样的外边界基准，不能评估不存在于成品的漏句，也不是所有语言和剪辑风险的完整总体。

本地证据：`output/accuracy_upgrade_20260907/h180_product_baseline.json`；新问题包：`private/_calibration/outer_upgrade_kpop130_20260907_v1/`。private/output 不进入公开源码分发。

## 达到封板仍须完成

- 独立真人确认新的标注；AI 生成时间不替代 human gold。基准建设的人工与日常生产人工队列不同，不等于要求每份字幕重新逐句人工修。
- 新候选在 calibration 的配对增益、尾部和缺失率；冻结 selector 后一次性使用保留曲目验收，不能反复调同一 holdout。
- 只有证据成立后才接 candidate-specific AUTO/RESCUE 到已有 materializer，并验证最终 SRT 的真实收益。未获证据的边界保持基线，结构歧义独立处理。
- KPOP110 手工变速/重复、H180 internal 协作、Walk140 正常点保护，以及漏句/多句/occurrence 的单独回归；当前 60 个 clip 不替代这些验收。

生产 `4.0.0a19` 和既有 authority 保持原身份。本报告不将诊断工具、测试通过或候选模型下载称为准确率提升。

## 本轮验证与交付边界

- Python 3.14.6（CI 矩阵之一）及仓库现有依赖：改动前 1269 tests OK；改动后 `python -m unittest discover -s scripts -p "test_*.py"` 为 1283 tests OK，耗时 146.635s。
- `compileall`、`validate_skill.py`、`privacy_scan.py`、`git diff --check` 通过。
- 对实际未提交路径调用 `validate_changed_paths`，文档契约无问题；没有拿仅比较提交的检查证明 worktree 修改。
- 两个 CLI 的实际 help、输入/输出路径冲突拒绝、输入哈希未变已核验。最终代码重放 H180 基线与初次 JSON 对象完全一致。
- 60 个新 clip 全部存在、非空、SHA 一致；120 个人工边界仍为空，没有自动填入模型答案。
- 仅本地修改，未提交、推送或发布。保留原有三份未跟踪 tmp 脚本。生产 SRT 未被本轮试验改写。
- 未覆盖：其他 CI Python 版本全套测试、新的跨项目真人 gold、生产 AUTO/RESCUE 写回效果、全项目漏句与结构真值。它们不由上述工程测试替代。

## 集成交付与可宣称范围

用户要求一次性继续完成、尽量不人工介入后，补齐 `scripts/v4_upgrade_subtitles.py`。它把既有、已经获得精确授权的 calibrated stages、旧标注应用、最终 SRT 读回与质量报告串联。policy 1.1 使用整条时间轴动态规划，自动解决相邻修正的联动，不以 gold 文件顺序决定可写回结果。

真实批次见 `output/accuracy_upgrade_20260907/integrated_v1/summary.json`，每个子目录含 final SRT、CSV 和带输入 hash 的结果。7 项目总计 6041 cues；H180 781 cues 中 3 start、2 end 被修改，文字 0 改动；其余 6 份 SRT 字节一致。H180 原 12 个起点 raw MAE 59.17→30.00 ms，12 个终点 91.17→9.17 ms。这是相同录音旧人工确认值的自动应用效果，不能外推为未知歌曲准确率。

`full_chain_v1` 另验证现有 internal calibration→human reuse→quality，646→781 cues；它是串联验证产物，未包含 fresh final 后续 textfix，不代替 `integrated_v1/h180/final.srt`。`replay_integrated_v1` 从升级后的 H180 再执行，SRT 字节相同，起止新增变化均为 0。

本轮不要求追加 60 个片段。Qwen 独立 CPU 实测未稳定优于 editor，SOFA/HuBERT 旧 gold 候选的可改善覆盖也很低，因此没有把它们强行接成默认外边界修正。下一阶段交接中的泛化 B/C Expected-Loss、未见曲目精度证明以及整包 release，仍不能宣称完成；这不是缺少再开一个开关，而是缺少已证明更优的候选和独立泛化证据。新 runner 不重写该事实。旧 5 条未授权 gap interval 的发布问题仍保留，不能通过复用局部标注解除。

最终集成验证：Python 3.14.6 完整 unittest **1299 tests OK / 170.536s**；compileall、validate_skill、privacy_scan、check_environment、实际 worktree 文档契约及 git diff --check 全部通过。Python 3.10/3.12 CI 矩阵未在本地重跑。证据文件 `output/accuracy_upgrade_20260907/integrated_verification.json`。
