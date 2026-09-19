#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单进程批量转写（带热压监控，可选自动冷却；输出带序号与标题）。

必须用 **video-subtitle 仓库的 venv python** 跑（要 mlx_whisper）：

    cd /Users/zeno/Documents/CodeProject/video-subtitle && \
    .venv/bin/python ~/.workbuddy/skills/yt-dlp-downloader/scripts/transcribe_batch.py \
        <ids.txt|单个文件> [outdir] [--meta 清单TSV] [--guard] [--guard-at serious|fair]

`<ids.txt>`：每行一个视频 id（不含扩展名）或 mp4 全路径。已存在非空 TXT 的会跳过（幂等）。

给了 `--meta`（合集清单 TSV）时：
  * 输出文件命名成 `<三位序号>_<标题>_<视频ID>.txt`（规则见 `weibo_naming.py`）
  * 每份 TXT 顶部写入表头：序号 / 标题 / 时长 / 日期 / 链接 / 转写模型
不给 `--meta` 就退回旧的 `<视频ID>.txt`、不写表头。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path("/Users/zeno/Documents/CodeProject/video-subtitle")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))     # thermal / weibo_naming

import mlx_whisper  # noqa: E402
from cli_transcribe import DEFAULT_MODEL, MODEL_MAP, resolve_local_model  # noqa: E402

import thermal  # noqa: E402
from weibo_naming import (  # noqa: E402
    find_media, header_block, load_rows, target_name, vid_of,
)

PROJ_DIR = Path("/Users/zeno/WorkBuddy/yt-dlp视频转文本")   # 2026-09-12 媒体/转写迁入项目
AUDIO_DIR = PROJ_DIR / "audio"
DEFAULT_OUTDIR = PROJ_DIR / "transcript"

# 表头里写的模型名，从实际用的路径推出来，避免写死说谎
MODEL_LABEL = MODEL_MAP[DEFAULT_MODEL].split("/")[-1] or "whisper"


# 领域提示词: 只放高频专有名词, 控制在 ~120 字
DOMAIN_PROMPT = (
    "以下是关于AI原生组织与企业AI转型的访谈内容。"
    "可能出现的术语：AI原生组织、老登公司、搓衣板思维、结果级服务、"
    "FDE前沿部署工程师、Agent智能体、数据治理、企业记忆、"
    "Anthropic、Palantir、数字化转型、商业创新。"
)

TRANSCRIBE_KW = dict(
    language="zh",
    verbose=None,
    initial_prompt=DOMAIN_PROMPT,
    condition_on_previous_text=False,   # A/B 结论: 无害, 抑制重复循环
    no_speech_threshold=0.6,
    compression_ratio_threshold=2.4,
    logprob_threshold=-1.0,
    temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
)

GUARD_LEVELS = {"fair": 1, "serious": 2}


