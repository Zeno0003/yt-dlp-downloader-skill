#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""微博合集「一键批量转写」流水线：序号范围 → 下载 → 校验 → 转写 → 合并。

好处是**新对话零上下文也能跑对**：只要给一个序号范围，剩下的事（挑条目、拼单条链接、
批量下载、修 .part 残留、用 ffprobe 校验时长、幂等转写、合并长文）都由它做。

命名规则（2026-09-11 zeno 要求）：素材一律命名成 `<序号>_<标题>_<视频ID>.<ext>`，
序号按清单「由旧数起」三位补零，便于在 Finder 里按顺序排。旧的 `<ID>.<ext>` 仍能识别。

用法：
    PY=/Users/zeno/.workbuddy/binaries/python/versions/3.13.12/bin/python3
    S=~/.workbuddy/skills/yt-dlp-downloader/scripts/batch_pipeline.py

    $PY $S 1-60                # 序号 1–60（清单见 --tsv，默认商业创新合集）
    $PY $S --next 40           # 从第一个还没转写的开始，跑 40 条
    $PY $S --ids 5084582845615713,5084945234006672
    $PY $S --status            # 只看进度，什么都不动
    $PY $S 1-60 --dry-run      # 只打印计划
    $PY $S 1-60 --guard        # 开自动冷却（热压 serious 就暂停，回落继续）
    $PY $S 1-60 --merge        # 转完顺手合并成一份带范围文件名的长文
    $PY $S --merge-only 1-60   # 只合并，不下载不转写
    $PY $S 1-60 --delete-video # 转完删掉 mp4（默认保留，见下）
    $PY $S --cleanup           # 单独清 mp4 缓存（每轮 ≤50 个）

⚠️ **默认保留 mp4**（2026-09-11 zeno 要求改成不删）。全量 469 条约占 6 GB，
   要省空间加 `--delete-video`，或事后用 `--cleanup` 分批清。

退出码：0 正常 / 1 参数错 / 2 下载失败 / 3 转写失败
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import thermal  # noqa: E402

YTDLP = "/Users/zeno/Documents/04-software-packages/yt-dlp-videodownload/yt-dlp_macos"
FFPROBE = "/opt/homebrew/bin/ffprobe"
VENV_PY = "/Users/zeno/Documents/CodeProject/video-subtitle/.venv/bin/python"
MERGE_PY = Path(__file__).resolve().parent / "merge_transcripts.py"   # 2026-09-12 收编进技能目录

PROJ_DIR = Path("/Users/zeno/WorkBuddy/yt-dlp视频转文本")             # 2026-09-12 媒体/转写迁入
AUDIO_DIR = PROJ_DIR / "audio"
TRANSCRIPT_DIR = PROJ_DIR / "transcript"
MERGED_DIR = PROJ_DIR / "合并稿"                                      # 合并稿放项目里，便于阅读
DEFAULT_TSV = Path("/Users/zeno/WorkBuddy/yt-dlp视频转文本/data/商业创新合集_条目.tsv")
QUALITY_TSV_NAME = "下载画质台账.tsv"   # 与 TSV 同目录；按 id 覆盖写，便于比对

# 微博没有独立音频流，只能取合流档喂给 whisper。三档音频轨完全相同（44k AAC），
# 所以**首选最小档省流量**；但实测踩到过「540P 那档的 CDN 地址坏了、720P/1080P 却是好的」
# （2026-09-11，#38 第37期），所以下载必须**逐档退让**，不能只用一档就判死刑。
YTDLP_TIERS = ("res:540", "res:720", "res:1080")
DUR_TOL_SEC = 5          # 时长校验容差：绝对 5 秒
DUR_TOL_RATIO = 0.03     # 或 3%


def log(msg: str = "") -> None:
    print(msg, flush=True)


# 命名规则 / 素材查找 / 清单解析都来自共用模块 `weibo_naming.py`，别在这里另写一份
from weibo_naming import (  # noqa: E402
    find_media, load_rows, load_unavailable, parse_dur, safe_title,
    target_name, unavail_path,
)




