# Lyric Aligner 文档同步契约

状态：mandatory  
生效范围：v4 及后续所有实质性/关键性更新  
生效日期：2026-08-17

## 1. 目标

代码、算法、CLI、schema、QA/release 语义、生产阶段状态发生实质变化时，相关权威文档必须在同一个 PR / commit series 中同步更新。文档更新不是发布后的补记，而是变更完成定义（Definition of Done）的一部分。

CI 通过 `scripts/validate_docs_contract.py` 检查本契约。规则以“受影响组件 -> 必须同步的权威文档”映射执行，避免只改一份无关 Markdown 来绕过检查。

## 2. 什么属于实质性/关键性更新

以下任一情况属于实质性更新：

- `lyric_aligner/` 下生产代码行为变化；
- `scripts/v4_*.py` 生产 CLI 输入、输出、阶段顺序或 release 语义变化；
- TrackAsset、artifact、task fingerprint、calibration profile、QA/release schema 变化；
- Source-to-Mix、TimeWarp、Fine、Transition、Timeline、Evidence、Forced Alignment、ASR 路由等算法行为变化；
- 默认生产路径、默认版本、生产/实验状态发生变化；
- 依赖、模型、backend 或可复现性要求变化；
- 删除、替换或退役旧生产路径。

以下通常不单独触发文档要求：

- 仅测试代码变化；
- 仅拼写、格式、注释变化且没有行为改变；
- CI 日志展示、artifact retention 等不改变产品/算法语义的流水线维护；
- 文档本身变化。

如果一个提交表面上是 refactor，但改变了公开数据结构、artifact 内容、默认路径、异常/阻断条件，则仍按实质性更新处理。

## 3. 权威文档映射

### A. 所有 v4 生产语义变化

必须更新：

- `references/v4-change-record.md`

用于记录：改了什么、为什么改、兼容/迁移、验证、回滚点、已知风险。

### B. 生产阶段/能力/默认策略变化

必须更新：

- `references/v4-status.md`

包括但不限于 production/shadow/experimental 状态、默认入口、已接管的事实真源、仍会 BLOCK 的边界。

### C. CLI / 实际操作流程变化

至少更新以下一项：

- `SKILL.md`
- `references/v4-cli-contract.md`
- `references/v4-runtime-guide.md`
- `references/workflow.md`

新的默认 v4 入口优先更新 `SKILL.md` 与 `v4-runtime-guide.md`；跨多个 artifact-writing CLI 的路径、写入、fail-closed 不变量由 `v4-cli-contract.md` 统一负责。

### D. schema / contract / artifact / calibration / release 变化

至少更新以下一项，并同时满足 A/B：

- `references/v4-implementation.md`
- `references/workflow.md`
- 本文件（如果契约本身改变）

涉及 production release authority 的跨 artifact 一致性时，不能只记录“某个 CLI 检查了一个字段”；owner 文档必须明确哪些 hash-bound config/evidence/QA 层共同构成 release contract，以及任一层矛盾时的 fail-closed 语义。`references/v4-cli-contract.md` 可记录具体 CLI 不变量，但仍需满足本 D 类 owner 要求。

### E. 架构职责或目录边界变化

至少更新以下一项，并同时满足 A/B：

- `references/v4-implementation.md`
- `references/v4-architecture-review-2026-08-17.md`

新增 `timeline/`、`evidence/`、`alignment/`、`fusion/`、`calibration/`、`cache/` 等长期层次时适用。

## 4. CI 规则

CI 对 PR 的完整 base..head diff 做检查，不只看最后一个 commit。

- 有实质性生产代码变化而 `v4-change-record.md` 未变化：FAIL。
- v4 核心能力/状态变化而 `v4-status.md` 未变化：FAIL。
- CLI 发生变化但 SKILL/CLI-contract/runtime/workflow 均未变化：FAIL。
- schema/contract 类变化但 implementation/workflow/contract 文档均未变化：FAIL。
- 架构目录/职责变化但 implementation/architecture 文档均未变化：FAIL。

测试文件、文档文件、CI 自身维护不触发“为了通过契约再改文档”的递归要求。

## 5. 禁止的绕过方式

