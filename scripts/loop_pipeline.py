#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""循环驱动 batch_pipeline.py：把序号段（默认 101 → 清单最新）按 20 条/批连续跑完。

策略
----
- 从清单里按序号升序挑前 20 条还没非空 TXT 的
- 用 `--ids` 显式喂给 batch_pipeline.py（不被排序意外干扰）
- 单轮命令等价：`batch_pipeline.py --ids <id1,id2,...>` —— **不带** `--merge` / `--delete-video` / `--guard`
- 轮末统计本轮新增 TXT 数；**连续两轮新增为 0** 时，把卡住 id 写进
  `data/批量转写_异常清单.txt` 并在后续轮次排除，避免死循环
- 每轮把「轮次 / 序号区间 / 成功 / 异常 / 耗时 / 剩余」追加到 `data/批量转写_后台日志.txt`
- 批间 `sleep 600`（最后一轮结束后不再睡）
- 全部处理完（或只剩异常清单里的）→ 退出码 0

红线（写死）
----
- **不按时长过滤**（#329、#352 等短视频是合集正规成员）
- **绝不写** `data/.unavailable.txt`（那是人工名单，脚本绝不自动改）
- **不动** `data/` 下清单文件（`商业创新合集_条目.tsv` / `.sync_state.json` / `.known_ids.txt`）
  `data/转写进度.md` 被 batch_pipeline.py 追加属正常脚本行为
- 画质策略由 batch_pipeline.py 负责（540P 起、失败才退让 720→1080），本脚本不再操心

用法
----
    nohup caffeinate -dimsu \\
      /Users/zeno/.workbuddy/binaries/python/versions/3.13.12/bin/python3 \\
      ~/.workbuddy/skills/yt-dlp-downloader/scripts/loop_pipeline.py \\
      >> /Users/zeno/WorkBuddy/yt-dlp视频转文本/data/批量转写_后台日志.txt 2>&1 &
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from weibo_naming import find_media, load_rows  # noqa: E402

PY = "/Users/zeno/.workbuddy/binaries/python/versions/3.13.12/bin/python3"
BATCH = HERE / "batch_pipeline.py"
DEFAULT_TSV = Path("/Users/zeno/WorkBuddy/yt-dlp视频转文本/data/商业创新合集_条目.tsv")
DATA_DIR = Path("/Users/zeno/WorkBuddy/yt-dlp视频转文本/data")
TRANS_DIR = Path("/Users/zeno/WorkBuddy/yt-dlp视频转文本/transcript")   # 2026-09-12 迁入项目
ANOMALY_FILE = DATA_DIR / "批量转写_异常清单.txt"
LOG_FILE = DATA_DIR / "批量转写_后台日志.txt"

DEFAULT_LO = 101
DEFAULT_HI = None       # None = 清单里的最大序号（2026-09-12 起动态计算，不再写死）
DEFAULT_BATCH = 20
DEFAULT_REST_SEC = 600          # 10 分钟


def log(msg: str = "") -> None:
    """打到 stdout 即可。`>> file 2>&1` 会同时落盘到 LOG_FILE（避免双写）。"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts}  {msg}", flush=True)


def pick_rows(rows, lo, hi, exclude_ids):
    """按序号升序，挑 [lo,hi] 内还没非空 TXT、且不在 exclude 集合的条目。"""
    out = []
    for r in rows:
        if not (lo <= r["seq"] <= hi):
            continue
        if r["id"] in exclude_ids:
            continue
        t = find_media(TRANS_DIR, r["id"], "txt")
        if t and t.stat().st_size > 0:
            continue
        out.append(r)
    out.sort(key=lambda x: x["seq"])
    return out


def load_exclude() -> set[str]:
    """读异常清单：每行一个 16 位 id，# 开头注释。"""
    out: set[str] = set()
    if not ANOMALY_FILE.exists():
        return out
    for ln in ANOMALY_FILE.read_text(encoding="utf-8").splitlines():
        for tok in ln.split():
            if tok.isdigit() and len(tok) >= 16:
                out.add(tok)
    return out


def append_anomaly(entries):
    """把卡住的 (id, title) 追加到异常清单；已存在的 id 不重复写。"""
    if not entries:
        return
    existing: set[str] = set()
    if ANOMALY_FILE.exists():
        for ln in ANOMALY_FILE.read_text(encoding="utf-8").splitlines():
            for tok in ln.split():
                if tok.isdigit() and len(tok) >= 16:
                    existing.add(tok)
    fresh = [(vid, ttl) for vid, ttl in entries if vid not in existing]
    if not fresh:
        return
    if not ANOMALY_FILE.exists():
        ANOMALY_FILE.write_text(
            "# 异常清单（loop_pipeline.py 自动写入 · 2026-09-12）\n"
            "# 连续两轮「无新增 TXT」即列入；人工判断后人工处理或加进 .unavailable.txt\n"
            "# 格式：<微博ID>  <原标题片段>\n\n",
            encoding="utf-8",
        )
    with open(ANOMALY_FILE, "a", encoding="utf-8") as f:
        for vid, ttl in fresh:
            f.write(f"{vid}  #{ttl}\n")


