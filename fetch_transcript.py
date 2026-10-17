#!/usr/bin/env python3
"""拉取单个 YouTube 视频的全文转写（TranscriptYT 新版 API）。

只给高分视频用：免费额度每月 100 条，由每日摘要 worker 在评分 >= 85
的视频上调用。key 存在同目录 config.json 的 transcriptyt_key 中。

用法：
    python3 fetch_transcript.py <video_id> [--save-dir DIR]

成功：纯文本转写存到 <save-dir>/<video_id>.txt，stdout 打印一行 JSON
（含 language_code，调用方据此决定精读摘要写中文还是英文）。
失败：stdout 打印 {"ok": false, ...}，exit 1。调用方应跳过该视频、继续流程，
绝不能因此中断整个摘要任务。

语言策略：中文优先 → 英文。transcript_type 固定 "any"（接口优先返回手动字幕）。

实现说明：
- 用 curl 而不用 urllib：实测 urllib 在这台 VM 上读该接口的大响应会
  IncompleteRead（疑似经出站代理时 TLS/分帧兼容问题），curl 始终正常。
- key 通过临时 --config 文件传给 curl（600 权限，用完即删），不出现在命令行。
- 接口有 Cloudflare 保护，必须带浏览器 User-Agent，否则 403 (error 1010)。
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
API = "https://api.transcriptyt.com/api/v1/transcript"
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def load_key() -> str:
    cfg = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
    key = (cfg.get("transcriptyt_key") or "").strip()
    if not key:
        raise RuntimeError("config.json 缺少 transcriptyt_key")
    return key


def _post_once(
    video_id: str, key: str, languages: list, timeout: int
) -> tuple[int, str]:
    """返回 (http_code, body)。网络层异常直接抛出。

    transcript_type 固定 "any"（接口优先返回手动字幕）；
    languages 严格过滤，一次只传一种语言，保证中/英文优先级可控。
    """
    payload = {
        "video_id": video_id,
        "languages": languages,
        "transcript_type": "any",  # 接口优先返回手动字幕
        "format": "json",
    }
    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        body_file = tdir / "body.bin"
        data_file = tdir / "data.json"
        data_file.write_text(json.dumps(payload), encoding="utf-8")
        cfg_file = tdir / "curl.cfg"
        cfg_file.write_text(f'header = "Authorization: Bearer {key}"\n', encoding="utf-8")
        os.chmod(cfg_file, 0o600)
        proc = subprocess.run(
            [
                "curl", "-sS", "--max-time", str(timeout),
                "-X", "POST", API,
                "-K", str(cfg_file),
                "-H", "Content-Type: application/json",
                "-H", "Accept: application/json",
                "-A", BROWSER_UA,
                "--data-binary", "@" + str(data_file),
                "-o", str(body_file),
                "-w", "%{http_code}",
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 10,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"curl exit={proc.returncode}: {proc.stderr.strip()[:200]}")
        code = int(proc.stdout.strip().rsplit("\n", 1)[-1] or 0)
        body = body_file.read_text(encoding="utf-8", errors="replace")
        return code, body


def fetch_transcript(
    video_id: str, key: str, languages: list, timeout: int = 120
) -> dict:
    last_err: Exception | None = None
    for _ in range(3):
        try:
            code, body = _post_once(video_id, key, languages, timeout)
        except Exception as e:  # curl 失败/超时：重试
            last_err = e
            time.sleep(2)
            continue
        if code == 200:
            data = json.loads(body)
            snippets = data.get("snippets") or []
            text = "\n".join(
                s.get("text", "") for s in snippets if s.get("text")
            ).strip()
            if not text:
                raise RuntimeError("接口返回成功但没有转写文本")
            return {
                "video_id": data.get("video_id", video_id),
                "language": data.get("language"),
                "language_code": data.get("language_code"),
                "is_generated": data.get("is_generated"),
                "text": text,
            }
        if 400 <= code < 500:
            # 明确答复（无字幕/参数错/key 无效）：不重试
            raise RuntimeError(f"转写接口失败 HTTP {code}: {body[:200]}")
        last_err = RuntimeError(f"转写接口失败 HTTP {code}: {body[:200]}")
        time.sleep(2)
    raise RuntimeError(f"转写接口失败，重试 3 次未果: {last_err}")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video_id")
    ap.add_argument("--save-dir", default=str(BASE / "transcripts"))
    args = ap.parse_args(argv)
    try:
        key = load_key()
        t = None
        last_err = ""
        # 中文优先，没有中文再要英文；接口对 languages 严格过滤，一次只传一种
        for langs in (["zh"], ["en"]):
            try:
                t = fetch_transcript(args.video_id, key, langs)
                break
            except RuntimeError as e:
                last_err = str(e)
        if t is None:
            raise RuntimeError(last_err or "转写拉取失败")
        save_dir = Path(args.save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
        path = save_dir / f"{args.video_id}.txt"
        path.write_text(t["text"], encoding="utf-8")
        print(
            json.dumps(
                {
                    "ok": True,
                    "path": str(path),
                    "chars": len(t["text"]),
                    "language": t["language"],
                    "language_code": t["language_code"],
                    "is_generated": t["is_generated"],
                },
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
