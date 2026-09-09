# 字幕项目下一阶段唯一交接：Max Expected-Loss Production Upgrade

> **2026-09-09 冻结后的产品下限决策：** `676b37f` 的 editor-first / hybrid timing 架构先冻结，不以继续增加 timing 功能为下一优先级。下一轮 P0 先建立“歌词内容保底层”：在 trusted canonical 与实际 occurrence/内容身份已经成立时，优先把 editor 的错字、漏字、多字、谐音、乱码、跨语种误识别恢复为正确 canonical 内容；若只是文字修复，必须保持 cue 数量/编号及所有 start/end 完全不变。整体优先级固定为 **Content correctness > Structure/ownership correctness > Timing non-regression > Timing improvement**。所谓内容“完全正确”只对已确认实际存在于 final mix 的 trusted canonical 内容成立；canonical 选择、occurrence、cut/repeat/overlap 本身仍有歧义时必须显式保留未知，不能用机械字符覆盖制造确定性。完成这一保底层及其验收后，再进入 untouched/blind timing decision validation 与 expected-loss selector。

> **大模型使用原则：** 下一轮不能把 lexical/structural resolver 做成纯规则系统。LLM/其它 foundation model 应在语义歧义、严重 ASR 乱码、谐音/音译/romanization、混语种、复杂 `1↔N/N↔1/N↔N` ownership、repeat/ad-lib/occurrence/cut/crossfade 候选解释与高价值复核中提供候选和裁决信息；程序继续负责字符覆盖、单调性、occurrence/window、overlap、timestamp immutability、hash/lineage 与 release gate 等可证明约束。原则是 **模型提出/比较假设，程序验证并物化**。文本大模型不得凭自身语义判断直接移动 timing；具备音频能力的 foundation model 也只能先成为有明确 model/runtime/prompt/policy identity 的新 evidence family，经过冻结评测/校准后才能获得自动 timing authority。不得把模型自己生成的文字映射再当成独立证据给自己授时。
>
> **2026-09-09 lexical floor 当前实现：** Standard/raw-LRC 层已能在 timeline 完全冻结下修字并报告严格 `lexical_floor`；Max production 新增 resolved-canonical character audit，KPOP130 pre-display hybrid 对 786 条 resolved canonical / 12,539 normalized characters 达到 12,539/12,539 覆盖，lexical mismatch/gap/overlap/unowned cue 均为 0。这个结论只相对已经解析出的 canonical evaluation 成立，**不证明原歌词文件/版本/canonical wording 本身绝对正确**。因此又新增 `canonical-semantic-rebuttal` shadow 层：规范化字词真正改变才算 lexical rebuttal；纯空格/标点仍属 presentation-only；自动授权必须 high-confidence、至少两个独立 `supports_corrected_text` evidence family，且不能同时存在直接 `supports_canonical_text` 反证。授权后仍先生成 `publish_ready=false`、timing immutable 的 shadow，不倒灌 timing authority。
>
> **2026-09-09 timing validation 当前实现：** 已新增 pre-gold `timing-decision-pack`、candidate-blind audio review manifest/UI、hash-bound review response→human gold ingestion 与 decision evaluator。KPOP130 只作为 development-visible wiring：758 个共享唯一 canonical identity 中按 `>=100ms` 变化冻结 60 个 changed boundary + 20 个 deterministic unchanged controls，共 80 case，selection lock=`f2ea01a158abffe420c4617a265273cae5023cdabdce0a28ed7bfde780a01818`，生成时 `gold_read=false`；它**不是** blind/untouched 精度证据。真实下一批新项目须先锁 pack/clip/hash，再由候选隐藏界面标 gold；人工可显式标 invalid/unscorable，不能被迫猜边界或从分母中静默删除。

> **2026-09-09 当前封板候选：** 本轮已完成 editor-first all-occurrences batch 与 hybrid topology-rebuttal production 链，并在 KPOP130 真实长混剪贯通到 viewer display；历史 development 8 条 MAE 584.9375->501.6875ms。KPOP110/WALK120/WALK140/H190/KPOP200 的真实运行证明该保守恢复机制具有跨项目覆盖，但不构成 blind accuracy。下文 2026-09-08 的 source/HFA/词典/新声学“下一步”均保留为历史实验记录，**不再是自动待办**。未来若重新扩张声学策略，必须先冻结新的 untouched final-mix truth，并证明最终 viewer SRT 相对 editor/当前 hybrid 的净收益。


第九切片后续数据入口：已由官方Range取得完整 `private/source_context_upgrade9_20260908/musdb_fresh/test/Al James - Schoolboy Facination.stem.mp4`，SHA256 `206d1ed3f140305c152496dc8be1e6476531e94b92ffb1022cc1dca84cebc10e`，约200.327秒，5条音频流；尚未提取mixture、读取标注或预测。使用 `output/source_context_upgrade9_20260908/musdb_fresh_asset_receipt_v2.json` 及selection_v2中的更正，保留v1。选曲来源是3989267行标注目录而非15547046词级API；先确认适用标注粒度、冻结后续预测，再评价，不能当已有独立收益。今天正负实验总表为 [准确率实验登记](accuracy-experiment-register-2026-09-08.md)。

2026-09-08 第九切片新增旧锚夹持的完整精确词序源锚，只进入显式joint实验。WALK882条重跑共同解码2→3对，恢复219/220，旧两对区间不变；但220与KEEP221仍重叠3017ms，全局采用0、成品时间/文字变化0。历史三首149行得到9个精确锚（4新增），新增8端点MAE165.75ms、最大312ms；这是源锚证据，不是成品或新盲测精度。无条件二段拆词的43076项词典遮蔽验证仅34.84%发音一致，未接入。隔离Python3.12全量1625项通过（4项可选FLOAT跳过）。全部正负实验登记见 references/accuracy-experiment-register-2026-09-08.md；本轮证据见 output/source_context_upgrade9_20260908/delivery_report.md。不封板。

2026-09-08 第八切片已实现显式相邻共同窗口与原子选择。同输入 WALK882条实跑触发4对，2对完成一次共享声学解码，另外2对因缺词dancefloor/缺右锚拒绝。两处内部候选重叠1929/862ms→0，但全局选择0，成品区间变化0，文字变化0；旧FW和旧HFA输出字节一致。这证明局部冲突机制可修复，不证明整段准确率提高；保留实验身份，不默认推广、不封板。下一步需获得长行内部可靠词级锚与可验证发音覆盖，不能靠放宽外部几何强行采用。工程与实测证据见 `output/source_context_upgrade8_20260908/delivery_report.md`。

