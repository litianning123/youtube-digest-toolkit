#!/usr/bin/env python3
"""用 NotebookLM（notebooklm-py 非官方客户端）给新增视频生成精读摘要。

流程（每天一次）：
  1. 建一个当天专用的 notebook（标题 "YouTube 摘要 YYYY-MM-DD"）；
  2. 把 new_videos.json 里每个新视频的 YouTube URL 加为 source；
  3. 等待各 source 处理完成；
  4. 对每个 source 用固定中文 prompt 提问，答案存 notebooklm_notes/<video_id>.md；
  5. stdout 打印 JSON 汇总，调用方据此把 deep_dive 写回视频字段。

认证（一次性，人工完成）：
  用户在自己电脑上跑 `notebooklm login --browser-cookies chrome`
  （需先 pip3 install "notebooklm-py[cookies]"，Chrome 里登录好 Google 小号），
  把生成的 ~/.notebooklm/profiles/default/storage_state.json 发给助手，
  助手放到本机同路径。之后每天 cron 跑本脚本前会自动 `auth refresh --quiet`
  保活（失败不致命）。

用法：
    python3 fetch_notebooklm.py [--date YYYY-MM-DD] [--auth-check]
    python3 fetch_notebooklm.py --auth-check   # 只检查登录状态，0=ok

成功：notebooklm_notes/<video_id>.md；stdout 打印 {"ok": true, ...}。
失败：单个视频失败只记 failed，不中断整体；exit 0 仍表示脚本跑完。
未登录：exit 2，调用方应在聊天里提醒用户完成一次性登录，当天其余流程照常。

环境变量：
    NOTEBOOKLM_BIN   notebooklm 可执行文件路径。缺省时依次查找：
                     同目录的 `notebooklm` wrapper（会清洗 no_proxy 里的 IPv6，
                     见 README）→ PATH 中的 notebooklm → .venv-notebooklm/bin/notebooklm
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
VENV_BIN = BASE / ".venv-notebooklm" / "bin" / "notebooklm"
WRAPPER = BASE / "notebooklm"
CACHE_DIR = BASE / "notebooklm_notes"
NEW_VIDEOS = BASE / "new_videos.json"
PROFILE_STATE = Path.home() / ".notebooklm" / "profiles" / "default" / "storage_state.json"

PROMPT = """请基于这个 YouTube 视频的完整内容，用简体中文写一份精读摘要，要求：

1. 一句话核心观点：这个视频最想讲什么
2. 关键要点：分条列出，每条包含具体细节、数据或例子，不要只写标题式概括
3. 可操作建议：观众看完能直接用的行动点
4. 局限或争议：如果视频中的观点有片面、过时或值得商榷之处，请指出

只基于视频内容作答，不要编造视频里没有的信息。精读部分篇幅 600-1000 字。

写完精读后，另起一段，用以下固定格式输出元信息（严格按格式，不要加多余文字）：

【元信息】
标签：5-8 个中文标签，用顿号分隔，基于视频实际内容概括（如：知识管理、Obsidian、网页剪藏）
关键概念：3-5 个适合收入知识库做双链的核心概念，格式为"概念名：一句话解释"，用分号分隔
金句：2-3 条视频中的原话，每条用中文双引号包裹，用分号分隔；没有合适的就写"无"

