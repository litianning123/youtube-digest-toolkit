#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
组装脚本：把抓取到的 new_videos.json 变成一份每日摘要 Markdown。

    new_videos.json --filter_videos(config.topics)--> 排序+slicing(config.max_items)
                    --> <output_dir>/<日期>-YouTube 每日摘要.md

用法：
    python3 fetch.py            # 先抓取
    python3 make_digest.py      # 用今天日期生成
    python3 make_digest.py 2026-10-14   # 指定日期
    python3 fetch.py --mark-seen        # 生成后标记已处理，避免明天重复

可选：在生成前给视频打推荐指数（v["score"] / v["score_reason"]），
摘要会自动按分数从高到低排序。评分标准见 digest.py 顶部说明。
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from digest import MAX_ITEMS, build_digest, filter_videos

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config.json"
IN = HERE / "new_videos.json"


def load_config() -> dict:
    if not CONFIG.exists():
        sys.exit("config.json 不存在：请先 cp config.example.json config.json 并填入 playlist_id")
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def main(argv: list[str]) -> int:
    digest_date = argv[1] if len(argv) > 1 else date.today().isoformat()
    config = load_config()
    if not IN.exists():
        sys.exit("new_videos.json 不存在：请先运行 python3 fetch.py")

    videos = json.loads(IN.read_text(encoding="utf-8"))
    kept = filter_videos(videos, config.get("topics"))
    cap = config.get("max_items") or MAX_ITEMS
    md = build_digest(digest_date, kept, cap)

    out_dir = HERE / config.get("output_dir", "YoutubeInbox")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{digest_date}-YouTube 每日摘要.md"
    out.write_text(md, encoding="utf-8")

    print(
        f"新增 {len(videos)} 个 → 入选 {len(kept)} 个 → 写入 {min(len(kept), cap)} 条："
        f"{out.relative_to(HERE)}"
    )
    if not kept:
        print("提示：没有视频命中 config.json 的 topics，摘要为空。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
