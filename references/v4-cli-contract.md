# Lyric Aligner V4 CLI Safety Contract

2026-09-10 source-clock authority 1.1：`v4_audit_semantic_sync.py` 的 authority 路径必须同时提供 `--source-clock-map`、`--source-clock-promotion-analysis`、`--source-clock-promotion-selection`、`--source-clock-promotion-protocol`；`v4_validate_release.py` 对应参数在四者前加 `semantic-`，即 `--semantic-source-clock-map` 等。四份文件作为受保护输入，QA/release 记录各自 SHA；selection 身份/transform、analysis ledger 指标及 frozen protocol verdict 必须一致。旧无 authority 路径不变；旧 1.0 authority 报告不得直接作为 1.1 release 依据。当前 a20 重放仍 7 首 final FAIL，见 [交接第 9 节](oumei140-a20-source-clock-upgrade-handoff-2026-09-10.md)。

同轮修正增加独立 `context_policy: anchored-path-v1`：合格源时间带参与声学解码路径，仍要求 `lexical_only_no_ap`，复用原 shadow CLI 与单独 HFA overlay。旧 `anchored-block-v1` 可重放，但新公开测试已否决其直接生产应用。时间带使用既有1500ms余量，不是开放的调优参数；未启用仍保持原三行行为。

2026-09-08 第六切片增加 HFA job 内可选 `context_policy: anchored-block-v1`（默认 `three-line-v1`），要求已有 `lexical_only_no_ap` 声学策略，复用同一 shadow CLI。最近两侧合格 anchor 之间共享推理以补齐相邻句；固定12内部行/256词/45秒（含padding）资源上限，只作用新增block。外侧anchor不输出，旧三行候选保留。字段、证据与输出仍遵循 [原曲上下文与区间联合升级](source-context-shadow-upgrade.md)。

2026-09-08 新增显式 `experimental_source_context_hubertfa`，绑定本地 adapter/model/config/VERSION/vocab/dictionary，runtime 按路径及实测版本记录。`report-only` 生成独立观察，`hfa-only-overlay` 额外生成独立 HFA SRT，原 FW shadow 输出路径和策略不混合。按候选隔离模型推理异常；输入身份或批次协议错误仍严格失败。完整字段、上下文与时钟约束见 [原曲上下文与区间联合升级](source-context-shadow-upgrade.md)。

2026-09-08 当前 shadow 策略 v4-bounded-target-search 内部启用有界目标编辑恢复，packet schema 1.2；job 不新增放宽阈值的参数。默认精确 packet API 及历史产物读取保持兼容。搜索预算耗尽以 `source_packet_resource_limit` 保留基线，完整协议见 [原曲上下文与区间联合升级](source-context-shadow-upgrade.md)。

2026-09-08 新增原曲上下文实验分支：同一 `v4_upgrade_subtitles.py` 入口接受 `subtitle-shadow-upgrade-job-1.0` + `execution_mode=shadow`，生成真实 shadow SRT/CSV、候选台账、联合选择和配对评估。job、缓存、artifact 链及输出契约见 [原曲上下文与区间联合升级](source-context-shadow-upgrade.md)。旧 schema 不接受 execution_mode，以免实验请求误入 final 写出。

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

`v4_execute_asr_evidence.py --retry-model-id <different-model>` 对已选择的 faster-whisper 作业自动执行单次局部补识别；不传时保持原单模型入口。第二模型必须与首轮不同；完整首轮不加载第二模型。输出包含 routing/composition policy、执行数、采用数及隐私过滤后的两次观察；只有正常一份ASR family进入fusion。Qwen入口不接受此参数。


Independent Fine 评测新增 `--observer contextual`，原 `independent` 默认兼容历史产物。contextual 校准→冻结策略→holdout protocol→verdict 必须保留算法版本、代码 revision 与 sample rate；不允许其他 observer 或采样率复用锁。`v4_select_independent_fine_holdout_pairs.py --exclude-source <path>` 可重复，按 SHA 和去序号/忽略大小写的歌曲名排除已使用歌曲，并在选择产物记录排除来源。此 CLI 只评测音频映射，不能据此授予歌词起止点写回权限。


`v4_run_independent_fine_benchmark.py --observer contextual` 运行上下文候选；默认 independent 保持旧算法重放。contextual holdout 必须携带自己的 observer_variant 和 observer_implementation_revision 冻结身份，不得复用旧observer的holdout协议。输出observer_version/variant与完整实现hash；无生产时间写回授权。