## 2026-09-08 第七切片：词典接入完成，覆盖瓶颈定位

2026-09-08 第七切片新增通用英文派生词典构建器及anchored-path显式manifest接入。原字典保留，1052项新增发音均来自既有尾撇号词形或固定CMU直接记录。WALK120完整882条重跑：13次推理/17完整候选（此前5），选择0，最终区间0变化。已有三首公开歌保持9/143覆盖和MAE45.222ms，未测到新精度收益。rank18同音频仅加英文提示，词覆盖0→165/203、锚0→3，但候选仍0/44。主要瓶颈为完整源上下文资格和相邻区间兼容；不加搜索预算、不改canonical、不默认推广。最终工程验证、失败日志与复核见 `output/source_context_upgrade7_20260908/delivery_report.md`。

下一步优先研究长行内部的单调词序声学证据与相邻区间联合重建。语种提示能修复部分观察内容，但单独不能恢复字幕资格；不得把language当置信度。当前所有公开样本均已开发可见，新策略须另找预冻结独立检验，不能反复称盲测。

## 2026-09-08 第六切片：从窗口约束到声学路径约束

第五切片提出的多句补齐已实施并用新五首验证，不能直接提升默认策略：53个新目标的端点MAE858.321ms，出现32.16秒重复段错误。根因不是两端锚点错，而是HuBERTFA只按锚点裁窗，词/音素状态没有source时间约束。AP开关对这两个反例无效。

同轮修正原型将已有合格canonical行的source区间（含目标自身合格区间）映射成非SP状态时间带，沿用1500ms余量，在DP转移后限制目的状态。相同53目标开发回归MAE207.047ms、最大1867ms，全部保留；22个自身有带与31个仅有周围证据分别报告。此结果支持修复该失配机制，不支持封板或日韩推广。正式实验入口已接入；新三首149行/143内部目标覆盖9行，两策略MAE均45.222ms且输出相同，新增目标仅1行、无自身合格时间带，不能证明泛化增益。最终隔离1583项通过（4项可选音频跳过），WALK120路径重跑882条、5候选、选择0。完整证据见 `output/source_context_upgrade6_20260908/delivery_report.md`。

下一阶段优先解决可靠发音前端和有效源时间覆盖，再验证相邻区间联合重建。当前神经G2P探针已有实际错误，不接入；不能靠放宽资格或调低成本制造成品变化。

WALK120首轮仍为882条、HFA选择0。主要缺口还受 `dancefloor` 等词典缺失限制。隔离g2p-en试验发现合法音素但错误发音，未采用；独立CMU词库/原词库别名也未完整覆盖主窗口，不允许用猜测发音或丢词绕过。

## 2026-09-08 第五切片最新交付与下一瓶颈

已有可运行的原曲三行 HuBERTFA provider，独立 `report-only` / `hfa-only-overlay`，以及显式 `lexical_only_no_ap` 策略。后者恢复 Rihanna 的 AP 拓扑异常候选；模型、词典、源时钟、实际词序及最终路径均验证，生产默认未获得新授权身份。历史22行44端点no-AP MAE116.727ms（FW291.682ms），新五首169行仅2行合格、4端点MAE192ms（FW462ms）；样本覆盖不能省略，也不能称全曲/日韩通用精度。

实际 WALK120 全882 cue已重跑：唯一HFA候选219的mix区间723464–727022ms，与220的KEEP起点724643ms冲突2379ms，因此HFA选择0，成品新增收益0。单句精度改善不能自动解决相邻旧cue的连续错误；下一步应研究同一段的多个有声学证据区间联合重建，以及原曲匹配覆盖不足，而不是把冲突gate取消或只改成本逼它被选中。不能由三行推理的最外侧词直接赋予内部目标相同的精度保证；新的上下文/联合策略需独立冻结比较。

完整实现和更正见 `output/source_context_upgrade5_20260908/delivery_report.md`。HFA台账由534MB减到241KB，保存真实源观察供复查；旧FW候选台账体积仍独立存在。此前中文SOFA完整上下文在同16个可解释端点显著劣于当前成品，应维持否决；FLOAT改进波形保真但无已有gold端点收益。以上均不支持全局最优或封板结论。

2026-09-08 第五切片：新增可选 FLOAT source ASR 解码，避免中间 PCM16 削波；整曲时钟一致，H180完整781 cue shadow仅一条无gold字幕相对legacy变化，旧24端点测量不变，不能计精度收益。完整左右音频的SOFA历史重跑16个语义可用端点MAE130.312ms，相对current final为1胜14负1平，未采用；全12case/24端点保留分母。初版target±1500却输入完整三行的实验已勘误，不能作否定完整上下文模型的证据。详见output/source_context_upgrade5_20260908/float_implementation_report.md及context_full_window/implementation_report.md。新独立英文Cortez4/42行8端点HuBERTFA相对FW有初步源端收益，但有单端回退，须看同协议新增歌曲复验，不代表混音SRT或跨语种精度。

2026-09-08 第四切片确认可继续研究现有声学链：SOFA源探针前处理PCM16削波约1.85%，FLOAT对照无readback误差；末词作为对齐终态随窗口变化3000ms。固定无异常canonical三行并把目标放在中间后，同歌三窗口的目标终点波动降至44.13ms、起点73.15ms。新增alignment_contextual_segment_interval_ms复用完整词序校验，返回目标末词offset而非下一句onset；三份真实TextGrid重放一致。此为去除窗口依赖的机制证据，无gold、无最终SRT精度声明，暂未接默认runner。下一步可用既有中文标注作历史回归，另选未用歌曲作新验证；已有标注不能重称blind。详见output/source_context_upgrade4_20260908/。

