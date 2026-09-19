# 欧美140 a20 Source-Clock / Semantic Authority 升级交接

日期：2026-09-10
状态：收敛到 a20 工程候选（prefix-v2 + acoustic schema 1.5）；semantic release 仍 BLOCK，尚未提交/推送。最新事实见第 8 节；仍以 `references/next-stage-max-expected-loss-handoff-2026-09-07.md` 的产品目标与约束为上位原则。

## 1. 当前任务与不可重做内容

当前真实任务：`private/oumei140-fresh-20260910-sourceclock-a20`

- task fingerprint：`4b006073c7e5fe8b0e52f26eab3ed7733ffcf7fbbde58872710633818109ea55`
- algorithm：`4.0.0a20`
- production source-clock map：`private/oumei140-fresh-20260910-sourceclock-a20/qa/source_clock_map.json`
- source-clock map SHA：`5cabe83a18b6a71c9b480d5d7813eda034d7045f0dd35a05961825541f89d880`
- V2 fresh final-mix promotion analysis SHA：`a9a0666ba4b245a68c743efceda4dfe4147e2aad228c0c86cb6079181cf26207`
- authority migration receipt SHA：`bb4613e7b5675a3b01e5b18bbc55f86b0057442fd10b8460e6c320708b67aaf7`

V2 fresh holdout 已完成，不能重新挑样/调阈值。严格晋级 7 首：ordinals `1,2,7,8,9,12,13`。

V2 代表性结果：
- Toxic：median `2796 -> 88ms`
- God Is a Girl：`2615 -> 235ms`
- I Gotta Feeling：`12446 -> 362.5ms`
- Everytime We Touch：`3098.5 -> 129.5ms`
- Womanizer：`1165 -> 205ms`
- Moonlight Shadow：`1736 -> 121ms`
- Scream & Shout：`5736 -> 418ms`

Hot N Cold V2 只有 2 个 fresh anchors，虽 `5691.5 -> 90ms` 且已有人工点 `3563 -> 711ms`，但冻结规则要求至少 3 anchor，因此不能事后晋级。

Fresh a20 Max 已完成：15 occurrences，14 transitions；最终 transition resolver 为 `13 resolved_clear + 1 confirmed_overlap + 0 unresolved`。唯一 confirmed overlap：Timber -> Moonlight Shadow，`2164.665–2168.408s`。

PRODUCT_A20 已存在并通过其自身 render/lexical 静态 QA，但这不等于 semantic release 已通过。`LEXICAL_FLOOR_A20.json` 对已解析 canonical evaluation 为 1022/1022 cues、21322/21322 chars，lexical mismatch/gap/overlap/unowned 均 0；该结论不证明 canonical wording 本身绝对正确，也不证明 timing。

## 2. Semantic 当前历史结果

Release90 + targeted ASR 已完成，targeted 新增 56/56 成功；union 共 146 jobs、无重复冲突。

当前冻结 fusion：
`output/oumei140_sourceclock_a20/SEMANTIC_A20/TARGETED_FUSION_A20/fusion_targeted.json`
SHA：`a189ac091990dd251f7af81ff1a0b3408072b59a553436b6f6db1d22a965c373`

旧 targeted semantic 报告：
`.../TARGETED_FUSION_A20/semantic_sync_targeted.json`
SHA：`a463dc965c277a8467b323abf9a214115137b903ef3b185c55607f1913959289`

旧策略下仍失败 10/15：ordinals `2,4,5,6,7,10,11,13,14,15`。
关键原因：ASR-only fallback 即使直接与 final timing 接近，也必须 editor witness lexically reliable 且 editor projection/final timing audit 通过；这对 a20“用独立证据 rescue 已知较差 editor/source clock”的目标形成循环依赖。

旧 targeted 代表性 final ASR timing：
- 2 God Is a Girl：6 anchors, median 185.5ms，但 editor text witness unreliable，旧 basis=insufficient。
- 5 Hot N Cold：6 anchors, median 315.5ms，仅失败 editor-final disagreement。
- 6 Loves Me Not：5 anchors, median 355ms，仅失败 editor-final disagreement。
- 10 Applause：9 anchors, median 1445ms，仅失败 editor-final disagreement。
- 11 Timber：5 anchors, median 296ms，仅失败 editor-final disagreement。
- 7 / 13 为 V2 已晋级 source-clock track，但旧 semantic 因 ASR coverage/policy 仍失败。

## 3. 2026-09-10 新 semantic 架构（当前主工作树，尚未提交）

没有降低旧阈值，也没有把普通 ASR-only 提升为独立 timing authority。

