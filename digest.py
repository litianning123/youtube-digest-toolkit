#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YouTube 每日摘要 — Markdown 生成器。

与抓取逻辑解耦（可离线测试）：
    输入：视频列表（顺序不限，生成器会按推荐指数重排），每个元素为 dict：
        video_id, title, channel, added_at (ISO 8601), duration (如 "12:34"),
        description, url, score (可选，1-100 推荐指数), score_reason (可选，一句话理由),
        key_comments (可选，精选观众评论), further_reading (可选，延伸阅读),
        deep_dive (可选，精读摘要), deep_dive_source (可选，精读来源：
        "notebooklm" 或 "transcriptyt"，缺省按 transcriptyt 渲染）,
        nb_tags (可选，NotebookLM 按内容生成的标签列表),
        nb_concepts (可选，关键概念列表，每项为 {"name", "desc"} 或字符串),
        nb_quotes (可选，金句列表）
    输出：每日目录——README.md 总览（含排行榜与 wikilink）+ 每个视频一篇独立文章
    （含 frontmatter），按推荐指数从高到低排序编号。

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


def filter_videos(
    videos: list[dict], topics: list[str] | None = None
) -> list[tuple[dict, list[str]]]:
    """只保留金融/理财/AI/科技类，返回 (video, topics)。保持原有顺序。

    topics 给定时（如 config.json 的 ["finance", "ai_tech"]）只保留命中其中主题的视频。
    """
    allowed = set(topics) if topics else None
    kept = []
    for v in videos:
        matched = classify(v.get("title", ""), v.get("description", ""))
        if allowed is not None:
            matched = [t for t in matched if t in allowed]
        if matched:
            kept.append((v, matched))
    return kept


# 文件名非法字符（含会破坏 Obsidian wikilink 的 # 与 []）
FILENAME_BAD_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f#\[\]]')


def safe_filename_part(title: str, max_len: int = 80) -> str:
    """清理标题中不允许出现在文件名的字符。"""
    s = FILENAME_BAD_RE.sub("-", title or "")
    s = re.sub(r"\s+", " ", s).strip().strip(".")
    if len(s) > max_len:
        s = s[:max_len].rstrip()
    return s or "untitled"


def article_filename(rank: int, title: str) -> str:
    """单篇文章文件名：序号（即推荐指数排名）+ 清理后的标题。"""
    return f"{rank:02d}-{safe_filename_part(title)}.md"


def render_video_body(v: dict, topics: list[str]) -> str:
    """单视频正文（不含标题），供独立文章使用。"""
    labels = "、".join(TOPIC_LABEL[t] for t in topics)
    lines = [
        f"- 频道：{v.get('channel', '未知')}｜时长：{v.get('duration', '未知')}｜"
        f"[在 YouTube 上观看]({v.get('url', '')})",
        f"- 入选原因：{labels}",
    ]
    score = v.get("score")
    if score:
        reason = v.get("score_reason", "")
        lines.append(f"- 推荐指数：{score}/100" + (f"（{reason}）" if reason else ""))
    key_comments = v.get("key_comments") or []
    if key_comments:
        lines.append("- 观众精华：")
        lines += [f"  - {c}" for c in key_comments[:3]]
    further = v.get("further_reading") or []
    if further:
        lines.append("- 延伸阅读：")
        lines += [f"  - [{f['title']}]({f['url']})" for f in further[:3]]
    deep = (v.get("deep_dive") or "").strip()
    if deep:
        if v.get("deep_dive_source") == "notebooklm":
            lines.append("- 全文精读（NotebookLM，基于视频完整内容）：")
        else:
            lines.append("- 全文精读（基于完整字幕）：")
        lines += [f"  {p}" for p in deep.splitlines() if p.strip()]
    nb_tags = v.get("nb_tags") or []
    if nb_tags:
        lines.append("- 内容标签：" + " ".join(f"#{t}" for t in nb_tags))
    nb_concepts = v.get("nb_concepts") or []
    if nb_concepts:
        names = [c["name"] if isinstance(c, dict) else str(c) for c in nb_concepts]
        lines.append("- 关键概念：" + "、".join(f"[[{n}]]" for n in names if n))
    nb_quotes = v.get("nb_quotes") or []
    if nb_quotes:
        lines.append("- 金句摘录：")
        lines += [f'  > "{q}"' for q in nb_quotes[:3]]
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


def _yaml_str(value: object) -> str:
    """把文本放进 YAML 双引号标量：转义反斜杠/双引号，换行压成空格。"""
    s = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    return f'"{s}"'


def build_article(digest_date: str, rank: int, v: dict, topics: list[str]) -> str:
    """生成单视频文章 Markdown（含 frontmatter，可独立保存/转存）。

    frontmatter 里的文本字段一律走 _yaml_str：频道名/标签常含 `:`、`#`、`[`，
    不转义会让整段 frontmatter 变成非法 YAML（Obsidian 属性面板直接失效）。
    """
    title = v.get("title", "(无标题)")
    score = v.get("score") or ""
    reason = v.get("score_reason") or ""
    nb_tags = [t for t in (v.get("nb_tags") or []) if t not in ("youtube", "每日摘要")]
    tag_items = ["youtube", "每日摘要", *(str(t) for t in nb_tags)]
    tag_line = "tags: [" + ", ".join(_yaml_str(t) for t in tag_items) + "]"
    lines = [
        "---",
        f"date: {digest_date}",
        f"rank: {rank}",
        f"video_id: {_yaml_str(v.get('video_id', ''))}",
        f"source: {_yaml_str(v.get('url', ''))}",
        f"channel: {_yaml_str(v.get('channel', ''))}",
        f"duration: {_yaml_str(v.get('duration', ''))}",
        f"score: {score}",
        f"score_reason: {_yaml_str(reason)}",
        f"topics: [{', '.join(topics)}]",
        tag_line,
        "---",
        "",
        f"# {title}",
        "",
        render_video_body(v, topics),
    ]
    return "\n".join(lines).rstrip() + "\n"


def build_index(
    digest_date: str,
    videos: list[tuple[dict, list[str]]],
    total_new: int,
    max_items: int | None = None,
) -> str:
    """生成当日目录的 README 总览：排行榜 + wikilink 链到各篇文章。

    先排序再截断，所以 max_items 截掉的是分数最低的；缺省用 MAX_ITEMS。
    """
    videos = sorted(videos, key=lambda vt: -(vt[0].get("score") or -1))
    videos = videos[: max_items or MAX_ITEMS]
    lines = [
        "---",
        f"date: {digest_date}",
        "type: youtube-daily-index",
        f"video_count: {len(videos)}",
        "tags: [youtube, 每日摘要]",
        "---",
        "",
        f"# YouTube 每日摘要（{digest_date}）",
        "",
        f"今日播放列表新增 {total_new} 个视频，其中 {len(videos)} 个符合主题"
        f"（金融/理财、AI/科技），已按推荐指数从高到低排序，每篇独立成文。",
        "",
        "> 注：摘要为大纲版，基于视频标题、简介与章节信息整理，非全文精读；"
        "推荐指数为初步评分，仅供参考。",
        "",
        "## 排行榜",
        "",
    ]
    for i, (v, topics) in enumerate(videos, 1):
        stem = article_filename(i, v.get("title", ""))[:-3]  # 去掉 .md 供 wikilink 用
        score = v.get("score")
        reason = v.get("score_reason", "")
        score_txt = f"{score}/100" + (f"（{reason}）" if reason else "")
        lines.append(f"{i}. [[{stem}]] —— {score_txt}")
    lines += ["", f"共 {len(videos)} 篇，详见各文章。", ""]
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
    # 目录模式：总览 + 独立文章
    idx = build_index("2026-10-14", kept, total_new=3)
    assert "[[01-" in idx and "[[02-" in idx
    assert "快手晚餐" not in idx
    assert idx.index("[[01-") < idx.index("[[02-")  # 按推荐指数从高到低编号
    assert "88/100" in idx and "72/100" in idx
    art = build_article("2026-10-14", 1, kept[1][0], kept[1][1])  # 88 分那篇
    assert art.startswith("---\n")
    assert "# Claude 新功能实测" in art
    assert "推荐指数：88/100" in art
    assert "00:00 开场" not in art  # 这篇简介无章节
    art0 = build_article("2026-10-14", 2, kept[0][0], kept[0][1])
    assert "00:00 开场" in art0  # 章节被提取
    assert 'rank: 2' in art0 and 'score: 72' in art0  # frontmatter 齐全
    # 文件名清理
    fn = article_filename(1, 'a/b:c*d?e"f<g>h|i#j[k]尾')
    assert "/" not in fn and ":" not in fn and "#" not in fn and "[" not in fn
    assert fn.startswith("01-") and fn.endswith(".md")
    # frontmatter 转义：频道/标签含 YAML 元字符也不能破坏结构
    tricky = {
        "video_id": "x:9", "title": "t", "channel": 'AI: 前线 #1 "频道"',
        "url": "https://www.youtube.com/watch?v=x", "duration": "1:00",
        "score": 90, "score_reason": '理由: 含 "引号"',
        "nb_tags": ["C#", '引号"标签', "知识管理"],
    }
    esc = build_article("2026-10-14", 1, tricky, ["ai_tech"])
    assert 'channel: "AI: 前线 #1 \\"频道\\""' in esc, esc.splitlines()[5]
    assert 'tags: ["youtube", "每日摘要", "C#", "引号\\"标签", "知识管理"]' in esc
    assert 'score_reason: "理由: 含 \\"引号\\""' in esc
    assert "\nchannel: AI:" not in esc
    print(idx[:300])
    print("…自检通过：过滤/分类/章节提取/评分排序/目录模式/frontmatter 转义均正常")