def resolve_targets(spec: str) -> list[Path]:
    p = Path(spec).expanduser()
    if p.is_file():
        lines = p.read_text(encoding="utf-8").splitlines()
        items = [ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")]
        out: list[Path] = []
        for it in items:
            if it.endswith(".mp4"):
                out.append(Path(it))
            else:
                # 新命名 <序号>_<标题>_<ID>.mp4 优先，旧命名兜底；都没有就留个占位好报缺
                out.append(find_media(AUDIO_DIR, it, "mp4") or (AUDIO_DIR / f"{it}.mp4"))
        return out
    return [p]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="批量转写（带热压监控、输出带序号与标题）")
    ap.add_argument("spec", help="ids.txt 或单个媒体文件")
    ap.add_argument("outdir", nargs="?", default=str(DEFAULT_OUTDIR))
    ap.add_argument("--meta", metavar="清单TSV",
                    help="合集清单 TSV；给了就按「序号_标题_ID」命名并写表头")
    ap.add_argument("--guard", action="store_true",
                    help="开启自动冷却：热压达阈值就暂停，回落后继续")
    ap.add_argument("--guard-at", choices=sorted(GUARD_LEVELS), default="serious",
                    help="冷却触发阈值（默认 serious）")
    ap.add_argument("--max-cool", type=float, default=1800,
                    help="单次冷却最长等待秒数，默认 1800")
    args = ap.parse_args(argv[1:])

    targets = resolve_targets(args.spec)
    outdir = Path(args.outdir).expanduser()
    outdir.mkdir(parents=True, exist_ok=True)

    meta: dict[str, dict] = {}
    if args.meta:
        rows = load_rows(Path(args.meta).expanduser())
        meta = {r["id"]: r for r in rows}

    snapshot = resolve_local_model(MODEL_MAP[DEFAULT_MODEL])
    print(f">>> 模型: {MODEL_MAP[DEFAULT_MODEL]}")
    print(f">>> 目标: {len(targets)} 个 -> {outdir}")
    print(f">>> 元数据: {len(meta)} 条{'（写表头 + 按序号命名）' if meta else '（无，退回旧命名）'}")
    print(f">>> 热压监控: 开  自动冷却: {'开 @' + args.guard_at if args.guard else '关'}")
    print(f">>> 当前热压: {thermal.describe()}")

    missing = [t for t in targets if not t.exists()]
    if missing:
        print(f"!! 缺失 {len(missing)} 个文件, 将跳过:", file=sys.stderr)
        for m in missing:
            print(f"   {m.name}", file=sys.stderr)
    targets = [t for t in targets if t.exists()]

    threshold = GUARD_LEVELS[args.guard_at]
    records: list[dict] = []
    peak = thermal.read()
    cooled_total = 0.0
    t_start = time.time()

    for i, mp4 in enumerate(targets, 1):
        vid = vid_of(mp4)            # ⚠️ 不能拿 mp4.stem 当 ID：新命名下它是 <序号>_<标题>_<ID>
        row = meta.get(vid, {})
        seq = row.get("seq")
        dest = outdir / (target_name(seq, row.get("title", ""), vid, "txt")
                         if isinstance(seq, int) else f"{vid}.txt")

        if dest.exists() and dest.stat().st_size > 0:
            print(f"[{i}/{len(targets)}] 跳过(已存在) {dest.name}")
            records.append({"id": vid, "seq": seq, "status": "skip",
                            "chars": dest.stat().st_size})
            continue
        alt = find_media(outdir, vid, "txt")            # 别的命名也算已转写
        if alt and alt.stat().st_size > 0:
            print(f"[{i}/{len(targets)}] 跳过(已转写，名为 {alt.name})")
            records.append({"id": vid, "seq": seq, "status": "skip",
                            "chars": alt.stat().st_size})
            continue

        t0 = time.time()
        print(f"[{i}/{len(targets)}] 转写 {mp4.name} …  热压={thermal.describe()}", flush=True)
        try:
            r = mlx_whisper.transcribe(str(mp4), path_or_hf_repo=snapshot, **TRANSCRIBE_KW)
        except Exception as exc:                        # noqa: BLE001 - 单条失败不中断整批
            print(f"   !! 失败: {exc}", file=sys.stderr)
            records.append({"id": vid, "seq": seq, "status": "error", "error": str(exc)})
            continue

        segs = [str(s.get("text", "")).strip() for s in r.get("segments", [])]
        segs = [s for s in segs if s]
        body = "\n".join(segs) + ("\n" if segs else "")
        head = header_block(row, MODEL_LABEL) if isinstance(seq, int) else ""
        dest.write_text(head + body, encoding="utf-8")

        v = thermal.read()
        peak = max(peak, v)
        dt = time.time() - t0
        print(f"   ok {dt:.1f}s  段数={len(segs)}  字数={len(body)}  热压={thermal.describe(v)}"
              f"  -> {dest.name}")
        records.append({
            "id": vid, "seq": seq, "status": "ok", "sec": round(dt, 1),
            "segments": len(segs), "chars": len(body), "file": dest.name,
            "duration": float(r.get("duration") or 0),
            "language": r.get("language"), "thermal": thermal.name(v),
        })

        if args.guard:
            cooled_total += thermal.wait_cool(threshold, args.max_cool, log=print)

    total = time.time() - t_start
    print(f"\n>>> 完成: {len(targets)} 个, 总耗时 {total:.0f}s, 冷却累计 {cooled_total:.0f}s, "
          f"热压峰值 {thermal.describe(peak)}")
    (outdir / "_transcribe_report.json").write_text(
        json.dumps({"peak_thermal": thermal.name(peak), "cooled_sec": round(cooled_total),
                    "elapsed_sec": round(total), "records": records},
                   ensure_ascii=False, indent=2),
        encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
