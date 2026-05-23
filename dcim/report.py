from datetime import datetime
from pathlib import Path

from dcim.scanner import VideoRecord


# ── Formatting helpers ────────────────────────────────────────────────────────

def fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


def fmt_size(size_bytes: int) -> str:
    mb = size_bytes / (1024 ** 2)
    if mb >= 1024:
        return f"{mb / 1024:.1f} GB"
    return f"{mb:.0f} MB"


def fmt_resolution(width: int | None, height: int | None) -> str:
    if width is None or height is None:
        return "—"
    long_edge = max(width, height)
    if long_edge >= 3840:
        return f"4K ({width}×{height})"
    if long_edge >= 1920:
        return f"1080p ({width}×{height})"
    if long_edge >= 1280:
        return f"720p ({width}×{height})"
    return f"{width}×{height}"


def fmt_date(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    return dt.strftime("%Y-%m-%d %H:%M")


# ── Dry-run table ─────────────────────────────────────────────────────────────

def print_dry_run(records: list[VideoRecord]) -> None:
    if not records:
        print("\n  No files matched the given filters.")
        print("  Try broadening --date, removing --min-duration, or checking --resolution.\n")
        return

    # Column headers and widths
    headers = ["#", "Filename", "Date Shot", "Duration", "Size", "Resolution"]
    rows = []
    for i, rec in enumerate(records, 1):
        rows.append([
            str(i),
            rec.path.name,
            fmt_date(rec.date_shot),
            fmt_duration(rec.duration_seconds),
            fmt_size(rec.size_bytes),
            fmt_resolution(rec.width, rec.height),
        ])

    col_widths = [len(h) for h in headers]
    for row in rows:
        for j, cell in enumerate(row):
            col_widths[j] = max(col_widths[j], len(cell))

    sep = "  " + "  ".join("-" * w for w in col_widths)
    header_line = "  " + "  ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))

    print()
    print(header_line)
    print(sep)
    for row in rows:
        print("  " + "  ".join(cell.ljust(col_widths[j]) for j, cell in enumerate(row)))

    total_bytes = sum(r.size_bytes for r in records)
    total_dur = sum(r.duration_seconds for r in records if r.duration_seconds)
    print()
    print(f"  {len(records)} file(s) matched  ·  {fmt_size(total_bytes)} total  ·  {fmt_duration(total_dur)} total duration")
    print("  Dry run — nothing copied.\n")


# ── Top-N report ──────────────────────────────────────────────────────────────

def print_top_n(records: list[VideoRecord], top: int, sort_by: str) -> None:
    if not records:
        print("\n  No video files found.\n")
        return

    if sort_by == "duration":
        sorted_records = sorted(
            records, key=lambda r: r.duration_seconds or 0, reverse=True
        )
        sort_label = "Duration"
    else:
        sorted_records = sorted(records, key=lambda r: r.size_bytes, reverse=True)
        sort_label = "Size"

    top_records = sorted_records[:top]

    headers = ["#", "Filename", "Date Shot", "Duration", "Size", "Resolution"]
    rows = []
    for i, rec in enumerate(top_records, 1):
        rows.append([
            str(i),
            rec.path.name,
            fmt_date(rec.date_shot),
            fmt_duration(rec.duration_seconds),
            fmt_size(rec.size_bytes),
            fmt_resolution(rec.width, rec.height),
        ])

    col_widths = [len(h) for h in headers]
    for row in rows:
        for j, cell in enumerate(row):
            col_widths[j] = max(col_widths[j], len(cell))

    sep = "  " + "  ".join("-" * w for w in col_widths)
    header_line = "  " + "  ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))

    print(f"\n  Top {top} clips by {sort_label.lower()}\n")
    print(header_line)
    print(sep)
    for row in rows:
        print("  " + "  ".join(cell.ljust(col_widths[j]) for j, cell in enumerate(row)))
    print()


# ── Scan timeline ─────────────────────────────────────────────────────────────

def print_scan_timeline(records: list[VideoRecord]) -> None:
    if not records:
        print("\n  No video files found.\n")
        return

    from collections import defaultdict
    by_day: dict[str, list[VideoRecord]] = defaultdict(list)
    no_date = []

    for rec in records:
        if rec.date_shot:
            key = rec.date_shot.strftime("%Y-%m-%d")
            by_day[key].append(rec)
        else:
            no_date.append(rec)

    bar_max = 20
    max_clips = max((len(v) for v in by_day.values()), default=1)

    print(f"\n  {len(records)} video(s) found on device\n")
    for day in sorted(by_day):
        day_records = by_day[day]
        total_bytes = sum(r.size_bytes for r in day_records)
        total_dur = sum(r.duration_seconds for r in day_records if r.duration_seconds)
        bar_len = max(1, round(len(day_records) / max_clips * bar_max))
        bar = "#" * bar_len
        print(
            f"  {day}  {bar:<{bar_max}}  "
            f"{len(day_records):>3} clip(s)  |  "
            f"{fmt_duration(total_dur)}  |  "
            f"{fmt_size(total_bytes)}"
        )

    if no_date:
        print(f"\n  {len(no_date)} file(s) had no date metadata.")
    print()
