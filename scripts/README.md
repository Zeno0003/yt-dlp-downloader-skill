# weibo_collection_sync.py

把一个微博作者的**某个视频合集**的条目清单抓下来，首次全量、之后增量，
并**原地重写同一份产物**（单一真源）。默认目标：老马自奋蹄（uid `1807436544`）
的「商业创新」合集（`t_id=5084258226602032`）。

## 跑法

```bash
PY=/Users/zeno/.workbuddy/binaries/python/versions/3.13.12/bin/python3
S=~/.workbuddy/skills/yt-dlp-downloader/scripts/weibo_collection_sync.py

$PY $S                      # 增量（首次自动全量）
$PY $S --dry-run            # 只抓不写，先看摘要
$PY $S --full               # 强制全量翻页（约 95 秒）
$PY $S --site-total 472     # 站方标注集数变了，重跑一次锚定列
$PY $S --full --allow-shrink  # 作者删了视频，接受条数变少
$PY $S --outdir /别的目录     # 换输出目录
```

## 依赖

只用 Python 标准库，不需要装任何包。网络直连微博即可。

## 状态与产物

产物默认写到 `/Users/zeno/WorkBuddy/yt-dlp视频转文本/data/`：

| 文件 | 说明 |
|---|---|
| `商业创新合集_条目.tsv` | 主清单。8 列：序号_由旧数起 / 站方集数_已锚定段 / 微博ID / mblogid / 发布时间 / 时长 / 标题 / 链接 |
| `商业创新合集_条目.md` | 同数据的 Markdown 表格版 |
| `商业创新合集_urls.txt` | 单条链接，每行一个，可直接 `yt-dlp -a` |
| `.sync_state.json` | 同步状态（游标、锚定参数、退出原因、上次同步时间） |
| `.known_ids.txt` | 一行一个微博 mid，用于增量 diff |

> 产物名**故意不带条数**。条数一变名字就废，目录里会留孤儿文件。条数写在文件头。

## 它是怎么工作的

**接口**：`weibo.com/ajax/profile/getWaterFallContent?uid=<uid>&cursor=<cursor>`
（这是微博给「个人主页 → 视频」标签页供数据的接口，也是 yt-dlp `WeiboUserIE` 用的同一个）。
抓之前要先做一次**访客握手**拿 guest cookie：`passport.weibo.com/visitor/genvisitor`
→ `passport.weibo.com/visitor/visitor?a=incarnate&...`。

**为什么不用 yt-dlp**：这个接口没有合集过滤参数（`collection_id` / `album_id` / `filter`
实测都被忽略），只能全量翻页后本地筛。yt-dlp 的 flat-playlist 也能枚举，但会丢掉
判定合集所需的字段，且慢。直接打接口 752 条约 95 秒。

**合集判据**（三级，但**整体降级**、不是逐条兜底）：

1. `page_info.actionlog.ext` 里的 `t_id:<id>|t_name:<名称>` —— **日常只用这一级**
2. `topic_struct[].topic_title`
3. `text_raw` 里的 `#话题#`

> ⚠ 这三级的严格程度差很多。同一份数据实测：**强判据 469 条 / 话题判据 488 条 /
> 正文判据 526 条**。因为「打了同一个话题」≠「属于这个合集」。
> 所以脚本是**先用强判据跑一遍，只有它整体 0 命中（说明字段改版了）才降级**，
> 并会打印警告和相关口径供人工核对。**不要**写成逐条 `or` 兜底 —— 那会把 469 变成 526。

**排序**：列表接口返回的是 **微博 id 降序**（= 帖子的先后）。产物按 id 升序（由旧到新）
输出。**不用发布时间排序** —— 同一批视频常在同一天发出，时间字段不可信。

**增量怎么省时间**：既然返回是 id 降序，翻到「已知最旧那条」就说明区间覆盖完了，直接停。
实测约 3 页 / 10 秒。兜底：连续 2 页没命中就停；最多翻 8 页，触上限自动转全量。

## 集数锚定（最容易搞错的地方）

站方给合集标「第 N 集」，但这个数字**公开接口拿不到**，只能靠两端的锚点推：

```
tail_len = ((site_total - 1) % 20) + 1     # 站方每页 20 条 → 末页剩余条数
gap      = site_total - 实抓条数
集数列：
  序号 <= head_len              → 序号
  序号 >= N - tail_len + 1      → 序号 + gap
  其余                          → "—"     ← 中段一律不猜
```

- `head_len` 默认 41：依据是用户从地址栏复制的 `first_cursor` 必落在 20k+1 的页首，
  实测 #1/#21/#41 三个游标与站方 1/21/41 对齐（gap=0），即头部到 41 是准的。
