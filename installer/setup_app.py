"""Fair Sorting Setup - the branded per-user installer.

Built into "Fair Sorting Setup.exe" by FINAL_BUILD/build_release.ps1, which
bundles the app folder as payload.zip.

    Fair Sorting Setup.exe                     # wizard
    Fair Sorting Setup.exe --silent            # install to the default folder, no UI
    Fair Sorting Setup.exe --silent --dir D:\\Apps --no-desktop
"""

from __future__ import annotations

import argparse
import base64
import os
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
ROOT = BUNDLE if FROZEN else Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fairsort import APP_NAME, __version__  # noqa: E402
from fairsort import install as inst  # noqa: E402
from fairsort import winshell  # noqa: E402

TITLE = f"{APP_NAME} Setup"
PAYLOAD = Path(os.environ.get("FAIR_SORTING_PAYLOAD") or (BUNDLE / "payload.zip" if FROZEN else ROOT / "FINAL_BUILD" / "dist" / "fair-sorting-app.zip"))
UI_FILE = BUNDLE / "setup.html" if FROZEN else Path(__file__).resolve().parent / "setup.html"
MASCOT = (BUNDLE / "fairsort" / "web" / "mascot.png") if FROZEN else ROOT / "fairsort" / "web" / "mascot.png"


def payload_size_mb() -> int:
    try:
        with zipfile.ZipFile(PAYLOAD) as archive:
            return round(sum(info.file_size for info in archive.infolist()) / (1024 * 1024))
    except (OSError, zipfile.BadZipFile):
        return 0


class Api:
    """Methods callable from the page as window.pywebview.api.<name>()."""

    def __init__(self):
        self._window = None
        self._state = {"pct": 0.0, "stage": "", "done": False, "error": "", "target": ""}
        self._thread: threading.Thread | None = None

    def info(self):
        existing = inst.existing_install()
        return {
            "version": __version__,
            "dir": existing["dir"] if existing else str(inst.default_dir()),
            "existing": existing,
            "payload": PAYLOAD.exists(),
            "size_mb": payload_size_mb(),
        }

    def browse(self, current):
        start = str(Path(current).parent) if current else str(inst.default_dir().parent)
        try:
            picked = winshell.pick_folders(start, "Choose where to install Fair Sorting", multi=False, owner=winshell.foreground_window())
        except (OSError, winshell.DialogBusy):
            return None
        return str(inst.normalize_target(picked[0])) if picked else None

    def install(self, folder, desktop, start_menu):
        if self._thread and self._thread.is_alive():
            return True
        self._state = {"pct": 0.0, "stage": "Preparing", "done": False, "error": "", "target": ""}

        def report(pct, stage):
            self._state["pct"] = pct
            self._state["stage"] = stage

        def work():
            try:
                target = inst.install(PAYLOAD, folder, bool(desktop), bool(start_menu), report)
                self._state.update(done=True, pct=1.0, target=str(target))
            except inst.InstallError as exc:
                self._state["error"] = str(exc)
            except Exception as exc:  # pragma: no cover - unexpected
                self._state["error"] = f"Install failed: {exc}"

        self._thread = threading.Thread(target=work, daemon=True)
        self._thread.start()
        return True

    def progress(self):
        return dict(self._state)

    def finish(self, launch):
        target = self._state.get("target")
        if launch and target:
            exe = Path(target) / inst.EXE_NAME
            flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            subprocess.Popen([str(exe)], cwd=target, creationflags=flags, close_fds=True)
        self.close()

    def close(self):
        if self._window is not None:
            threading.Thread(target=self._window.destroy, daemon=True).start()


def build_html() -> str:
    html = UI_FILE.read_text(encoding="utf-8")
    mascot = base64.b64encode(MASCOT.read_bytes()).decode("ascii") if MASCOT.exists() else ""
    return html.replace("__MASCOT__", f"data:image/png;base64,{mascot}").replace("__VERSION__", __version__)


def run_silent(folder: str | None, desktop: bool) -> int:
    try:
        target = inst.install(PAYLOAD, folder or str(inst.default_dir()), desktop, True)
    except inst.InstallError as exc:
        print(f"Install failed: {exc}", file=sys.stderr)
        return 1
    print(f"Installed to {target}")
    return 0


def run_wizard() -> int:
    import webview  # type: ignore

    from fairsort.desktop import _apply_icon, _set_app_id, style_titlebar

    _set_app_id()
    api = Api()
    window = webview.create_window(
        TITLE, html=build_html(), js_api=api, width=780, height=500,
        resizable=False, background_color="#121114", text_select=False,
    )
    api._window = window

    def on_shown():
        time.sleep(0.15)
        _apply_icon(TITLE)
        style_titlebar("dark", TITLE)

    window.events.shown += on_shown
    webview.start(gui="edgechromium", private_mode=True)
    return 0


def main() -> int:
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    parser = argparse.ArgumentParser(prog="Fair Sorting Setup")
    parser.add_argument("--silent", action="store_true", help="install without showing the wizard")
    parser.add_argument("--dir", default=None, help="install folder (silent mode)")
    parser.add_argument("--no-desktop", action="store_true", help="skip the desktop shortcut (silent mode)")
    args = parser.parse_args()
    if args.silent:
        return run_silent(args.dir, not args.no_desktop)
    return run_wizard()


if __name__ == "__main__":
    raise SystemExit(main())
