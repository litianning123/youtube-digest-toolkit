#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
组装脚本：把抓取到的 new_videos.json 变成当天的摘要目录。

    new_videos.json --filter_videos(config.topics)--> 排序+截断(config.max_items)
                    --> <output_dir>/<日期>-YouTube 每日摘要/
                        ├── README.md      总览：排行榜 + wikilink
                        └── NN-标题.md     每个视频一篇独立文章

用法：
    python3 fetch.py            # 先抓取
    python3 make_digest.py      # 用今天日期生成
    python3 make_digest.py 2026-10-14   # 指定日期
    python3 fetch.py --mark-seen        # 生成后标记已处理，避免明天重复

可选：在生成前给视频补字段，摘要会自动渲染——
    score / score_reason（推荐指数）、key_comments（精选评论）、
    further_reading（延伸阅读）、deep_dive + deep_dive_source（精读）、
    nb_tags / nb_concepts / nb_quotes（NotebookLM 元信息）。
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from digest import (
    MAX_ITEMS,
    article_filename,
    build_article,
    build_index,
    filter_videos,
)

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

    out_dir = HERE / config.get("output_dir", "YoutubeInbox") / f"{digest_date}-YouTube 每日摘要"
    out_dir.mkdir(parents=True, exist_ok=True)
    # 同一天重跑时清掉上次生成的产物，避免残留旧文章与总览对不上
    (out_dir / "README.md").unlink(missing_ok=True)
    for stale in out_dir.glob("[0-9][0-9]-*.md"):
        stale.unlink()
    (out_dir / "README.md").write_text(
        build_index(digest_date, kept, total_new=len(videos), max_items=cap),
        encoding="utf-8",
    )

    ranked = sorted(kept, key=lambda vt: -(vt[0].get("score") or -1))[:cap]
    for i, (v, topics) in enumerate(ranked, 1):
        (out_dir / article_filename(i, v.get("title", ""))).write_text(
            build_article(digest_date, i, v, topics), encoding="utf-8"
        )

    print(
        f"新增 {len(videos)} 个 → 入选 {len(kept)} 个 → 写入 {len(ranked)} 篇："
        f"{out_dir.relative_to(HERE)}"
    )
    if not ranked:
        print("提示：没有视频命中 config.json 的 topics，只生成了空的 README.md。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