新增 `lyric_aligner/qa/source_clock_authority.py`：`verified-source-clock-final-mix-holdout-1.0`。

只有 V2 fresh holdout 已晋级且能完整 replay 的 exact track 才获得 `verified_source_clock_final_mix_holdout` semantic basis。Replay 必须核验：
- task fingerprint
- algorithm version
- source-clock map SHA
- V2 promotion analysis SHA
- frozen selection/protocol SHA
- promoted ordinal/count
- occurrence_id / track_id
- source audio / canonical selection identity（由 map/timeline 链约束）
- exact rate/offset
- run source_clock provenance
- timeline source_clock_map SHA
- canonical selection SHA
- promotion >=3 valid final-mix anchors，metric/risk/human pass

新 semantic 不“无条件放行”晋级 track：
1. V2 fresh holdout signed errors 作为已冻结的 projection-vs-audio 校准证据；
2. 当前 final SRT 仍逐 canonical ownership 检查 `final onset - verified source projection onset`；若 hybrid/editor preservation 又把 final 带偏，仍 FAIL；
3. 当前 ASR/forced evidence 若有足够 anchors 且强烈反驳已晋级 projection，新增 `verified_source_clock_current_audio_rebuttal`，仍 FAIL；
4. 未晋级曲完全不能借用该 authority；普通 ASR-only 旧安全策略保持不变。

semantic CLI：`scripts/v4_audit_semantic_sync.py`
- 旧任务不传 source-clock authority，继续输出 `semantic-sync-qa-1.1` + old policy；
- 新 authority 必须成对传 `--source-clock-map` 与 `--source-clock-promotion-analysis`；启用后输出 `semantic-sync-qa-1.2`。

release validator：`scripts/v4_validate_release.py`
- 双读 legacy 1.1 与 source-clock 1.2；
- source-clock 1.2 不仅检查 basis 字符串，还重新读取 map/promotion/run/timelines，再独立 replay authority；
- source-clock map/promotion 必须成对提供；legacy QA 不得夹带 source-clock authority inputs；
- release manifest 增加 semantic source-clock map/promotion SHA provenance。

## 4. SHE25 分支 `82311c8` 的处理结论

本地分支：`codex/she25-lexical-floor-20260910`
commit：`82311c843237cf88e404fb931f34a9a0ea2a4365`
parent/main committed base：`5a15c507d8ce1eda0ac856f464b47fb3d241a9b6`
20 files，+706/-31。没有整包 cherry-pick。

主体方向可用，已选择性吸收并加固：
- continuous canonical/editor character-stream 重复检测，防跨分行伪唯一 exact anchor；
- connected canonical ownership lexical floor，检查重复占用/倒序/Latin word split；
- Smart current -> v1.2.11；
- 显式 performer-prefix canonical preparation；
- Pro mix EOF/context window clamp 与 AAC/M4A duration fallback。

原提交发现并修正的问题：
1. review 若保留 multi-cue `cue_span`，原实现只按 `cue_ordinal` 排除，可能让同 envelope 其它 cue 被误当 trusted；已改为整个 review envelope unresolved。
2. 原 v1.2.11 lexical-floor failure 后把 cues 降为 review，但仍保留 pre-quarantine failed floor；现改为两阶段 audit -> quarantine -> 依据最终 decisions 重算，安全隔离后为 `review_required` 而不是 stale failed。
3. Pro 原改动把 mix SHA 只写 plan、未进入 job identity；现 `mix_audio_sha256` 进入 job identity，避免不同实际 mix 复用同 job id。
4. 原 performer prefix regex 只覆盖 Latin/CJK；现用 Unicode Letter 支持 Kana/Hangul 等。无冒号 prefix 只允许在非 ASCII Unicode-letter 边界剥离，避免 `Hoh`/`Ella's` 等英文误切。
5. 不加入任何敏感词自动屏蔽/替换。

## 5. 已完成验证

冻结 focused regression 第二轮：全部通过。
- py_compile：通过
- new semantic source-clock authority：7/7
- SHE25 absorbed regressions：10/10
- existing semantic sync：16/16
- selective repair：6/6
- Pro decision fusion：12/12
- historical Smart v1.2.10 contract：1/1

冻结 release-contract 第二轮：全部通过。
- py_compile：通过
- semantic release contract：12/12
- source-clock authority：7/7
- release integrity：10/10
- release lineage：14/14

第一轮中曾有测试夹具/version import 启动方式问题，均已修正；不得把第一轮 FAIL 当生产代码失败。

## 6. 真实 semantic 1.2 结果与下一步固定顺序

