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
python3 make_digest.py    # 过滤 → 生成 → YoutubeInbox/2026-10-14-YouTube 每日摘要.md
python3 fetch.py --mark-seen   # 标记已处理，避免明天重复抓取
```

`make_digest.py` 读 `config.json` 决定摘要长什么样：

| 字段 | 作用 |
| --- | --- |
| `output_dir` | 摘要写到哪个目录（相对本工具包，默认 `YoutubeInbox`） |
| `topics` | 保留哪些主题，可用 `finance`（金融/理财）、`ai_tech`（AI/科技）；不写则两者都留 |
| `max_items` | 每天最多几条（先按分数排序再截断，默认 10） |

`digest.py` 是纯标准库的摘要生成库，默认不依赖任何 AI：只按标题/简介/章节出大纲版摘要。
想升级成「接 LLM 版」，就用它的函数、给视频补上 `score` / `score_reason`
（在 `make_digest.py` 里插一步打分调用，或自己写脚本）：

```python
from digest import filter_videos, build_digest
import json

videos = json.load(open('new_videos.json'))
kept = filter_videos(videos, ["finance", "ai_tech"])   # 只留金融/理财、AI/科技
# （可选）用 LLM 打分：v["score"] = 88; v["score_reason"] = "一句话理由"
md = build_digest('2026-10-14', kept, max_items=10)    # 按分数从高到低排序
open('2026-10-14-YouTube 每日摘要.md', 'w').write(md)
```

## 定时运行

Linux/macOS cron 示例（每天早上 8:40）：

```
40 8 * * * cd /path/to/toolkit && YOUTUBE_API_KEY=xxx python3 fetch.py && python3 make_digest.py
```

`make_digest.py` 就是仓库里这个组装脚本（过滤 → 排序截断 → 生成 md → 归档到 `output_dir`）；
想加 LLM 打分，在它调 `build_digest` 之前插一步即可。Windows 可用任务计划程序实现同样效果。
（不想每天手工记账的话，可以在 `make_digest.py` 成功后接着跑 `fetch.py --mark-seen`。）

## 输出示例

落在你配置的 `output_dir`（默认 `YoutubeInbox/`）下，文件名 `<日期>-YouTube 每日摘要.md`。
同名文件会被覆盖；要留档就在 cron 里接着 `mv` 到别处。

Markdown 文档，含 frontmatter（date/source/tags），每个视频包含：
标题、频道、时长、观看链接、入选原因、推荐指数/100（一句话理由）、内容章节、简介摘录。

## 评分标准（供 LLM 参考）

- 信息密度与深度 40 / 新颖度与时效性 25 / 可操作性 20 / 兴趣匹配度 15
- 90+ 必看；75–89 值得看；60–74 看摘要即可；60 以下可跳过
- 评分基于标题、简介与章节信息初步判断，注明仅供参考

## 主题关键词

在 `digest.py` 的 `FINANCE_KEYWORDS` / `AI_TECH_KEYWORDS` 里，按需增删。
比如不关心币圈就删掉相关词，只想看 AI 就收紧列表。

两级控制各管一段：`config.json` 的 `topics` 决定**要哪几个大类**，
关键词决定**什么内容算命中那一类**。所以「只想看 AI」有两条路：
把 `topics` 收成 `["ai_tech"]`（不改代码），或直接删掉金融类关键词。

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `fetch.py` | 抓取播放列表新增视频 → `new_videos.json`；`--mark-seen` 记账到 `state.json` |
| `digest.py` | 摘要生成库（分类/章节提取/简介摘录/Markdown 渲染），可单独 import |
| `make_digest.py` | 把 `new_videos.json` 组装成 `output_dir/<日期>-YouTube 每日摘要.md` |
| `config.example.json` | 配置模板，复制成 `config.json` 后填 `playlist_id` |

`config.json`、`state.json`、`new_videos.json` 与生成的摘要都已在 `.gitignore` 里，
不会被提交（里面有你的 playlist_id、已处理记录和个人摘要）。

## 限制

- 拿不到视频字幕（YouTube 会封禁抓取方的 IP），只能做基于标题/简介/章节的大纲版摘要；
  想要全文级摘要，需要人工复制字幕文本再喂给 AI。
- API key 读不到设为"私密"的播放列表，请用"不公开"或"公开"。
- 每个人的 API key 互相独立，请勿分享自己的 key。

## 许可

MIT，见 [LICENSE](LICENSE)。