新 ASR 输出策略为 `bounded-lexical-match-2026-09-07-v4-edge-coverage`：FW/Qwen 首尾覆盖分别输出，未知端点为 null。Fusion策略 `evidence-fusion-shadow-2026-09-07-v5-edge-coverage` 可额外输出 `canonical_onset`，含start_ms、support_score、job_id、backend及canonical_prefix_word_span basis；它不构成完整boundary_ms，不增加投票family。历史无覆盖字段仍可读取，但新fusion只保留未知观察，不授予新的整句或句首证据效力；需绑定词级文本重算或重新识别。

semantic-sync-qa 新增 `diagnostics_policy=semantic-layer-errors-1.0`，标识 projection/final 层错误分开报告；原 audio_evidence_policy、发布要求和历史读取保持兼容。

## 按曲保留编辑器时间

`v4_upgrade_subtitles.py` 的独立 job 可含 `editor_preservation: {run, run_artifact, assets, assets_artifact, occurrence_id}`；路径相对 job 解析。该阶段不与 calibrated/human/gap 或旧 QA 输入叠加，输出新 final.srt、final.csv、preservation.artifact.json 与 upgrade.artifact.json，必须另做匹配新成品的 QA。缺少完整 canonical 字符流或出现跨歌曲边界时拒绝写回，不自动发起人工任务。

独立入口为 `python scripts/v4_preserve_editor_occurrence.py --task-manifest <manifest> --srt <baseline> --audit <csv> --run <run> --run-artifact <artifact> --assets <assets> --assets-artifact <artifact> --occurrence-id <id> --out-dir <new-directory>`。它恢复 manifest 中 source_srt 的该歌曲 cue 时间，校正完整 canonical 文字，重建来源身份；不会把模型时钟候选或旧边界 authority 转入新产物。

## 可选本地 Qwen ASR evidence

`v4_execute_asr_evidence.py --backend qwen3_asr --model-id <local-ASR-directory> --alignment-model-id <local-aligner-directory>` 使用已安装的 Qwen runtime；默认仍为 `faster_whisper`。Qwen 专属 `--qwen-dtype` 为 `bfloat16` 或 `float32`。两个模型必须是现有本地目录，入口不负责下载或安装。保留原有 task/plan/run/artifact 参数、精确 job 选择与 lineage 校验。

Qwen 不接受 canonical 文本提示；forced timestamp alignment 只消费独立识别文本，两者计为一个 ASR family。未知概率输出 null，缺少句首/句尾分别不输出对应 canonical 边界；fusion 的完整区间比较要求两端均覆盖。原始文字仍仅由 `--include-private-text` 显式开启。输出路径在模型执行前与 manifest、上游及本地模型目录隔离校验。该观察入口不构成自动回退、跨模型投票或生产边界写回授权。

两个 ASR 后端的新产物均带 `word_match_policy_id=bounded-lexical-match-2026-09-07-v3-ambiguity`。并列最佳词段匹配输出 `canonical_match_ambiguous=true` 与仅含时间及文本 hash 的 `canonical_match_candidates`，唯一 start/end 保持 null；fusion 不以 segment envelope 替代该未知边界。历史 evidence 不批量迁移。

## 人工 gap 导入与离线 A/B 核对

`v4_upgrade_subtitles.py` job 可含 `gap_review: {lock, review, prior_receipt}`，三者均为路径，解析方式沿用 job 契约。该阶段单独从锁定的 report/SRT 开始，不能与 calibrated_stages 或 human_confirmations 在同一 job 叠加；prior_receipt 必须为原始、可重放的 human reuse 1.1。输出 `gap_review/gap.artifact.json`，保留旧确认链且只新增精确确认的边界。`human_confirmed=false` 和非 present 记录不自动写回，presence=absent 不等同删除授权。

独立 CLI：`python scripts/v4_apply_gap_review.py --manifest <manifest> --lock <lock> --review <review> --report <csv> --srt <srt> --prior-receipt <receipt> --output-dir <new-dir>`。

试听页：`python scripts/v4_build_gap_ab_review.py --job <gap-job.json> --output-dir <new-dir> --expand-case <id>`，expand-case 可重复。扩大音频窗口会产生新的 lock/clip hash，原同片段的确认可保留，窗口变化的目标需要重新核对。导出仍为 human-gap-boundary-review-1.0，绑定新 lock；旧导出文件不被覆盖。页面在本地工作，播放位置与边界编辑分离，手动编辑后撤销确认。

状态：mandatory  
适用范围：所有会写入 production/evaluation artifact 的 `scripts/v4_*.py` CLI。

