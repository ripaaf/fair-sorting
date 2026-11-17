"""Sorting state and file operations.

Everything that touches the user's files lives here, behind one lock, so the
desktop window and any LAN browsers always agree on what's going on.

Compatibility: the move log keeps the original ``source -> destination`` line
format, and ``fair_config.txt`` (source on line 1, destinations below) is still
read on first run and kept up to date for older builds.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

from .env import LEGACY_CONFIG_FILE, OUTPUT_DIR, SETTINGS_FILE, is_within, norm_key
from .media import KIND_BY_EXT, RAW_EXTS

LOG_PATTERN = re.compile(r"^move_log.*\.txt$", re.IGNORECASE)
SKIP_DIRS = {"$recycle.bin", "system volume information", "__pycache__", "node_modules", ".git"}
MAX_FILES = 250_000
_NUM = re.compile(r"(\d+)")


class OpError(Exception):
    """A user-facing failure (bad path, file vanished, name taken...)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def natural_key(text: str) -> tuple:
    return tuple(int(part) if part.isdigit() else part for part in _NUM.split(text.casefold()))


# Windows error codes that mean "someone still has the file open".
_IN_USE = {5, 32, 33}  # ERROR_ACCESS_DENIED, ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION
_CROSS_DEVICE = {17}  # ERROR_NOT_SAME_DEVICE
IN_USE_MESSAGE = (
    "{name} is still open in another program (a player, editor, sync or antivirus), "
    "so Windows won't let it move. Close it and press the key again."
)


def _in_use(exc: OSError) -> bool:
    return getattr(exc, "winerror", None) in _IN_USE or isinstance(exc, PermissionError)


def _cross_device(exc: OSError) -> bool:
    import errno

    return getattr(exc, "winerror", None) in _CROSS_DEVICE or exc.errno == errno.EXDEV


def _retry(action, path: str, release=None, seconds: float = 4.0):
    """Run a file operation, stopping our own readers and retrying while the file is busy."""
    if release:
        try:
            release(path)
        except Exception:
            pass
    deadline = time.monotonic() + seconds
    delay = 0.08
    while True:
        try:
            return action()
        except OSError as exc:
            if not _in_use(exc) or time.monotonic() >= deadline:
                raise
        time.sleep(delay)
        delay = min(delay * 1.6, 0.5)


def safe_move(src: str, dst: str, release=None):
    """Move a file without ever leaving a half-done copy behind.

    Same drive: a rename, retried for a few seconds if something still holds the
    file. Another drive: copy to a temporary name, swap it in, then remove the
    original; if the original can't be removed the copy is deleted again.
    (shutil.move falls back to copying on *any* rename error and then leaves
    that copy behind when deleting the source fails, which is exactly what
    produced name_1/name_2 duplicates on busy files.)
    """
    try:
        _retry(lambda: os.rename(src, dst), src, release)
        return
    except OSError as exc:
        if not _cross_device(exc):
            if _in_use(exc):
                raise OpError(IN_USE_MESSAGE.format(name=os.path.basename(src)), 409) from exc
            raise OpError(f"Move failed: {exc.strerror or exc}") from exc
    temp = f"{dst}.fs-partial"
    try:
        shutil.copy2(src, temp)
        os.replace(temp, dst)
    except OSError as exc:
        for leftover in (temp, dst):
            try:
                os.remove(leftover)
            except OSError:
                pass
        raise OpError(f"Copying to the other drive failed: {exc.strerror or exc}") from exc
    try:
        _retry(lambda: os.remove(src), src, release)
    except OSError as exc:
        try:
            os.remove(dst)  # roll back: the original stays where it was
        except OSError:
            pass
        if _in_use(exc):
            raise OpError(IN_USE_MESSAGE.format(name=os.path.basename(src)), 409) from exc
        raise OpError(f"Move failed: {exc.strerror or exc}") from exc


def unique_path(folder: str, filename: str) -> str:
    """Never overwrite: name.ext, name_1.ext, name_2.ext ... (same scheme as v1)."""
    return group_targets(folder, [filename])[0]


# ------------------------------------------------------------------- companions
#
# Pro cameras write several files per shot: RAW+JPEG pairs, Lightroom/darktable
# .xmp edits, .THM thumbnails, drone .SRT telemetry, .LRF/.LRV proxies, Apple
# .AAE edits, Sony's C0001M01.XML clip metadata. With "keep together" on they
# ride along with the file you look at: one item in the list, one key press,
# and move/rename/delete/undo apply to the whole set.

SIDECAR_EXTS = frozenset(".xmp .thm .srt .lrf .lrv .aae .pp3 .dop .on1 .dr4 .vrd .xml".split())
_IMAGE_SIDECARS = frozenset(".xmp .aae .pp3 .dop .on1 .dr4 .vrd".split())
_GOPRO = re.compile(r"^g[hxl](\d{6})(_\d+)?$")  # GX010123.MP4 <-> GL010123.LRV
_SONY_XML = re.compile(r"^(.+)m\d\d$")  # C0001.MP4 <-> C0001M01.XML
_PRIMARY_ORDER = (".jpg", ".jpeg", ".jpe", ".heic", ".heif", ".hif", ".tif", ".tiff", ".png", ".webp", ".avif")