def ffprobe_info(path: Path) -> dict:
    """一次 ffprobe 同时拿时长和分辨率（顺手用于画质台账，不额外增加探测开销）。"""
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error",
             "-show_entries", "format=duration:stream=width,height,codec_type",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60)
        d = json.loads(out.stdout)
    except Exception:                                  # noqa: BLE001
        return {"duration": -1.0}
    info: dict = {"duration": -1.0}
    try:
        info["duration"] = float(d["format"]["duration"])
    except Exception:                                  # noqa: BLE001
        pass
    for s in d.get("streams", []):
        if s.get("codec_type") == "video" and s.get("width"):
            info["width"], info["height"] = s["width"], s["height"]
            break
    return info


def tier_of(w: int, h: int) -> str:
    """按短边反推档位（微博竖屏：540x960 / 720x1280 / 1080x1920）。"""
    m = min(w, h)
    for t in (540, 720, 1080):
        if abs(m - t) <= 60:
            return f"{t}P"
    return f"{m}p"


def update_quality_ledger(data_dir: Path, entries: list[dict]) -> Path:
    """写「下载画质台账」：按视频 id **覆盖**该行，保证重跑逐字节一致。

    entries 每项：{"seq", "id", "w", "h", "mb"}
    """
    p = data_dir / QUALITY_TSV_NAME
    rows: dict[str, list[str]] = {}
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines()[1:]:
            c = ln.split("\t")
            if len(c) >= 5 and c[1].isdigit():
                rows[c[1]] = c
    for e in entries:
        rows[e["id"]] = [str(e["seq"]), e["id"], f"{e['w']}x{e['h']}",
                         tier_of(e["w"], e["h"]), f"{e['mb']:.1f}"]
    ordered = sorted(rows.values(), key=lambda r: int(r[0]))
    head = "序号\t视频ID\t分辨率\t档位\t大小MB\n"
    p.write_text(head + "".join("\t".join(r) + "\n" for r in ordered), encoding="utf-8")
    return p


def select(rows: list[dict], args, unavail: dict[str, str] | None = None) -> list[dict]:
    if args.ids:                                       # 显式点名 → 照跑，不受失效名单约束
        want = [i.strip() for i in args.ids.split(",") if i.strip()]
        idx = {r["id"]: r for r in rows}
        return [idx[i] for i in want if i in idx]
    unavail = unavail or {}
    rows = [r for r in rows if r["id"] not in unavail]
    done = {r["id"] for r in rows
            if (t := find_media(TRANSCRIPT_DIR, r["id"], "txt")) and t.stat().st_size > 0}
    if args.next:
        return [r for r in rows if r["id"] not in done][: args.next]
    lo, hi = (int(x) for x in args.range.split("-"))
    return [r for r in rows if lo <= r["seq"] <= hi]


def do_status(rows: list[dict]) -> None:
    done = {r["id"] for r in rows
            if (t := find_media(TRANSCRIPT_DIR, r["id"], "txt")) and t.stat().st_size > 0}
    have = {r["id"] for r in rows if find_media(AUDIO_DIR, r["id"], "mp4")}
    unavail = load_unavailable(DEFAULT_TSV)
    todo = [r for r in rows if r["id"] not in done and r["id"] not in unavail]
    left = sum(parse_dur(r["dur"]) for r in todo)
    log(f"清单         {len(rows)} 条  ({DEFAULT_TSV})")
    log(f"已转写       {len(done)} 条")
    log(f"待转写       {len(todo)} 条，音频合计 {left / 3600:.1f} 小时 "
        f"（≈ GPU 满载 {left / 26 / 60:.0f} 分钟）")
    if unavail:
        log(f"已失效排除   {len(unavail)} 条（手工维护，见 {unavail_path(DEFAULT_TSV).name}）")
        for r in rows:
            if r["id"] in unavail:
                log(f"     #{r['seq']} {r['title'][:28]} —— {unavail[r['id']] or '未写原因'}")
    log(f"本地已有视频 {len(have)} 条（默认保留；要清用 --cleanup）")
    log(f"当前热压     {thermal.describe()}")
    if todo:
        log(f"下一条待转   序号 #{todo[0]['seq']}  {todo[0]['title'][:40]}")
        log(f"建议命令     batch_pipeline.py --next 40")


