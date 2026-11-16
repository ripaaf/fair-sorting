"""Native OS helpers: folder/file pickers, Recycle Bin, reveal in Explorer.

The pickers use the Vista+ IFileOpenDialog through ctypes so we get
multi-select folder picking with no extra dependencies.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading

from .env import IS_WINDOWS, hidden_process_kwargs

_dialog_lock = threading.Lock()


class DialogBusy(Exception):
    pass


# ----------------------------------------------------------------------- COM glue


class _Guid(ctypes.Structure):
    _fields_ = [
        ("data1", ctypes.c_uint32),
        ("data2", ctypes.c_uint16),
        ("data3", ctypes.c_uint16),
        ("data4", ctypes.c_ubyte * 8),
    ]


class _FilterSpec(ctypes.Structure):
    _fields_ = [("name", ctypes.c_wchar_p), ("spec", ctypes.c_wchar_p)]


def _check(result: int, action: str):
    if result < 0:
        raise OSError(f"{action} failed (HRESULT 0x{result & 0xFFFFFFFF:08X})")


def _method(interface, index: int, result_type, *argument_types):
    vtable = ctypes.cast(interface, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    prototype = ctypes.WINFUNCTYPE(result_type, ctypes.c_void_p, *argument_types)
    return prototype(vtable[index])


def _release(interface):
    if interface:
        _method(interface, 2, ctypes.c_ulong)(interface)


_CANCELLED = 0x800704C7


def _run_dialog(
    *,
    folders: bool,
    multi: bool,
    initial_dir: str,
    title: str,
    ok_label: str,
    filters: list[tuple[str, str]] | None,
    owner: int,
) -> list[str]:
    ole32 = ctypes.windll.ole32
    shell32 = ctypes.windll.shell32
    ole32.CLSIDFromString.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(_Guid)]
    ole32.CLSIDFromString.restype = ctypes.c_long
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoCreateInstance.argtypes = [
        ctypes.POINTER(_Guid),
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(_Guid),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    ole32.CoCreateInstance.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    shell32.SHCreateItemFromParsingName.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_void_p,
        ctypes.POINTER(_Guid),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    shell32.SHCreateItemFromParsingName.restype = ctypes.c_long

    def guid(text: str) -> _Guid:
        parsed = _Guid()
        _check(ole32.CLSIDFromString(text, ctypes.byref(parsed)), "Parsing GUID")
        return parsed

    init = ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
    initialized_here = init in (0, 1)
    if not initialized_here and (init & 0xFFFFFFFF) != 0x80010106:
        _check(init, "COM initialization")

    dialog = ctypes.c_void_p()
    results = ctypes.c_void_p()
    try:
        clsid = guid("{DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7}")  # FileOpenDialog
        iid = guid("{D57C7288-D4AD-4768-BE02-9D969532D960}")  # IFileOpenDialog
        shell_item_iid = guid("{43826D1E-E718-42EE-BC55-A1E261C37BFE}")
        _check(ole32.CoCreateInstance(ctypes.byref(clsid), None, 0x1, ctypes.byref(iid), ctypes.byref(dialog)), "Creating picker")

        options = ctypes.c_uint32()
        _check(_method(dialog, 10, ctypes.c_long, ctypes.POINTER(ctypes.c_uint32))(dialog, ctypes.byref(options)), "Reading options")
        flags = 0x00000008 | 0x00000040 | 0x00000800  # NOCHANGEDIR | FORCEFILESYSTEM | PATHMUSTEXIST
        if folders:
            flags |= 0x00000020  # PICKFOLDERS
        else:
            flags |= 0x00001000  # FILEMUSTEXIST
        if multi:
            flags |= 0x00000200  # ALLOWMULTISELECT
        _check(_method(dialog, 9, ctypes.c_long, ctypes.c_uint32)(dialog, options.value | flags), "Setting options")

        if filters and not folders:
            specs = (_FilterSpec * len(filters))(*[_FilterSpec(name, spec) for name, spec in filters])
            _check(_method(dialog, 4, ctypes.c_long, ctypes.c_uint, ctypes.c_void_p)(dialog, len(filters), ctypes.cast(specs, ctypes.c_void_p)), "Setting file types")

        _check(_method(dialog, 17, ctypes.c_long, ctypes.c_wchar_p)(dialog, title), "Setting title")
        if ok_label:
            _check(_method(dialog, 18, ctypes.c_long, ctypes.c_wchar_p)(dialog, ok_label), "Setting button")

        if initial_dir and os.path.isdir(initial_dir):
            item = ctypes.c_void_p()
            if shell32.SHCreateItemFromParsingName(os.path.abspath(initial_dir), None, ctypes.byref(shell_item_iid), ctypes.byref(item)) >= 0:
                try:
                    _method(dialog, 12, ctypes.c_long, ctypes.c_void_p)(dialog, item)
                finally:
                    _release(item)

        shown = _method(dialog, 3, ctypes.c_long, ctypes.c_void_p)(dialog, ctypes.c_void_p(owner or None))
        if (shown & 0xFFFFFFFF) == _CANCELLED:
            return []
        _check(shown, "Showing picker")

        _check(_method(dialog, 27, ctypes.c_long, ctypes.POINTER(ctypes.c_void_p))(dialog, ctypes.byref(results)), "Reading selection")
        count = ctypes.c_uint32()
        _check(_method(results, 7, ctypes.c_long, ctypes.POINTER(ctypes.c_uint32))(results, ctypes.byref(count)), "Counting selection")
        get_item = _method(results, 8, ctypes.c_long, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p))

        picked: list[str] = []
        seen: set[str] = set()
        for index in range(count.value):
            item = ctypes.c_void_p()
            _check(get_item(results, index, ctypes.byref(item)), "Reading item")
            try:
                name = ctypes.c_void_p()
                get_name = _method(item, 5, ctypes.c_long, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p))
                _check(get_name(item, 0x80058000, ctypes.byref(name)), "Reading path")  # SIGDN_FILESYSPATH
                try:
                    path = os.path.normpath(ctypes.wstring_at(name.value))
                finally:
                    ole32.CoTaskMemFree(name)
            finally:
                _release(item)
            key = os.path.normcase(path)
            if key not in seen:
                seen.add(key)
                picked.append(path)
        return picked
    finally:
        _release(results)
        _release(dialog)
        if initialized_here:
            ole32.CoUninitialize()


def foreground_window() -> int:
    if not IS_WINDOWS:
        return 0
    try:
        return int(ctypes.windll.user32.GetForegroundWindow() or 0)
    except Exception:
        return 0


def _with_dialog_lock(fn):
    if not _dialog_lock.acquire(blocking=False):
        raise DialogBusy("A picker is already open")
    try:
        return fn()
    finally:
        _dialog_lock.release()


def pick_folders(initial_dir: str, title: str, multi: bool = True, owner: int = 0) -> list[str]:
    if not IS_WINDOWS:
        raise OSError("Native pickers are only available on Windows")
    return _with_dialog_lock(
        lambda: _run_dialog(
            folders=True,
            multi=multi,
            initial_dir=initial_dir,
            title=title,
            ok_label="Select folder" if not multi else "Add folders",
            filters=None,
            owner=owner,
        )
    )


def pick_file(initial_dir: str, title: str, filters: list[tuple[str, str]], owner: int = 0) -> str | None:
    if not IS_WINDOWS:
        raise OSError("Native pickers are only available on Windows")
    picked = _with_dialog_lock(
        lambda: _run_dialog(
            folders=False,
            multi=False,
            initial_dir=initial_dir,
            title=title,
            ok_label="Open",
            filters=filters,
            owner=owner,
        )
    )
    return picked[0] if picked else None


# -------------------------------------------------------------------- file actions


class _SHFileOp(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("wFunc", ctypes.c_uint),
        ("pFrom", ctypes.c_void_p),
        ("pTo", ctypes.c_void_p),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", ctypes.c_int),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", ctypes.c_wchar_p),
    ]


def has_recycle_bin(path: str) -> bool:
    """USB sticks, card readers and network shares have no Recycle Bin on Windows."""
    if not IS_WINDOWS:
        return True
    drive = os.path.splitdrive(os.path.abspath(path))[0]
    if not drive or drive.startswith("\\\\"):
        return False  # UNC network path
    kind = ctypes.windll.kernel32.GetDriveTypeW(drive + "\\")
    return kind not in (2, 4, 5)  # REMOVABLE, REMOTE, CDROM


def recycle(path: str, owner: int = 0):
    """Send a file to the Recycle Bin. Windows warns first if it can't be recycled."""
    path = os.path.abspath(path)
    if not has_recycle_bin(path):
        raise OSError(
            "This drive has no Recycle Bin (USB, SD card or network), so deleting would be permanent. "
            "Move it to a destination instead, or delete it in Explorer if you're sure."
        )
    if IS_WINDOWS:
        FO_DELETE = 0x0003
        flags = 0x0040 | 0x0010 | 0x0004 | 0x4000  # ALLOWUNDO | NOCONFIRMATION | SILENT | WANTNUKEWARNING
        source = ctypes.create_unicode_buffer(path + "\0\0")  # list must be double-NUL terminated
        op = _SHFileOp()
        op.hwnd = owner or None
        op.wFunc = FO_DELETE
        op.pFrom = ctypes.cast(source, ctypes.c_void_p)
        op.pTo = None
        op.fFlags = flags
        result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        if op.fAnyOperationsAborted:
            raise OSError("Delete was cancelled")
        if result != 0:
            raise OSError(f"Windows could not delete the file (code {result})")
        if os.path.exists(path):
            raise OSError("The file is still there; it may be in use")
        return
    try:
        from send2trash import send2trash  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise OSError("Recycle bin support needs the Send2Trash package") from exc
    send2trash(path)


def reveal(path: str):
    target = os.path.normpath(path)
    if IS_WINDOWS:
        explorer = os.path.join(os.getenv("WINDIR", "C:\\Windows"), "explorer.exe")
        if os.path.isdir(target):
            subprocess.Popen([explorer, target])
        else:
            subprocess.Popen([explorer, "/select,", target])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", target])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(target) if os.path.isfile(target) else target])


def open_default(path: str):
    if IS_WINDOWS:
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path], **hidden_process_kwargs())


def list_drives() -> list[str]:
    if not IS_WINDOWS:
        return ["/"]
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    return [f"{chr(65 + index)}:\\" for index in range(26) if mask & (1 << index)]