def _ext(name: str) -> str:
    return os.path.splitext(name)[1].lower()


def _stem_key(stem: str) -> str:
    match = _GOPRO.match(stem)
    return f"gopro:{match.group(1)}{match.group(2) or ''}" if match else stem


def group_key(name: str) -> str | None:
    """Files with the same key in one folder belong to one shot. None: never grouped."""
    stem, ext = os.path.splitext(name.lower())
    if ext in KIND_BY_EXT:
        return _stem_key(stem)
    if ext not in SIDECAR_EXTS:
        return None
    inner, inner_ext = os.path.splitext(stem)
    if inner_ext in KIND_BY_EXT:  # darktable / RawTherapee style: IMG_0001.CR3.xmp
        stem = inner
    if ext == ".xml":
        match = _SONY_XML.match(stem)
        if not match:
            return None  # some other XML: not ours to move
        stem = match.group(1)
    return _stem_key(stem)


def is_sidecar(name: str) -> bool:
    return _ext(name) in SIDECAR_EXTS and group_key(name) is not None


def _sidecar_owner(members: list, name: str):
    inner = os.path.splitext(name.lower())[0]
    for entry in members:
        if entry.name.lower() == inner or any(os.path.basename(x).lower() == inner for x in entry.extras):
            return entry
    want_image = _ext(name) in _IMAGE_SIDECARS
    for entry in members:
        if (entry.kind == "image") == want_image:
            return entry
    return members[0]


def group_entries(entries: list, sidecars: list[str]) -> list:
    """Fold RAW partners and sidecar files into the entry people will look at."""
    groups: dict[tuple[str, str], list] = {}
    for entry in entries:
        key = group_key(entry.name)
        if key:
            groups.setdefault((norm_key(os.path.dirname(entry.path)), key), []).append(entry)
    folded: set[str] = set()
    for members in groups.values():
        if len(members) < 2:
            continue
        raws = [e for e in members if e.kind == "image" and _ext(e.name) in RAW_EXTS]
        shown = [e for e in members if e.kind == "image" and _ext(e.name) not in RAW_EXTS]
        if raws and shown:
            # Show the camera JPEG/HEIF (instant preview), carry the RAW.
            primary = min(shown, key=lambda e: _PRIMARY_ORDER.index(_ext(e.name)) if _ext(e.name) in _PRIMARY_ORDER else 99)
            for raw in raws:
                primary.extras.append(raw.path)
                folded.add(raw.key)
    for path in sidecars:
        name = os.path.basename(path)
        key = group_key(name)
        if not key:
            continue
        members = [e for e in groups.get((norm_key(os.path.dirname(path)), key), []) if e.key not in folded]
        if members:
            _sidecar_owner(members, name).extras.append(path)
    return [e for e in entries if e.key not in folded] if folded else entries


def _suffixed(name: str, stem: str, suffix: str) -> str:
    if not suffix:
        return name
    if name.lower().startswith(stem.lower()):
        return name[: len(stem)] + suffix + name[len(stem) :]
    base, ext = os.path.splitext(name)
    return base + suffix + ext


def _restemmed(name: str, old_stem: str, new_stem: str) -> str:
    """IMG_0001.CR3.xmp -> beach.CR3.xmp when IMG_0001.JPG becomes beach.JPG."""
    if name.lower().startswith(old_stem.lower()):
        return new_stem + name[len(old_stem) :]
    return new_stem + os.path.splitext(name)[1]


def group_targets(folder: str, names: list[str]) -> list[str]:
    """Free names in `folder` for a whole set, sharing one _N suffix (shot_1.jpg + shot_1.cr3)."""
    stem = os.path.splitext(names[0])[0]
    counter = 0
    while True:
        suffix = f"_{counter}" if counter else ""
        targets = [os.path.join(folder, _suffixed(name, stem, suffix)) for name in names]
        if len({norm_key(t) for t in targets}) == len(targets) and not any(os.path.exists(t) for t in targets):
            return targets
        counter += 1


def _log_group(records: list[tuple[str, str]], index: int) -> list[int]:
    """Neighbouring log lines that moved together as one shot."""

    def key(i):
        src, dst = records[i]
        group = group_key(os.path.basename(src))
        return (norm_key(os.path.dirname(src)), norm_key(os.path.dirname(dst)), group) if group else None

    target = key(index)
    if target is None:
        return [index]
    lo = hi = index
    while lo > 0 and index - lo < 16 and key(lo - 1) == target:
        lo -= 1
    while hi + 1 < len(records) and hi - index < 16 and key(hi + 1) == target:
        hi += 1
    return list(range(lo, hi + 1))


# ------------------------------------------------------------------------ settings

DEFAULT_SETTINGS = {
    "version": 2,
    "source": "",
    "include_subfolders": False,
    "destinations": [],  # [{"path": str, "label": str}]
    "recent_sources": [],
    "confirm_delete": True,
    "autoplay": True,
    "always_convert": False,
    "check_updates": True,
    "keep_together": True,
    "lan_port": 8501,
    "lan_pin": "",
    "lan_secret": "",
}