- **严禁拿作者标题里的「第N期」反推集数**：合集里夹着非编号内容，偏移会累积
  （实测第 11 期偏移 0、第 71 期偏移 −6）。
- `site_total` 只能人工从站上看，存 `.sync_state.json`。跑了新数据后脚本会提示
  「若站方标注已变为 X，请 `--site-total X` 重跑」。

**`gap` 就是缺口信号**：

| gap | 含义 | 动作 |
|---|---|---|
| 保持 2 | 正常，缺口没动 | 摘要里带一句 |
| 变 0 | 缺口被补上了 | 提示「缺口已闭合」，全表连续编号 |
| 变负 | 实抓比标注多（重复发布/计数滞后） | 中段留 `—`，提示人工核对 |
| 变大 | 缺口扩大 | 提示去站上核对总数，再 `--site-total` 重跑 |

## 幂等与容错

- **写文件一律 tmp + `os.replace`**，不会留半截文件。
- **无变化就不写**：产物渲染结果与现有文件逐字节比较，相同则跳过写盘（md5 保持不变）。
- 握手 / 翻页失败：重试 3 次（每次重试都重新握手），仍失败 → **退出码 2，产物与状态零改动**。
- 抓到 0 条 → **退出码 3**，拒绝写入（防接口改版把清单清空）。
- 全量模式下条数变少 → 默认拒绝写入；确认是作者删除才加 `--allow-shrink`。

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 2 | 网络失败，什么都没动 |
| 3 | 数据异常（抓 0 条 / 条数变少），拒绝写入 |
| 4 | 参数或环境错误（如输出目录不存在） |

## 边界（这些情况它解决不了）

1. **公开列表只覆盖「视频」标签页**。作者若把某集发成转发/图文，或已删除，
   接口永远给不到 ⇒ 缺口可能**永远补不上**。脚本只能靠 `gap` 监测，不承诺"
   跑够次数就能对齐"。
2. **`t_id` 不硬编码**：默认从状态文件读；换合集用 `--t-id` / `--t-name` 覆盖。
3. 未公开的 ajax 接口，字段随时可能变。三级判据 + 「抓 0 条就拒绝写入」是兜底。
4. 增量模式**检测不到删除**（没翻到那么深）。要查删除得 `--full`。

---

# batch_pipeline.py —— 一键批量转写

把「序号范围」变成「一堆 TXT」：挑条目、拼单条链接、批量下载、校验时长、幂等转写、
保留/清理视频、可选合并长文，全在内部做完。**零上下文也能跑对。**
入口说明见 `SKILL.md` 的「一键批量转写流水线」；给人粘贴的提示词见 `批量转写_提示词模板.md`。
**下面的参数表是唯一权威列表。**

```bash
PY=/Users/zeno/.workbuddy/binaries/python/versions/3.13.12/bin/python3
S=~/.workbuddy/skills/yt-dlp-downloader/scripts/batch_pipeline.py

$PY $S --status            # 进度：清单/已转/待转/下一步建议/当前热压
$PY $S 1-60 --dry-run      # 看会跑哪些，不实际跑
$PY $S 1-60                # 跑序号 1–60
$PY $S --next 40           # 从第一个未转写的开始跑 40 条（最省心）
$PY $S --ids 5084582845615713,5084945234006672
$PY $S 1-60 --guard        # 开自动冷却（热压 serious 就暂停）
$PY $S --next 40 --merge   # 转完顺手合并成长文
$PY $S 1-60 --merge-only   # 只补合并稿，不下载不转写
$PY $S --delete-video      # 转完删 mp4（默认保留）
$PY $S --cleanup           # 单独清 mp4（每轮 ≤50 个）
```

**⚠ 必须用 `run_in_background` 跑**。前台会被 120 秒超时杀掉，只留半截文件。

| 参数 | 作用 |
|---|---|
| `<range>`（如 `1-60`） | 按清单序号范围选 |
| `--next N` | 从第一个未转写的开始跑 N 条（推荐，不用记序号） |
| `--ids a,b,c` | 直接指定微博 id |
| `--tsv 路径` | 换清单（默认 `商业创新合集_条目.tsv`） |
| `--status` | 只看进度，什么都不动 |
| `--cleanup` | 只删「已转写成功」的 mp4；幂等可反复跑；**每轮 ≤50 个** |
| `--quality-scan` | 扫本地已有的 mp4，刷新画质台账后退出（幂等） |
| `--dry-run` | 只打印计划 |
| `--guard` | 开自动冷却（热压到阈值暂停，回落继续） |
| `--guard-at fair\|serious` | 冷却阈值，默认 `serious` |
| `--delete-video` | 转完删 mp4（**默认保留**，不删） |
| `--skip-download` | 跳过下载（视频已下过时用） |
| `--merge` | 转写完成后合并成一份带范围文件名的长文 |
| `--merge-only` | 只合并（按范围），不下载不转写 |
| `--emit-ids 路径` | 写出一份 id 清单后退出 |

