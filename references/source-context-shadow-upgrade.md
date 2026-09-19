# 原曲上下文与区间联合升级

> 当前处于[维护收敛阶段](maintenance-convergence-2026-09-08.md)。本文保留已有实验入口供复现与排障；其选项均不自动成为生产默认，不再按历史“下一步”扩展模型或策略。


实现入口：`scripts/v4_upgrade_subtitles.py`。新分支的 job schema 为 `subtitle-shadow-upgrade-job-1.0`，必须同时声明 `execution_mode: shadow`。旧 `subtitle-upgrade-job-1.0` 保持生产重放行为，不能混入 `execution_mode`。本分支只生成实验字幕，不生成 `final.srt`、发布凭证或校准授权。

## 输入与执行

顶层必填 `task_manifest`、`report`、`srt`、`assets`、`assets_artifact`、`run`、`run_artifact`，每项均为 `{path, sha256}`。`run` 使用已有已解决的运行记录；资产、fine、run 的产物自哈希、输出哈希、任务身份和上游链接必须一致。

`sources` 为本次计算的 occurrence 列表；每项包含 `occurrence_id`、`fine`、`fine_artifact`，后两者同样为 `{path, sha256}`。可选 `mix_centres_seconds` 是预先选定的上下文映射核对位置，单位为最终混音的绝对秒。窗口未覆盖的字幕明确记录 `not_checked_at_this_interval`，不冒充已得到局部音频确认。可选 `cue_bindings` 提供逐 cue 的原文字符归属，每项为 `{position, canonical_character_ranges}`；范围含 `canonical_line_index/start_char/end_char`，字符区间为左闭右开。

默认从已绑定 canonical 索引和 editor-preservation 的规范化内容偏移恢复字符归属。无法证明文字保持、重复位置或字符边界的条目保留 KEEP。不能通过搜索同一句文字并选第一次出现来补索引。

`source_asr` 配置含 `model_path`，可选 `device`（默认 cpu）、`compute_type`（int8）、`beam_size`（5）、`language`（自动检测）、`cpu_threads`（4）。模型必须已经存在于本地；不会因执行该分支自动下载模型。缺模型或可选后端不可用时保留 KEEP；错误输入、推理失败、缓存损坏不会伪装成成功。语种影响识别提示，不决定可靠性权重。

可选 `decode_policy` 默认 `faster-whisper-s16-v1`，保持既有 source-observer-1.0 缓存身份。实验值 `pyav-float32-mono-peak-safe-2026-09-08-v1` 用 PyAV FLOAT 单声道重采样，峰值超过 1 时仅施加整曲统一增益，避免中间 PCM16 逐样本削波；输出 source-observer-1.1，记录解码峰值、增益及超范围样本数，缓存明确区分策略。该策略禁止注入自定义 audio_loader；任何解码语义变化必须更新 policy ID，缓存不自动绑定源码哈希。此选项尚无最终字幕准确率收益证明，不设为默认。

`source_cache_dir` 与输入、输出及 staging 均须分离。每次执行使用独占的 `.staging.<attempt-id>`，失败记录保留供诊断，再次执行同一输出目录不需要手工清理。缓存以实际源音频、模型文件、运行依赖及预处理身份绑定，完整写出后原子发布。识别覆盖整首已解码原曲，不用 LRC 的几秒窄窗限制歌词位置。原始 ASR 文字保留在私有缓存；实验产物携带摘要、字符归属及候选证据，因此也按项目的本地研究产物规则保管。

可选 `regression: {selection_lock: {path, sha256}, gold: {path, sha256}}` 复用旧人工记录。执行前只冻结其文件身份；候选生成、选择和 SRT 回读完成后才读取标注值。已有标注是历史回归数据，不能重新称为盲测。

命令仍为：

```text
python scripts/v4_upgrade_subtitles.py --job <shadow-job.json> --output-dir <new-output-dir>
```

## 算法边界

### 原曲三行 HuBERTFA 实验观察

#### 可选多句补齐

2026-09-08 实测否决将仅裁窗的 `anchored-block-v1` 提升为生产策略：新五首229行/219内部目标新增53行，端点MAE858.321ms、最大32160ms。正确外侧锚点仍可能让重复句跨长间奏串位。该身份保留用于重现失败；不能把候选覆盖增加写成准确率通过。

