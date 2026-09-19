# Lyric Aligner v4 当前实施状态

2026-09-19 语义敏感词最终 gate：新增 `review/semantic_sensitive.py`，并在 `scripts/v4_review_safe_final.py` 接入 `sensitive-pack / sensitive-finalize`。所有未来真实 Safe Final 在最终文字与普通 QA 收敛后必须由大模型完整扫描冻结 SRT，结合语义决定 KEEP/MASK/REVIEW；规则/正则只提供候选提示，不再默认机械写回。finalizer 只允许 exact term 的高置信 mask，REVIEW、漏审、stale hash/text/timing 一律 fail closed，且 cue count/number/start/end 完全冻结。Best-Safe 默认 `mask_profile` 已改为 `none`，主 CLI 会自动生成 `SENSITIVE_PACK.json` 并把自身标记为 `formal_safe_final_ready=false`；正式 display/release 默认也要求绑定同一模型审查，历史回放只能显式 legacy opt-out。v0.2 direct queue 最新验收：受影响/语义定向测试全部通过，Skill 校验通过；Python 3.14.6 全量 `1998 tests / OK`（205.098s）；`git diff --check` 通过。

2026-09-18 最终 Review：用户流程收口为 Safe Final / 最低下限最终版，Max Recovery 只处理严重 timeline 事故。新增语言候选扫描和只读 Gap Rescue 审定验证，文字继续复用 reviewed canonical splice；修复报告/SRT hash 错绑和逆序 canonical span 可被接受的问题，支持真正 Standard floor，落盘前复核 lineage，音频哈希改为共享流式实现。没有新增产品模式、renderer、ASR backend 或 timing authority。Balanced / Fluent 不恢复；历史 output/private 证据、production freeze 与既有 selector 身份保持。

默认语言路由：一般中文优先 Standard stable floor，occurrence/拆句/重复结构复杂时再用 Smart；一般韩文 / K-Pop 优先 Smart stable floor，再进入 pseudo-English / 韩英混唱 / 真实英文保留的 lexical review。Pro 只按需提供局部证据，不能因为语种直接升级 Max。

上一轮 Safe Final/Gap Rescue 基线验收：Python 3.14.6 完整 `1987 tests / OK`、定向 `56 tests / OK`；代表性历史/当前任务的兼容与继承回归通过，既有修复不丢失。两个输入漏洞在上一版源码复现、由当时 verifier 拒绝，并补齐同 cue 多个 disjoint edits 的 canonical 顺序保护。compile、Skill、privacy、environment、docs-contract、diff-check 当时均通过。**该数字早于本页上方 semantic-sensitive gate 新增，不作为本次新 gate 的测试通过声明。**

本轮任务级语言审定与 Gap Rescue 验证仅保存在本地私有输出中，不进入通用仓库。已验证 partial-text 修复不改变 cue 编号、时间和分段；真实 gap 在缺少独立 timing authority 时保持 floor，不插入新 cue。未取得新的独立 Gold，不把回放、采用数或候选数算作正确率。新契约见 [Safe Final review](safe-final-review.md)。

试听入口保持 **3.2 唯一默认**：`试听复核.cmd` / `scripts/v4_listen.py`；其他版本页面 legacy。冻结原播放核心，Safe/R5、Max、Gold、转场/ownership 共用；复核页面本身不授予字幕修改 authority。详见 [统一试听入口](listening-ui.md)。

更新：2026-09-19
主线算法版本：`4.0.0a20`
Best-Safe：`1.1.0 / best-safe-smart-timing-floor-1.1`

本文件只描述当前事实。历史实验、旧候选、阶段性测试数字和被否决路线见 [v4-change-record.md](v4-change-record.md)、[accuracy-experiment-register-2026-09-08.md](accuracy-experiment-register-2026-09-08.md) 与 Git history，不再在本页重复。

## 1. 当前代码与生产入口

```text
branch / code line : main
algorithm_version  : 4.0.0a20
Standard           : Text Repair V2.1
Smart              : v1.2.11
Smart policy       : smart-validation-policy-2026-09-10-v1.2.11
Pro                 : v1.2.7
Pro acoustic schema: 1.5
Max                 : 4.0.0a20
Best-Safe           : 1.1.0
Best-Safe policy    : best-safe-smart-timing-floor-1.1
```