### 成品质量研究入口（2026-09-07）

`python scripts/v4_reuse_human_boundaries.py --task-manifest MANIFEST --selection-lock LOCK --gold GOLD --report CSV --srt SRT --output-dir NEW_DIR`

将已有独立真人确认应用于同一 final mix、同一歌词目标。先复核 manifest fingerprint/audio/source hashes、gold/lock、旧 report、当前 CSV/SRT，再生成新目录。兼容 lossless internal split，只有其最外起止边界可变；已在人工容差内的值保留。起止组合的几何约束分别裁决，安全的一边不被另一边冲突阻断。产物包含 per-boundary record id、old/new、gold/source/code hashes 和完整决策，不设置整行 `manual_verified_interval`，不授予任何模型权限。支持当前 Human Anchor gold schema；无匹配目标不会强行迁移确认。已有 output/staging 拒绝；IO 失败的 staging 保留作诊断，未完成 artifact 不提升为结果。整包 release 不由该入口判定。

`python scripts/v4_evaluate_product_boundaries.py --selection-lock LOCK --gold GOLD --predictions START_JSON END_JSON --final-srt FINAL --final-report CSV --output NEW_JSON`

读取已绑定的历史 outer human gold 和可选 observer predictions。final SRT/report 必须成对提供且全文一致；split 通过原 cue ownership 与连续 canonical text 关联。输出是历史回归诊断，oracle 不可用作生产 selector；原始误差与扣标注容差后的误差分别报告。缺失的 selector 不被虚构成 KEEP，写回损失保留 unknown。输出仅允许新文件，保护直接输入、lock 内 lineage path 和 canonical lyrics directory。

`python scripts/v4_build_outer_validation_pack.py --pool POOL_JSON --output-dir NEW_DIR`

pool 每条须有 `id / recording_group / audio_path / audio_sha256 / text / start_ms / end_ms`。recording_group 必须由调用方核实为跨 occurrence/混音可归并的原始录音身份；已揭晓 gold 的录音应由 pool producer 排除。默认冻结 12 组、每组 5 clip，8 组 calibration、4 组 holdout。先验证 output-tree ownership 和音频 SHA，再输出 clip、selection lock、空白 annotations.csv；已有目录拒绝。标注时间以 clip 为零点，selection 保存精确起始采样帧。CLI 只准备问题，不生成真人标注、不签发 production authority。失败遗留目录只是部分输出，未完成 selection lock 不可作验收包，重试应使用新的输出目录。

## 1. Artifact writer 的路径不变量

任何 v4 artifact writer 在第一次写文件之前都必须 fail closed 验证：

```text
outputs ∩ task-manifest-bound inputs = ∅
outputs ∩ direct CLI inputs = ∅
outputs ∩ discovered upstream/materialized inputs = ∅
all output paths are pairwise distinct
```

其中 task manifest 的 directory input 保护的是**整棵目录子树**，不是只保护目录名或当前已存在的成员：

- manifest 中已 fingerprint 的每个文件成员会显式进入 protected input 集合；
- 即使 `lyrics_dir/new-output.json` 之类目标文件事先不存在，只要位于受保护 input directory 下也必须拒绝；
- 这样 artifact writer 既不能覆盖已有输入，也不能通过新增文件静默改变目录递归哈希，使刚验证过的 task manifest 立即失效。

会动态生成多个子文件的 materializer/orchestrator 还必须证明**整棵 output tree 与输入双向不相交**：output tree 不得位于 protected input directory 内，任何 protected input file/directory 也不得位于 output tree 内。输入 run payload 中声明的全部 `*_path` lineage 在首次 `mkdir`、子进程或 materialization 前都视为 protected input，即使该路径当前不存在。

路径保护只负责 ownership/safety，不改变 artifact identity、timing、text、review 或 release authority。

## 2. 当前 Max 必经 CLI

### `v4_run.py` / `v4_run_optimized.py` / `v4_run_legacy.py`

三条 public production orchestration entrypoint 都必须在第一次 filesystem mutation 前证明 `--out-dir` 的整棵 tree ownership：