2026-09-08 第三切片实测结论：新增 duplicate-only 全曲全最优链共识实验层，公开开发4首182行选中17→24（原17不变，新增端点38–372ms）；但预冻结的新4首137行仍15→15、无排除、无时间变化，因此暂不接入默认shadow/生产，不计最终SRT精度收益。Qwen整曲及固定短窗强制对齐均劣于现有FW；短窗同34端点MAE1806ms vs 267.9ms，健康条件留下的13端点仍1135.5ms vs189.6ms，且存在4个无健康报警的>500ms错误。补充SOFA原曲固定13.105秒两行探针亦完成：目标首词实际为5.77ms正时长且非边缘（早先零时长判断是舍入误读，已撤回），末词撞窗口边缘；起点结构可用但无gold不知准确率，无健康完整候选，不能泛化为SOFA整体失败。当前可证明的是文字匹配/现有声学观察的局部瓶颈，不是全算法最优；后续须先证明新声学候选的独立收益，不能扩大阈值或按开发集新增数升级默认。完整实现、冻结137行检验与否决证据在 output/source_context_upgrade3_20260908/implementation_report.md。

日期：2026-09-07
状态：原曲上下文到完整 shadow SRT 及目标内部转录恢复两个切片已实现并实跑；新的生产策略与跨歌曲精度仍未获得验证
适用范围：后续字幕算法升级、真实生产回归、Max 自动裁决与最终 SRT 写回

> 后续会话接手本项目时，先读 `AGENTS.md`、`SKILL.md`、`references/v4-status.md`，然后以本文作为下一阶段唯一实施任务书。不要重新发散成“再加模型 / 再加 gate / 再做一次全仓 review”。

## 2026-09-08 重新评估后的实施优先级

第二切片更新：固定首尾字符路径共识允许一个内部转录编辑段，保持原上下文资格；同时修复孤立 exact 遮蔽、n-best 截断与重复文本无界计算。三期完整 shadow 重跑选择 5/1/6 条字幕，起点变化 4/1/4、终点变化 5/1/6；相对首切片只在 WALK120 两条字幕上增加变化。已有 24 边界仍无新增收益，不能据此封板。隔离 Python 3.12 全量 1519 项通过（213.629 秒），独立复核通过；具体 producer/input/output 核验和逐条变化见 output/source_context_upgrade2_20260908/。额外三个局部 FW 解码探针均未产生合格新候选，未接入默认重试。公开多行整曲检验在代码、选样冻结后执行；结果不能用来调整同一测试集上的阈值。

公开整曲检验结果：JamendoLyrics 固定 revision、每语种文件名排序取首首，共 4 首、182 行，先冻结预测后读取人工时间标注，0 行剔除。旧 exact / 新 bounded / 共同 / 新增选中为 15/17/15/2；新增德语两句的起点误差 123/187ms、终点误差 115/41ms，四端均不超过 200ms。共同 15 句时间不变；仍有 165 行未选择，已有精确目标候选最大起点误差 3378ms。该结果证明源端正确候选覆盖增加，不证明最终混音 SRT 精度或所有语种可靠。选样、脚本、观察、候选、gold 与评估哈希链均经独立复核。该四首自此次评估后已成为可见诊断数据，后续修改需另冻结未见歌曲评价。

后续优先级据实测收窄：保留本轮内部转录恢复；不要继续按这两句调编辑阈值。先改善源端声学起止观察，再验证词级/音素级对齐如何避免把重复歌词或间奏划给首词；当前文字与上下文匹配不能解决这个误差。对已有源端 forced/CTC 能力先做预先固定选样的真实运行，确认能提供独立、非零时长端点，再接入现有候选到 shadow SRT 链。原先三个局部同模型 FW 解码失败的证据仍适用，不增加无效重试。

首个切片的交付更新：整曲 source ASR、多行容错定位、独立首尾候选、cut-aware 投影、区间联合选择及 SRT/CSV 回读已实现，协议见 [原曲上下文与区间联合升级](source-context-shadow-upgrade.md)。三期完整 shadow 实跑共选择 11 条字幕，改变 7 个起点和 11 个终点；已有 24 个标注边界没有变化，因此本次已标注样本的新增精度收益为 0。隔离 Python 3.12 全量 1485 项和独立复核通过。后续应先验证源端正确候选覆盖与未参与调参的公开标注，不继续按这 11 条变化数调选择器，不把实验评分升级为生产授权。详细执行记录保留在 output/source_shadow_upgrade_20260908/。

本节依据当前源码、真实产物和独立复核更新后续顺序；下文保留原始设计背景。与下文 P0 标注扩容、默认分段映射和严格串行顺序冲突时，以本节及用户最新授权为准。完整研究依据见 `output/architecture_reassessment_20260908/recommendation.md`；它不是另一份实施任务书。

1. **开发不等待新增人工标注。** 用户已明确要求复用已有确认并自主开发。现有外边界集实际为 12 个片段、24 个边界、11 首歌；它已多轮参与开发，只作历史回归，不能重新包装成未见验证。第 8 节的 60 点扩容不再是候选生成、影子 SRT、自动变换和公开语料验证的前置条件；缺独立 gold 仍限制新策略生产授权及跨新歌精度声明。
2. **先补原曲中的歌词位置和真实起止，再优化选择器。** 音频映射准确不代表 LRC 边界准确。以多行 canonical 上下文确定重复位置，生成 source start/end 候选，再经 cut-aware 投影进入最终混音；原曲不匹配或证据不足时保留 editor/当前安全结果。现有强制对齐、CTC、投影和候选结构应复用，不重新实现同类底层模块。
3. **第一个开发切片必须输出完整影子 SRT。** 一次串联上下文候选、路径兼容的相邻区间选择、影子物化、SRT/CSV 回读与配对评价。不能只新增 observer 后停止，也不能把未校准分数当成已知期望误差。现有生产 materializer 继续只写获授权的边界；影子产物使用明确实验身份，不冒用生产 authority。
4. **同一结构段先验证简单仿射，按证据引入局部/分段。** contextual 映射接入须保持采样率、窗口、搜索域和外侧上下文与验证配置一致。已有 6 秒/16kHz 最终混音诊断不能当成 24 秒/22050Hz 配置的正式失效；后者也不能越过 cut 或短片段适用范围强行运行。
5. **start/end 的统计分离与区间路径绑定同时成立。** 分别评估起止误差，但组合必须来自兼容的歌词位置/映射路径。已有逐点 DP 需增加路径兼容；`joint_boundary_geometry` 只提供几何可行性，不能按修改数或位移量替代声学选择。
6. **实际显示 SRT 单独验收。** 连续哼唱合并后，不再显示的内部边界与组外边界分开处理；外边界仍需证据。保留声学 QA，不以清除内部 flags 或改变 cue 数冒充起止精度改善。
7. **先验证候选上限，再衡量选择和写回损失。** 已有产品 evaluator 应填入真实候选、selected 和 final。正确候选不存在时改源端观察；候选存在但选错时改 selector；选对而未进入最终 SRT 时修接线。H180 冻结证据重放不变只能证明确定性，不能证明升级空间耗尽。

