# yt-dlp-downloader

WorkBuddy 技能：用 **yt-dlp** 从各大平台下载视频 / 音频，并把视频**转成文字**（本地 Whisper 转写）。

强调两点设计取向：

- **画质「够用即可」**：默认 1080P，下不到就自动退档；转写用途默认只取最小档，不为转写下大文件。
- **产物「够用即可」**：转文本默认**只出 TXT**，不闲出 SRT；只产出被明确要求的那一种文件。

> 这是一个 **WorkBuddy 技能**（Agent 技能），给 AI 助手读的 `SKILL.md` 在本仓库根目录，包含了完整的行为规则。本 README 是给人看的快速上手。

---

## 环境要求

| 组件 | 说明 |
|---|---|
| **yt-dlp** | 下载器本体（macOS 可执行文件）。记下它的路径，下文用 `$YTDLP` 指代。 |
| **Python 3.13+** | 跑 `scripts/` 下的编排脚本（标准库即可的部分）。 |
| **ffmpeg / ffprobe** | 仅当你用 `-x` 转码时才需要；纯音频流下载不需要。`brew install ffmpeg`。 |
| **mlx_whisper**（Apple Silicon） | 本地转写用的 Whisper 实现。需放在一个 venv 里，模型缓存到本地、离线运行。 |
| **网络代理（部分平台）** | YouTube 等需代理；微博一般不需要。见下文「坑点」。 |

把 yt-dlp 路径记成变量（换成你机器上的实际路径）：

```bash
YTDLP=/path/to/yt-dlp_macos          # 例如 ~/Documents/.../yt-dlp_macos
PY=/path/to/python3                  # 例如 python3（3.13+）
```

---

## 安装为 WorkBuddy 技能

把本仓库放到 WorkBuddy 的技能目录即可被加载：

```bash
# 方式一：直接克隆
git clone https://github.com/Zeno0003/yt-dlp-downloader-skill.git \
  ~/.workbuddy/skills/yt-dlp-downloader

# 方式二：下载 ZIP 后解压到同上目录
```

重启 / 唤醒 WorkBuddy 后，对助手说「转文本 `<链接>`」「下载视频 `<链接>`」「更新合集」等即可触发（触发词见 `SKILL.md` 头部）。

---

## 快速上手（命令行直跑）

不依赖 WorkBuddy、在终端直接调 yt-dlp 也行：

```bash
# 下载视频：默认 1080P，命中不了就自动退到最接近的更低档（不要用 height<=1080，会误杀竖屏）
$YTDLP -S "res:1080" -o "%(title)s.%(ext)s" <URL>

# 仅下载音频（默认写法）：优先原生 m4a 音频流，零转码、不需要 ffmpeg
$YTDLP -f "ba[ext=m4a]/ba" -o "~/Downloads/%(id)s.%(ext)s" <URL>

# 下载最高画质（仅用户明确要求时才用）
$YTDLP -f bv*+ba/b -o "%(title)s.%(ext)s" <URL>

# 查看某链接有哪些可用格式
$YTDLP -F <URL>
```

**转文本（视频 → 文字）**：先下音频流，再喂本地 Whisper 转写，默认只出一份 `.txt`：

```bash
# 1) 只下音频流（零转码）
$YTDLP -f "ba[ext=m4a]/ba" -o "~/Downloads/%(id)s.%(ext)s" <URL>

# 2) 本地转写（示例用 mlx_whisper；模型走本地缓存、离线）
cd /path/to/video-subtitle && \
  .venv/bin/python cli_transcribe.py ~/Downloads/<id>.m4a --language zh --no-srt --outdir ./transcript
```

---

## 微博合集流水线（`scripts/`）

针对「把一个微博作者的某个视频合集整批下载 + 转写成文字」的场景，仓库提供了编排脚本。核心思路：先抓全量清单 → 之后增量同步 → 批量下载 + 本地转写 → 合并长文。