- canonical `v4_run.py` 必须在 `OutputRunLock` 创建 output directory 或 `.v4-run.lock` 前检查；
- direct optimized entrypoint 必须在 `cache/`、verified-input session、stage directory 创建前检查；
- direct legacy entrypoint 必须在 stage directories 创建前检查；
- task manifest、所有 manifest-bound input roots/subtrees，以及显式 `--profile`、`--language-map`、`--middle-cut-map`、`--lyric-role-map`、`--source-clock-map` 都属于 protected inputs；
- `4.0.0a14` 起，若 task-local `qa/v4_run_config.json` 存在，三个 public run entrypoint 必须在任何 output mutation 前自动发现并验证它；该 config 自身也属于 direct protected input；
- run config 绑定 exact task fingerprint，并记录 `profile/language_map/middle_cut_map/lyric_role_map` 的 path/size/SHA；缺失 semantic CLI 由 wrapper 自动展开，显式 CLI 与 config 不一致、绑定文件变化或 config-null 角色被临时填入时 fail closed；`v4-run-config-1.0` 不新增 `source_clock_map` 字段，避免破坏历史 config 指纹，a20 的 source-clock 首次接入仅接受显式 `--source-clock-map`；
- wrapper-only `--run-config` 只用于 preflight/auto-expansion，ownership gate 完成后必须从 argv 移除，再进入原 production parser；不存在 run config 的 legacy task 保持旧显式 flag 兼容；
- output tree 位于任一 protected input 内，或 output tree 反向包含 protected input，都必须 fail closed。

Legacy/optimized 原 orchestration implementation 以 blob-identical `_v4_run_*_impl.txt` internal source resource 保存，由安全 public wrapper 在 preflight 后加载。internal resource 不是受支持 CLI，也不得作为绕过 preflight 的第二入口。该拆分不改变 orchestration algorithm、monkey-patch compatibility、readiness 或 release authority。

### `v4_resolve_assets.py` / `v4_coarse_align.py` / `v4_fine_align.py` / `v4_probe_transition.py`

四条 primary-stage CLI 即使脱离 orchestrator 被直接执行，也必须在原实现第一次 artifact/cache write 前完成 ownership preflight：

- 所有 `--out` / `--artifact-out` 必须 pairwise distinct，且不得覆盖 task manifest、manifest-bound input subtree 或直接 upstream/config input；
- TrackAssets/coarse 等 JSON 输入中递归声明的 `*_path` lineage 同样属于 protected inputs；
- `v4_coarse_align.py` 的 `--feature-cache-dir` 是动态 writable tree，必须与 protected inputs 双向不相交；未显式传参时，也必须对由 `--out` 推导出的 production cache tree执行相同检查；
- output 文件只是单文件 ownership，不因为其父目录包含别的合法 stage artifact 就误判整棵父目录为 writer-owned；
- public `v4_*.py` 是唯一受支持入口，原四份 stage implementation 以 blob-identical `_v4_*_impl.txt` internal source resource 保存，不能通过 resource 绕过 preflight。

当前冻结 implementation blob：

```text
resolve_assets    162b1d9dfc25b3ae2e5995d0e790c47dbcc931f8
coarse_align      735c9aa1a98607953206aedbe1264f7680b5c145
fine_align        005ba2744ba299ded2eed4c7ee7a8c9511448706
probe_transition  eabf2b2f10f67d1057adab992b395ee562a1f8c4
```

该 gate 不改变 asset resolution、Source-to-Mix、Fine、transition score/margin、TimeWarp 或 readiness authority。

### `v4_review.py`

`template` 输出不得覆盖：task manifest/任一 task input、production run、production run artifact。

`apply` 还必须保护 review decisions 输入；reviewed run 与 review artifact 两个输出也不得同路径。

人工 review 的 allowed actions、issue identity 和 replay semantics 不因路径保护改变。

### `v4_rebuild_cut.py` / `v4_recompose_overlap.py` / `v4_compose_materializations.py`

三条 review 后 materializer 在任何目录创建、Fine 子进程或 JSON materialization 前必须完成 output-tree preflight，保护 task inputs/subtrees、直接 run/artifact 与 TrackAssets 输入，以及输入 payload 中递归声明的全部 `*_path` provenance。

公开 `v4_*.py` 是唯一支持的 CLI entrypoint。原 materializer 算法 source 以 blob-identical 的 `_v4_*_impl.txt` internal resource 保存，由通过 preflight 的 wrapper 以非 `__main__` 名称加载；这些 resource 不是 CLI、不得直接执行。这个拆分只把安全 guard 放到原实现第一次写入之前，不改变 cut/overlap/combined 算法。

### `v4_render.py`

四个输出：

```text
final SRT
audit CSV
QA JSON
final-render artifact
```

必须彼此不同，并不得覆盖：task manifest/任一 task input、run、run artifact、TrackAssets、asset artifact，以及 run 中实际读取的每个 canonical timeline / timeline artifact。

