from __future__ import annotations

import atexit
import hashlib
import html
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import streamlit as st
from PIL import Image, UnidentifiedImageError

SCRIPT_DIR = Path(__file__).resolve().parent
IS_FROZEN = getattr(sys, "frozen", False)
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", SCRIPT_DIR) if IS_FROZEN else SCRIPT_DIR).resolve()
APP_HOME = Path(
    os.environ.get("FAIR_SORTING_HOME")
    or (Path(sys.executable).resolve().parent if IS_FROZEN else SCRIPT_DIR)
).resolve()
OUTPUT_DIR = APP_HOME / "output"
CACHE_DIR = OUTPUT_DIR / "web_previews"
TOOLS_DIR = APP_HOME / "tools" / "ffmpeg"
CONFIG_FILE = APP_HOME / "fair_config.txt"
WEB_RUNTIME_FILE = OUTPUT_DIR / "web_server_runtime.json"
WINDOWS_FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
DEFAULT_BIND_ADDRESS = os.environ.get("FAIR_SORTING_BIND_ADDRESS", "127.0.0.1").strip() or "127.0.0.1"
DEFAULT_PORT = os.environ.get("FAIR_SORTING_PORT", "8501").strip() or "8501"
DEFAULT_LOGFILE = os.environ.get("FAIR_SORTING_LOGFILE", "").strip()

IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".bmp",
    ".tiff",
    ".tif",
    ".webp",
    ".heic",
    ".heif",
    ".avif",
    ".jp2",
    ".j2k",
    ".jpf",
    ".jfif",
    ".ico",
    ".tga",
    ".ppm",
    ".pgm",
    ".pbm",
    ".pnm",
    ".hdr",
    ".exr",
    ".psd",
    ".raw",
    ".cr2",
    ".nef",
    ".orf",
    ".arw",
    ".rw2",
    ".dng",
    ".xcf",
    ".pcx",
}

VIDEO_EXTENSIONS = {
    ".mp4",
    ".avi",
    ".mkv",
    ".webm",
    ".mov",
    ".flv",
    ".3gp",
    ".wmv",
    ".rmvb",
    ".m4v",
    ".mpeg",
    ".mpg",
    ".divx",
    ".ogv",
    ".ts",
    ".m2ts",
    ".vob",
    ".mts",
    ".asf",
    ".rm",
    ".ogm",
    ".mxf",
    ".f4v",
}

AUDIO_EXTENSIONS = {
    ".mp3",
    ".wav",
    ".m4a",
    ".ogg",
    ".wma",
    ".aac",
    ".ape",
    ".alac",
    ".mid",
    ".ac3",
    ".amr",
    ".ra",
    ".opus",
    ".flac",
}

SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS

# Extensions that browsers can natively play without transcoding
BROWSER_NATIVE_VIDEO = {".mp4", ".webm", ".ogv"}
BROWSER_NATIVE_AUDIO = {".mp3", ".wav", ".ogg", ".m4a"}


@dataclass
class MoveRecord:
    source_path: str
    destination_path: str


def safe_rerun() -> None:
    if hasattr(st, "rerun"):
        st.rerun()
    else:
        st.experimental_rerun()


def now_str() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S")


def normalize_path_str(path: str) -> str:
    return os.path.normpath(path).strip()


def is_valid_dir(path: str) -> bool:
    return Path(path).is_dir()


def _normalize_existing_dir(path: str | None) -> Path | None:
    if not path:
        return None
    candidate = Path(path).expanduser().resolve()
    return candidate if candidate.is_dir() else None


def preferred_log_root(source_folder: str | None = None) -> Path:
    for candidate in (
        source_folder,
        st.session_state.get("source_folder"),
        st.session_state.get("source_folder_input"),
    ):
        normalized = _normalize_existing_dir(candidate)
        if normalized is not None:
            return normalized
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR


def discover_logfiles(source_folder: str | None = None) -> list[Path]:
    candidates: list[Path] = []
    seen_roots: set[Path] = set()
    for base in (
        preferred_log_root(source_folder),
        APP_HOME,
        OUTPUT_DIR,
    ):
        resolved = base.resolve()
        if resolved in seen_roots or not resolved.exists():
            continue
        seen_roots.add(resolved)
        for log_file in resolved.glob("move_log*.txt"):
            candidates.append(log_file)
    return sorted(candidates, key=lambda p: p.stat().st_ctime if p.exists() else 0, reverse=True)


def create_default_logfile(source_folder: str | None = None) -> Path:
    log_root = preferred_log_root(source_folder)
    log_root.mkdir(parents=True, exist_ok=True)
    logfile = log_root / f"move_log_{now_str()}.txt"
    logfile.touch(exist_ok=True)
    return logfile


def resolve_logfile_for_folder(source_folder: str | None = None) -> Path:
    preferred_root = preferred_log_root(source_folder).resolve()
    for log_file in discover_logfiles(source_folder):
        if log_file.parent.resolve() == preferred_root:
            return log_file
    return create_default_logfile(source_folder)


def write_runtime_record() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": os.getpid(),
        "command": [str(Path(sys.argv[0]).resolve())],
    }
    try:
        WEB_RUNTIME_FILE.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        return


