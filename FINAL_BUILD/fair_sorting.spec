# -*- mode: python ; coding: utf-8 -*-
# Fair Sorting v2 build spec. Use build_release.ps1 next to this file.
#
#   FAIR_SORTING_ONEFILE=0  ->  dist/app-folder/fair sorting.exe  (fast start)
#   FAIR_SORTING_ONEFILE=1  ->  dist/single-exe/fair sorting.exe  (one portable file)

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

SPEC_DIR = Path(SPECPATH)  # noqa: F821 - provided by PyInstaller
ROOT = SPEC_DIR.parent
FFMPEG_DIR = SPEC_DIR / "ffmpeg" / "bin"
ONEFILE = os.environ.get("FAIR_SORTING_ONEFILE") == "1"


def safe_data(package):
    try:
        return collect_data_files(package)
    except Exception:
        return []


binaries = [(str(FFMPEG_DIR / name), ".") for name in ("ffmpeg.exe", "ffprobe.exe") if (FFMPEG_DIR / name).exists()]
datas = [
    (str(ROOT / "fairsort" / "web"), "fairsort/web"),
    (str(ROOT / "cocute.ico"), "."),
] + safe_data("pillow_heif")

hiddenimports = [
    "pillow_heif",
    "qrcode",
    "qrcode.image.svg",
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
    "clr",
]

# The v1 build dragged in Streamlit, OpenCV, pygame and numpy. None are needed now.
excludes = [
    "tkinter", "_tkinter", "numpy", "cv2", "pygame", "streamlit", "imageio", "imageio_ffmpeg",
    "matplotlib", "scipy", "pandas", "pyarrow", "PySide6", "PyQt5", "PyQt6", "IPython",
    "pytest", "torch", "tensorflow", "av", "psutil",
    # Not used at runtime; also sidesteps a PyInstaller hook crash with newer setuptools.
    "setuptools", "pkg_resources", "distutils", "_distutils_hack",
    # Optional imports of pywebview's bundled bottle server and of Send2Trash
    # (Windows uses the shell API directly), which drag in ~25 MB we never call.
    "send2trash", "win32com", "pythoncom", "pywintypes", "win32api", "Pythonwin",
    "eventlet", "gevent", "greenlet", "psycopg2", "bs4", "html5lib", "lxml",
    "OpenSSL", "cryptography", "paste", "cherrypy", "waitress", "tornado", "twisted",
    "test",
]

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

common = dict(
    name="fair sorting",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[str(ROOT / "cocute.ico")],
)

if ONEFILE:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], runtime_tmpdir=None, **common)
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, contents_directory=".", **common)
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, upx_exclude=[], name="app-folder")