该检查必须发生在 `_write_srt()` 或任何其他 materialization 之前。当前 renderer 仍是 `canonical_line_evaluation_only`；路径保护不授予 `editor_reconciled`。

决定 render eligibility / materialization 完整性的以下计数必须是真正 JSON integer，不能依赖 `int(...)` coercion：

```text
review_resolution.remaining_issue_count
overlap_recomposition.remaining_issue_count
cut_rebuild.remaining_issue_count
cut_rebuild.canonical_fragment_issue_count
cut_rebuild.rebuilt_occurrence_count
combined_recomposition.remaining_issue_count
combined_recomposition.combined_occurrence_count
```

相关 run artifact 的 `normalized_config` 也必须确实为 JSON object。

### `v4_editor_cue_reconcile.py`

reconciliation output/artifact 不得覆盖 task input 或 canonical evaluation SRT/audit/QA/final-render artifact。它继续保持：

```text
segmentation_authority = editor_reconciliation_evaluation_only
production_authority_granted = false
```

### `v4_apply_display_policy.py`

Display-policy materializer 只能消费已获得 `editor_reconciled`、`publish_ready=true` 且无 `release_blocked_reason` 的 exact production final-render。它不得改变 cue count/number/start、occurrence/track/canonical-line identity；viewer-facing text 可以按 policy 改写。timing 默认完全冻结；只有 `trim_extreme_unknown_end_v1` 可以 shorten-only 修改 end，绝不允许延长 end 或移动 start。

显式模型 override 必须绑定 task fingerprint 与 `occurrence_id + track_id + canonical_line_index + expected_text`，且明确 `confidence=high`。expected text 不匹配、override 未命中/重复命中、policy task identity 不一致均必须 fail closed。自动敏感词 profile 只允许窄 strong-profanity 规则；语境相关词不能由该 profile 自动删除或替换。

`trim_extreme_unknown_end_v1` 的 `source_end_basis` 只允许 `next_line_start`；`source_duration_at_least_ms` / `max_display_hold_ms` 必须是真正 JSON integer，且 max hold 必须严格小于 trigger。满足 gate 时新 end 只能等于 `start + max_display_hold_ms`；`open_end`、显式 end authority 与未达到 trigger 的 cue 必须保持原 end。

输出 audit 必须保留 canonical 原文及其 hash，以及原始 source start/end；同时把 `text/start_ms/end_ms` 绑定到最终 display 值，记录 display timing change reason，并重算 `text_sha256` / `cue_id`。该阶段生成新的唯一 `final_render` artifact，以上一层 production final-render 为 upstream；release 时只提交新的 display final-render，现有 exactly-one-final-render contract 不变。

### `v4_audit_semantic_sync.py`

该 CLI 是 release 前的只读语义时间轴审计，不修改 final SRT、不授予新的 timing/text/segmentation authority。它必须绑定 exact task manifest、同 task run、同 task evidence fusion、exact final SRT 与 exact final audit report。正式 timing witness 来自独立 audio-semantic evidence：优先使用 canonical text 对实际 source audio 的 forced alignment 再经 source-to-mix 投影；ASR 仅允许使用 canonical word-span，且只有 editor/Jianying witness 经文本覆盖证明可靠时才可作为 fallback。source editor/Jianying SRT 只做 auxiliary lexical/timing witness，永远不是 canonical text truth，也不能单独授予 release authority。

输出 `semantic-sync-qa-1.1` 必须包含 task fingerprint、algorithm version 与 `audio_evidence_policy=forced_alignment_or_asr_plus_reliable_editor_v1`，并精确绑定 source SRT/audio/song-list/run/evidence-fusion/final-SRT/final-report SHA-256。projection 与 final 任一层 failed、任一歌曲独立音频锚点覆盖不足、median absolute onset error 超阈值、大误差比例超阈值、forced/ASR family conflict、editor-only 或 stale binding 时，顶层 `passed` 必须为 false。

### `v4_validate_release.py`

release manifest 不得覆盖 task manifest/任一 task input、final SRT、audit CSV、QA JSON、run、semantic-sync fusion、semantic-sync QA 或任何 upstream artifact。

V4 release 只有在唯一 exact final-render artifact 的 production authority **三层一致**时才可继续：

```text
normalized_config.segmentation_authority = editor_reconciled
evidence.segmentation_authority          = editor_reconciled
evidence.publish_ready                   = true
exact QA.segmentation_authority           = editor_reconciled
exact QA.publish_ready                    = true
```

