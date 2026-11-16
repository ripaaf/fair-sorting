"""Media knowledge: file types, metadata, thumbnails and browser-friendly previews.

Design notes
------------
* Browser-native formats are streamed as-is (zero work, instant).
* Everything else is converted lazily and cached on disk, keyed by
  path + mtime + size so edits invalidate the cache automatically.
* Video/audio conversion runs as leased background jobs: if the viewer stops
  asking for a job (user moved on) the ffmpeg process is killed.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.request
import zipfile
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageOps

from .env import (
    APP_HOME,
    CACHE_DIR,
    FROZEN,
    IS_WINDOWS,
    PACKAGE_DIR,
    RESOURCE_DIR,
    hidden_process_kwargs,
)

try:  # HEIC/HEIF/AVIF from phones
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIF_ENABLED = True
except Exception:  # pragma: no cover - optional dependency
    HEIF_ENABLED = False

# Big panoramas are legitimate here; keep a ceiling against absurd files.
Image.MAX_IMAGE_PIXELS = 500_000_000

IMAGE_EXTS = frozenset(
    """.jpg .jpeg .png .gif .bmp .tiff .tif .webp .heic .heif .avif .jp2 .j2k .jpf .jfif
    .ico .tga .ppm .pgm .pbm .pnm .hdr .exr .psd .raw .cr2 .nef .orf .arw .rw2 .dng .xcf .pcx""".split()
)
VIDEO_EXTS = frozenset(
    """.mp4 .avi .mkv .webm .mov .flv .3gp .wmv .rmvb .m4v .mpeg .mpg .divx .ogv .ts .m2ts
    .vob .mts .asf .rm .ogm .mxf .f4v""".split()
)
AUDIO_EXTS = frozenset(".mp3 .wav .m4a .ogg .wma .aac .ape .alac .mid .ac3 .amr .ra .opus .flac".split())
RAW_EXTS = frozenset(".raw .cr2 .nef .orf .arw .rw2 .dng".split())

KIND_BY_EXT: dict[str, str] = {}
KIND_BY_EXT.update({ext: "image" for ext in IMAGE_EXTS})
KIND_BY_EXT.update({ext: "video" for ext in VIDEO_EXTS})
KIND_BY_EXT.update({ext: "audio" for ext in AUDIO_EXTS})

# What a Chromium-based view (WebView2, Edge, Chrome, Android) shows directly.
WEB_IMAGE_EXTS = frozenset(".jpg .jpeg .jfif .png .gif .bmp .webp .avif .ico".split())
# Containers worth trying natively first; the client falls back to conversion on error.
WEB_VIDEO_EXTS = frozenset(".mp4 .m4v .mov .webm .ogv .mkv".split())
WEB_AUDIO_EXTS = frozenset(".mp3 .wav .m4a .ogg .aac .opus .flac".split())

MIME_BY_EXT = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".jfif": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".ico": "image/x-icon",
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/mp4",  # Chromium plays most QuickTime/H.264 when labelled mp4
    ".webm": "video/webm",
    ".mkv": "video/webm",
    ".ogv": "video/ogg",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
}

THUMB_DIR = CACHE_DIR / "thumbs"
VIEW_DIR = CACHE_DIR / "views"
PLAY_DIR = CACHE_DIR / "playback"
TOOLS_DIR = APP_HOME / "tools" / "ffmpeg"
FFMPEG_DOWNLOAD_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"


class MediaError(Exception):
    pass


def media_kind(path: str) -> str | None:
    return KIND_BY_EXT.get(os.path.splitext(path)[1].lower())


def ext_of(path: str) -> str:
    return os.path.splitext(path)[1].lower()


def cache_key(path: str, profile: str) -> str:
    st = os.stat(path)
    raw = f"{os.path.abspath(path)}|{st.st_mtime_ns}|{st.st_size}|{profile}"
    return hashlib.sha1(raw.encode("utf-8", "surrogatepass")).hexdigest()


class _KeyedLocks:
    """One lock per cache key so the same preview is never built twice at once."""

    def __init__(self):
        self._guard = threading.Lock()
        self._locks: dict[str, list] = {}

    def acquire(self, key: str) -> threading.Lock:
        with self._guard:
            entry = self._locks.get(key)
            if entry is None:
                entry = [threading.Lock(), 0]
                self._locks[key] = entry
            entry[1] += 1
        entry[0].acquire()
        return entry[0]

    def release(self, key: str):
        with self._guard:
            entry = self._locks.get(key)
            if entry is None:
                return
            entry[0].release()
            entry[1] -= 1
            if entry[1] <= 0:
                self._locks.pop(key, None)


_keyed = _KeyedLocks()
# Thumbnails are cheap individually but a filmstrip can ask for dozens at once.
_thumb_slots = threading.BoundedSemaphore(3)


# --------------------------------------------------------------------------- tools


class Tools:
    def __init__(self):
        self._lock = threading.Lock()
        self._resolved = False
        self.ffmpeg: str | None = None
        self.ffprobe: str | None = None

    def _search_dirs(self) -> list[Path]:
        dirs = []
        override = os.environ.get("FAIR_SORTING_FFMPEG", "").strip()
        if override:
            dirs.append(Path(override))
        dirs += [
            APP_HOME,
            APP_HOME / "ffmpeg" / "bin",
            APP_HOME / "ffmpeg",
            TOOLS_DIR / "bin",
            RESOURCE_DIR,
            RESOURCE_DIR / "ffmpeg" / "bin",
        ]
        if not FROZEN:
            dirs.append(PACKAGE_DIR.parent / "FINAL_BUILD" / "ffmpeg" / "bin")
        return dirs

    def _find(self, name: str) -> str | None:
        exe = f"{name}.exe" if IS_WINDOWS else name
        for folder in self._search_dirs():
            candidate = folder / exe
            if candidate.is_file():
                return str(candidate)
        found = shutil.which(name)
        if found:
            return found
        if name == "ffmpeg":
            try:
                import imageio_ffmpeg  # type: ignore

                return imageio_ffmpeg.get_ffmpeg_exe()
            except Exception:
                return None
        return None

    def resolve(self, force: bool = False):
        with self._lock:
            if self._resolved and not force:
                return
            self.ffmpeg = self._find("ffmpeg")
            self.ffprobe = self._find("ffprobe")
            self._resolved = True


TOOLS = Tools()


def run_tool(args: list[str], timeout: float | None = 30.0) -> tuple[int, bytes, str]:
    try:
        result = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
            **hidden_process_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return -1, b"", "timed out"
    except OSError as exc:
        return -1, b"", str(exc)
    return result.returncode, result.stdout, result.stderr.decode("utf-8", "replace")


# ------------------------------------------------------------------------ metadata

_probe_cache: OrderedDict[tuple, dict] = OrderedDict()
_probe_lock = threading.Lock()


def _parse_rate(text: str | None) -> float:
    if not text or text in ("0/0", "N/A"):
        return 0.0
    try:
        if "/" in text:
            num, den = text.split("/", 1)
            den_f = float(den)
            return float(num) / den_f if den_f else 0.0
        return float(text)
    except ValueError:
        return 0.0


def probe(path: str) -> dict:
    """ffprobe summary (duration, streams, tags). Empty dict when unavailable."""
    TOOLS.resolve()
    if not TOOLS.ffprobe:
        return {}
    try:
        st = os.stat(path)
    except OSError:
        return {}
    key = (os.path.abspath(path), st.st_mtime_ns, st.st_size)
    with _probe_lock:
        if key in _probe_cache:
            _probe_cache.move_to_end(key)
            return _probe_cache[key]

    code, out, _ = run_tool(
        [TOOLS.ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        timeout=10,
    )
    info: dict = {}
    if code == 0:
        try:
            data = json.loads(out.decode("utf-8", "replace") or "{}")
        except ValueError:
            data = {}
        fmt = data.get("format") or {}
        info["duration"] = _parse_rate(fmt.get("duration"))
        info["container"] = fmt.get("format_name", "")
        try:
            info["bitrate"] = int(fmt.get("bit_rate") or 0)
        except ValueError:
            info["bitrate"] = 0
        tags = {str(k).lower(): v for k, v in (fmt.get("tags") or {}).items()}
        info["tags"] = {k: tags[k] for k in ("title", "artist", "album", "date", "creation_time") if tags.get(k)}
        for stream in data.get("streams") or []:
            kind = stream.get("codec_type")
            disposition = stream.get("disposition") or {}
            if kind == "video" and disposition.get("attached_pic"):
                info["cover"] = True
                continue
            if kind == "video" and "video" not in info:
                width = int(stream.get("width") or 0)
                height = int(stream.get("height") or 0)
                rotation = 0
                try:
                    rotation = int(float((stream.get("tags") or {}).get("rotate", 0)))
                except ValueError:
                    rotation = 0
                for side in stream.get("side_data_list") or []:
                    if "rotation" in side:
                        try:
                            rotation = int(float(side["rotation"]))
                        except (TypeError, ValueError):
                            pass
                if abs(rotation) % 180 == 90:
                    width, height = height, width
                info["video"] = {
                    "codec": stream.get("codec_name", ""),
                    "width": width,
                    "height": height,
                    "fps": round(_parse_rate(stream.get("avg_frame_rate") or stream.get("r_frame_rate")), 3),
                }
            elif kind == "audio" and "audio" not in info:
                info["audio"] = {
                    "codec": stream.get("codec_name", ""),
                    "rate": int(stream.get("sample_rate") or 0),
                    "channels": int(stream.get("channels") or 0),
                }
    with _probe_lock:
        _probe_cache[key] = info
        while len(_probe_cache) > 512:
            _probe_cache.popitem(last=False)
    return info


def _exif_text(value) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    return str(value).strip().strip("\x00").strip()


def _fraction(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        try:
            return float(value[0]) / float(value[1])
        except Exception:
            return 0.0


def image_meta(path: str) -> dict:
    """Dimensions plus the EXIF bits people actually look at when sorting."""
    meta: dict = {}
    try:
        with Image.open(path) as im:
            width, height = im.size
            meta["format"] = im.format or ""
            frames = getattr(im, "n_frames", 1)
            if frames and frames > 1:
                meta["frames"] = frames
            exif = im.getexif()
            if exif:
                if exif.get(0x0112) in (5, 6, 7, 8):
                    width, height = height, width
                make = _exif_text(exif.get(0x010F, ""))
                model = _exif_text(exif.get(0x0110, ""))
                if model and make and model.lower().startswith(make.lower().split(" ")[0]):
                    camera = model
                else:
                    camera = " ".join(part for part in (make, model) if part)
                if camera:
                    meta["camera"] = camera
                taken = _exif_text(exif.get(0x0132, ""))
                try:
                    sub = exif.get_ifd(0x8769)
                except Exception:
                    sub = {}
                if sub:
                    taken = _exif_text(sub.get(0x9003, "")) or taken
                    exposure = _fraction(sub.get(0x829A))
                    if exposure:
                        meta["shutter"] = f"1/{round(1 / exposure)}" if exposure < 1 else f"{exposure:g}s"
                    aperture = _fraction(sub.get(0x829D))
                    if aperture:
                        meta["aperture"] = f"f/{aperture:.1f}".replace(".0", "")
                    iso = sub.get(0x8827)
                    if iso:
                        meta["iso"] = int(iso[0] if isinstance(iso, tuple) else iso)
                    focal = _fraction(sub.get(0x920A))
                    if focal:
                        meta["focal"] = f"{focal:.0f}mm"
                if taken and len(taken) >= 19 and taken[4] == ":":
                    meta["taken"] = f"{taken[0:4]}-{taken[5:7]}-{taken[8:10]}T{taken[11:19]}"
            meta["width"], meta["height"] = width, height
    except Exception:
        embedded = _embedded_jpeg(path) if ext_of(path) in RAW_EXTS else None
        if embedded is not None:
            meta["width"], meta["height"] = embedded.size
            meta["format"] = ext_of(path).lstrip(".").upper()
            meta["note"] = "embedded preview"
    return meta


def describe(path: str) -> dict:
    """Everything the inspector panel shows for one file."""
    kind = media_kind(path) or "unknown"
    try:
        st = os.stat(path)
    except OSError as exc:
        raise MediaError("File no longer exists") from exc
    info: dict = {"kind": kind, "size": st.st_size, "mtime": st.st_mtime, "ext": ext_of(path).lstrip(".")}
    if kind == "image":
        info.update(image_meta(path))
    elif kind in ("video", "audio"):
        data = probe(path)
        if data:
            info["duration"] = data.get("duration", 0.0)
            info["container"] = data.get("container", "")
            info["bitrate"] = data.get("bitrate", 0)
            if data.get("tags"):
                info["tags"] = data["tags"]
            if data.get("video") and kind == "video":
                info.update(
                    width=data["video"]["width"],
                    height=data["video"]["height"],
                    fps=data["video"]["fps"],
                    vcodec=data["video"]["codec"],
                )
            if data.get("audio"):
                info["acodec"] = data["audio"]["codec"]
                info["channels"] = data["audio"]["channels"]
                info["rate"] = data["audio"]["rate"]
            info["cover"] = bool(data.get("cover"))
    return info


# ------------------------------------------------------------------- image decode


def _to_rgb(im: Image.Image, background=(128, 128, 128)) -> Image.Image:
    if im.mode in ("I;16", "I;16B", "I;16L", "I;16N"):
        im = im.point(lambda value: value * (1 / 256)).convert("L")
    elif im.mode == "I":
        im = im.point(lambda value: value * (1 / 256)).convert("L")
    elif im.mode == "F":
        lo, hi = im.getextrema()
        scale = 255.0 / (hi - lo) if hi > lo else 1.0
        im = im.point(lambda value: (value - lo) * scale).convert("L")
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, background)
        canvas.paste(rgba, mask=rgba.getchannel("A"))
        return canvas
    if im.mode != "RGB":
        return im.convert("RGB")
    return im


def _has_alpha(im: Image.Image) -> bool:
    return im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)


def _embedded_jpeg(path: str) -> Image.Image | None:
    """Most camera RAW files carry a full-size JPEG preview; find the biggest one."""
    try:
        size = os.path.getsize(path)
        if size > 200 * 1024 * 1024:
            return None
        data = Path(path).read_bytes()
    except OSError:
        return None
    best: tuple[int, int] | None = None
    start = 0
    for _ in range(32):
        index = data.find(b"\xff\xd8\xff", start)
        if index < 0:
            break
        start = index + 3
        try:
            with Image.open(io.BytesIO(data[index : index + 40 * 1024 * 1024])) as candidate:
                area = candidate.size[0] * candidate.size[1]
        except Exception:
            continue
        if best is None or area > best[1]:
            best = (index, area)
    if best is None or best[1] < 160 * 120:
        return None
    try:
        im = Image.open(io.BytesIO(data[best[0] : best[0] + 40 * 1024 * 1024]))
        im.load()
        return ImageOps.exif_transpose(im)
    except Exception:
        return None


def _ffmpeg_frame(path: str, seek: float | None = None, max_width: int | None = None, timeout: float = 20) -> Image.Image | None:
    TOOLS.resolve()
    if not TOOLS.ffmpeg:
        return None
    args = [TOOLS.ffmpeg, "-v", "error", "-nostdin"]
    if seek:
        args += ["-ss", f"{seek:.3f}"]
    args += ["-i", path, "-frames:v", "1", "-an", "-sn"]
    if max_width:
        args += ["-vf", f"scale='min({max_width},iw)':-2"]
    args += ["-f", "image2pipe", "-vcodec", "png", "-"]
    code, out, _ = run_tool(args, timeout=timeout)
    if code != 0 or not out:
        return None
    try:
        im = Image.open(io.BytesIO(out))
        im.load()
        return im
    except Exception:
        return None


def decode_image(path: str) -> Image.Image | None:
    try:
        im = Image.open(path)
        im.load()
        return ImageOps.exif_transpose(im) or im
    except Exception:
        pass
    if ext_of(path) in RAW_EXTS:
        embedded = _embedded_jpeg(path)
        if embedded is not None:
            return embedded
    frame = _ffmpeg_frame(path)
    if frame is not None:
        return frame
    return _embedded_jpeg(path)


def _atomic_save(im: Image.Image, target: Path, fmt: str, **params):
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + f".{threading.get_ident()}.tmp")
    im.save(temp, format=fmt, **params)
    os.replace(temp, target)


def _touch(path: Path):
    try:
        os.utime(path, None)
    except OSError:
        pass


def ensure_view(path: str) -> tuple[str, str]:
    """Return (file, mime) the browser can display for an image."""
    ext = ext_of(path)
    if ext in WEB_IMAGE_EXTS:
        return path, MIME_BY_EXT[ext]
    key = cache_key(path, "view-v2")
    jpg, png = VIEW_DIR / f"{key}.jpg", VIEW_DIR / f"{key}.png"
    for candidate, mime in ((jpg, "image/jpeg"), (png, "image/png")):
        if candidate.exists():
            _touch(candidate)
            return str(candidate), mime
    _keyed.acquire(key)
    try:
        for candidate, mime in ((jpg, "image/jpeg"), (png, "image/png")):
            if candidate.exists():
                return str(candidate), mime
        im = decode_image(path)
        if im is None:
            raise MediaError("This image format could not be decoded")
        if im.width * im.height > 40_000_000:
            im.thumbnail((7000, 7000), Image.LANCZOS)
        if _has_alpha(im):
            _atomic_save(im.convert("RGBA"), png, "PNG", optimize=False)
            return str(png), "image/png"
        _atomic_save(_to_rgb(im), jpg, "JPEG", quality=90, optimize=True)
        return str(jpg), "image/jpeg"
    finally:
        _keyed.release(key)


_thumb_failures: set[str] = set()


def thumbnail(path: str, size: int = 256) -> str | None:
    """Small JPEG for filmstrips/history. None when there is nothing to show."""
    size = 128 if size <= 160 else 256 if size <= 320 else 512
    key = cache_key(path, f"thumb-{size}")
    target = THUMB_DIR / key[:2] / f"{key}.jpg"
    if target.exists():
        return str(target)
    if key in _thumb_failures:
        return None
    _keyed.acquire(key)
    try:
        if target.exists():
            return str(target)
        with _thumb_slots:
            im = _build_thumbnail(path, size)
        if im is None:
            _thumb_failures.add(key)
            return None
        im.thumbnail((size, size), Image.BILINEAR if size <= 128 else Image.LANCZOS)
        _atomic_save(_to_rgb(im, background=(40, 38, 44)), target, "JPEG", quality=82)
        return str(target)
    except Exception:
        _thumb_failures.add(key)
        return None
    finally:
        _keyed.release(key)


def _build_thumbnail(path: str, size: int) -> Image.Image | None:
    kind = media_kind(path)
    if kind == "image":
        try:
            im = Image.open(path)
            if im.format == "JPEG":
                im.draft("RGB", (size * 2, size * 2))
            im.load()
            return ImageOps.exif_transpose(im) or im
        except Exception:
            return decode_image(path)
    if kind == "video":
        duration = probe(path).get("duration") or 0.0
        seek = min(max(duration * 0.1, 0.0), 3.0) if duration else 1.0
        frame = _ffmpeg_frame(path, seek=seek, max_width=size * 2, timeout=15)
        if frame is None and seek:
            frame = _ffmpeg_frame(path, seek=None, max_width=size * 2, timeout=15)
        return frame
    if kind == "audio":
        info = probe(path)
        if info and not info.get("cover"):
            return None
        return _ffmpeg_frame(path, max_width=size * 2, timeout=10)
    return None


# ----------------------------------------------------------------- conversion jobs

LEASE_SECONDS = 20.0


class _Job:
    def __init__(self, key: str, source: str, kind: str):
        self.key = key
        self.source = source
        self.kind = kind
        self.status = "queued"
        self.progress = 0.0
        self.error = ""
        self.output: Path | None = None
        self.mime = ""
        self.touched = time.monotonic()
        self.process: subprocess.Popen | None = None

    def snapshot(self) -> dict:
        return {
            "status": self.status,
            "progress": round(self.progress, 3),
            "error": self.error,
            "ready": self.status == "done",
        }


class Converter:
    """Turns exotic video/audio into something every browser plays."""

    def __init__(self, workers: int = 2):
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="fs-convert")
        self._jobs: dict[str, _Job] = {}
        self._lock = threading.Lock()
        self._closed = False

    @staticmethod
    def _outputs(key: str) -> list[tuple[Path, str]]:
        return [
            (PLAY_DIR / f"{key}.mp4", "video/mp4"),
            (PLAY_DIR / f"{key}.webm", "video/webm"),
            (PLAY_DIR / f"{key}.mp3", "audio/mpeg"),
        ]

    def cached_output(self, path: str, kind: str) -> tuple[str, str] | None:
        key = cache_key(path, f"play-{kind}-v2")
        for candidate, mime in self._outputs(key):
            if candidate.exists():
                _touch(candidate)
                return str(candidate), mime
        return None

    def request(self, path: str, kind: str, retry: bool = False) -> dict:
        key = cache_key(path, f"play-{kind}-v2")
        for candidate, _ in self._outputs(key):
            if candidate.exists():
                return {"status": "done", "progress": 1.0, "error": "", "ready": True}
        TOOLS.resolve()
        if not TOOLS.ffmpeg:
            return {"status": "error", "progress": 0, "ready": False, "error": "FFmpeg is not installed, so this format can't be converted."}
        with self._lock:
            job = self._jobs.get(key)
            if job and job.status in ("queued", "running"):
                job.touched = time.monotonic()
                return job.snapshot()
            if job and job.status == "error" and not retry:
                return job.snapshot()
            if self._closed:
                return {"status": "error", "progress": 0, "ready": False, "error": "Shutting down"}
            job = _Job(key, path, kind)
            self._jobs[key] = job
            self._pool.submit(self._run, job)
            return job.snapshot()

    def _plans(self, job: _Job) -> list[tuple[list[str], Path, str]]:
        info = probe(job.source)
        base = [TOOLS.ffmpeg, "-y", "-v", "error", "-nostdin", "-nostats", "-progress", "pipe:1", "-i", job.source]
        mp4 = PLAY_DIR / f"{job.key}.mp4"
        if job.kind == "audio":
            out = PLAY_DIR / f"{job.key}.mp3"
            return [(base + ["-map", "0:a:0", "-vn", "-c:a", "libmp3lame", "-q:a", "3", "-f", "mp3"], out, "audio/mpeg")]

        vcodec = (info.get("video") or {}).get("codec", "")
        acodec = (info.get("audio") or {}).get("codec", "") if info.get("audio") else ""
        plans: list[tuple[list[str], Path, str]] = []
        maps = ["-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn"]
        if vcodec == "h264" and acodec in ("", "aac", "mp3"):
            plans.append((base + maps + ["-c", "copy", "-movflags", "+faststart", "-f", "mp4"], mp4, "video/mp4"))
        elif vcodec == "h264":
            plans.append(
                (base + maps + ["-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ac", "2", "-movflags", "+faststart", "-f", "mp4"], mp4, "video/mp4")
            )
        elif vcodec in ("vp8", "vp9", "av1") and acodec in ("", "opus", "vorbis"):
            webm = PLAY_DIR / f"{job.key}.webm"
            plans.append((base + maps + ["-c", "copy", "-f", "webm"], webm, "video/webm"))
        plans.append(
            (
                base
                + maps
                + [
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                    "-vf", "scale='trunc(min(1920,iw)/2)*2':-2",
                    "-c:a", "aac", "-b:a", "160k", "-ac", "2",
                    "-movflags", "+faststart", "-f", "mp4",
                ],
                mp4,
                "video/mp4",
            )
        )
        return plans

    def _run(self, job: _Job):
        if time.monotonic() - job.touched > LEASE_SECONDS:
            job.status = "cancelled"
            return
        job.status = "running"
        PLAY_DIR.mkdir(parents=True, exist_ok=True)
        duration = probe(job.source).get("duration") or 0.0
        last_error = "Conversion failed"
        for args, output, mime in self._plans(job):
            temp = output.with_name(output.name + ".part")
            ok, error = self._ffmpeg(job, args + [str(temp)], duration)
            if job.status == "cancelled":
                _unlink(temp)
                return
            if ok and temp.exists() and temp.stat().st_size > 0:
                os.replace(temp, output)
                job.output, job.mime = output, mime
                job.progress = 1.0
                job.status = "done"
                return
            _unlink(temp)
            last_error = error or last_error
            job.progress = 0.0
        job.status = "error"
        job.error = _clean_error(last_error)

    def _ffmpeg(self, job: _Job, args: list[str], duration: float) -> tuple[bool, str]:
        try:
            process = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                **hidden_process_kwargs(),
            )
        except OSError as exc:
            return False, str(exc)
        job.process = process
        tail: list[str] = []

        def drain_stderr():
            for raw in process.stderr:  # type: ignore[union-attr]
                tail.append(raw.decode("utf-8", "replace").strip())
                del tail[:-12]

        reader = threading.Thread(target=drain_stderr, daemon=True)
        reader.start()
        try:
            for raw in process.stdout:  # type: ignore[union-attr]
                line = raw.decode("ascii", "ignore").strip()
                if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                    try:
                        seconds = int(line.split("=", 1)[1]) / 1_000_000
                    except ValueError:
                        seconds = 0
                    if duration > 0:
                        job.progress = max(0.0, min(0.99, seconds / duration))
                if self._closed or time.monotonic() - job.touched > LEASE_SECONDS:
                    job.status = "cancelled"
                    process.kill()
                    break
            process.wait()
        finally:
            reader.join(timeout=2)
            job.process = None
        if job.status == "cancelled":
            return False, "cancelled"
        return process.returncode == 0, "\n".join(line for line in tail if line)

    def output_for(self, path: str, kind: str) -> tuple[str, str] | None:
        return self.cached_output(path, kind)

    def shutdown(self):
        self._closed = True
        with self._lock:
            for job in self._jobs.values():
                if job.process is not None:
                    try:
                        job.process.kill()
                    except OSError:
                        pass
        self._pool.shutdown(wait=False, cancel_futures=True)


def _unlink(path: Path):
    try:
        path.unlink()
    except OSError:
        pass


def _clean_error(text: str) -> str:
    lines = [line for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return "Conversion failed"
    message = lines[-1]
    if ": " in message and len(message) > 140:
        message = message.split(": ", 1)[1]
    return message[:200]


# --------------------------------------------------------------------------- cache


def cache_size() -> int:
    total = 0
    if not CACHE_DIR.exists():
        return 0
    for root, _, files in os.walk(CACHE_DIR):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def clear_cache():
    _thumb_failures.clear()
    if CACHE_DIR.exists():
        shutil.rmtree(CACHE_DIR, ignore_errors=True)


def prune_cache(limit_bytes: int = 3 * 1024**3):
    """Keep the preview cache bounded; oldest entries go first."""
    if not CACHE_DIR.exists():
        return
    items = []
    total = 0
    for root, _, files in os.walk(CACHE_DIR):
        for name in files:
            full = os.path.join(root, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            if name.endswith((".part", ".tmp")) and time.time() - st.st_mtime > 3600:
                _unlink(Path(full))
                continue
            items.append((st.st_mtime, st.st_size, full))
            total += st.st_size
    if total <= limit_bytes:
        return
    items.sort()
    target = limit_bytes * 0.7
    for _, size, full in items:
        if total <= target:
            break
        _unlink(Path(full))
        total -= size


# -------------------------------------------------------------------- ffmpeg setup


class FFmpegInstaller:
    """Optional one-click download of FFmpeg into ./tools/ffmpeg (Windows)."""

    def __init__(self):
        self.state = {"status": "idle", "message": ""}
        self._thread: threading.Thread | None = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return self.state
        self.state = {"status": "running", "message": "Downloading FFmpeg..."}
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self.state

    def _run(self):
        download = TOOLS_DIR / "ffmpeg_download.zip"
        extract = TOOLS_DIR / "_extract"
        try:
            TOOLS_DIR.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(FFMPEG_DOWNLOAD_URL, timeout=120) as response, download.open("wb") as out:
                shutil.copyfileobj(response, out)
            self.state["message"] = "Extracting..."
            shutil.rmtree(extract, ignore_errors=True)
            with zipfile.ZipFile(download) as archive:
                archive.extractall(extract)
            bin_dir = TOOLS_DIR / "bin"
            bin_dir.mkdir(parents=True, exist_ok=True)
            found = False
            for name in ("ffmpeg.exe", "ffprobe.exe"):
                matches = list(extract.rglob(name))
                if matches:
                    shutil.copy2(matches[0], bin_dir / name)
                    found = found or name == "ffmpeg.exe"
            if not found:
                raise RuntimeError("ffmpeg.exe was not in the download")
            TOOLS.resolve(force=True)
            self.state = {"status": "done", "message": f"FFmpeg installed in {bin_dir}"}
        except Exception as exc:
            self.state = {"status": "error", "message": f"FFmpeg install failed: {exc}"}
        finally:
            _unlink(download)
            shutil.rmtree(extract, ignore_errors=True)
