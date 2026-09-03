# Freeze Review — 2026-09-03

## 审计范围

入口/legacy、版本身份与 selector 真源、异常/断言、path ownership、task manifest / run-config / fingerprint、artifact lineage 与 release authority、依赖/环境、随机性/确定性、正式输出与 identity JSON 写入原子性、privacy、tests hollow-green、docs drift、artifact retention、structural/ablation closeout。

## 实际修复

- 补齐 runtime base 的 `soundfile` direct dependency，并让环境预检与 repository contract 明确覆盖它；requirements / environment 变化纳入 documentation contract。
- TrackAssets manifest 改用已有 shared `atomic_write_json()`；payload/schema/asset selection 不变。
- task manifest / QA JSON 与 task-local `v4_run_config.json` 原先虽然名为 atomic writer，但使用固定 `<name>.tmp`；现统一复用唯一同目录 tempfile + flush/fsync + `os.replace` 的 shared writer，消除并发写临时名竞争，不改变 JSON payload、task fingerprint 或 semantic fingerprint。
- canonical evaluation render 的 SRT 与 audit CSV 原先直接覆盖目标文件；现按同一 release 链后置 materializer 的既有模式使用同目录 tempfile + flush/fsync + `os.replace`，避免中断留下截断正式输出。SRT/CSV 字节语义、QA、artifact lineage 与 `publish_ready=false` authority 不变。
- 修正 directory-input path-safety 过期注释；澄清旧 Smart/Pro freeze tag 仅是历史 baseline，当前 selector 仍为 Smart v1.2.10 / Pro v1.2.6；明确旧 `change-record.md` 与当前 V4 change record 的职责边界。

## 深审结论

- `smart_current.py` 仍是 current Smart 唯一生产真源，绑定 v1.2.10；Pro policy 仍为 v1.2.6；Max/package/docs 当前版本一致为 `4.0.0a14`。没有发现 production consumer 绕开 current facade 或自建 selector。
- release validator 仍逐文件 hash 绑定 final SRT/audit/QA，并要求 production final-render 三层同时满足 `editor_reconciled`、`publish_ready=true`、无 `release_blocked_reason`；canonical evaluation render 继续 fail closed 为 `publish_ready=false`。
- 生产 Python 未发现 `shell=True` / `os.system`；算法结果未发现未固定随机性。安全随机 token 仅用于 verification session / run lock。
- public tests 未发现 `assertTrue(True)`、明显空集合真通过或大面积 conditional skip；唯一极窄 SkipTest 是 LibreOffice Python 环境缺少 system launcher 时的兼容分支。
- legacy pre-v4 pipeline 保持 fail-closed migration wrapper；artifact retention 不按版本盲删；2026-09-03 calibration-only ablation 结论仍成立：保留 Chroma+MFCC、Fine、Piecewise capability，不新增 `--skip-fine` / 新 heuristic gate。

## 明确未改

Fine/MFCC/Piecewise TimeWarp 算法、Smart immutable version chain、Pro/Max timing/text mutation authority、`_v4_*_impl` wrappers、redo compatibility、production timing/text/release semantics、private blind/truth；不增加新算法能力，不消费未观察 blind truth 调参。

## 封板原则

没有新的真实 production failure 或独立 structural truth，不重开已否决 heuristic；diagnostic 不升级 mutation/release authority。封板维护只接受能明确降低错误、漂移、并发/中断损坏或发布误判风险的窄改动。

## 当前验证证据

- 上一版 clean 隔离提交完整 suite：814/814 OK；该证据早于本轮新增 durability 修复，因此只作为历史基线，不作为最新候选最终验收。
- 当前 dirty candidate targeted：docs-contract 10/10、repository/environment contract 18/18、TrackAsset resolver 6/6、render 3/3（含两条 replace-failure 故障注入）、run-config 6/6（含 task/run identity writer 故障注入）、compileall、environment preflight、Skill validation 均通过。
- privacy scan 在清理本轮 8 个 disposable full-suite runner/log 后重新通过：`ok=true, issues=[]`。此前唯一命中来自本轮临时 runner 内的本机绝对路径，不是 tracked source/privacy leak。
- 封板最终验收必须在包含本轮全部改动的 clean commit 上完成完整 suite、documentation validator、privacy/Skill、`git diff --check`，并在合入主线后确认 `HEAD == origin/main` 且工作树 clean；不以 dirty-tree 或旧候选的绿灯替代这些条件。
