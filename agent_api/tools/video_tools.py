"""视频工具：抽帧 / 探测 / 抽音轨。

依赖系统的 ffmpeg（Termux: pkg install ffmpeg；Ubuntu: apt install ffmpeg）。
没装就如实报错，不会假装抽成功。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..config import settings
from ..errors import ToolError
from ..security import active_root
from ..security import display_path, resolve_in_sandbox
from .base import ToolContext, ToolRegistry

_PROBE_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "视频路径（相对 sandbox_root），例如 uploads/a.mp4"},
    },
    "required": ["path"],
}

_FRAMES_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "视频路径（相对 sandbox_root）"},
        "count": {"type": "integer", "description": "一共抽几帧，1-60，默认 6"},
        "start": {"type": "number", "description": "从第几秒开始，默认 0"},
        "interval": {"type": "number", "description": "每隔几秒抽一帧；填了就忽略 count"},
        "width": {"type": "integer", "description": "缩放宽度（高度按比例），默认保持原尺寸"},
        "out": {"type": "string", "description": "输出目录（相对 sandbox_root），默认 frames/"},
    },
    "required": ["path"],
}

_AUDIO_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "视频路径（相对 sandbox_root）"},
        "format": {"type": "string", "description": "mp3 或 wav，默认 mp3"},
    },
    "required": ["path"],
}


def _ensure(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise ToolError(
            f"系统里没有 {name}。Termux 先装：pkg install ffmpeg（Ubuntu 用 apt install ffmpeg），装完再试。"
        )
    return path


def _exec(cmd: list[str], timeout: float = 180.0) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(active_root()),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            start_new_session=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"ffmpeg 超时（{timeout}s）：{exc}") from exc
    except OSError as exc:
        raise ToolError(f"ffmpeg 起不来：{exc}") from exc
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _probe(src: Path) -> dict[str, Any]:
    code, out, err = _exec(
        [
            _ensure("ffprobe"),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(src),
        ],
        60.0,
    )
    if code != 0:
        raise ToolError("ffprobe 读不了这个文件：" + (err or out)[-300:])
    try:
        return json.loads(out or "{}")
    except json.JSONDecodeError as exc:
        raise ToolError(f"ffprobe 输出不是 JSON：{exc}") from exc


def _summary(info: dict[str, Any]) -> str:
    fmt = info.get("format") or {}
    video = next((s for s in (info.get("streams") or []) if s.get("codec_type") == "video"), {})
    audio = next((s for s in (info.get("streams") or []) if s.get("codec_type") == "audio"), {})
    bits = []
    if video:
        bits.append(f"{video.get('width')}x{video.get('height')}")
        if video.get("r_frame_rate"):
            bits.append(f"{video.get('r_frame_rate')} fps")
        if video.get("codec_name"):
            bits.append(str(video.get("codec_name")))
    if audio:
        bits.append("audio:" + str(audio.get("codec_name") or "?"))
    if fmt.get("duration"):
        bits.append(f"{float(fmt['duration']):.1f}s")
    if fmt.get("size"):
        bits.append(f"{int(fmt['size']) / 1024 / 1024:.1f}MB")
    return " · ".join(bits)


def register(registry: ToolRegistry) -> None:
    def video_probe(ctx: ToolContext, args: dict[str, Any]) -> str:
        src = resolve_in_sandbox(str(args.get("path") or ""))
        if not src.exists():
            raise ToolError(f"文件不存在：{display_path(src)}")
        info = _probe(src)
        return (
            f"{display_path(src)}\n{_summary(info)}\n\n原始 ffprobe JSON：\n"
            f"{json.dumps(info, ensure_ascii=False)[:1500]}"
        )

    def video_frames(ctx: ToolContext, args: dict[str, Any]) -> str:
        src = resolve_in_sandbox(str(args.get("path") or ""))
        if not src.exists():
            raise ToolError(f"文件不存在：{display_path(src)}")
        count = max(1, min(60, int(args.get("count") or 6)))
        start = max(0.0, float(args.get("start") or 0))
        interval = float(args.get("interval") or 0)
        width = int(args.get("width") or 0)
        out_rel = str(args.get("out") or "frames").strip().strip("/") or "frames"
        out_dir = resolve_in_sandbox(out_rel)
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = src.stem
        for old in out_dir.glob(f"{stem}-*.jpg"):
            try:
                old.unlink()
            except OSError:
                pass

        info = _probe(src)
        duration = 0.0
        try:
            duration = float((info.get("format") or {}).get("duration") or 0)
        except (TypeError, ValueError):
            duration = 0.0

        vf = []
        if interval > 0:
            vf.append(f"fps=1/{interval:g}")
        else:
            span = max(duration - start, 0.0) if duration > 0 else 0.0
            if span > 0:
                fps = max(min(count / span, 25.0), 0.01)
                vf.append(f"fps={fps:.6f}")
            else:
                vf.append(f"fps=1/{max(1, count):d}")
        if width > 0:
            vf.append(f"scale={width}:-2")

        cmd = [_ensure("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error"]
        if start > 0:
            cmd += ["-ss", f"{start:g}"]
        cmd += ["-i", str(src), "-vf", ",".join(vf), "-frames:v", str(count), "-q:v", "3"]
        cmd.append(str(out_dir / f"{stem}-%03d.jpg"))

        code, out, err = _exec(cmd, 240.0)
        made = sorted(out_dir.glob(f"{stem}-*.jpg"))
        if code != 0 or not made:
            raise ToolError("抽帧失败：" + (err or out or "ffmpeg 没产出文件")[-400:])

        lines = [f"从 {display_path(src)}（{_summary(info)}）抽了 {len(made)} 帧，目录 {display_path(out_dir)}："]
        for one in made[:12]:
            lines.append(f"- ![{one.name}](/files/{display_path(one)})")
        if len(made) > 12:
            lines.append(f"- …（还有 {len(made) - 12} 张）")
        lines.append("\n要我看图的话，把这些帧作为附件发过来（网页粘贴图片，或 /api/chat 带 attachments）。")
        return "\n".join(lines)

    def video_audio(ctx: ToolContext, args: dict[str, Any]) -> str:
        src = resolve_in_sandbox(str(args.get("path") or ""))
        if not src.exists():
            raise ToolError(f"文件不存在：{display_path(src)}")
        fmt = str(args.get("format") or "mp3").lower()
        if fmt not in {"mp3", "wav", "m4a"}:
            raise ToolError("format 只能 mp3 / wav / m4a")
        info = _probe(src)
        has_audio = any(s.get("codec_type") == "audio" for s in (info.get("streams") or []))
        if not has_audio:
            raise ToolError("这个文件里没有音轨，抽不出来（先 video_probe 看一眼）")
        out_dir = resolve_in_sandbox("media")
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"{src.stem}.{fmt}"
        code, out, err = _exec(
            [_ensure("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-vn", str(target)],
            240.0,
        )
        if code != 0 or not target.exists():
            raise ToolError("抽音轨失败：" + (err or out)[-400:])
        rel = display_path(target)
        return f"音轨已导出：{rel}（{target.stat().st_size / 1024:.0f} KB）\nMEDIA_AUDIO: /files/{rel}"

    registry.register(
        "video_probe",
        "读视频的时长/分辨率/帧率/编码，先看这一个再决定怎么处理。",
        _PROBE_PARAMS,
        video_probe,
    )
    registry.register(
        "video_frames",
        "给视频抽帧（默认 6 张，可定时长/间隔/缩放），产出图片可当附件发给模型看。",
        _FRAMES_PARAMS,
        video_frames,
    )
    registry.register(
        "video_audio",
        "把视频里的音轨导成 mp3/wav/m4a。",
        _AUDIO_PARAMS,
        video_audio,
    )
