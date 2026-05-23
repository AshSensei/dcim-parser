import argparse
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dcim",
        description="Extract and organize iPhone videos from a USB-mounted DCIM folder.",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # ── extract ──────────────────────────────────────────────────────────────
    extract = sub.add_parser("extract", help="Filter and copy video clips")
    extract.add_argument("--source", metavar="PATH",
                         help="Local path (e.g. D:\\) or omit to auto-detect connected device")
    extract.add_argument("--dest", metavar="PATH",
                         help="Destination folder for copied clips (required unless --dry-run)")
    extract.add_argument("--date", metavar="DATE",
                         help="Date or range: 2026-05-18 | 2026-05-01:2026-05-18 | last-7-days | last-weekend | comma-separated: 2026-05-03,2026-05-13")
    extract.add_argument("--min-duration", metavar="DURATION",
                         help="Minimum clip length, e.g. 10s or 2m")
    extract.add_argument("--max-duration", metavar="DURATION",
                         help="Maximum clip length, e.g. 2m")
    extract.add_argument("--min-size", metavar="SIZE",
                         help="Minimum file size, e.g. 50MB")
    extract.add_argument("--max-size", metavar="SIZE",
                         help="Maximum file size, e.g. 500MB")
    extract.add_argument("--resolution", metavar="RES",
                         choices=["4k", "1080p", "720p"],
                         help="Resolution: 4k | 1080p | 720p")
    extract.add_argument("--codec", metavar="CODEC",
                         help="Codec: hevc | h264 | prores")
    extract.add_argument("--fps", metavar="FPS", type=float,
                         help="Frame rate, e.g. 240 | 120 | 30")
    extract.add_argument("--name", metavar="PATTERN",
                         help="Glob pattern on filename, e.g. \"IMG_42*\"")
    extract.add_argument("--flat", action="store_true",
                         help="Copy all files directly into --dest with no date subfolders")
    extract.add_argument("--dry-run", action="store_true",
                         help="Preview matched files without copying")

    # ── scan ─────────────────────────────────────────────────────────────────
    scan = sub.add_parser("scan", help="Print a timeline of all footage on the device")
    scan.add_argument("--source", metavar="PATH",
                      help="Local path or omit to auto-detect connected device")

    # ── report ───────────────────────────────────────────────────────────────
    report = sub.add_parser("report", help="Show top N clips by size or duration")
    report.add_argument("--source", metavar="PATH",
                        help="Local path or omit to auto-detect connected device")
    report.add_argument("--top", type=int, default=10, metavar="N",
                        help="Number of clips to show (default: 10)")
    report.add_argument("--sort", choices=["size", "duration"], default="size",
                        help="Sort by size or duration (default: size)")

    return parser


def _resolve_source(source_arg: str | None) -> tuple[Path | None, str | None, str | None]:
    """
    Resolve --source to either a local path or an MTP device.

    Returns:
        (local_path, mtp_device_id, mtp_friendly_name)
        Exactly one of local_path or (mtp_device_id, mtp_friendly_name) will be set.
    """
    if source_arg is not None:
        p = Path(source_arg)
        if p.exists():
            return p, None, None

    # Not a valid local path — try MTP auto-detection
    from dcim.scanner import pick_mtp_device
    device = pick_mtp_device()
    if device is None:
        if source_arg:
            print(f"Error: path does not exist and no MTP device found: {source_arg}", file=sys.stderr)
        else:
            print("Error: no --source given and no MTP device detected.", file=sys.stderr)
        sys.exit(1)

    device_id, friendly_name = device
    return None, device_id, friendly_name


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if args.command == "extract":
        _cmd_extract(args, parser)
    elif args.command == "scan":
        _cmd_scan(args)
    elif args.command == "report":
        _cmd_report(args)


def _cmd_extract(args, parser) -> None:
    if not args.dry_run and not args.dest:
        parser.error("--dest is required unless --dry-run is set")

    local_path, mtp_id, mtp_name = _resolve_source(args.source)

    from dcim import scanner, filters, report, copy

    if local_path:
        print(f"\n  Scanning {local_path} ...")
        records = scanner.scan(local_path)
    else:
        print(f"\n  Scanning {mtp_name} via MTP ...")
        records = scanner.scan_mtp(mtp_id, mtp_name)

    if not records:
        sys.exit(0)

    print(f"  {len(records)} video(s) found. Applying filters...")
    matched = filters.apply_filters(records, args)

    if args.dry_run:
        report.print_dry_run(matched)
    else:
        dest = Path(args.dest)
        if matched:
            print(f"\n  {len(matched)} file(s) matched. Copying to {dest} ...\n")
        copy.copy_files(matched, dest, flat=args.flat)


def _cmd_scan(args) -> None:
    local_path, mtp_id, mtp_name = _resolve_source(args.source)

    from dcim import scanner, report

    if local_path:
        print(f"\n  Scanning {local_path} ...")
        records = scanner.scan(local_path)
    else:
        print(f"\n  Scanning {mtp_name} via MTP ...")
        records = scanner.scan_mtp(mtp_id, mtp_name)

    report.print_scan_timeline(records)


def _cmd_report(args) -> None:
    local_path, mtp_id, mtp_name = _resolve_source(args.source)

    from dcim import scanner, report

    if local_path:
        print(f"\n  Scanning {local_path} ...")
        records = scanner.scan(local_path)
    else:
        print(f"\n  Scanning {mtp_name} via MTP ...")
        records = scanner.scan_mtp(mtp_id, mtp_name)

    report.print_top_n(records, top=args.top, sort_by=args.sort)