- 退出码：`0` 正常 / `1` 参数错 / `2` 下载失败或无可用文件 / `3` 转写失败
- ⚠️ **单批 ≤ 40 条**（单轮删文件上限 50，仅 `--delete-video`/`--cleanup` 会遇到）

## loop_pipeline.py —— 大范围整夜连续跑

循环驱动 `batch_pipeline.py --ids`，把一个序号区间按 20 条/批连续跑完（2026-09-12 实测
101–469 共 369 条、19 批整夜无人值守跑通：00:44 启动 → 08:21 结束，369/369 成功、0 异常）。

```bash
PY=/Users/zeno/.workbuddy/binaries/python/versions/3.13.12/bin/python3
L=~/.workbuddy/skills/yt-dlp-downloader/scripts/loop_pipeline.py
nohup caffeinate -dimsu $PY $L \
  >> "/Users/zeno/WorkBuddy/yt-dlp视频转文本/data/批量转写_后台日志.txt" 2>&1 &
```

| 参数 | 作用 |
|---|---|
| `--lo N` / `--hi N` | 序号范围（默认 101 → 清单最新，动态） |
| `--batch N` | 每批条数（默认 20） |
| `--rest 秒` | 批间休息（默认 600） |
| `--no-rest` | 跳过批间休息（调试用） |

- **必须 `nohup + caffeinate -dimsu` 后台跑**（防合盖休眠；系统级后台，不走 run_in_background）。
- **防死循环**：连续两轮「零新增 TXT」→ 把卡住的 id 写进 `data/批量转写_异常清单.txt`
  并在后续轮次排除；区间跑完（或只剩异常清单成员）自然退出，退出码 0。
- 启动即初始化 `批量转写_后台日志.txt`（覆盖写文件头，之后每轮追加）；每批在 `data/转写进度.md` 留一行台账。
- 它**不带** `--merge` / `--delete-video` / `--guard`；合并稿跑完另行 `--merge-only`。
- ⛔ 红线写死：不按时长过滤、绝不写 `.unavailable.txt`、不动 `data/` 下清单文件。

## update_collection.py —— 「更新合集」一键入口（终端直跑版）

把「增量同步 → 有新增就下载 + 转写 → 固定格式日志」串成**一条命令**，与聊天里说
「更新合集」等效（2026-09-12 zeno 定的固化流程，见 SKILL.md 同名字节）。
日志追加到 `data/合集更新日志.txt`。

```bash
# 直接跑（已 chmod +x，shebang 指向托管 python3.13）
~/.workbuddy/skills/yt-dlp-downloader/scripts/update_collection.py

# 等价写法
/Users/zeno/.workbuddy/binaries/python/versions/3.13.12/bin/python3 \
  ~/.workbuddy/skills/yt-dlp-downloader/scripts/update_collection.py

update_collection.py --dry-run        # 预演：只检查，不写、不转写
update_collection.py --no-transcribe  # 只同步清单，不转写
```

- 退出码：`0` 正常 / `2` 同步失败 / `3` 解析失败 / `4` 转写失败 / `130` 手动中断
- 单批上限 40 条自动分段；要离开电脑跑时可前缀 `caffeinate -i` 防休眠

## 同伴脚本

| 脚本 | 跑什么解释器 | 干什么 |
|---|---|---|
| `thermal.py` | 任意 python3 | 读 macOS 官方热压状态（osascript 走 JXA，**免 sudo、免编译**，实测 30ms/次）。`--watch 3` 可连续看 |
| `transcribe_batch.py` | **video-subtitle 的 venv python**（要 mlx_whisper） | 单进程批量转写，模型只加载一次；每条记录热压；`--guard` 自动冷却 |
| `weibo_naming.py` | — | 命名/查找/表头的**唯一真源**，上面两个脚本都从它 import |
| `migrate_names.py` | 任意 python3 | 存量素材归位（改名 + 补表头），**默认 dry-run** |
| `batch_pipeline.py` | 任意 python3 | 编排全流程 |
| `loop_pipeline.py` | 任意 python3 | 循环驱动 `batch_pipeline.py`，大范围整夜连续跑（见上节） |
| `merge_transcripts.py` | 任意 python3 | 单条转写→总库长文的合并器（纠错表真源），被 batch_pipeline / make_distill_txt 调用；2026-09-12 从 Downloads 收编进本目录 |
| `update_collection.py` | 任意 python3（带 shebang，可直接执行） | 「更新合集」一键入口：同步 + 转写新增 + 固定日志（见上节） |

