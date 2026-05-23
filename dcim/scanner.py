import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from dcim import metadata

VIDEO_EXTENSIONS = {".mov", ".mp4", ".m4v"}


@dataclass
class VideoRecord:
    path: Path | None           # None for MTP sources (no local path until copied)
    date_shot: datetime | None
    duration_seconds: float | None
    size_bytes: int
    width: int | None
    height: int | None
    codec: str | None
    fps: float | None
    # MTP-specific fields (None for local sources)
    mtp_device_id: str | None = None
    mtp_file: object | None = None   # dcim.mtp.MtpFile when scanning via MTP
    mtp_name: str | None = None      # original filename on device


def scan(source: Path) -> list[VideoRecord]:
    """Walk source recursively, read metadata for every video file found."""
    if not source.exists():
        print(f"Error: source path does not exist: {source}", file=sys.stderr)
        sys.exit(1)

    video_files = [
        p for p in source.rglob("*")
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    ]

    if not video_files:
        print("No video files found in source.", file=sys.stderr)
        return []

    records: list[VideoRecord] = []
    stub_count = 0

    for i, path in enumerate(video_files, 1):
        print(f"\r  Scanning {i}/{len(video_files)}: {path.name:<40}", end="", flush=True)

        if path.stat().st_size == 0:
            stub_count += 1
            print(f"\n  Warning: skipping 0-byte iCloud stub: {path.name}", file=sys.stderr)
            continue

        meta = metadata.read(path)
        records.append(VideoRecord(
            path=path,
            date_shot=meta["date_shot"],
            duration_seconds=meta["duration_seconds"],
            size_bytes=meta["size_bytes"],
            width=meta["width"],
            height=meta["height"],
            codec=meta["codec"],
            fps=meta["fps"],
        ))

    print()

    if stub_count:
        print(f"  {stub_count} iCloud stub file(s) skipped (0 bytes — download from iCloud first).")

    return records


def scan_mtp(device_id: str, friendly_name: str) -> list[VideoRecord]:
    """
    Scan an MTP device (e.g. iPhone connected via USB) for video files.

    MTP metadata only gives us name, size, and date_modified.
    Duration, resolution, and codec will be populated after download via ffprobe.
    """
    from dcim.mtp import scan_device

    mtp_files = scan_device(device_id, friendly_name)

    if not mtp_files:
        print("No video files found on device.", file=sys.stderr)
        return []

    stub_count = sum(1 for f in mtp_files if f.size_bytes == 0)
    if stub_count:
        print(f"  Warning: {stub_count} file(s) appear to be 0-byte iCloud stubs — they will be skipped during copy.")

    records = []
    for f in mtp_files:
        if f.size_bytes == 0:
            continue
        records.append(VideoRecord(
            path=None,
            date_shot=f.date_modified,
            duration_seconds=None,   # available after download + ffprobe
            size_bytes=f.size_bytes,
            width=None,
            height=None,
            codec=None,
            fps=None,
            mtp_device_id=device_id,
            mtp_file=f,
            mtp_name=f.name,
        ))

    return records


def pick_mtp_device() -> tuple[str, str] | None:
    """
    Auto-detect a connected MTP device.
    Returns (device_id, friendly_name), or None if no device found.
    Exits with a message if multiple devices are connected.
    """
    from dcim.mtp import list_devices
    devices = list_devices()
    if not devices:
        return None
    if len(devices) == 1:
        return devices[0]
    print("Multiple MTP devices connected. Please specify which device to use:")
    for i, (_, name) in enumerate(devices):
        print(f"  [{i + 1}] {name}")
    print("(Auto-selection not yet supported — connect only the target device.)", file=sys.stderr)
    sys.exit(1)