后续 `anchored-path-v1` 将完整源序列中合格行的区间用于解码状态时间约束（仍使用原1500ms余量），包括目标自身合格时的区间；SP状态不受词汇时间带约束。解码每步先算原转移，再限制目的状态，无法满足的路径失败。它不修改原三行结果或选择成本。开发可见的同53行原型重放MAE207.047ms、最大1867ms，仍有15个>500ms端点；这不是独立新测试，也不是封板依据。22行有自身合格时间带，31行只有周围证据，两组须分开报告。

该路径策略已接入，完整policy ID为 `source-context-hubertfa-anchored-path-lexical-only-no-ap-v1`。job还须给出 `time_band_decoder: {path, sha256}`，绑定 `lyric_aligner/alignment/hubertfa_time_bands.py`。请求记录解码器与后验边界检查身份、合格锚点来源和1500ms时间余量；旧策略不能夹带该覆盖项。模型逐记录切换路径解码器并在结束/失败时恢复原解码器，避免跨记录状态污染。后处理词区间超过自身时间带加声学帧量化余量时，该记录失败；此检查不根据gold误差调参。

`experimental_source_context_hubertfa.context_policy` 默认为 `three-line-v1`。新增显式 `anchored-block-v1`，要求 `acoustic_policy: lexical_only_no_ap`，使用独立策略 `source-context-hubertfa-anchored-block-lexical-only-no-ap-v1`。它保留原三行记录，仅为缺失目标增加多个相邻句共享的声学窗口；仍使用原 interval DP 联合选择，不新增按位移或修改数裁决的选择器。

同一完整 canonical packet 集和全曲 source-sequence 共识只计算一次。缺失目标取最近的合格左右 anchor（已选完整上下文或原全曲重复位置 promotion），不使用 raw singleton/partial 作为 anchor；一对失败后不继续扩大范围。共享窗口包括两个完整 anchor 及中间全部 canonical 行，只输出内部且完整绑定一条 canonical 行的 cue。外侧 anchor 本身不从这次推理取得字幕区间。

新增 block 固定限制为内部最多12行、总计最多14行/256词汇单元、含两侧各1500ms padding 的实际源窗口最多45000ms。它们是资源上限，随请求身份记录，不是精度阈值。锚点顺序、内部已有合格锚点的一致性、完整英语词典、FLOAT时钟及投影要求仍成立。相同锚点对只推理一次，多个内部目标按各自 segment index 提取末词 offset；不插值 ASR 缺失点。原三行路径不因这些新增 block 限额改变。

block 的两端实际 candidate、packet key、资格来源、canonical 范围和原曲观察身份进入紧凑台账。此策略仍是实验功能；正确的外侧 anchor 不保证中间没有版本删改、漏唱或重复段，完整强制词序也不是独立正确率证明。当前实施与验证协议见 `output/source_context_upgrade6_20260908/implementation_protocol.md`。

可选顶层 `experimental_source_context_hubertfa` 显式启用独立音素对齐观察。必填 `enabled: true`、`mode`、`adapter`、`model`、`model_config`、`model_version`、`model_vocab`、`dictionary`、`runtime`。前六个文件入口（adapter 至 dictionary）均为 `{path, sha256}`；`runtime` 仅含本地 Python 的 `{path}`，执行记录版本，不把解释器路径冒充完整环境哈希。推荐受版本控制的 adapter 为 `scripts/source_context_hubertfa_adapter.py`，不依赖私有 shim 的未跟踪实现。

`mode: report-only` 只生成观察台账；`mode: hfa-only-overlay` 额外生成独立 `hfa_overlay.srt/csv/selection/artifact`，其 KEEP 基线是 job 输入的当前成品。原有 `shadow.srt` 仍执行原 FW 选择策略；HFA 不混入 FW 候选成本。HFA overlay 的完整双端候选成本 0、KEEP 成本 1 仅用于实验几何选择，不是经校准的期望误差；`publish_ready` 保持 false。

可选 `acoustic_policy` 默认 `ap_default`，保持 `source-context-hubertfa-three-line-v1`；实验值 `lexical_only_no_ap` 使用独立 `source-context-hubertfa-three-line-lexical-only-no-ap-v1`。后者请求显式绑定空 `non_lexical_phonemes`、`pad_times: 3`、`pad_length: 3`，解决 AP 插入导致三个 padding 无相同音素组的问题。不能把关闭 AP 后的输出伪称为旧 AP 策略结果；它也不保证所有边界更准确。