## 流程细节（按步了解）

1. **挑条目**：读 `data/商业创新合集_条目.tsv` 的序号列。也可以是 `--next`（第一个未转写的）/ `--ids`。
2. **下载**：微博**没有独立音频流**，只能下合流视频；三档（540/720/1080）的**音轨完全相同**，
   所以**首选最小档 `res:540`**，失败时**逐档上推** 720 → 1080（只补仍缺的那些）。详见下方「画质与音轨」。
   多条 URL 走**同一个 yt-dlp 进程**（`-a` 文件），否则每条都重做一次访客握手，实测慢 3 倍。
   加 `--no-part` 绕开沙箱里的重命名失败。
3. **归位 + 校验**：`.part` 残留自动 `mv` 修复；按 `序号_标题_ID` 改名；
   `ffprobe` 量实际时长与清单比对（容差 max(5s, 3%)），对不上就**删掉重下**，
   避免把半截文件喂给 whisper 产出垃圾文本。
4. **转写**：交给 `transcribe_batch.py`，**幂等**（已存在非空 TXT 跳过）；写表头。
   实测约 26× 实时；20 条 / 9118 秒音频 → 355 秒。
5. **保留/清理**：**默认保留 mp4**（zeno 2026-09-11 要求）。要省空间加 `--delete-video`，
   或事后 `--cleanup`（单轮删 >50 个会被系统安全阀拦，分几轮跑）。

## 热压控制（"电脑发烫"怎么办）

- MLX Whisper 走 **GPU**，约 26× 实时 = 转 1 小时素材约 2.3 分钟**满载** GPU。持续满载就是发烫根源。
- 读的是 **macOS 官方热压信号** `NSProcessInfo.thermalState`（0 nominal / 1 fair / 2 serious / 3 critical）
  —— **这正是系统决定要不要降频的同一个信号**，比外部读温度更贴近"系统觉得烫不烫"。
- Apple Silicon 上 `sysctl machdep.xcpm.*`、`powermetrics` 都拿不到可用读数（后者还要 sudo，
  本环境 sudo 被禁）。所以走 osascript JXA 这条路。
- **策略**：默认不介入（只是记录），加 `--guard` 才自动冷却；`--guard-at fair` 更保守。
- 热压峰值写在 `transcript/_transcribe_report.json` 的 `peak_thermal`，每条记录也有 `thermal` 字段。
- 最终手段永远是**分批**：每批 20–30 条就停。物理散热（垫高、别堵进风口、别放床上）比软件管用。

## 合并成长文

最省事是跑批时加 `--merge`；已有 TXT 只想补合并稿用 `--merge-only`：

```bash
$PY $S 1-60 --merge-only        # 产出 商业创新合集_合并_序号1-60_<日期>.md
```

`merge_transcripts.py` 做四件事：**剥掉表头**、按句边界拼回段落、按日期升序、套**术语纠错表**
（SARS→SaaS、搓衣板→洗衣机板 等；拿不准的另列 `UNCERTAIN` 不擅改）。

## 命名与表头（规则唯一真源：`weibo_naming.py`）

素材一律 `<三位序号>_<标题>_<视频ID>.<ext>`，例如
`001_当前中国企业面临哪些问题与挑战？应该如何创新突破_5084582845615713.txt`。

- 标题会做文件名安全化（`/ \ : * ? " < > |` → `_`、压空白、截 60 字）
- 旧命名 `<ID>.<ext>` / `<标题>_<ID>.<ext>` 仍能识别（`find_media` 兜底），不会重复下载
- 每份 TXT 顶部有表头（序号/标题/时长/发布/链接/模型），merge 会自动剥掉
- `batch_pipeline.py` 与 `transcribe_batch.py` 都从 `weibo_naming.py` import，**改规则只改那一个文件**
- 存量素材归位：`migrate_names.py`（**默认 dry-run**；`--apply` 会先备份 TXT 到
  `_backup_names_<时间戳>/` 并写 `_rename_map_<时间戳>.tsv`，可回滚）

