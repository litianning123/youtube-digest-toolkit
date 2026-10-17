# YouTube 每日摘要工具包

把你 YouTube 播放列表里新增的视频，自动整理成中文摘要文档。
每天看一份摘要，就知道哪些视频值得点开，哪些扫一眼就行。

## 两种用法

**A. 纯脚本版（零成本，不需要 AI）**
抓取新增视频 → 按主题过滤 → 生成大纲版摘要（标题/简介/章节/链接），
无评分时按加入时间从新到旧排列（有评分则按分数从高到低，见 B）。

**B. 接 LLM 版（推荐）**
在 A 的基础上，让 AI 给每个视频打推荐指数（百分制）并写一句话推荐理由，
摘要按分数从高到低排列；`fetch.py` 还会抓取每个视频的高赞评论供 AI 参考；
全文精读走 NotebookLM（见下），所有视频都会尝试，不看分数；单条失败不影响当天摘要。

`digest.py` 已内置评分/评论/精读渲染，
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
python3 make_digest.py    # 过滤 → 生成 YoutubeInbox/<日期>-YouTube 每日摘要/
python3 fetch.py --mark-seen   # 摘要确认后标记已处理，避免明天重复抓取
```

`config.json` 控制输出与过滤：

| 字段 | 作用 |
| --- | --- |
| `output_dir` | 摘要写到哪；相对路径按本工具包算，也可写绝对路径（如 `/Users/you/Obsidian/Inbox`） |
| `git_push` | `true` 时写完把该目录 commit + push 到它所属的仓库（推送失败只告警，下次重试） |
| `topics` | 保留哪些主题：`finance`、`ai_tech`；空数组视为不过滤 |
| `max_items` | 每天最多几篇（先按分数排序再截断，默认 10） |
| `transcriptyt_key` | TranscriptYT 的 key，只走降级方案时才需要 |

**写进 Obsidian 仓库并自动同步**：把 `output_dir` 指向 vault 里的目录（建议绝对路径），
并设 `"git_push": true`。`make_digest.py` 生成完会 `git add` 当天那个目录、
`git commit -m "每日摘要：<日期>（N 篇）"`、`git push`。
同一天重跑若没有实际变更，会跳过提交但仍 push 一次，把之前没推成功的提交补上。
cron 环境下要保证 git 凭据可用（SSH key 或 credential helper），否则只会得到一条 push 失败告警。

`digest.py` 是摘要生成库（只需 Python 3 标准库）。
日常直接用 `python3 make_digest.py`（含旧文件清理、`git_push`、`--with-cache`）；
只有想自己控制流程时才用下面的库函数。它生成一个目录：总览 `README.md`
（排行榜用 wikilink 链到各文章）+ 每个视频一篇 `NN-标题.md`（NN 为推荐指数排名），
避免长文被截断，也方便单独保存某篇：

```python
from digest import filter_videos, build_index, build_article, article_filename
import json, os

with open('config.json', encoding='utf-8') as f:
    cap = json.load(f).get('max_items') or 10
with open('new_videos.json', encoding='utf-8') as f:
    videos = json.load(f)
kept = filter_videos(videos)          # 只留金融/理财/AI/科技，可自行改关键词
# （可选）用 LLM 打分：v['score'] = 88; v['score_reason'] = '一句话理由'
# （可选）从 v['top_comments'] 挑 2-3 条有信息量的评论 → v['key_comments']
# （可选）全文精读（见下）→ v['deep_dive'] = '精读正文'; v['deep_dive_source'] = 'notebooklm'
# （可选）精读元信息 → v['nb_tags'] / v['nb_concepts'] / v['nb_quotes']
d = '2026-10-14-YouTube 每日摘要'
os.makedirs(d, exist_ok=True)
with open(f'{d}/README.md', 'w', encoding='utf-8') as f:
    f.write(build_index('2026-10-14', kept, total_new=len(videos), max_items=cap))
ranked = sorted(kept, key=lambda vt: -(vt[0].get('score') or -1))[:cap]
for i, (v, topics) in enumerate(ranked, 1):
    with open(f'{d}/{article_filename(i, v["title"])}', 'w', encoding='utf-8') as f:
        f.write(build_article('2026-10-14', i, v, topics))
