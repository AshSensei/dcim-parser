"""
Windows Portable Devices (WPD) MTP access layer.

Provides device enumeration and DCIM file streaming from an iPhone (or any
MTP device) connected via USB — no Apple software required.

Adapted from the WPD COM API patterns in Heribert17/mtp and KasparNagu/PortableDevices.
"""

import ctypes
import io
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta

try:
    import comtypes
    import comtypes.client
    import comtypes.automation
except ImportError:
    print(
        "Error: comtypes is required for MTP device access.\n"
        "Install it with: pip install comtypes",
        file=sys.stderr,
    )
    sys.exit(1)

# Generate COM type wrappers from Windows DLLs (cached after first run)
comtypes.client.GetModule("portabledeviceapi.dll")
comtypes.client.GetModule("portabledevicetypes.dll")

import comtypes.gen.PortableDeviceApiLib as _port
import comtypes.gen.PortableDeviceTypesLib as _types

# ── WPD property key constants ────────────────────────────────────────────────

def _prop(guid: str, pid: int):
    p = comtypes.pointer(_port._tagpropertykey())
    p.contents.fmtid = comtypes.GUID(guid)
    p.contents.pid = pid
    return p

WPD_RESOURCE_DEFAULT          = _prop("{E81E79BE-34F0-41BF-B53F-F1A06AE87842}", 0)
WPD_OBJECT_NAME               = _prop("{EF6B490D-5CD8-437A-AFFC-DA8B60EE4A3C}", 4)
WPD_OBJECT_PARENT_ID          = _prop("{EF6B490D-5CD8-437A-AFFC-DA8B60EE4A3C}", 3)
WPD_OBJECT_CONTENT_TYPE       = _prop("{EF6B490D-5CD8-437A-AFFC-DA8B60EE4A3C}", 7)
WPD_OBJECT_SIZE               = _prop("{EF6B490D-5CD8-437A-AFFC-DA8B60EE4A3C}", 11)
WPD_OBJECT_ORIGINAL_FILE_NAME = _prop("{EF6B490D-5CD8-437A-AFFC-DA8B60EE4A3C}", 12)
WPD_OBJECT_DATE_CREATED       = _prop("{EF6B490D-5CD8-437A-AFFC-DA8B60EE4A3C}", 18)
WPD_OBJECT_DATE_MODIFIED      = _prop("{EF6B490D-5CD8-437A-AFFC-DA8B60EE4A3C}", 19)
WPD_CONTENT_TYPE_FOLDER_GUID  = comtypes.GUID("{27E2E392-A111-48E0-AB0C-E17705A05F85}")

_FOLDER_TYPE  = "{27E2E392-A111-48E0-AB0C-E17705A05F85}"
_STORAGE_TYPES = {
    "{23F05BBC-15DE-4C2A-A55B-A9AF5CE412EF}",
    "{99ED0160-17FF-4C44-9D98-1D7A6F941921}",
}

VIDEO_EXTENSIONS = {".mov", ".mp4", ".m4v"}

# ── Date conversion ───────────────────────────────────────────────────────────

_EPOCH_OFFSET = (datetime(1970, 1, 1) - datetime(1899, 12, 30)).days

def _ole_date_to_datetime(ole_float: float) -> datetime:
    """Convert OLE Automation date (float days since 1899-12-30) to datetime."""
    days = int(abs(ole_float))
    frac = abs(ole_float) - days
    days_since_epoch = days - _EPOCH_OFFSET
    hours = frac * 24
    minutes = (hours - int(hours)) * 60
    seconds = (minutes - int(minutes)) * 60
    ms = round((seconds - int(seconds)) * 1000)
    return datetime(1970, 1, 1) + timedelta(
        days=days_since_epoch,
        hours=int(hours),
        minutes=int(minutes),
        seconds=int(seconds),
        milliseconds=ms,
    )

# ── MTP file record ───────────────────────────────────────────────────────────

@dataclass
class MtpFile:
    object_id: str
    name: str
    size_bytes: int
    date_modified: datetime | None
    folder_path: str   # logical path on device, e.g. "Internal Storage/DCIM/101APPLE"


# ── Low-level WPD helpers ─────────────────────────────────────────────────────

def _make_key_collection(*props) -> _types.PortableDeviceKeyCollection:
    col = comtypes.client.CreateObject(
        _types.PortableDeviceKeyCollection,
        clsctx=comtypes.CLSCTX_INPROC_SERVER,
        interface=_port.IPortableDeviceKeyCollection,
    )
    for p in props:
        col.Add(p)
    return col


def _get_string(values, prop) -> str | None:
    try:
        return str(values.GetStringValue(prop))
    except comtypes.COMError:
        return None


def _get_uint64(values, prop) -> int | None:
    try:
        return int(values.GetUnsignedLargeIntegerValue(prop))
    except comtypes.COMError:
        return None


def _get_guid(values, prop) -> str | None:
    try:
        return str(values.GetGuidValue(prop)).upper()
    except comtypes.COMError:
        return None


def _get_ole_date(values, prop) -> datetime | None:
    try:
        variant = values.GetValue(prop)
        raw = getattr(
            variant,
            "__MIDL____MIDL_itf_PortableDeviceApi_0001_00000001",
        ).date
        return _ole_date_to_datetime(float(raw))
    except (comtypes.COMError, AttributeError, TypeError):
        return None


# ── Device-level API ──────────────────────────────────────────────────────────

_KEY_COLLECTION = None   # lazily built once

