#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
组装脚本：把抓取到的 new_videos.json 变成当天的摘要目录。

    new_videos.json --filter_videos(config.topics)--> 排序+截断(config.max_items)
                    --> <output_dir>/<日期>-YouTube 每日摘要/
                        ├── README.md      总览：排行榜 + wikilink
                        └── NN-标题.md     每个视频一篇独立文章
                    --> （config.git_push 为真时）commit + push 到 output_dir 所属仓库

用法：
    python3 fetch.py            # 先抓取
    python3 make_digest.py      # 用今天日期生成
    python3 make_digest.py 2026-10-14   # 指定日期
    python3 fetch.py --mark-seen        # 生成后标记已处理，避免明天重复

可选：在生成前给视频补字段，摘要会自动渲染——
    score / score_reason（推荐指数）、key_comments（精选评论）、
    further_reading（延伸阅读）、deep_dive + deep_dive_source（精读）、
    nb_tags / nb_concepts / nb_quotes（NotebookLM 元信息）。

output_dir 支持绝对路径，用来直接写进 Obsidian 仓库：
    "output_dir": "/Users/me/Obsidian/Inbox"
配 "git_push": true 时，写完把该目录提交并推送。推送失败只告警不中断，
下次运行会重新推。
"""

from __future__ import annotations

import json
import subprocess
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


def resolve_output_base(raw: str) -> Path:
    """output_dir 支持相对（相对本工具包）或绝对（如 Obsidian 仓库里的目录）路径。"""
    p = Path(raw).expanduser()
    return p if p.is_absolute() else HERE / p


def display(path: Path) -> str:
    try:
        return str(path.relative_to(HERE))
    except ValueError:
        return str(path)


def git_publish(out_dir: Path, message: str) -> None:
    """把 out_dir 提交并推送到它所属的 git 仓库。

    发布失败不影响已经生成的摘要：只往 stderr 告警，退出码仍为 0。
    没有变更时跳过提交，但仍 push 一次，把上次没推成功的提交带上去。
    """
    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(out_dir), *args], capture_output=True, text=True
        )

    top = git("rev-parse", "--show-toplevel")
    if top.returncode != 0:
        print(
            f"警告：output_dir 不在 git 仓库里，跳过提交推送：{out_dir}",
            file=sys.stderr,
        )
        return
    root = top.stdout.strip()

    if git("add", "--", str(out_dir)).returncode != 0:
        print(f"警告：git add 失败：{root}", file=sys.stderr)
        return
    staged = subprocess.run(
        ["git", "-C", root, "diff", "--cached", "--quiet", "--", str(out_dir)]
    ).returncode
    if staged != 0:
        commit = subprocess.run(
            ["git", "-C", root, "commit", "-q", "-m", message],
            capture_output=True,
            text=True,
        )
        if commit.returncode != 0:
            print(
                f"警告：commit 失败（可能没配 user.name/user.email）："
                f"{(commit.stderr or commit.stdout).strip()[:300]}",
                file=sys.stderr,
            )
            return
        print(f"已提交：{message}")
    else:
        print("无变更，跳过提交。")

    push = subprocess.run(["git", "-C", root, "push"], capture_output=True, text=True)
    if push.returncode != 0:
        print(
            f"警告：push 失败（下次运行会重试）：{(push.stderr or push.stdout).strip()[:300]}",
            file=sys.stderr,
        )
    else:
        print("已推送。")


def main(argv: list[str]) -> int:
    digest_date = argv[1] if len(argv) > 1 else date.today().isoformat()
    config = load_config()
    if not IN.exists():
        sys.exit("new_videos.json 不存在：请先运行 python3 fetch.py")

    videos = json.loads(IN.read_text(encoding="utf-8"))
    kept = filter_videos(videos, config.get("topics"))
    cap = config.get("max_items") or MAX_ITEMS

    out_dir = (
        resolve_output_base(config.get("output_dir", "YoutubeInbox"))
        / f"{digest_date}-YouTube 每日摘要"
    )
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
        f"{display(out_dir)}"
    )
    if not ranked:
        print("提示：没有视频命中 config.json 的 topics，只生成了空的 README.md。")
    if config.get("git_push"):
        git_publish(out_dir, f"每日摘要：{digest_date}（{len(ranked)} 篇）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
