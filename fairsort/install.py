"""Per-user install, update and uninstall for the Windows release.

* Installs to %LOCALAPPDATA%\\Programs\\Fair Sorting by default - no admin rights.
* Every installed file is listed in ``install_manifest.txt``. Updates and the
  uninstaller only ever delete files from that list (plus the app's own cache),
  so choosing an odd folder can never wipe someone's documents.
* Registers in Settings > Apps with ``"fair sorting.exe" --uninstall``.
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from . import APP_NAME, __version__
from .env import IS_WINDOWS, hidden_process_kwargs

EXE_NAME = "fair sorting.exe"
MANIFEST = "install_manifest.txt"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\FairSorting"
USER_DATA = ("fair_settings.json", "fair_config.txt")
APP_DATA_DIRS = ("output",)  # cache + webview profile written by the app itself
HOMEPAGE = "https://github.com/ripaaf/fair-sorting"


class InstallError(Exception):
    pass


def default_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Programs" / APP_NAME


def normalize_target(chosen: str) -> Path:
    """Folder pickers return the parent; install into a dedicated subfolder."""
    path = Path(chosen).expanduser()
    if path.name.lower() != APP_NAME.lower() and not (path / EXE_NAME).exists():
        path = path / APP_NAME
    return path


def _validate(target: Path):
    resolved = target.resolve()
    if resolved.parent == resolved:
        raise InstallError("Pick a folder, not the root of a drive.")
    windir = Path(os.environ.get("WINDIR", r"C:\Windows")).resolve()
    if resolved == windir or windir in resolved.parents:
        raise InstallError("Fair Sorting can't be installed inside the Windows folder.")


# ------------------------------------------------------------------ registry


def existing_install() -> dict | None:
    if not IS_WINDOWS:
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            location = winreg.QueryValueEx(key, "InstallLocation")[0]
            version = winreg.QueryValueEx(key, "DisplayVersion")[0]
    except OSError:
        return None
    if not (Path(location) / EXE_NAME).exists():
        return None
    return {"dir": location, "version": version}


def register(target: Path):
    import winreg

    exe = target / EXE_NAME
    size_kb = 0
    for root, _, files in os.walk(target):
        for name in files:
            try:
                size_kb += os.path.getsize(os.path.join(root, name)) // 1024
            except OSError:
                pass
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
        values = {
            "DisplayName": APP_NAME,
            "DisplayVersion": __version__,
            "Publisher": "ripaaf",
            "DisplayIcon": f'"{exe}",0',
            "InstallLocation": str(target),
            "UninstallString": f'"{exe}" --uninstall',
            "QuietUninstallString": f'"{exe}" --uninstall --quiet',
            "URLInfoAbout": HOMEPAGE,
            "HelpLink": HOMEPAGE,
        }
        for name, value in values.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        winreg.SetValueEx(key, "EstimatedSize", 0, winreg.REG_DWORD, int(size_kb))
        winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)


def unregister():
    import winreg

    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except OSError:
        pass


# ----------------------------------------------------------------- shortcuts

_SHORTCUT_PS = r"""
$ws = New-Object -ComObject WScript.Shell
function Make([string]$folder) {
  $link = $ws.CreateShortcut((Join-Path $folder 'Fair Sorting.lnk'))
  $link.TargetPath = $env:FS_TARGET
  $link.WorkingDirectory = $env:FS_DIR
  $link.IconLocation = "$env:FS_TARGET,0"
  $link.Description = 'Sort photos, videos and audio into folders'
  $link.Save()
}
if ($env:FS_START -eq '1') { Make ([Environment]::GetFolderPath('Programs')) }
if ($env:FS_DESKTOP -eq '1') { Make ([Environment]::GetFolderPath('Desktop')) }
"""

_REMOVE_SHORTCUTS_PS = r"""
foreach ($folder in @([Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Desktop'))) {
  $path = Join-Path $folder 'Fair Sorting.lnk'
  if (Test-Path -LiteralPath $path) {
    $target = (New-Object -ComObject WScript.Shell).CreateShortcut($path).TargetPath
    if ($target -and ($target -ieq $env:FS_TARGET)) { Remove-Item -LiteralPath $path -Force }
  }
}
"""


def _powershell(script: str, env: dict, wait: bool = True):
    command = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script]
    full_env = {**os.environ, **env}
    if wait:
        subprocess.run(command, env=full_env, check=False, timeout=60, **hidden_process_kwargs())
        return
    # Must outlive us. DETACHED_PROCESS makes PowerShell quit at once, so use a
    # hidden console instead, and leave our job object when Windows allows it
    # (Settings > Apps may kill the uninstaller's children otherwise).
    base = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    for flags in (base | 0x01000000, base):  # CREATE_BREAKAWAY_FROM_JOB first
        try:
            subprocess.Popen(command, env=full_env, creationflags=flags, close_fds=True)
            return
        except OSError:
            continue


def make_shortcuts(target: Path, desktop: bool, start_menu: bool):
    if not (desktop or start_menu):
        return
    _powershell(_SHORTCUT_PS, {
        "FS_TARGET": str(target / EXE_NAME),
        "FS_DIR": str(target),
        "FS_START": "1" if start_menu else "0",
        "FS_DESKTOP": "1" if desktop else "0",
    })


def remove_shortcuts(target: Path):
    _powershell(_REMOVE_SHORTCUTS_PS, {"FS_TARGET": str(target / EXE_NAME)})


# ------------------------------------------------------------------- install


def _read_manifest(target: Path) -> list[str]:
    try:
        return [line.strip() for line in (target / MANIFEST).read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return []


def _is_running(target: Path) -> bool:
    exe = target / EXE_NAME
    if not exe.exists():
        return False
    try:
        with open(exe, "ab"):
            return False
    except PermissionError:
        return True
    except OSError:
        return False


def install(payload: Path, chosen_dir: str, desktop: bool, start_menu: bool, progress=None) -> Path:
    """Extract the app folder, write the manifest, shortcuts and Apps entry."""
    report = progress or (lambda pct, stage: None)
    target = normalize_target(chosen_dir)
    _validate(target)
    if _is_running(target):
        raise InstallError("Fair Sorting is running from that folder. Close it, then try again.")
    report(0.0, "Preparing")
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise InstallError(f"Can't create the folder: {exc.strerror or exc}") from exc

    # Updating: remove files the previous version installed (never anything else).
    for rel in _read_manifest(target):
        candidate = target / rel
        if candidate.is_file() and rel not in USER_DATA:
            try:
                candidate.unlink()
            except OSError:
                pass

    try:
        archive = zipfile.ZipFile(payload)
    except (OSError, zipfile.BadZipFile) as exc:
        raise InstallError("The installer is damaged. Download it again.") from exc
    with archive:
        members = [info for info in archive.infolist() if not info.is_dir()]
        total = sum(info.file_size for info in members) or 1
        done = 0
        written: list[str] = []
        for info in members:
            rel = info.filename.replace("\\", "/").lstrip("/")
            if ".." in rel.split("/"):
                continue
            destination = target / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                with archive.open(info) as source, open(destination, "wb") as out:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        out.write(chunk)
                        done += len(chunk)
                        report(0.02 + 0.9 * done / total, "Copying files")
            except PermissionError as exc:
                raise InstallError(f"{rel} is in use. Close Fair Sorting and try again.") from exc
            except OSError as exc:
                raise InstallError(f"Couldn't write {rel}: {exc.strerror or exc}") from exc
            written.append(rel)
    (target / MANIFEST).write_text("\n".join(written + [MANIFEST]) + "\n", encoding="utf-8")

    report(0.94, "Adding shortcuts")
    try:
        make_shortcuts(target, desktop, start_menu)
    except (OSError, subprocess.SubprocessError):
        pass
    report(0.98, "Registering")
    if IS_WINDOWS:
        register(target)
    report(1.0, "Done")
    return target


# ----------------------------------------------------------------- uninstall

_CLEANUP_PS = r"""
$cfg = Get-Content -Raw -LiteralPath $env:FS_PLAN | ConvertFrom-Json
try { Wait-Process -Id $cfg.pid -Timeout 30 -ErrorAction SilentlyContinue } catch {}
Start-Sleep -Milliseconds 500
foreach ($rel in $cfg.files) {
  $path = Join-Path $cfg.dir $rel
  if (Test-Path -LiteralPath $path -PathType Leaf) { Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue }
}
foreach ($rel in $cfg.dirs) {
  $path = Join-Path $cfg.dir $rel
  if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction SilentlyContinue }
}
Get-ChildItem -LiteralPath $cfg.dir -Directory -Recurse -Force -ErrorAction SilentlyContinue |
  Sort-Object { $_.FullName.Length } -Descending |
  ForEach-Object { if (-not (Get-ChildItem -LiteralPath $_.FullName -Force)) { Remove-Item -LiteralPath $_.FullName -Force } }
if (-not (Get-ChildItem -LiteralPath $cfg.dir -Force -ErrorAction SilentlyContinue)) { Remove-Item -LiteralPath $cfg.dir -Force -ErrorAction SilentlyContinue }
Remove-Item -LiteralPath $env:FS_PLAN -Force -ErrorAction SilentlyContinue
"""


def _message(text: str, flags: int) -> int:
    return ctypes.windll.user32.MessageBoxW(None, text, f"{APP_NAME} {__version__}", flags | 0x00010000)  # MB_SETFOREGROUND


def uninstall(quiet: bool = False) -> int:
    """Run from the installed exe (``--uninstall``). Deletion happens after we exit."""
    if not IS_WINDOWS:
        return 1
    target = Path(sys.executable).resolve().parent
    files = _read_manifest(target)
    if not files:
        if not quiet:
            _message("This copy of Fair Sorting wasn't installed by the installer, so there's nothing to uninstall.\n\nYou can delete its folder yourself.", 0x40)
        return 1
    remove_data = False
    if not quiet:
        answer = _message(
            "Remove Fair Sorting from this PC?\n\n"
            "Yes - remove the app, your settings and its preview cache.\n"
            "No - remove the app but keep your settings for later.\n"
            "Cancel - keep everything.\n\n"
            "Your photos, videos, audio and move logs are never touched.",
            0x03 | 0x20,  # MB_YESNOCANCEL | MB_ICONQUESTION
        )
        if answer == 2:  # IDCANCEL
            return 0
        remove_data = answer == 6  # IDYES
    remove_shortcuts(target)
    unregister()
    plan = {
        "pid": os.getpid(),
        "dir": str(target),
        "files": [rel for rel in files if rel not in USER_DATA] + (list(USER_DATA) if remove_data else []),
        "dirs": list(APP_DATA_DIRS),
    }
    if not remove_data:
        plan["dirs"] = ["output/cache", "output/webview"]
    handle, plan_path = tempfile.mkstemp(prefix="fair-sorting-uninstall-", suffix=".json")
    with os.fdopen(handle, "w", encoding="utf-8") as out:
        json.dump(plan, out)
    _powershell(_CLEANUP_PS, {"FS_PLAN": plan_path}, wait=False)
    if not quiet:
        _message("Fair Sorting has been removed.", 0x40)
    return 0
