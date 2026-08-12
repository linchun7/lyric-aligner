# 多语言混剪歌词字幕工作流

## 目录

- 默认版本与核心原则
- 每次任务需要的输入
- 处理阶段 0–6
- 建议的最终人工检查
- Skill 资源与本地任务文件

## 默认版本

- 下次新任务默认使用 `scripts/redo_karaoke_pipeline.py` 中的算法 `v3.7`。
- `v3.6` 及更早版本只用于结果回滚和差异诊断，不再作为新任务起点。
- `private/<任务名>/qa/*_manual_overrides.json`、`private/<任务名>/qa/*_regression_cases.json` 等包含具体 cue、歌词和毫秒时间的文件只属于对应输入。新任务必须创建自己的覆盖与回归文件，且由输入文件 SHA-256 限定作用域。
- QA 文件的统一元数据格式和全局改动记录要求见 `references/change-record.md`；新文件使用顶层 `source_srt_sha256`，缺少或不匹配时必须拒绝加载。

## 核心原则

1. 剪映 SRT 是已有规范歌词字幕格的主要时间参考，不用 BPM 单独推算歌词时间。
   剪映格内的文字可以完全错误，但只要该格对应规范 LRC 中的真实歌词，就保留格子的起止时间并替换文字。唯一例外是只含规范 LRC 未收录的短促衬词（ad-lib）的格子：该格直接不进入终稿。
2. 原曲音频用于检测实际截取位置、速度变化和剪切点；不会复制或修改原曲文件。
3. BPM 变化是速度比例先验，不是时间轴权威。例如原曲从 103 BPM 调到 130 BPM，理论“原曲时间/混剪时间”斜率为 `130/103`。程序用它归一波形、约束候选和检查异常；剪切只改变局部偏移。
4. LRC 提供最终原语言文字，ASR 只用于发音、顺序和断句复核。
5. 没有可靠证据时保留剪映文字并进入审计，不让低置信候选直接污染终稿。
6. 剪映没有创建字幕格的韩文空档，可根据原曲映射新增字幕。
7. 剪映把多句韩文合并成长占位格时，可在原格覆盖范围内拆分。
8. 规范歌词的一整句若跨过多个可信的剪映时间格，按实际唱到的字词切片，不强求每格语义完整。例如前格音频已经唱到“死去的”、后格从“回忆”起，就写成前格“……死去的”、后格“回忆……”。

## 每次任务需要的输入

- 40–60 分钟混剪音频。
- 剪映导出的 SRT。
- 歌曲清单，包含混剪中的大致开始时间、歌手和歌名。
- 每首歌的主要语言，建议写为 `zh`、`en`、`ko`、`ja` 或 `mixed`；混合语言可写成 `ko+en` 等。
- BPM 变化清单，建议使用 `歌手 - 歌名原BPM-混剪BPM` 格式。
- 每首歌的原语言 LRC。
- 推荐提供每首原曲音频；FLAC、WAV、MP3、M4A 均可。

原曲版本应尽量与混剪使用的版本一致。若有 remix、伴奏版或不同发行版，应在文件名中标明。

## 处理阶段

### 0. 检查环境并初始化任务

```powershell
python scripts/check_environment.py
python scripts/init_task.py `
  --task "任务名" `
  --source-srt "private/任务名/input/剪映.srt"
```

需要运行 ASR 时，将环境检查改为 `python scripts/check_environment.py --asr`。初始化命令只创建被 `.gitignore` 排除的任务目录和 QA JSON 骨架，不复制或修改输入文件。

### 1. 建立保守文本基线

```powershell
python scripts/redo_karaoke_pipeline.py prepare `
  --audio "混剪.wav" `
  --srt "剪映.srt" `
  --song-list "歌曲清单.txt" `
  --lyrics-dir "原歌词" `
  --out-dir "output/任务名/01_prepare"
```

这一阶段只自动接受高相似度、单行、顺序一致的文本匹配。

### 2. 原曲与混剪实际音频对齐

```powershell
python scripts/redo_karaoke_pipeline.py audio-align `
  --audio "混剪.wav" `
  --srt "剪映.srt" `
  --song-list "歌曲清单.txt" `
  --lyrics-dir "原歌词" `
  --source-dir "原曲音频目录" `
  --bpm-changes "歌曲名称及bpm变化.txt" `
  --out "output/任务名/02_audio_alignment.json"
```

对齐仍以实际波形和歌词锚点为主。BPM 用于预先归一速度、约束候选路径和检查斜率；不能单独决定歌词起点。每首歌保存 BPM 理论比例、波形比例、起始偏移、残差和原曲哈希。

