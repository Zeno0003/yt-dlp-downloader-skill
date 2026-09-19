---
name: yt-dlp-downloader
description: 用 yt-dlp 下载视频/音频，并把视频转成文字。触发词：转文本、转文字、转录、转写、语音转文字、字幕、下载视频、下载音频、1080P、更新合集、更新商业创新、同步商业创新条目。含画质策略、音频流优先、本地 Whisper 转写流程
allowed-tools: Bash, Read, Write
---
# yt-dlp 视频下载技能
你是一个视频下载专家，擅长使用 yt-dlp 从各大平台下载视频和音频。

## 怎么用（zeno 的最短提问形式）
**丢链接 + 两三个字就够**，其余默认值不用他说：
| 他说 | 我做什么 |
|---|---|
| `转文本 <链接>` / `转文字` / `转写` | 只下音频流 → 本地转写 → **只出 TXT** |
| `下载视频 <链接>` | 1080P（无 1080P 自动退档） |
| `下载音频 <链接>` | 只要音频流（优先 m4a） |
| 只丢一个链接、不说别的 | 默认按**下载视频 1080P** |
| `更新合集` / `更新商业创新`（不带链接） | **同步清单 + 转写新增**：见下方「更新合集」一句话流程 |
- **不要反问画质、不要反问格式**。默认值已定：TXT、不留视频文件、1080P。
- 只有「要 SRT 吗」「要不要顺手留视频文件」这类**会改变产物**的事才追问，且默认值就是上面这些。
## 工具路径
yt-dlp 位于：`/Users/zeno/Documents/04-software-packages/yt-dlp-videodownload/yt-dlp_macos`
## 默认配置
- 默认下载目录（随手下载）：`~/Downloads/`（合集流水线的媒体与转写稿在项目目录，2026-09-12 迁入）
- **画质策略（2026-09-11 zeno 明确要求 + 离线实测）**：默认**不追求最高画质**，目标档位 **1080P**；命中不了 1080P 时自动退档（更小的优先，连更小的都没有才取更大的）。统一用这一条：
  ```
  -S "res:1080"
  ```
  - 实测结论（yt-dlp 2026.08.19，构造 info.json 离线验证）：精确命中 1080P → 取它；无 1080P 但有更低档 → 取**最接近的更低档**（720P）；所有档位都高于目标 → 取**最小的一档**。正好对应"1080P 下不了就取更小或更大"。
  - ⚠️ **不要**用 `-f "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b"` 这类写法：**竖屏视频**（如微博 1080x1920）的 `height` 是 1920，会被 `height<=1080` 误杀 —— 实测把本来有 1080P 的视频降级成了 540P。`-S` 的 `res` 按**短边** `min(width,height)` 计算，横屏竖屏都正确。
  - 只有用户**明确要求原画/最高画质**时才用 `-f bv*+ba/b`。
  - ⚠️ 纯转写用途走下方「转写工作流」。平台**没有 m4a 音频流**时（微博就是）被迫下视频，
    此时**取最小档 `-S "res:540"`**，该档失败则**逐档上推 `res:720 → res:1080`**（不是"只退到 720"）。
    详见「画质与音轨」—— 三档音轨完全相同，**画质不影响转写质量**。
- **仅音频下载（2026-09-11 修订，依据官方 README + 本机 `--help` 原文）**：用于**两种场景** —— ① 用户明确说"只要音频 / 不要视频"；② **要转文本**（转写同样不下载视频，见下方「转写工作流」）。统一默认用 `-f "ba[ext=m4a]/ba"` —— 优先取**原生 m4a 音频流**（如 YouTube 的 format 140），**零转码、不下载任何视频流、不需要 ffmpeg**，最省流量最快。
  - README 原文（FORMAT SELECTION）：``ba``, `bestaudio`: Select the best quality **audio-only** format. Equivalent to `best*[vcodec=none]`
  - ⚠️ **不要**再用 `-x --audio-format mp3`（旧写法，已废弃）：`-x` 不带 `-f` 时走默认格式选择 `bv*+ba/b`，会**先下最高画质视频再抽音频**，实测多下 20MB+；Opus→MP3 还是有损重编码。
  - 只有**必须统一成 m4a/AAC**（源可能不是）时才转码：`-f "ba[acodec^=aac]/ba[acodec^=mp4a.40.]/ba" -x --audio-format m4a`（即官方 `-t aac` 预设的写法：先挑原生 AAC 源，没有再转码）。
  - `-f ba` 单独用**不需要 ffmpeg**；只有 `-x` 才要求 ffmpeg 和 ffprobe（`--help` 原文：`Convert video files to audio-only files (requires ffmpeg and ffprobe)`）。