artifact evidence 或 exact QA 任何一处仍有非空 `release_blocked_reason` 时必须 fail closed。不能只把 `normalized_config` 改成 production authority，而让 evidence/QA 仍保持 evaluation-only；这种半升级状态不得生成 release manifest。

之后仍需通过既有 exact SRT/audit/QA hash binding、task fingerprint、algorithm version、calibration profile 与 release QA 完整检查。路径保护和三层一致性检查都不创造新的 segmentation authority；它们只验证真正的 production materializer 是否给出了完整一致的证据。

### `v4_audit_final.py`

该 CLI 是 diagnostic-only writer，不生成 artifact 或 production authority。它要求 final SRT/audit exact binding 与 publish-ready QA，读取同 task run 的 authoritative timeline windows、`content_end` 和 confirmed-overlap regions；输出 JSON 不得覆盖 task input、run/final/report/QA，也不得覆盖 run 递归声明的任何 `*_path` lineage input。结构错误包括非单调 final file order、非正 cue、occurrence-window/content-end 越界、same-occurrence overlap 与没有 exact confirmed region 覆盖的 cross-occurrence overlap。长/极端驻留只是 warning，不自动阻断 release；该诊断也不能替代 `v4_validate_release.py`。

## 3. JSON 类型必须 fail closed

Release/evaluation authority 不能依赖 Python 的宽松强制转换。

`review_candidate_count` 的零值必须是真正 JSON integer `0`：

- `0`：有效；
- `false`：无效；
- `0.0` / `0.5`：无效；
- `"0"`：无效；
- `null`：无效。

相同原则适用于上面列出的 render authority counts。同理，artifact 顶层、`normalized_config` 与 production release 需要消费的 `evidence` 在 object contract 位置必须确实是 JSON object；畸形、自洽重哈希的 artifact 也只能得到受控 fail-closed 错误，不能靠 `AttributeError` 等未处理异常泄漏出契约。

## 4. 回归要求

公共 regression 只能使用 generic synthetic fixtures，并至少证明：

- task directory member 会进入 protected path 集合；
- output 指向 task input directory 下一个尚不存在的新文件时同样被拒绝；
- run/materializer output tree 不能包住 direct/lineage input，也不能位于 task input subtree；
- canonical / optimized / legacy 三个 run entrypoint 的 unsafe output collision 必须在任何 output directory、lock、cache/session 或 stage write 前失败；
- task-local run config 必须自动展开已绑定 semantic inputs；文件内容变更、task mismatch、CLI/config drift 与 config-null 临时注入必须在 output write 前失败；
- resolve/coarse/fine/transition direct CLI 的 unsafe output collision 必须在 stage artifact 写入前失败；
- coarse feature-cache tree 不能进入或反向包含 task/upstream inputs；
- primary-stage `--out` 与 `--artifact-out` 必须 pairwise distinct；
- materializer collision 在原实现首次写入前失败，被保护输入字节不变；
- `--help` 与正常 run/asset/coarse/fine/transition/cut/overlap/combined E2E 不因安全 wrapper 退化；
- review template/apply 的碰撞不会改变被保护输入字节；
- release manifest 碰撞不会改变被保护输入字节；
- malformed review/rebuild/render authority count 被拒绝；
- final-render config/evidence/QA authority 任一层不一致时 release 被拒绝；
- 正常 render/review/release/evaluation 路径不因 guard 产生 false positive。

真实歌曲名、歌词、cue、timestamp、audio 或私有路径不得进入本文件或公开测试。

## `v4_upgrade_subtitles.py`：一次执行已验证升级

```powershell
python scripts/v4_upgrade_subtitles.py --job <job.json> --output-dir <new-directory>
```

job schema 为 `subtitle-upgrade-job-1.0`，必填 `task_manifest`、`report`、`srt`。job 内路径相对 job 文件目录解析；manifest 自身仍遵循项目原有路径契约。可选 `calibrated_stages` 为顺序数组，每项含 `mode`（outer/internal）、`plan`、`evidence`、`decisions`、`bundle`；可选 `human_confirmations` 含 `selection_lock`、`gold`、`predictions` 数组。未提供证据时保持输入 SRT 字节；不能由配置布尔值授予模型权限。

输出 `final.srt`、`final.csv`、`upgrade.artifact.json`；有 human gold 时另含 before/after quality。cue 数变化时，按位置计算的 start/end/text 变化数为 null，不能把错位 zip 当作准确率改善。输出目录必须全新。未配置 QA 时 `publish_ready=false` 表示未验收；配置 QA 后如实消费其结果，不能由配置布尔值或修改数量授予 ready。


