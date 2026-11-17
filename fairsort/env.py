"""Paths and process helpers shared by every part of the app."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
IS_WINDOWS = os.name == "nt"
PACKAGE_DIR = Path(__file__).resolve().parent

if FROZEN:
    _default_home = Path(sys.executable).resolve().parent
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", _default_home)).resolve()
else:
    _default_home = PACKAGE_DIR.parent
    RESOURCE_DIR = PACKAGE_DIR.parent

WEB_DIR = PACKAGE_DIR / "web"
if not WEB_DIR.is_dir():
    WEB_DIR = RESOURCE_DIR / "fairsort" / "web"

ICON_FILE = RESOURCE_DIR / "cocute.ico"
if not ICON_FILE.exists():
    ICON_FILE = PACKAGE_DIR.parent / "cocute.ico"


def _is_writable(folder: Path) -> bool:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=folder, prefix=".fs-write-test-", delete=True):
            pass
        return True
    except OSError:
        return False


def _resolve_app_home() -> Path:
    override = os.environ.get("FAIR_SORTING_HOME", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if _is_writable(_default_home):
        return _default_home
    # Installed somewhere read-only (e.g. Program Files): keep data per user.
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return Path(base) / "FairSorting"


APP_HOME = _resolve_app_home()
OUTPUT_DIR = APP_HOME / "output"
CACHE_DIR = OUTPUT_DIR / "cache"
WEBVIEW_DATA_DIR = OUTPUT_DIR / "webview"
SETTINGS_FILE = APP_HOME / "fair_settings.json"
LEGACY_CONFIG_FILE = APP_HOME / "fair_config.txt"


def hidden_process_kwargs() -> dict:
    """Keep helper processes (ffmpeg, explorer) from flashing a console window."""
    if not IS_WINDOWS:
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
        "startupinfo": startupinfo,
    }


def norm_key(path: str) -> str:
    """Case/separator-insensitive key for comparing paths on this OS."""
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def is_within(path: str, root: str) -> bool:
    """True when `path` is `root` or lives somewhere below it."""
    if not path or not root:
        return False
    p = norm_key(path)
    r = norm_key(root)
    if p == r:
        return True
    return p.startswith(r.rstrip("\\/") + os.sep)


def open_shared(path: str, sequential: bool = True):
    """Open for reading while still letting the file be moved, renamed or deleted.

    Python's open() doesn't pass FILE_SHARE_DELETE on Windows, so a video that is
    streaming to the player would block "move to folder". CreateFileW with all
    share flags behaves like POSIX instead.
    """
    if not IS_WINDOWS:
        return open(path, "rb")
    import ctypes
    import msvcrt
    from ctypes import wintypes

    create = ctypes.windll.kernel32.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    flags = 0x08000000 if sequential else 0x10000000  # SEQUENTIAL_SCAN / RANDOM_ACCESS hint
    handle = create(path, 0x80000000, 0x1 | 0x2 | 0x4, None, 3, flags, None)  # GENERIC_READ, share all, OPEN_EXISTING
    if handle in (None, wintypes.HANDLE(-1).value):
        return open(path, "rb")  # let Python raise a normal, readable error
    fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    return os.fdopen(fd, "rb")