def _props_to_read():
    global _KEY_COLLECTION
    if _KEY_COLLECTION is None:
        _KEY_COLLECTION = _make_key_collection(
            WPD_OBJECT_NAME,
            WPD_OBJECT_ORIGINAL_FILE_NAME,
            WPD_OBJECT_CONTENT_TYPE,
            WPD_OBJECT_SIZE,
            WPD_OBJECT_DATE_CREATED,
            WPD_OBJECT_DATE_MODIFIED,
        )
    return _KEY_COLLECTION


def _enum_children(content, object_id: str):
    """Yield object_ids that are direct children of object_id."""
    enum = content.EnumObjects(
        ctypes.c_ulong(0),
        object_id,
        ctypes.POINTER(_port.IPortableDeviceValues)(),
    )
    while True:
        num = ctypes.pointer(ctypes.c_ulong(0))
        ids = enum.Next(1, num)
        if num.contents.value == 0:
            break
        yield str(ids[0])


def _walk_for_videos(content, properties, object_id: str, folder_path: str):
    """
    Recursively walk MTP content tree from object_id.
    Yields MtpFile for every video file found.
    """
    try:
        children = list(_enum_children(content, object_id))
    except comtypes.COMError:
        return

    for child_id in children:
        try:
            vals = properties.GetValues(child_id, _props_to_read())
        except comtypes.COMError:
            continue

        content_type_guid = _get_guid(vals, WPD_OBJECT_CONTENT_TYPE) or ""
        name = _get_string(vals, WPD_OBJECT_ORIGINAL_FILE_NAME) or _get_string(vals, WPD_OBJECT_NAME) or ""

        is_folder = content_type_guid in _STORAGE_TYPES or content_type_guid == _FOLDER_TYPE.upper()

        if is_folder:
            child_path = f"{folder_path}/{name}" if name else folder_path
            yield from _walk_for_videos(content, properties, child_id, child_path)
        else:
            ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
            if ext in VIDEO_EXTENSIONS:
                size = _get_uint64(vals, WPD_OBJECT_SIZE) or 0
                date = _get_ole_date(vals, WPD_OBJECT_DATE_CREATED) or _get_ole_date(vals, WPD_OBJECT_DATE_MODIFIED)
                yield MtpFile(
                    object_id=child_id,
                    name=name,
                    size_bytes=size,
                    date_modified=date,
                    folder_path=folder_path,
                )

        vals.Clear()


# ── Public API ────────────────────────────────────────────────────────────────

def list_devices() -> list[tuple[str, str]]:
    """
    Return list of (device_id, friendly_name) for all connected MTP devices.
    """
    comtypes.CoInitialize()
    manager = comtypes.client.CreateObject(
        _port.PortableDeviceManager,
        clsctx=comtypes.CLSCTX_INPROC_SERVER,
        interface=_port.IPortableDeviceManager,
    )
    count = ctypes.pointer(ctypes.c_ulong(0))
    manager.GetDevices(ctypes.POINTER(ctypes.c_wchar_p)(), count)
    if count.contents.value == 0:
        return []

    ids = (ctypes.c_wchar_p * count.contents.value)()
    manager.GetDevices(ctypes.cast(ids, ctypes.POINTER(ctypes.c_wchar_p)), count)

    result = []
    for dev_id in ids:
        if dev_id is None:
            continue
        name_len = ctypes.pointer(ctypes.c_ulong(0))
        try:
            manager.GetDeviceFriendlyName(dev_id, ctypes.POINTER(ctypes.c_ushort)(), name_len)
            buf = ctypes.create_unicode_buffer(name_len.contents.value)
            manager.GetDeviceFriendlyName(dev_id, ctypes.cast(buf, ctypes.POINTER(ctypes.c_ushort)), name_len)
            friendly = buf.value
        except comtypes.COMError:
            friendly = dev_id
        result.append((dev_id, friendly))
    return result


def open_device(device_id: str):
    """Open a WPD device and return the IPortableDevice interface."""
    client_info = comtypes.client.CreateObject(
        _types.PortableDeviceValues,
        clsctx=comtypes.CLSCTX_INPROC_SERVER,
        interface=_port.IPortableDeviceValues,
    )
    device = comtypes.client.CreateObject(
        _port.PortableDevice,
        clsctx=comtypes.CLSCTX_INPROC_SERVER,
        interface=_port.IPortableDevice,
    )
    device.Open(device_id, client_info)
    return device


def scan_device(device_id: str, friendly_name: str) -> list[MtpFile]:
    """
    Scan an MTP device for all video files under DCIM.
    Returns a list of MtpFile records.
    """
    device = open_device(device_id)
    content = device.Content()
    properties = content.Properties()

    files: list[MtpFile] = []
    count = [0]

    def _walk(object_id: str, path: str):
        for f in _walk_for_videos(content, properties, object_id, path):
            count[0] += 1
            print(f"\r  Scanning {friendly_name}: {count[0]} video(s) found...", end="", flush=True)
            files.append(f)

    # Start from device root ("DEVICE") — WPD uses this sentinel object_id
    _walk("DEVICE", friendly_name)
    print()
    return files


def download_file(device_id: str, mtp_file: MtpFile, dest_path) -> None:
    """
    Stream a file from the MTP device directly to dest_path (a pathlib.Path).
    Never buffers the whole file in memory.
    """
    device = open_device(device_id)
    content = device.Content()
    resources = content.Transfer()

    stgm_read = ctypes.c_uint(0)
    optimal_size = ctypes.pointer(ctypes.c_ulong(0))
    optimal_size, q_stream = resources.GetStream(
        mtp_file.object_id,
        WPD_RESOURCE_DEFAULT,
        stgm_read,
        optimal_size,
    )
    block_size = max(int(optimal_size.contents.value), 65536)
    filestream = q_stream.value

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dest_path, "wb") as out:
        while True:
            buf, length = filestream.RemoteRead(block_size)
            if length == 0:
                break
            out.write(bytearray(buf[:length]))