短而重复的文字（如 `na na`、`la la` 或重复的短副歌）即使相似度高，也不能单独充当全轨锚点。至少两个文本锚点须与归一后的波形偏移一致，否则由模型结合前后歌词顺序判为重复句歧义，并退回波形/BPM 路径。

若歌曲中间剪掉一截，归一后的波形路径会表现为“混剪时间连续、原曲时间突然向前跳”。程序把它输出为 `edit_candidates`，记录混剪切点、被跳过的原曲起止时间和残差。只有波形两侧稳定，且相邻文本锚点也支持同一跳跃（或该语言没有足够文本锚点）时，才列为阻塞发布的 `review`；明显的重复段误匹配只记为 `informational`。`review` 不能自动删除歌词，模型须将其标记为 `confirmed` 或 `rejected`。

确认后的剪切采用分段映射：切点前后各自计算偏移，BPM 斜率保持不变；位于被剪原曲区间内的 LRC 行标记为 `confirmed_source_cut`，不得进入插入候选。多处剪切按多个分段依次处理，不能再用全曲一条直线拟合。

### 3. 固定时间格投影及漏行补全

```powershell
python scripts/redo_karaoke_pipeline.py build `
  --srt "剪映.srt" `
  --song-list "歌曲清单.txt" `
  --lyrics-dir "原歌词" `
  --audio-alignment "output/任务名/02_audio_alignment.json" `
  --out-srt "output/任务名/03_projected_draft.srt" `
  --out-report "output/任务名/03_projected_review.csv" `
  --out-mapping "output/任务名/03_track_mappings.json"
```

输出报告会区分：原字幕格替换、保留原文、空档新增、长占位格拆分。

分配时必须同时满足“格内时间优先”和“歌词索引单调递增”。LRC 若把相邻两句标得过近，不能逐句按最近时间贪心塞入同一格；应结合相邻剪映识别文本，把第一句回填到前一格、第二句保留在后一格。

逐格候选完成后，再按每首歌运行一次全局序列对齐。状态必须明确区分：字幕格跳过、歌词事件跳过、一个格合并多条连续 LRC，以及同一条 LRC 跨相邻两个格延续。短重复句不能单独决定位置；全局路径只在文字/发音和时间证据明显强于局部结果时落地。

全局修正采用事务式覆盖保护：可以补入缺失事件，或把重复事件移到正确出现位置；但若一次替换会让某条原本唯一覆盖的 LRC 在所有实际字幕格中消失，必须拒绝替换。被剪掉的歌词应在音频映射阶段先登记，不能在这里静默删除。

正确歌词与原格断句不一致时，先把规范歌词做成连续字词序列，再依据各格中实际可听见的字词切片。只有通过波形或词级 ASR 明确找到更准确的起音点，才允许调整或新增边界；不能仅为了语义断句漂亮而把整句提前或推迟。

程序会对高置信、同语言的相邻边界自动迭代重切：把相邻格视为连续歌词序列，比较剪映两格的实际识别内容，自动移动错分到前后格的规范歌词片段，再重新检查相邻边界。重复副歌、韩文谐音、低相似度和时间边界本身错误不自动修改，继续进入人工听音。

边界评分同时包含“整格相似度”和“边缘单位相似度”。英文按词、中文按字检查前格末尾和后格开头；若最多移动 3 个单位后两侧都与剪映观察达到至少 96% 一致，可自动修复，即使长句导致整格平均提升不足 0.12。该规则只重新分配文字，不改变时间。

短促衬词（`ad-lib vocalizations`，也称无实义发声）包括 `Uh`、`Oh`、`Yeah`、`Ah`、`Ooh`、`Huh`、`Na-na` 等。它们只有出现在规范 LRC 中时才作为歌词保留并参与对齐。若规范 LRC 未收录，即使剪映、ASR、原曲和混剪音频都检测到，也不新增、不保留、不列为可选项；默认不提供“完整发声模式”。剪映单独识别到的衬词也不得把 LRC 中唯一的同类词挪到相邻格。

若人工试听确认剪映边界本身提前或延后，应成对修改前格结束时间和后格开始时间，并记录为 `manual_timing_review`。该听音边界的优先级高于剪映原时间，后续边界检测不得再建议恢复到未经确认的剪映切点。

为了减少下次人工量，人工校正只保留三种：时间边界本身错误、真实重复次数无法从文本确定、单词确实听不清。普通的“整句被挤到前一格或后一格”应由连续序列重切自动完成。

### 4. 低证据语言复核

所有语言都先使用原曲映射确定正确 LRC 范围；ASR 只提供实际发音、顺序和边界观察，不能覆盖规范 LRC。词级时间戳必须先按整首歌做单调序列对齐，再用于相邻格的前缀/后缀切分，不能逐格独立选择重复副歌。

当前 `v3.7` 的语言能力边界如下：

- 中文：规范文本按汉字做相邻格边缘重切；剪映中文通常可作为较强文字观察。对同音错字和完全空白段，仍以 LRC、原曲映射和 ASR 为准。
- 英文：规范文本按词做边缘重切；保留 LRC 中已有的缩写、否定词、连字符跨格和短促衬词（ad-lib）。口语缩写、吞音或脏词消音时，ASR 只作证据，不改写规范歌词。
- 韩文：可将 Hangul 转成近似罗马音，与剪映英文谐音比较；必要时运行独立韩文 ASR。当前脚本支持该能力。
- 日文：现阶段可使用剪映格、LRC 顺序、原曲波形和通用 ASR，但尚未实现“汉字转实际读音”的完整层。假名可按字符辅助对齐；含汉字且剪映为空/谐音严重时，不能把自动结果声称为与韩文同等级置信度，必须进入直接听音复核，或先启用日文读音模块。
- 混合语言：以歌曲和片段为单位标记语言，不能让整首歌的单一语言假设覆盖代码切换段。英文短促衬词只有在规范 LRC 中存在时，才与中、韩、日正文一并保留。

当前 `scripts/validate_korean_asr.py` 仍是韩文专用验证脚本，不是完整的多语言验证器。新任务若包含大段日文或其他低证据语言，应先完成 `references/multilingual-roadmap.md` 中的 P0 项，或者把相关候选明确列入对话复核。

需要韩文 ASR 时先生成验证结果：

```powershell
python scripts/validate_korean_asr.py `
  --audio "混剪.wav" `
  --jobs "private/任务名/qa/korean_asr_jobs.json" `
  --out "output/任务名/04_korean_asr.json"
```