- 只改一个与本次变更无关的 Markdown；
- 在 change record 写“update docs”但不描述实质变化；
- 修改阈值/默认模型但不改变 calibration profile identity；
- 代码先合并、后续再补文档；
- 用 `skip`, `ignore`, commit message 或 PR label 绕过 CI。

如确有纯内部重构不应触发契约，应调整 `validate_docs_contract.py` 的组件分类规则并同步更新本文件，而不是临时跳过 CI。

## 6. 与 production-first 的关系

v4 采用 production-first：尽早进入真实任务，真实失败与 review candidate 反哺校准和算法升级。越是快速迭代，越需要文档契约保证：

- 当前默认路径可被准确复现；
- 线上/真实任务遇到的问题能对应到具体版本、profile 和 artifact；
- 下一轮 AI/Codex 不会基于过期说明重复设计或错误接线；
- 退役 v3.9 后，回滚依赖 Git tag/commit + artifact lineage，而不是维护第二套生产说明。

## 7. Max segmentation / release authority contract（2026-08-22）

Max 必须把以下三类状态分开记录，不能用一个 `ready` 布尔值互相替代：

```text
projection authority
review completeness
subtitle segmentation authority
```

具体约束：

- `ready_for_render` 只表示 reconstruction/review 足以进入 renderer，不自动代表 production publish；
- canonical LRC line break 不是 final subtitle cue segmentation authority；
- 当前 canonical-line renderer 必须显式声明 `segmentation_authority=canonical_line_evaluation_only` 与 `publish_ready=false`；
- V4 production release 的 final-render `normalized_config`、artifact `evidence` 与 exact hash-bound QA 必须对 `editor_reconciled` / `publish_ready` 保持一致；任一层仍为 evaluation-only、not-publish-ready 或带 `release_blocked_reason` 时必须 fail closed；
- 上述三层一致只是 production authority 的必要条件，不是来源证明。当前仓库尚无可授予 `editor_reconciled` 的 production reconciliation/materialization artifact contract，因此 release validator 必须在一致性检查后继续 fail closed；自声明字段不能替代 first-class reconciliation provenance；
- 只有未来 first-class production Editor-Cue Reconciliation artifact 能绑定 exact editor/source SRT、canonical occurrence/timeline identity、source render identity 及任何用于推翻 editor boundary 的更强证据，并被 release validator 验证后，才允许移除当前 production-release sentinel；
- transition/cut/overlap 人工 review 完成、artifact hash 完整、或 run 已 `ready_for_render` 都不能替代 editor reconciliation authority；
- `projection_coverage.authority_omitted_line_count > 0` 属于内容完整性 blocker：unproven canonical suffix 不得被 extrapolation 恢复普通 timing authority，也不得被静默丢弃后继续 final render。

这些规则是 release contract，不是可通过降低 acoustic/transition threshold 绕过的 calibration 参数。

## 8. Artifact-writer filesystem ownership contract（2026-08-23）

生产/evaluation CLI 对 output path 或 output tree 的所有权也属于 contract，而不是普通参数校验。只要 writer 能创建 lock/cache/session/stage/artifact，就必须在**第一次 filesystem mutation 之前**证明写入目标与 task/upstream provenance 不冲突。

当前 Max contract 要求：

- task manifest 与 manifest-bound file/directory subtree 是 protected inputs；
- 直接 CLI upstream/config input 以及 payload 中声明的 `*_path` lineage 也是 protected inputs；
- 单文件 outputs 必须彼此不同且不得落入 protected input directory；
- 动态 output/cache tree 必须与 protected inputs 双向不相交：既不能进入输入，也不能反向包住输入；
- canonical/optimized/legacy orchestration 以及 resolve-assets/coarse/Fine/transition direct stage entrypoint 都必须在原实现首次写入前执行同一 fail-closed ownership preflight；
- 为证明安全修复没有混入算法变化，可以把原 implementation 以 blob-identical internal source resource 保存，但该 resource 不能成为绕过 public preflight 的第二个支持入口。

该 filesystem ownership contract 不授予新的 timing、text、review、segmentation 或 release authority；它只保证通过 task fingerprint / upstream lineage 验证后的证据不会被后续 writer 自己污染。具体 CLI 级不变量与 regression 见 `references/v4-cli-contract.md`。