真实 source-clock authority semantic 已完成，不得重跑已有 146-job ASR/fusion：

- 新报告：`output/oumei140_sourceclock_a20/SEMANTIC_A20/SOURCE_CLOCK_AUTH_A20/semantic_sync_sourceclock.json`
- SHA：`cb553e79a43c3a96ece63317cd6aef3c5a3b6fb8b4c9a8454014bff2f24e40a2`
- schema：`semantic-sync-qa-1.2`
- policy：`verified_source_clock_or_forced_alignment_or_asr_plus_reliable_editor_v2`
- final 真实失败从旧 10 首收敛为 7 首：ordinals `4/5/6/10/11/14/15`。
- 旧失败中的 `2/7/13` 已解除：它们均为 V2 已晋级 source-clock track；新 gate 通过 exact authority replay 后不再被旧 generic ASR/editor 循环依赖阻断。
- 7 个 V2 authority track `1/2/7/8/9/12/13` 全部通过，且未出现 `verified_source_clock_current_audio_rebuttal`。
- ordinal 12 Moonlight Shadow 因 overlap recomposition 使用 `legacy_overlap_numeric_replay` 绑定，其余 authority track 为 `exact_sha`；该兼容链已有独立任务/测试覆盖，不要随意回退。

剩余 7 首 final blocker：

- `4 On The Floor`：6 ASR anchors，median `1597.5ms`、P90 `2226ms`；`asr_fallback_editor_final_disagrees` + median 超阈值。不能直接授权。
- `5 Hot N Cold`：5 个 final 可用 ASR matches，median `363ms`、P90 `2525ms`；当前只因 `asr_fallback_editor_final_disagrees` 阻断，但它未达到 V2 >=3 fresh holdout，因此不得借 source-clock authority。
- `6 Loves Me Not`：5 anchors，median `355ms`、P90 `746ms`；只因 editor-final disagreement 阻断。
- `10 Applause`：9 anchors，median `1445ms`、P90 `1764ms`；只因 editor-final disagreement 阻断，已接近现有 median 上限，需更强独立证据。
- `11 Timber`：5 anchors，median `296ms`、P90 `1292ms`；只因 editor-final disagreement 阻断；此前 source-clock candidate 本身不足，不能补造 source-clock authority。
- `14 Party Rock Anthem`：3 anchors、fraction `0.029126`，median `1198ms`，P90 `10440ms`，large fraction `0.333333`；独立证据覆盖与尾部风险均不足。
- `15 We Found Love`：2 anchors，median `213ms`、P90 `413ms`；主要是独立证据/最终 audio anchor coverage 不足。

下一步不再修改 semantic 阈值或 generic ASR fallback。固定研究方向是新增真正独立于 editor timing 的 `source-audio -> final-mix` evidence：

1. 复用已经在 ASR outcome 之前冻结的 `TARGETED_ASR_A20/selection_lock.json` 作为 ordinals `4/5/6/10/11/14/15` 的固定 56-line population；不得依据本次 final-mix ASR 结果重新挑样。
2. 在 exact source-audio 上产生 canonical-independent source observation（优先复用 `source_observer.py` whole-recording ASR/cache identity）；canonical 仅在 inference 后用于严格唯一/单调匹配 source onset。
3. 将 source-ASR 得到的真实 source onset 作为 `expected_source_time_ms`，用现有 `local_acoustic_v11` 将 source waveform 映射到 exact final mix。候选 LRC/source-clock 只能用于限制搜索域，不能作为输出 onset，也不能用 editor timing参与预测。
4. 新 evidence family 应显式记录 source audio SHA、mix SHA、canonical identity、source ASR model/runtime、source onset、acoustic config/slope/score/margin/boundary-hit 与 projected mix onset。只有 `not ambiguous + feature agreement + no slope/source boundary hit` 才可作为候选独立证据。
5. 不因新 family 存在就授予 production authority。先跑固定 56-line population，检查 coverage、与 current final-mix ASR/最终 SRT 的一致性和明显冲突；只有证据足够再设计新的双音频 family semantic basis，例如 `source_acoustic_projection+asr`。不得用 V2 final-mix ASR holdout 去“校准 ASR 自己”。
6. 对 4/14 等本身已有大误差/长尾反例的曲目，即使新 family 不足以证明 final，也继续 fail closed，不为整批通过而调阈值。
7. semantic 得到可辩护 release pass 后，才继续 production lexical floor、viewer lexical floor、structural/release QA；若仍失败，保留真实 blocker。
8. 用吸收后的 Smart v1.2.11 对 SHE25 当前真实输入重新回归，对比旧 `125 -> 92 review`；重点验 timing signature 完全不变、错误 review 是否减少而非被误清，不能以 review 数下降直接宣称准确率。
9. 更新 owning docs；然后冻结工作树执行 `python scripts/validate_skill.py .`、`python -m unittest discover -s scripts -p "test_*.py"`、CI/docs contract 与真实 output/diff audit。
10. 最后做 worktree/版本/artifact lineage 审计，只提交本任务拥有的代码/测试/文档；再 commit/push。`codex/she25-lexical-floor-20260910` 在修正版主线完全吸收、无独有价值后再决定清理。

