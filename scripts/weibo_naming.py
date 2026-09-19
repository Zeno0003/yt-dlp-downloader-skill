#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""合集素材的统一命名 / 查找 / 表头（batch_pipeline、transcribe_batch 共用）。

**这里是命名规则的唯一真源。**改命名规则只改这个文件，别在两处各写一份。

规则（2026-09-11 zeno 要求）：素材一律命名成

    <三位序号>_<标题>_<视频ID>.<ext>

序号按清单「由旧数起」补零到三位，这样在 Finder 里能按顺序排；
视频ID 保留在尾部，保证还能对回微博原始链接。
旧命名 `<视频ID>.<ext>` 仍然能识别（`find_media` 会兜底）。
"""

from __future__ import annotations

import re
from pathlib import Path

BAD_CHARS = re.compile(r'[/\\:*?"<>|\x00-\x1f]')   # 文件名里非法/危险的字符

TITLE_LIMIT = 60          # 标题片段最长多少字符（太长会撞文件名长度上限）

# TXT 表头的起止标记；merge 靠它把表头剥掉
HEAD_RULE = "─"
HEAD_PREFIX = "# "


def safe_title(title: str, limit: int = TITLE_LIMIT) -> str:
    """标题转成安全的文件名片段：去非法字符、压空白、限长。"""
    t = BAD_CHARS.sub("_", title or "")
    t = re.sub(r"\s+", " ", t).strip(" ._")
    return t[:limit].strip(" ._") or "无标题"


def target_name(seq: int, title: str, vid: str, ext: str) -> str:
    """统一命名：<三位序号>_<标题>_<视频ID>.<ext>"""
    return f"{seq:03d}_{safe_title(title)}_{vid}.{ext.lstrip('.')}"


def find_media(d: Path, vid: str, ext: str) -> Path | None:
    """按新命名找 `<序号>_<标题>_<ID>.<ext>`；找不到再退回旧的 `<ID>.<ext>`。"""
    hits = sorted(d.glob(f"*_{vid}.{ext}"))
    if not hits:
        hits = sorted(d.glob(f"{vid}.{ext}"))
    return hits[0] if hits else None


ID_TAIL = re.compile(r"_(\d{16})$")


def vid_of(path: Path) -> str:
    """从素材文件名里取回视频 ID。

    ⚠️ 别用 `path.stem` 当 ID —— 新命名下 stem 是 `<序号>_<标题>_<ID>`，
    直接拿去查元数据表会查不到（2026-09-11 实际踩过：导致表头没写、序号丢失）。
    """
    m = ID_TAIL.search(path.stem)
    return m.group(1) if m else path.stem


def load_rows(tsv: Path) -> list[dict]:
    """读合集清单 TSV（列：序号 / 站方集数 / 微博ID / mblogid / 发布时间 / 时长 / 标题 / 链接）。"""
    rows: list[dict] = []
    if not Path(tsv).exists():
        return rows
    for line in Path(tsv).read_text(encoding="utf-8").splitlines()[1:]:
        c = line.split("\t")
        if len(c) >= 8 and c[2].isdigit():
            rows.append({"seq": int(c[0]), "id": c[2], "mblogid": c[3],
                         "pub": c[4], "dur": c[5], "title": c[6], "url": c[7]})
    return rows


def parse_dur(s: str) -> int:
    """`m:ss` → 秒。"""
    try:
        m, x = s.split(":")
        return int(m) * 60 + int(x)
    except Exception:                                  # noqa: BLE001
        return 0


UNAVAIL_NAME = ".unavailable.txt"


def unavail_path(tsv: Path) -> Path:
    return Path(tsv).parent / UNAVAIL_NAME


def load_unavailable(tsv: Path) -> dict[str, str]:
    """读「永久失效」名单：`<视频ID>  <原因>` 一行一条，`#` 开头是注释。

    只手工维护，**脚本绝不自动往里写**（避免变成静默过滤）。
    用途：下载时稳定 404 的视频（如 #38）别每次占 `--next` 的名额。
    """
    p = unavail_path(tsv)
    out: dict[str, str] = {}
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split(None, 1)
        if parts[0].isdigit():
            out[parts[0]] = parts[1].strip() if len(parts) > 1 else ""
    return out


def header_block(row: dict, model: str = "whisper-large-v3-turbo") -> str:
    """每份 TXT 顶部的表头（序号 / 标题 / 时长 / 日期 / 链接）。"""
    seq, title = row.get("seq"), row.get("title", "")
    dur, pub, url = row.get("dur", ""), row.get("pub", ""), row.get("url", "")
    bar = HEAD_RULE * 58
    lines = [
        f"# {bar}",
        f"# 商业创新合集  第 {seq:03d} 条" if isinstance(seq, int) else f"# 商业创新合集  {title}",
        f"# 标题：{title}",
        f"# 时长：{dur} ｜ 发布：{pub}",
        f"# 链接：{url}",
        f"# 转写：{model}（本地离线，未经逐句人工校对，术语可能有出入）",
        f"# {bar}",
        "",
    ]
    return "\n".join(lines)


def strip_header(raw: str) -> str:
    """剥掉 `header_block` 写的表头，只留正文（给 merge 用）。"""
    lines = raw.splitlines()
    i = 0
    while i < len(lines) and (lines[i].startswith("#") or not lines[i].strip()):
        i += 1
    return "\n".join(lines[i:])