def clear_runtime_record() -> None:
    if not WEB_RUNTIME_FILE.exists():
        return
    try:
        payload = json.loads(WEB_RUNTIME_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        payload = {}

    if isinstance(payload, dict):
        recorded_pid = payload.get("pid")
        if isinstance(recorded_pid, int) and recorded_pid not in (0, os.getpid()):
            return

    try:
        WEB_RUNTIME_FILE.unlink()
    except OSError:
        return


def load_config() -> tuple[str, list[str]]:
    if not CONFIG_FILE.exists():
        return str(Path.home()), []

    try:
        lines = [line.strip() for line in CONFIG_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return str(Path.home()), []

    if not lines:
        return str(Path.home()), []

    source_folder = lines[0]
    destinations: list[str] = []
    for item in lines[1:]:
        normalized = normalize_path_str(item)
        if normalized and normalized not in destinations:
            destinations.append(normalized)

    return source_folder, destinations


def save_config(source_folder: str, destinations: list[str]) -> None:
    lines = [source_folder] + destinations
    CONFIG_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def media_kind(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    return "unknown"


def scan_media_files(folder: str) -> list[str]:
    root = Path(folder)
    if not root.is_dir():
        return []

    files: list[str] = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if entry.is_file() and entry.suffix.lower() in SUPPORTED_EXTENSIONS:
            files.append(str(entry))
    return files


def count_media(files: list[str]) -> tuple[int, int, int]:
    image_count = 0
    video_count = 0
    audio_count = 0
    for file_path in files:
        kind = media_kind(file_path)
        if kind == "image":
            image_count += 1
        elif kind == "video":
            video_count += 1
        elif kind == "audio":
            audio_count += 1
    return image_count, video_count, audio_count


def convert_bytes(size_bytes: int) -> str:
    if size_bytes <= 0:
        return "0 B"
    size_name = ("B", "KB", "MB", "GB", "TB")
    index = 0
    size_value = float(size_bytes)
    while size_value >= 1024 and index < len(size_name) - 1:
        size_value /= 1024
        index += 1
    return f"{size_value:.2f} {size_name[index]}"


def format_seconds(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def get_ffmpeg_local_path() -> Path:
    binary = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    return TOOLS_DIR / "bin" / binary


def get_ffprobe_local_path() -> Path:
    binary = "ffprobe.exe" if os.name == "nt" else "ffprobe"
    return TOOLS_DIR / "bin" / binary


def find_ffmpeg() -> str | None:
    search_roots = [
        APP_HOME,
        RESOURCE_DIR,
        SCRIPT_DIR,
        APP_HOME / "ffmpeg",
        APP_HOME / "ffmpeg" / "bin",
        TOOLS_DIR,
        TOOLS_DIR / "bin",
    ]
    names = ["ffmpeg.exe", "ffmpeg"] if os.name == "nt" else ["ffmpeg"]

    for name in names:
        system_path = shutil.which(name)
        if system_path:
            return system_path

        for root in search_roots:
            candidate = root / name
            if candidate.exists():
                return str(candidate)
    return None


def run_ffmpeg(ffmpeg_path: str, args: list[str]) -> tuple[bool, str]:
    command = [ffmpeg_path] + args
    startup_kwargs: dict[str, object] = {}
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        startup_kwargs = {
            "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
            "startupinfo": startupinfo,
        }
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        **startup_kwargs,
    )
    output = result.stderr.decode("utf-8", errors="ignore")
    return result.returncode == 0, output


def ffmpeg_cache_key(source: Path, profile: str) -> str:
    stat = source.stat()
    payload = f"{source.resolve()}|{stat.st_mtime_ns}|{stat.st_size}|{profile}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def ensure_preview_image(source_file: str, ffmpeg_path: str | None) -> str | None:
    source = Path(source_file)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    cached = CACHE_DIR / f"{ffmpeg_cache_key(source, 'image')}.jpg"
    if cached.exists():
        return str(cached)

    if ffmpeg_path:
        ok, _ = run_ffmpeg(
            ffmpeg_path,
            [
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(source),
                "-frames:v",
                "1",
                str(cached),
            ],
        )
        if ok and cached.exists():
            return str(cached)

    try:
        with Image.open(source) as img:
            rgb = img.convert("RGB")
            rgb.save(cached, format="JPEG", quality=92)
            return str(cached)
    except (OSError, UnidentifiedImageError):
        return None


def needs_video_transcode(source_file: str) -> bool:
    """Check if video needs transcoding for browser playback."""
    return Path(source_file).suffix.lower() not in BROWSER_NATIVE_VIDEO


def needs_audio_transcode(source_file: str) -> bool:
    """Check if audio needs transcoding for browser playback."""
    return Path(source_file).suffix.lower() not in BROWSER_NATIVE_AUDIO


def ensure_preview_video(source_file: str, ffmpeg_path: str | None, force_transcode: bool) -> str:
    source = Path(source_file)

    # Skip transcoding if browser can play natively and force is off
    if not force_transcode and not needs_video_transcode(source_file):
        return str(source)

    if not ffmpeg_path:
        return str(source)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = CACHE_DIR / f"{ffmpeg_cache_key(source, 'video_mp4')}.mp4"
    if cached.exists():
        return str(cached)

    ok, _ = run_ffmpeg(
        ffmpeg_path,
        [
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(cached),
        ],
    )
    if ok and cached.exists():
        return str(cached)

    return str(source)


def ensure_preview_audio(source_file: str, ffmpeg_path: str | None, force_transcode: bool) -> str:
    source = Path(source_file)

    # Skip transcoding if browser can play natively and force is off
    if not force_transcode and not needs_audio_transcode(source_file):
        return str(source)

    if not ffmpeg_path:
        return str(source)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = CACHE_DIR / f"{ffmpeg_cache_key(source, 'audio_mp3')}.mp3"
    if cached.exists():
        return str(cached)

    ok, _ = run_ffmpeg(
        ffmpeg_path,
        [
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-vn",
            "-c:a",
            "libmp3lame",
            "-q:a",
            "4",
            str(cached),
        ],
    )
    if ok and cached.exists():
        return str(cached)

    return str(source)


def parse_log_records(logfile: str) -> list[MoveRecord]:
    path = Path(logfile)
    if not path.exists():
        return []

    records: list[MoveRecord] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if " -> " not in line:
                continue
            source, destination = line.split(" -> ", 1)
            records.append(MoveRecord(source_path=source.strip(), destination_path=destination.strip()))
    except OSError:
        return []

    return records


def append_log_record(logfile: str, record: MoveRecord) -> None:
    path = Path(logfile)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{record.source_path} -> {record.destination_path}\n")


def write_log_records(logfile: str, records: list[MoveRecord]) -> None:
    path = Path(logfile)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(f"{r.source_path} -> {r.destination_path}" for r in records)
    if content:
        content += "\n"
    path.write_text(content, encoding="utf-8")


def remove_log_by_destination(logfile: str, destination_path: str) -> None:
    records = parse_log_records(logfile)
    filtered = [r for r in records if os.path.normcase(r.destination_path) != os.path.normcase(destination_path)]
    write_log_records(logfile, filtered)


def unique_destination_path(destination_dir: str, filename: str) -> str:
    root = Path(destination_dir)
    root.mkdir(parents=True, exist_ok=True)

    base = Path(filename).stem
    ext = Path(filename).suffix
    candidate = root / filename
    index = 1
    while candidate.exists():
        candidate = root / f"{base}_{index}{ext}"
        index += 1
    return str(candidate)


def try_move(source: str, destination: str) -> tuple[bool, str | None]:
    try:
        shutil.move(source, destination)
        return True, None
    except OSError as exc:
        return False, str(exc)


def install_ffmpeg_windows(status_callback: Callable[[str], None] | None = None) -> tuple[bool, str]:
    if os.name != "nt":
        return False, "Installer otomatis FFmpeg saat ini hanya disiapkan untuk Windows."

    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    download_zip = TOOLS_DIR / "ffmpeg_download.zip"
    extract_dir = TOOLS_DIR / "_extract"
    bin_dir = TOOLS_DIR / "bin"

    try:
        if status_callback:
            status_callback("Downloading FFmpeg...")
        with urllib.request.urlopen(WINDOWS_FFMPEG_URL, timeout=120) as response:
            with download_zip.open("wb") as output:
                shutil.copyfileobj(response, output)

        if extract_dir.exists():
            shutil.rmtree(extract_dir, ignore_errors=True)
        extract_dir.mkdir(parents=True, exist_ok=True)

        if status_callback:
            status_callback("Extracting FFmpeg package...")
        with zipfile.ZipFile(download_zip, "r") as archive:
            archive.extractall(extract_dir)

        ffmpeg_candidates = list(extract_dir.rglob("ffmpeg.exe"))
        ffprobe_candidates = list(extract_dir.rglob("ffprobe.exe"))

        if not ffmpeg_candidates:
            return False, "Gagal menemukan ffmpeg.exe di paket yang diunduh."

        bin_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ffmpeg_candidates[0], bin_dir / "ffmpeg.exe")
        if ffprobe_candidates:
            shutil.copy2(ffprobe_candidates[0], bin_dir / "ffprobe.exe")

        if download_zip.exists():
            download_zip.unlink()
        if extract_dir.exists():
            shutil.rmtree(extract_dir, ignore_errors=True)

        return True, f"FFmpeg berhasil dipasang di {bin_dir}"
    except Exception as exc:
        return False, f"Install FFmpeg gagal: {exc}"


def init_state() -> None:
    if st.session_state.get("initialized"):
        return

    source_folder, destinations = load_config()
    if not source_folder:
        source_folder = str(Path.home())

    logs = discover_logfiles(source_folder)
    logfile = DEFAULT_LOGFILE or (str(logs[0]) if logs else str(resolve_logfile_for_folder(source_folder)))
    logfile_path = Path(logfile)
    logfile_path.parent.mkdir(parents=True, exist_ok=True)
    logfile_path.touch(exist_ok=True)

    st.session_state.source_folder_input = source_folder
    st.session_state.source_folder = source_folder if is_valid_dir(source_folder) else ""
    st.session_state.destinations = destinations
    st.session_state.files = scan_media_files(st.session_state.source_folder) if st.session_state.source_folder else []
    st.session_state.current_index = 0
    st.session_state.undo_record = None
    st.session_state.logfile = logfile
    st.session_state.zoom_percent = 100
    st.session_state.status_text = "Ready"
    # FIX: Default to False — browser-native formats (mp4, webm, mp3, wav, etc.)
    # play directly without FFmpeg transcoding, which is much faster.
    # Force transcode only needed for exotic codecs (mkv, avi, flac, etc.).
    st.session_state.force_video_transcode = False
    st.session_state.force_audio_transcode = False
    st.session_state.new_destination_input = ""
    st.session_state.initialized = True


def set_status(text: str) -> None:
    st.session_state.status_text = text


def current_file() -> str | None:
    files = st.session_state.files
    if not files:
        return None
    idx = st.session_state.current_index
    idx = max(0, min(idx, len(files) - 1))
    st.session_state.current_index = idx
    return files[idx]


def action_load_folder() -> None:
    folder = normalize_path_str(st.session_state.source_folder_input)
    if not is_valid_dir(folder):
        set_status("Folder tidak valid")
        return

    st.session_state.source_folder = folder
    st.session_state.files = scan_media_files(folder)
    st.session_state.current_index = 0
    st.session_state.zoom_percent = 100
    st.session_state.logfile = str(resolve_logfile_for_folder(folder))
    save_config(folder, st.session_state.destinations)

    image_count, video_count, audio_count = count_media(st.session_state.files)
    set_status(
        f"Loaded {len(st.session_state.files)} files (image {image_count}, video {video_count}, audio {audio_count})"
    )


def action_prev() -> None:
    if not st.session_state.files:
        return
    st.session_state.current_index = (st.session_state.current_index - 1) % len(st.session_state.files)


def action_next() -> None:
    if not st.session_state.files:
        return
    st.session_state.current_index = (st.session_state.current_index + 1) % len(st.session_state.files)


def action_add_destination() -> None:
    raw_path = normalize_path_str(st.session_state.new_destination_input)
    if not raw_path:
        return

    if raw_path in st.session_state.destinations:
        set_status("Destination sudah ada")
        return

    st.session_state.destinations.append(raw_path)
    save_config(st.session_state.source_folder_input, st.session_state.destinations)
    set_status(f"Destination ditambahkan: {raw_path}")


def action_remove_destination(destination: str) -> None:
    if destination in st.session_state.destinations:
        st.session_state.destinations.remove(destination)
        save_config(st.session_state.source_folder_input, st.session_state.destinations)
        set_status(f"Destination dihapus: {destination}")


def action_move_current(destination: str) -> None:
    source = current_file()
    if not source:
        set_status("Tidak ada file untuk dipindah")
        return

    if not is_valid_dir(destination):
        try:
            Path(destination).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            set_status(f"Gagal membuat folder destination: {exc}")
            return

    final_destination = unique_destination_path(destination, Path(source).name)
    success, error_text = try_move(source, final_destination)
    if not success:
        set_status(f"Move gagal: {error_text}")
        return

    record = MoveRecord(source_path=source, destination_path=final_destination)
    st.session_state.undo_record = record
    append_log_record(st.session_state.logfile, record)

    st.session_state.files.pop(st.session_state.current_index)
    if st.session_state.files and st.session_state.current_index >= len(st.session_state.files):
        st.session_state.current_index = len(st.session_state.files) - 1
    elif not st.session_state.files:
        st.session_state.current_index = 0

    set_status(f"Moved: {Path(source).name} -> {Path(destination).name}")


def try_restore_to_source(record: MoveRecord) -> tuple[bool, str]:
    restore_path = record.source_path
    if Path(restore_path).exists():
        restore_path = unique_destination_path(str(Path(restore_path).parent), Path(restore_path).name)

    success, error_text = try_move(record.destination_path, restore_path)
    if not success:
        return False, error_text or "Unknown error"

    if st.session_state.source_folder:
        src_parent = os.path.normcase(str(Path(restore_path).parent))
        loaded_parent = os.path.normcase(st.session_state.source_folder)
        if src_parent == loaded_parent and restore_path not in st.session_state.files:
            st.session_state.files.append(restore_path)
            st.session_state.files.sort(key=lambda p: Path(p).name.lower())
            st.session_state.current_index = st.session_state.files.index(restore_path)

    remove_log_by_destination(st.session_state.logfile, record.destination_path)
    set_status(f"Undo sukses: {Path(restore_path).name}")
    return True, restore_path


def action_undo_last() -> None:
    record: MoveRecord | None = st.session_state.undo_record
    if not record:
        set_status("Tidak ada move terakhir untuk undo")
        return

    if not Path(record.destination_path).exists():
        st.session_state.undo_record = None
        set_status("Undo gagal: file destination sudah tidak ada")
        return

    success, _ = try_restore_to_source(record)
    if success:
        st.session_state.undo_record = None


def action_undo_log(original_index: int) -> None:
    """Undo a log entry by its original (chronological) index in the log file."""
    records = parse_log_records(st.session_state.logfile)
    if not records:
        set_status("Log kosong")
        return

    if original_index < 0 or original_index >= len(records):
        set_status("Index log tidak valid")
        return

    record = records[original_index]
    if not Path(record.destination_path).exists():
        set_status("Undo log gagal: file destination tidak ada")
        return

    success, _ = try_restore_to_source(record)
    if success:
        if st.session_state.undo_record and (
            os.path.normcase(st.session_state.undo_record.destination_path) == os.path.normcase(record.destination_path)
        ):
            st.session_state.undo_record = None


def inject_styles() -> None:
    st.markdown(
        """
        <style>
            :root {
                --app-bg: #f4f8ff;
                --panel-bg: #ffffff;
                --panel-bg-soft: #f7fbff;
                --panel-bg-alt: #f2f7ff;
                --border: #dbe5f3;
                --text: #1f3046;
                --muted: #5a6d86;
                --chip-bg: #f4f8ff;
                --chip-text: #223247;
                --audio-bg: linear-gradient(150deg,#edf4ff 0%, #e3efff 100%);
                --button-bg: #ffffff;
                --button-text: #1f3046;
                --input-bg: #ffffff;
                --input-text: #1f3046;
            }
            @media (prefers-color-scheme: dark) {
                :root {
                    --app-bg: #0f1724;
                    --panel-bg: #141e2c;
                    --panel-bg-soft: #172334;
                    --panel-bg-alt: #101a29;
                    --border: #2a3b52;
                    --text: #e7eef8;
                    --muted: #a6b6cb;
                    --chip-bg: #172334;
                    --chip-text: #dfe8f5;
                    --audio-bg: linear-gradient(150deg,#17283d 0%, #122032 100%);
                    --button-bg: #172334;
                    --button-text: #e7eef8;
                    --input-bg: #101a29;
                    --input-text: #e7eef8;
                }
            }
            [data-testid="stAppViewContainer"],
            [data-testid="stAppViewContainer"] .main {
                background: var(--app-bg);
            }
            [data-testid="stSidebar"] {
                background: var(--panel-bg-soft);
            }
            [data-testid="stSidebar"] * {
                color: var(--text);
            }
            .app-shell {
                padding: 0.25rem 0.2rem 7.5rem 0.2rem;
                color: var(--text);
            }
            .header-card {
                border: 1px solid var(--border);
                border-radius: 14px;
                padding: 14px 16px;
                background: linear-gradient(145deg, var(--panel-bg) 0%, var(--panel-bg-soft) 100%);
                margin-bottom: 10px;
                color: var(--text);
            }
            .badge {
                display: inline-block;
                font-size: 0.72rem;
                font-weight: 700;
                border-radius: 999px;
                padding: 4px 10px;
                margin-right: 8px;
            }
            .badge-image {background:#dcfce7; color:#166534;}
            .badge-video {background:#fef3c7; color:#92400e;}
            .badge-audio {background:#dbeafe; color:#1d4ed8;}
            .badge-unknown {background:#e2e8f0; color:#334155;}
            @media (prefers-color-scheme: dark) {
                .badge-image {background:#1f4e35; color:#c8f2d8;}
                .badge-video {background:#5b421b; color:#ffd79c;}
                .badge-audio {background:#1f3957; color:#cde3ff;}
                .badge-unknown {background:#303a46; color:#d8e0ea;}
            }
            .muted {color:var(--muted); font-size:0.85rem;}
            .audio-card {
                border: 1px solid var(--border);
                background: var(--audio-bg);
                border-radius: 14px;
                padding: 18px;
                text-align: center;
                margin-bottom: 8px;
                color: var(--text);
            }
            .status-chip {
                border: 1px solid var(--border);
                border-radius: 999px;
                padding: 6px 12px;
                font-size: 0.84rem;
                color: var(--chip-text);
                background: var(--chip-bg);
                display: inline-block;
            }
            .topbar {
                display:flex;
                align-items:center;
                justify-content:space-between;
                gap:12px;
                margin-bottom:10px;
            }
            .topbar-title {
                font-size: 1.02rem;
                font-weight: 700;
                color: var(--text);
            }
            .section-card {
                border: 1px solid var(--border);
                border-radius: 16px;
                padding: 14px 16px;
                background: linear-gradient(180deg, var(--panel-bg) 0%, var(--panel-bg-soft) 100%);
                color: var(--text);
            }
            .sticky-dest-title {
                font-size: 0.95rem;
                font-weight: 700;
                color: var(--text);
                margin-bottom: 0.4rem;
            }
            .st-key-sticky_move_bar {
                position: fixed;
                left: 1rem;
                right: 1rem;
                bottom: 0.6rem;
                z-index: 60;
                background: linear-gradient(180deg, var(--panel-bg) 0%, var(--panel-bg-soft) 100%);
                border: 1px solid var(--border);
                border-radius: 18px;
                padding: 0.85rem 0.95rem;
                box-shadow: 0 18px 36px rgba(15, 23, 36, 0.14);
                backdrop-filter: blur(8px);
                max-height: min(38vh, 18rem);
                overflow-y: auto;
            }
            .st-key-sticky_move_bar > div {
                gap: 0.45rem;
            }
            .st-emotion-cache-1n6tfoc{
                width: auto;
            }
            .padding-top{
                margin-top:200px;
            }
            @media (max-width: 1100px) {
                .app-shell {
                    padding-bottom: 9.5rem;
                }
                .st-key-sticky_move_bar {
                    left: 1rem;
                    right: 1rem;
                    bottom: 0.3rem;
                    padding: 0.75rem 0.8rem;
                }
                .st-key-sticky_move_bar {
                    z-index: 60;
                }
                .st-emotion-cache-1n6tfoc{
                    width: auto;
                }
                .st-key-sticky_move_bar {
                    max-height: min(38vh, 6rem);
                }
            }
            @media (max-width: 610px) {
                .st-emotion-cache-1n6tfoc{
                    flex-flow: row;
                }
            }
            .log-detail-card {
                border: 1px solid var(--border);
                border-radius: 16px;
                padding: 14px 16px;
                background: linear-gradient(180deg, var(--panel-bg) 0%, var(--panel-bg-alt) 100%);
                min-height: 100%;
                color: var(--text);
            }
            .detail-label {
                color:var(--muted);
                font-size:0.78rem;
                text-transform:uppercase;
                letter-spacing:0.04em;
                margin-bottom:2px;
            }
            .detail-value {
                color:var(--text);
                font-size:0.92rem;
                margin-bottom:10px;
                word-break:break-word;
            }
            .stButton > button,
            .stForm button {
                background: var(--button-bg);
                color: var(--button-text);
                border: 1px solid var(--border);
            }
            .stButton > button:hover,
            .stForm button:hover {
                border-color: var(--muted);
                color: var(--button-text);
            }
            .stTextInput input,
            .stSelectbox [data-baseweb="select"] > div,
            .stSelectbox [data-baseweb="select"] input {
                background: var(--input-bg);
                color: var(--input-text);
                border-color: var(--border);
            }
            .stMarkdown, .stCaption, .stText, .stSubheader, label, p, h1, h2, h3, h4 {
                color: var(--text);
            }
            [data-testid="stSidebar"] .stTextInput input,
            [data-testid="stSidebar"] .stSelectbox [data-baseweb="select"] > div {
                background: var(--input-bg);
                color: var(--input-text);
            }
            [data-testid="stAlert"] {
                background: var(--panel-bg-soft);
                color: var(--text);
                border: 1px solid var(--border);
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_media_header(file_path: str, kind: str) -> None:
    badge_class = {
        "image": "badge-image",
        "video": "badge-video",
        "audio": "badge-audio",
    }.get(kind, "badge-unknown")

    name = Path(file_path).name
    file_size = convert_bytes(Path(file_path).stat().st_size) if Path(file_path).exists() else "-"
    modified = datetime.fromtimestamp(Path(file_path).stat().st_mtime).strftime("%d.%m.%Y %H:%M") if Path(file_path).exists() else "-"

    st.markdown(
        f"""
        <div class="header-card">
            <div><span class="badge {badge_class}">{kind.upper()}</span></div>
            <h4 style="margin:8px 0 4px 0;">{html.escape(name)}</h4>
            <div class="muted">Size: {file_size} &nbsp;|&nbsp; Modified: {modified}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_image_preview(file_path: str, ffmpeg_path: str | None) -> None:
    preview = ensure_preview_image(file_path, ffmpeg_path)
    if not preview:
        st.error("Gagal render image preview")
        return

    zoom_cols = st.columns([1, 1, 2])
    if zoom_cols[0].button("- Zoom", use_container_width=True):
        st.session_state.zoom_percent = max(20, st.session_state.zoom_percent - 10)
        safe_rerun()
    if zoom_cols[1].button("+ Zoom", use_container_width=True):
        st.session_state.zoom_percent = min(800, st.session_state.zoom_percent + 10)
        safe_rerun()
    zoom = zoom_cols[2].slider("Zoom", min_value=20, max_value=800, value=st.session_state.zoom_percent, step=10)
    st.session_state.zoom_percent = zoom

    with Image.open(preview) as img:
        target_width = int(max(240, min(2200, img.width * (zoom / 100.0))))
        st.image(img, width=target_width, use_container_width=False)


def render_video_preview(file_path: str, ffmpeg_path: str | None) -> None:
    preview = ensure_preview_video(
        file_path,
        ffmpeg_path=ffmpeg_path,
        force_transcode=st.session_state.force_video_transcode,
    )

    # Caption rendered OUTSIDE the keyed container so it always sits above the
    # video and never floats beside it on narrow / mobile viewports.
    if preview != file_path:
        st.caption("Video ditranscode via FFmpeg ke MP4 browser-compatible.")
    elif needs_video_transcode(file_path):
        suffix = Path(file_path).suffix.lower()
        st.caption(f"ℹ️ Format {suffix} mungkin tidak bisa diputar langsung. Aktifkan 'Force transcode video' di sidebar jika gagal.")
    else:
        st.caption("Video diputar langsung dari file asli (native browser format).")

    # Keyed container so Streamlit unmounts the old video element immediately
    # when the file changes, preventing the "lingering old video" effect.
    file_key = hashlib.sha1(file_path.encode()).hexdigest()[:12]
    with st.container(key=f"video_wrap_{file_key}"):
        st.video(preview, autoplay=True)


def render_audio_preview(file_path: str, ffmpeg_path: str | None) -> None:
    preview = ensure_preview_audio(
        file_path,
        ffmpeg_path=ffmpeg_path,
        force_transcode=st.session_state.force_audio_transcode,
    )

    st.markdown(
        """
        <div class="audio-card">
            <div style="font-size:28px; font-weight:700; color:#1d4ed8; margin-bottom:6px;">AUDIO</div>
            <div style="font-size:13px; color:#445973;">Audio marker aktif. Kontrol play/pause gunakan player di bawah.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # FIX: Key the audio element by file so it re-mounts when file changes
    file_key = hashlib.sha1(file_path.encode()).hexdigest()[:12]
    with st.container(key=f"audio_wrap_{file_key}"):
        st.audio(preview)
        if preview != file_path:
            st.caption("Audio ditranscode via FFmpeg ke MP3 browser-compatible.")
        else:
            suffix = Path(file_path).suffix.lower()
            if needs_audio_transcode(file_path):
                st.caption(f"ℹ️ Format {suffix} mungkin tidak kompatibel. Aktifkan 'Force transcode audio' di sidebar jika gagal.")


def render_sticky_destination_bar() -> None:
    destinations = st.session_state.destinations
    if not destinations:
        st.info("Tambahkan destination folder dulu untuk mulai sorting.")
        return

    sticky_bar = st.container(key="sticky_move_bar")
    st.markdown('<div class="sticky-dest-title">Move current file to:</div>', unsafe_allow_html=True)
    with sticky_bar:
        columns_per_row = 3
        for start in range(0, len(destinations), columns_per_row):
            row_items = destinations[start : start + columns_per_row]
            cols = st.columns(columns_per_row)
            for idx, destination in enumerate(row_items):
                label = Path(destination).name or destination
                key = f"move_{start}_{idx}_{destination}"
                if cols[idx].button(f"Move -> {label}", key=key, use_container_width=True):
                    action_move_current(destination)
                    safe_rerun()


def render_log_entry_details(record: MoveRecord) -> None:
    destination = Path(record.destination_path)
    destination_exists = destination.exists()
    destination_kind = media_kind(str(destination)) if destination_exists else "unknown"
    size_text = convert_bytes(destination.stat().st_size) if destination_exists else "-"
    modified_text = (
        datetime.fromtimestamp(destination.stat().st_mtime).strftime("%d.%m.%Y %H:%M") if destination_exists else "-"
    )

    st.markdown(
        f"""
        <div class="log-detail-card">
            <div class="detail-label">Source</div>
            <div class="detail-value">{html.escape(record.source_path)}</div>
            <div class="detail-label">Destination</div>
            <div class="detail-value">{html.escape(record.destination_path)}</div>
            <div class="detail-label">Status</div>
            <div class="detail-value">{"Exists" if destination_exists else "Missing destination file"}</div>
            <div class="detail-label">Media</div>
            <div class="detail-value">{destination_kind.upper()}</div>
            <div class="detail-label">Size / Modified</div>
            <div class="detail-value">{size_text} | {modified_text}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

def render_log_entry_preview(record: MoveRecord, ffmpeg_path: str | None) -> None:
    destination = Path(record.destination_path)
    destination_exists = destination.exists()
    destination_kind = media_kind(str(destination)) if destination_exists else "unknown"

    if destination_exists and destination_kind == "image":
        preview = ensure_preview_image(str(destination), ffmpeg_path)
        if preview:
            st.image(preview, use_container_width=True)
        else:
            st.info("Preview image tidak bisa dirender.")
    elif destination_exists:
        st.info("Preview detail gabungan saat ini ditampilkan untuk file gambar. File non-image tetap menampilkan metadata lengkap.")
    else:
        st.warning("Destination file sudah tidak ada, jadi preview tidak tersedia.")


def render_log_section(ffmpeg_path: str | None, show_heading: bool = True) -> None:
    if show_heading:
        st.subheader("Move Log")

    # FIX: Read records in original order, then display DESC (newest first)
    records = parse_log_records(st.session_state.logfile)

    if not records:
        st.info("Log masih kosong.")
        return

    # Build reversed view for display; keep mapping back to original index for undo
    reversed_indices = list(range(len(records) - 1, -1, -1))  # [last, ..., 0]
    reversed_records = [records[i] for i in reversed_indices]

    display_options = list(range(len(reversed_records)))
    selected_display = st.selectbox(
        "Pilih entry untuk undo (terbaru di atas)",
        options=display_options,
        format_func=lambda i: (
            f"[{len(records) - i}] {Path(reversed_records[i].source_path).name}"
            f" -> {Path(reversed_records[i].destination_path).parent.name}"
        ),
    )

    selected_record = reversed_records[selected_display]
    original_index = reversed_indices[selected_display]

    detail_cols = st.columns([1.1, 0.9])
    with detail_cols[0]:
        render_log_entry_details(selected_record)
    with detail_cols[1]:
        st.markdown('<div class="section-card">Preview / Detail gabungan</div>', unsafe_allow_html=True)
        render_log_entry_preview(selected_record, ffmpeg_path)

    if st.button("Undo selected log entry", use_container_width=True):
        action_undo_log(original_index)
        safe_rerun()

    st.caption(f"Total entries: {len(records)}")


def render_log_expander(ffmpeg_path: str | None) -> None:
    with st.expander("Move Log", expanded=False):
        render_log_section(ffmpeg_path, show_heading=False)


def render_sidebar(ffmpeg_path: str | None) -> None:
    st.sidebar.title("Fair Sorting Web")

    st.sidebar.markdown("### FFmpeg Codec")
    if ffmpeg_path:
        st.sidebar.success(f"FFmpeg active\n\n{ffmpeg_path}")
    else:
        st.sidebar.warning("FFmpeg belum tersedia.")

    if IS_FROZEN:
        st.sidebar.info("Bundled mode aktif. FFmpeg mengikuti file distribusi aplikasi.")
    elif os.name == "nt" and st.sidebar.button("Install FFmpeg ke folder project", use_container_width=True):
        with st.spinner("Mengunduh dan memasang FFmpeg..."):
            success, message = install_ffmpeg_windows(set_status)
        if success:
            st.sidebar.success(message)
        else:
            st.sidebar.error(message)
        safe_rerun()
    elif os.name != "nt":
        st.sidebar.info("Untuk non-Windows, pasang FFmpeg manual atau siapkan di PATH sistem.")

    st.session_state.force_video_transcode = st.sidebar.toggle(
        "Force transcode video (FFmpeg)",
        value=st.session_state.force_video_transcode,
        help="Aktifkan untuk format non-browser (mkv, avi, wmv, dll). MP4/WebM tidak perlu ini.",
    )
    st.session_state.force_audio_transcode = st.sidebar.toggle(
        "Force transcode audio (FFmpeg)",
        value=st.session_state.force_audio_transcode,
        help="Aktifkan untuk format non-browser (flac, wma, aac, dll). MP3/WAV/OGG tidak perlu ini.",
    )

    st.sidebar.markdown("---")
    st.sidebar.markdown("### Source Folder")
    st.sidebar.text_input("Path source", key="source_folder_input")
    if st.sidebar.button("Load Folder", use_container_width=True):
        action_load_folder()
        safe_rerun()

    st.sidebar.markdown("---")
    st.sidebar.markdown("### Destination")
    with st.sidebar.form("destination_form", clear_on_submit=True):
        st.text_input("Tambah destination path", key="new_destination_input")
        add_destination = st.form_submit_button("Add Destination", use_container_width=True)
    if add_destination:
        action_add_destination()
        safe_rerun()

    for idx, destination in enumerate(st.session_state.destinations):
        row = st.sidebar.columns([4, 1])
        row[0].caption(destination)
        if row[1].button("X", key=f"remove_dest_{idx}"):
            action_remove_destination(destination)
            safe_rerun()

    st.sidebar.markdown("---")
    st.sidebar.markdown("### Logfile")
    logs = discover_logfiles(st.session_state.source_folder)
    current_log = Path(st.session_state.logfile)
    if current_log not in logs:
        logs.insert(0, current_log)
    selected_log = st.sidebar.selectbox(
        "Choose logfile",
        options=[str(log) for log in logs] if logs else [st.session_state.logfile],
        index=0,
    )
    if selected_log != st.session_state.logfile:
        st.session_state.logfile = selected_log
        safe_rerun()

    if st.sidebar.button("Create New Logfile", use_container_width=True):
        st.session_state.logfile = str(create_default_logfile(st.session_state.source_folder))
        set_status("New logfile created")
        safe_rerun()

    st.sidebar.markdown("---")
    if st.sidebar.button("Undo Last Move", use_container_width=True):
        action_undo_last()
        safe_rerun()


def main() -> None:
    st.set_page_config(page_title="Fair Sorting Web", page_icon="\U0001F5BC\uFE0F", layout="wide")
    init_state()
    inject_styles()

    ffmpeg_path = find_ffmpeg()

    render_sidebar(ffmpeg_path)

    st.markdown('<div class="app-shell" style="display:none;">', unsafe_allow_html=True)
    st.markdown(
        f"""
        <div class="topbar">
            <div class="topbar-title">Fair Sorting Web Viewer</div>
            <span class="status-chip">Status: {html.escape(st.session_state.status_text)}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    files = st.session_state.files
    if not files:
        st.warning("Belum ada media yang dimuat. Masukkan source folder di sidebar lalu klik Load Folder.")
        render_log_expander(ffmpeg_path)
        st.markdown("</div>", unsafe_allow_html=True)
        return

    nav_cols = st.columns([1, 2, 1])
    if nav_cols[0].button("Previous", use_container_width=True):
        action_prev()
        safe_rerun()

    nav_cols[1].markdown(
        f"<div style='text-align:center; padding-top:10px; font-weight:600;'>"
        f"{st.session_state.current_index + 1} / {len(files)}"
        f"</div>",
        unsafe_allow_html=True,
    )

    if nav_cols[2].button("Next", use_container_width=True):
        action_next()
        safe_rerun()

    file_path = current_file()
    if not file_path:
        st.error("File index tidak valid.")
        st.markdown("</div>", unsafe_allow_html=True)
        return

    kind = media_kind(file_path)
    render_media_header(file_path, kind)

    if kind == "image":
        render_image_preview(file_path, ffmpeg_path)
    elif kind == "video":
        render_video_preview(file_path, ffmpeg_path)
    elif kind == "audio":
        render_audio_preview(file_path, ffmpeg_path)
    else:
        st.error("Format media belum didukung preview web.")

    render_sticky_destination_bar()

    st.markdown("---")
    render_log_expander(ffmpeg_path)

    st.markdown("</div>", unsafe_allow_html=True)
    st.markdown('<div class="padding-top">', unsafe_allow_html=True)


def _run_streamlit_cli() -> int:
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
    except Exception:
        get_script_run_ctx = None

    if get_script_run_ctx is not None and get_script_run_ctx() is not None:
        return 0

    from streamlit.web.cli import main as streamlit_main

    target_script = RESOURCE_DIR / "web_sorter.py"
    if not target_script.exists():
        target_script = Path(__file__).resolve()

    sys.argv = [
        "streamlit",
        "run",
        str(target_script),
        "--global.developmentMode",
        "false",
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
        "--server.fileWatcherType",
        "none",
        "--server.address",
        DEFAULT_BIND_ADDRESS,
        "--server.port",
        DEFAULT_PORT,
    ]
    return streamlit_main()


if __name__ == "__main__":
    write_runtime_record()
    atexit.register(clear_runtime_record)
    exit_code = _run_streamlit_cli()
    if exit_code != 0:
        raise SystemExit(exit_code)
    main()
