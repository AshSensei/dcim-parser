import fnmatch
import sys
from argparse import Namespace
from datetime import date, datetime, timedelta

from dcim.scanner import VideoRecord


# ── Date parsing ─────────────────────────────────────────────────────────────

def parse_date_arg(arg: str) -> tuple[date, date]:
    """
    Parse --date argument into an inclusive (start, end) date range.

    Accepted formats:
        2026-05-18              exact day
        2026-05-01:2026-05-18   date range
        last-7-days             rolling window
        last-weekend            most recent Sat–Sun
    """
    today = date.today()

    if arg == "last-7-days":
        return today - timedelta(days=7), today

    if arg == "last-weekend":
        # Most recent Saturday (weekday 5)
        days_since_sat = (today.weekday() - 5) % 7
        if days_since_sat == 0:
            days_since_sat = 7  # don't return today if today is Saturday
        sat = today - timedelta(days=days_since_sat)
        sun = sat + timedelta(days=1)
        return sat, sun

    if arg == "today":
        return today, today

    if arg == "this-week":
        start = today - timedelta(days=today.weekday())  # Monday
        return start, today

    if ":" in arg:
        parts = arg.split(":", 1)
        try:
            start = date.fromisoformat(parts[0])
            end = date.fromisoformat(parts[1])
            if end < start:
                print(f"Error: date range end ({parts[1]}) is before start ({parts[0]})", file=sys.stderr)
                sys.exit(1)
            return start, end
        except ValueError:
            pass

    try:
        d = date.fromisoformat(arg)
        return d, d
    except ValueError:
        print(
            f"Error: unrecognised --date value '{arg}'.\n"
            "  Use: YYYY-MM-DD | YYYY-MM-DD:YYYY-MM-DD | last-7-days | last-weekend | today | this-week",
            file=sys.stderr,
        )
        sys.exit(1)


# ── Size parsing ──────────────────────────────────────────────────────────────

def parse_size_arg(arg: str) -> int:
    """Parse a size string like '50MB' or '1GB' into bytes."""
    arg = arg.strip()
    units = {"KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
    upper = arg.upper()
    for suffix, factor in units.items():
        if upper.endswith(suffix):
            try:
                return int(float(upper[: -len(suffix)]) * factor)
            except ValueError:
                break
    try:
        return int(arg)
    except ValueError:
        print(f"Error: unrecognised size value '{arg}'. Use e.g. 50MB, 1GB.", file=sys.stderr)
        sys.exit(1)


# ── Duration parsing ──────────────────────────────────────────────────────────

def parse_duration_arg(arg: str) -> float:
    """Parse a duration string like '10s', '2m', '1h' into seconds."""
    arg = arg.strip().lower()
    if arg.endswith("s"):
        try:
            return float(arg[:-1])
        except ValueError:
            pass
    if arg.endswith("m"):
        try:
            return float(arg[:-1]) * 60
        except ValueError:
            pass
    if arg.endswith("h"):
        try:
            return float(arg[:-1]) * 3600
        except ValueError:
            pass
    try:
        return float(arg)
    except ValueError:
        print(f"Error: unrecognised duration value '{arg}'. Use e.g. 10s, 2m, 1h.", file=sys.stderr)
        sys.exit(1)


# ── Resolution helper ─────────────────────────────────────────────────────────

def _matches_resolution(width: int | None, height: int | None, res: str) -> bool:
    if width is None or height is None:
        return False
    long_edge = max(width, height)
    if res == "4k":
        return long_edge >= 3840
    if res == "1080p":
        return 1920 <= long_edge < 3840
    if res == "720p":
        return 1280 <= long_edge < 1920
    return False


# ── Time-of-day parsing ───────────────────────────────────────────────────────

_NAMED_TIMES = {
    "golden-hour": ("05:30", "08:00", "17:00", "20:00"),  # morning + evening windows
    "daytime": ("08:00", "17:00"),
    "night": ("20:00", "05:30"),
}


def _time_in_window(dt: datetime | None, window: str) -> bool:
    if dt is None:
        return False
    t = dt.time()
    if window in _NAMED_TIMES:
        ranges = _NAMED_TIMES[window]
        if len(ranges) == 4:
            # Two windows (golden hour: morning OR evening)
            from datetime import time as dtime
            s1 = dtime.fromisoformat(ranges[0])
            e1 = dtime.fromisoformat(ranges[1])
            s2 = dtime.fromisoformat(ranges[2])
            e2 = dtime.fromisoformat(ranges[3])
            return (s1 <= t <= e1) or (s2 <= t <= e2)
        s = dtime.fromisoformat(ranges[0])
        e = dtime.fromisoformat(ranges[1])
        if s <= e:
            return s <= t <= e
        # overnight window (e.g. night: 20:00–05:30)
        return t >= s or t <= e
    # Raw HH:MM:HH:MM range
    parts = window.split(":")
    if len(parts) == 4:
        from datetime import time as dtime
        try:
            s = dtime(int(parts[0]), int(parts[1]))
            e = dtime(int(parts[2]), int(parts[3]))
            if s <= e:
                return s <= t <= e
            return t >= s or t <= e
        except ValueError:
            pass
    return False


# ── Main filter entry point ───────────────────────────────────────────────────

def apply_filters(records: list[VideoRecord], args: Namespace) -> list[VideoRecord]:
    """Apply all active filters from parsed CLI args. Returns matching records."""
    date_ranges: list[tuple[date, date]] = []
    if args.date:
        for token in args.date.split(","):
            token = token.strip()
            if token:
                date_ranges.append(parse_date_arg(token))

    min_dur = parse_duration_arg(args.min_duration) if args.min_duration else None
    max_dur = parse_duration_arg(args.max_duration) if args.max_duration else None
    min_sz = parse_size_arg(args.min_size) if args.min_size else None
    max_sz = parse_size_arg(args.max_size) if args.max_size else None

    results = []
    no_date_count = 0

    for rec in records:
        # Date filter (OR across multiple ranges)
        if date_ranges:
            if rec.date_shot is None:
                no_date_count += 1
                continue
            shot_date = rec.date_shot.date()
            if not any(r[0] <= shot_date <= r[1] for r in date_ranges):
                continue

        # Duration filter
        if min_dur is not None and (rec.duration_seconds is None or rec.duration_seconds < min_dur):
            continue
        if max_dur is not None and (rec.duration_seconds is None or rec.duration_seconds > max_dur):
            continue

        # Size filter
        if min_sz is not None and rec.size_bytes < min_sz:
            continue
        if max_sz is not None and rec.size_bytes > max_sz:
            continue

        # Resolution filter
        if args.resolution and not _matches_resolution(rec.width, rec.height, args.resolution):
            continue

        # Codec filter
        if args.codec and (rec.codec or "").lower() != args.codec.lower():
            continue

        # FPS filter (within ±1 fps tolerance)
        if args.fps is not None:
            if rec.fps is None or abs(rec.fps - args.fps) > 1:
                continue

        # Filename pattern filter
        if args.name and not fnmatch.fnmatch(rec.path.name, args.name):
            continue

        results.append(rec)

    if no_date_count and date_ranges:
        print(f"  Note: {no_date_count} file(s) had no date metadata and were excluded.")

    return results
