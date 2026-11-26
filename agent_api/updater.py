"""GitHub 更新检查：读 commits.atom（不依赖 GitHub API 的 60 次/小时限额）。

本地版本用 git HEAD 判断；目录不是 git 仓库时不瞎猜，直接告诉用户要重新 clone。
自动更新只做 git pull --ff-only；本地有未提交改动就拒绝，避免把用户代码覆盖掉。
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .errors import AppError

ROOT = Path(__file__).resolve().parent.parent
REPO = (os.getenv("UPDATE_REPO") or "cvcxx000/agent-api").strip()
BRANCH = (os.getenv("UPDATE_BRANCH") or "main").strip()
FEED_URL = (os.getenv("UPDATE_FEED_URL") or f"https://github.com/{REPO}/commits/{BRANCH}.atom").strip()
CHECK_SECONDS = max(60, int(os.getenv("UPDATE_CHECK_SECONDS") or 600))
_ATOM = "{http://www.w3.org/2005/Atom}"
_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "data": None}


def _git(*args: str, timeout: float = 20.0) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(ROOT), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        return proc.returncode, (proc.stdout or "").strip() or (proc.stderr or "").strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def local_state() -> dict[str, Any]:
    if not (ROOT / ".git").exists():
        return {"git": False, "sha": "", "short": "", "subject": "", "branch": "", "dirty": False}
    code, sha = _git("rev-parse", "HEAD")
    if code != 0 or not sha:
        return {"git": False, "sha": "", "short": "", "subject": "", "branch": "", "dirty": False}
    code, short = _git("rev-parse", "--short", "HEAD")
    _, subject = _git("log", "-1", "--format=%s")
    _, branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    _, status = _git("status", "--porcelain", "--untracked-files=no")
    return {
        "git": True,
        "sha": sha,
        "short": short if code == 0 else sha[:7],
        "subject": subject,
        "branch": branch,
        "dirty": bool(status),
    }


def _parse_feed(text: str) -> list[dict[str, str]]:
    root = ET.fromstring(text)
    entries: list[dict[str, str]] = []
    for entry in root.findall(f"{_ATOM}entry"):
        sha = ""
        raw_id = entry.findtext(f"{_ATOM}id") or ""
        if "Grit::Commit/" in raw_id:
            sha = raw_id.rsplit("Grit::Commit/", 1)[-1]
        else:
            sha = raw_id.rsplit("/", 1)[-1]
        title = " ".join((entry.findtext(f"{_ATOM}title") or "").split())
        updated = (entry.findtext(f"{_ATOM}updated") or "").strip()
        link = ""
        for tag in entry.findall(f"{_ATOM}link"):
            if tag.get("rel") == "alternate":
                link = tag.get("href") or ""
                break
        if sha and title:
            entries.append({"sha": sha, "short": sha[:7], "title": title, "updated": updated, "url": link})
    return entries


def _fetch_feed() -> list[dict[str, str]]:
    request = urllib.request.Request(
        FEED_URL,
        headers={"User-Agent": "agent-api-updater", "Accept": "application/atom+xml"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        text = response.read().decode("utf-8", "replace")
    return _parse_feed(text)


def _summary(commits: list[dict[str, str]], truncated: bool) -> str:
    lines = [f"• {c['title']}" for c in commits[:5]]
    if len(commits) > 5:
        lines.append(f"…还有 {len(commits) - 5} 个提交")
    if truncated:
        lines.append("（本地版本比订阅源还旧，先按远端最近几个提交概括）")
    return "\n".join(lines)


def check(force: bool = False) -> dict[str, Any]:
    now = time.time()
    with _lock:
        cached = _cache.get("data")
        if not force and cached is not None and now - float(_cache.get("at") or 0) < CHECK_SECONDS:
            return dict(cached)

    local = local_state()
    base: dict[str, Any] = {
        "ok": True,
        "available": False,
        "can_apply": False,
        "git": local["git"],
        "current": local,
        "checked_at": int(now),
        "feed": FEED_URL,
        "branch": BRANCH,
    }
    if not local["git"]:
        base["reason"] = "当前目录不是 git 仓库（zip 安装），没法自动比较和更新；重新 clone 一次即可。"
        return base

    try:
        feed = _fetch_feed()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ET.ParseError, OSError) as exc:
        return {**base, "ok": False, "error": f"检查 GitHub 失败：{type(exc).__name__}: {exc}"}

    if not feed:
        return {**base, "ok": False, "error": "GitHub 订阅源里没有提交"}

    remote = feed[0]
    base["remote"] = remote
    base["available"] = remote["sha"] != local["sha"]
    if not base["available"]:
        base["reason"] = "已经是最新版"
        with _lock:
            _cache["at"] = now
            _cache["data"] = dict(base)
        return base

    index = next((i for i, c in enumerate(feed) if c["sha"] == local["sha"]), -1)
    if index >= 0:
        commits = feed[:index]
        truncated = False
    else:
        commits = feed
        truncated = True
    base["commits"] = commits
    base["behind"] = None if truncated else len(commits)
    base["truncated"] = truncated
    base["summary"] = _summary(commits, truncated)
    base["can_apply"] = not local["dirty"] and local["branch"] not in ("", "HEAD")
    if local["dirty"]:
        base["reason"] = "本地有未提交改动，先 commit 或 stash 再更新。"
    elif base["can_apply"]:
        base["reason"] = ""
    else:
        base["reason"] = "当前不在分支上（detached HEAD），请手动切回分支再更新。"

    with _lock:
        _cache["at"] = now
        _cache["data"] = dict(base)
    return dict(base)


def apply() -> dict[str, Any]:
    data = check(force=True)
    if not data.get("ok"):
        raise AppError(data.get("error") or "检查更新失败")
    if not data.get("available"):
        return {"ok": True, "updated": False, "message": "已经是最新版"}
    if not data.get("can_apply"):
        raise AppError(data.get("reason") or "当前不能自动更新")
    before = data["current"]["sha"]
    code, output = _git("pull", "--ff-only", "origin", BRANCH, timeout=90)
    if code != 0:
        raise AppError(f"git pull 失败：{output[-1200:]}")
    after = local_state()
    fresh = check(force=True)
    return {
        "ok": True,
        "updated": after["sha"] != before,
        "restart": True,
        "current": after,
        "commits": fresh.get("commits") or [],
        "summary": fresh.get("summary") or data.get("summary") or "",
        "message": f"已更新到 {after['short']}，重启 python3 run.py 后生效。",
        "output": output[-1200:],
    }