```bash
PY=/Users/zeno/.workbuddy/binaries/python/versions/3.13.12/bin/python3
$PY ~/.workbuddy/skills/yt-dlp-downloader/scripts/migrate_names.py           # 预览
$PY ~/.workbuddy/skills/yt-dlp-downloader/scripts/migrate_names.py --apply   # 执行
```


## 画质与音轨（2026-09-11 实测）

**结论：三档音轨完全一致，只有视频码率不同 ⇒ 转写取最小档即可，画质不影响文字质量。**

用真实文件 `ffprobe` 实测（同一条 5:12 视频）：

| 档位 | 音轨 | 视频码率 | 体积 |
|---|---|---|---|
| `mp4_hd` (540P) | AAC **HE-AAC** / 44100 Hz / 2ch / **128 kbps** | ~410 kbps | 16.2 MB |
| `mp4_720p` | 同上，逐字段一致 | ~584 kbps | 26.8 MB |
| `mp4_1080p` | 同上，逐字段一致 | ~1.29 Mbps | 50.6 MB |

- 微博**没有纯音频格式**（唯一 `vcodec=none` 的 `scrubber_hd` 是预览图，`acodec` 也是 none）
- **没有比 540P 更小的档** ⇒ 540P 是下限

**适用范围（别搞混）**

| 场景 | 策略 |
|---|---|
| **合集批量下载 / 转写**（本脚本） | **540P 优先**，失败逐档上推 720 → 1080（zeno 2026-09-11 确认） |
| 随手给一个链接说「下载视频」 | 仍是 **`-S "res:1080"`** 默认（zeno 2026-09-11 定，不动） |

体积推算（全量 469 条 / 57.9 小时）：540P ≈ 10.8 GB ｜ 720P ≈ 19 GB ｜ 1080P ≈ 35 GB ｜ 仅音轨 ≈ 3.3 GB

⚠️ **一档 404 ≠ 视频没了**。实测 `#38 第37期：为什么会产生云计算？`（`5097628814281550`）
540P 恒 404，但 720P / 1080P 都正常下载。**三档都试过才允许判定失效。**


## 永久失效名单

`<TSV同目录>/.unavailable.txt`，格式 `<视频ID>  <原因>`，`#` 开头是注释。

- **只手工维护，脚本绝不会自动写入**（避免变成静默过滤）
- 记进去后 `--next` / `--status` 不再把它算作待转；每轮会打印被排除的条目
- `--ids` 显式点名时名单不生效（照跑）
- **加之前必须确认三档都试过**（见上）
- 当前：**空**

## 实测基准（2026-09-11）

| 项目 | 数值 |
|---|---|
| 转写产能 | ≈ **26× 实时**（1 小时素材 ≈ 2.3 分钟） |
| 校准批 | 1–60：新转 57、跳过 2、未拿到 1，总耗时 25.0 分 |
| 热压峰值 | **`fair(1)`**——连转 516 秒也没到 `serious`，`--guard` 未触发 |
| 下载 | 60 条 / 1.1GB，热压恒 `nominal(0)` ⇒ **发热全在转写** |
| 全量 | 455 条 / 53.0 小时音频 ≈ GPU 满载 **122 分钟** |

## 产物位置

> 2026-09-12 起媒体与转写稿已迁入项目目录（`/Users/zeno/WorkBuddy/yt-dlp视频转文本`），不再放 `~/Downloads/yt-dlp/`。

| 路径 | 内容 |
|---|---|
| `/Users/zeno/WorkBuddy/yt-dlp视频转文本/audio/<序号>_<标题>_<ID>.mp4` | 下载的视频（**默认保留**，要删加 `--delete-video`） |
| `/Users/zeno/WorkBuddy/yt-dlp视频转文本/transcript/<序号>_<标题>_<ID>.txt` | 转写产物（带表头） |
| `/Users/zeno/WorkBuddy/yt-dlp视频转文本/transcript/_transcribe_report.json` | 逐条耗时/字数/热压 + `peak_thermal`/`cooled_sec` |
| `/Users/zeno/WorkBuddy/yt-dlp视频转文本/合并稿/商业创新合集_合并_序号<lo>-<hi>_<日期>.md` | 合并长文（`--merge` / `--merge-only` 产出） |
| `<TSV同目录>/转写进度.md` | 台账，每批自动追加一行 |
| `<TSV同目录>/下载画质台账.tsv` | 序号/视频ID/分辨率/档位/大小MB，**按 id 覆盖写**（幂等），一眼看出哪些降级到 720P/1080P |

## 新对话怎么用

看同目录 `批量转写_提示词模板.md`（项目根目录也有副本）—— 复制粘贴即可，零上下文也能跑对。


