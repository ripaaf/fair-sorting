# -*- mode: python ; coding: utf-8 -*-
# "Fair Sorting Setup.exe" - the installer. build_release.ps1 prepares
# build/payload/payload.zip (the zipped app folder) before running this spec.

import os
from pathlib import Path

SPEC_DIR = Path(SPECPATH)  # noqa: F821 - provided by PyInstaller
ROOT = SPEC_DIR.parent
PAYLOAD = Path(os.environ.get("FAIR_SORTING_PAYLOAD") or SPEC_DIR / "build" / "payload" / "payload.zip")
if not PAYLOAD.exists():
    raise SystemExit(f"Payload not found: {PAYLOAD}. Build the app folder first.")

a = Analysis(
    [str(ROOT / "installer" / "setup_app.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(PAYLOAD), "."),
        (str(ROOT / "installer" / "setup.html"), "."),
        (str(ROOT / "fairsort" / "web" / "mascot.png"), "fairsort/web"),
        (str(ROOT / "cocute.ico"), "."),
    ],
    hiddenimports=["webview.platforms.edgechromium", "webview.platforms.winforms", "clr"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PIL", "pillow_heif", "qrcode", "tkinter", "_tkinter", "numpy", "cv2", "pygame", "streamlit",
        "imageio", "imageio_ffmpeg", "matplotlib", "scipy", "pandas", "PySide6", "PyQt5", "PyQt6",
        "IPython", "pytest", "psutil", "setuptools", "pkg_resources", "distutils", "_distutils_hack",
        "send2trash", "win32com", "pythoncom", "pywintypes", "win32api", "Pythonwin",
        "eventlet", "gevent", "greenlet", "psycopg2", "bs4", "html5lib", "lxml", "OpenSSL",
        "cryptography", "paste", "cherrypy", "waitress", "tornado", "twisted", "test",
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Fair Sorting Setup",
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
    runtime_tmpdir=None,
    icon=[str(ROOT / "cocute.ico")],
)