```

## 全文精读（NotebookLM，推荐）

YouTube 官方不提供字幕接口，但 NotebookLM（Google 的 AI 笔记服务）
可以在服务端抓取视频字幕并做总结。用开源的非官方客户端 `notebooklm-py`
（协议以其仓库为准）即可程序化调用，不限视频分数，所有视频都会尝试；
单条失败只记进 `failed`，不影响其余视频和当天摘要：

```bash
pip3 install "notebooklm-py[cookies]"      # 复用浏览器登录态需要 cookies extra
notebooklm login --browser-cookies chrome  # 一次性登录（建议小号）；之后脚本每次自动 auth refresh
python3 fetch_notebooklm.py   # 建当天专用 notebook → 加视频源 → 逐个提问
                              # 精读落到 notebooklm_notes/<video_id>.md
python3 make_digest.py --with-cache   # 生成摘要时自动回填精读
```

输出 `notebooklm_notes/<video_id>.md`：Obsidian-ready 的独立精读笔记，含完整
frontmatter（标题/日期/video_id/视频网址/频道/时长/tags），正文为一句话核心观点、
关键要点、可操作建议、局限与争议，外加三组元信息：

- **内容标签**：模型按视频内容生成的 5–8 个标签（`#知识管理`），跨视频聚合检索；
- **关键概念**：3–5 个适合做双链的核心概念，`digest.py` 会渲染成 `[[双链]]`；
- **金句摘录**：2–3 条视频原话，引用块格式。

`make_digest.py --with-cache` 会读这些笔记：正文填进 `deep_dive`
（标记 `deep_dive_source: notebooklm`），「内容标签 / 关键概念 / 金句摘录」
填进 `nb_tags` / `nb_concepts` / `nb_quotes`，`digest.py` 负责渲染，
标签还会并入文章 frontmatter。没有笔记、或想手写，直接给视频补这些字段同样生效。

`fetch_notebooklm.py` 未登录时以 `exit 2` 退出，另外 `--auth-check` 只查登录态（0=已登录）。
cron 里建议先跑 `--auth-check` 判断，或把精读排在 `make_digest.py` 之后，
免得一次未登录就用 `&&` 掐断当天的摘要。

环境注意：如果你的机器 `no_proxy` 环境变量里有 IPv6 条目（如 `::1`），
`notebooklm` 命令会报 `Invalid port` 起不来——这是 notebooklm-py 依赖的 httpx
解析代理 pattern 的 bug。用本包自带的 `notebooklm` wrapper 脚本代替直接调用
（它会先清洗环境变量），或把 IPv6 条目从 `no_proxy` 里去掉。
`fetch_notebooklm.py` 内部调用已自动处理这个问题。

## 降级：高分视频全文精读（TranscriptYT）

NotebookLM 偶尔会失败（无字幕视频、Google 改接口）。降级方案用第三方
TranscriptYT（免费额度以官网为准，够 85 分以上视频用）：

1. 去 TranscriptYT 官网注册并复制 API key，填入 `config.json` 的 `transcriptyt_key`。
2. 对高分视频运行：`python3 fetch_transcript.py <video_id>`
   → 全文存到 `transcripts/<video_id>.txt`（转写按中文优先、英文其次拉取）。
3. 把全文喂给 LLM 写一段中文精读版摘要，写入该视频的 `deep_dive` 字段
   （标记 `deep_dive_source: transcriptyt`）。

注意：`fetch_transcript.py` 用 curl 发请求（在这类云主机上 urllib 读大响应
可能被截断），key 通过临时文件传递，不会出现在命令行或日志里。

## 定时运行

Linux/macOS cron 示例（每天早上 8:40）：

```
40 8 * * * cd /path/to/toolkit && export YOUTUBE_API_KEY=xxx && python3 fetch.py && python3 make_digest.py && python3 fetch.py --mark-seen
```

其中 `make_digest.py` 就是仓库里的组装脚本（过滤 → 排序截断 → 生成总览与各篇文章，
`git_push` 开启时还会提交推送）；
LLM 打分、`fetch_notebooklm.py` 精读在它前后各插一步即可。
`export` 不能用 `A=1 cmd1 && cmd2` 那种写法替代：`fetch.py` 在模块级就校验 key，
连 `--mark-seen` 也需要环境变量。
Windows 可用任务计划程序实现同样效果。

## 输出示例

落在 `config.json` 的 `output_dir`（默认 `YoutubeInbox/`，也可以是指向 Obsidian 仓库的绝对路径）下，每天一个目录 `YYYY-MM-DD-YouTube 每日摘要/`：
- `README.md`：总览，含排行榜（wikilink 链到各文章）
- `NN-标题.md`：每个视频一篇独立文章，含 frontmatter
 （date/rank/video_id/source/channel/duration/score/score_reason/topics/tags），
  正文含标题、频道、时长、观看链接、入选原因、推荐指数/100（一句话理由）、
  观众精华（高赞评论 2-3 条）、延伸阅读、全文章节、简介摘录；
  有精读的视频还带"全文精读"版，以及内容标签（`#tag`）、关键概念（`[[双链]]`）、
  金句摘录三节。

