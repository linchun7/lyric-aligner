# 当前能力与使用边界

评估日期：2026-09-09。基于当前 editor-first / hybrid 封板候选代码、六个真实长项目结构回归、KPOP130 viewer display 与既有历史标注；产品身份仍为 `4.0.0a19`，新生产语义由独立 policy/mode/artifact lineage 区分。总体准确率仍因缺少代表性的 untouched final-mix truth 而未知。

## 定位与评分

当前适合充当**有规范歌词和剪映字幕输入的、可追溯的字幕修复与审核工具**。尚不具备任意歌曲、任意语种、无需审核即可保证准确的自动字幕生产能力。

工程质量综合 **7.0/10**：四个维度等权平均。以下为基于代码与证据的工程判断，不是实测识别正确率，也不是与商业产品的排行榜比较。

| 维度 | 分数 | 判断依据与扣分原因 |
| --- | --- | --- |
| 架构 | 7/10 | 文字、映射、边界证据、选择与写出已有分层；旧总入口仍承载解析、QA及流程编排，跨层维护成本较高。 |
| 代码可维护性 | 6/10 | 有版本化策略、显式异常和回归保护；`redo_karaoke_pipeline.py` 超过5600行，source packet与内部切分模块均超过1200行，实验分支及兼容路径较多。 |
| 工程验证 | 8/10 | 具备单元测试、文档契约、产物身份检查、回放与CI矩阵；单元测试不能替代真实歌曲质量评测，公开源码也不包含私有媒体和gold。 |
| 性能与风险控制 | 7/10 | 有缓存、有限搜索预算和不确定性拒绝；音频模型环境复杂，完整跨语种时延、资源与错误率尚无统一测量，拒绝输出不能算正确识别。 |

按本项目有准备输入的辅助修复目标，实用能力约 **7/10**；若目标改为“任意新歌全自动、无人复核交付”，成熟度约 **4/10**。两者是不同使用目标，不能平均成正确率。当前缺少有代表性的独立成品真值集，**无法负责任地给出总体准确率百分比**。

## 已具备的能力

| 能力 | 当前可做 | 前提与边界 |
| --- | --- | --- |
| Standard / Lexical Floor | 按可信 canonical 修正文案并冻结 cue 编号、数量和起止时间；单独报告 unresolved cue / unmatched canonical，而不是把“程序没报错”当成文字完成 | 不修复原时间轴错误；raw-LRC 全曲未使用内容不能直接当作 final-mix 漏词。 |
| Smart | 使用timed canonical、剪映多数锚点和变速先验进行无音频修复 | 依赖可识别锚点；不是声学验证，不按语种直接推断剪映可靠度。 |
| Pro / Max | 按需加入局部音频或完整Source-to-Mix映射、对齐与重建 | 需要相应音频、模型与有效证据；更高模式不保证更准。 |
| 已确认结果复用 | 在录音和目标身份匹配时复用人工边界、重放修补 | 减少同一材料的重复劳动，不证明新歌泛化。 |
| 剪映区域恢复 | 根据文字归属、顺序和范围恢复已有可信区域，支持任务级 all-occurrences 重复恢复和切分后的字符坐标 | 不能把所有剪映时间当真值，不能从少数锚点无条件外推整曲；无可证明区域保持原结果。 |
| Hybrid topology reconciliation | canonical evaluation 负责结构/漏句完整性，严格 editor-preservation 负责局部可信 timing，再以完整 character coverage 合并为 production | 只接受 exact-bound preservation/reconciliation；至少一次真实 editor restore；不是 LRC 全局 timing authority。 |
| Resolved-canonical lexical floor | 对已解析到 final mix 的 occurrence 做字符级 ownership/coverage 审计；KPOP130 当前 786 canonical rows / 12,539 normalized characters 已做到 12,539/12,539 覆盖且 mismatch/gap/overlap/unowned=0 | 只证明相对 resolved canonical 的生产链不丢字/改字，不证明歌词源、版本或 canonical wording 本身绝对正确。 |
| 大模型语义复核 / canonical rebuttal | 将严重 ASR、音译/混语种、canonical 疑似错词变成 hash-bound 结构化候选；程序重新验证 evidence role、ownership 与 timeline immutability | canonical lexical rebuttal 需独立支持证据且不得有直接 canonical 反证；目前先 shadow，不能靠语义“读起来更顺”自动定真。 |
| Viewer lexical gate | display policy 只允许 normalized-equivalent 的空格/标点/大小写/排版变化及显式 mask；未经 canonical rebuttal 授权的 lexical 改字在加载/最终 audit 两层都会失败 | 展示层不再拥有绕过 canonical truth 的改字权限；敏感词 mask 与 lexical truth 分开审计。 |
| Blind timing decision evaluation | 可在读人工 truth 前冻结 changed boundary + deterministic unchanged controls，生成候选隐藏的音频复核包，并统计 harm/rescue/catastrophic/manual-repair 等 selector 指标 | KPOP130 80-case 只是 development wiring，不是 untouched/blind 精度证据；真实结论需新项目先锁题再标 gold。 |
| QA与审计 | 追踪来源、配置、版本、边界证据及最终产物，保留review/BLOCK | 证明可追溯与约束成立，不等于全部听感正确。 |
| 辅助试听 | A/B、选点前后试听、步长输入与时间显示 | 是纠错辅助工具，不是算法必须持续依赖的训练循环。 |

