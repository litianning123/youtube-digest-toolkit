# YouTube 每日摘要工具包

把你 YouTube 播放列表里新增的视频，自动整理成中文摘要文档。
每天看一份摘要，就知道哪些视频值得点开，哪些扫一眼就行。

## 两种用法

**A. 纯脚本版（零成本，不需要 AI）**
抓取新增视频 → 按主题过滤 → 生成大纲版摘要（标题/简介/章节/链接），
按加入时间从新到旧排列。

**B. 接 LLM 版（推荐）**
在 A 的基础上，让 AI 给每个视频打推荐指数（百分制）并写一句话推荐理由，
摘要按分数从高到低排列。`digest.py` 已内置评分渲染，
你只需要把视频信息喂给任意大模型，按下面的评分标准打分即可。

## 准备工作

1. **建一个专用播放列表**：在 YouTube 新建播放列表（比如叫「待摘要」），
   隐私设为"不公开"（unlisted），把想摘要的视频加进去。
   记下播放列表 URL 中 `list=` 后面的 ID。
   （为什么不用 Watch Later：YouTube 官方 API 自 2016 年起关闭了第三方对其的访问。）
2. **申请 API key**（免费）：
   - 打开 console.cloud.google.com，用 Google 账号登录，新建一个项目；
   - "API 和服务" → "库"，搜索 "YouTube Data API v3" 并启用；
   - "API 和服务" → "凭据" → "创建凭据" → "API 密钥"，复制下来；
   - 建议在密钥设置里限制为仅 YouTube Data API v3。
   - 每天读几十个视频只消耗几十 units，默认每日 10000 units 额度完全够用。

## 安装运行

```bash
export YOUTUBE_API_KEY=你刚才复制的key
cp config.example.json config.json
# 编辑 config.json，填入 playlist_id
python3 fetch.py          # 抓取新增视频 → new_videos.json
```

`digest.py` 是摘要生成库（只需 Python 3 标准库），拿去用：

```python
from digest import filter_videos, build_digest
import json

videos = json.load(open('new_videos.json'))
kept = filter_videos(videos)          # 只留金融/理财/AI/科技，可自行改关键词
# （可选）用 LLM 打分：v['score'] = 88; v['score_reason'] = '一句话理由'
md = build_digest('2026-10-14', kept)
open('2026-10-14-YouTube 每日摘要.md', 'w').write(md)
```

摘要完成后记得标记已处理，避免重复抓取：

```bash
python3 fetch.py --mark-seen
```

## 定时运行

Linux/macOS cron 示例（每天早上 8:40）：

```
40 8 * * * cd /path/to/toolkit && YOUTUBE_API_KEY=xxx python3 fetch.py && python3 make_digest.py
```

其中 `make_digest.py` 是你自己的组装脚本（过滤 → 可选调 LLM 打分 → 生成 md → 归档）。
Windows 可用任务计划程序实现同样效果。

## 输出示例

Markdown 文档，含 frontmatter（date/source/tags），每个视频包含：
标题、频道、时长、观看链接、入选原因、推荐指数/100（一句话理由）、内容章节、简介摘录。

## 评分标准（供 LLM 参考）

- 信息密度与深度 40 / 新颖度与时效性 25 / 可操作性 20 / 兴趣匹配度 15
- 90+ 必看；75–89 值得看；60–74 看摘要即可；60 以下可跳过
- 评分基于标题、简介与章节信息初步判断，注明仅供参考

## 主题关键词

在 `digest.py` 的 `FINANCE_KEYWORDS` / `AI_TECH_KEYWORDS` 里，按需增删。
比如不关心币圈就删掉相关词，只想看 AI 就收紧列表。

## 限制

- 拿不到视频字幕（YouTube 会封禁抓取方的 IP），只能做基于标题/简介/章节的大纲版摘要；
  想要全文级摘要，需要人工复制字幕文本再喂给 AI。
- API key 读不到设为"私密"的播放列表，请用"不公开"或"公开"。
- 每个人的 API key 互相独立，请勿分享自己的 key。
