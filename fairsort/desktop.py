"""Entry points.

* Desktop (default): local server on 127.0.0.1 + a native window (pywebview /
  WebView2). Falls back to an Edge/Chrome "app" window, then the default browser.
* Web (``--web``): serve the same UI to other devices on the network.
"""

from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
import threading
import time
import webbrowser

from . import APP_NAME, __version__
from .env import FROZEN, ICON_FILE, IS_WINDOWS, WEBVIEW_DATA_DIR

THEME_COLORS = {
    "dark": ("#121114", "#F2EEE8"),
    "light": ("#F3F0EA", "#1A181C"),
}


def _quiet_streams():
    # Windowed PyInstaller builds have no console; printing must not crash.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def _ensure_console():
    if not (IS_WINDOWS and FROZEN):
        return
    try:
        if ctypes.windll.kernel32.GetConsoleWindow() == 0:
            ctypes.windll.kernel32.AllocConsole()
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        sys.stderr = sys.stdout
    except OSError:
        pass


# ------------------------------------------------------------------ window chrome


def _own_window(title: str) -> int:
    """Find our top-level window by title, restricted to this process."""
    if not IS_WINDOWS:
        return 0
    user32 = ctypes.windll.user32
    pid = os.getpid()
    found = ctypes.c_void_p(0)
    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def callback(hwnd, _):
        owner = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(owner))
        if owner.value != pid or not user32.IsWindowVisible(ctypes.c_void_p(hwnd)):
            return True
        buffer = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(ctypes.c_void_p(hwnd), buffer, 256)
        if buffer.value == title:
            found.value = hwnd
            return False
        return True

    user32.EnumWindows(enum_proc(callback), 0)
    return int(found.value or 0)


def _colorref(hex_color: str) -> int:
    value = hex_color.lstrip("#")
    red, green, blue = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    return red | (green << 8) | (blue << 16)


def style_titlebar(theme: str, title: str = APP_NAME):
    """Paint the Windows 11 title bar in the app's own colors."""
    if not IS_WINDOWS:
        return
    hwnd = _own_window(title)
    if not hwnd:
        return
    background, text = THEME_COLORS.get(theme, THEME_COLORS["dark"])
    dwm = ctypes.windll.dwmapi
    for attribute, value in (
        (20, 1 if theme == "dark" else 0),  # DWMWA_USE_IMMERSIVE_DARK_MODE
        (35, _colorref(background)),  # DWMWA_CAPTION_COLOR (Win11)
        (36, _colorref(text)),  # DWMWA_TEXT_COLOR (Win11)
    ):
        data = ctypes.c_int(value)
        try:
            dwm.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), attribute, ctypes.byref(data), ctypes.sizeof(data))
        except OSError:
            pass