## 7. 执行分工

ChatGPT 负责思考、方案、代码、绝大部分执行、测试设计、审计、验收。只有 ChatGPT 工具权限确实不能直接完成的本地命令才通过 CodexPro direct queue 交给 Codex；优先 Luna，必要时 Terra。Codex 执行结果必须由 ChatGPT 回读业务产物核验，executor exit 0 不等于业务通过。

## 8. 2026-09-10 R3 核实与 R4 收敛

重新读取并核验 R3：plan SHA `11fc12ea6d6a26cf336bcfaa94e8498b258afe2f5f5ea9aaa26751bf4f3bece5`，evidence SHA `ca839b373742a9991a1140a1f019e84f6d626e58a5f7c2e1a4dd79f99b76a2e2`。上轮 direct queue ID / execution-plan SHA 仅作转述，本轮不以队列退出状态作为业务验证。

prefix-v2 采用最短合格行首 prefix（至少 3 lexical units、12 signal characters），在完整 canonical stream 和 source observation stream 中各自唯一；不得用行中片段反推 onset。历史 projection 1.0/v1 保持可读，当前 plan 1.1/v2 身份不变。R3 为 56 frozen -> 10 source onset -> 1 nominal eligible。Hot N Cold ordinal 5 / canonical line 26：source onset=72680ms，prediction=864690ms，mix query=[866410,872858]ms，向前外推 1720ms；final SRT=867910ms，差 3220ms。该条不是可辩护 timing evidence。

根因：local_acoustic_v11 原 schema 1.4 只要求 retrieval、slope/source search interior，未验证投影是否在本 job 的查询区间内。修复为 schema 1.5：输出 projection_within_mix_window 与非负 projection_extrapolation_ms，仅闭区间内（距离为 0）可获得 eligibility。merged decode region 不扩大单个 job 的观察域。Pro fusion、shadow binder/evaluator 共用 acoustic_eligibility 的严格坐标及全部 eligibility 条件校验；缺字段、类型错误、标志与坐标/原有门槛矛盾均 fail closed；binder 还要求 result window 与 plan window 相同，并保存窗口和 local gate。历史文件保持可读，不继承新的资格。

修复前新增 producer/consumer 回归已运行并失败；修复后定向 43/43 PASS（区间两侧、端点、merged region、旧缺字段、矛盾坐标/门槛、Pro 无裁决、shadow 无授予、完整 canonical、防重复 job、双向 cache 路径隔离）。前轮 asdict config、content-bound cache 复用且正式五产物 fresh、合法空 ownership CSV 行跳过等实现均已回读。

R4 最终冻结目录：`output/oumei140_sourceclock_a20/SEMANTIC_A20/SOURCE_ASR_ACOUSTIC_SHADOW_A20_R4_SEALED_INPUTS/`。结果为 **56 frozen -> 10 source onset -> 0 eligible**。原 7 缓存逐文件 SHA 不变；model factory 被断言禁止调用，未运行 source ASR。source observations 相对 R3 字节相同；plan 的 10 jobs 完全相同，所有检索数值与 config 相同。输入审查后，唯一性验证改用 hash-bound 完整 canonical selection，补齐 timeline 身份、重复 job 和 cache 双向隔离保护；5 个仍被拒绝的目标记录了更完整的 canonical 重复计数，因此整个 plan SHA 有变化，不能声称 plan 字节不变。所有 56 行的接受/拒绝状态与 source onset 均不变。min_margin=0.012、score、ambiguity、feature agreement、slope/source boundary gate 未变。较早 R4_DOMAIN_V15、R4_DOMAIN_V15_FINAL、R4_SEALED 均保留为诊断历史，不作为最终工程快照输入。