然后运行：

```powershell
python scripts/redo_karaoke_pipeline.py refine-korean `
  --srt "剪映.srt" `
  --song-list "歌曲清单.txt" `
  --lyrics-dir "原歌词" `
  --audio-alignment "output/任务名/02_audio_alignment.json" `
  --asr-json "output/任务名/04_korean_asr.json" `
  --in-report "output/任务名/03_projected_review.csv" `
  --out-srt "output/任务名/05_refined.srt" `
  --out-report "output/任务名/05_refined_review.csv"
```

没有韩文 ASR 任务时跳过 `validate_korean_asr.py` 和 `refine-korean`。进入下一阶段前设置报告路径：

```powershell
# 运行过 refine-korean 时：
$ReviewReport = "output/任务名/05_refined_review.csv"

# 未运行 ASR/refine-korean 时改为：
$ReviewReport = "output/任务名/03_projected_review.csv"
```

### 5. 模型冲突确认与终稿合成

大模型只确认候选冲突，不凭歌词语义自行创造时间。输入证据包括：相邻剪映格、对应 LRC 序列、BPM/波形投影、词级 ASR、前后已确认锚点。

- 有可靠剪映格但歌词语言识别失败：使用混合模式，剪映起点逐毫秒保留，空白段只在相邻锚点之间插值。
- 整段确实没有剪映格：才允许新增字幕。
- 仅当一首歌没有任何可靠剪映格时，才允许整轨重建。
- 一个剪映格实际唱了两句而程序漏掉中间 LRC 索引时，在原格内拆分，不能用后一整句覆盖原格。
- 多句合并导致的孤立 LRC 覆盖缺口必须阻止发布。例如已表示索引为 `24, 26, 27`，第 25 行既未被相邻终稿文字吸收，也未处于确认剪切区间时，必须由模型决定拆格或跨格切片。

```powershell
python scripts/redo_karaoke_pipeline.py finalize `
  --srt "剪映.srt" `
  --song-list "歌曲清单.txt" `
  --lyrics-dir "原歌词" `
  --audio-alignment "output/任务名/02_audio_alignment.json" `
  --in-report $ReviewReport `
  --manual-overrides "private/任务名/qa/任务名_manual_overrides.json" `
  --out-srt "output/任务名/任务名_FINAL.srt" `
  --out-report "output/任务名/任务名_FINAL_审计.csv"
```

仅对确实需要补空白格的歌曲追加 `--hybrid-track "歌曲名"`；需要整轨重建时追加 `--rebuild-track "歌曲名"`。这两个参数均可重复，也都可以省略。

### 6. 最终 QA