当前正常路径：`Editor + canonical -> Standard/Smart stable floor -> GPT reviewed text -> QA -> GPT semantic-sensitive full scan -> mask-only finalize -> Safe Final`。Gap Rescue 当前只读，Max Recovery 仅事故恢复。内部 Best-Safe 继续作为既有 selector，不改变 Smart/Max authority；不把内部层级当作必跑升级链。

`smart_current.py` 是唯一 current-production Smart facade。v1.2.10 的独立入口只作历史 regression baseline，但其实现仍被 v1.2.11 调用，不能作为 dead current code 删除；v1.2.11 沿用 v1.2.10 timing authority，并增加 final canonical ownership / connected lexical-floor hardening。

仓库当前代码版本不等于任意具体字幕任务自动 release-ready。任务仍必须使用自身输入、manifest、配置和证据通过 lexical / structural / semantic / release gate。

2026-09-12 无音频下限实验仅保留本地排版缺陷修复：词边界空格不再跨越已有标点，横向空白仍会压缩。五批对照只有两处空格变化，没有授予新的 lexical / ownership / timing authority；正式冻结 tag 未移动。word / character ownership 扩展未证明成品收益，暂停继续扩展 Max。独立人工 blind 仍缺原生响应，不能把回归或候选一致性当作 Gold。见 [实验结论](noaudio-floor-trial-2026-09-12.md)。

2026-09-13 早期机器阶段：49 组冻结窗口完成无提示本地 Faster-Whisper 取证，合计582.365秒音频。该阶段原生人工响应0、生产新增修复0；这些历史事实与 frozen manifest 保持。正式停止本轮无音频 ownership 重建开发及阈值调整，不扩张 Max。

2026-09-13 当前任务交付修订 `2026-09-13.5`：在 `.4` 剩余65条确定谐音上新增采用18处（KPOP110 11、KPOP200 7），5条整条文字审定、13条局部改善仍review；47条原文保持。残留确定58、高疑似308。新增通用**已审定**canonical字符片段入口及当前floor空档检查，不新增自动词义selector；旧150处真实修复经通用入口回放，11组成品逐字节一致。五批3,286条编号、时间和分段不变，其他三批字节保持 `.4`。新29个扩窗仍是同一ASR的相关观察，未获得独立Gold正确率或人工编辑分钟数；本次逐条审定未发现新增文字错误。Smart/Best-Safe/Max及ownership/DP门槛不变，旧V3 FAIL保持。见 [交付及证据边界](smart-safe-incremental-delivery.md) 和 [通用入口契约](reviewed-canonical-splices.md)。

## 2. 当前工程状态

2026-09-12 Best-Safe 1.1 工程封板验证：

- Best-Safe focused suite：`20/20 / OK`；
- `python scripts/validate_skill.py .`：PASS；
- `python -m unittest discover -s scripts -p "test_*.py"`：`1882 tests / OK`；
- `git diff --check`：PASS（仅工作区 LF/CRLF 提示）；
- SHE25 真实 A/B 验证：v1.2.11 相对同参数 v1.2.10 的 610 cue timing signature、完整 timing decisions 与 rendered SRT 均不变；
- v1.2.11 的新增影响是收紧 final canonical ownership / lexical-floor authority，不重新建立 timing model。

测试数字只描述该次快照；后续修改必须以新的实际重跑为准。

### Source release freeze

Best-Safe 1.1 的源码冻结身份为 production tag `prod-v4.0.0a20-best-safe-v1.1.0-20260912`，精确指向 commit `f2a22562c9ca8b35576dddee827776b7d2b24e7d`；该提交已通过 GitHub `validate` run `34676113801` 后才创建并推送 tag。冻结契约见 [release manifest](releases/prod-v4.0.0a20-best-safe-v1.1.0-20260912.json)。历史 `prod-smart-v1.2.5-pro-v1.1.4-20260821` 仅表示旧 Smart/Pro baseline，不代表当前 Best-Safe。任务级私有输入/字幕/Gold/Review 不进入源码仓库；欧美140 Best-Safe artifact SHA 单独记录在 release manifest，不把任务 artifact 与 source tag 混成同一 authority。

### 本地音频模型部署状态