本轮追加真实音频对照还发现：低可信单窗的速率先验可能把已接受的全局稳健映射排除出候选域。更换为预先绑定的 effective timewarp 后，已知分歧点由低分歧义恢复为高分无歧义，对照点基本不变。此为候选搜索机制证据，不是最终歌词边界精度或生产授权；首个切片须检查这种先验覆盖问题。详见 `output/architecture_reassessment_20260908/prior_comparison.json`。

首个切片完成后，仍以第 10 节最终 SRT KPI 决定是否扩展或提升生产策略；不要求用户以“继续”触发每一步，不恢复已暂停的定时续跑。

---

## 0. 真实业务背景：这些字幕从哪里来、最终要解决什么

本文不是面向抽象 ASR benchmark，而是服务于真实的音乐混剪视频生产。现有业务背景已经分散记录在 `references/production-requirements.md`、`references/task-template.md`，以及历史工作流 `../../视频创作/歌词识别项目/README.md` 中；本文把与下一阶段 Max 直接相关的事实收敛到这里。历史工具只作为业务来源证据，不重新成为当前生产入口。

### 0.1 实际字幕从哪里来

正常任务**不是从零生成一份字幕**。典型链路是：

```text
多首歌曲/素材
  -> 调速、裁切、拼接、混音，形成最终节目音频
  -> 在剪映/Jianying 对最终节目做歌词/语音识别
  -> 导出 editor/source SRT
  -> 用 canonical lyrics + 音频证据校正文字、身份、结构和 timing
  -> 产出最终 SRT，回到视频编辑/发布流程使用
```

历史项目曾对同一成品做两次剪映识别并选取分段/时间更合适的一份；这说明 source SRT 本质上是**编辑器识别结果**，不是人工真值。但当前生产契约不要求固定“两次识别”：必须绑定本任务实际提供的 exact source SRT，不能假定识别次数或质量。

典型任务输入为：

- `source SRT`：剪映/Jianying 对最终节目识别后导出的字幕；
- `final mix`：用户最终视频实际使用的混剪音频，常见约 40–60 分钟；
- `song list`：歌曲顺序及大致节目位置；
- `canonical lyrics`：标准歌词，常见 LRC / Enhanced LRC / QRC；
- `source audio`：原曲或可用于 acoustic evidence 的对应单曲；
- 可选的 DAW/调速后单曲 WAV、BPM、exact stretch ratio、已知 cut/版本/口播等信息。

当前规范目录仍以 `references/task-template.md` 为准，例如：

```text
private/<任务>/input/
├─ source.srt
├─ mix.wav
├─ songs.txt
├─ lyrics/
├─ source-audio/
└─ bpm.txt / 其它可选任务信息
```

### 0.2 final mix 才是最终时间轴；调速后单曲不是成品真值

用户工作流中经常有“原曲/调速后单曲 WAV”，它们非常有价值，但不能等同于最终节目音频。

进入 final mix 后还可能发生：

- 裁前奏/尾奏；
- 中间剪断或跳段；
- 同曲内部 splice；
- 手工多段调速 / piecewise timewarp；
- 两首歌 crossfade；
- overlap / 伴唱 / 口播；
- 插入、删除、静音或其它编辑。

因此：

> **原曲、LRC 时间戳、调速后单曲都只能提供 source/local evidence；exact final mix 才是最终字幕时间轴要贴合的对象。**

这也是为什么“把 LRC 按 BPM 或一个 affine 比例整首缩放到节目里”不能成为最终方案。单曲→final mix 应优先做局部、可验证、必要时 piecewise 的映射。

### 0.3 canonical lyric 与 editor 各自解决什么

- **canonical lyrics**：文字内容与歌词顺序的真源；
- **canonical line break / LRC timestamp**：可提供结构和时间证据，但不是最终 cue segmentation / boundary 的无条件真源；
- **editor SRT text**：识别观察，可用于 identity evidence，但可能错字、漏字、谐音、乱码或识别成另一语言；
- **editor SRT timing**：一个候选时间轴，局部可能很准，也可能很差；可靠度必须由当前任务/track/cue 的证据动态估计；
- **source/retimed audio**：帮助定位 source 内容及局部变换；
- **final mix audio**：最终 timing 判断必须落到这里。

文字正确与 timing 正确是两条独立轴。不能因为 editor 文本很差，就自动认定它的 timing 也差；也不能因为文字碰巧识别正确，就自动授予 timing authority。

### 0.4 用户真正需要解决的问题

最终要解决的不是单一“字幕识别率”，而是下面几类真实生产问题：

1. **文字错误**：editor ASR 的错字、漏字、多字、谐音、乱码、code-switch 误判等，最终必须回到 canonical text/order。
2. **局部起点错误**：字幕早出或晚出；目标是在有证据时做到接近帧级，而不是把整条 timeline 无差别重建。
3. **局部终点错误**：唱完后字幕长时间挂着，或过早消失；应区分 acoustic end 与 display end。
4. **漏句/多句/错误分句**：editor 漏掉实际演唱，或 canonical line break 与合理显示 cue 不一致。
5. **调速/剪辑映射错误**：单一 BPM/LRC 缩放无法覆盖手工调速、裁切、splice、crossfade 等真实编辑。
6. **结构语义问题**：重复副歌/同句多 occurrence、cut/repeat/reorder、overlap/crossfade、cue ownership、歌曲版本身份等。
7. **人工成本过高**：系统应自动处理能经校准证明更优的 timing；人工主要留给多个结构解释仍无法消歧的少量难点。

### 0.5 最终成品什么才算“好”

用户真正需要的是一份可直接进入视频生产的最终 SRT：

