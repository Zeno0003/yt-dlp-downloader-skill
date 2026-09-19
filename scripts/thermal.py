#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""读 macOS 热压状态（免 sudo），并按需等待冷却。

用苹果官方 API `NSProcessInfo.processInfo.thermalState`，通过 osascript 的 JXA
调用。**不需要 sudo、不需要编译任何东西**（实测本机 30 ms/次）。

为什么用热压而不是温度：thermalState 正是 macOS 用来决定要不要降频的**同一个信号**，
比外部读温度更贴近"系统觉得烫不烫"。Apple Silicon 上 `sysctl machdep.xcpm.*`
和 `powermetrics`（要 sudo，本环境 sudo 被禁）都拿不到可用读数。

取值：
    0  nominal   正常
    1  fair      略高，风扇/被动散热开始吃力
    2  serious   系统已开始降频（"电脑发烫"通常在这一档）
    3  critical  严重，应立刻停

用法（也可直接命令行）：
    python3 thermal.py            # 打印当前状态
    python3 thermal.py --watch 3  # 每 3 秒打印一次
"""

from __future__ import annotations

import argparse
import subprocess
import time

NAMES = {0: "nominal", 1: "fair", 2: "serious", 3: "critical", -1: "unknown"}

# JXA 一行：读 NSProcessInfo.thermalState（0..3）
_JXA = 'ObjC.import("Foundation"); $.NSProcessInfo.processInfo.thermalState'


def read() -> int:
    """返回 0..3；读不到返回 -1（例如 osascript 被限制）。"""
    try:
        out = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", _JXA],
            capture_output=True, text=True, timeout=20)
        return int(out.stdout.strip())
    except Exception:                                  # noqa: BLE001
        return -1


def name(value: int) -> str:
    return NAMES.get(value, "unknown")


def describe(value: int | None = None) -> str:
    v = read() if value is None else value
    return f"{name(v)}({v})"


def wait_cool(threshold: int = 2, max_wait: float = 1800, poll: float = 15,
              log=print) -> float:
    """等到热压降到 threshold 以下。

    threshold: 达到该值就认为"该歇了"（2=serious，1=fair 更保守）
    返回实际等待秒数；max_wait 用完仍未降下来也返回（调用方可自行决定）。
    """
    v = read()
    if v < threshold or v < 0:
        return 0.0
    t0 = time.time()
    log(f"    🌡 热压 {describe(v)} ≥ 阈值 {name(threshold)}，暂停冷却…")
    while time.time() - t0 < max_wait:
        time.sleep(poll)
        v = read()
        if v < threshold:
            waited = time.time() - t0
            log(f"    🌡 已回落到 {describe(v)}，冷却 {waited:.0f}s，继续")
            return waited
        log(f"    🌡 仍在 {describe(v)}，已等 {time.time() - t0:.0f}s")
    log(f"    ⚠ 冷却等待超过 {max_wait:.0f}s 仍未回落，继续跑（可 Ctrl-C 手动停）")
    return time.time() - t0


def main() -> int:
    ap = argparse.ArgumentParser(description="读 macOS 热压状态")
    ap.add_argument("--watch", type=float, default=None, metavar="秒",
                    help="每 N 秒持续打印")
    args = ap.parse_args()
    if args.watch:
        try:
            while True:
                print(f"{time.strftime('%H:%M:%S')}  {describe()}", flush=True)
                time.sleep(args.watch)
        except KeyboardInterrupt:
            return 0
    print(describe())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