def _apply_icon(title: str = APP_NAME):
    if not (IS_WINDOWS and ICON_FILE.exists()):
        return
    hwnd = _own_window(title)
    if not hwnd:
        return
    user32 = ctypes.windll.user32
    user32.LoadImageW.restype = ctypes.c_void_p
    user32.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
    for which, size in ((1, 32), (0, 16)):  # ICON_BIG, ICON_SMALL
        handle = user32.LoadImageW(None, str(ICON_FILE), 1, size, size, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
        if handle:
            user32.SendMessageW(ctypes.c_void_p(hwnd), 0x80, ctypes.c_void_p(which), ctypes.c_void_p(handle))


def _set_app_id():
    if IS_WINDOWS:
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("FairSorting.Desktop.2")
        except Exception:
            pass


# ---------------------------------------------------------------------- runners


def _run_webview(app, url: str) -> bool:
    try:
        import webview  # type: ignore
    except Exception:
        return False
    WEBVIEW_DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        window = webview.create_window(
            APP_NAME,
            url,
            width=1320,
            height=860,
            min_size=(820, 560),
            background_color=THEME_COLORS["dark"][0],
            text_select=True,
        )
    except Exception:
        return False
    app.window = window
    app.on_theme = style_titlebar

    def on_shown():
        time.sleep(0.15)
        _apply_icon()
        style_titlebar("dark")

    try:
        window.events.shown += on_shown
    except Exception:
        pass
    try:
        webview.start(
            gui="edgechromium" if IS_WINDOWS else None,
            private_mode=False,
            storage_path=str(WEBVIEW_DATA_DIR),
            debug=bool(os.environ.get("FAIR_SORTING_DEBUG")),
        )
    except Exception:
        app.window = None
        return False
    return True


def _app_browser() -> str | None:
    if not IS_WINDOWS:
        return None
    roots = [os.environ.get(name) for name in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")]
    for relative in (
        r"Microsoft\Edge\Application\msedge.exe",
        r"Google\Chrome\Application\chrome.exe",
        r"BraveSoftware\Brave-Browser\Application\brave.exe",
    ):
        for root in roots:
            if root and os.path.isfile(os.path.join(root, relative)):
                return os.path.join(root, relative)
    return None


def _wait_for_heartbeat(app, timeout: float = 45.0):
    try:
        while time.monotonic() - app.last_ping < timeout:
            time.sleep(2)
    except KeyboardInterrupt:
        pass


def _run_app_window(app, url: str):
    browser = _app_browser()
    if browser:
        profile = WEBVIEW_DATA_DIR / "app-window"
        profile.mkdir(parents=True, exist_ok=True)
        try:
            process = subprocess.Popen(
                [
                    browser,
                    f"--app={url}",
                    f"--user-data-dir={profile}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--window-size=1320,860",
                ]
            )
            process.wait()
        except OSError:
            webbrowser.open(url)
    else:
        webbrowser.open(url)
    app.last_ping = time.monotonic()
    _wait_for_heartbeat(app)


def run_desktop(force_browser: bool = False) -> int:
    from .server import App, start_server

    _set_app_id()
    app = App(mode="desktop")
    httpd = start_server(app, "127.0.0.1", 0)
    url = f"http://127.0.0.1:{app.main_port}/?key={app.local_token}"
    try:
        if force_browser or not _run_webview(app, url):
            _run_app_window(app, url)
    finally:
        app.shutdown()
        httpd.shutdown()
    return 0


def run_web(host: str, port: int | None, open_browser: bool) -> int:
    from .server import App, lan_addresses, start_server

    _ensure_console()
    app = App(mode="web")
    port = port or int(app.settings.get("lan_port") or 8501)
    try:
        httpd = start_server(app, host, port)
    except OSError as exc:
        print(f"\n  Couldn't start on port {port}: {exc.strerror or exc}\n  Try: --port 8600\n")
        return 1
    pin = app.settings.get("lan_pin")
    print(f"\n  {APP_NAME} {__version__} - web mode\n")
    print(f"  On this computer : http://127.0.0.1:{port}/")
    for address in lan_addresses() if host in ("0.0.0.0", "") else []:
        print(f"  On your network  : http://{address}:{port}/")
    print(f"  PIN for other devices : {pin}\n")
    print("  Press Ctrl+C to stop.\n", flush=True)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(f"http://127.0.0.1:{port}/?key={app.local_token}")).start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("  Stopping...")
    finally:
        app.shutdown()
        httpd.shutdown()
    return 0


def main(argv: list[str] | None = None) -> int:
    _quiet_streams()
    parser = argparse.ArgumentParser(prog="fair-sorting", description=f"{APP_NAME} {__version__}")
    parser.add_argument("--web", action="store_true", help="serve the UI to other devices instead of opening a window")
    parser.add_argument("--host", default="0.0.0.0", help="web mode: address to listen on (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=None, help="web mode: port (default 8501)")
    parser.add_argument("--no-browser", action="store_true", help="web mode: don't open a browser tab")
    parser.add_argument("--browser", action="store_true", help="desktop mode: use an Edge/Chrome window instead of WebView2")
    parser.add_argument("--uninstall", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--quiet", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.uninstall:
        from .install import uninstall

        return uninstall(quiet=args.quiet)
    if args.web:
        return run_web(args.host, args.port, not args.no_browser)
    return run_desktop(force_browser=args.browser)