- 歌词文字和顺序正确；
- 在 exact final mix 上出现/消失位置合理；
- 多数本来正确的 cue 不被无证据破坏；
- 原来明显错误的 cue，只要有更优候选就应自动纠正或 rescue；
- cut/repeat/overlap 等结构自洽；
- 尽量少人工；
- display text 清理、敏感词显示策略、异常长尾等发布层规则可以继续存在，但必须与“真实 timing accuracy 提升”分开统计，不能拿文字/显示层变化冒充边界精度升级。

### 0.6 产品最低质量契约与“大模型 + 程序”分工

即使当前还不能普遍证明 timing 修正比 editor 更准，项目也必须有确定的产品下限：

1. **Content correctness**：trusted canonical 与实际内容身份已成立的地方，不保留已知错误的 editor ASR 文字。
2. **Structure / ownership correctness**：正确歌曲、occurrence、字符归属、split/merge 与实际 cut/repeat/overlap 语义优先于时间微调；结构仍有多个合理解释时保留未知。
3. **Timing non-regression**：纯文字修复不得顺带移动时间；没有更强证据时保留已有 editor/hybrid timing。
4. **Timing improvement**：只有候选在冻结评测/校准下证明预期收益后，才获得自动 timing 写回资格。

因此项目即使暂时不动一个毫秒，也应该能把“时间尚可但文字识别很差”的字幕变成 **文字明显更可靠、结构不被破坏、原时间轴完整保留** 的可用成品。这是项目最低价值，不依赖是否已经解决帧级 timing。

这一层不应只靠字符串相似度、DP 和手写规则。大模型最适合处理程序难以表达但人能理解的语言/语义问题，例如严重乱码仍对应哪段 canonical、音译/谐音/romanization、混语种、歌词缩写/变体、复杂分句、重复段语义和候选结构解释。大模型可以输出结构化候选、理由和不确定性，再由确定性程序检查：完整字符 ownership、顺序单调、不得跨未知 occurrence、不得吞掉其它 cue、纯文字修复 timeline signature 必须完全不变等硬约束。模型无法满足这些约束时不自动物化。

大模型也可以承担“选择下一步看哪里”的职责：阅读多 observer 的冲突摘要、找出最值得追加声学证据或人工 gold 的区域、分类失败模式，从而把昂贵模型预算集中到真正困难的少数 cue。它不是 canonical 真源，也不是未经校准的 timing 真值。

---

## 1. 项目核心目标

Max 的目标不是“尽量不犯错”，也不是“证明很多地方不敢改”。

**Max 的产品目标是：在当前可获得证据条件下，自动产出期望误差最低、结构语义自洽、人工需求尽可能低的最终字幕。**

具体含义：

1. **高确定性结果直接精准修改。**
2. **中等确定性结果，只要经过预先冻结的 calibration + untouched blind 证明其条件期望误差低于现有 editor，就自动修改。**
3. **即使 candidate 只能做到数百毫秒，只要 editor 已被独立证据证明更差，也应自动 rescue。**
4. **普通 timing 不确定性不应自动变成人工任务。** 如果没有候选被证明优于 editor，就自动保留 editor。
5. **只有歌词身份、歌曲 occurrence、cut/repeat/reorder/overlap/crossfade/cue ownership 等结构语义存在多个仍无法自动消歧的解释时，才保留人工。**
6. 人工应集中在真正需要“理解结构”的少量难点，而不是承担大规模逐句 timing 微调。

最终评价对象始终是：

> **exact final mix 上最终用户看到的 SRT，是否比旧 final / editor 更准。**

代码量、模型数量、artifact lineage、单测数量、`publish_ready`、gate 数量都只是手段，不能代替这个目标。

---

## 2. 当前 a19 为什么“工程进步很大，最终字幕提升很小”

2026-09-07 对 fresh a19 与历史 final 做逐 cue / 字节级比较：

- 7 个项目合计 6041 cues；
- 5994 cues 完全未变，约 99.22%；
- 30 条只做了 display end 长尾截短；
- 17 条为文本清理；
- **start timestamp 实际变化 0 条。**

根因不是最近工作没有价值，而是大量新能力被固定成只诊断、不写回：

- Pro 当前固定 `automatic_timing_change_allowed=false`；
- Max Next 当前固定 `selection_recommendation_only_no_srt_mutation`；
- outer Expected-Loss closeout 明确 `production_materializer_added=false`；
- outer start/end observer 未取得 production authority 时统一 `keep_editor`。

这些机制提高了安全性、provenance 和 false-ready 检测，但天然很难转化成最终时间轴提升。

**下一阶段必须把研发重心从“能否发现风险”切换到“在严格 blind 约束下，哪些改动可以安全写回并真实降低误差”。**

---

## 3. 关键修正：Editor 不是固定强先验，任何语言也不预设好坏

剪映 / editor 的可靠性会随语言、唱法、曲风、音色、混响、伴唱、局部切分和当前素材而变化。

**禁止写死以下规则：**

- 中文一定可靠；
- 韩文一定不可靠；
- 日文处于某个固定等级；
- 古风一定较差；
- 某艺人的歌固定降低权重。

这些都只能是历史观察或候选特征，不能直接成为 production authority。

### 3.1 Editor 可靠度必须动态、局部估计

至少拆成两条互不替代的轴：

- `editor_text_identity_reliability`
- `editor_timing_reliability`

例如某些韩文 cue 可能被识别成英文谐音：文本身份明显错误，但 cue 的大致 vocal onset 仍可能有参考价值。反过来，也可能文字碰巧对而 timing 很差。

因此 text identity 与 timing 必须独立评估。

### 3.2 语言、曲风、唱法只能作为弱特征

可以进入 reliability/risk model 的特征包括但不限于：

- language / script / code-switch；
- phoneme/token coverage；
- editor 文本与 canonical 的 lexical similarity；
- ASR language posterior / confusion；
- rap / fast syllabic density；
- 长拖腔 / ad-lib / melisma；
- 和声、群唱、双人；
- 混响、伴奏遮蔽、低 vocal SNR；
- 重复副歌 / 重复短句；
- cut / splice / crossfade / overlap 邻近；
- manual tempo change / local timewarp；
- 当前 track 上 editor 与独立音频锚点的已观测 residual；
- 当前 cue 周边多个候选的一致性与 ambiguity。

**这些只是 feature，不是 hard rule。**