## 评分标准（供 LLM 参考）

- 信息密度与深度 40 / 新颖度与时效性 25 / 可操作性 20 / 兴趣匹配度 15
- 90+ 必看；75–89 值得看；60–74 看摘要即可；60 以下可跳过
- 评分基于标题、简介与章节信息初步判断，注明仅供参考

## 主题关键词

在 `digest.py` 的 `FINANCE_KEYWORDS` / `AI_TECH_KEYWORDS` 里，按需增删。
比如不关心币圈就删掉相关词，只想看 AI 就收紧列表。

两级控制：`config.json` 的 `topics` 决定要哪几个大类
（`finance` / `ai_tech`，空数组=不过滤），这两个关键词表决定
「什么内容算命中某一类」。

## Muse 用户复刻指南（想复刻完整机器人）

把这个压缩包发给你的 Muse，它会帮你搭出和原版一样的机器人：

1. 你自己做（约十分钟）：按上面的"准备工作"建不公开播放列表、申请 API key。
   key 在 Muse 给你的安全链接里填，Muse 看不到原文（按 Muse 的说法）。
2. Muse 来做：通过 `custom_connector_setup` 建 `custom.youtube` 连接器、
   适配抓取脚本、创建两个定时任务（每天早上检查更新生成摘要、每周日出周报）。
   周报不在本仓库里，需要 Muse 在每日摘要之上另行汇总。
3. 输出位置你定：有 Obsidian+Git 的，把 `output_dir` 指到 vault 里的目录并设
   `"git_push": true`，每次生成完自动 commit + push 进知识库（像原版一样）；
   没有的可以留在本地，或由 Muse 侧再转存到 Google Drive / 发邮件（不在本仓库里）。
4. 全文精读：在自己的电脑上跑一次 `notebooklm login`（建议小号），
   之后每天的定时任务会自动用 NotebookLM 出精读。

打包前先排掉个人数据：`config.json`（含 `playlist_id` 与 `transcriptyt_key`）、
`new_videos.json`（含评论者账号）、`state.json`、`notebooklm_notes/`、
`transcripts/`、生成的摘要目录。它们都在 `.gitignore` 里，所以
`git archive HEAD | gzip > toolkit.tar.gz` 只打包已提交文件、天然不含这些；
直接 `zip -r` 整个目录则会把它们一起带走。

Token 开销参考：每天约 2–5 万 tokens（额度占比以 Muse 官方口径为准）；
NotebookLM 的总结在 Google 服务端完成，不占你的 token。

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `fetch.py` | 抓播放列表新增视频（含高赞评论）→ `new_videos.json`；`--mark-seen` 记账到 `state.json` |
| `digest.py` | 渲染库：分类/章节/评论/精读/Markdown，可单独 import |
| `make_digest.py` | 组装当天摘要目录；`--with-cache` 回填精读，`git_push` 提交推送 |
| `fetch_notebooklm.py` | 用 NotebookLM 出精读 → `notebooklm_notes/<video_id>.md` |
| `fetch_transcript.py` | TranscriptYT 降级：全文转写 → `transcripts/<video_id>.txt` |
| `notebooklm` | wrapper 脚本：清洗 `no_proxy` 里的 IPv6 条目后调用 CLI |
| `config.example.json` | 配置模板，复制成 `config.json` 后填 `playlist_id` |

`config.json`、`state.json`、`new_videos.json`、`notebooklm_notes/`、`transcripts/`、
`.venv-notebooklm/` 与生成的摘要目录都在 `.gitignore` 里，不会被提交。

## 限制

- YouTube 官方不提供字幕接口（会封禁抓取方 IP），默认只能做基于标题/简介/章节的
  大纲版摘要；想要全文级摘要，按上面的"全文精读（NotebookLM）"接入，
  或人工复制字幕文本再喂给 AI。
- API key 读不到设为"私密"的播放列表，请用"不公开"或"公开"。
- 每个人的 API key 互相独立，请勿分享自己的 key。
- 评论区关闭或无权限时（HTTP 403）按「无评论」处理，摘要里就没有观众精华；
  配额耗尽同样返回 403，也会被静默当作无评论。
- `notebooklm-py` 是非官方逆向客户端，Google 改内部接口就可能失效；
  届时用 TranscriptYT 降级方案顶上。

## 许可

MIT，见 [LICENSE](LICENSE)。
