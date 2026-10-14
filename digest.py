#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YouTube 每日摘要 — Markdown 生成器。

与抓取逻辑解耦（可离线测试）：
    输入：视频列表（顺序不限，生成器会按推荐指数重排），每个元素为 dict：
        video_id, title, channel, added_at (ISO 8601), duration (如 "12:34"),
        description, url, score (可选，1-100 推荐指数), score_reason (可选，一句话理由)
    输出：Obsidian Inbox 风格的 Markdown 文本（含 frontmatter），按推荐指数从高到低排序。

抓取（playlistItems.list / videos.list）见 fetch_playlist.py。

推荐指数评分说明（供定时任务 worker 打分参考，百分制）：
    - 信息密度与深度（40 分）：是否讲透一个问题，有无数据/案例/框架；标题党、泛泛而谈扣分。
    - 新颖度与时效性（25 分）：是否有新信息、新观点、新工具；老调重弹扣分。
    - 可操作性（20 分）：看完能否行动（策略可执行、工具可上手）。
    - 与用户兴趣的匹配度（15 分）：金融/理财/AI/科技四类内的相关程度。
    参考档位：90+ 必看；75-89 值得花时间看；60-74 看摘要即可；60 以下可跳过。
    注意：评分只能基于标题、简介与章节信息做初步判断，须在摘要中注明"仅供参考"。