### 3.3 Reliability 使用分层校准，而不是固定语言表

建议形式：

```text
global prior
  + language/style weak features
  + track-level observed reliability
  + local cue evidence
  -> candidate-specific conditional risk
```

重要规则：

1. 数据不足的 language/style bucket 自动回退到更宽的 global calibration，不自行猜测。
2. 当前 track 有足够独立 anchors 时，track-local residual 应覆盖宽泛的语言经验。
3. 当前 cue 的 direct final-mix evidence 应优先于语言标签。
4. 不允许为某语言单独放宽 catastrophic gate 来换 coverage。

---

## 4. 下一代 Max 架构：先结构，后 timing

### 4.1 Layer A — Structural Resolver

先解决：

- canonical lyric identity；
- track / song occurrence；
- lyric occurrence / repeated chorus；
- cut / repeat / reorder；
- overlap / crossfade；
- cue ownership；
- source segment identity。

输出只允许：

- `STRUCTURE_RESOLVED`
- `STRUCTURE_AMBIGUOUS_MANUAL`

只有第二类进入人工。

**结构不确定时，禁止通过时间取平均、最高 score 或强行 monotonic 来掩盖语义问题。**

### 4.2 Layer B — Boundary Candidate Set

在结构已解决的前提下，对每个 outer start / outer end 建立候选集合。

至少包括：

- editor 原时间（candidate 0）；
- Independent Fine；
- SOFA；
- HuBERTFA；
- local source→final-mix waveform / spectral matching；
- 必要时 bounded ASR word-span；
- 必要时 vocal onset / vocal offset evidence。

原则：

- start 与 end **必须分开建模、分开校准、分开验收**；
- 单曲 WAV 只提供局部模板，**final mix 才是真正时间轴**；
- 默认做 local / piecewise mapping，不允许用全曲单一 affine/BPM 覆盖手工分段调速、裁切、splice、crossfade；
- candidate generator 只产候选与特征，不自行宣布全局 authority。

### 4.3 Layer C — Joint Expected-Loss Selector

每个 boundary 同时比较 editor 与所有 candidate，不再问“哪个 backend 是真理”。

输出仅允许：

- `KEEP_EDITOR`
- `USE_CANDIDATE:<id>`
- `RESCUE_WITH_CANDIDATE:<id>`
- `STRUCTURE_AMBIGUOUS_MANUAL`

普通 timing 证据不足时应 `KEEP_EDITOR`，不是人工。

---

## 5. Expected-Loss 必须优化“相对 editor 的真实收益”

单看模型自身 MAE 不够。真正要估计的是：

> `E[loss(candidate) | 当前局部特征]` 与 `E[loss(editor) | 当前局部特征]`

只有 candidate 的条件期望损失低于 editor，并达到冻结的 promotion margin，才允许自动修改。

Loss 至少包含：

- absolute timing error；
- 比 editor 恶化 >100ms 的 harmful change；
- >250ms 明显误差；
- >500ms catastrophic；
- >1000ms severe catastrophic；
- paired improvement / regression；
- start/end 分开；
- track-grouped，而不是把同曲大量重复 cue 当独立样本。

结构语义错误不进入普通 timing loss，直接回 Structural Resolver。

所有 loss 权重、promotion margin、catastrophic cap、feature set 必须在 calibration 阶段冻结；**blind 揭晓后不得再调。**

---

## 6. 三档自动修改能力

### Tier A — Precise Auto

候选经 blind 证明高精度、低 catastrophic 风险。

动作：直接写回。

### Tier B — Calibrated-Better Auto

候选未必达到几帧级，但 calibration + blind 证明其 conditional expected loss 显著优于 editor。

动作：直接写回。

这是当前系统最缺的能力。

### Tier C — Rescue Auto

独立证据强烈表明 editor 明显错误，例如 editor 可能偏 1–2 秒，而最佳 candidate 虽只有 200–400ms 级，但期望损失显著更低。

动作：自动 rescue。

原则：

> **不能因为 candidate 不够完美，就保留已经被证明更差的 editor。**

### 无候选占优

结构已解决，但没有 candidate 被证明优于 editor：

- 自动 `KEEP_EDITOR`；
- 不创建人工任务。

---

## 7. 当前最值得解决的技术问题：Independent Fine 的灾难误配识别

Independent Fine 当前最有潜力：

- calibration selected 19/24；median/P90/max = 10.49/28.38/38.40ms；
- blind selected 16/24；median/P90 = 12.04/17.60ms；
- 但出现 1 个 1040.14ms catastrophic，因此 authority 被正确拒绝。

下一阶段首要问题不是让 median 再低几毫秒，而是：

> **如何在不知道 blind truth 的前提下，把这类约 1 秒 catastrophe 自动识别并拒绝。**

优先研究的通用特征：

- 多窗口尺度预测一致性；
- source→mix 与 mix→source 双向 cycle consistency；
- top1/top2 peak ambiguity、margin 与位置间距；
- candidate 是否命中/接近搜索边界；
- 相邻可靠 anchors 的 local timewarp 连续性；
- 重复歌词/重复副歌的多峰风险；
- candidate 与 editor 差值；
- 多 backend 局部共识/冲突；
- vocal activity 在 candidate 附近是否真的发生 onset/offset；
- cut/crossfade/manual tempo change proximity。

旧 1040ms holdout case只用于 failure taxonomy，**不得用来事后调旧 selector 阈值。** 新规则必须在新的 calibration population 上形成，再用新的 untouched holdout 验证。

---

## 8. 分阶段实施：每一步都必须可做、可测、可验收

### P0 — 建立真实产品质量基线

目标：先测清 editor / 旧 final / a19 在不同场景到底多准。

任务：

1. 建立新的 outer start/end human-gold benchmark。
2. selection 在任何新 selector/backend 调参前冻结。
3. 以 **track 为 split unit**，禁止同曲泄漏 calibration→holdout。
4. 起步目标：
   - start >= 60 gold points；
   - end >= 60 gold points；
   - >= 12 首不同 track；
   - 覆盖中文、韩文、日文、英文/混合中当前真实可得项目，不要求人为凑语言比例；
   - 覆盖普通、重复、含糊咬字、rap/快句、拖腔、手工调速、结构邻界等实际 failure mode。