def append_progress(data_dir: Path, batch: str, ok: int, skip: int, bad: int,
                    peak: str, cooled: float, elapsed: float) -> None:
    """往 data/转写进度.md 追加一行台账，方便长期跟踪。"""
    p = data_dir / "转写进度.md"
    if not p.exists():
        p.write_text(
            "# 商业创新合集 · 转写进度台账\n\n"
            "由 `batch_pipeline.py` 每批跑完自动追加。\n\n"
            "| 时间 | 批次 | 成功 | 跳过 | 异常 | 热压峰值 | 冷却 | 本批耗时 |\n"
            "|---|---|---|---|---|---|---|---|\n", encoding="utf-8")
    import datetime as _dt
    row = (f"| {_dt.datetime.now():%Y-%m-%d %H:%M} | {batch} | {ok} | {skip} | {bad} | "
           f"{peak} | {cooled:.0f}s | {elapsed / 60:.1f} 分 |\n")
    with open(p, "a", encoding="utf-8") as f:
        f.write(row)


def do_cleanup(rows: list[dict]) -> None:
    """删掉「已转写成功」对应的 mp4 缓存。只删 TXT 非空的，幂等、可重跑。

    系统安全阀单轮最多删 50 个文件，超了会被拒；被拒的留下次再删即可。
    """
    cand = []
    for r in rows:
        mp4 = find_media(AUDIO_DIR, r["id"], "mp4")
        txt = find_media(TRANSCRIPT_DIR, r["id"], "txt")
        if mp4 and txt and txt.stat().st_size > 0:
            cand.append((r, mp4))
    total_mb = sum(m.stat().st_size for _, m in cand) / 1048576
    log(f"可清理 {len(cand)} 个已转写 mp4，合计 {total_mb:.0f} MB")
    if not cand:
        log("没有可清理的（都已清过，或还没转写）")
        return
    freed = removed = denied = 0
    for i, (r, mp4) in enumerate(cand, 1):
        try:
            freed += mp4.stat().st_size
            mp4.unlink()
            removed += 1
        except OSError as e:                           # noqa: BLE001
            denied += 1
            if denied == 1:
                log(f"  首个被拒 #{r['seq']}：{e}")
        if i % 10 == 0:
            log(f"  ...已处理 {i}/{len(cand)}（删成 {removed}）")
    log(f"已删除 {removed} 个，释放 {freed / 1048576:.0f} MB"
        + (f"；{denied} 个被安全阀拦下 → 隔一轮再跑 --cleanup 继续" if denied else ""))
    log(f"audio 目录剩余 {len(list(AUDIO_DIR.glob('*.mp4')))} 个 mp4")


def range_label(sel: list[dict]) -> str:
    """给这一批起个**不骗人**的标签（台账 + 合并稿文件名都用它）。

    连续 → `1-60`；不连续且条数少 → 逐个列 `38,61`；不连续且多 → `38-471（共47条）`。
    别直接用 min-max，`--next 2` 这种会选到 #38 和 #61，写成 `38-61` 是假的。
    """
    seqs = sorted(r["seq"] for r in sel)
    if not seqs:
        return "空"
    if seqs == list(range(seqs[0], seqs[-1] + 1)):
        return f"{seqs[0]}" if len(seqs) == 1 else f"{seqs[0]}-{seqs[-1]}"
    if len(seqs) <= 6:
        return ",".join(str(s) for s in seqs)
    return f"{seqs[0]}-{seqs[-1]}（共{len(seqs)}条，不连续）"


