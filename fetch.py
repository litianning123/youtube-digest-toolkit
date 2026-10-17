#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YouTube 播放列表新增视频抓取（可移植版）。

与 agent 专用版的区别：API key 从环境变量 YOUTUBE_API_KEY 读取，
不依赖任何 agent 运行时。只需 Python 3 标准库。

用法：
    export YOUTUBE_API_KEY=你的key
    cp config.example.json config.json   # 填入 playlist_id
    python3 fetch.py                     # 抓取新增 → new_videos.json
    python3 fetch.py --mark-seen         # 摘要完成后，把已处理 ID 并入 state.json
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent

API_KEY = os.environ.get("YOUTUBE_API_KEY")
if not API_KEY:
    sys.exit("请先设置环境变量 YOUTUBE_API_KEY")

BASE = "https://www.googleapis.com/youtube/v3/"
CONFIG = HERE / "config.json"
STATE = HERE / "state.json"
OUT = HERE / "new_videos.json"

DURATION_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def yt_get(path: str, params: dict, fatal: bool = True) -> dict:
    params = dict(params)
    params["key"] = API_KEY
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        body = e.read(2000).decode("utf-8", "replace")
        msg = f"YouTube API HTTP {e.code}: {body[:300]}"
        if not fatal:
            raise RuntimeError(msg)
        sys.exit(msg)


def parse_duration(iso: str) -> str:
    m = DURATION_RE.fullmatch(iso or "")
    if not m:
        return "未知"
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    if h:
        return f"{h}:{mi:02d}:{s:02d}"
    return f"{mi}:{s:02d}"


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"seen_ids": [], "last_run": None}


def fetch_playlist_items(playlist_id: str) -> list[dict]:
    items: list[dict] = []
    page_token: str | None = None
    while True:
        params = {
            "part": "snippet,contentDetails",
            "playlistId": playlist_id,
            "maxResults": "50",
        }
        if page_token:
            params["pageToken"] = page_token
        resp = yt_get("playlistItems", params)
        items.extend(resp.get("items", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return items


def fetch_video_details(video_ids: list[str]) -> dict[str, dict]:
    details: dict[str, dict] = {}
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i : i + 50]
        resp = yt_get(
            "videos",
            {"part": "snippet,contentDetails", "id": ",".join(chunk)},
        )
        for v in resp.get("items", []):
            details[v["id"]] = v
    return details


def fetch_top_comments(video_id: str, max_results: int = 8) -> list[dict]:
    """拉取按热度排序的高赞评论；评论区关闭时返回空列表。"""
    try:
        resp = yt_get(
            "commentThreads",
            {
                "part": "snippet",
                "videoId": video_id,
                "maxResults": str(max_results),
                "order": "relevance",
                "textFormat": "plainText",
            },
            fatal=False,
        )
    except RuntimeError as e:
        if "403" in str(e):  # 评论区被关闭
            return []
        raise
    comments = []
    for it in resp.get("items", []):
        s = it["snippet"]["topLevelComment"]["snippet"]
        text = s.get("textDisplay", "")
        if len(text) > 300:
            text = text[:300].rstrip() + "…"
        comments.append(
            {
                "author": s.get("authorDisplayName", ""),
                "likes": s.get("likeCount", 0),
                "text": text,
            }
        )
    return comments


def main(argv: list[str]) -> int:
    if "--mark-seen" in argv:
        if not OUT.exists():
            print("new_videos.json 不存在，无需标记")
            return 0
        new_videos = json.loads(OUT.read_text(encoding="utf-8"))
        state = load_state()
        seen = set(state.get("seen_ids", []))
        seen.update(v["video_id"] for v in new_videos)
        state["seen_ids"] = sorted(seen)
        state["last_run"] = datetime.now(timezone.utc).isoformat()
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已标记 {len(new_videos)} 个视频为已处理")
        return 0

    if not CONFIG.exists():
        sys.exit("config.json 不存在：请先 cp config.example.json config.json 并填入 playlist_id")
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    playlist_id = config.get("playlist_id")
    if not playlist_id or playlist_id.startswith("PLxxx"):
        sys.exit("请在 config.json 中填入真实的 playlist_id（播放列表 URL 中 list= 后面的部分）")

    state = load_state()
    seen = set(state.get("seen_ids", []))
    items = fetch_playlist_items(playlist_id)
    items.sort(key=lambda it: it["snippet"].get("publishedAt", ""), reverse=True)
    fresh = [it for it in items if it["snippet"]["resourceId"]["videoId"] not in seen]
    if not fresh:
        OUT.write_text("[]", encoding="utf-8")
        print("没有新增视频")
        return 0

    ids = [it["snippet"]["resourceId"]["videoId"] for it in fresh]
    details = fetch_video_details(ids)
    videos = []
    for it in fresh:
        vid = it["snippet"]["resourceId"]["videoId"]
        d = details.get(vid, {})
        snip = d.get("snippet", {})
        videos.append(
            {
                "video_id": vid,
                "title": snip.get("title", it["snippet"].get("title", "")),
                "channel": snip.get("channelTitle", ""),
                "added_at": it["snippet"].get("publishedAt", ""),
                "duration": parse_duration(d.get("contentDetails", {}).get("duration", "")),
                "description": snip.get("description", ""),
                "url": f"https://www.youtube.com/watch?v={vid}",
                "top_comments": fetch_top_comments(vid),
            }
        )
    OUT.write_text(json.dumps(videos, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"新增 {len(videos)} 个视频，已写入 new_videos.json")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
