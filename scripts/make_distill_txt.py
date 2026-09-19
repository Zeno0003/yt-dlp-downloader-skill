#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把合集全部转写 TXT 合并成一份「蒸馏用」总 TXT（2026-09-12，zeno 需求）。

与 merge_transcripts.py（日期排序、带链接的阅读版）的差异：
- 按**序号升序**排列（合集顺序，稳定可引用），不是按发布日期
- 每条只留 **序号 + 标题 + 正文**，头部行附发布日期；**全文无任何 URL**
- 套用同一套增值处理：strip_header 剥表头 → CORRECTIONS 高置信纠错 → reflow 拼段落
- 条数与清单不符直接报错退出（不静默跳过）

原始单条 TXT 一个不动；只写出一个输出文件。

用法：
    python3 make_distill_txt.py [输出文件]
    （缺省输出：<项目根>/蒸馏/商业创新合集_全文<条数>条_无链接_<今日>.txt）
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from weibo_naming import find_media, load_rows, strip_header  # noqa: E402
from merge_transcripts import CORRECTIONS, UNCERTAIN, apply_corrections, reflow  # noqa: E402

TRANS_DIR = Path("/Users/zeno/WorkBuddy/yt-dlp视频转文本/transcript")  # 2026-09-12 迁入项目
DEFAULT_TSV = Path("/Users/zeno/WorkBuddy/yt-dlp视频转文本/data/商业创新合集_条目.tsv")
OUT_DIR = Path("/Users/zeno/WorkBuddy/yt-dlp视频转文本/蒸馏")


def main(argv: list[str]) -> int:
    out_file = Path(argv[1]) if len(argv) > 1 else None   # 未给路径 → 算完条数后生成默认名

    rows = load_rows(DEFAULT_TSV)
    if not rows:
        print(f"!! 清单为空或不存在：{DEFAULT_TSV}", file=sys.stderr)
        return 1
    rows.sort(key=lambda r: r["seq"])

    entries, total_corr = [], {}
    for r in rows:
        t = find_media(TRANS_DIR, r["id"], "txt")
        if not t or t.stat().st_size == 0:
            print(f"!! 序号#{r['seq']} 「{r['title']}」缺非空 TXT，中止（不静默跳过）",
                  file=sys.stderr)
            return 2
        raw = strip_header(t.read_text(encoding="utf-8"))
        fixed, stats = apply_corrections(raw)
        body = reflow(fixed)
        for why, n in stats.items():
            total_corr[why] = total_corr.get(why, 0) + n
        entries.append({
            "seq": r["seq"], "title": r["title"], "pub": r["pub"][:10], "body": body,
        })

    # 存疑项：只报告，不改
    unsure: dict[str, int] = {}
    for e in entries:
        for pat, why in UNCERTAIN:
            n = len(__import__("re").findall(pat, e["body"]))
            if n:
                unsure[why] = unsure.get(why, 0) + n

    total_chars = sum(len(e["body"]) for e in entries)
    L: list[str] = [
        f"老马自奋蹄 · 商业创新合集 全文（序号 {entries[0]['seq']}–{entries[-1]['seq']}）",
        f"条数 {len(entries)} ｜ 总字数约 {total_chars} ｜ 生成 {datetime.now():%Y-%m-%d %H:%M}",
        "—— 转写说明 ——",
        "1) whisper-large-v3-turbo 本地离线转写，未逐句人工校对，重要结论建议回看原视频核对。",
        f"2) 已对高置信错听做确定性纠正（共 {sum(total_corr.values())} 处）。",
    ]
    for why, n in sorted(total_corr.items(), key=lambda kv: -kv[1]):
        L.append(f"   · {why} —— 纠正 {n} 处")
    L.append("3) 正文已按句子边界重新分段（仅调整换行，未增删文字）；按序号升序排列。")
    if unsure:
        L.append("4) 以下转写存疑，刻意保留原样，请人工判断：")
        for why, n in sorted(unsure.items(), key=lambda kv: -kv[1]):
            L.append(f"   · {why}  ×{n}")
    L.append("")

    bar, thin = "=" * 72, "-" * 72
    for e in entries:
        L += [bar,
              f"序号#{e['seq']:03d} ｜ {e['title']} ｜ 发布 {e['pub']}",
              thin, e["body"], ""]

    text = "\n".join(L) + "\n"
    if "http" in text or "weibo.com" in text:
        print("!! 输出里检测到 URL，违反「无链接」要求，未写出", file=sys.stderr)
        return 3
    if out_file is None:   # 未给路径 → 用实际条数命名（2026-09-12 动态化）
        out_file = OUT_DIR / f"商业创新合集_全文{len(entries)}条_无链接_{datetime.now():%Y%m%d}.txt"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(text, encoding="utf-8")
    print(f">>> 已写入 {out_file}")
    print(f">>> {len(entries)} 条，约 {total_chars} 字，纠正 {sum(total_corr.values())} 处")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