本切片到此停止调参。高 fused score 与低 margin 表明当前检索缺乏判别力；音乐重复结构是否为主因尚未证明。prefix-v2 仍 shadow-only，未接 semantic authority。ordinals 4/5/6/10/11/14/15 继续 FAIL，a20 成品不得发布。工程封存不是通用最优或生产 release；第 6 节 semantic pass 后的 SHE25 真实回归、成品 release QA 和 commit/push 条件尚未满足。现有 SHE25 单测不代替真实回归。后续重新启动准确率工作需要独立 temporal observation/forced alignment 实验，不得在本次已见样本上降低门槛。

R4 SEALED_INPUTS exact SHA：

- plan：`df7f037db6a3c3b224171e536a25408e64923cbab87190b371794042a920de69`
- observations：`8ee349ac398e8bdf1fc316ea3f40d6f9aa39e9265e46c99b402b02b4fb14016d`
- acoustic raw：`faabb79ab6d16741aaa3fe50766e9fab2523f3fa58dfd63fc945d059d4ce82b9`
- evidence：`aa7a32f7129685967e7f946fb646970c48630207cfdae187f04cbd1508f6b05e`
- evaluation：`957a79a51f9bfb835c0ee8550ed6ec8cbb829cfa65e6e9dc0028ca3ac28d49fe`

V1/R2 原 plan 已重新核验为 projection 1.0/v1、56 frozen、2 source onsets；R3 v2 为 10。因此 2 -> 10 的源端覆盖提升属实，但不能等同最终 SRT 精度提升。

## 9. Source-clock authority 1.1 重放与封存边界

封板审查发现，第 3 节原实现的“exact replay”表述过强：旧代码只比较 selection/protocol SHA 声明，并信任 metric/risk/human pass 标志。本轮补齐原始 selection/protocol 实物输入，重放每个 track 的 source/canonical/occurrence 身份与 transform；将 ledger 绑定到冻结 probes，重新核对 signed errors、指标、risk improvement、human non-regression 与冻结阈值。authority policy 升为 `verified-source-clock-final-mix-holdout-1.1`。这是对已有 content-bound analysis ledger 的一致性重放；没有重新执行 ASR 或重放原始 shard 推理，ledger 的 valid/观察值仍是既有证据输入，不宣称新独立准确率验证。

semantic audit 现在需要完整四件套（release validator 的对应四个参数均在 source-clock 前加 semantic-）：

- `--source-clock-map`
- `--source-clock-promotion-analysis`
- `--source-clock-promotion-selection`
- `--source-clock-promotion-protocol`

缺任何一件、SHA/身份/变换不匹配、summary/pass 与重算矛盾均拒绝；legacy 无 source-clock authority 的语义路径保持。旧 authority 1.0 报告不自动取得 1.1 放行资格。

原始 V2 文件保留在 `output/oumei140_fresh_20260909_rerun1/SOURCE_CLOCK_CHATGPT/`，对应 `final_mix_promotion_v2_analysis.json`、`final_mix_promotion_v2_selection_lock.json`、`final_mix_promotion_v2_protocol.json`。本轮新报告为 `output/oumei140_sourceclock_a20/SEMANTIC_A20/SOURCE_CLOCK_AUTH_A20_REPLAY_V11/semantic_sync_sourceclock.json`，SHA `effbb52508ef5bad2bb58b15ef34fd1b23bf1c32a2c24df2ae9031d0824209f2`。真实 CLI exit 2 是业务 FAIL 的正确返回，不是执行错误。

新重放仍确认 ordinals 1/2/7/8/9/12/13 的 authority；12 保留已核验的 legacy overlap 数值重放。final semantic 仍失败 4/5/6/10/11/14/15。所有输入 SHA（含原 final SRT、audit CSV、146-job fusion）保持不变。故本轮只能封存工程候选，SHE25 真实晋级回归、完整成品 release、commit/push 继续受第 6 节既定 semantic pass 条件约束。

## 10. 2026-09-11 独立能力评估与下一阶段 Pivot（执行中）

本轮不把“7 首 semantic BLOCK”解释为项目整体失败。当前能力分层如下：Content/lexical ownership、结构保真、现有 editor/hybrid 非回归、source-clock 1.1 的 7 首晋级重放和 semantic fail-closed 已有较强工程闭环；真正未解决的是**未获 source-clock 晋级曲目的独立 exact-final-mix timing 观测与自动改写授权**。R4 已证明 `source-ASR + local harmonic retrieval` 在冻结 56-line population 上为 0/56 defensible timing evidence，因此停止继续降低 margin、放宽 ambiguity/boundary gate 或在已见样本上继续调该 family。

阶段判断为 **Pivot / selective continue**：保留 source-clock 1.1、semantic 1.2、Smart/lexical/structure 已证明部分；停止当前 harmonic family 的准确率开发；只继续一个真正不同观测机制的 English exact-final-mix HuBERTFA forced-alignment shadow。该 family 在新的人类 gold / holdout 证明前始终 `shadow_only_uncalibrated`，不得接 semantic/release authority。