```powershell
python scripts/redo_karaoke_pipeline.py qa `
  --source-srt "剪映.srt" `
  --final-srt "output/任务名/任务名_FINAL.srt" `
  --report "output/任务名/任务名_FINAL_审计.csv" `
  --song-list "歌曲清单.txt" `
  --lyrics-dir "原歌词" `
  --audio-alignment "output/任务名/02_audio_alignment.json" `
  --manual-overrides "private/任务名/qa/任务名_manual_overrides.json" `
  --regression-cases "private/任务名/qa/任务名_regression_cases.json" `
  --out "output/任务名/任务名_FINAL_QA.json" `
  --out-review "output/任务名/任务名_FINAL_复核.csv"
```

必须通过：

- 字幕编号连续。
- 无空文本、负时长、乱码和歌词元数据。
- 除源文件已有叠加字幕外，无新增重叠。
- 未重建歌曲的原剪映时间格逐毫秒保留。
- 所有自动修改均能追溯到 LRC、原曲映射、ASR 或人工覆盖。
- 不允许出现“前一格仍为 `jianying_only`，后一格却合并了两条连续 LRC”的序列冲突；此类情况必须自动修复或使 QA 失败。
- 同一歌曲的 LRC 索引不得向后跳；规范歌词在相邻格之间的最佳字词切点若与终稿明显不一致，列入边界复核。
- 混合模式采用的每个剪映起点必须零漂移；任何偏移直接使 QA 失败。
- BPM 理论比例与波形主路径斜率冲突超过 4% 时阻止发布；少数局部异常段只记警告，因为它可能是剪切点。
- 检查所有未关联 LRC 事件，不只检查孤立漏行；邻近终稿没有实际唱词时，无论单句还是连续多句都阻止发布。
- 连续缺口必须明确归类为音频剪切并登记在 `_confirmed_omitted_lrc_events`，否则不能被 QA 当作已处理。
- 所有波形跳点候选必须明确标成 `confirmed` 或 `rejected`；仍为 `review` 的剪切候选阻止发布。
- 已确认剪切区间内的 LRC 不算漏词；区间外的孤立歌词缺口即使只有中等文字证据，也阻止 `publish_ready=true`。
- 非旁白、非哼唱的 `keep_existing` 必须进入残留复核，不能因为结构 QA 正常就直接发布。
- 连字符跨格（如前格 `what-`、后格 `-at`）视为一个词的时间切片，保留两侧连字符并检查拼接后是否还原规范歌词。
- 真实重复的短副歌不能仅因文字相同而去重；重复次数应按原格和音频保留。
- 已被剪映准确识别的前格末尾/后格开头短词不得仅因 LRC 换行而移到另一格；QA 回归应覆盖边缘词，而不只覆盖整条 LRC 是否出现。
- 小于 300ms 的前缀格若已完整包含在紧邻下一格（如 `and` → `and ain't...`），标记为“下一格已吸收”，不作为文字错误；若下一格未包含，则仍阻止发布。
- 歌词字幕不以阅读速度或字符/秒作为错误判据；只检查唱词是否正确、出现和消失时间是否对应音频。
- 具体歌词、cue 编号和毫秒时间只能存放在任务专属回归文件中；回归文件必须包含源剪映 SRT 的 SHA-256。哈希不一致时 QA 必须拒绝加载，禁止把上一次人工结论套到新歌曲或新混剪。
- `*_manual_overrides.json` 与 `*_regression_cases.json` 都必须包含 `schema_version`、`project`、`source_srt_sha256` 和 `scope` 顶层元数据；新 override 使用 `source_srt_sha256`，旧的 `_source_srt_sha256` 只作为迁移兼容字段。
- 短促衬词（ad-lib）必须关联到规范 LRC 索引；只有剪映、ASR或音频证据而没有 LRC 索引的 `Uh/Oh/Yeah/Ah/Ooh/Huh/Na-na` 等不得进入终稿，QA 中 `noncanonical_vocalization_count` 必须为 0。

`passed=true` 表示结构检查通过；只有 `publish_ready=true` 才表示高风险边界和错字残留全部清零。高风险候选未试听或未人工确认时，命令返回失败状态，阻止把半成品误当终稿。

## 建议的最终人工检查

在 Subtitle Edit 中加载混剪音频和终稿 SRT，重点播放审计 CSV 中 `confidence=review` 的行，以及所有 `inserted`、`split`、`rebuilt` 行。只修改歌词文字或明确标记的重建行，不批量平移全轨时间。

## Skill 资源与本地任务文件

- 通用处理器：`scripts/redo_karaoke_pipeline.py`
- 韩文独立 ASR 验证：`scripts/validate_korean_asr.py`
- 当前默认算法：`v3.7`
- 当前任务人工覆盖和项目回归：`private/<任务名>/qa/`（本地，不上传）
- 当前任务输入：`private/<任务名>/input/`（本地，不上传）
- 当前任务草稿、审计、QA 和回滚结果：`output/<任务名>/`（本地，不上传）
