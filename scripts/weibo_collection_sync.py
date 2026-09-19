#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""微博「合集」条目增量同步。

把某个微博作者某个合集（默认 老马自奋蹄 / 商业创新）的**条目清单**抓下来，
首次全量、之后增量，并**原地重写同一份产物**（单一真源）。

用法：
    PY=/Users/zeno/.workbuddy/binaries/python/versions/3.13.12/bin/python3
    $PY weibo_collection_sync.py                    # 增量（首次自动全量）
    $PY weibo_collection_sync.py --full             # 强制全量翻页
    $PY weibo_collection_sync.py --site-total 471   # 更新站方标注集数
    $PY weibo_collection_sync.py --head-len 41      # 更新头部锚定长度
    $PY weibo_collection_sync.py --dry-run          # 只抓不写，打印摘要

退出码：
    0  成功
    2  网络失败（握手 / 翻页）——产物与状态零改动
    3  数据异常（抓 0 条 / 全量模式下比上轮少）——拒绝写入
    4  参数或环境错误

依赖：仅 Python 标准库。详见同目录 README.md。
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import random
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

# ---------------------------------------------------------------- 常量

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

DEFAULT_UID = "1807436544"
DEFAULT_T_ID = "5084258226602032"
DEFAULT_T_NAME = "商业创新"
DEFAULT_SITE_TOTAL = 471
DEFAULT_HEAD_LEN = 41
DEFAULT_OUTDIR = "/Users/zeno/WorkBuddy/yt-dlp视频转文本/data"

LIST_API = "https://weibo.com/ajax/profile/getWaterFallContent"
REFERER = "https://weibo.com/"

PAGE_SIZE = 20            # 站方每页条数（实测），用于推导尾部锚定长度
INCR_MAX_PAGES = 15       # 增量最多翻几页，触上限说明积压过多、改走全量
INCR_EMPTY_PAGES = 3      # 连续多少页一条合集条目都没命中就停（兜底）
SLEEP_BETWEEN_PAGES = 0.6
RETRY = 3
RETRY_WAIT = 2

TSV_NAME = "商业创新合集_条目.tsv"
MD_NAME = "商业创新合集_条目.md"
URLS_NAME = "商业创新合集_urls.txt"
STATE_NAME = ".sync_state.json"
IDS_NAME = ".known_ids.txt"


def log(msg: str = "") -> None:
    print(msg, flush=True)


def die(code: int, msg: str) -> None:
    log(f"[退出码 {code}] {msg}")
    sys.exit(code)