"""

from __future__ import annotations

import re

# 用户要求：每天最多 10 个
MAX_ITEMS = 10

FINANCE_KEYWORDS = [
    "金融", "理财", "投资", "股票", "基金", "债券", "etf", "指数基金",
    "退休", "401k", "ira", "roth", "保险", "税务", "报税", "信用卡",
    "贷款", "房贷", "财务", "资产", "财富", "存钱", "省钱", "美股",
    "港股", "加密货币", "比特币", "以太坊", "通胀", "利率", "美联储",
    "降息", "加息", "finance", "investing", "investment", "stock",
    "retirement", "credit card", "mortgage", "tax", "insurance",
    "wealth", "personal finance", "money",
]

AI_TECH_KEYWORDS = [
    "ai", "人工智能", "机器学习", "深度学习", "大模型", "llm",
    "gpt", "claude", "gemini", "obsidian", "科技", "芯片", "半导体",
    "英伟达", "nvidia", "苹果", "特斯拉", "机器人", "自动驾驶",
    "编程", "代码", "软件", "数码",
    "technology", "tech", "artificial intelligence", "machine learning",
    "openai", "semiconductor", "robot",
]

TOPIC_LABEL = {"finance": "金融/理财", "ai_tech": "AI/科技"}

# 章节行，如 "00:00 开场" / "12:34 - 主题" / "(1:02:03) 总结"
CHAPTER_RE = re.compile(
    r"(?m)^\s*\(?(\d{1,2}:\d{2}(?::\d{2})?)\)?\s*[-–—:：.]?\s*(.{1,80}?)\s*$"
)
URL_RE = re.compile(r"https?://\S+")


def _contains(text: str, keyword: str) -> bool:
    # 英文短词用词边界，避免 "ai" 误命中 "said" 之类
    kw = keyword.lower()
    if re.fullmatch(r"[a-z0-9+#.]+", kw):
        return re.search(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])", text) is not None
    return kw in text


def classify(title: str, description: str) -> list[str]:
    """判断视频主题，返回命中的主题 key 列表（可为空）。"""
    text = f"{title}\n{description}".lower()
    topics = []
    if any(_contains(text, kw) for kw in FINANCE_KEYWORDS):
        topics.append("finance")
    if any(_contains(text, kw) for kw in AI_TECH_KEYWORDS):
        topics.append("ai_tech")
    return topics


def parse_chapters(description: str, limit: int = 12) -> list[tuple[str, str]]:
    """从简介中提取章节（时间戳 + 标题）。"""
    chapters = []
    for m in CHAPTER_RE.finditer(description or ""):
        ts, title = m.group(1), m.group(2).strip()
        if title and not URL_RE.search(title):
            chapters.append((ts, title))
        if len(chapters) >= limit:
            break
    return chapters


def outline_summary(description: str, max_chars: int = 500) -> str:
    """简介摘录：去 URL、去多余空行，截断。"""
    text = URL_RE.sub("", description or "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "……"
    return text


def filter_videos(videos: list[dict]) -> list[tuple[dict, list[str]]]:
    """只保留金融/理财/AI/科技类，返回 (video, topics)。保持原有顺序。"""
    kept = []
    for v in videos:
        topics = classify(v.get("title", ""), v.get("description", ""))
        if topics:
            kept.append((v, topics))
    return kept


def build_digest(digest_date: str, videos: list[tuple[dict, list[str]]]) -> str:
    """生成整份每日摘要 Markdown。按推荐指数从高到低排序；无评分的沉底并保持原序。"""
    videos = sorted(videos, key=lambda vt: -(vt[0].get("score") or -1))
    videos = videos[:MAX_ITEMS]
    lines = [
        "---",
        f"date: {digest_date}",
        "source: youtube-daily-digest",
        "tags: [youtube, 每日摘要]",
        "---",
        "",
        f"# YouTube 每日摘要（{digest_date}）",
        "",
        f"今日新增符合主题的视频 {len(videos)} 个（金融/理财、AI/科技；超过 10 个取最新 10 个），已按推荐指数从高到低排序。",
        "",
        "> 注：本摘要为大纲版，基于视频标题、简介与章节信息整理，非全文精读；推荐指数为初步评分，仅供参考。",
        "",
    ]
    for i, (v, topics) in enumerate(videos, 1):
        labels = "、".join(TOPIC_LABEL[t] for t in topics)
        lines += [
            f"## {i}. {v.get('title', '(无标题)')}",
            "",
            f"- 频道：{v.get('channel', '未知')}｜时长：{v.get('duration', '未知')}｜"
            f"[在 YouTube 上观看]({v.get('url', '')})",
            f"- 入选原因：{labels}",
        ]
        score = v.get("score")
        if score:
            reason = v.get("score_reason", "")
            lines.append(f"- 推荐指数：{score}/100" + (f"（{reason}）" if reason else ""))
        lines.append("")
        chapters = parse_chapters(v.get("description", ""))
        if chapters:
            lines.append("- 内容章节：")
            lines += [f"  - {ts} {t}" for ts, t in chapters]
            lines.append("")
        excerpt = outline_summary(v.get("description", ""))
        if excerpt:
            lines += ["- 简介摘录：", "", excerpt, ""]
    return "\n".join(lines).rstrip() + "\n"


if __name__ == "__main__":
    # 自检：3 个样本（金融 / AI / 烹饪→应被过滤）
    samples = [
        {
            "video_id": "a1", "title": "2026 美股展望：指数基金定投策略",
            "channel": "财经频道", "added_at": "2026-10-14T10:00:00Z",
            "duration": "18:24",
            "description": "00:00 开场\n08:12 为什么选指数基金\n15:40 定投实操\n\n本期聊聊美股指数基金定投……",
            "url": "https://www.youtube.com/watch?v=a1",
            "score": 72,
            "score_reason": "内容扎实但偏入门",
        },
        {
            "video_id": "b2", "title": "Claude 新功能实测：AI 编程效率翻倍？",
            "channel": "科技前线", "added_at": "2026-10-14T09:00:00Z",
            "duration": "12:05",
            "description": "实测 Claude 的新功能，看看 AI 编程到底多强。",
            "url": "https://www.youtube.com/watch?v=b2",
            "score": 88,
            "score_reason": "新功能实测，信息密度高",
        },
        {
            "video_id": "c3", "title": "10 分钟快手晚餐",
            "channel": "美食日记", "added_at": "2026-10-14T08:00:00Z",
            "duration": "10:00",
            "description": "今天做三道快手菜。",
            "url": "https://www.youtube.com/watch?v=c3",
        },
    ]
    kept = filter_videos(samples)
    assert len(kept) == 2, f"应过滤掉烹饪视频，实际保留 {len(kept)}"
    assert kept[0][1] == ["finance"], kept[0][1]
    assert kept[1][1] == ["ai_tech"], kept[1][1]
    md = build_digest("2026-10-14", kept)
    assert "快手晚餐" not in md
    assert "## 1." in md and "## 2." in md
    assert "00:00 开场" in md  # 章节被提取
    assert "推荐指数：88/100" in md and "推荐指数：72/100" in md
    assert md.index("Claude 新功能实测") < md.index("2026 美股展望")  # 按推荐指数从高到低
    print(md[:400])
    print("…自检通过：过滤/分类/章节提取/评分排序/Markdown 生成均正常")