升级 job 可选 `qa` 对象（`audio_alignment`、`manual_overrides`、`regression_cases` 路径），自动衔接旧正式 QA；receipt 由内部阶段传给 `redo_karaoke_pipeline.py qa --boundary-confirmations <reuse.artifact.json>`。receipt 1.1 必须通过完整输入重放与最终 SRT/CSV 读回，旧 1.0 不用于新增 QA authority。QA 的 out/out-review/release-manifest（含默认值）不得覆盖确认凭证及其依赖。display audit 不作为 acoustic stage 输入，须提供精确配套的 pre-display report/SRT。


### 无损非词汇人声显示合并

`v4_group_vocalization_display.py --receipt <gap.artifact.json> --report <final.csv> --srt <final.srt> --output-dir <new-directory>` 输出独立 `display.srt` 和 `display.artifact.json`。输入须通过 gap 确认链及完整 CSV/SRT 重放。只合并满足同曲、连续 canonical、相同重复形式、已确认间隔覆盖和长度限制的行，保留文字与两端。输出不充当新声学报告，不授予 publish_ready。`verify(output_dir)` 重新推导分组，核对完整 SRT、成员哈希和输入身份。

单次升级入口在 `gap_review` job 中自动调用上述显示阶段，输出位于 `display/`，并在目录迁移后重新验证。`upgrade.artifact.json` 的 `display_derivative` 单独记录相对路径、哈希、分组数、cue 数及未获得 release authority 的范围；无 gap review 时为 null。`final.csv` / `final.srt` 和 QA 继续成对绑定声学阶段，显示版不得沿用该 QA 宣称已发布就绪。

### gap 试听页命令

`v4_build_gap_ab_review.py --job <job.json> --output-dir <new-directory> --expand-case <case-id> --context-seconds 30` 从任务 final mix 扩展选定 case 前后上下文，默认单侧 30 秒，保留旧文件与输入导出。页面的试听长度与音频裁切长度分别配置；支持选点前/后独立播放、A/B 停顿、时间直接输入、可配置步长和预设。扩大音频不自动新增人工确认；未完成试听不阻止独立算法开发。
# 2026-09-08 FLOAT source decode 实验选项

shadow job 的 `source_asr.decode_policy` 支持默认 `faster-whisper-s16-v1` 和显式 `pyav-float32-mono-peak-safe-2026-09-08-v1`。后者只改变源音 ASR 前处理，输出 observer 1.1 并隔离缓存；详情见 `source-context-shadow-upgrade.md`。不改变生产 final 授权。
# 2026-09-08 通用英文词典补全入口

`python scripts/v4_build_english_lexicon.py --base-dictionary BASE --vocab VOCAB --cmudict PINNED_CMU --output-dictionary NEW_DICT --output-manifest MANIFEST` 构建独立完整字典。`--cmudict`可省略；原有词条保留，新增仅允许末尾撇号词形别名及直接外部词典记录，歧义与不支持音素拒绝。显式`anchored-path-v1`作业须同时绑定`dictionary.path/sha256`和`dictionary_manifest.path/sha256`；adapter校验来源并重建字典，真正推理使用验证后的新字典。旧请求仍固定原词典。外部词库许可和固定版本随实验来源收据保留。该入口的运行成功只代表词库可用，精度需要真实对照。
# 2026-09-08：实验相邻共同窗口入口

第九切片增加可选 `experimental_exact_source_anchors: {"policy_id":"source-exact-word-run-legacy-bracket-v1"}`，必须同时开启原 joint 字段。它只为 joint 请求提供全流唯一、逐词精确且被旧锚夹持的源锚，不改旧 HFA 请求。共同请求策略独立为 `atomic-adjacent-pair-exact-source-v1`，overlay 策略为 `experimental atomic-adjacent-pair-exact-source-v1`；新源锚单独放在 `context.exact_source_anchors`，不能伪装为旧 full-context packet。旧调用未设置该字段时行为不变。

身份分层：job 与共同请求的 `joint_proposal_policy_id` 使用 `atomic-adjacent-pair-v1`；overlay 的 materialization `strategy_id`/`policy_id` 为 `experimental atomic-adjacent-pair-v1`，并另存 `provider_joint_policy_id`。它们不是产品版本，也不改旧产物身份。