def normalize_names(rows: list[dict]) -> int:
    """把旧的 `<ID>.mp4` 改成 `<序号>_<标题>_<ID>.mp4`（幂等，已是新名则不动）。"""
    n = 0
    for r in rows:
        old = AUDIO_DIR / f"{r['id']}.mp4"
        if not old.exists():
            continue
        new = AUDIO_DIR / target_name(r["seq"], r["title"], r["id"], "mp4")
        if new == old:
            continue
        try:
            if new.exists():
                old.unlink(missing_ok=True)            # 新名已在，旧的当重复删掉
            else:
                old.rename(new)
            n += 1
        except OSError as e:                           # noqa: BLE001
            log(f"  #{r['seq']} 改名失败：{e}")
    return n


def do_merge(sel: list[dict], out_dir: Path, tag_range: str) -> Path | None:
    """调 merge_transcripts.py 把选中的条目合并成一份长文（文件名带范围）。"""
    if not MERGE_PY.exists():
        log(f"!! 找不到合并脚本：{MERGE_PY}")
        return None
    have = [r for r in sel
            if (t := find_media(TRANSCRIPT_DIR, r["id"], "txt")) and t.stat().st_size > 0]
    miss = [r for r in sel if r not in have]
    if not have:
        log("!! 选中的条目一条 TXT 都没有，无法合并")
        return None
    ids_file = HERE / "_merge_ids.txt"
    ids_file.write_text("".join(r["id"] + "\n" for r in sel), encoding="utf-8")
    import datetime as _dt
    out = out_dir / f"商业创新合集_合并_序号{tag_range}_{_dt.datetime.now():%Y%m%d}.md"
    cmd = [VENV_PY, str(MERGE_PY), str(ids_file), str(out)]
    log("  $ " + " ".join(cmd))
    rc = subprocess.run(cmd).returncode
    ids_file.unlink(missing_ok=True)
    if miss:
        log(f"  （{len(miss)} 条没有 TXT 已跳过：" +
            ", ".join(f"#{r['seq']}" for r in miss[:12]) +
            ("…" if len(miss) > 12 else "") + "）")
    if rc == 0 and out.exists():
        log(f"  合并稿 -> {out}（{out.stat().st_size / 1024:.0f} KB）")
        return out
    log(f"!! 合并失败，退出码 {rc}")
    return None


def do_quality_scan(rows: list[dict], data_dir: Path) -> None:
    """扫本地**已有**的 mp4，把实际画质补进台账（幂等，按 id 覆盖）。

    用于给「台账功能上线前就下好的」旧素材补记录，也让 `--delete-video` 之前
    能留一份"这条当初下的是哪档"的凭据。
    """
    quality: list[dict] = []
    for r in rows:
        mp4 = find_media(AUDIO_DIR, r["id"], "mp4")
        if not mp4:
            continue
        info = ffprobe_info(mp4)
        if not info.get("width"):
            log(f"  #{r['seq']:>3} 读不到分辨率，跳过：{mp4.name}")
            continue
        quality.append({"seq": r["seq"], "id": r["id"], "w": info["width"],
                        "h": info["height"], "mb": mp4.stat().st_size / 1048576})
    if not quality:
        log("本地没有可扫描的 mp4")
        return
    tiers: dict[str, int] = {}
    for q in quality:
        t = tier_of(q["w"], q["h"])
        tiers[t] = tiers.get(t, 0) + 1
    log(f"扫到 {len(quality)} 个 mp4：" + "、".join(f"{k} {v} 条" for k, v in sorted(tiers.items())))
    log(f"画质台账 -> {update_quality_ledger(data_dir, quality)}")


