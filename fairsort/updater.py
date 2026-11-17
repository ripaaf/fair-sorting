"""Check GitHub for a newer release, download it and hand over to the right updater.

How an update is applied depends on how this copy is running:

* installed  (Setup put it in place, install_manifest.txt next to the exe)
* folder     (portable app folder)          -> run the new Setup in --update mode
* single     (portable one-file exe)        -> swap the exe after we exit
* source     (python main.py)               -> can only point to the release page

Downloads are checked against the SHA-256 list in the release notes (and the
asset size), and only ever come from github.com URLs the GitHub API returned.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from . import __version__
from .env import FROZEN, IS_WINDOWS

REPO = "ripaaf/fair-sorting"
TEST_URL = os.environ.get("FAIR_SORTING_UPDATE_URL", "").strip()  # point at a local fake release when testing
API_URL = TEST_URL or f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
ASSET_FOR_KIND = {"installed": "fair-sorting-setup.exe", "folder": "fair-sorting-setup.exe", "single": "fair-sorting.exe"}
ALLOWED_HOSTS = ("github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com") + (("127.0.0.1", "localhost") if TEST_URL else ())
CHECK_TTL = 3600
DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "FairSortingUpdate"


def parse_version(text: str) -> tuple[int, int, int]:
    numbers = [int(part) for part in re.findall(r"\d+", text or "")[:3]]
    while len(numbers) < 3:
        numbers.append(0)
    return tuple(numbers)  # type: ignore[return-value]


def install_kind() -> str:
    if not FROZEN:
        return "source"
    exe_dir = Path(sys.executable).resolve().parent
    if (exe_dir / "install_manifest.txt").exists():
        return "installed"
    meipass = Path(getattr(sys, "_MEIPASS", exe_dir)).resolve()
    return "folder" if meipass == exe_dir else "single"


def _host_ok(url: str) -> bool:
    match = re.match(r"^https?://([^/:]+)", url or "")
    return bool(match) and any(match.group(1) == host or match.group(1).endswith("." + host) for host in ALLOWED_HOSTS)


def _checksums(notes: str) -> dict[str, str]:
    return {name: digest.lower() for digest, name in re.findall(r"\b([0-9a-fA-F]{64})\s+\*?([\w.\-]+)", notes or "")}


class Updater:
    def __init__(self):
        self._lock = threading.Lock()
        self._release: dict | None = None
        self._checked_at = 0.0
        self.state = {"status": "idle", "progress": 0.0, "error": "", "file": ""}
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ check

    def check(self, force: bool = False) -> dict:
        with self._lock:
            fresh = self._release is not None and time.time() - self._checked_at < CHECK_TTL
        if force or not fresh:
            request = urllib.request.Request(API_URL, headers={"Accept": "application/vnd.github+json", "User-Agent": "FairSorting-Updater"})
            try:
                with urllib.request.urlopen(request, timeout=8) as response:
                    data = json.loads(response.read().decode("utf-8"))
            except Exception as exc:
                return {**self._summary(), "error": f"Couldn't reach GitHub: {getattr(exc, 'reason', exc)}"}
            with self._lock:
                self._release = data
                self._checked_at = time.time()
        return self._summary()

    def _summary(self) -> dict:
        kind = install_kind()
        release = self._release or {}
        latest = str(release.get("tag_name") or "")
        newer = bool(latest) and parse_version(latest) > parse_version(__version__)
        wanted = ASSET_FOR_KIND.get(kind)
        asset = next((a for a in release.get("assets", []) if a.get("name") == wanted), None)
        return {
            "current": __version__,
            "latest": latest.lstrip("vV"),
            "newer": newer,
            "name": release.get("name") or latest,
            "notes": release.get("body") or "",
            "page": release.get("html_url") or RELEASES_PAGE,
            "published": release.get("published_at") or "",
            "kind": kind,
            "can_apply": newer and kind != "source" and IS_WINDOWS and asset is not None,
            "size": (asset or {}).get("size", 0),
            "download": dict(self.state),
        }

    # --------------------------------------------------------------- download

    def start_download(self) -> dict:
        summary = self._summary()
        if not summary["can_apply"]:
            raise RuntimeError("There's no update this copy can install by itself.")
        if self._thread and self._thread.is_alive():
            return dict(self.state)
        self.state = {"status": "downloading", "progress": 0.0, "error": "", "file": ""}
        self._thread = threading.Thread(target=self._download, name="fs-update", daemon=True)
        self._thread.start()
        return dict(self.state)

    def _download(self):
        try:
            release = self._release or {}
            name = ASSET_FOR_KIND[install_kind()]
            asset = next(a for a in release.get("assets", []) if a.get("name") == name)
            url, size = asset["browser_download_url"], int(asset.get("size") or 0)
            if not _host_ok(url):
                raise RuntimeError("Refusing to download from an unexpected address.")
            expected = _checksums(release.get("body") or "").get(name)
            DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
            tag = re.sub(r"[^\w.\-]", "", str(release.get("tag_name") or "new"))
            target = DOWNLOAD_DIR / f"{tag}-{name}"
            partial = target.with_name(target.name + ".part")
            digest = hashlib.sha256()
            done = 0
            request = urllib.request.Request(url, headers={"User-Agent": "FairSorting-Updater"})
            with urllib.request.urlopen(request, timeout=30) as response, open(partial, "wb") as out:
                if not _host_ok(response.geturl()):
                    raise RuntimeError("The download was redirected to an unexpected address.")
                while True:
                    chunk = response.read(1024 * 256)
                    if not chunk:
                        break
                    out.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    if size:
                        self.state["progress"] = min(0.99, done / size)
            if size and done != size:
                raise RuntimeError("The download was incomplete. Try again.")
            if expected and digest.hexdigest() != expected:
                raise RuntimeError("The downloaded file didn't match its checksum, so it wasn't used.")
            os.replace(partial, target)
            self.state = {"status": "ready", "progress": 1.0, "error": "", "file": str(target), "verified": bool(expected)}
        except Exception as exc:
            self.state = {"status": "error", "progress": 0.0, "error": str(exc), "file": ""}

    # ------------------------------------------------------------------ apply

    def apply(self) -> str:
        """Start the updater process. The caller must quit the app right after."""
        if self.state.get("status") != "ready":
            raise RuntimeError("Download the update first.")
        downloaded = Path(self.state["file"])
        kind = install_kind()
        exe = Path(sys.executable).resolve()
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        if kind in ("installed", "folder"):
            args = [str(downloaded), "--update", "--dir", str(exe.parent), "--wait-pid", str(os.getpid())]
            if kind == "folder":
                args.append("--portable")
            subprocess.Popen(args, close_fds=True, creationflags=flags)
            return kind
        if kind == "single":
            script = _SWAP_PS
            env = {**os.environ, "FS_PID": str(os.getpid()), "FS_OLD": str(exe), "FS_NEW": str(downloaded)}
            command = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script]
            base = getattr(subprocess, "CREATE_NO_WINDOW", 0) | flags
            for creation in (base | 0x01000000, base):  # try to leave our job object first
                try:
                    subprocess.Popen(command, env=env, close_fds=True, creationflags=creation)
                    return kind
                except OSError:
                    continue
            raise RuntimeError("Couldn't start the updater.")
        raise RuntimeError("Running from source: update with git pull or download the new release.")


# Swap a one-file exe once it has exited (the bootloader holds it a moment longer).
_SWAP_PS = r"""
try { Wait-Process -Id ([int]$env:FS_PID) -Timeout 60 -ErrorAction SilentlyContinue } catch {}
$old = $env:FS_OLD; $new = $env:FS_NEW; $backup = "$old.old"
for ($i = 0; $i -lt 120; $i++) {
  try {
    if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup -Force -ErrorAction Stop }
    Move-Item -LiteralPath $old -Destination $backup -Force -ErrorAction Stop
    try { Copy-Item -LiteralPath $new -Destination $old -Force -ErrorAction Stop }
    catch { Move-Item -LiteralPath $backup -Destination $old -Force; throw }
    break
  } catch { Start-Sleep -Milliseconds 500 }
}
Start-Process -FilePath $old -WorkingDirectory (Split-Path -Parent $old)
Start-Sleep -Seconds 3
Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $new -Force -ErrorAction SilentlyContinue
"""