本实现要求目标及左右邻行在整曲源观察中各有唯一、完整、正时长的位置；用完整三行的首尾外扩 1500ms，而非仅扩目标行。全 canonical 序列用于重复出现位置判断，保留原行索引；当前 cue 须完整对应一行。实际三行文字须被英语前端和固定词典完整表示，不用整曲 language 标签决定可靠度。可因此检查混语歌曲内独立的英语片段，但不声称具备日韩音素对齐能力。

三个独立源区间必须按 left→target→right 排序且不重叠，不能只用 min/max 包住相互矛盾的观察。目标依旧需要自身的完整上下文资格；邻行要求唯一的完整原始匹配，不额外要求邻行外侧的更远上下文合格。

sidecar 按原采样率读取 FLOAT 源音，核对解码时长与源观察时钟；模型只加载一次，逐记录隔离推理失败，批次超过 96 条时分块。显式设置 CPU intra-op 4、inter-op 1。请求自哈希绑定窗口、词汇、模型、配置、词典及有限 vendor 文件集合；返回的完整词序列通过共享 contextual interval API 验证后，才将目标双端经已验证 fine 逆变换投影到混音。跨 cut、歧义映射、缺词及异常结果不生成候选，保留具体不可用原因。

公开英语测试只支持合格目标上的条件收益，不是整曲覆盖率或最终节目精确率。证据与完整分母见 `output/source_context_upgrade5_20260908/english_evidence_summary.md`；失败重跑也保留原 staging，不能删除后只报告成功率。

prepared/ledger 的紧凑 schema 1.1 保留实际源观察文字、canonical 顺序、哈希范围、资格统计、源序列决策及已使用的三行候选证据，不再把所有 n-best trace 重复写入每条台账。原完整候选计算的摘要只用于复算核对，不冒充另一个可读取的原始 artifact；旧 1.0 报告保留。此序列化升级与 AP/no-AP 声学策略身份分别记录。

当前分块限制控制记录数量，不限制单个三行源窗口的时长或模型峰值内存。还不能据此宣称任意长音频的全量无人值守推理具有资源上界；本轮真实节目验证使用约 14 秒的合格三行窗口。

原曲 ASR 提供观察到的文字与词时间，`source_packets` 负责多行上下文及 canonical 字符归属。目标的精确词汇锚点与邻域的有界单调 edit trace 分开处理；邻句单个错字不再使整包消失。两处近似相同的候选、canonical 本身重复的整包和缺乏邻域覆盖的情况保留歧义。它不声称完成了音素强制对齐。目标首尾各自必须位于匹配的正时长观察词的外边界；词内切点、缺字、零时长和没有可证明位置的重复段不能凭插值补成端点。

当前固定策略要求至少一侧存在可用的相邻 canonical 上下文；整个输入只有一行时，即使该行完整命中也不会自动选择。这类输入继续保留已有流程结果。公开 PJS 单行短歌重放的 12 条均未选中（其中 2 条完整命中、10 条目标不完整），不能用未选候选的端点覆盖冒充该策略的实际输出能力。

映射复用已绑定的 effective fine timewarp，以 source→mix 的解析逆变换投影；CUT_AWARE 区间必须完全位于一个保留片段。新 contextual 观察使用 22050Hz、24秒目标窗、两侧2秒上下文、源位置±3秒搜索和全局/有效片段斜率周围的固定候选网格，按绝对 crop origin 恢复坐标。跨 cut 或速率断点的长窗口不适用。局部匹配分数不自动获得歌词边界权威。

联合选择器将一条 cue 的 start/end 作为同一个候选，绑定 lane、歌词出现路径和映射路径；保留已有重叠与同 lane/连续组起点顺序，不新增或加重未经结构支持的重叠。同组 KEEP 与候选共享已验证路径身份。双端有观察时可生成完整区间；也可将有观察的一端与该 cue 精确保留的另一端组成独立候选，不会用 LRC/邻句补造缺失的端点。每端来源、形状和被拒原因均进入台账；CUT_AWARE 的保留端也必须在同一保留片段。

当前策略 `source-context-interval-shadow-2026-09-08-v5-source-sequence` 的双端、单端、KEEP 成本分别为 0、0.5、1。这是显式无量纲实验排序，不是经过校准的毫秒期望损失。活动状态超过上限时返回完整 KEEP，并明确 `globally_optimal: false`。历史 v2/v3/v4 产物保留原身份。