`v4_shadow_upgrade.run_shadow_job` 可选 job 字段 `experimental_joint_source_context` 仅接受 `{"policy_id":"atomic-adjacent-pair-v1"}`。要求现有 HuBERTFA 配置同时为 `hfa-only-overlay` 和 `anchored-path-v1`，否则报错；字段省略时无共同窗口推理。输出独立 `joint_overlay.csv`、`joint_overlay.srt`、`joint_overlay.artifact.json`、`joint_preparation.json`、`joint_pair_outcomes.json`、`joint_overlay.selection.json` 及绑定的 batch。均为实验产物，`publish_ready=false`。旧 overlay 和普通 shadow 保留各自身份。

### 2026-09-08 局部 editor restoration 作用域修正

`v4_preserve_editor_occurrence.py --canonical-region FIRST END` 仍接受 Smart 预处理后、唯一精确 whole-cue 区域的半开位置范围。未选中歌曲边缘 cue 跨 occurrence 不再提前否决内部区域；实际选中的 cue 跨界仍拒绝。整段请求行为不变。区域 ownership 字符偏移写入完整绑定 canonical 歌词坐标，支持下游 shadow 重建跨行 cue；不能以原始 editor 的区域范围代替 Smart 后的范围。CLI 无新参数、依赖或默认声学策略。旧文件不批量重写，所有恢复输出到新目录并保持 QA 未封板状态。

## 2026-09-08 第十一切片：普通多语种顺序消歧

无新增CLI/配置项。`subtitle-shadow-upgrade-job-1.0`普通路径产生策略`source-context-interval-shadow-2026-09-08-v5-source-sequence`；读取旧产物身份不改写。观察为observed时须有真实observer自哈希（忽略cache_hit后验哈希）；普通source摘要提供该绑定，完整目标包的全局消歧状态进入ledger/artifact的source_sequences。仅complete且原duplicate歧义候选命中promotion才新增准入，原局部资格、KEEP及几何约束不变。产物仍为shadow.srt/CSV，不是生产release。

### 2026-09-08 source observer 逐段语种选项

source shadow job 的 `source_asr.multilingual` 可显式设为布尔 `true`，同时 `language` 必须为 `null`，启用逐段自动检测。未提供/false 保留旧行为和旧缓存身份；true 使用 source-observer-1.2 独立身份。无新增命令行 flag，未改变生产默认。`detected_language` 仅是初始检测，不是全曲逐段语言清单。

### 2026-09-08 维护：请求字段不能静默失效

普通升级job与显式shadow job仅接受各自实现声明的字段，未知或误拼字段在输出/推理前报错并列出字段名。普通job的editor_preservation、calibrated_stages、human_confirmations、gap_review和qa，以及shadow的source_asr与sources项同样核对键名。source_asr.multilingual为正确入口；source_config、human_confirmation、canonical_regions等不是别名，不能被默默当作默认或无证据作业。未提供的可选项仍按原默认执行，历史产物读取身份不改。

区域恢复重放兼容既有行索引和已写出的完整canonical字符归属；同一区域再次处理的验收是最终SRT保持，而非再次产生字幕移动。当前开发范围见[维护收敛约定](maintenance-convergence-2026-09-08.md)。


#### 2026-09-09 editor preservation batch / hybrid contract

`subtitle-upgrade-job-1.0.editor_preservation` 必须提供 `run`、`run_artifact`、`assets`、`assets_artifact`。默认 `scope="single_occurrence"`：要求 `occurrence_id`，`canonical_region` 省略/`"auto"` 时自动选择本 occurrence 最大安全区域，显式 `[first,end]` 继续支持，显式 `null` 仍是严格 whole-occurrence。`scope="all_occurrences"` 时禁止 `occurrence_id` 与 `canonical_region`，可提供 `max_passes_per_occurrence`；materializer 按 run occurrence 顺序重复 auto restore 直到每首歌稳定。batch 输出仍 `publish_ready=false`，必须进入 fresh product QA / hybrid authority 链。

region 模式允许 crossfade 导致的全局 occurrence 非连续，但实际选中区域必须完整连续；whole-occurrence 仍拒绝非连续 ownership。nonlexical editor cue 不参与 exact canonical stream 匹配并保持原样。run 中 timeline/timeline artifact 的仓库相对路径统一相对 repository root 解析，不依赖调用 cwd，`..` 逃逸拒绝。

`v4_materialize_editor_reconciled.py` 的 production rebuttal 还必须提供 `--preserved-srt / --preserved-report / --preservation-report / --preservation-artifact`。这些输入必须来自 exact-bound `editor_preservation_batch`，至少包含一次 restore，且完整 canonical character coverage 验证通过；成功模式为 `hybrid_editor_preservation_after_editor_topology_rebuttal`。旧“只给 canonical evaluation + reconciliation 就直接发布”的调用不再合法。
