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