v5将既有全最优source顺序消歧接入普通多语种路径，无新增job参数。每个occurrence的全部可解析目标包保留n_best=1024候选，验证同一observer自哈希，调用`resolve_source_sequence`；有截断、资源不足、身份/拓扑冲突或其它非complete状态时不新增全局候选，原局部合格候选继续有效。只有原`ambiguous_canonical_packet_identity`的指定候选可借全局promotion进入原投影和区间优化，局部`selected_candidate_id/full_context_disambiguated`不改写。ledger和artifact的`source_sequences`报告目标分母、未解析归属、完整候选数、解析状态和promotion数；逐条promotion绑定观察自哈希和sequence cache key。当前保留完整实验lattice便于复验，Gee台账约20.9→31.2MB，不是模型推理量增加。该功能不把语言、剪映时间或LRC插值作为词边界真值。

### 目标内部转录错误恢复

默认 source packet API 继续使用 `exact_target_only_v1` 和 schema 1.1。shadow 入口显式选择 `exact_or_outer_anchored_bounded_edit_v1`，新产物使用 schema `source-packet-candidates-1.2`，与旧缓存隔离。该策略只在没有上下文合格的精确目标时搜索容错目标；孤立的同文识别不能挡住有邻句支持的候选。

容错目标的首尾必须是正时长观察词的真实边界，且所有代价不超过最小字符编辑距离加 1 的完整路径都将这两个端点映射到同一观察字符。内部只能有一个编辑段，匹配比例至少 0.8，长度差至多 `min(8, max(2, ceil(目标长度 × 0.2)))`；适用邻域仍要求原来的覆盖和编辑段条件。内部有歧义的字符不声称唯一原文归属。多个合格源区间保持歧义，即使只返回一个 n-best 候选也不能自动选中。字符动态规划不等于声学或音素强制对齐。

有界目标策略 `source-multiline-context-packet-2026-09-08-v5-outer-anchored-target-edit-bounded-search` 每包最多检查 512 个候选跨度，单次动态规划最多 250,000 个逻辑网格单元，总动态规划最多 2,000,000 个单元；单次分配前检查大小，限额进入新策略的缓存身份。未命中首尾而进入 partial 诊断时，也先核算输入字符对的工作量，上限为 250,000，避免无候选诊断绕开预算。预算耗尽时返回 `source_packet_resource_limit`，清空包括先前暂存结果在内的所有候选并保留基线，不能在尚未检查潜在竞争位置时选择第一个结果。这些限制是运行资源约束，不是准确率校准；默认 exact 历史策略保持原行为。

## 输出与验收

输出 `shadow.srt`、精确对应的 `shadow.csv`、`candidate_ledger.json`、`selection.json`、`differences.json`、源观察摘要、可选上下文映射观察与 `historical_quality.json`，最后生成 `shadow.artifact.json`。产物记录输入及生产代码 SHA、覆盖、候选、选择、实际起止变化、未覆盖原因和耗时；写出后严格回读核对。

历史质量报告同时保留旧 editor、当前基线、候选上限、实际选择和最终回读。`paired_vs_current_baseline` 才是相对这一轮输入字幕的配对收益，不能把之前应用人工确认的累计收益算到新算法上。没有独立边界真值的节目只报告实际变化，不把映射一致性或与同一 ASR 的接近程度当作精确率。

本切片的验收范围是可执行、可复现的候选到字幕链路和可预测的回退。跨新歌、歌手、混音和语种的精确率仍需独立证据；不能据历史回归通过承诺所有未来输入永久最优。

## 逐段自动语种观察（显式实验选项）

`SourceObservationConfig(multilingual=True, language=None)` 启用 faster-whisper 的逐段语种检测；现有 shadow job 的 `source_asr` 可传递该字段，不新增 CLI 参数。默认 `False` 不向后端传该参数，保留原 source-observer-1.0 / FLOAT 1.1 缓存身份。启用时使用独立 source-observer-1.2 身份，且配置必须为布尔值、不得同时指定 language。返回的 `detected_language` 仍只代表初始检测，启用模式记录 `detected_language_scope=initial_detection_only`，不推造各段语种。此选项不会赋予某语种更高权威，也不自动改变生产默认；实测协议与结果保存在 `output/source_context_upgrade12_20260908/`。
