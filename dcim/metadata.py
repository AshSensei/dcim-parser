import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def _require_ffprobe() -> str:
    """Return the ffprobe executable name, or exit with a helpful message."""
    try:
        subprocess.run(
            ["ffprobe", "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        return "ffprobe"
    except FileNotFoundError:
        print(
            "Error: ffprobe not found on PATH.\n"
            "Install ffmpeg (which includes ffprobe) and add it to your PATH.\n"
            "Download: https://ffmpeg.org/download.html",
            file=sys.stderr,
        )
        sys.exit(1)


_FFPROBE_EXE: str | None = None


def _ffprobe_exe() -> str:
    global _FFPROBE_EXE
    if _FFPROBE_EXE is None:
        _FFPROBE_EXE = _require_ffprobe()
    return _FFPROBE_EXE


def read(path: Path) -> dict:
    """
    Run ffprobe on a single video file and return a metadata dict.

    Returns keys: date_shot, duration_seconds, size_bytes, width, height, codec, fps.
    Any field that cannot be determined is None.
    """
    result = subprocess.run(
        [
            _ffprobe_exe(),
            "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            "-show_format",
            str(path),
        ],
        capture_output=True,
        text=True,
    )

    size_bytes = path.stat().st_size

    if result.returncode != 0 or not result.stdout.strip():
        return _empty(size_bytes)

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return _empty(size_bytes)

    fmt = data.get("format", {})
    streams = data.get("streams", [])

    # Pick the first video stream
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)

    date_shot = _parse_date(fmt, streams)
    duration_seconds = _parse_duration(fmt, video_stream)
    width, height = _parse_dimensions(video_stream)
    codec = _parse_codec(video_stream)
    fps = _parse_fps(video_stream)

    return {
        "date_shot": date_shot,
        "duration_seconds": duration_seconds,
        "size_bytes": size_bytes,
        "width": width,
        "height": height,
        "codec": codec,
        "fps": fps,
    }


def _empty(size_bytes: int) -> dict:
    return {
        "date_shot": None,
        "duration_seconds": None,
        "size_bytes": size_bytes,
        "width": None,
        "height": None,
        "codec": None,
        "fps": None,
    }


_DATE_FORMATS = [
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%d %H:%M:%S",
]


def _parse_date(fmt: dict, streams: list) -> datetime | None:
    # iPhone stores shoot date in creation_time tag — check format tags first,
    # then fall back to stream tags. The format-level one is most reliable.
    candidates = []

    tags = fmt.get("tags", {})
    if ct := tags.get("creation_time") or tags.get("com.apple.quicktime.creationdate"):
        candidates.append(ct)

    for s in streams:
        st = s.get("tags", {})
        if ct := st.get("creation_time"):
            candidates.append(ct)

    for raw in candidates:
        for fmt_str in _DATE_FORMATS:
            try:
                dt = datetime.strptime(raw, fmt_str)
                # Strip timezone info so all dates are naive local comparisons
                return dt.replace(tzinfo=None)
            except ValueError:
                continue

    return None


def _parse_duration(fmt: dict, video_stream: dict | None) -> float | None:
    # Prefer format-level duration (most reliable for MOV containers)
    raw = fmt.get("duration")
    if raw is None and video_stream:
        raw = video_stream.get("duration")
    try:
        return float(raw) if raw is not None else None
    except (ValueError, TypeError):
        return None


def _parse_dimensions(video_stream: dict | None) -> tuple[int | None, int | None]:
    if not video_stream:
        return None, None
    w = video_stream.get("width")
    h = video_stream.get("height")
    return (int(w) if w else None, int(h) if h else None)


def _parse_codec(video_stream: dict | None) -> str | None:
    if not video_stream:
        return None
    name = video_stream.get("codec_name", "").lower()
    # Normalise to the values used by --codec filter
    if name in ("hevc", "h265"):
        return "hevc"
    if name in ("h264", "avc"):
        return "h264"
    if name == "prores":
        return "prores"
    return name or None


def _parse_fps(video_stream: dict | None) -> float | None:
    if not video_stream:
        return None
    # r_frame_rate is the container FPS (e.g. "240/1") — use this for --fps
    raw = video_stream.get("r_frame_rate") or video_stream.get("avg_frame_rate")
    if not raw:
        return None
    try:
        if "/" in raw:
            num, den = raw.split("/")
            den = int(den)
            return round(int(num) / den, 3) if den else None
        return float(raw)
    except (ValueError, ZeroDivisionError):
        return None
