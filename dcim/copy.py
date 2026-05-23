import shutil
import sys
from pathlib import Path

from dcim.report import fmt_size
from dcim.scanner import VideoRecord


def copy_files(records: list[VideoRecord], dest: Path) -> None:
    """Copy matched records to dest, organized into YYYY/MM/DD subfolders."""
    if not records:
        print("  No files to copy.")
        return

    dest.mkdir(parents=True, exist_ok=True)

    copied = 0
    skipped = 0
    total_bytes = 0

    for i, rec in enumerate(records, 1):
        filename = rec.mtp_name if rec.mtp_name else rec.path.name
        dest_dir = _dest_dir(rec, dest)
        dest_path = dest_dir / filename

        label = f"  [{i}/{len(records)}] {filename} ({fmt_size(rec.size_bytes)})"

        if dest_path.exists() and dest_path.stat().st_size == rec.size_bytes:
            print(f"{label}  →  already exists, skipped")
            skipped += 1
            continue

        dest_dir.mkdir(parents=True, exist_ok=True)

        if rec.mtp_device_id is not None:
            # MTP source — stream directly from device
            print(f"{label}  →  downloading from device...", end="", flush=True)
            try:
                from dcim.mtp import download_file
                download_file(rec.mtp_device_id, rec.mtp_file, dest_path)
                print("  done")
                copied += 1
                total_bytes += rec.size_bytes
            except Exception as e:
                print(f"\n  Error downloading {filename}: {e}", file=sys.stderr)
        else:
            # Local source — regular file copy
            print(f"{label}  →  copying...", end="", flush=True)
            try:
                shutil.copy2(rec.path, dest_path)
                print("  done")
                copied += 1
                total_bytes += rec.size_bytes
            except PermissionError as e:
                print(f"\n  Error: permission denied copying {filename}: {e}", file=sys.stderr)
            except OSError as e:
                print(f"\n  Error copying {filename}: {e}", file=sys.stderr)

    print()
    print(f"  {copied} file(s) copied ({fmt_size(total_bytes)})  ·  {skipped} skipped (already at destination)")
    if copied:
        print(f"  Destination: {dest}")
    print()


def _dest_dir(rec: VideoRecord, dest: Path) -> Path:
    if rec.date_shot:
        return dest / str(rec.date_shot.year) / f"{rec.date_shot.month:02d}" / f"{rec.date_shot.day:02d}"
    return dest / "undated"