当前工作树已有未实跑的 `english_final_mix_hubertfa_shadow` 候选实现，但独立审查发现必须先修：

1. plan 不能重新解析 raw LRC 后按原始行号取目标；必须使用 TrackAssets `resolution[].canonical_selection` 并重算/核对 `canonical_selection_sha256`，否则 metadata/role preparation 后可能对错歌词。
2. regular target 应复用已有 `alignment_contextual_segment_interval_ms`，要求左右 canonical context 且返回目标自身 start/end；这条机制已做过多窗口稳定性验证，但尚无 English final-mix accuracy gold，因此只作 shadow observation。
3. response binder 必须重放 task fingerprint、window policy、record count、record order/identity、exact mix window、lexical SHA、aligned onset/interval domain；缺失/越界/矛盾一律 fail closed。
4. response 必须保留并校验实际 model/config/version/vocab/dictionary/adapter dependency 的 `{path,sha256}` runtime bundle。首次 pilot 可以把它当观测后 provenance；任何后续 calibration profile 必须在采集 gold 前冻结这套 bundle，不能只靠 observer 字符串继承校准。
5. evaluation 必须以冻结 selected targets 为 denominator，逐 ordinal 报 selected/prepared/aligned/rejected/unaligned 与 rejection taxonomy；current final 只作 descriptive comparison，不是真值。
6. editor/current-final timing 只可路由 bounded search window。若 pilot 大量结果贴窗口边界或 unaligned，不得在已见结果上扩大窗口；需要新 policy + 预先冻结 protocol 再测。

下一阶段验收门：先通过上述 contract 单测与原 forced-alignment/contextual 回归；再用冻结 56-line population 做第一次 English HuBERTFA exact-final-mix shadow pilot。pilot 只判断覆盖、字典缺失、alignment reject、窗口边界行为和 observer-vs-current-final 分布，不据此授予 authority。只有 pilot 显示该 observer 有足够可用覆盖，才创建**新的、此前未用于开发的人类 final-mix gold**，在采 gold 前冻结 observer runtime bundle、window/context policy、指标和阈值；至少报告 median/P90/worst、coverage、窗口边界率，并与 current final/editor 做 paired error。若不能证明 viewer-relevant final timing error 更低，则停止 timing R&D，保留现有 fail-closed 产品而不是继续堆模型。

### 10.1 续接检查点（2026-09-11）

已完成：独立能力评估；确认 harmonic R4 路线停止调参；English final-mix HuBERTFA shadow 已改为 resolved canonical selection、三行双侧 context、`alignment_contextual_segment_interval_ms`、严格 response/runtime lineage、frozen denominator evaluation；CLI 已加入 output-tree/input collision preflight；新 contract tests 已重写但尚未执行。

固定执行顺序：① 先跑 English shadow focused tests + contextual forced-alignment/source-context 回归 + py_compile；② 失败直接修到 focused green；③ 用既有 56-line frozen selection 做 fresh HuBERTFA shadow pilot，不改 selection/window/policy；④ 回读 plan/response/evaluation，判断 coverage、字典缺失、alignment reject、window-edge 行为及 observer-vs-current-final 分布；⑤ pilot 只有在覆盖和健康性值得继续时才进入新 human-gold protocol，否则停止该 timing family；⑥ semantic 未形成可辩护 pass 前不做 a20 release；⑦ 之后才做 SHE25 真实 v1.2.11 回归、全量测试/docs/diff/lineage audit 与提交。任何已有 production/frozen artifact 不覆盖，所有新 pilot 输出必须 fresh。

停止条件：focused contract 无法自洽、56-line pilot 覆盖明显不足/大量贴窗或 backend 结果不健康、或后续 untouched gold 不能证明 paired viewer timing error 优于 current final/editor，任一成立即停止自动 timing R&D，不通过调当前已见样本的阈值来制造通过。

### 10.2 Focused verification 实测

2026-09-11 通过 Luna direct queue 仅执行本地测试（因为 ChatGPT 的 CodexPro bash 处于 safe allowlist，不能直接执行 Python）；ChatGPT 随后回读 task `result.json` 核验原始命令结果。`scripts.test_english_final_mix_hubertfa_shadow` 初轮 11/11 PASS；加入 scalar ownership fallback 后 12/12 PASS；`scripts.test_contextual_forced_interval` 7/7 PASS；`scripts.test_source_context_hubertfa_adapter` 16/16 PASS；相关 `py_compile` PASS。该 executor 没有修改源码/测试/生产或 shadow artifact。