def run_one_batch(ids: list[str]):
    """调 batch_pipeline.py --ids；返回 (returncode, stdout_str)。"""
    cmd = [PY, str(BATCH), "--ids", ",".join(ids)]
    log(f"$ {' '.join(cmd)}")
    p = subprocess.run(cmd, capture_output=True, text=True)
    # 把 batch_pipeline 的输出原样落盘（每行带缩进方便看层级）
    for ln in p.stdout.splitlines():
        log(f"  | {ln}")
    if p.returncode != 0:
        log(f"  !! batch_pipeline 退出码 {p.returncode}")
        for ln in p.stderr.splitlines()[-20:]:
            log(f"  ! {ln}")
    return p.returncode


def count_new(prev_existing: set[str], ids: list[str]) -> tuple[int, list[str]]:
    """本轮新写入 TXT 的 id 数 + 仍未转的 id 列表。"""
    cur: set[str] = set()
    for vid in ids:
        t = find_media(TRANS_DIR, vid, "txt")
        if t and t.stat().st_size > 0:
            cur.add(vid)
    new_ids = cur - prev_existing
    stuck = [vid for vid in ids if vid not in cur]
    return len(new_ids), stuck


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lo", type=int, default=DEFAULT_LO)
    ap.add_argument("--hi", type=int, default=DEFAULT_HI,
                    help="序号上界；默认=清单里的最大序号（动态）")
    ap.add_argument("--batch", type=int, default=DEFAULT_BATCH)
    ap.add_argument("--rest", type=int, default=DEFAULT_REST_SEC,
                    help="批间休息秒数（默认 600 = 10 分钟）")
    ap.add_argument("--no-rest", action="store_true", help="跳过批间休息（调试用）")
    args = ap.parse_args()

    # 动态上界：默认=清单里的最大序号（2026-09-12 起；清单增长后无需改代码）
    rows = load_rows(DEFAULT_TSV)
    if args.hi is None:
        args.hi = max((r["seq"] for r in rows), default=0)

    # 启动时清空/初始化日志
    if not LOG_FILE.exists():
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOG_FILE.write_text(
            f"# 批量转写后台日志 · {datetime.now():%Y-%m-%d %H:%M:%S}\n"
            f"# 范围 #{args.lo}-#{args.hi} · 批大小 {args.batch} · 批间休息 {args.rest}s\n\n",
            encoding="utf-8",
        )

    log("")
    log(f"=== 启动 loop_pipeline · 范围 #{args.lo}-#{args.hi} · "
        f"批大小 {args.batch} · 批间休息 {args.rest}s ===")
    log(f"清单 {len(rows)} 条；异常清单初始排除 {len(load_exclude())} 条")

    exclude = load_exclude()
    zero_runs = 0           # 连续零进展轮数
    last_stuck: list[tuple[str, str]] = []   # 最近一轮卡住的 (id, title)
    total_success = 0
    round_idx = 0
    t_start = time.time()

    while True:
        candidates = pick_rows(rows, args.lo, args.hi, exclude)
        if not candidates:
            log("✅ 已无剩余候选（全部处理完或都被异常清单排除），退出")
            break

        batch = candidates[: args.batch]
        ids = [r["id"] for r in batch]
        titles = {r["id"]: r["title"] for r in batch}
        seq_lo, seq_hi = batch[0]["seq"], batch[-1]["seq"]
        round_idx += 1

        # 本轮开始前已存在的 TXT 集合（本批里应为空，因为 pick_rows 已过滤）
        prev_existing: set[str] = set()
        for vid in ids:
            t = find_media(TRANS_DIR, vid, "txt")
            if t and t.stat().st_size > 0:
                prev_existing.add(vid)

        log("")
        log(f"── 第 {round_idx} 轮 · 序号 #{seq_lo}-#{seq_hi}（{len(batch)} 条）"
            f"  本段剩余约 {len(candidates)} 条 ──")

        t0 = time.time()
        rc = run_one_batch(ids)
        elapsed = time.time() - t0

        new_n, stuck_ids = count_new(prev_existing, ids)
        total_success += new_n
        if new_n > 0:
            zero_runs = 0
            last_stuck = []
        else:
            zero_runs += 1
            last_stuck = [(vid, titles[vid]) for vid in stuck_ids]

        log(f"  本轮新增 TXT: {new_n}/{len(batch)} · 退出码 {rc} · "
            f"耗时 {elapsed / 60:.1f} 分 · 累计成功 {total_success}")

        # 连续两轮零进展 → 记异常并排除
        if zero_runs >= 2:
            if last_stuck:
                log(f"  ⚠ 连续两轮零进展，把 {len(last_stuck)} 条加入异常清单并从后续轮次排除")
                append_anomaly(last_stuck)
                exclude = load_exclude()
                last_stuck = []
                zero_runs = 0
            else:
                # 已经无可记的（都被排除 / 都没问题）→ 自然结束
                log("  连续两轮零进展，但已无可记异常 → 自然结束")
                break

        # 剩余为空 → 全部完成
        if not pick_rows(rows, args.lo, args.hi, exclude):
            log(f"✅ 范围 #{args.lo}-#{args.hi} 全部完成")
            break

        # 批间休息（最后一轮不再睡：上面已经 break 出来）
        if not args.no_rest:
            log(f"  休息 {args.rest}s（{args.rest // 60} 分钟）...")
            time.sleep(args.rest)

    total_min = (time.time() - t_start) / 60
    log("")
    log(f"=== 全部结束 · 共 {round_idx} 轮 · 成功 {total_success} 条 · "
        f"总耗时 {total_min:.0f} 分 · 异常清单 {len(load_exclude())} 条 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())