注意：元信息三行要紧凑输出，元信息块结束后不要再添加任何其他文字。
"""

CITATION_RE = re.compile(r"\s*\[\d+(?:\s*[-,]\s*\d+)*\]")
ANSWER_PREFIX_RE = re.compile(r"^Answer:\s*\n?", re.IGNORECASE)
NEW_CONV_RE = re.compile(r"\n?New conversation:\s*\S+\s*$", re.IGNORECASE)
META_RE = re.compile(r"【元信息】\s*\n(?P<meta>.*)", re.S)


def clean_answer(text: str) -> str:
    """去掉 ask 输出的 Answer: 前缀、New conversation 尾巴和引用标记 [1]/[1-4]/[2, 5]。"""
    text = ANSWER_PREFIX_RE.sub("", text.strip())
    text = NEW_CONV_RE.sub("", text)
    return CITATION_RE.sub("", text).strip()


def _strip_quotes(s: str) -> str:
    return s.strip().strip("\"'“”‘’").strip()


FIELD_PREFIXES = ("标签：", "关键概念：", "金句：")


def parse_meta(answer: str) -> tuple[str, list[str], list[dict], list[str]]:
    """从答案尾部的【元信息】块解析出 (正文, 标签, 概念[{name, desc}], 金句)。

    模型输出会自动换行（如 "Obsidian Web \\nClipper"），解析时先把续行合并
    回当前字段；遇到空行则认为元信息块结束（挡掉模型尾巴上的客套话）。
    """
    tags: list[str] = []
    concepts: list[dict] = []
    quotes: list[str] = []
    m = META_RE.search(answer)
    body = answer
    if not m:
        return body, tags, concepts, quotes
    body = answer[: m.start()].rstrip()

    fields: list[list] = []  # [字段名, 合并后的值]；合并时保留行尾空格做连接判断
    seen: set[str] = set()
    for raw_line in m.group("meta").splitlines():
        s = raw_line.strip()
        if not s:
            if len(seen) == len(FIELD_PREFIXES):
                break  # 三个字段都齐了，空行后是模型的客套话，直接截断
            continue
        if s.startswith(FIELD_PREFIXES):
            for p in FIELD_PREFIXES:
                if s.startswith(p):
                    idx = raw_line.find(p) + len(p)
                    fields.append([p, raw_line[idx:].lstrip()])
                    seen.add(p)
                    break
        elif fields and not s.startswith("💡"):
            p, val = fields[-1]
            cont = raw_line.lstrip()  # 去缩进，保留行尾空格
            sep = " " if val.endswith((" ", "\t")) else ""
            fields[-1][1] = val.rstrip() + sep + cont

    for name, val in fields:
        if name == "标签：":
            raw = val.replace("、", ",").replace("，", ",")
            tags = [t.strip().lstrip("#") for t in raw.split(",") if t.strip()]
        elif name == "关键概念：":
            for part in val.split("；"):
                part = part.strip()
                if not part:
                    continue
                if "：" in part:
                    cname, desc = part.split("：", 1)
                elif ":" in part:
                    cname, desc = part.split(":", 1)
                else:
                    cname, desc = part, ""
                cname = cname.strip().strip("【】[]")
                if cname:
                    concepts.append({"name": cname, "desc": desc.strip()})
        elif name == "金句：":
            if val and val != "无":
                for q in val.split("；"):
                    q = _strip_quotes(q)
                    if q and q != "无":
                        quotes.append(q)
    return body, tags, concepts, quotes


def build_enriched_note(v: dict, date: str, body: str,
                        tags: list[str], concepts: list[dict],
                        quotes: list[str]) -> str:
    """生成 Obsidian-ready 的独立精读笔记（含完整 frontmatter）。"""
    title = v.get("title", "(无标题)")
    safe_title = title.replace('"', "'")
    all_tags = ["youtube", "精读"] + [t for t in tags if t not in ("youtube", "精读")]
    lines = [
        "---",
        f'title: "{safe_title}"',
        f"date: {date}",
        "type: youtube-notebooklm-dive",
        f"video_id: {v.get('video_id', '')}",
        f"source: {v.get('url', '')}",
        f"channel: \"{(v.get('channel', '') or '').replace(chr(34), chr(39))}\"",
        f"duration: \"{v.get('duration', '')}\"",
        f"tags: [{', '.join(all_tags)}]",
        "---",
        "",
        f"# {title}",
        "",
        f"> 视频链接：{v.get('url', '')}",
        f"> 频道：{v.get('channel', '未知')}｜时长：{v.get('duration', '未知')}｜抓取日期：{date}",
        "",
        body.strip(),
        "",
    ]
    if tags:
        lines += ["## 内容标签", "", " ".join(f"#{t}" for t in tags), ""]
    if concepts:
        lines += ["## 关键概念", ""]
        lines += [f"- **{c['name']}**" + (f"：{c['desc']}" if c.get("desc") else "")
                  for c in concepts]
        lines += [""]
    if quotes:
        lines += ["## 金句摘录", ""]
        lines += [f'> "{q}"' for q in quotes]
        lines += [""]
    return "\n".join(lines).rstrip() + "\n"


def _sanitized_env() -> dict:
    """清洗 no_proxy/NO_PROXY：去掉所有 IPv6 条目。

    背景：本机 no_proxy 里有 IPv6（裸写 ::1 和方括号 [::1] 都有），httpx 为
    每个条目生成代理 pattern 时，连带生成 `*` 通配符形式（如 all://*[::1]），
    其 URL 解析会把 ":1]" 当端口，直接抛 Invalid port，导致 notebooklm CLI
    所有命令失败。去掉含 ":" 的条目即可，剩下 localhost/127.0.0.1/内网 IPv4
    照常直连；本流水线只访问外部 Google 服务，无影响。
    """
    env = dict(os.environ)
    for key in ("no_proxy", "NO_PROXY"):
        val = env.get(key)
        if not val:
            continue
        kept = [p for p in (s.strip() for s in val.split(","))
                if p and ":" not in p]
        env[key] = ",".join(kept)
    return env


def _notebooklm_bin() -> str:
    """按优先级找到 notebooklm 可执行文件。"""
    if os.environ.get("NOTEBOOKLM_BIN"):
        return os.environ["NOTEBOOKLM_BIN"]
    if WRAPPER.is_file() and os.access(WRAPPER, os.X_OK):
        return str(WRAPPER)
    which = shutil.which("notebooklm")
    if which:
        return which
    return str(VENV_BIN)


def nb(*args, timeout=600):
    """调 notebooklm CLI，返回 (rc, stdout, stderr)。"""
    p = subprocess.run(
        [_notebooklm_bin(), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_sanitized_env(),
    )
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def auth_ok() -> tuple[bool, str]:
    if not PROFILE_STATE.exists():
        return False, f"找不到登录态文件 {PROFILE_STATE}，一次性交互登录还没做"
    rc, out, err = nb("auth", "check", timeout=120)
    if rc != 0:
        return False, f"auth check 失败：{err or out}"
    return True, out or "ok"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--auth-check", action="store_true",
                    help="只检查登录状态")
    args = ap.parse_args(argv)

    ok, msg = auth_ok()
    if args.auth_check:
        print(json.dumps({"ok": ok, "detail": msg}, ensure_ascii=False))
        return 0 if ok else 2
    if not ok:
        print(json.dumps({"ok": False, "error": msg,
                          "hint": "用户需在自己电脑上跑 notebooklm login "
                                  "--browser-cookies chrome 完成一次性登录，"
                                  "再把 storage_state.json 发给助手放到本机"},
                         ensure_ascii=False))
        return 2

    # 保活（best-effort）
    nb("auth", "refresh", "--quiet", timeout=180)

    try:
        videos = json.loads(NEW_VIDEOS.read_text(encoding="utf-8"))
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"读 new_videos.json 失败：{e}"},
                         ensure_ascii=False))
        return 1
    if not isinstance(videos, list):
        videos = []

    CACHE_DIR.mkdir(exist_ok=True)
    date = args.date
    processed, skipped, failed = [], [], []

    targets = []
    for v in videos:
        vid = v.get("video_id", "")
        if not vid:
            continue
        cache = CACHE_DIR / f"{vid}.md"
        if cache.exists() and cache.stat().st_size > 0:
            skipped.append(vid)
            continue
        targets.append(v)
    if not targets:
        print(json.dumps({"ok": True, "notebook": None, "processed": [],
                          "skipped": skipped, "failed": []},
                         ensure_ascii=False))
        return 0

    rc, out, err = nb("create", f"YouTube 摘要 {date}", "--json", timeout=180)
    if rc != 0:
        print(json.dumps({"ok": False, "error": f"建 notebook 失败：{err or out}"},
                         ensure_ascii=False))
        return 1
    try:
        data = json.loads(out)
        nb_id = data.get("id") or data.get("notebook", {}).get("id", "")
    except Exception:
        nb_id = ""
    if not nb_id:
        print(json.dumps({"ok": False, "error": f"建 notebook 返回异常：{out}"},
                         ensure_ascii=False))
        return 1

    # 1) 全部加为 source
    pending = []  # (video, source_id)
    for v in targets:
        vid = v["video_id"]
        url = v.get("url") or f"https://www.youtube.com/watch?v={vid}"
        rc, out, err = nb("source", "add", url, "--type", "youtube",
                          "-n", nb_id, "--json", timeout=300)
        if rc != 0:
            failed.append({"video_id": vid, "stage": "add",
                           "error": (err or out)[:300]})
            continue
        try:
            _d = json.loads(out)
            sid = _d.get("id") or _d.get("source", {}).get("id", "")
        except Exception:
            sid = ""
        if not sid:
            failed.append({"video_id": vid, "stage": "add",
                           "error": f"返回异常：{out[:200]}"})
            continue
        pending.append((v, sid))

    # 2) 等待处理完成（逐个等，互不影响）
    ready = []
    for v, sid in pending:
        vid = v["video_id"]
        rc, out, err = nb("source", "wait", sid, "-n", nb_id,
                          "--timeout", "600", "--interval", "15", timeout=660)
        if rc != 0:
            failed.append({"video_id": vid, "stage": "wait",
                           "error": (err or out)[:300] or f"rc={rc}"})
            continue
        ready.append((v, sid))

    # 3) 逐个提问，存精读
    prompt_file = CACHE_DIR / f".prompt-{date}.txt"
    prompt_file.write_text(PROMPT, encoding="utf-8")
    meta: dict[str, dict] = {}
    try:
        for v, sid in ready:
            vid = v["video_id"]
            rc, out, err = nb("ask", "--new", "-y", "-s", sid, "-n", nb_id,
                              "--prompt-file", str(prompt_file),
                              "--timeout", "300", timeout=360)
            if rc != 0 or not out:
                failed.append({"video_id": vid, "stage": "ask",
                               "error": (err or out)[:300] or f"rc={rc}"})
                continue
            text = clean_answer(out)
            if not text:
                failed.append({"video_id": vid, "stage": "ask",
                               "error": "答案为空"})
                continue
            body, tags, concepts, quotes = parse_meta(text)
            note = build_enriched_note(v, date, body, tags, concepts, quotes)
            (CACHE_DIR / f"{vid}.md").write_text(note, encoding="utf-8")
            meta[vid] = {"tags": tags, "concepts": concepts, "quotes": quotes}
            processed.append(vid)
    finally:
        prompt_file.unlink(missing_ok=True)

    print(json.dumps({"ok": True, "notebook": nb_id, "notebook_title": f"YouTube 摘要 {date}",
                      "processed": processed, "meta": meta, "skipped": skipped,
                      "failed": failed}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