### 10.3 V2/V3 真实 shadow pilot 与当前 go/no-go 边界

V2 首次 frozen 56-line pilot 暴露 audit 接线缺陷：PRODUCT `FINAL.audit.csv` 的大量 direct canonical `line_lrc` 行只有 scalar `canonical_line_index`，plural `canonical_line_indices` 为空；旧 shadow 因只读 plural 字段而把实际存在的相邻 canonical context 错判为缺失，得到 `56 selected -> 8 prepared -> 7 aligned`。修复为：仅当 plural ownership 为空时，允许使用 scalar `canonical_line_index`，并要求该 audit 行 `text_sha256` 与 resolved canonical text SHA 精确一致；否则仍拒绝。因行为变化，shadow plan/schema/request identity 升级为 V3，旧 V2 artifact 保留为诊断。

V3 fresh pilot：`56 selected -> 47 prepared -> 33 aligned + 14 unaligned + 9 pre-rejected`。33 个 aligned 相对 current final 的 descriptive disagreement 为 median `667ms`、P90 `2374.2ms`、max `19924ms`；`<=250ms` 7、`<=500ms` 13、`<=750ms` 17、`<=1500ms` 21、`>2500ms` 3、`>5000ms` 2。minimum mix-window edge distance `437ms`，没有 <=250ms 的 aligned point，因此主要风险不是贴窗。该分布不能当 accuracy，因为 current final 不是 gold；但明显的非边缘多秒级 collapse 足以阻止进入 production calibration。

已独立审查两个 >5s 反例。We Found Love line 10 的三行 context 跨约 27 秒歌词空档，40.8 秒 window 内强行连续对齐导致目标段被吸到前方，属于可解释的 context discontinuity；Hot N Cold line 59 的相邻三行在 final audit 上连续，仍出现约 5.3 秒前移，说明还存在 forced-aligner intrinsic segment-duration/placement collapse。故不能把“加一个长停顿 gate”误认为已经解决 observer 可靠性。

下一步只允许做一个**与 V3 prediction outcome 无关的结构性 continuity gate**，且阈值必须来自既有项目 window/context contract 或事前定义的通用上限，不能根据 19.924s/5.322s 两个反例反推。该 V4 若仍出现非边缘多秒 collapse，就正式触发 timing R&D stop：不再围绕 frozen 56 行调 observer/窗口/阈值，不创建大规模 human gold；转入 SHE25 真实 v1.2.11 回归与整包工程封板。若 V4 仅消除结构性坏输入、剩余 observer 健康性明显改善，才有资格预先冻结新 human-gold protocol。

### 10.4 V4 continuity pilot 与 timing R&D 最终判断

V4 只新增一个事前结构 gate：复用项目既有 `FULL_SEQUENCE_ALIGNMENT_CONTEXT_MS=1500` 作为三行相邻 owner interval 的最大允许空档；超过则 `context_temporal_discontinuity`，不送 HuBERTFA。未依据 V3 prediction delta 选阈值。policy/schema/request identity 升版；focused regression 为 English shadow 13/13、contextual interval 7/7、source-context adapter 16/16、py_compile PASS。

Fresh V4 结果：`56 selected -> 42 prepared -> 28 aligned + 14 unaligned + 14 pre-rejected`。continuity gate 正确拒绝 We Found Love ordinal 15 的 lines 4/5/10/13/22，并把该曲 aligned 长尾收敛到 2 条（evaluation track median `51ms`、P90/max `518ms`）。但其它曲仍存在非边缘 collapse：Hot N Cold ordinal 5 line 59 与 current final descriptive disagreement `5322ms`、min window-edge distance `1680ms`；Loves Me Not ordinal 6 line 28 disagreement `2543ms`、edge distance `640ms`。全体 28 aligned descriptive median `924.5ms`、P90 `2343ms`、max `5322ms`；只有 4 条 <=250ms、9 条 <=500ms、17 条 <=1500ms，2 条 >2500ms、1 条 >5000ms。current final 仍不是 gold，但这些非边缘多秒 collapse 满足预先声明的 stop 条件。

**最终 go/no-go：停止当前 English HuBERTFA timing R&D。** 不在 frozen 56 行上继续调 window、dictionary、segment geometry、observer 或阈值；不创建大规模 human gold；不把 V2/V3/V4 shadow 接入 semantic/release authority。保留代码与 shadow 产物作为诊断/未来研究基础，但 a20 semantic blockers `4/5/6/10/11/14/15` 继续 fail-closed，a20 当前成品不得宣称 release pass。工程主线从此切换到 SHE25 Smart v1.2.11 真实回归、全量测试/docs/diff/lineage audit 与提交封板。

