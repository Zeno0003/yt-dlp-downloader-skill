#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把已有素材规范化成 `<序号>_<标题>_<ID>.<ext>`，并给 TXT 补上表头。

用途：命名规则改过之后（2026-09-11 zeno 要求文件名带序号+标题），把已经落盘的
旧素材一次性归位。**幂等**：已规范的跳过，重复跑安全。

默认 **dry-run**（只打印计划，不动手）。真改要显式加 `--apply`。
`--apply` 时会先把 `transcript/*.txt` 整份备份到 `_backup_names_<时间戳>/`，
并写一份 `old -> new` 映射表，便于回滚。

不碰清单之外的 id（那些是别的任务的产物，认不出序号/标题）。

用法：
    PY=/Users/zeno/.workbuddy/binaries/python/versions/3.13.12/bin/python3
    S=~/.workbuddy/skills/yt-dlp-downloader/scripts/migrate_names.py
    $PY $S                    # 看看会改什么
    $PY $S --apply            # 真改
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from weibo_naming import find_media, header_block, load_rows, target_name  # noqa: E402

ROOT = Path("/Users/zeno/WorkBuddy/yt-dlp视频转文本")   # 2026-09-12 媒体/转写迁入项目
TXT_DIR = ROOT / "transcript"
AUDIO_DIR = ROOT / "audio"
DEFAULT_TSV = Path("/Users/zeno/WorkBuddy/yt-dlp视频转文本/data/商业创新合集_条目.tsv")

HEAD_MARK = "# ─"          # 表头第一行的开头，用来判断有没有写过


def has_header(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.readline().startswith(HEAD_MARK)
    except OSError:
        return False


def plan_for(rows: list[dict]) -> list[dict]:
    """列出要做的事，不改任何东西。同一条目的「改名」一定排在「补表头」前面。"""
    todo: list[dict] = []
    for r in rows:
        for ext, d in (("txt", TXT_DIR), ("mp4", AUDIO_DIR)):
            cur = find_media(d, r["id"], ext)
            if not cur:
                continue
            want = d / target_name(r["seq"], r["title"], r["id"], ext)
            final = cur
            if cur != want:
                if want.exists():
                    todo.append({"act": "冲突", "row": r, "src": cur, "dst": want,
                                 "why": f"{want.name} 已存在，为免覆盖不动"})
                    continue
                todo.append({"act": "改名", "row": r, "src": cur, "dst": want, "why": ""})
                final = want
            if ext == "txt" and not has_header(cur):   # 改名不改内容，查 cur 就行
                todo.append({"act": "补表头", "row": r, "src": final, "dst": final, "why": ""})
    return todo


def main() -> int:
    ap = argparse.ArgumentParser(description="规范化素材命名 + 补 TXT 表头（默认 dry-run）")
    ap.add_argument("--apply", action="store_true", help="真的改（不加则只打印计划）")
    ap.add_argument("--tsv", default=str(DEFAULT_TSV))
    ap.add_argument("--root", default=str(ROOT))
    args = ap.parse_args()

    global TXT_DIR, AUDIO_DIR
    root = Path(args.root).expanduser()
    TXT_DIR, AUDIO_DIR = root / "transcript", root / "audio"

    rows = load_rows(Path(args.tsv).expanduser())
    if not rows:
        print(f"!! 清单是空的：{args.tsv}")
        return 1

    todo = plan_for(rows)
    by_act: dict[str, int] = {}
    for t in todo:
        by_act[t["act"]] = by_act.get(t["act"], 0) + 1
    print(f"清单 {len(rows)} 条 ｜ 计划动作：{by_act or '无（已全部规范）'}")
    for t in todo[:12]:
        arrow = f"{t['src'].name}  ->  {t['dst'].name}" if t["act"] != "补表头" \
            else f"{t['src'].name}  （加表头）"
        print(f"   [{t['act']}] #{t['row']['seq']:>3}  {arrow}")
    if len(todo) > 12:
        print(f"   … 另有 {len(todo) - 12} 项")

    if not args.apply:
        print("\n（dry-run：未改动任何文件。要执行加 --apply）")
        return 0

    if not todo:
        print("\n无需改动，跳过备份。")
        return 0

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bdir = root / f"_backup_names_{ts}"
    bdir.mkdir(parents=True, exist_ok=True)
    n_bak = 0
    for p in TXT_DIR.glob("*.txt"):
        shutil.copy2(p, bdir / p.name)
        n_bak += 1
    print(f"\n已备份 {n_bak} 个 TXT -> {bdir}")

    log_lines: list[str] = ["old\tnew\taction"]
    renamed = headed = failed = 0
    for t in todo:
        try:
            if t["act"] == "改名":
                t["src"].rename(t["dst"])
                log_lines.append(f"{t['src']}\t{t['dst']}\t改名")
                renamed += 1
            elif t["act"] == "补表头":
                body = t["src"].read_text(encoding="utf-8")
                t["src"].write_text(header_block(t["row"]) + body, encoding="utf-8")
                log_lines.append(f"{t['src']}\t{t['src']}\t补表头")
                headed += 1
        except OSError as e:                           # noqa: BLE001
            failed += 1
            print(f"   !! 失败 #{t['row']['seq']} {t['act']}：{e}")

    log_p = root / f"_rename_map_{ts}.tsv"
    log_p.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    print(f"改名 {renamed} 个、补表头 {headed} 个、失败 {failed} 个")
    print(f"映射表 -> {log_p}（回滚依据）")
    return 0 if failed == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