```bash
# ① 同步合集清单（首次自动全量，之后增量；只改一份产物）
$PY scripts/weibo_collection_sync.py
$PY scripts/weibo_collection_sync.py --dry-run     # 只看摘要不写
$PY scripts/weibo_collection_sync.py --full        # 强制全量翻页

# ② 一键「更新合集」= 增量同步 + 有新增就下载转写（终端直跑版，等价于对助手说「更新合集」）
$PY scripts/update_collection.py
$PY scripts/update_collection.py --dry-run

# ③ 批量转写（从第一个还没转的往后跑 40 条，最省心）
$PY scripts/batch_pipeline.py --next 40
$PY scripts/batch_pipeline.py 1-60                 # 按清单序号范围
$PY scripts/batch_pipeline.py --status            # 只看进度
$PY scripts/batch_pipeline.py --next 40 --merge   # 转完顺手合并成长文

# ④ 大范围整夜连续跑（循环驱动 batch_pipeline，防合盖休眠）
nohup caffeinate -dimsu $PY scripts/loop_pipeline.py \
  >> data/批量转写_后台日志.txt 2>&1 &
```

⚠️ 批量 / 整夜任务**必须后台跑**（前台会被超时杀掉，只留半截文件）。

### 脚本清单

| 脚本 | 干什么 |
|---|---|
| `weibo_collection_sync.py` | 抓某个微博视频合集的条目清单（首次全量 / 之后增量），原地重写同一份产物。仅标准库。 |
| `update_collection.py` | 「更新合集」一键入口：增量同步 → 有新增就下载 + 转写 → 固定格式日志。 |
| `batch_pipeline.py` | 一键批量转写：挑条目 → 下载 → 校验时长 → 幂等转写 → 保留/清理视频 → 可选合并。 |
| `loop_pipeline.py` | 大范围整夜连续跑，循环驱动 `batch_pipeline.py`，带防死循环机制。 |
| `transcribe_batch.py` | 单进程批量转写（模型只加载一次），记录热压，支持 `--guard` 自动冷却。 |
| `merge_transcripts.py` | 把单条 TXT 合并成长文：剥表头、按句拼段、套术语纠错表。 |
| `weibo_naming.py` | 素材命名 / 查找 / 表头的**唯一真源**，其它脚本都从它 import。 |
| `migrate_names.py` | 存量素材归位（改名 + 补表头），默认 dry-run，`--apply` 才动手。 |
| `thermal.py` | 读 macOS 官方热压状态（免 sudo、免编译），用于转写时自动冷却。 |
| `批量转写_提示词模板.md` | 可直接复制到新对话的提示词，自带全部路径与要求。 |

> 各脚本的**完整参数表、产物位置、退出码、坑点**见 `scripts/README.md`（更偏开发者视角）。

---

## 坑点（踩过的都记下来了）

- **YouTube 403 / 需要代理**：本机会话自带的代理对 YouTube 返回 502，必须显式走 Clash（如 `7892`）并清空会话代理 env：
  `env -u HTTP_PROXY -u HTTPS_PROXY yt-dlp_macos --proxy http://127.0.0.1:7892 <URL>`。
  yt-dlp 2026.08.19 需解 JS 签名，要指向**系统 Node**（托管 Node 被权限沙箱锁死），并让 Clash 走 **Global 模式**使取签名与拉媒体出口 IP 一致。
- **竖屏别用 `height<=1080`**：微博 1080x1920 的 `height=1920` 会被误杀成 540P。统一用 `-S "res:1080"`（`res` 按短边算）。
- **微博没有独立音频流**：只有合流视频，三档音轨完全一致 → 转写取最小档 `res:540`；某档 404 **不等于视频失效**，脚本会按 `540 → 720 → 1080` 逐档重试，三档都试过才算真失效。
- **`.part` 重命名可能被沙箱拒**：下载 100% 后报 `Operation not permitted`，`.part` 内容已完整，补一句 `mv X.part X.mp4` 即可。
- **不要按时长过滤合集**：合集里有 <1 分钟的视频（#329=0:40、#352=0:10），它们是正规成员，照常转写。

---

## 许可证

未指定许可证。如需使用 / 修改，请联系仓库所有者。