def main() -> int:
    ap = argparse.ArgumentParser(description="微博合集批量转写流水线")
    ap.add_argument("range", nargs="?", help="序号范围，如 1-60")
    ap.add_argument("--next", type=int, help="从第一个未转写的开始，跑 N 条")
    ap.add_argument("--ids", help="直接指定微博 id，逗号分隔")
    ap.add_argument("--tsv", default=str(DEFAULT_TSV))
    ap.add_argument("--guard", action="store_true", help="开启自动冷却")
    ap.add_argument("--guard-at", choices=["fair", "serious"], default="serious")
    ap.add_argument("--delete-video", action="store_true",
                    help="转完删掉 mp4（默认保留，见文件头说明）")
    ap.add_argument("--skip-download", action="store_true", help="跳过下载步骤")
    ap.add_argument("--status", action="store_true", help="只看进度")
    ap.add_argument("--cleanup", action="store_true",
                    help="只删「已转写成功」的 mp4，不下载不转写（每轮 ≤50 个）")
    ap.add_argument("--quality-scan", action="store_true",
                    help="扫本地已有的 mp4，刷新画质台账后退出（幂等）")
    ap.add_argument("--merge", action="store_true",
                    help="转写完成后合并成一份带范围文件名的长文")
    ap.add_argument("--merge-only", action="store_true",
                    help="只合并（按给定范围），不下载不转写")
    ap.add_argument("--emit-ids", metavar="路径",
                    help="把选中条目的 id 按序号升序写成一个 txt 后退出")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划")
    args = ap.parse_args()

    tsv = Path(args.tsv).expanduser()
    if not tsv.exists():
        log(f"!! 清单不存在：{tsv}")
        return 1
    rows = load_rows(tsv)

    if args.status:
        do_status(rows)
        return 0

    if args.cleanup:
        do_cleanup(rows)
        return 0

    if args.quality_scan:
        do_quality_scan(rows, tsv.parent)
        return 0

    if not (args.range or args.next or args.ids):
        do_status(rows)
        log("\n（未给范围，只显示进度。用法示例：batch_pipeline.py 1-60）")
        return 0

    unavail = load_unavailable(tsv)
    picked = select(rows, args, unavail)
    if not picked:
        log("!! 选出来 0 条，检查范围/ids（或都已被失效名单排除）")
        return 1

    if args.emit_ids:
        out = Path(args.emit_ids).expanduser()
        out.write_text("".join(r["id"] + "\n" for r in picked), encoding="utf-8")
        log(f"已写出 {len(picked)} 个 id（按序号升序）-> {out}")
        return 0

    tag_range = range_label(picked)

    if args.merge_only:
        MERGED_DIR.mkdir(parents=True, exist_ok=True)
        log(f"=== 只合并 序号 {tag_range}（{len(picked)} 条）===")
        return 0 if do_merge(picked, MERGED_DIR, tag_range) else 3

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)

    total_audio = sum(parse_dur(r["dur"]) for r in picked)
    log(f"=== 批量转写 序号 {tag_range}"
        f"（{len(picked)} 条，音频 {total_audio / 3600:.1f} 小时）===")
    log(f"当前热压 {thermal.describe()}  自动冷却={'开' if args.guard else '关'}"
        f"  视频={'转完删除' if args.delete_video else '保留'}")
    if unavail:
        hits = [r for r in rows if r["id"] in unavail]
        log(f"已失效排除 {len(hits)} 条（手工名单 {unavail_path(tsv).name}）："
            + ", ".join(f"#{r['seq']}" for r in hits[:12]))

    todo_txt = [r for r in picked
                if not ((t := find_media(TRANSCRIPT_DIR, r["id"], "txt"))
                        and t.stat().st_size > 0)]
    log(f"其中已转写 {len(picked) - len(todo_txt)} 条（会跳过），待转写 {len(todo_txt)} 条")
    if not todo_txt:
        log("这批全都转过了。")
        if args.merge:
            MERGED_DIR.mkdir(parents=True, exist_ok=True)
            do_merge(picked, MERGED_DIR, tag_range)
        return 0

    if args.dry_run:
        for r in todo_txt:
            log(f"   #{r['seq']:>3}  {r['dur']:>6}  {r['title'][:44]}  {r['url']}")
        log("（--dry-run：未做任何事）")
        return 0

    # ---------- 1. 下载缺失的 ----------
    missing = [r for r in todo_txt if not find_media(AUDIO_DIR, r["id"], "mp4")]
    t_batch_start = time.time()
    log("")
    log(f"--- 步骤 1/4 下载：缺 {len(missing)} 条 ---")
    if missing and not args.skip_download:
        urls_file = HERE / "_batch_urls.txt"
        still = missing
        for ti, tier in enumerate(YTDLP_TIERS, 1):
            if not still:
                break
            urls_file.write_text("".join(r["url"] + "\n" for r in still), encoding="utf-8")
            log(f"  档位 {tier}（第 {ti}/{len(YTDLP_TIERS)} 轮，{len(still)} 条待下）")
            cmd = [YTDLP, "-S", tier, "--no-part", "--ignore-errors",
                   "--retries", "3", "--fragment-retries", "3",
                   "-o", str(AUDIO_DIR / "%(id)s.%(ext)s"),
                   "-a", str(urls_file)]
            log("  $ " + " ".join(cmd))
            rc = subprocess.run(cmd).returncode
            before = len(still)
            still = [r for r in still if not find_media(AUDIO_DIR, r["id"], "mp4")]
            log(f"    退出码 {rc}；本轮补到 {before - len(still)} 条，仍缺 {len(still)} 条")
        urls_file.unlink(missing_ok=True)
        if still:
            log(f"  ⚠ 三档都试过仍缺 {len(still)} 条：" + ", ".join(f"#{r['seq']}" for r in still))
            log("    到这一步才可能是媒体真的没了 —— 此时再考虑手工加进 data/.unavailable.txt")
    elif missing:
        log("  （--skip-download：跳过）")

    # ---------- 2. 归位（修 .part → 改名）＋ 校验时长 ----------
    log("")
    log("--- 步骤 2/4 归位与校验 ---")
    for r in todo_txt:                                 # 先修 .part 残留
        part = AUDIO_DIR / f"{r['id']}.mp4.part"
        if part.exists() and not (AUDIO_DIR / f"{r['id']}.mp4").exists():
            try:
                os.replace(part, AUDIO_DIR / f"{r['id']}.mp4")
                log(f"  #{r['seq']:>3} 修复 .part 残留")
            except OSError as e:                       # noqa: BLE001
                log(f"  #{r['seq']:>3} .part 修复失败：{e}")
    renamed = normalize_names(todo_txt)
    if renamed:
        log(f"  已按「序号_标题_ID」改名 {renamed} 个")
    bad: list[dict] = []
    quality: list[dict] = []                           # 实际画质，用于台账
    ok_count = 0
    for r in todo_txt:
        mp4 = find_media(AUDIO_DIR, r["id"], "mp4")
        if not mp4 or not mp4.exists() or mp4.stat().st_size == 0:
            log(f"  #{r['seq']:>3} 缺文件，跳过（下次重跑会补下）")
            bad.append({**r, "why": "文件缺失"})
            continue
        want = parse_dur(r["dur"])
        info = ffprobe_info(mp4)                       # 一次拿到时长 + 分辨率
        got = info.get("duration", -1.0)
        tol = max(DUR_TOL_SEC, want * DUR_TOL_RATIO)
        if got > 0 and want > 0 and abs(got - want) > tol:
            log(f"  #{r['seq']:>3} 时长不符：期望 {want}s 实得 {got:.0f}s → 删除重下")
            mp4.unlink(missing_ok=True)
            bad.append({**r, "why": f"时长不符 {got:.0f}s≠{want}s"})
        else:
            ok_count += 1
            if info.get("width"):
                quality.append({"seq": r["seq"], "id": r["id"],
                                "w": info["width"], "h": info["height"],
                                "mb": mp4.stat().st_size / 1048576})
    log(f"  校验通过 {ok_count} 条，异常 {len(bad)} 条")
    if quality:
        qp = update_quality_ledger(tsv.parent, quality)
        tiers: dict[str, int] = {}
        for q in quality:
            t = tier_of(q["w"], q["h"])
            tiers[t] = tiers.get(t, 0) + 1
        log("  本批实际画质：" + "、".join(f"{k} {v} 条" for k, v in sorted(tiers.items())))
        log(f"  画质台账 -> {qp}")

    good = [r for r in todo_txt if not any(b["id"] == r["id"] for b in bad)]
    if not good:
        log("!! 没有可转写的文件")
        return 2

    # ---------- 3. 转写 ----------
    log("")
    log(f"--- 步骤 3/4 转写：{len(good)} 条 ---")
    ids_file = HERE / "_batch_ids.txt"
    ids_file.write_text("".join(r["id"] + "\n" for r in good), encoding="utf-8")
    cmd = [VENV_PY, str(HERE / "transcribe_batch.py"), str(ids_file),
           str(TRANSCRIPT_DIR), "--meta", str(tsv)]
    if args.guard:
        cmd += ["--guard", "--guard-at", args.guard_at]
    log("  $ " + " ".join(cmd))
    rc = subprocess.run(cmd).returncode
    ids_file.unlink(missing_ok=True)

    # ---------- 汇总（先写台账再做清理/合并） ----------
    rep = TRANSCRIPT_DIR / "_transcribe_report.json"
    peak, cooled = "?", 0
    if rep.exists():
        try:
            d = json.loads(rep.read_text(encoding="utf-8"))
            peak, cooled = d.get("peak_thermal", "?"), d.get("cooled_sec", 0)
        except Exception:                              # noqa: BLE001
            pass
    done_now = sum(1 for r in good
                   if (t := find_media(TRANSCRIPT_DIR, r["id"], "txt"))
                   and t.stat().st_size > 0)
    total_elapsed = time.time() - t_batch_start
    log("")
    log("=== 本批完成 ===")
    log(f"成功写入 TXT {done_now} / {len(good)} 条")
    if bad:
        log(f"异常 {len(bad)} 条：" + ", ".join(f"#{b['seq']}({b['why']})" for b in bad))
    log(f"热压峰值 {peak}  冷却累计 {cooled}s  当前 {thermal.describe()}")
    log(f"转写退出码 {rc}")
    log(f"本批总耗时 {total_elapsed / 60:.1f} 分（含下载+校验+转写）")
    try:
        append_progress(tsv.parent, f"序号 {tag_range}",
                        done_now, len(picked) - len(todo_txt), len(bad),
                        str(peak), float(cooled), total_elapsed)
        log(f"台账已追加 -> {tsv.parent / '转写进度.md'}")
    except OSError as e:                               # noqa: BLE001
        log(f"!! 台账写入失败：{e}")

    # ---------- 4. 合并 / 清理 ----------
    log("")
    log("--- 步骤 4/4 合并 / 清理 ---")
    if args.merge:
        MERGED_DIR.mkdir(parents=True, exist_ok=True)
        do_merge(picked, MERGED_DIR, tag_range)
    else:
        log("  （未加 --merge，跳过合并；要合并跑 batch_pipeline.py --merge-only "
            f"{tag_range}）")

    if args.delete_video:
        freed = removed = denied = 0
        first_denied = ""
        for r in good:
            txt = find_media(TRANSCRIPT_DIR, r["id"], "txt")
            mp4 = find_media(AUDIO_DIR, r["id"], "mp4")
            if txt and txt.stat().st_size > 0 and mp4:
                try:
                    freed += mp4.stat().st_size
                    mp4.unlink()
                    removed += 1
                except OSError as e:                   # noqa: BLE001
                    denied += 1
                    first_denied = first_denied or str(e)
        log(f"  已删除转写完成的视频 {removed} 个，释放 {freed / 1048576:.0f} MB")
        if denied:
            log(f"  ⚠ {denied} 个被系统安全阀拦下（单轮删除上限 50 个）：{first_denied}")
            log("    隔一轮再跑 batch_pipeline.py --cleanup 继续")
    else:
        log("  （默认保留 mp4；要删加 --delete-video，或事后 --cleanup）")
    return 0 if rc == 0 else 3



if __name__ == "__main__":
    raise SystemExit(main())