- 文件名模板：`-o "%(title)s.%(ext)s"`
## 工作流程
1. 收到用户提供的视频链接后，先确认用户想要下载什么（视频/音频/字幕/**转文本**）。
   - **要转文本 → 不下载视频**，直接走下面的「仅音频下载」`-f "ba[ext=m4a]/ba"`，再走「转写工作流」。
   - **不要反问画质**：要视频时按上面的 1080P 策略直接下，用户另有要求时再改。
   - **不要反问字幕格式**：默认只出 TXT，用户提了才出 SRT。
2. 根据需求拼接命令，例如：
- 下载视频（默认 1080P 优先，无 1080P 自动退档）：`/Users/zeno/Documents/04-software-packages/yt-dlp-videodownload/yt-dlp_macos -S "res:1080" -o "~/Downloads/%(title)s.%(ext)s" <URL>`
- 下载最高画质（仅在用户明确要求时）：`-f bv*+ba/b`
- 下载音频（默认，仅音频流）：`/Users/zeno/Documents/04-software-packages/yt-dlp-videodownload/yt-dlp_macos -f "ba[ext=m4a]/ba" -o "~/Downloads/%(title)s.%(ext)s" <URL>`
- 下载音频并强制 m4a/AAC 容器（源非 AAC 时才转码）：`/Users/zeno/Documents/04-software-packages/yt-dlp-videodownload/yt-dlp_macos -f "ba[acodec^=aac]/ba[acodec^=mp4a.40.]/ba" -x --audio-format m4a -o "~/Downloads/%(title)s.%(ext)s" <URL>`
- 查看可用格式：`/Users/zeno/Documents/04-software-packages/yt-dlp-videodownload/yt-dlp_macos -F <URL>`
3. 执行命令并告知用户下载完成，输出文件位置
## 注意事项
- 如果提示权限错误，先执行：`xattr -d com.apple.quarantine /Users/zeno/Documents/04-software-packages/yt-dlp-videodownload/yt-dlp_macos`
- 如果提示找不到 ffmpeg，提醒用户安装：`brew install ffmpeg`

## 本机 macOS 网络 / 403 排查（2026-09-12 实测）
需代理的平台（YouTube 等）在本机跑 yt-dlp 的三个必坑：

**① 代理必须显式指定 Clash，并清空会话代理 env**
- 本会话自带 `HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:60131`（WorkBuddy 内部代理），对 YouTube 返回 502。**yt-dlp 会优先吃这个 env 而忽略你的意图**，所以任何 yt-dlp 命令都要：
  `env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy yt-dlp_macos --proxy http://127.0.0.1:7892 ...`
- Clash Verge 在 `7892` 监听（HTTP 代理 / mixed）。实测直连被 GFW 挡、会话 `60131` 超时或 502，只有走 `7892` 才通。`-F`/元数据能列只是因为只读网页，真正下媒体才暴露 403。

**② yt-dlp 的 JS 签名必须走「系统 Node」，托管 Node 被权限沙箱锁死**
- yt-dlp 2026.08.19 默认客户端（visionos）下媒体需解 `n` 签名，必须配 JS 运行时。本机**托管 Node**（`/Users/zeno/.workbuddy/binaries/node/.../bin/node`）被锁，跑 EJS 会报 `ERR_ACCESS_DENIED: Access to this API has been restricted`（读 shim 被拒）→ 签名解不出 → 格式缺失 / 403。
- 修法：指向**系统 Node**（fnm 的 v24.19.0，无此限制）：
  `--js-runtimes "node:/Users/zeno/.local/share/fnm/node-versions/v24.19.0/installation/bin/node"`
- 验证：`node -e "require('fs').readFileSync('/etc/hosts')"` 不报错即可用。

**③ YouTube 媒体 403 = Clash 规则模式 IP 不一致（最常见卡点）**
- 现象：`[info] Downloading 1 format(s): 140` 后 `ERROR: unable to download video data: HTTP Error 403: Forbidden`。签名 URL 带 `ip=<IPv6>`，与拉媒体时的出口 IP 对不上。
- 根因：Clash **规则模式**下 `youtube.com`（取签名）和 `googlevideo.com`（拉媒体）被分到**不同出口 IP**（实测真实出口 `111.55.205.88`、Clash 出口 `140.235.143.68`、URL 内 `ip=` 是另一 IPv6）。
- 官方 FAQ 口径：YouTube 要求「取签名」与「拉媒体」来自**同一 IP**（含 cookie/UA）。
- 已试无效：`tv` 客户端（反被反爬拦"需刷新"）、`--cookies-from-browser chrome`（Chrome cookie 走 Keychain 加密，yt-dlp 取 0 条）、系统 Node 签名本身。
- **修法（用户侧）**：Clash Verge 切 **Global 模式**（或让两域名走同一节点），出口 IP 一致后 403 消失。改完用 ①+② 的命令直接重跑即可。
- SOCKS5（`socks5://127.0.0.1:7892`）在本机握手失败（exit 35），别走这条路。

## Weibo 平台专项（2026-09-09 实测）
- 用户主页的"视频"标签页地址（`weibo.com/u/<uid>?tabtype=newVideo&layerid=<vid>`）会被当作**整个用户视频列表**解析，`layerid` 定位不到单条。要下载其中某条视频，先把地址改写为单条微博格式：`https://weibo.com/<uid>/<vid>`（实测 layerid 即该条微博的 mid，可直接命中）。
- `weibo.com/tv/show/1034:<vid>` 格式会报错 `'NoneType' object is not subscriptable`，不要用。
- **字幕**：yt-dlp 的 Weibo 提取器未实现字幕下载，`--list-subs` 恒返回 has no subtitles，`-J` 元数据中也没有字幕字段。微博详情接口（ajax/statuses/show、m.weibo.cn）对未登录访客返回 403/302，无法绕过核实。需要字幕时向用户说明该限制，可提示用已登录浏览器的 cookie（`--cookies-from-browser`）或第三方转写服务。
- **无独立音频流（2026-09-11 用 5 条视频 `-F` 实测确认）**：微博只有 `mp4_hd / mp4_720p / mp4_1080p` 三档合流格式，`vcodec` 全为 avc1、`acodec` 全为 `mp4a.40.5 (44k)`。**`-f "ba..."` 必然失败**，转写只能下视频流再喂脚本。
  - 三档的**音频轨完全相同**（实测 `ffprobe`：AAC / HE-AAC / 44100 Hz / 双声道 / **128 kbps**，三档逐字段一致），所以转写取**最小档 `mp4_hd`（540P，竖屏 540x960）**即可，用 `-S "res:540"`。实测比 `res:720` 省约 40% 流量（5 条 104MB vs 174MB）。
  - ⚠️ **但单档会踩「某档 CDN 地址坏了」的坑**：实测 `#38` 的 540P 恒 404，720P/1080P 却正常。
    所以批量下载要按 `res:540 → res:720 → res:1080` **逐档退让重试**（`batch_pipeline.py` 已实现），
    **不要用一档的失败判定视频已失效**。
  - 档位梯度不一定都有 540：若无 540，`-S "res:540"` 会自动退到最近的更低档；**不要**写 `height<=540` 之类（竖屏 height=960 会被误杀）。
- **取标题/时长（2026-09-11）**：微博标题就是可用信息，且同 UP 的多条视频 `upload_date` 常是**同一天**、区分不出发布顺序——系列视频排序要靠标题与文案里的「第一期/上期」线索，不要依赖日期。命令：
  ```
  yt-dlp_macos --skip-download --print "%(upload_date)s | %(duration)ss | %(title)s" <单条URL>
  ```
- **文件命名（2026-09-11）**：`-o "%(id)s.%(ext)s"` 便于对回原始链接；转写完成后再把 TXT 重命名为 `<标题>_<视频ID>.txt`，既自解释又能溯源到 URL。
- **整个作品列表可枚举（2026-09-11 实测）**：`https://weibo.com/u/<uid>?tabtype=newVideo` 会被解析成**播放列表**。实测该 UP 共 **748 条**（2018-01 → 2026-09）。要"把这个人的视频都转成文字"时，**先用一次 flat 调用拿全库清单**再筛，不要一条条来：
  ```
  yt-dlp_macos --flat-playlist --skip-download \
    --print "%(id)s|%(duration)s|%(upload_date)s|%(title)s" "https://weibo.com/u/<uid>?tabtype=newVideo"
  ```
  flat 模式**已带 `duration`**，所以"只取 >60 秒的"这类筛选可以在**下载前**完成。
  ⚠️ **这条筛选只适用于"全量主页列表"（剔搬运/短评），`绝对不要`用在 `商业创新` 合集流程上 —— 见下方「不要按时长过滤」。**
  ⚠️ 列表顺序≈新到旧，但**不严格按日期**（实测第 1 条 20260904、第 2 条 20260910），要排序必须显式按 `upload_date`。
- **判"自制 vs 转发搬运"用 `tags`（2026-09-11 实测）**：这是唯一可用的离线信号。
  - 自制内容带作者固定话题块，实测 `tags` = `['商业创新','管理创新','技术创新','数字化转型','产业互联网','大模型','智能体','AI原生组织']`（长视频 8 个 / 短视频 2–4 个）。
  - 2018–2019 年那批搬运内容（如「白岩松讲养老！」「教你做上海年夜十道菜」）实测 `tags=NA`。
  - ⚠️ **`uploader`/`uploader_id` 完全不能判转发** —— 转发他人视频时它照样显示发布者本人（实测「白岩松讲养老！」→ `uploader=老马自奋蹄(1807436544)`）。别用这个字段。
  - ⚠️ `repost_count` 也不是转发标志，它是"被别人转发的次数"（实测老爆款 =167）。
  - `tags` 需**非 flat** 元数据（≈8s/条）。取法：下载时加 `--write-info-json`，事后读 `<id>.info.json` 的 `tags`，**不必单独跑一遍**（批量时能省几十分钟）。

### 画质与音轨（2026-09-11 实测，zeno 确认策略）

**核心结论：微博三档音轨完全一致，只有视频码率不同 ⇒ 转写取最小档即可，画质不影响文字质量。**

用**真实文件** `ffprobe` 实测（同一条 5:12 视频；比元数据更硬 —— 微博接口的 `abr` 字段全为 null）：

| 档位 | 音轨 | 视频码率 | 体积 |
|---|---|---|---|
| `mp4_hd`（540P，竖屏 540x960） | AAC **HE-AAC** / 44100 Hz / 2ch / **128 kbps** | ~410 kbps | 16.2 MB |
| `mp4_720p` | 同上，**逐字段一致** | ~584 kbps | 26.8 MB |
| `mp4_1080p` | 同上，**逐字段一致** | ~1.29 Mbps | 50.6 MB |

- **没有任何纯音频格式**：唯一 `vcodec=none` 的 `scrubber_hd` 是 180x320 预览图，`acodec` 也是 none。
- **没有比 540P 更小的档** ⇒ **540P 就是下限**，"只下音频"在微博上做不到。

**适用范围（必须切分，否则会和上文的 1080P 默认打架）**

| 场景 | 策略 |
|---|---|
| **合集批量下载 / 转写**（`batch_pipeline.py`） | **540P 优先**；该档失败则**逐档上推** `720 → 1080`（zeno 2026-09-11 确认） |
| 随手给一个链接说「下载视频」 | 仍是 **`-S "res:1080"`** 的通用默认（zeno 2026-09-11 定，**不改**） |

体积推算（全量 469 条 / 57.9 小时）：540P ≈ 10.8 GB ｜ 720P ≈ 19 GB ｜ 1080P ≈ 35 GB ｜ 仅音轨 ≈ 3.3 GB

**zeno 的取向是"别为转写下大文件"，不是"要最高画质"** ⇒ 转写路径永远从最小档起步；
他明确说了"保留 mp4"时才需要在意画质档位。

**画质台账**：跑批时校验步骤会顺手把实际分辨率记进 `<TSV同目录>/下载画质台账.tsv`
（序号 / 视频ID / 分辨率 / 档位 / 大小MB），**按 id 覆盖写** ⇒ 重跑逐字节一致。
给旧素材补记录用：

```
$PY $S --quality-scan      # 扫本地已有的 mp4，刷新台账后退出（幂等）
```

这样能一眼看出**哪些条目因为 540P 档坏了被降级到 720P/1080P**（例如 #38 就是 720P）。

### 枚举列表与判定合集归属（2026-09-11 实测，新增）
- **抓列表直接打这个接口，不要用 yt-dlp 的高层封装**：`https://weibo.com/ajax/profile/getWaterFallContent?uid=<uid>&cursor=<cursor>`（yt-dlp `WeiboUserIE._fetch_page`，源码 `yt_dlp/extractor/weibo.py:341`）。带 cookie 用 curl / urllib 直接翻页，每页 20 条，`data.next_cursor` 是下一页游标（空 = 到底）。实测该账号 752 条 ≈ 95 秒，比 flat-playlist 快，且能拿到 yt-dlp 丢弃的字段。
- **前置：访客握手拿 cookie**，照抄 weibo.py `WeiboBaseIE._update_visitor_cookies`：`passport.weibo.com/visitor/genvisitor` → `passport.weibo.com/visitor/visitor?a=incarnate&...`。**已封装好**：`scripts/weibo_collection_sync.py`（含重试重握手），别再依赖 `/tmp` 里的临时脚本（`/tmp` 会被系统清掉）。
- **判定"这条属于哪个合集/话题"用这两个字段**（比 `tags` 更准，且不需要非 flat 元数据）：
  - `page_info.media_info.belong_collection`（`1` = 属于某合集）
  - `page_info.actionlog.ext` 里的 `t_id:<id>|t_name:<名称>`，实测该账号为 `t_id:5084258226602032|t_name:商业创新`
- **核验"有没有抓全"的硬办法**：站方合集按 **20 条/页** 分页，所以用户从地址栏复制的 `first_cursor` 必然落在 **20k+1** 的位置。拿它反查在自己列表里的序号 → 能同时验证"我爬的和用户看的是同一个列表"，并用两端锚点推出缺口区间（实测该账号：抓到 469，站方标 471，缺口锚定在中间第 42–458 条之间，公开接口无法定位）。
- ⚠️ **作者写在视频标题里的"第N期" ≠ 站方"第N集"**：合集里夹着非编号内容，偏移会累积（实测第 11 期偏移 0、第 71 期偏移 −6）。别拿期号反推集数，也别拿它当排序依据。

### ⛔ 不要按时长过滤（2026-09-11 zeno 明确要求，硬规则）

**`商业创新` 合集里 <1 分钟的视频必须保留、必须转写。任何时候都不要按时长筛这个合集。**

实测依据（可复核）：

| 序号 | 时长 | 标题 | 视频ID | 归属判据 |
|---|---|---|---|---|
| #329 | 0:40 | 现在我看什么都会质疑是不是AI做的。只有这个视频没有疑问。。。 | 5270913631392361 | `belong_collection=1` + `t_id=5084258226602032` |
| #352 | 0:10 | 别在中年，就把生命的煤气罐烧得一干二净 | 5280108215797806 | 同上 |

- 469 条清单里 <60 秒的**正好这 2 条**，且都是**用最强判据（`t_id`）进来的**，和其余 467 条同源。
- 全量 752 条里 <60 秒的共 **120 条**，其中 **118 条不属于任何话题**（= 2018–2019 搬运 + 2026 AI 短评），
  只有这 2 条属于商业创新。⇒ 上面那句「只取 >60 秒的」针对的是那 118 条，**跟合集毫无关系**。
- 代码侧已核对：`weibo_collection_sync.py`（只按 `t_id` 筛）、`batch_pipeline.py`（只按序号/`--next`/`--ids` 选）、
  `transcribe_batch.py`（只跳过"已有 TXT"）——**都没有任何时长门槛，以后也不要加**。

**判断某条该不该进合集，找依据只用「合集归属（`t_id`）」，绝不用「时长」。**
若哪次跑出来条数变少，第一件事就是查有没有被误加时长过滤。

## 合集条目增量同步（2026-09-11 实测）

一次性抓全 → 之后按需增量，**原地重写同一份产物**（单一真源）。
脚本：`scripts/weibo_collection_sync.py`（只用标准库，无第三方依赖）；说明见 `scripts/README.md`。
**zeno 说「同步商业创新条目」= 跑下面第一条（只同步、不转写）；要「同步 + 转写新增」用「更新合集」，见下方同名字节。**

```
PY=/Users/zeno/.workbuddy/binaries/python/versions/3.13.12/bin/python3
S=~/.workbuddy/skills/yt-dlp-downloader/scripts/weibo_collection_sync.py
$PY $S                       # 增量（首次自动全量），实测无新增时 4 秒 / 1 页
$PY $S --dry-run             # 只抓不写，先看摘要
$PY $S --full                # 强制全量（约 95 秒 / 39 页）
$PY $S --site-total 472      # 站方标注集数变了，重算锚定列
$PY $S --full --allow-shrink # 作者删了视频，接受条数变少
```

- 状态：`<项目>/data/.sync_state.json` + `.known_ids.txt`（一行一个 mid）
- 产物：`商业创新合集_条目.tsv` / `.md` / `_urls.txt`，**固定文件名**（条数写文件头，
  别把数字塞进文件名，否则条数一变就留孤儿文件）
- 退出码：`0` 成功 / `2` 网络失败（产物零改动）/ `3` 数据异常（拒绝写入）/ `4` 参数错误

### 三个必须记住的坑（都踩过）

1. **合集判据要「整体降级」，不能逐条兜底。** 三级信号（`actionlog.ext` 的 `t_id`
   → `topic_struct` → `text_raw`）严格度差很多：同一份数据实测**强判据 469 条 /
   话题判据 488 条 / 正文判据 526 条**（打了同一话题 ≠ 属于这个合集）。必须先用强判据
   跑，**只有它整体 0 命中才降级**。写成逐条 `or` 兜底会把 469 变成 526，
   症状是「尾部锚定偏移变成 −55」。
2. **增量提前停止不能用「翻到已知最旧那条」。** 列表是 id **降序**，最旧那条在列表
   最底部，用它当判据等于没省时间（实测仍是 1 分 53 秒）。正确判据是
   **「本页命中的合集条目里有 ≥2 条已经知道」→ 已翻进旧区间，停**（实测 1 页 4 秒）。
   兜底：连续 3 页无命中；上限 15 页，触上限说明积压过多 → 自动转全量。
3. **不要用发布时间排序，也不要拿它判新旧。** 同一批视频常在同一天发出；排序键只用
   微博 id。

### 集数锚定（唯一容易搞错的地方）

- 站方按 **20 条/页**分页 ⇒ `tail_len = ((site_total - 1) % 20) + 1`（471→11）
- 头部 `head_len` 默认 41：依据是用户从地址栏复制的 `first_cursor` 必落在 20k+1 的页首，
  实测 #1/#21/#41 与站方 1/21/41 对齐（gap=0）
- 尾部：`序号 >= N - tail_len + 1` → `集数 = 序号 + gap`，其中 `gap = site_total - N`
- 中段一律 `—`，**不猜**。**严禁拿作者标题里的「第N期」反推集数**：合集里夹着非编号
  内容，偏移会累积（实测第 11 期偏移 0、第 71 期偏移 −6）
- `site_total`（站方标注总集数）**公开接口拿不到**（tv/api 的 Playinfo 无合集字段），
  只能人工从站上看，存状态文件。跑了新数据后脚本会提示「若站方标注已变为 X，
  请 `--site-total X` 重跑」
- `gap` 就是缺口信号：`=2` 正常 / `=0` 缺口闭合（全表可连续编号）/ `<0` 实抓比标注多
  （重复发布或计数滞后）/ 变大 = 缺口扩大，去站上核对

### 幂等与容错（已实测）

- 写文件一律 `tmp + os.replace`，不留半截文件；**渲染结果与现有文件逐字节相同则跳过写盘**
- 断网/握手失败重试 3 次（每次重试都重新握手），仍失败 → 退出码 2，**产物与状态零改动**
- 抓到 0 条 → 退出码 3 拒绝写入（防接口改版把清单清空）
- 全量模式下条数变少 → 默认拒绝写入，确认是作者删除才加 `--allow-shrink`
- 验收方式：连跑两次，`md5` 必须一致；`--full` 与增量跑出的 TSV 也必须一致

### 它解决不了的

- **公开列表只覆盖「视频」标签页**。作者若把某集发成转发/图文，或已删除，接口永远给不到
  ⇒ 缺口可能**永远补不上**（该账号实测：抓到 469，站方标 471）。只能靠 `gap` 监测，
  不要承诺「跑够次数就能对齐」。
- 增量模式**检测不到删除**（没翻到那么深）。要查删除必须 `--full`。

## 「更新合集」一句话流程（2026-09-12 定，zeno 的触发词）

**zeno 说「更新合集」（或「更新商业创新」「把新的转写一下」）= 跑两步，不反问、不请示：**

1. **增量同步**（约 4 秒）：
   `/Users/zeno/.workbuddy/binaries/python/versions/3.13.12/bin/python3 ~/.workbuddy/skills/yt-dlp-downloader/scripts/weibo_collection_sync.py`
2. 看同步报告的「**新增 N 条**」：
   - **N > 0** → 下载 + 转写这 N 条（**必须后台跑**）：
     `/Users/zeno/.workbuddy/binaries/python/versions/3.13.12/bin/python3 ~/.workbuddy/skills/yt-dlp-downloader/scripts/batch_pipeline.py --next N`
   - **N = 0** → 不跑转写
   - ⚠️ 忽略同步脚本末尾建议的全量 `yt-dlp -a urls.txt`（那是全量重下用途，不是增量）
3. **汇报不超过 3 行**（2026-09-12 zeno 要求省 token）：
   - 有新增：「新增 N 条：<标题列表>」/「已转成文字：N 条」/ 异常一句话（没有就不写）
   - 无新增：只回一句「微博上暂无新视频」
   - **不在回复里复述流程、解释脚本**——细节都在本文档，执行时照做

- 只要清单、不要转写 → zeno 说「**只同步**」（旧说法「同步商业创新条目」同义）
- 同步/下载失败（退出码 2）→ 汇报「失败（网络），产物零改动，可稍后重试」；**不要自行反复重试**
- 增量 N 一般是小数（个位数）；若 N > 40 分几批跑（单批 ≤40）
- **终端直跑**（zeno 2026-09-12 要的，与聊天版等效）：`scripts/update_collection.py` ——
  `~/.workbuddy/skills/yt-dlp-downloader/scripts/update_collection.py`
  （固定日志 + 追加 `data/合集更新日志.txt`；`--dry-run` 只探测不写。2026-09-12 实测通过）

## 一键批量转写流水线（2026-09-11 实测，zeno 自己分批跑的主力工具）

**zeno 说「把 X 到 Y 条转成文字」/「继续往后转 N 条」= 跑这个，不要手搓 yt-dlp + 转写脚本。**
把「挑条目 → 拼单条链接 → 批量下载 → 校验时长 → 幂等转写 → 合并长文」串成一条命令，
**新对话零上下文也能跑对**。脚本：`scripts/batch_pipeline.py`（+ `thermal.py` 测温、`weibo_naming.py` 命名）。

```
PY=/Users/zeno/.workbuddy/binaries/python/versions/3.13.12/bin/python3
S=~/.workbuddy/skills/yt-dlp-downloader/scripts/batch_pipeline.py
$PY $S --next 20            # ★最省心：从第一个还没转写的往后跑 20 条（不用记序号）
$PY $S 1-60                 # 按清单序号范围跑
$PY $S --ids 5084582845615713,5084945234006672
$PY $S --status             # 只看进度（已转/待转/剩余小时数/当前热压）
$PY $S 1-60 --dry-run       # 只打印计划，不动手
$PY $S --next 20 --guard    # 开自动冷却：热压到 serious 暂停，回落继续
$PY $S --next 20 --merge    # 转完顺手合并成一份带范围文件名的长文
$PY $S 1-60 --merge-only    # 只合并，不下载不转写（补合并稿时用）
$PY $S --delete-video       # 转完删 mp4（默认保留，见下）
$PY $S --cleanup            # 单独清 mp4（幂等，可反复跑）
```

- ⚠️ **必须 `run_in_background=true`**。前台会撞 120s 超时被杀，只留半截文件。一批 60 条约 25 分钟（下载是瓶颈）。
- **单批别超过 40 条**：见下方「单轮删除上限」坑。
- ⚠️ **默认保留 mp4**（2026-09-11 zeno 要求改成不删）。全量 469 条约 **6 GB**；要省空间加
  `--delete-video`（单轮删 >50 个会被安全阀拦，见下），或事后 `--cleanup` 分批清。
- **产物**（固定位置，别改）：
  - 视频 `/Users/zeno/WorkBuddy/yt-dlp视频转文本/audio/<序号>_<标题>_<ID>.mp4`（默认保留）
  - 转写 `/Users/zeno/WorkBuddy/yt-dlp视频转文本/transcript/<序号>_<标题>_<ID>.txt`（**每份顶部带表头**，见下）
  - 报告 `transcript/_transcribe_report.json`（逐条耗时/字数/热压 + `peak_thermal` / `cooled_sec`）
  - 合并稿 `<项目根>/商业创新合集_合并_序号<lo>-<hi>_<日期>.md`
  - 台账 `<TSV同目录>/转写进度.md`（每批自动追加一行）
- **幂等**：已存在非空 TXT 自动跳过；中断后用**同一条命令**续跑即可。清单只读，**不要**在这条流程里重抓清单。
- ⛔ **不按时长过滤**：合集里有 2 条 <1 分钟的视频（#329 是 0:40、#352 是 0:10），它们是正规成员，照转。
  详见上方「不要按时长过滤（硬规则）」。
- **下载必须逐档退让**（2026-09-11 实测踩坑）：首选最小档 `res:540` 省流量，但**一档 404 ≠ 视频没了**。
  实测 `#38 第37期：为什么会产生云计算？`（id `5097628814281550`）报了两轮 `HTTP Error 404`，
  我据此误判「永久失效」；实际上**只有 540P 那一档的 CDN 地址坏了，720P / 1080P 都完好**。
  脚本已改成按 `res:540 → res:720 → res:1080` **逐档重试**，只补真正还缺的那些。
  ⇒ **三档都试过才允许说"媒体没了"**，别拿一档的失败下结论。
- **永久失效名单** `data/.unavailable.txt`（手工维护，**脚本绝不自动写**）：确认三档都下不来的视频才记进去，
  `--next` / `--status` 就不再把它算作待转，免得每次白占名额。每轮都会**打印被排除的条目**——
  这是显式排除，不是静默过滤。**当前为空**。用 `--ids` 显式点名时名单**不生效**（照跑）。
- 退出码：`0` 正常 / `1` 参数错 / `2` 下载失败或无可用文件 / `3` 转写失败。
- 参数错、清单不存在都会在动手前拦下，不会写坏数据。

### 整夜连续跑：loop_pipeline.py（2026-09-12 实测 101–469 跑通）

**zeno 说「把 101–469 连夜跑完」这类大范围连续任务 = 跑这个**。它循环调 `batch_pipeline.py --ids`：
每轮从清单挑 20 条没转的 → 转完统计 → 批间休 600s → 下一轮。**红绿灯机制**：连续两轮零新增 TXT
就把卡住的 id 写进 `data/批量转写_异常清单.txt` 并在后续轮次排除，不会死循环；全部跑完退出码 0。

```
PY=/Users/zeno/.workbuddy/binaries/python/versions/3.13.12/bin/python3
L=~/.workbuddy/skills/yt-dlp-downloader/scripts/loop_pipeline.py
nohup caffeinate -dimsu $PY $L >> "/Users/zeno/WorkBuddy/yt-dlp视频转文本/data/批量转写_后台日志.txt" 2>&1 &
# 可选：--lo/--hi 范围（默认 101 → 清单最新，自动）、--batch N（默认 20）、--rest 秒（默认 600）、--no-rest（调试）
```

- **必须用 `nohup + caffeinate -dimsu` 后台跑**（zeno 合盖也不中断；这是系统级后台，不走 run_in_background）。
- 实测（2026-09-12）：101–469 共 369 条、19 批，00:44 启动 → 08:21 结束，**369/369 全部成功、0 异常**；
  热压峰值 `fair`，`--guard` 式冷却一次都没触发。
- 它**不带** `--merge` / `--delete-video` / `--guard`（保持行为单一）；跑完要合并稿另行 `--merge-only`。
- ⛔ 红线写死在脚本里：不按时长过滤、绝不写 `.unavailable.txt`、不动 `data/` 清单文件。
- 进度看两处：`data/批量转写_后台日志.txt`（每轮明细）、`data/转写进度.md`（每批一行台账）。
- ⚠️ 已修 bug（2026-09-12）：结束行的 `log()` 用过 `#%d` 占位导致 `TypeError`，已改 f-string；
  如再见到结束前带 traceback 的退出，先查这里。

### 命名与表头（2026-09-11 zeno 要求，规则唯一真源在 `weibo_naming.py`）

**素材一律命名成 `<三位序号>_<标题>_<视频ID>.<ext>`**，序号按清单「由旧数起」补零：

```
001_当前中国企业面临哪些问题与挑战？应该如何创新突破_5084582845615713.txt
050_第46期：大中小企业云计算选型建议_5101252687433645.mp4
```

- 好处：Finder 里能按顺序排，又能一眼看出讲什么，尾部 ID 还能对回微博原始链接。
- 标题会做**文件名安全化**：`/ \ : * ? " < > |` 换成 `_`、压空白、截到 60 字。
- 旧命名 `<ID>.<ext>` 和 `<标题>_<ID>.<ext>` **仍能识别**（`find_media` 会兜底），不会重复下载。
- **每份 TXT 顶部有表头**（序号 / 标题 / 时长 / 发布 / 链接 / 转写模型）；`merge_transcripts.py`
  会自动剥掉表头。判定"有没有表头"看首行是不是 `# ─`。
- **改命名规则只改 `weibo_naming.py`**，`batch_pipeline.py` / `transcribe_batch.py` 都从它 import，
  别在两处各写一份（会漂移）。
- 存量素材归位用 `scripts/migrate_names.py`（**默认 dry-run**，`--apply` 才动手；
  会先把 TXT 备份到 `_backup_names_<时间戳>/` 并写 `old→new` 映射，可回滚）：

```
$PY ~/.workbuddy/skills/yt-dlp-downloader/scripts/migrate_names.py            # 预览
$PY ~/.workbuddy/skills/yt-dlp-downloader/scripts/migrate_names.py --apply    # 执行
```

### 视频不见了怎么办（2026-09-12 实测：先查废纸篓，别急着重下）

- **Spotlight（`mdfind`）不索引 `~/.Trash`** —— 用它搜不到 ≠ 文件没了。我就这么误判过一次，
  直接告诉 zeno「只能重下 45 条」，结果他从废纸篓全捞回来了。**判定前必须直接查废纸篓**：
  ① `ls -A ~/.Trash` ② `find ~ -name "*<视频ID>*" -not -path "*/Library/*"` ③ 都没有再说"真没了"。
- 从废纸篓恢复回来的是**旧命名**（`<ID>.mp4`），两步就能并入体系：
  1. `migrate_names.py --apply` —— 改名成 `NNN_标题_ID.mp4`（`find_media` 认旧名，不会重复下载）
  2. `batch_pipeline.py --quality-scan` —— ffprobe 补 `data/下载画质台账.tsv`（按 id 覆盖写，幂等）
- 剩下少数几条单独补：切到 `audio/` 目录跑
  `yt-dlp_macos -S "res:540" --no-part --ignore-errors -o "%(id)s.%(ext)s" -a urls.txt`，再跑上面两步。
- **坑**：`batch_pipeline.py <范围>` 遇到「这批 TXT 都在」会**提前 return，不会补下 mp4**
  （`main()` 里 `if not todo_txt`）。单纯想补视频不能靠它 —— 现阶段走上面的手工三步。

### 单轮删除上限（2026-09-11 实测踩到）

跑 `1-60` 时清理步骤删到第 50 个 mp4 被系统拦下，报 `[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED]`
（`{"count":50,"threshold":50,"scope":"turn"}`）—— **单轮最多删 50 个文件**，脚本当时直接中断，
**连台账都没写成**。已修：① 台账改为**清理之前**写，清理被拦也不丢记录；② 清理逐文件 `try/except`，
被拒只报告不崩；③ 新增 `--cleanup` 单独清理（幂等，可反复跑）。
现在**默认根本不删**，所以这个坑只在你显式加 `--delete-video` 或跑 `--cleanup` 时才会碰到。

- 对策：单批 ≤ 40 条（`--next 40`），删除量天然 < 50。
- 已残留的用 `--cleanup` 清；一轮清不完就隔一轮再跑。

### 发烫与测温（本机热压 API，不用 sudo、不用编译）

- 测法：`osascript -l JavaScript -e 'ObjC.import("Foundation"); $.NSProcessInfo.processInfo.thermalState'`
  → `0 nominal / 1 fair / 2 serious / 3 critical`，约 30ms/次。已封装成 `scripts/thermal.py`
  （`thermal.read()` / `thermal.wait_cool(threshold, max_wait, poll)` / `thermal.describe()`）。
- **实测结论：发热源是转写（mlx_whisper 走 GPU 满载，约 26× 实时），不是下载。**
  - 下载 60 条 / 1.1GB 那十几分钟：热压**恒为 `nominal(0)`**。
  - 连转 **57 条 / 516 秒 GPU 满载**：热压峰值**只到 `fair(1)`，一次没到 `serious`**，
    `--guard` 全程未触发（`cooled=0s`）。⇒ **默认阈值 `serious` 够安全，不必更严；按 30–40 条一批这台机器烫不起来。**
- 分批要按**音频总时长**切，不是条数：1 小时素材 ≈ 2.3 分钟 GPU 满载；`--status` 直接给待转小时数。
- `--guard` 只在达到阈值时**暂停等降温**，回落再继续；`--guard-at fair` 更保守。
- **`--guard` 跑批前先确认没别的东西占 GPU**（浏览器视频、其他 ML 任务），否则测得不准。

### 给未来对话的提示词模板

`scripts/批量转写_提示词模板.md`（项目根目录有副本）——**可直接复制粘贴到新对话**，
自带全部路径与要求（含"必须后台跑""只出 TXT""别动清单"）。zeno 的用法：
决定今天跑哪几条 → 复制模板 → 改数字 → 粘到新对话。

## 批量链接处理（2026-09-11 实测，一次给多条链接时用）
用户粘贴多条链接是常态，别再一条条调 yt-dlp。

**核心结论：N 条 URL 塞进【同一个 yt-dlp 进程】，微博 guest 握手只跑 1 次。**
- 实测握手次数（`-v` 输出里数 `first-visit callback`）：单进程传 3 条 URL = **1 次**；分开调 3 次 = **1+1+1 次**。
- 实测耗时（仅元数据解析，同 3 条 URL）：单进程 **24.5s** vs 分开 **72.4s**，约 **3 倍**，折合每条省 ~16s。
- 用法：命令行直接并列多条 URL，或用 `-a batch.txt`（链接多到怕撞 ARG_MAX 时用后者）。
  ```
  yt-dlp_macos -S "res:540" -o "%(id)s.%(ext)s" URL1 URL2 URL3 ...
  ```

**不要用并行多进程"加速"**：本机到 weibocdn 实测吞吐只有 ~400–600 KB/s，是**带宽瓶颈**；多进程只会各自重做握手，还互相抢带宽。正确姿势 = 单进程 + 串行下载。

**① 先归一化 URL（必做）**。用户给的常是主页格式 `weibo.com/u/<uid>?tabtype=newVideo&layerid=<vid>`，或 `m.weibo.cn/detail/<vid>`。这些**都不能直接喂**，必须先抽成单条格式 `https://weibo.com/<uid>/<vid>`。实测可正确处理这几种输入（含带多余参数的）：
```
perl -ne 'next unless /(\d{16})/; my $v=$1; my ($u)= $_ =~ m{/u/(\d+)}; print "https://weibo.com/".($u||"<UID>")."/$v\n"' urls.txt | sort -u > batch.txt
```
- `sort -u` 顺手去重（用户常重复贴同一条）。
- 无 `/u/<uid>` 的形态（如 `m.weibo.cn/detail/`、裸 `weibo.com/<uid>/<vid>`）取不到 uid，需把 `<UID>` 换成该 UP 的 uid（本次是 `1807436544`）。
- 别用 `sed` 做这步：URL 里带 `&`，sed 的替换串会被它坑。

**② 跑完检查 `.part` 是否卡着重命名失败**（见上方转写工作流第 0 步），批量的概率远高于单条，收尾要统一 `mv`。

## 转写工作流（2026-09-11 zeno 确认改为「仅音频流优先」，用户要"转录视频文本"时用这个）
> **默认 = 只下音频流，不下视频**：`-f "ba[ext=m4a]/ba"` → 本地脚本转写。
> 这是 **2026-09-11 zeno 明确要求的新默认**（此前是"先下 1080P 视频再抽音频"）。只有他**同时还要视频文件**时，才退回 `-S "res:1080"` 的视频下载。
0. **下载一律走后台**（`run_in_background=true`）。前台跑 yt-dlp 会撞默认 120s 超时被 SIGTERM 杀掉（exit 137），只留一个 `.part` 残file。实测 15MB 文件约 36s 下完，但早期版本可能更慢。
   - **默认命令（仅音频流）**：
     ```
     /Users/zeno/Documents/04-software-packages/yt-dlp-videodownload/yt-dlp_macos \
       -f "ba[ext=m4a]/ba" -o "~/Downloads/%(id)s.%(ext)s" <URL>
     ```
     零转码、不需要 ffmpeg、不下载任何视频流。文件名用 `%(id)s` 便于对回原始链接（见上方「文件命名」）。
   - 只有他**同时还要视频文件**时才下视频：`-S "res:1080"`。
   - **批量下载**：见下方「批量链接处理」。⚠️ **不要**写成 `for u in $URLS; do yt-dlp "$u"; done` —— 每条都重做一次 guest 握手，实测慢 3 倍。
   - ⚠️ **下完检查两件事**：
     - 落地文件后缀应是 **`.m4a`**。转写脚本 `cli_transcribe.py` 位置参数的 help 原文只列 `如 .mp3/.m4a/.mp4/.mkv` —— **`.webm`/Opus 不在列表内**。
     - 若平台**只有"视频+音频合流"**、没有独立音频流（**微博已实测确认如此**，见上方 Weibo 专项），`ba` 选择器会直接报 `Requested format is not available`。
     - 上面任一情况发生 → **改走视频下载**，转写不看画质，**取能拿到的档位里最小的**（微博用 `-S "res:540"` 命中 `mp4_hd`；失败则**逐档上推** `res:720` → `res:1080`，三档都试过才算真失效）再喂脚本，别硬塞 webm、也别退回 `-x`。
   - ⚠️ **`.part` 重命名可能被沙箱拒**（2026-09-11 实测 5/5 条命中）：下载进度跑到 100% 后报
     `ERROR: Unable to rename file: [Errno 1] Operation not permitted: 'X.mp4.part' -> 'X.mp4'`。
     **这不是下载失败**，`.part` 内容已完整（字节数与 `-F` 的 FILESIZE 一致）。只需在普通 Bash 里补一条 `mv X.mp4.part X.mp4` 即可，脚本照常吃 .mp4。
   - **不要**用 `-x`：不带 `-f` 时它走默认格式选择 `bv*+ba/b`，会先挑最高画质视频再抽音频，实测多下 20MB+ 纯属浪费。
   - 转写脚本**直接吃 .mp4/.m4a**，不必先 `--audio-format mp3`。
1. **输出物默认只出 TXT**（2026-09-11 zeno 明确要求）：默认加 `--no-srt`，只在同目录留一份 `.txt`。
   - 仅当用户**明确说要 SRT / 字幕 / 时间轴**时，才去掉 `--no-srt` 同时产出 `.srt`。
   - 不要"顺手都生成"，多余文件是噪音。
2. **不要**在托管 venv 里装 faster-whisper / mlx-whisper 自己转写 —— hf-mirror 镜像实测极慢（10 分钟仅 2.6MB），托管 venv 的 pip install 也容易被超时信号打断。
2b. **批量转写要写脚本循环，不要调 N 次 CLI**（2026-09-11 实测）：
   - 仓库的 `cli_transcribe.py` **一次只吃一个文件**（`parser.add_argument("media")` 无 nargs），且**每次调用都重新加载模型**。
   - 但 `mlx_whisper` 内部有模型缓存：`mlx_whisper/transcribe.py:50-59` 的 `ModelHolder.get_model()` 按 `path_or_hf_repo` 做**类级缓存**。所以在**同一个进程**里循环调用 `mlx_whisper.transcribe(..., path_or_hf_repo=<同一路径>)`，模型**只加载一次**。
   - 现成脚本：`~/.workbuddy/skills/yt-dlp-downloader/scripts/transcribe_batch.py <ids.txt> [outdir] [--meta 清单TSV] [--guard]`，跑在 `video-subtitle/.venv/bin/python` 下（要 mlx_whisper）。模型路径复用 `cli_transcribe.resolve_local_model()`（`local_files_only=True`，**离线**）。已实现**幂等**：已存在非空 TXT 就跳过，放量重跑不重复算。给了 `--meta` 会按 `<序号>_<标题>_<ID>` 命名并写表头（详见「命名与表头」）。
   - 单进程实测：20 个视频 / 9118 秒音频 → **355 秒**（约 26× 实时）。
2c. **中文术语靠"后处理纠错表"，不要指望 `initial_prompt`**（2026-09-11 A/B 实测结论）：
   - `initial_prompt` 只放领域词，**不稳定**——同一句「结果级服务」被听成 `结果技/结果计/结果寄/结果际`，加了提示词只是**换了一种错法**，并不能稳定修对。别把它当质量保证。
   - `condition_on_previous_text=False`：实测**无内容损失**（字数差 <3%），对背景噪音段的乱码**无明显改善**（那是真实语音识别难度，不是上下文中毒）。留着当防重复循环的保险即可，别期待它治乱码。
   - **真正有效的是确定性替换表**：同一作者术语固定、同音错听高度可预测。实测一次 20 条语料里，光 `SARS→SaaS` 就 10 处，另有 `支付库→知识库`、`假以方→甲乙方`、`反普→反哺`、`招式与股价→招式与骨架`、`企业技艺→企业记忆`、`AI实在顾问→AI时代顾问` 等，共纠正 28 处。
   - 替换表的两条纪律：① 只放**高置信**条目（错写法在该语境下不是合法中文，或正确写法在同作者其他语料里被写出过）；② **拿不准的绝不擅改**，单独列出交人工判断——宁可留个显眼的错字，也不要改成可能错误的写法。现成实现在 `~/.workbuddy/skills/yt-dlp-downloader/scripts/merge_transcripts.py` 的 `CORRECTIONS` / `UNCERTAIN`。
   - ⚠️ whisper 的**分段是按停顿切的**，一行几个字，直接读很碎。合并时按句子边界（。！？）拼回 200–480 字的段落，可读性提升很大（只调换行、不增删字）。
3. 本机已有现成转写工具：`/Users/zeno/Documents/CodeProject/video-subtitle/`（zeno 上个会话确认过）。自带 `.venv`，模型 `mlx-community/whisper-large-v3-turbo` 已缓存在 `~/.cache/huggingface/hub`，**只读本地缓存、不联网下载**。
4. 命令（默认只出 TXT）：
   ```
   cd /Users/zeno/Documents/CodeProject/video-subtitle && \
   .venv/bin/python cli_transcribe.py <音频路径> --language zh --no-srt --outdir <输出目录>
   ```
   217 秒音频约 9 秒转完，脚本原生支持 `--no-txt` / `--no-srt` 两个开关。
5. 若报模型缓存缺失，按脚本 exit 2 的提示让用户自行决定是否重新下载，不要擅自联网拉模型。