class Settings:
    def __init__(self):
        self._lock = threading.RLock()
        self.data = json.loads(json.dumps(DEFAULT_SETTINGS))
        self._load()

    def _load(self):
        loaded = None
        if SETTINGS_FILE.exists():
            try:
                loaded = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                loaded = None
        if isinstance(loaded, dict):
            for key, value in loaded.items():
                if key in self.data:
                    self.data[key] = value
        else:
            self._migrate_legacy()
        self.data["destinations"] = [
            {"path": os.path.normpath(str(item.get("path", ""))), "label": str(item.get("label", ""))[:60]}
            for item in self.data.get("destinations", [])
            if isinstance(item, dict) and item.get("path")
        ]
        if not self.data.get("lan_pin"):
            self.data["lan_pin"] = f"{secrets.randbelow(10**6):06d}"
        if not self.data.get("lan_secret"):
            self.data["lan_secret"] = secrets.token_hex(16)
        self.save()

    def _migrate_legacy(self):
        if not LEGACY_CONFIG_FILE.exists():
            return
        try:
            lines = [line.strip() for line in LEGACY_CONFIG_FILE.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
        except OSError:
            return
        if not lines:
            return
        self.data["source"] = os.path.normpath(lines[0])
        seen = set()
        for line in lines[1:]:
            key = norm_key(line)
            if key not in seen:
                seen.add(key)
                self.data["destinations"].append({"path": os.path.normpath(line), "label": ""})

    def get(self, key, default=None):
        with self._lock:
            return self.data.get(key, default)

    def update(self, **values):
        with self._lock:
            self.data.update(values)
            self.save()

    def save(self):
        with self._lock:
            try:
                SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
                temp = SETTINGS_FILE.with_suffix(".json.tmp")
                temp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
                os.replace(temp, SETTINGS_FILE)
                lines = [self.data.get("source") or ""] + [item["path"] for item in self.data["destinations"]]
                LEGACY_CONFIG_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
            except OSError:
                pass

    def public(self) -> dict:
        with self._lock:
            return {
                key: self.data[key]
                for key in ("include_subfolders", "confirm_delete", "autoplay", "always_convert", "check_updates", "keep_together", "lan_port")
            }


# --------------------------------------------------------------------------- files


@dataclass(slots=True)
class Entry:
    path: str
    name: str
    kind: str
    size: int
    mtime: float
    folder: str
    key: str = field(default="")
    extras: list[str] = field(default_factory=list)  # RAW partner + sidecars that move with it

    def to_json(self) -> dict:
        data = {"p": self.path, "n": self.name, "k": self.kind, "s": self.size, "m": round(self.mtime, 3), "f": self.folder}
        if self.extras:
            data["x"] = [os.path.basename(path) for path in self.extras]
        return data


def _entry_for(path: str, root: str) -> Entry | None:
    kind = KIND_BY_EXT.get(os.path.splitext(path)[1].lower())
    if not kind:
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    folder = os.path.relpath(os.path.dirname(path), root) if root else ""
    folder = "" if folder in (".", "") else folder.replace("\\", "/")
    return Entry(path, os.path.basename(path), kind, st.st_size, st.st_mtime, folder, norm_key(path))


def scan_folder(root: str, recursive: bool, exclude: list[str], together: bool = False) -> list[Entry]:
    entries: list[Entry] = []
    sidecars: list[str] = []
    excluded = {norm_key(path) for path in exclude}
    stack = [(root, "")]
    while stack and len(entries) < MAX_FILES:
        folder, rel = stack.pop()
        try:
            iterator = os.scandir(folder)
        except OSError:
            continue
        with iterator:
            for item in iterator:
                name = item.name
                try:
                    if item.is_dir():
                        if (
                            recursive
                            and not name.startswith(".")
                            and name.lower() not in SKIP_DIRS
                            and norm_key(item.path) not in excluded
                        ):
                            stack.append((item.path, f"{rel}/{name}" if rel else name))
                        continue
                    ext = os.path.splitext(name)[1].lower()
                    kind = KIND_BY_EXT.get(ext)
                    if not kind and not (together and ext in SIDECAR_EXTS):
                        continue
                    if name.startswith("._"):
                        continue  # macOS resource forks left on camera cards
                    st = item.stat()
                    if getattr(st, "st_file_attributes", 0) & 0x2:  # hidden
                        continue
                except OSError:
                    continue
                if kind:
                    entries.append(Entry(item.path, name, kind, st.st_size, st.st_mtime, rel, norm_key(item.path)))
                else:
                    sidecars.append(item.path)
    if together:
        entries = group_entries(entries, sidecars)
    entries.sort(key=lambda entry: (natural_key(entry.folder), natural_key(entry.name)))
    return entries


# ------------------------------------------------------------------------ move log


def read_log(path: str) -> list[tuple[str, str]]:
    records: list[tuple[str, str]] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if " -> " not in line:
                    continue
                source, destination = line.rstrip("\r\n").split(" -> ", 1)
                records.append((source.strip(), destination.strip()))
    except OSError:
        return []
    return records


def append_log(path: str, pairs: list[tuple[str, str]]):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.writelines(f"{source} -> {destination}\n" for source, destination in pairs)


def drop_log_line(path: str, destination: str, prefer_index: int | None = None) -> bool:
    """Remove one record (by destination) while keeping every other line untouched."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
    except OSError:
        return False
    wanted = norm_key(destination)
    record_positions = []
    for line_no, line in enumerate(lines):
        if " -> " in line:
            record_positions.append(line_no)
    target_line = None
    if prefer_index is not None and 0 <= prefer_index < len(record_positions):
        line_no = record_positions[prefer_index]
        if norm_key(lines[line_no].rstrip("\r\n").split(" -> ", 1)[1].strip()) == wanted:
            target_line = line_no
    if target_line is None:
        for line_no in reversed(record_positions):
            if norm_key(lines[line_no].rstrip("\r\n").split(" -> ", 1)[1].strip()) == wanted:
                target_line = line_no
                break
    if target_line is None:
        return False
    del lines[target_line]
    with open(path, "w", encoding="utf-8") as handle:
        handle.writelines(lines)
    return True


# ------------------------------------------------------------------------ workspace


class Workspace:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.lock = threading.RLock()
        self.source = ""
        self.recursive = False
        self.entries: list[Entry] = []
        self.by_key: dict[str, Entry] = {}
        self.rev = 1
        self.changes: deque[tuple[int, dict]] = deque(maxlen=400)
        self.undo_stack: list[dict] = []
        self.history: deque[dict] = deque(maxlen=300)
        self._history_id = 0
        self.logfile = ""
        self._log_cache: tuple[tuple, list[tuple[str, str]]] | None = None
        self.stats = self._fresh_stats(0)
        self.release = None  # set by the server: stops our own readers of a file

        saved = settings.get("source") or ""
        if saved and os.path.isdir(saved):
            try:
                self.load_source(saved, remember=False)
            except OpError:
                pass

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _fresh_stats(total: int) -> dict:
        return {"total": total, "moved": 0, "deleted": 0, "renamed": 0, "per_dest": {}, "started": time.time()}

    def _bump(self, op: dict):
        self.rev += 1
        self.changes.append((self.rev, op))

    def _remember(self, kind: str, **data) -> dict:
        self._history_id += 1
        item = {"id": self._history_id, "type": kind, "time": time.time(), "undone": False, **data}
        self.history.appendleft(item)
        return item

    def destinations(self) -> list[dict]:
        return self.settings.get("destinations") or []

    @property
    def keep_together(self) -> bool:
        return bool(self.settings.get("keep_together", True))

    def _move_pairs(self, pairs: list[tuple[str, str]]):
        """Move a set of files; if one fails, put the others back so nothing is split up."""
        done: list[tuple[str, str]] = []
        try:
            for src, dst in pairs:
                safe_move(src, dst, self.release)
                done.append((src, dst))
        except OpError:
            for src, dst in reversed(done):
                try:
                    safe_move(dst, src, self.release)
                except OpError:
                    pass
            raise

    def _rename_pairs(self, pairs: list[tuple[str, str]]):
        done: list[tuple[str, str]] = []
        try:
            for src, dst in pairs:
                _retry(lambda s=src, d=dst: os.rename(s, d), src, self.release)
                done.append((src, dst))
        except OSError as exc:
            for src, dst in reversed(done):
                try:
                    os.rename(dst, src)
                except OSError:
                    pass
            name = os.path.basename(pairs[len(done)][0])
            if _in_use(exc):
                raise OpError(IN_USE_MESSAGE.format(name=name).replace("let it move", "let it be renamed"), 409) from exc
            raise OpError(f"Rename failed: {exc.strerror or exc}") from exc

    def _readd(self, paths: list[str]):
        """Put restored files back in the list (grouped again when keep-together is on)."""
        found, sidecars = [], []
        for path in paths:
            if not self._in_scope(path):
                continue
            entry = _entry_for(path, self.source)
            if entry is not None:
                found.append(entry)
            elif is_sidecar(os.path.basename(path)):
                sidecars.append(path)
        if self.keep_together:
            found = group_entries(found, sidecars)
        for entry in found:
            self._insert_entry(entry)
            self._bump({"op": "add", "e": entry.to_json()})

    def _destination(self, path: str) -> dict | None:
        key = norm_key(path)
        for item in self.destinations():
            if norm_key(item["path"]) == key:
                return item
        return None

    def _in_scope(self, path: str) -> bool:
        if not self.source:
            return False
        parent = os.path.dirname(path)
        if self.recursive:
            if not is_within(parent, self.source):
                return False
            return not any(is_within(parent, item["path"]) for item in self.destinations())
        return norm_key(parent) == norm_key(self.source)

    def _insert_entry(self, entry: Entry):
        self.entries.append(entry)
        self.entries.sort(key=lambda item: (natural_key(item.folder), natural_key(item.name)))
        self.by_key[entry.key] = entry

    def _remove_entry(self, entry: Entry):
        try:
            self.entries.remove(entry)
        except ValueError:
            pass
        self.by_key.pop(entry.key, None)

    def _require_entry(self, path: str) -> Entry:
        entry = self.by_key.get(norm_key(path))
        if entry is None:
            raise OpError("That file is no longer in the sorting list. Try rescanning.", 409)
        if not os.path.exists(entry.path):
            self._remove_entry(entry)
            self._bump({"op": "remove", "p": entry.path})
            raise OpError("That file was moved or deleted outside Fair Sorting.", 409)
        return entry

    def allowed(self, path: str) -> bool:
        """Remote clients may only touch the source tree, destinations and logged files."""
        if self.source and is_within(path, self.source):
            return True
        if any(is_within(path, item["path"]) for item in self.destinations()):
            return True
        key = norm_key(path)
        return any(norm_key(dst) == key for _, dst in self._records())

    # ------------------------------------------------------------------ source

    def load_source(self, path: str, recursive: bool | None = None, remember: bool = True) -> dict:
        path = os.path.normpath(os.path.expanduser(path.strip().strip('"')))
        if not os.path.isdir(path):
            raise OpError("That folder doesn't exist or can't be opened.")
        if recursive is None:
            recursive = bool(self.settings.get("include_subfolders"))
        with self.lock:
            exclude = [item["path"] for item in self.destinations()]
            entries = scan_folder(path, recursive, exclude, together=self.keep_together)
            self.source = path
            self.recursive = recursive
            self.entries = entries
            self.by_key = {entry.key: entry for entry in entries}
            self.undo_stack.clear()
            self.stats = self._fresh_stats(len(entries))
            self.logfile = self._logfile_for(path)
            self._log_cache = None
            recent = [item for item in self.settings.get("recent_sources", []) if norm_key(item) != norm_key(path)]
            recent.insert(0, path)
            values = {"source": path, "include_subfolders": recursive}
            if remember:
                values["recent_sources"] = recent[:8]
            self.settings.update(**values)
            self._bump({"op": "reset"})
            return {"count": len(entries)}

    def rescan(self) -> dict:
        if not self.source:
            raise OpError("Pick a folder first.")
        with self.lock:
            stats = self.stats
            undo = list(self.undo_stack)
            logfile = self.logfile
            result = self.load_source(self.source, self.recursive, remember=False)
            # A rescan shouldn't wipe the session's progress, undo history or chosen log.
            stats["total"] = max(stats["total"], len(self.entries) + stats["moved"] + stats["deleted"])
            self.stats = stats
            self.undo_stack = undo
            self.logfile = logfile or self.logfile
            return result

    def forget_recent(self, path: str):
        with self.lock:
            recent = [item for item in self.settings.get("recent_sources", []) if norm_key(item) != norm_key(path)]
            self.settings.update(recent_sources=recent)

    # ------------------------------------------------------------ destinations

    def add_destinations(self, paths: list[str]) -> dict:
        added, skipped = [], []
        with self.lock:
            current = list(self.destinations())
            known = {norm_key(item["path"]) for item in current}
            for raw in paths:
                path = os.path.normpath(os.path.expanduser(str(raw).strip().strip('"')))
                if not path or path == ".":
                    continue
                if not os.path.isdir(path):
                    skipped.append({"path": path, "reason": "not found"})
                    continue
                if self.source and norm_key(path) == norm_key(self.source):
                    skipped.append({"path": path, "reason": "this is the folder being sorted"})
                    continue
                key = norm_key(path)
                if key in known:
                    skipped.append({"path": path, "reason": "already added"})
                    continue
                known.add(key)
                current.append({"path": path, "label": ""})
                added.append(path)
            if added:
                self.settings.update(destinations=current)
                self._bump({"op": "meta"})
                if self.recursive and any(is_within(path, self.source) for path in added):
                    self.rescan()
        return {"added": added, "skipped": skipped}

    def remove_destination(self, path: str):
        with self.lock:
            key = norm_key(path)
            current = [item for item in self.destinations() if norm_key(item["path"]) != key]
            self.settings.update(destinations=current)
            self._bump({"op": "meta"})

    def update_destination(self, path: str, label: str):
        with self.lock:
            current = list(self.destinations())
            for item in current:
                if norm_key(item["path"]) == norm_key(path):
                    item["label"] = label.strip()[:60]
            self.settings.update(destinations=current)
            self._bump({"op": "meta"})

    def reorder_destinations(self, paths: list[str]):
        with self.lock:
            current = list(self.destinations())
            order = {norm_key(path): index for index, path in enumerate(paths)}
            current.sort(key=lambda item: order.get(norm_key(item["path"]), len(order)))
            self.settings.update(destinations=current)
            self._bump({"op": "meta"})

    # ------------------------------------------------------------- operations

    def move(self, path: str, destination: str) -> dict:
        with self.lock:
            entry = self._require_entry(path)
            dest = self._destination(destination)
            if dest is None:
                raise OpError("That destination isn't in your list anymore.", 409)
            folder = dest["path"]
            if norm_key(os.path.dirname(entry.path)) == norm_key(folder):
                raise OpError("The file is already in that folder.")
            try:
                os.makedirs(folder, exist_ok=True)
            except OSError as exc:
                raise OpError(f"Can't open the destination folder: {exc.strerror or exc}") from exc
            extras = [path for path in entry.extras if os.path.exists(path)]
            targets = group_targets(folder, [entry.name] + [os.path.basename(path) for path in extras])
            pairs = list(zip([entry.path] + extras, targets))
            self._move_pairs(pairs)
            target = targets[0]
            self._ensure_logfile()
            log_error = ""
            try:
                append_log(self.logfile, pairs)
                self._log_cache = None
            except OSError as exc:
                log_error = f"File moved, but the log couldn't be written: {exc.strerror or exc}"
            self._remove_entry(entry)
            self.undo_stack.append({"type": "move", "src": entry.path, "dst": target, "extras": [list(pair) for pair in pairs[1:]]})
            del self.undo_stack[:-500]
            self.stats["moved"] += 1
            per_dest = self.stats["per_dest"]
            per_dest[norm_key(folder)] = per_dest.get(norm_key(folder), 0) + 1
            label = dest["label"] or os.path.basename(folder.rstrip("\\/")) or folder
            self._remember("move", name=entry.name, src=entry.path, dst=target, dest=label, extras=len(extras))
            self._bump({"op": "remove", "p": entry.path})
            return {
                "moved": entry.path,
                "to": target,
                "dest": label,
                "renamed": os.path.basename(target) != entry.name,
                "extras": len(extras),
                "warning": log_error,
            }

    def _restore_group(self, pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """Move a set back to where it came from. Returns the (from, to) moves made."""
        present = [(src, dst) for src, dst in pairs if os.path.exists(dst)]
        if not present:
            raise OpError("Can't undo: the moved file isn't at its destination anymore.", 409)
        folder = os.path.dirname(present[0][0])
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as exc:
            raise OpError(f"Undo failed: {exc.strerror or exc}") from exc
        if all(norm_key(os.path.dirname(src)) == norm_key(folder) for src, _ in present):
            homes = group_targets(folder, [os.path.basename(src) for src, _ in present])
        else:
            homes = [unique_path(os.path.dirname(src), os.path.basename(src)) for src, _ in present]
        moves = [(dst, home) for (_, dst), home in zip(present, homes)]
        self._move_pairs(moves)
        self._readd([home for _, home in moves])
        return moves

    def _mark_history_undone(self, kind: str, dst: str):
        key = norm_key(dst)
        for item in self.history:
            if item["type"] == kind and not item["undone"] and norm_key(item.get("dst", "")) == key:
                item["undone"] = True
                return

    def undo(self) -> dict:
        with self.lock:
            while self.undo_stack:
                action = self.undo_stack.pop()
                if action["type"] == "move":
                    pairs = [(action["src"], action["dst"])] + [tuple(pair) for pair in action.get("extras", [])]
                    try:
                        moves = self._restore_group(pairs)
                    except OpError:
                        if any(os.path.exists(dst) for _, dst in pairs):
                            self.undo_stack.append(action)
                            raise
                        continue  # files vanished; skip to the previous action
                    if self.logfile:
                        for dst, _ in moves:
                            drop_log_line(self.logfile, dst)
                        self._log_cache = None
                    restored = moves[0][1]
                    self.stats["moved"] = max(0, self.stats["moved"] - 1)
                    folder_key = norm_key(os.path.dirname(action["dst"]))
                    per_dest = self.stats["per_dest"]
                    if per_dest.get(folder_key):
                        per_dest[folder_key] -= 1
                    self._mark_history_undone("move", action["dst"])
                    self._bump({"op": "meta"})
                    return {"restored": restored, "name": os.path.basename(restored), "kind": "move", "extras": len(moves) - 1}
                if action["type"] == "rename":
                    entry = self.by_key.get(norm_key(action["dst"]))
                    if entry is None or not os.path.exists(action["dst"]):
                        continue
                    pairs = [(action["dst"], action["src"])] + [(dst, src) for src, dst in action.get("extras", []) if os.path.exists(dst)]
                    if any(os.path.exists(old) and norm_key(old) != norm_key(new) for new, old in pairs):
                        self.undo_stack.append(action)
                        raise OpError("Can't undo the rename: the old name is taken now.", 409)
                    try:
                        self._rename_pairs(pairs)
                    except OpError:
                        self.undo_stack.append(action)
                        raise
                    self._remove_entry(entry)
                    restored = _entry_for(action["src"], self.source)
                    if restored is not None:
                        restored.extras = [old for _, old in pairs[1:]]
                        self._insert_entry(restored)
                    self._bump({"op": "remove", "p": action["dst"]})
                    if restored is not None:
                        self._bump({"op": "add", "e": restored.to_json()})
                    self.stats["renamed"] = max(0, self.stats["renamed"] - 1)
                    self._mark_history_undone("rename", action["dst"])
                    return {"restored": action["src"], "name": os.path.basename(action["src"]), "kind": "rename"}
            # This session's history is used up: keep going back through the move
            # log, newest first, so undo also reaches moves from earlier sessions.
            records = self._records()
            for index in range(len(records) - 1, -1, -1):
                src, dst = records[index]
                if os.path.exists(dst):
                    return {**self.undo_log(index, dst), "kind": "log"}
            raise OpError("Nothing left to undo. The move log is empty too.", 409)

    def undo_log(self, index: int, destination: str) -> dict:
        with self.lock:
            records = self._records()
            if not (0 <= index < len(records)) or norm_key(records[index][1]) != norm_key(destination):
                raise OpError("The log changed since you opened it. Refresh and try again.", 409)
            group = _log_group(records, index) if self.keep_together else [index]
            moves = self._restore_group([records[i] for i in group])
            moved = {norm_key(dst) for dst, _ in moves}
            for i in sorted(group, reverse=True):  # bottom-up keeps the earlier indices valid
                if norm_key(records[i][1]) in moved:
                    drop_log_line(self.logfile, records[i][1], prefer_index=i)
            self._log_cache = None
            before = len(self.undo_stack)
            self.undo_stack = [item for item in self.undo_stack if norm_key(item.get("dst", "")) not in moved]
            if len(self.undo_stack) != before:
                self.stats["moved"] = max(0, self.stats["moved"] - (before - len(self.undo_stack)))
            for dst, _ in moves:
                self._mark_history_undone("move", dst)
            self._bump({"op": "meta"})
            restored = moves[0][1]
            return {"restored": restored, "name": os.path.basename(restored), "extras": len(moves) - 1}

    def delete(self, path: str, recycle) -> dict:
        with self.lock:
            entry = self._require_entry(path)
            try:
                _retry(lambda: recycle(entry.path), entry.path, self.release, seconds=2.5)
            except OSError as exc:
                raise OpError(str(exc)) from exc
            binned, warning = 0, ""
            for extra in entry.extras:
                if not os.path.exists(extra):
                    continue
                try:
                    _retry(lambda x=extra: recycle(x), extra, self.release, seconds=2.5)
                    binned += 1
                except OSError as exc:
                    warning = f"{os.path.basename(extra)} couldn't go to the Recycle Bin: {exc}"
            self._remove_entry(entry)
            self.stats["deleted"] += 1
            self._remember("delete", name=entry.name, src=entry.path, extras=binned)
            self._bump({"op": "remove", "p": entry.path})
            return {"deleted": entry.path, "extras": binned, "warning": warning}

    def rename(self, path: str, new_name: str) -> dict:
        new_name = new_name.strip()
        if not new_name or new_name in (".", "..") or re.search(r'[\\/:*?"<>|]', new_name):
            raise OpError('Names can\'t be empty or contain \\ / : * ? " < > |')
        with self.lock:
            entry = self._require_entry(path)
            old_ext = os.path.splitext(entry.name)[1]
            if not os.path.splitext(new_name)[1] and old_ext:
                new_name += old_ext
            if new_name == entry.name:
                return {"path": entry.path, "name": entry.name}
            if KIND_BY_EXT.get(os.path.splitext(new_name)[1].lower()) is None:
                raise OpError("Keep a supported media extension so the file stays in the list.")
            target = os.path.join(os.path.dirname(entry.path), new_name)
            if os.path.exists(target) and norm_key(target) != entry.key:
                raise OpError("A file with that name already exists here.", 409)
            old_stem, new_stem = os.path.splitext(entry.name)[0], os.path.splitext(new_name)[0]
            pairs = [(entry.path, target)] + [
                (x, os.path.join(os.path.dirname(x), _restemmed(os.path.basename(x), old_stem, new_stem)))
                for x in entry.extras
                if os.path.exists(x)
            ]
            for src, dst in pairs[1:]:
                if os.path.exists(dst) and norm_key(dst) != norm_key(src):
                    raise OpError(f"{os.path.basename(dst)} already exists here, so its companion file can't take that name.", 409)
            self._rename_pairs(pairs)
            self._remove_entry(entry)
            renamed = _entry_for(target, self.source)
            if renamed is not None:
                renamed.extras = [dst for _, dst in pairs[1:]]
                self._insert_entry(renamed)
            self.undo_stack.append({"type": "rename", "src": entry.path, "dst": target, "extras": [list(pair) for pair in pairs[1:]]})
            self.stats["renamed"] += 1
            self._remember("rename", name=new_name, src=entry.path, dst=target)
            self._bump({"op": "remove", "p": entry.path})
            if renamed is not None:
                self._bump({"op": "add", "e": renamed.to_json()})
            return {"path": target, "name": new_name}

    # ----------------------------------------------------------------- logfile

    def _logfile_for(self, folder: str) -> str:
        """Reuse the newest move_log in the sorted folder, else plan a new one (created on first move)."""
        for candidate in self._discover_logs(folder):
            if norm_key(os.path.dirname(candidate)) == norm_key(folder):
                return candidate
        return os.path.join(folder, f"move_log_{datetime.now():%Y%m%d%H%M%S}.txt")

    def _ensure_logfile(self):
        if not self.logfile:
            base = self.source or str(OUTPUT_DIR)
            self.logfile = os.path.join(base, f"move_log_{datetime.now():%Y%m%d%H%M%S}.txt")

    def _discover_logs(self, folder: str | None = None) -> list[str]:
        roots = []
        for candidate in (folder, self.source, str(OUTPUT_DIR), os.path.dirname(str(OUTPUT_DIR))):
            if candidate and os.path.isdir(candidate) and norm_key(candidate) not in {norm_key(r) for r in roots}:
                roots.append(candidate)
        found = []
        for root in roots:
            try:
                with os.scandir(root) as iterator:
                    for item in iterator:
                        if LOG_PATTERN.match(item.name) and item.is_file():
                            found.append((item.stat().st_ctime, item.path))
            except OSError:
                continue
        found.sort(reverse=True)
        return [path for _, path in found]

    def _records(self) -> list[tuple[str, str]]:
        if not self.logfile or not os.path.exists(self.logfile):
            return []
        st = os.stat(self.logfile)
        key = (norm_key(self.logfile), st.st_mtime_ns, st.st_size)
        if self._log_cache and self._log_cache[0] == key:
            return self._log_cache[1]
        records = read_log(self.logfile)
        self._log_cache = (key, records)
        return records

    def log_page(self, offset: int = 0, limit: int = 60, query: str = "") -> dict:
        with self.lock:
            records = self._records()
            total = len(records)
            indices = range(total - 1, -1, -1)
            if query:
                needle = query.casefold()
                indices = [i for i in indices if needle in records[i][0].casefold() or needle in records[i][1].casefold()]
            indices = list(indices)
            page = []
            for i in indices[offset : offset + limit]:
                src, dst = records[i]
                page.append(
                    {
                        "i": i,
                        "n": total - i,
                        "src": src,
                        "dst": dst,
                        "name": os.path.basename(src),
                        "folder": os.path.basename(os.path.dirname(dst)) or dst,
                        "exists": os.path.exists(dst),
                    }
                )
            return {
                "logfile": self.logfile,
                "exists": bool(self.logfile and os.path.exists(self.logfile)),
                "total": total,
                "matches": len(indices),
                "items": page,
                "offset": offset,
            }

    def list_logs(self) -> list[dict]:
        out = []
        for path in self._discover_logs():
            try:
                st = os.stat(path)
            except OSError:
                continue
            out.append({"path": path, "name": os.path.basename(path), "size": st.st_size, "mtime": st.st_mtime, "current": norm_key(path) == norm_key(self.logfile or "")})
        return out

    def select_log(self, path: str) -> dict:
        path = os.path.normpath(path.strip().strip('"'))
        if not os.path.isfile(path):
            raise OpError("That log file doesn't exist.")
        with self.lock:
            self.logfile = path
            self._log_cache = None
            self._bump({"op": "meta"})
        return {"logfile": path}

    def new_log(self) -> dict:
        with self.lock:
            base = self.source or str(OUTPUT_DIR)
            os.makedirs(base, exist_ok=True)
            path = os.path.join(base, f"move_log_{datetime.now():%Y%m%d%H%M%S}.txt")
            if os.path.exists(path):
                path = unique_path(base, os.path.basename(path))
            open(path, "a", encoding="utf-8").close()
            self.logfile = path
            self._log_cache = None
            self._bump({"op": "meta"})
            return {"logfile": path}

    def latest_log(self) -> dict:
        logs = self._discover_logs()
        if not logs:
            raise OpError("No move_log files found yet.", 404)
        return self.select_log(logs[0])

    # ------------------------------------------------------------------- views

    def meta(self) -> dict:
        per_dest = self.stats["per_dest"]
        destinations = []
        for item in self.destinations():
            path = item["path"]
            destinations.append(
                {
                    "path": path,
                    "label": item.get("label", ""),
                    "name": os.path.basename(path.rstrip("\\/")) or path,
                    "exists": os.path.isdir(path),
                    "count": per_dest.get(norm_key(path), 0),
                }
            )
        last = self.undo_stack[-1] if self.undo_stack else None
        records = self._records()
        if last:
            extra = len(last.get("extras") or [])
            undo_label = f"{last['type']} {os.path.basename(last['src'])}" + (f" +{extra}" if extra else "")
        elif records:
            undo_label = f"move {os.path.basename(records[-1][0])} back (move log)"
        else:
            undo_label = ""
        return {
            "rev": self.rev,
            "source": self.source,
            "recursive": self.recursive,
            "recent": [path for path in self.settings.get("recent_sources", []) if path],
            "destinations": destinations,
            "logfile": self.logfile,
            "undo": {
                "count": len(self.undo_stack),
                "log": len(records),
                "label": undo_label,
            },
            "stats": {key: value for key, value in self.stats.items() if key != "per_dest"},
            "history": list(self.history)[:80],
        }

    def snapshot(self) -> dict:
        with self.lock:
            data = self.meta()
            data["files"] = [entry.to_json() for entry in self.entries]
            return data

    def changes_since(self, rev: int) -> dict:
        with self.lock:
            if rev == self.rev:
                return {"rev": self.rev, "ops": [], "meta": None}
            ops = [op for r, op in self.changes if r > rev]
            oldest = self.changes[0][0] if self.changes else self.rev
            if rev > self.rev or rev < oldest - 1 or any(op["op"] == "reset" for op in ops):
                return {"rev": self.rev, "reset": True}
            file_ops = [op for op in ops if op["op"] in ("add", "remove")]
            return {"rev": self.rev, "ops": file_ops, "meta": self.meta()}