5. 每个点记录 editor、旧 final、a19 与可用独立候选误差。

验收：

- selection lock + final-audio SHA + human gold；
- editor baseline：median/P90/P95/max、>100/>250/>500/>1000ms；
- 按 track、language feature、style/risk feature 分层报告，但不把分层结果硬编码成 production rule；
- 不允许根据 holdout 调参。

如果没有足够 gold，不继续声称“准确率升级”。

### P1 — 统一 Candidate Generator

目标：所有 observer 统一只负责产候选和局部特征。

任务：

1. 定义 candidate schema：boundary kind、candidate_ms、backend、correlation group、local features、provenance。
2. editor 永远作为 candidate 0。
3. Independent Fine、SOFA、HuBERTFA、local waveform 等进入同一接口。
4. start/end 独立。
5. 单曲→final mix 使用 bounded local/piecewise mapping。

测试：

- deterministic replay；
- hash/provenance binding；
- 中文路径；
- cut/crossfade 不跨结构边界搜索；
- repeated lyric 多峰不静默选错 occurrence；
- backend unavailable 时 editor candidate 仍可完成生产。

验收：

- 同一输入重复运行 candidate set blob-identical；
- 本阶段 **不得修改 SRT**。

### P2 — Dynamic Editor Reliability + Candidate Risk

目标：不按语言写死 prior，而是估计当前 boundary 的 editor 与 candidate 条件风险。

任务：

1. 建立 global + track + local cue 的分层 reliability features。
2. text identity 与 timing reliability 分开。
3. language/style 只作为 weak feature，不作为 hard rule。
4. 数据稀疏 bucket 回退到 global calibration。
5. 冻结 feature set、loss、threshold、promotion margin。

测试：

- 去掉 language feature 时模型仍能依赖直接证据工作；
- language label 改变但所有 direct evidence 不变时，不允许出现无依据的大幅 authority 翻转；
- track-local evidence 足够时能覆盖宽泛 global prior；
- sparse bucket fail-safe 回退。

验收：

- calibration-only report；
- frozen policy artifact；
- 运行 untouched holdout 前 policy SHA 固定。

### P3 — Catastrophic Rejection

目标：解决“多数很准、偶发 1 秒错”。

任务：

- 对 candidate ambiguity/cycle consistency/local continuity 等建立 rejection/risk 规则；
- track-grouped calibration/cross-validation；
- 冻结后再跑 untouched holdout。

验收门槛：

- auto-selected subset expected loss 明确低于 editor；
- paired track-grouped bootstrap improvement 95% CI 不支持明显劣于 editor；
- **不得制造新的 >500ms catastrophic auto-change**；
- changed-boundary harmful-change rate（比 editor 恶化 >100ms）目标 <=5%；
- 不能靠把自动修改 coverage 压到接近 0 来“通过”。

### P4 — Joint Selector：KEEP / AUTO / RESCUE

目标：把 candidate-specific selection authority 接起来。

任务：

1. start selector 与 end selector 分开。
2. 对每个 boundary 估计 editor 与 candidates 的 conditional expected loss。
3. 输出 KEEP / USE / RESCUE / STRUCTURE_MANUAL。
4. Tier A/B/C 的 promotion 规则在 blind 前冻结。

验收：

- selector decision artifact 可独立 replay；
- 没有结构歧义的 timing case不得落人工；
- rescue case 可以在 candidate 绝对误差不漂亮、但相对 editor 明显更优时自动选择 candidate。

### P5 — Production Materializer

目标：让已经通过 blind 的 selector 结果真正进入最终 SRT。

任务：

1. 新增/扩展 production materializer 消费 exact selector decision bundle。
2. 只允许修改 selector 授权的 boundary。
3. 保留 old/editor timing、候选、risk、decision 的完整 audit lineage。
4. start/end 可分别来自不同 decision，但必须满足 cue geometry 和结构约束。
5. 不允许 materializer 自己重新选 candidate。

测试：

- unauthorized boundary mutation hard fail；
- stale selector/bundle/audio SHA hard fail；
- monotonic/order/overlap/negative duration 全覆盖；
- text ownership 不因 timing materialization 改变。

验收：

- synthetic + frozen real regression 通过；
- exact SRT diff 只包含授权变化。

### P6 — 真实生产 A/B

优先重跑具有代表性的现有项目，而不是一次全量乱跑。

建议顺序：

1. KPOP130：已有文本/display 改善，适合验证韩文/混合场景的动态 editor reliability；
2. KPOP110：Gee 手工多段调速，适合验证 local/piecewise mapping；
3. H180：已有 human-gold/internal authority，可验证 outer selector 与结构层协作；
4. Walk140：验证普通中文/清晰场景下“多数 KEEP_EDITOR、少数改得更好”；
5. 再扩 H190/KPOP200/Walk120。

每个项目必须输出：

- old vs new cue count；
- start changed count；
- end changed count；
- KEEP/AUTO/RESCUE/MANUAL counts；
- human-gold 覆盖区 old/new paired errors；
- changed-boundary improvement rate；
- harmful-change rate；
- >100/>250/>500/>1000ms；
- 最终 SRT A/B；
- 哪些变化是 text/display-only，不能混入 timing improvement。

**如果新版最终 SRT 没有真实改善，就不能因为内部 gate 更完善而宣布本阶段成功。**

---

## 9. Human review 的收敛原则

后续人工队列只允许主要包含：

- identity ambiguity；
- repeated occurrence ambiguity；
- cut/repeat/reorder ambiguity；
- overlap/crossfade ownership ambiguity；
- 多个结构解释都与当前证据相容。

以下情况默认不应人工：

- timing candidate 不够强 → KEEP_EDITOR；
- editor 明显差且 candidate 经校准更优 → RESCUE；
- 多个 timing candidate 中一个期望损失最低且通过 promotion → AUTO；
- backend unavailable → 使用剩余候选/KEEP_EDITOR。

人工率是产品指标，但不能单独优化；降低人工不能以增加 catastrophic timing 为代价。

---

## 10. 每次升级必须报告的产品 KPI

以后每次 Max 算法升级，必须同时报告：

