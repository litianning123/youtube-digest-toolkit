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
    python3 make_digest.py --with-cache # 顺带把 notebooklm-cache/ 里的精读回填进摘要
    python3 fetch.py --mark-seen        # 生成后标记已处理，避免明天重复

可选：在生成前给视频补字段，摘要会自动渲染——
    score / score_reason（推荐指数）、key_comments（精选评论）、
    further_reading（延伸阅读）、deep_dive + deep_dive_source（精读）、
    nb_tags / nb_concepts / nb_quotes（NotebookLM 元信息）。
    `--with-cache` 会自动从 fetch_notebooklm.py 的输出里补后两组。

output_dir 支持绝对路径，用来直接写进 Obsidian 仓库：
    "output_dir": "/Users/me/Obsidian/Inbox"
配 "git_push": true 时，写完把该目录提交并推送。推送失败只告警不中断，
下次运行会重新推。
"""

from __future__ import annotations

import json
import re
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
from fetch_notebooklm import CACHE_DIR as NB_CACHE_DIR

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config.json"
IN = HERE / "new_videos.json"

FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.S)
DIVE_SECTION_RE = re.compile(r"\n## (?:内容标签|关键概念|金句摘录)\s*\n", re.S)


def parse_cache_note(text: str) -> dict:
    """把 fetch_notebooklm.py 生成的精读笔记拆成摘要需要的字段。

    笔记格式见 fetch_notebooklm.build_enriched_note：YAML frontmatter、
    `# 标题`、两行视频元信息、正文，最后是「内容标签 / 关键概念 / 金句摘录」
    三个可选小节。正文取到第一个小节标题为止。
    """
    fm_match = FRONTMATTER_RE.match(text)
    frontmatter = fm_match.group(0) if fm_match else ""
    body = text[fm_match.end():] if fm_match else text

    tags: list[str] = []
    for line in frontmatter.splitlines():
        if line.startswith("tags:") and "[" in line:
            raw = line.split("[", 1)[1].rstrip("]")
            tags = [t.strip() for t in raw.split(",") if t.strip()]
            break
    tags = [t for t in tags if t not in ("youtube", "精读", "每日摘要")]

    # 去掉 `# 标题` 与紧随其后的 `> 视频链接` / `> 频道` 两行
    lines = body.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and lines[0].startswith("# "):
        lines.pop(0)
    while lines and (not lines[0].strip() or lines[0].startswith(">")):
        lines.pop(0)
    body = "\n".join(lines)

    concepts: list[dict] = []
    quotes: list[str] = []
    for chunk in re.split(r"\n## ", "\n" + body)[1:]:
        head, _, rest = chunk.partition("\n")
        head = head.strip()
        if head == "内容标签":
            tags += [
                t.strip().lstrip("#")
                for t in rest.replace("\n", " ").split()
                if t.strip().lstrip("#")
            ]
        elif head == "关键概念":
            for line in rest.splitlines():
                m = re.match(r"\s*-\s+\*\*(.+?)\*\*\s*(?:[：:]\s*(.*))?$", line)
                if m:
                    concepts.append(
                        {"name": m.group(1).strip(), "desc": (m.group(2) or "").strip()}
                    )
        elif head == "金句摘录":
            for line in rest.splitlines():
                q = line.strip().lstrip(">").strip().strip("\"'“”‘’").strip()
                if q:
                    quotes.append(q)
    body = DIVE_SECTION_RE.split(body, maxsplit=1)[0].strip()

    out: dict = {"deep_dive": body, "deep_dive_source": "notebooklm"}
    if tags:
        out["nb_tags"] = list(dict.fromkeys(tags))
    if concepts:
        out["nb_concepts"] = concepts
    if quotes:
        out["nb_quotes"] = quotes
    return out



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
    positional = [a for a in argv[1:] if not a.startswith("-")]
    with_cache = "--with-cache" in argv
    digest_date = positional[0] if positional else date.today().isoformat()
    config = load_config()
    if not IN.exists():
        sys.exit("new_videos.json 不存在：请先运行 python3 fetch.py")

    videos = json.loads(IN.read_text(encoding="utf-8"))
    if with_cache:
        filled = 0
        for v in videos:
            vid = v.get("video_id", "")
            note = NB_CACHE_DIR / f"{vid}.md"
            if not vid or not note.is_file():
                continue
            data = parse_cache_note(note.read_text(encoding="utf-8"))
            if data.get("deep_dive"):
                v.update(data)
                filled += 1
        print(f"从 notebooklm-cache 回填 {filled} 个视频的精读。")
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