2026-09-12 完成本地实验模型清理：删除 Qwen3-ASR 0.6B/1.7B 权重、Qwen boundary/forced-aligner 权重与 probe venv、SOFA 模型/runtime、HuBERTFA ONNX/runtime/压缩包及临时探针缓存、STARS 权重/runtime，以及 faster-whisper medium/small 缓存，共释放约 `17.115 GB`。保留源码、adapter、模型身份/下载元数据与历史实验结果；这些 Qwen/SOFA/HuBERTFA/STARS 路径仍可作为可选实验代码，但再次实跑前必须重新部署本地模型，不能假设权重仍存在。当前本机保留的主要 ASR 权重为 `mobiuslabsgmbh/faster-whisper-large-v3-turbo`（约 `1.51 GB`），仍作为现有默认/主要 faster-whisper backend 使用。清理后完整工程回归 `1882 tests / OK`，未触发模型自动下载。模块身份与重新启用条件见 [module-lifecycle](module-lifecycle.md)。

### P1 boundary promotion shadow

P0 production tag 冻结后，`main` 可继续承载不改变 production authority 的 P1 研究工具。当前 P1 补齐 `timing_decision_pack -> pre-gold selector/partition lock -> selection-bound candidate-blind review -> raw response -> Gold -> shadow gate`：新增 `evaluation/boundary_promotion_shadow.py` 与 `scripts/v4_boundary_promotion_shadow.py`，并以向后兼容的可选字段扩展 timing-decision review。promotion 必须逐 boundary 绑定 frozen Smart/candidate 值、selector code SHA 与独立 evidence family/correlation group；selection/gate policy 必须在人工 truth 前冻结，Smart==candidate 的 unchanged control 不能充作 promotion。冻结 selection hash 与 intended partition 必须在人工 review 前一起进入 review manifest；P1 evaluate 会由 frozen pack/selection/partition 确定性重建 exact candidate-blind manifest，并同时核对 manifest、raw response 与 Gold，再从 response 重算 Gold，拒绝 post-gold reselection、development/calibration→blind/holdout 重标、Gold 手改、case/clip/instructions 漂移和候选/private 字段泄漏。只有预先冻结为新的 `blind` / `holdout` review 有资格通过 shadow gate；development/calibration 只作诊断。无论 shadow PASS/BLOCK，当前均固定 `production_authority_granted=false` / `production_writeback_permitted=false`，不得直接生成 Best-Safe timing promotion。首轮协议目标仍为冻结32个新 boundary、至少24个有效 truth、至少4个独立 track；该 hash 链不是外部可信时间戳，真正 blind campaign 仍需保留原始 response。详见 [P1 protocol](boundary-promotion-p1.md)。当前 P1 focused `22/22 / OK`、timing-decision review `11/11 / OK`、Best-Safe focused `20/20 / OK`、完整工程 `1908 tests / OK`；compile、`validate_skill`、privacy scan、dirty docs-contract（19 changed / 5 substantive / 0 issue）、`git diff --check` 与静态 production-reference 审计均通过。引用审计未发现 P1 被 Smart、Best-Safe、Pro、Max 或 release 生产路径导入。

## 3. 当前任务级阻断

### 欧美经典140 / a20

2026-09-12 fresh production 的 Max semantic release gate 仍为 `passed=false`。当前逐曲 `final_sync` 通过 `1 / 3 / 5 / 6 / 7 / 8 / 9 / 12`，BLOCK：

```text
2 / 4 / 10 / 11 / 13 / 14 / 15
```

因此 **Max Release 继续 BLOCK**，不得把 `PRODUCT/FINAL.srt` 当作完整 release-ready 成品。