### 10.5 SHE25 Smart v1.2.11 真实回归与生产参数判断

旧 `82311c8` 没有保存可验证的 SHE25 `125 -> 92` 运行命令/输入 SHA/计数字段，因此该数字不作为验收基线。重新以当前真实 12 首最终歌序、原始 `SHE25周年-180.srt` 做 fresh A/B：A 不给 rate prior；B 使用现有调速 WAV 文件名可直接读出的 `source BPM -> target 180 BPM`，全部标记为 `bpm_derived` soft prior。两组均 610 cues，Smart v1.2.11 policy=`smart-validation-policy-2026-09-10-v1.2.11`，audio_read=false。

与同参数 fresh v1.2.10 基线逐项比较后确认：A/B 两组 v1.2.10 与 v1.2.11 的完整 SRT SHA 分别完全相同，610 cue timing signature 与原始 SRT 也完全相同，`timing_decisions` JSON 结构完全相同，rendered text diff=0。v1.2.11 只收紧决策 authority：A 将 2 条、B 将 4 条 `editor_boundary_ownership_restored` 且 `canonical_span=null` 的旧 non-review decision 降级为 `final_canonical_ownership_unproven` review；`final_lexical_floor_failed` 新变化为 0，quarantined cue=0，trusted lexical error=0，trusted word-boundary error=0。故 v1.2.11 的真实 timing non-regression 已证明，且本批没有 silent lexical-floor failure。

A 最终 text review=136；B=110。A->B 有 26 条 `review -> non-review`，0 条 `non-review -> review`；26 条新自动决策全部具有有效 canonical span，0 条缺 canonical identity。主要来自既有 `bpm_projection_confirms_mapped_canonical`，并含 2 条 `bpm_projection_trims_optional_vocalization` 与 1 条 `sequence_projection_confirms_bpm_bounded_stream`；B 的 floor 仍为 `review_required`，trusted lexical/word-boundary error 均为 0。B 的 12 个 rate metadata 已逐项核对，全部与事前冻结的 source BPM 和 target=180、`bpm_derived` provenance 一致。因此 **SHE25 本期 Smart 应优先使用 B 的 BPM soft-prior 配置**，但它仍有 110 条 text review、528 条 timing review 且 `pro_escalation_required=true`，不能宣称 Smart 已全部验收完成。

固定下一步：不再新增 accuracy heuristic。先跑 Smart/SHE25/source-clock/semantic/release focused tests，再跑全量 unittest；同步 owning docs 中 current candidate v1.2.11 与正式 production v1.2.10 的表述；执行 skill/docs/CLI contract 与 worktree/version/artifact-lineage 审计。只有测试和文档全部通过后才判断代码是否可提交；a20 release blocker 与 SHE25 Smart review 状态必须继续明确保留。

### 10.6 工程封板验证与提交边界

2026-09-11 focused closeout 已全绿：SHE25 absorbed regressions、Smart v1.2.10、production lexical floor、Pro fusion、semantic/source-clock authority、source-clock core/run routing、source-ASR acoustic projection、English HuBERTFA shadow、release semantic gate、output-tree safety 及相关 `py_compile` 均通过。两项最初失败仅为错误的 unittest module 启动方式导致 `scripts` import root 缺失；改用项目标准 `unittest discover -s scripts` 后分别 14/14 与 2/2 PASS，未修改生产代码。

最终工程验收：`python scripts/validate_skill.py .` 返回 `ok=true`；同一工作树执行 `python -m unittest discover -s scripts -p "test_*.py"`，**Ran 1862 tests in 117.656s / OK**，进程 exit code=0；`git diff --check` 无 whitespace/error，只有 Windows LF->CRLF 提示。`lyric_aligner.__version__` 与当前 Max 工程候选均为 `4.0.0a20`。

提交边界：只允许提交 `SKILL.md`、`lyric_aligner/`、`references/`、`scripts/` 中本轮/本阶段拥有的生产代码、测试与 owning docs；明确排除 `.playwright-cli/`、根级 bridge/agent 日志、`implementation-diff.patch`、`tmp/` 诊断 runner/log/json 以及 `output/`/`private/` 真实运行证据。代码可提交不等于字幕 release：a20 blockers `4/5/6/10/11/14/15` 仍保持 BLOCK，SHE25 v1.2.11 B 仍 `review_required` / `pro_escalation_required=true`。
