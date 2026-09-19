#!/usr/bin/env python3
"""把单条转录合并成一份总库 TXT (供阅读/学习用)。

做三件事:
1. 术语纠错: A/B 实测证明 initial_prompt 只能"部分修好"且不稳定
   (例: 结果技/结果计/结果寄/结果际 都是「结果级」的错听), 因此用确定性的
   替换表兜底。所有改动都会统计并写进文件头, 便于人工复核。
2. 重排版: whisper 的分段是按停顿切的, 一行几个字, 读起来很碎。
   这里把连续分段按句子边界拼回段落, 不改动任何文字内容。
3. 按日期升序排列 (看观点演变), 注意列表顺序并非严格按日期。

用法:
    python3 merge_transcripts.py <ids.txt> <输出文件> [transcript目录] [audio目录]

元数据来源(标题/日期/时长), 按顺序取第一个存在的:
    1) <audio目录>/<id>.info.json   (若下载时带过 --write-info-json)
    2) 合集清单 TSV                  (batch_pipeline.py 默认不带 info.json, 所以主要靠这条)
    3) 都没有 → 退化成 id, 日期 00000000 (会排在最前, 需人工注意)
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

PROJ = Path("/Users/zeno/WorkBuddy/yt-dlp视频转文本")   # 2026-09-12 媒体/转写迁入项目
DEF_TRANSCRIPT = PROJ / "transcript"
DEF_AUDIO = PROJ / "audio"
# 合集清单(单一真源), 列: 序号 集数 微博ID mblogid 发布时间 时长 标题 链接
DEF_TSV = Path("/Users/zeno/WorkBuddy/yt-dlp视频转文本/data/商业创新合集_条目.tsv")


def load_tsv_map(tsv: Path) -> dict[str, dict]:
    """从合集清单取标题/日期/时长, 补 info.json 的缺口。"""
    m: dict[str, dict] = {}
    if not tsv.exists():
        return m
    for line in tsv.read_text(encoding="utf-8").splitlines()[1:]:
        c = line.split("\t")
        if len(c) < 8 or not c[2].isdigit():
            continue
        pub, dur, title = c[4].strip(), c[5].strip(), c[6].strip()
        date8 = re.sub(r"\D", "", pub)[:8]              # "2024-09-30 10:50" -> "20240930"
        secs = 0
        try:
            parts = [int(x) for x in dur.split(":")]
            while parts:
                secs = secs * 60 + parts.pop(0)
        except ValueError:
            secs = 0
        m[c[2]] = {"seq": int(c[0]) if c[0].strip().isdigit() else None,
                   "title": title, "date": date8, "duration": float(secs)}
    return m

# 确定性纠错表: 只放高置信、可解释的条目。
# 依据: 同一作者反复使用固定术语, 且标题/上下文已给出正确写法。
CORRECTIONS: list[tuple[str, str, str]] = [
    # (正则, 替换, 说明) —— 只放【高置信】条目: 错写法在该语境下不是合法中文,
    # 或正确写法在同一作者的其他语料里被明确写出过。
    # 领域术语
    (r"结果[技计寄际纪级]服务", "结果级服务", "结果级服务 (A/B 实测被听成 技/计/寄/际)"),
    (r"老灯公司", "老登公司", "老登公司 (视频标题即写作「老登公司」)"),
    (r"AI原始组织", "AI原生组织", "AI原生组织 (他被听成「原始」)"),
    (r"搓一板", "搓衣板", "搓衣板 (洗衣机隐喻)"),
    # 常见商业词同音错听
    (r"支付库", "知识库", "知识库 (「最佳实践知识库」「项目实施知识库」)"),
    (r"假以方", "甲乙方", "甲乙方"),
    (r"以方厂商", "乙方厂商", "乙方厂商 (与「对甲方企业而言」对举)"),
    (r"反普", "反哺", "反哺 (「反哺行业与产品」)"),
    (r"SARS", "SaaS", "SaaS (全文多处: SaaS时代/SaaS厂商/SaaS末日论)"),
    (r"认知误缺", "认知误区", "认知误区"),
    (r"盲目造班", "盲目照搬", "照搬 (「盲目照搬个案」)"),
    (r"移交给育媒", "移交给运维", "运维 (交付话术)"),
    (r"企业技艺", "企业记忆", "企业记忆 (同文另有「企业记忆系统」)"),
    (r"招式与股价", "招式与骨架", "骨架 (「招式与骨架」对举)"),
    (r"以方法为股价", "以方法为骨架", "骨架"),
    (r"功利的顾问", "功底的顾问", "功底"),
    (r"AI实在顾问", "AI时代顾问", "AI时代顾问"),
]

# 拿不准的: 【不做改动】, 只在文件头列出, 交由人工判断。
# 宁可留一个显眼的错字让人一眼看到, 也不要擅自改成可能错误的写法。
UNCERTAIN: list[tuple[str, str]] = [
    (r"威曼29条关键判断", "「威曼」疑为某咨询方法论/人名, 正确写法未确认"),
    (r"华为三接十二法", "「三接」疑为「三阶」等, 未确认"),
    (r"价富能客户", "疑为「价值赋能客户」, 未确认"),
]

SENT_END = "。！？!?…"
MIN_PARA, MAX_PARA = 200, 480


def find_txt(tdir: Path, vid: str) -> Path | None:
    """按 `<序号>_<标题>_<ID>.txt` 找；再退回 `<标题>_<ID>.txt` / `<ID>.txt`。
    （与 skill 的 scripts/weibo_naming.py 同规则，那边是唯一真源。）"""
    for pat in (f"*_{vid}.txt", f"{vid}.txt"):
        hits = sorted(tdir.glob(pat))
        if hits:
            return hits[0]
    return None


def strip_header(raw: str) -> str:
    """剥掉 transcribe_batch.py 写的表头（`#` 开头的连续行），只留正文。"""
    lines = raw.splitlines()
    i = 0
    while i < len(lines) and (lines[i].startswith("#") or not lines[i].strip()):
        i += 1
    return "\n".join(lines[i:])


def apply_corrections(text: str) -> tuple[str, dict[str, int]]:
    stats: dict[str, int] = {}
    for pat, rep, why in CORRECTIONS:
        text, n = re.subn(pat, rep, text)
        if n:
            stats[why] = stats.get(why, 0) + n
    return text, stats


def reflow(text: str) -> str:
    """把逐段输出拼回可读段落; 只调整换行, 不改字。"""
    segs = [ln.strip() for ln in text.splitlines() if ln.strip()]
    paras: list[str] = []
    buf = ""
    for s in segs:
        buf += s
        if (len(buf) >= MIN_PARA and s[-1] in SENT_END) or len(buf) >= MAX_PARA:
            paras.append(buf)
            buf = ""
    if buf:
        paras.append(buf)
    return "\n\n".join(paras)


def hhmmss(sec: float) -> str:
    sec = int(round(sec or 0))
    return f"{sec // 60:02d}:{sec % 60:02d}"


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 1
    ids_file, out_file = Path(argv[1]), Path(argv[2])
    tdir = Path(argv[3]) if len(argv) > 3 else DEF_TRANSCRIPT
    adir = Path(argv[4]) if len(argv) > 4 else DEF_AUDIO

    ids = [ln.strip() for ln in ids_file.read_text(encoding="utf-8").splitlines()
           if ln.strip() and not ln.strip().startswith("#")]

    tsv_map = load_tsv_map(DEF_TSV)
    missing_meta = 0
    entries = []
    for vid in ids:
        txt_p = find_txt(tdir, vid)
        info_p = adir / f"{vid}.info.json"
        if not txt_p:
            print(f"!! 缺转录, 跳过 {vid}", file=sys.stderr)
            continue
        info = json.loads(info_p.read_text(encoding="utf-8")) if info_p.exists() else {}
        meta = tsv_map.get(vid, {})
        if not info and not meta:
            missing_meta += 1
        title = (meta.get("title") or info.get("title") or vid).strip()
        date = meta.get("date") or info.get("upload_date") or "00000000"
        duration = meta.get("duration") or float(info.get("duration") or 0)
        raw = strip_header(txt_p.read_text(encoding="utf-8"))
        fixed, stats = apply_corrections(raw)
        body = reflow(fixed)
        entries.append({
            "id": vid,
            "seq": meta.get("seq"),
            "title": title,
            "date": date,
            "duration": duration,
            "tags": info.get("tags") or [],
            "body": body,
            "chars": len(body),
            "corrections": stats,
        })
    if missing_meta:
        print(f"!! 有 {missing_meta} 条既无 info.json 也不在清单里, "
              f"标题/日期已退化成 id —— 检查 {DEF_TSV}", file=sys.stderr)

    entries.sort(key=lambda e: (e["date"], e["id"]))  # 升序: 看观点演变

    total_corr: dict[str, int] = {}
    for e in entries:
        for k, v in e["corrections"].items():
            total_corr[k] = total_corr.get(k, 0) + v

    d0 = entries[0]["date"] if entries else ""
    d1 = entries[-1]["date"] if entries else ""

    def fmt(d: str) -> str:
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else d

    seqs = [e["seq"] for e in entries if isinstance(e.get("seq"), int)]
    range_txt = f"序号 {min(seqs)}–{max(seqs)}" if seqs else "序号未知"

    L: list[str] = []
    L.append(f"# 老马自奋蹄 · 商业创新合集 文字稿（{range_txt}）")
    L.append("#")
    L.append(f"# 作者微博：https://weibo.com/u/1807436544")
    L.append(f"# 范围：{range_txt} ｜ 条数：{len(entries)} 条 ｜ "
             f"时间跨度：{fmt(d0)} ~ {fmt(d1)}")
    L.append(f"# 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    L.append(f"# 总字数：约 {sum(e['chars'] for e in entries)} 字")
    L.append("#")
    L.append("# —— 转写说明（请务必阅读）——")
    L.append("# 1) 本文件由 whisper-large-v3-turbo 在本地离线转写，未逐句人工校对。")
    L.append("#    中文专业术语仍可能存在个别出入，重要结论建议回看原视频核对。")
    L.append("# 2) 已对以下高置信错听做了确定性纠正（共 %d 处）：" % sum(total_corr.values()))
    if total_corr:
        for why, n in sorted(total_corr.items(), key=lambda kv: -kv[1]):
            L.append(f"#      · {why} —— 纠正 {n} 处")
    else:
        L.append("#      · （本次未命中任何已知错词）")
    L.append("# 3) 正文已按句子边界重新分段（仅调整换行，未增删文字）。")
    L.append("# 4) 按发布日期升序排列，便于观察其观点的演进。")

    # 存疑项: 只报告, 不修改
    unsure_hits: dict[str, int] = {}
    for e in entries:
        for pat, why in UNCERTAIN:
            n = len(re.findall(pat, e["body"]))
            if n:
                unsure_hits[why] = unsure_hits.get(why, 0) + n
    if unsure_hits:
        L.append("# 5) ⚠️ 以下位置转写存疑，【刻意保留原样未改】，请人工判断：")
        for why, n in sorted(unsure_hits.items(), key=lambda kv: -kv[1]):
            L.append(f"#      · {why}  ×{n}")
    L.append("")
    L.append("")

    for i, e in enumerate(entries, 1):
        tag = f"序号#{e['seq']} " if isinstance(e.get("seq"), int) else ""
        L.append("=" * 72)
        L.append(f"[{i:02d}/{len(entries)}] {tag}{e['title']}")
        L.append(f"日期 {fmt(e['date'])} ｜ 时长 {hhmmss(e['duration'])} ｜ "
                 f"https://weibo.com/1807436544/{e['id']}")
        L.append("-" * 72)
        L.append(e["body"])
        L.append("")

    out_file.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f">>> 已写入 {out_file}")
    print(f">>> {len(entries)} 条, 约 {sum(e['chars'] for e in entries)} 字, "
          f"纠正合计 {sum(total_corr.values())} 处")
    for why, n in sorted(total_corr.items(), key=lambda kv: -kv[1]):
        print(f"      {why}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