同一冻结任务已升级为 Best-Safe 1.1：直接从原始 Smart v1.2.11 构建，Smart 的 923 cues、顺序及全部 start/end 作为 topology/timing floor；8 首 Max semantic-pass 曲仅记录为 candidate，不再整首吸收 Max timing/topology，7 首 semantic-BLOCK 同样不影响 floor。当前 `BEST_SAFE/FINAL.srt` 为 923 cues，逐 cue 对 Smart 的 timing diff=0，`topology_exact=true`、`unsupported_timing_change_count=0`。用户人工真值 Toxic cue28 start=`116833ms` 已写入 task-bound timing truth，最终实际值同为 `116833ms`、误差0。Smart 全部70个文字 review 区域现在均有显式处置：Smart report 的155个 review cues 已由 ledger 155/155 完整且唯一记账（unaccounted=0）；16个区域进入 canonical-gap proposal、54个模型直接 keep Smart；完整 deterministic + cue-ownership verifier 最终只接受4个区域 / 6 cues、拒绝12个，最终66个区域 / 149 cues 保守留在 Smart。文字层不允许无 timing authority 的跨 cue 插词/删词/搬词；显式 `*` mask、`strong_profanity_v1` 与 normalized-equivalent display 可安全吸收。最终相对 Smart 仅24条文字/display变化，timing 变化0；独立审计 PASS，残留可 mask 强敏感词0、英文粘词0，SRT SHA=`adc5f26b10ffd95da3d039482b9a942312075b12cf5c586823f04d41768212bb`。该 `publish_ready=true` 只表示 Best-Safe 1.1 Smart-floor 合同通过，**不改变 Max Release 的 BLOCK 状态**。

### SHE25

Smart v1.2.11 的 BPM soft-prior 配置优于无 prior 的 text review 数，但仍为 `review_required` 且 `pro_escalation_required=true`；不能写成无人审核 final-ready。

## 4. Timing R&D 当前决定

当前两条 timing family 已停止继续调参：

1. source-ASR -> harmonic/local acoustic retrieval；
2. English exact-final-mix HuBERTFA shadow。

停止原因不是“理论永远无效”，而是现有冻结样本上未获得足以授予 production timing authority 的独立证据，并出现非边缘多秒 collapse。没有新的独立 final-mix truth、预先冻结的新评测集或明确 production failure 时，不重新围绕旧样本调 window、dictionary、margin、segment geometry、observer 或 selector threshold。

这些模块可保留作诊断/未来研究，但不得从 shadow 身份直接升级为 production authority。

## 5. 当前已验证能力

- Standard：canonical text/order 修复，timeline signature 冻结；
- Smart：timed canonical、sequence/BPM text recovery、editor ownership 与 lexical-floor hardening；
- Pro：对 Smart unresolved region 做 bounded audio/evidence planning，默认不自动写 timing；
- Max：source-to-mix、cut/overlap/recomposition、editor-preservation、canonical evaluation 与严格 release lineage；
- Human Gold/Review、calibration/blind、selection lock、regression baseline 与 release artifact 有独立身份；
- evidence/source-clock/observer/runtime/config 均要求 hash/version lineage，证据不足时 fail closed。

总体无人审核准确率仍未知，不能从局部已见样本或单个 observer 的条件误差外推任意新歌。

## 6. 当前维护原则

质量优先级：

```text
Content correctness
> Structure / ownership correctness
> Timing non-regression
> Timing improvement
```

优先修可复现 correctness、lineage、path-safety、运行可靠性和文档事实漂移。新的 accuracy heuristic 必须先有独立 truth 与事前冻结的验收协议，不能通过降低阈值或反复调同一 holdout 获得“进步”。

## 7. 本地资产保护

清理必须遵守 [local-artifact-retention.md](local-artifact-retention.md)。以下默认 KEEP：

- 当前生产输入与 manifest/run config；
- production final、QA/release/audit；
- Human Gold/Review、blind/truth、selection lock；
- calibration/regression baseline 与评估结果；
- 人工确认、editor reconciliation、结构 evidence；
- 被当前文档、测试或 release lineage 明确引用的 output/private 资产。

`__pycache__`、`.pytest_cache`、tmp/debug、Playwright、明确 disposable cache 可清理。不要按日期、版本号或体积批量删除 output/private。

## 8. 文档入口

- 项目入口：[../README.md](../README.md)
- 生产流程：[workflow.md](workflow.md)
- 能力边界：[capabilities-and-limits.md](capabilities-and-limits.md)
- Smart / Pro：[smart-pro-v1-1.md](smart-pro-v1-1.md)
- Runtime：[v4-runtime-guide.md](v4-runtime-guide.md)
- CLI：[v4-cli-contract.md](v4-cli-contract.md)
- 历史变更：[v4-change-record.md](v4-change-record.md)
- 实验台账：[accuracy-experiment-register-2026-09-08.md](accuracy-experiment-register-2026-09-08.md)