1. 最终 SRT 实际修改了多少 start；
2. 修改了多少 end；
3. KEEP / AUTO / RESCUE / MANUAL 比例；
4. editor baseline MAE/median/P90/P95/max；
5. 新版相同指标；
6. paired improvement；
7. changed-boundary improvement rate；
8. harmful-change rate；
9. >100ms / >250ms / >500ms / >1000ms；
10. track-grouped 结果；
11. 按 language/style/risk feature 的诊断分层；
12. 真实 SRT old/new diff。

没有这些数据，不得把“代码升级”描述成“字幕准确率升级”。

---

## 11. 明确禁止的下一步

后续会话不要：

- 继续为了“更严谨”增加一堆永远 no-mutation 的 gate；
- 直接给 SOFA/HuBERTFA/Independent Fine 某一个 backend 全局 authority；
- 把语言标签硬编码为 editor 高/低可信；
- 用旧 blind catastrophic case 调阈值再重新宣布通过；
- 为提高 coverage 放宽 edge guard / catastrophic gate；
- 用单曲 WAV 假定等于 final mix；
- 把 semantic gate 的秒级阈值当成帧级 accuracy evaluator；
- 用单测、QA `publish_ready`、artifact seal 代替最终 SRT A/B；
- 一开始就全量重跑 7 个项目，先完成 P0–P5 的严格小范围闭环。

---

## 12. 后续会话的执行顺序

接手后严格按：

```text
P0 真实 outer human-gold / editor baseline
  -> P1 candidate schema/generator
  -> P2 dynamic editor reliability
  -> P3 catastrophic rejection
  -> P4 joint expected-loss selector
  -> P5 production materializer
  -> P6 representative real A/B
  -> 达标后再扩全量 production
```

任何一步未达到验收条件，不要靠后续步骤“掩盖”。

最重要的判断标准只有一个：

> **最终 SRT 是否在真实 final mix 上，以可复验的人耳 gold / 独立证据证明，比现有 editor/旧 final 更接近期望最优。**

## 2026-09-08 第十切片执行结论与后续约束

WALK实际归因：696–736秒15个波形patch相对旧mapping偏差仅2.28–2.83ms，不解释秒级冲突。Rihanna为ordinal4，历史editor_batch未包含它；其KEEP是canonical/LRC投影，不能称剪映原时间轴。按内容运行后原稿3个精确区域，Smart后合成一个[4,70)连续区域，可安全接回旧时间轴。

本轮已修区域恢复的边缘检查作用域和完整canonical字符offset，真实生成876条SRT，55对齐起点+55对齐终点变化，源端HFA一致性显著改善；证据 `output/source_context_upgrade10_20260908/editor_final_comparison.json`。无该区人工gold，不能将proxy写成准确率。v2为最新结果，v1 metadata缺陷已保留并声明替代。

本轮停止将“更长HFA上下文/更多局部锚”作为默认改进方向：17目标oracle比旧HFA略差，5目标matched外侧位置收益仅3.6ms且连续文本比旧三行差。新歌严格唯一上下文词起点仍有长尾，官方数据也没有词尾。下一阶段应从真实剪映/Smart已证明的cue ownership与时间先验进入，针对未匹配片段保留source声学补证，不能先用LRC分行重建一条更差的KEEP再逐条抢救。以此形成更多歌曲的实际SRT对照后再决定推广；不得按语种硬编码editor可靠度，也不得凭本轮单首proxy封板。

## 2026-09-08 第十一切片：普通多语种顺序消歧

第十一切片没有追加LRC线性外推，而是接通已有普通多语种source重复消歧。Gee与Whiplash各有1个新的终点写出，分别提前150/1085ms；仅有source词序/观察证据，无两端独立gold，不能将位移解释为准确率收益。真实同候选消融和输入hash见 output/source_context_upgrade11_20260908/。下一方向继续组合局部editor可信run、独立source观察和映射；长谐音cue/缺句须补声学证据，单点剪映值不能跨重复段外推，通用ASR幻觉也不能因字面匹配而获得边界真值地位。

本轮已有medium权重交叉检查未确认Whiplash新增pos36；三个共同target的词尾仍有160–740ms分歧。不同权重的共同位置不等于精确边界同意。不得把1085ms写出位移包装成准确率突破，也不能从两种auto语种设置的失败推断所有语种路由/歌唱模型已达上限；后续需区分自动语种判断、书写形式、真实声学漏识别和边界解码问题。

## 2026-09-08 第十二切片：逐段语种检测实测

新增默认关闭的 source_asr.multilingual=true（language=null），原生逐段检测、source-observer-1.2 独立缓存；旧默认和1.0/1.1缓存身份不变。Whiplash同turbo/音频对照：auto 5候选/5采用，整曲en 0/0，逐段自动11/10；新模式相对输入写出8 start、10 end，文字不变。这是覆盖和写出变化，没有独立端点gold，不能声称准确率提升。

Al James独立公开词起点诊断：严格唯一上下文匹配92/312，220保留null；新旧共同92起点全部一致，MAE539.966ms、p95 1543.249ms、max5880ms无变化。旧en与新auto+multilingual同时改变两个控制，不能称单因素；归因附加更正保留原报告和收据。无word-end真值、不是blind，不推广默认、不宣布封板。逐段模式开头误识别未恢复，并丢失auto的第36条候选（end55209退回56294ms）；相对auto共11条cue时间变化，不能称无损收益。三组固定对照完整记录于output/source_context_upgrade12_20260908/。
## 2026-09-09 editor-first / hybrid 最终封板文档收口

当前收口必须区分 hybrid production/materializer QA、viewer final structural audit 与 semantic/release gate。KPOP130 viewer display v3 structural audit 已通过：`passed=true`、errors=0、window violation=0、content-end violation=0、confirmed/unconfirmed overlap 均为0；warning仅 `long_display_holds` 10条（duration min349 / median1902.5 / p95 4395.3 / max7563ms，0条>=8000ms）。但 semantic/release gate 仍失败：projection editor witness 2/12 track fail，independent-audio final layer 12/12 fail，`audio_anchor_count=0`。formal fusion SHA `01575459...` 为 `e22f10d` 前旧证据，缺少当前 `canonical_start_covered` / `canonical_match_ambiguous` 等资格字段；不得复用旧 semantic QA。完整 release-ready 仍需 fresh independent audio evidence/fusion 并通过 semantic gate，不能把 materializer `publish_ready=true`作为完整发布结论。