## 尚未具备或尚未证明

- 对未见的K-pop、日语、混合语言、说唱、拖长音、重复副歌和多人重叠提供稳定的端到端质量保证。
- 在剪映严重漏识、大段连续或谐音误识时，自动补齐全部歌词并可靠确定所有内部边界。
- 从任意不完整/错误 canonical 自动恢复权威歌词。当前已能把 canonical 疑似错词变成大模型/声学证据绑定的 rebuttal candidate，并阻止未经授权的 viewer 改字；但没有足量独立支持且仍有直接反证时不会自动推翻 canonical。
- 对每条输出给出经过独立校准的“正确概率”，以及已验证的全量覆盖率、尾部误差和自动交付率。
- 在不增加人工或独立真值的情况下，证明新策略优于现有最终SRT；旧标注能回归，不能反复充当新盲测。
- 开箱即用的带模型离线产品与统一硬件性能承诺；源码仓库不附私有媒体、模型或人工标注。

## 实验结果应如何理解

既有历史成品对照中，24个已确认边界的起点/终点MAE曾由59.17/91.17ms降至30/9.17ms，主要来自既有确认值复用。这不是跨歌曲的新模型准确率。维护阶段同区域重复恢复成功、最终876条字幕字节不变，证明执行稳定性，不代表新的精度提升。

共同窗口实验曾把两处候选重叠1929/862ms降至0，但因其他区间冲突，最终选择为0、成品未改变。部分新公开源端实验仅覆盖9/143个目标；不能只报这9个目标的低误差而忽略134个缺失。详见[实验登记](accuracy-experiment-register-2026-09-08.md)。

source shadow、HFA/joint、FLOAT及multilingual等继续保留显式实验身份，不因实现存在或候选增多而自动推广。2026-09-09 的 editor-first / hybrid production 已完成工程封板候选与真实长项目回归，但只有 KPOP130 有本轮可量化的 historical development 边界收益；仍不能声明全项目或未见歌曲的总体准确率。

## 维护与重新升级的条件

优先处理可复现缺陷、运行可靠性和文档入口一致性。大型模块值得在实际修改时逐步拆分，但不为评分新增一次全面重构。重新做算法升级，应先确定错误类别、冻结未参与调参的独立数据，并同时报告最终SRT的覆盖率、端点误差分布、严重错误、回退/拒绝和运行成本；改善须超过旧final及editor基线且不损害原本正确部分。

最强的反方证据是现有局部声学实验确有改善，说明算法并非理论上无路可走；当前最大风险是把少量已见目标上的改善误认成通用进展。足量独立成品配对结果若证明稳定净收益，将推翻目前暂停扩张的判断。现阶段以 editor-first / hybrid 作为保守生产升级方向，并继续按[维护收敛约定](maintenance-convergence-2026-09-08.md)限制无证据的算法扩张。

## 公开仓库范围

仓库提供通用源码、合成测试夹具、接口说明及脱敏结果摘要。私有任务脚本、音视频、歌词/SRT、模型、人工标注、完整运行证据和本机路径不得随源码上传。文档中的`output/`引用是本地证据位置，不是公开下载地址；公开读者无法仅靠这些摘要重现私有歌曲指标。

2026-09-08 维护快照的历史验证为隔离Python3.12共1642项（1638通过、4项轻量环境缺音频依赖跳过），补充音频环境4/4通过；这些数字只描述当时快照。2026-09-09 editor-first / hybrid 改动后的最终回归必须以当前工作区实际测试结果为准，不能沿用旧快照数字替代。本页中的 KPOP130 MAE 与六任务恢复覆盖分别属于 historical development 与结构回归证据，不是新盲测总体准确率。

上传前再次全量回归出现1次Windows `WinError 5`：shadow将staging目录重命名为最终目录时被拒绝访问；该模块28项立即重跑全部通过。文件访问失败的具体外部诱因尚未证明，不能声称已根治。应保留失败产物并检查占用/权限后重试；这是运行可靠性限制，不是识别精度结论。
2026-09-09 最终封板事实分三层：hybrid production/materializer QA 已完成 editor-first 结构收口；KPOP130 viewer display v3 structural audit 通过（passed=true、errors/window/content-end/overlap均为0，10条 long-display warning，最大7563ms）；semantic/release gate 尚未通过（projection editor witness 2/12 fail，independent-audio final layer 12/12 fail，audio_anchor_count=0）。因此当前不声称总体 accuracy 或完整 release-ready；materializer 的 `publish_ready=true` 仅是该层状态，完整发布仍需 fresh independent audio evidence/fusion 与 semantic gate。