def now_local() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fmt_dt(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


def fmt_dur(sec) -> str:
    try:
        s = int(sec)
    except Exception:
        return ""
    return f"{s // 60}:{s % 60:02d}"


def atomic_write(path: str, text: str) -> None:
    """先写临时文件再 os.replace，杜绝半截文件。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def read_text(path: str) -> str | None:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def read_json(path: str) -> dict:
    text = read_text(path)
    return json.loads(text) if text else {}


# ---------------------------------------------------------------- 网络层

class Weibo:
    """访客态抓取器：握手一次拿 guest cookie，之后复用。"""

    def __init__(self) -> None:
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))
        self.ready = False

    def _open(self, url: str, data: bytes | None = None,
              headers: dict | None = None) -> str:
        h = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}
        h.update(headers or {})
        req = urllib.request.Request(url, data=data, headers=h)
        return self.opener.open(req, timeout=30).read().decode("utf-8", "replace")

    @staticmethod
    def _strip_jsonp(s: str) -> str:
        return re.sub(r"^[^{]*\(", "", s).rstrip(");\n ")

    def handshake(self) -> None:
        """照抄 yt-dlp WeiboBaseIE._update_visitor_cookies。"""
        fp = json.dumps({"os": "1", "browser": "Chrome122,0,0,0", "fonts": "undefined",
                         "screenInfo": "1920*1080*24", "plugins": ""},
                        separators=(",", ":"))
        raw = self._open("https://passport.weibo.com/visitor/genvisitor",
                         data=urllib.parse.urlencode(
                             {"cb": "gen_callback", "fp": fp}).encode(),
                         headers={"Referer": REFERER})
        data = json.loads(self._strip_jsonp(raw))["data"]
        query = urllib.parse.urlencode({
            "a": "incarnate", "t": data["tid"],
            "w": 3 if data.get("new_tid") else 2,
            "c": f"{data.get('confidence', 100):03d}",
            "gc": "", "cb": "cross_domain", "from": "weibo",
            "_rand": random.random(),
        })
        self._open("https://passport.weibo.com/visitor/visitor?" + query,
                   headers={"Referer": REFERER})
        self.ready = True

    def fetch_page(self, uid: str, cursor: str) -> dict:
        """取一页；失败重试（每次重试都重新握手）。"""
        url = LIST_API + "?" + urllib.parse.urlencode({"uid": uid, "cursor": cursor})
        last_err = None
        for attempt in range(1, RETRY + 1):
            try:
                if not self.ready:
                    self.handshake()
                payload = json.loads(self._open(url, headers={"Referer": REFERER}))
                data = payload.get("data") or {}
                if not isinstance(data, dict):
                    raise ValueError(f"data 结构异常：{type(data).__name__}")
                return data
            except Exception as exc:                       # noqa: BLE001
                last_err = exc
                self.ready = False
                log(f"    第 {attempt}/{RETRY} 次取页失败：{exc}")
                if attempt < RETRY:
                    time.sleep(RETRY_WAIT)
        raise RuntimeError(f"翻页失败（cursor={cursor}）：{last_err}")


# ---------------------------------------------------------------- 解析层

RE_TID = re.compile(r"t_id:(\d+)\|t_name:([^|]*)")


def match_level(item: dict, t_id: str, t_name: str) -> tuple[int, str]:
    """逐条打标，返回 (level, source)。

    level 1 = 最强判据（合集 id），2 = 话题，3 = 正文话题字面量，0 = 不匹配。

    ⚠ 2/3 是**整体降级**用的，不是逐条兜底：它们会把「打了同一话题但不属于该
    合集」的内容也算进来（同一份数据实测：强判据 469 条 / 话题判据 488 条 /
    正文判据 526 条）。所以只有当 level 1 **整体失效（0 命中）**时才启用。
    """
    page = item.get("page_info") or {}
    ext = ((page.get("actionlog") or {}).get("ext") or "")
    m = RE_TID.search(ext)
    if m:
        if m.group(1) == t_id:
            return 1, "actionlog_ext"
        if urllib.parse.unquote(m.group(2)) == t_name:
            return 1, "actionlog_ext_name"
    for topic in (item.get("topic_struct") or []):
        if (topic or {}).get("topic_title") == t_name:
            return 2, "topic_struct"
    if f"#{t_name}#" in (item.get("text_raw") or ""):
        return 3, "text_raw"
    return 0, ""


def parse_item(item: dict, source: str) -> dict | None:
    page = item.get("page_info") or {}
    media = page.get("media_info") or {}
    if not media:                       # 图文等非视频条目
        return None
    mid = str(item.get("id") or item.get("mid") or "")
    if not mid.isdigit():
        return None
    return {
        "id": mid,
        "mblogid": item.get("mblogid") or "",
        "ts": media.get("video_publish_time") or "",
        "duration": media.get("duration") or "",
        "title": (media.get("video_title") or media.get("kol_title")
                  or media.get("name") or "").replace("\n", " ").strip(),
        "match": source,
    }


def load_prev_rows(tsv_path: str) -> dict[str, dict]:
    """把上一轮 TSV 的条目原样读回来（用于增量模式下的 carry-over）。"""
    text = read_text(tsv_path)
    out: dict[str, dict] = {}
    if not text:
        return out
    for line in text.splitlines()[1:]:
        cols = line.rstrip("\n").split("\t")
        if len(cols) < 8 or not cols[2].isdigit():
            continue
        out[cols[2]] = {
            "id": cols[2], "mblogid": cols[3], "ts": "", "duration": "",
            "title": cols[6], "match": "carried_over",
            "_dt": cols[4], "_dur": cols[5],
        }
    return out


def order_rows(records: list[dict], uid: str, prev_order: list[str]) -> list[dict]:
    """去重 → 由旧到新排序 → 编号。

    排序键：微博 id（数值）。若某条缺 id 或 id 与上一轮顺序冲突，退回上一轮顺序，
    保证「同一批数据永远排出同一个顺序」。
    """
    uniq: dict[str, dict] = {}
    for r in records:
        uniq.setdefault(r["id"], r)

    # 主排序：id 升序（= 由旧到新）
    ordered = sorted(uniq.values(), key=lambda r: int(r["id"]))

    # 稳定性校验：若上一轮顺序里这两条都有，且相对次序被翻转，则整体沿用上一轮顺序
    if prev_order:
        pos = {mid: i for i, mid in enumerate(prev_order)}
        pairs = [(pos[a["id"]], pos[b["id"]])
                 for a, b in zip(ordered, ordered[1:]) if a["id"] in pos and b["id"] in pos]
        if any(x > y for x, y in pairs):
            ordered = sorted(ordered, key=lambda r: pos.get(r["id"], 10 ** 9))

    rows = []
    for i, r in enumerate(ordered, 1):
        mid = r["mblogid"]
        url = (f"https://weibo.com/{uid}/{mid}" if mid
               else f"https://weibo.com/{uid}/{r['id']}")
        rows.append({**r, "seq": i, "url": url})
    return rows


def compute_anchors(n: int, site_total: int, head_len: int) -> dict:
    return {
        "head_len": head_len,
        "tail_len": ((site_total - 1) % PAGE_SIZE) + 1,
        "gap": site_total - n,
    }


def episode_no(seq: int, n: int, anc: dict) -> str:
    if anc["gap"] == 0:
        return str(seq)                       # 无缺口 → 全表连续编号
    if seq <= anc["head_len"]:
        return str(seq)
    if seq >= n - anc["tail_len"] + 1:
        return str(seq + anc["gap"])
    return "—"                                # 中段不猜


# ---------------------------------------------------------------- 产物渲染

def render(rows: list[dict], anc: dict, meta: dict) -> dict[str, str]:
    uid, n = meta["uid"], len(rows)
    dt_of = lambda r: r.get("_dt") or fmt_dt(r["ts"])          # noqa: E731
    dur_of = lambda r: r.get("_dur") or fmt_dur(r["duration"])  # noqa: E731

    tsv = ["序号_由旧数起\t站方集数_已锚定段\t微博ID\tmblogid\t发布时间\t时长\t标题\t链接"]
    for r in rows:
        tsv.append("\t".join([
            str(r["seq"]), episode_no(r["seq"], n, anc), r["id"], r["mblogid"],
            dt_of(r), dur_of(r), r["title"].replace("\t", " "), r["url"],
        ]))
    grid = "".join(line + "\n" for line in tsv)

    if anc["gap"] > 0:
        gap_note = f"站方标注 {meta['site_total']} 集，实抓 {n} 条，**缺口 {anc['gap']} 条**"
    elif anc["gap"] == 0:
        gap_note = f"站方标注 {meta['site_total']} 集，实抓 {n} 条，**无缺口**"
    else:
        gap_note = (f"站方标注 {meta['site_total']} 集，实抓 {n} 条，"
                    f"实抓比标注多 {-anc['gap']} 条（疑重复发布或站方计数滞后）")

    md = [
        f"# 老马自奋蹄 · 「{meta['t_name']}」合集 视频条目",
        "",
        f"- 来源：微博 uid `{uid}` 官方视频列表接口 "
        f"`weibo.com/ajax/profile/getWaterFallContent`",
        f"- 合集标识：`t_id={meta['t_id']}` / `t_name={meta['t_name']}`",
        f"- 共 **{n} 条**；{gap_note}",
        f"- 集数列只填**可锚定段**：头部 1–{anc['head_len']}、尾部 "
        f"{n - anc['tail_len'] + 1}–{n}（偏移 {anc['gap']:+d}），中段一律 `—`，不猜",
        "- 排序：合集顺序（由旧到新）；排序键只用微博 id。发布时间不可信"
        "（同一批常同一天发出），仅供参考",
        "- 本文件由 `weibo_collection_sync.py` 自动生成并原地重写；"
        "最后同步时间见同目录 `.sync_state.json`",
        "",
        "| 序号 | 站方集数 | 发布时间 | 时长 | 标题 | 链接 |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        label = r["mblogid"] or r["id"]
        md.append(f"| {r['seq']} | {episode_no(r['seq'], n, anc)} | "
                  f"{dt_of(r)[:10]} | {dur_of(r)} | "
                  f"{r['title'].replace('|', '｜')} | [{label}]({r['url']}) |")
    return {
        "tsv": grid,
        "md": "\n".join(md) + "\n",
        "urls": "".join(r["url"] + "\n" for r in rows),
    }


# ---------------------------------------------------------------- 抓取

def crawl(wb: Weibo, uid: str, t_id: str, t_name: str,
          known_ids: set[str], full: bool, depth: int = 0):
    """返回 (candidates, pages, scanned, exit_reason)。

    candidates 是 `(level, source, item)` 三元组列表，**尚未按判据定级**——
    定级在 main 里做（避免逐条兜底把「同话题非本合集」的内容混进来）。

    增量退出条件（列表是 id 降序，新条目必在最前）：
      主：本页命中合集的条目里有 >=2 条是已知道的 → 已翻进旧区间，停
      兜底：连续 3 页一条合集条目都没命中 → 停
      上限：最多 INCR_MAX_PAGES 页；触上限说明积压太久 → 转全量
    ⚠ 不要用「翻到已知最旧那条」当判据 —— 它在列表最底部，等于没省时间。
    """
    records: list[tuple] = []
    cursor, pages, scanned, empty_streak = "0", 0, 0, 0
    exit_reason = "unknown"

    while True:
        pages += 1
        data = wb.fetch_page(uid, cursor)
        batch = data.get("list") or []
        if not batch:
            exit_reason = "no_more_items"
            break
        scanned += len(batch)

        hit = known_hits = new_hits = 0
        for item in batch:
            level, source = match_level(item, t_id, t_name)
            if not level:
                continue
            hit += 1
            if str(item.get("id") or "") in known_ids:
                known_hits += 1
            else:
                new_hits += 1
            records.append((level, source, item))
        log(f"  第 {pages:>2} 页：{len(batch)} 条，合集 {hit} 条"
            f"（新 {new_hits} / 已知 {known_hits}），累计候选 {len(records)} 条")

        next_cursor = str(data.get("next_cursor") or "")
        if not next_cursor or next_cursor == cursor or next_cursor == "0":
            exit_reason = "no_more_items"
            break

        if not full:
            if known_ids and known_hits >= 2:
                exit_reason = "hit_known_id"
                break
            empty_streak = empty_streak + 1 if hit == 0 else 0
            if empty_streak >= INCR_EMPTY_PAGES:
                exit_reason = "no_hit_pages"
                break
            if pages >= INCR_MAX_PAGES:
                if depth == 0:
                    log(f"  增量扫描达上限 {INCR_MAX_PAGES} 页（积压较多），转全量扫描")
                    return crawl(wb, uid, t_id, t_name, known_ids, True, depth + 1)
                exit_reason = "max_pages"
                break

        cursor = next_cursor
        time.sleep(SLEEP_BETWEEN_PAGES)

    return records, pages, scanned, exit_reason


# ---------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(description="微博合集条目增量同步")
    ap.add_argument("--uid", default=None)
    ap.add_argument("--t-id", default=None)
    ap.add_argument("--t-name", default=None)
    ap.add_argument("--outdir", default=DEFAULT_OUTDIR)
    ap.add_argument("--site-total", type=int, default=None,
                    help="站方页面上标注的合集总集数（公开接口取不到，只能人工提供）")
    ap.add_argument("--head-len", type=int, default=None,
                    help="头部可锚定长度：序号<=该值 时 集数=序号")
    ap.add_argument("--full", action="store_true", help="强制全量翻页")
    ap.add_argument("--allow-shrink", action="store_true",
                    help="全量模式下允许比上轮少（作者删视频时才用）")
    ap.add_argument("--dry-run", action="store_true", help="只抓不写")
    args = ap.parse_args()

    outdir = os.path.expanduser(args.outdir)
    if not os.path.isdir(outdir):
        die(4, f"输出目录不存在：{outdir}")

    state_path = os.path.join(outdir, STATE_NAME)
    ids_path = os.path.join(outdir, IDS_NAME)
    tsv_path = os.path.join(outdir, TSV_NAME)
    state = read_json(state_path)

    uid = args.uid or state.get("uid") or DEFAULT_UID
    coll = state.get("collection") or {}
    t_id = args.t_id or coll.get("t_id") or DEFAULT_T_ID
    t_name = args.t_name or coll.get("t_name") or DEFAULT_T_NAME
    site_total = (args.site_total if args.site_total is not None
                  else state.get("site_episode_total") or DEFAULT_SITE_TOTAL)
    head_len = (args.head_len if args.head_len is not None
                else (state.get("anchors") or {}).get("head_len") or DEFAULT_HEAD_LEN)

    known_text = read_text(ids_path) or ""
    known_ids = {ln.strip() for ln in known_text.splitlines() if ln.strip()}
    prev_rows = load_prev_rows(tsv_path)
    prev_order = [r["id"] for r in
                  sorted(prev_rows.values(), key=lambda x: int(x["id"]))]
    first_run = not known_ids
    full = args.full or first_run
    prev_site = state.get("site_episode_total")

    log(f"=== 商业创新合集 同步开始 {now_local()} ===")
    log(f"uid={uid}  t_id={t_id}  t_name={t_name}  "
        f"模式={'全量' if full else '增量'}  已知 {len(known_ids)} 条  "
        f"站方标注 {site_total} 集")

    wb = Weibo()
    try:
        records, pages, scanned, why = crawl(
            wb, uid, t_id, t_name, known_ids, full)
    except Exception as exc:                            # noqa: BLE001
        die(2, f"网络失败，产物与状态零改动：{exc}")

    # ---- 判据分级：优先强判据；只有它整体失效（0 命中）时才降级 ----
    by_level: dict[int, list] = {}
    for level, source, item in records:
        by_level.setdefault(level, []).append((source, item))
    counts = {lv: len(v) for lv, v in by_level.items()}
    if not by_level:
        die(3, "本轮三条判据全部 0 命中（接口改版，或合集被删/改名？）"
               "——拒绝写入以保护现有清单")
    chosen = min(by_level)
    if chosen > 1:
        log(f"⚠ 强判据 actionlog.ext 的 t_id 0 命中，已整体降级到 "
            f"{'topic_struct' if chosen == 2 else 'text_raw'}")
        log(f"  降级判据会把「同话题但不属于该合集」的内容算进来，请人工核对")
    picked = by_level[chosen]
    if counts.get(1) and len(picked) != counts[1]:
        log(f"  （参考口径：强判据 {counts.get(1, 0)} 条 / "
            f"话题 {counts.get(2, 0)} 条 / 正文 {counts.get(3, 0)} 条）")
    parsed = [r for r in (parse_item(item, source) for source, item in picked) if r]
    if not parsed:
        die(3, f"命中的 {len(picked)} 条里没有一条是视频（全是图文？），拒绝写入")

    crawled_rows = order_rows(parsed, uid, prev_order)
    crawled_ids = {r["id"] for r in crawled_rows}

    # 增量模式只翻了前几页，必须把没翻到的旧条目原样带过来
    by_id = {r["id"]: r for r in crawled_rows}
    if not full:
        for mid, r in prev_rows.items():
            by_id.setdefault(mid, r)
    # 全量模式：上一轮有、本轮没有的，才是真的消失
    gone_ids = sorted(known_ids - crawled_ids) if full else []

    if gone_ids and not args.allow_shrink:
        log(f"⚠ 全量扫描发现 {len(gone_ids)} 条消失：{gone_ids[:5]}")
        die(3, "拒绝写入。确认是作者删除后，可加 --allow-shrink 重跑")
    if gone_ids:
        log(f"⚠ 已按 --allow-shrink 接受 {len(gone_ids)} 条消失：{gone_ids[:5]}")

    rowset = order_rows(list(by_id.values()), uid, prev_order)
    merged_ids = {r["id"] for r in rowset}
    new_ids = sorted(crawled_ids - known_ids, key=int, reverse=True)

    anc = compute_anchors(len(rowset), site_total, head_len)
    meta = {"uid": uid, "t_id": t_id, "t_name": t_name, "site_total": site_total}

    if anc["gap"] < 0:
        log(f"⚠ gap={anc['gap']}：实抓比站方标注多，集数列中段保持 —，请人工核对")
    if anc["head_len"] + anc["tail_len"] > len(rowset):
        log("⚠ 头部锚定段与尾部锚定段重叠，集数列可能有误，请人工核对")

    out = render(rowset, anc, meta)
    old = {name: read_text(os.path.join(outdir, name))
           for name in (TSV_NAME, MD_NAME, URLS_NAME)}
    changed = any(old[name] != out[key] for name, key in
                  ((TSV_NAME, "tsv"), (MD_NAME, "md"), (URLS_NAME, "urls")))
    ids_text = "".join(i + "\n" for i in sorted(merged_ids, key=int, reverse=True))
    ids_changed = ids_text != known_text

    if not args.dry_run:
        if changed:
            atomic_write(tsv_path, out["tsv"])
            atomic_write(os.path.join(outdir, MD_NAME), out["md"])
            atomic_write(os.path.join(outdir, URLS_NAME), out["urls"])
        if ids_changed:
            atomic_write(ids_path, ids_text)
        atomic_write(state_path, json.dumps({
            "schema_version": 1,
            "uid": uid,
            "collection": {"t_id": t_id, "t_name": t_name, "belong_collection": 1},
            "last_run_utc": now_utc_iso(),
            "last_pages_scanned": pages,
            "exit_reason": why,
            "mode": "full" if full else "incremental",
            "known_count": len(merged_ids),
            "known_ids_file": IDS_NAME,
            "newest_id": rowset[-1]["id"] if rowset else None,
            "oldest_id": rowset[0]["id"] if rowset else None,
            "total_scanned": scanned,
            "site_episode_total": site_total,
            "anchors": anc,
            "match_source": sorted({r["match"] for r in rowset}),
            "artifacts": {"tsv": TSV_NAME, "md": MD_NAME, "urls": URLS_NAME},
        }, ensure_ascii=False, indent=2) + "\n")

    # ---------------- 摘要 ----------------
    n = len(rowset)
    log("")
    log(f"=== 同步报告 {now_local()} ===")
    log(f"扫描 {pages} 页 / {scanned} 条，退出原因：{why}")
    log(f"合集条数  {len(known_ids) if not first_run else 0} → {n}  （新增 {len(new_ids)} 条）")
    if new_ids:
        log("新增：")
        for r in rowset:
            if r["id"] in new_ids:
                label = r.get("_dt") or fmt_dt(r["ts"])
                dur = r.get("_dur") or fmt_dur(r["duration"])
                log(f"  · {label}  {dur:>6}  {r['title'][:50]}  {r['url']}")
    else:
        log("无新增内容。")
    log(f"集数锚定  头部 1–{anc['head_len']}；尾部 "
        f"{n - anc['tail_len'] + 1}–{n}（偏移 {anc['gap']:+d}）；中段留 —")
    if prev_site is not None and prev_site != site_total:
        log(f"⚠ 站方标注集数 {prev_site} → {site_total}，锚定列已重算")
    if anc["gap"] == 0:
        log("✅ 缺口已闭合（实抓 == 站方标注），集数列对全部条目有效")
    elif anc["gap"] > 0:
        log(f"⚠ 仍有 {anc['gap']} 条缺口。公开接口只覆盖「视频」标签页，"
            f"若那几条是转发/图文/已删，可能永远补不上")
    if args.dry_run:
        log("（--dry-run：未写任何文件）")
    elif changed:
        log(f"产物已原地更新：{TSV_NAME} / {MD_NAME} / {URLS_NAME}")
    else:
        log("产物无变化，未改写（md5 保持不变）")
    log("下次转写命令：")
    log(f'  yt-dlp_macos -S "res:540" -a "{os.path.join(outdir, URLS_NAME)}"')


if __name__ == "__main__":
    main()
