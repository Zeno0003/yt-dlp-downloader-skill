#!/Users/zeno/.workbuddy/binaries/python/versions/3.13.12/bin/python3
# -*- coding: utf-8 -*-
"""「更新合集」本地命令行入口（2026-09-12 定，zeno 手动跑版）

一条命令 = 增量同步清单 → 有新条目就下载 + 转写 → 输出固定格式日志。
与聊天里说「更新合集」完全等效；规则细节见：
    ~/.workbuddy/skills/yt-dlp-downloader/SKILL.md（「更新合集」一句话流程）

用法：
    update_collection.py                  # 正式跑：同步 + 转写新增
    update_collection.py --dry-run        # 预演：只检查（不写任何文件、不转写）
    update_collection.py --no-transcribe  # 只同步清单，不转写

日志：默认追加到 /Users/zeno/WorkBuddy/yt-dlp视频转文本/data/合集更新日志.txt
跑长途（要离开电脑）建议前缀 caffeinate -i，防止休眠掐断。

退出码：0 正常 / 2 同步失败 / 3 同步输出解析失败 / 4 转写失败 / 130 手动中断
"""
import argparse
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PY = "/Users/zeno/.workbuddy/binaries/python/versions/3.13.12/bin/python3"
SCRIPTS = Path("/Users/zeno/.workbuddy/skills/yt-dlp-downloader/scripts")
SYNC = SCRIPTS / "weibo_collection_sync.py"
BATCH = SCRIPTS / "batch_pipeline.py"
TRANSCRIPT_DIR = Path("/Users/zeno/WorkBuddy/yt-dlp视频转文本/transcript")   # 2026-09-12 迁入项目
DEFAULT_LOG = Path("/Users/zeno/WorkBuddy/yt-dlp视频转文本/data/合集更新日志.txt")
MAX_BATCH = 40  # 单批上限（对齐 batch_pipeline 的安全值）


def tag(msg=""):
    print(f"[更新合集] {msg}", flush=True)


def fmt_elapsed(sec):
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def run_stream(cmd):
    """跑子进程：输出逐行透传（实时可见），同时收集起来供解析/落盘用。"""
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", bufsize=1,
    )
    lines = []
    for ln in proc.stdout:  # type: ignore[union-attr]
        sys.stdout.write(ln)
        sys.stdout.flush()
        lines.append(ln.rstrip("\n"))
    proc.wait()
    return proc.returncode, lines


def append_log(path, stamp, n_new, titles, note, elapsed):
    path = Path(path).expanduser()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(f"[{stamp}] 新增 {n_new} 条 | {note} | 用时 {fmt_elapsed(elapsed)}\n")
            for t in titles:
                f.write(f"    · {t}\n")
    except OSError as e:
        tag(f"（日志写入失败：{e}）")


def main():
    ap = argparse.ArgumentParser(description="「更新合集」一键入口：同步 + 转写新增")
    ap.add_argument("--dry-run", action="store_true", help="预演：不写任何文件、不转写")
    ap.add_argument("--no-transcribe", action="store_true", help="只同步清单，不转写")
    ap.add_argument("--log", default=str(DEFAULT_LOG), help="日志文件（追加）")
    args = ap.parse_args()

    t0 = time.time()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tag(f"{stamp} 开始" + ("（dry-run 预演）" if args.dry_run else ""))

    # ① 增量同步
    tag("[1/2] 检查合集清单（增量同步）…")
    code, out = run_stream([PY, str(SYNC)] + (["--dry-run"] if args.dry_run else []))
    if code != 0:
        tag(f"[1/2] ✗ 同步失败（退出码 {code}：2=网络 / 3=数据异常 / 4=参数）。"
            f"产物零改动，可稍后重试。")
        return 2
    text = "\n".join(out)
    m = re.search(r"合集条数\s*(\d+)\s*→\s*(\d+)\s*（新增\s*(\d+)\s*条）", text)
    if not m:
        tag("[1/2] ✗ 没能从同步输出里解析出「新增 N 条」，中止（不转写）。")
        return 3
    n_old, n_total, n_new = int(m.group(1)), int(m.group(2)), int(m.group(3))
    titles = re.findall(r"·\s+\d{4}-\d{2}-\d{2} \d{2}:\d{2}\s+\S+\s+(.+?)\s+https?://", text)
    tag(f"[1/2] ✓ 清单 {n_old} → {n_total}，新增 {n_new} 条")
    for t in titles:
        tag(f"      · {t}")

    if args.dry_run:
        tag("预演结束：未写任何文件、未转写。")
        return 0

    if n_new == 0:
        tag("[2/2] 无新增，跳过转写。")
        append_log(args.log, stamp, 0, [], "跳过转写", time.time() - t0)
        tag(f"结束，用时 {fmt_elapsed(time.time() - t0)}")
        return 0

    if args.no_transcribe:
        tag("[2/2] 按参数跳过转写（清单已更新）。")
        append_log(args.log, stamp, n_new, titles, "仅同步（按参数跳过转写）", time.time() - t0)
        tag(f"结束，用时 {fmt_elapsed(time.time() - t0)}")
        return 0

    # ② 下载 + 转写（单批 ≤40，超出自动分段）
    left = n_new
    failed = False
    while left > 0:
        chunk = min(MAX_BATCH, left)
        tag(f"[2/2] 下载 + 转写 {chunk} 条…（共 {n_new} 条，剩余 {left}）")
        code2, _ = run_stream([PY, str(BATCH), "--next", str(chunk)])
        if code2 != 0:
            failed = True
            tag(f"[2/2] ✗ 转写失败（退出码 {code2}：2=下载失败 / 3=转写失败）。")
            break
        left -= chunk

    elapsed = time.time() - t0
    if failed:
        append_log(args.log, stamp, n_new, titles, "转写失败", elapsed)
        tag(f"结束，用时 {fmt_elapsed(elapsed)}")
        return 4

    # 收尾：列出本次新出的 TXT
    fresh = []
    try:
        for p in sorted(TRANSCRIPT_DIR.glob("*.txt")):
            if p.stat().st_mtime >= t0 - 60:
                fresh.append(p.name)
    except OSError:
        pass

    tag(f"[2/2] ✓ 完成，用时 {fmt_elapsed(elapsed)}")
    tag("── 汇总 ──")
    tag(f"新增 {n_new} 条：{'；'.join(titles) if titles else '（见清单）'}")
    tag(f"已转成文字：{len(fresh) if fresh else n_new} 条")
    for f in fresh:
        tag(f"      · {f}")
    append_log(args.log, stamp, n_new, titles, "转写完成", elapsed)
    tag(f"日志：{Path(args.log).expanduser()}")
    tag(f"结束，总用时 {fmt_elapsed(elapsed)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        tag("已手动中断（可能有残留 .part/半截文件，重跑一遍会自动修复）。")
        sys.exit(130)
