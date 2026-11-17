"""Fast, correctly rotated previews for camera RAW files.

Every RAW format carries one or more JPEGs rendered by the camera itself.
Reading one of those (a few MB at a known offset) is far faster than
demosaicing the sensor data and looks like the camera's own screen, which is
what pro culling tools show too.

The catch, and why portrait shots used to show up sideways: those JPEGs are
stored the way the sensor saw the scene. The "rotate me" flag lives in the
RAW's own header (TIFF IFD0, Canon CR3's CMT1 box...), so it's read here and
applied to whichever preview gets used.

Handled structurally: TIFF-based RAWs (NEF, NRW, ARW, SR2, SRF, CR2, DNG, ORF,
RW2, RWL, PEF, SRW, 3FR, FFF, IIQ, ERF, MEF, MOS, KDC, DCR, GPR), Canon CR3,
Fujifilm RAF and Minolta MRW. Anything else falls back to scanning the file for
embedded JPEGs.
"""

from __future__ import annotations

import io
import os
import struct
import threading
from collections import OrderedDict
from dataclasses import dataclass, field

from PIL import Image

from .env import open_shared

MAX_SCAN = 256 * 1024 * 1024  # never pull more than this into memory hunting for JPEGs
_SOF_OK = {0xC0, 0xC1, 0xC2}  # baseline, extended, progressive: what Pillow decodes
_SOF_ALL = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4}
CANON_UUID = bytes.fromhex("85c0b687820f11e08111f4ce462b6a48")
CANON_PRVW_UUID = bytes.fromhex("eaf42b5e1c984b88b9fbb7dc406e4d16")

_TRANSPOSE = {
    2: Image.Transpose.FLIP_LEFT_RIGHT,
    3: Image.Transpose.ROTATE_180,
    4: Image.Transpose.FLIP_TOP_BOTTOM,
    5: Image.Transpose.TRANSPOSE,
    6: Image.Transpose.ROTATE_270,
    7: Image.Transpose.TRANSVERSE,
    8: Image.Transpose.ROTATE_90,
}


@dataclass
class Preview:
    offset: int
    length: int
    width: int
    height: int
    orientation: int = 0  # the JPEG's own EXIF flag; 0 when it has none
    exif: int = 0  # absolute offset of its EXIF TIFF header, 0 when none

    @property
    def long_side(self) -> int:
        return max(self.width, self.height)


@dataclass
class RawInfo:
    orientation: int = 0  # 0 = the RAW header didn't say
    width: int = 0
    height: int = 0
    make: str = ""
    model: str = ""
    taken: str = ""
    exposure: float = 0.0
    fnumber: float = 0.0
    iso: int = 0
    focal: float = 0.0
    previews: list[Preview] = field(default_factory=list)
    tiff: bool = False  # plain TIFF container: Pillow can open its first image
    scanned: bool = False

    def best(self, want: int = 0) -> Preview | None:
        """Smallest preview that still covers `want` pixels, else the biggest one."""
        if not self.previews:
            return None
        ordered = sorted(self.previews, key=lambda p: p.width * p.height)
        if want:
            for preview in ordered:
                if preview.long_side >= want:
                    return preview
        return ordered[-1]

    def orientation_for(self, preview: Preview) -> int:
        """Which EXIF orientation to apply to this preview's pixels."""
        if preview.orientation and preview.orientation != 1:
            return preview.orientation  # Fuji/Panasonic previews carry their own flag
        flag = self.orientation or preview.orientation or 1
        if flag in (5, 6, 7, 8) and preview.height > preview.width:
            return 1  # the camera already stored it upright
        return flag


# ------------------------------------------------------------------- readers


class _File:
    """Bounded random access with tiny reads (no full-file loads)."""

    def __init__(self, handle, size: int):
        self.f = handle
        self.size = size

    def read(self, offset: int, length: int) -> bytes:
        if offset < 0 or length <= 0 or offset >= self.size:
            return b""
        self.f.seek(offset)
        return self.f.read(min(length, self.size - offset))


class _Mem:
    def __init__(self, data: bytes):
        self.data = data
        self.size = len(data)

    def read(self, offset: int, length: int) -> bytes:
        if offset < 0 or length <= 0:
            return b""
        return self.data[offset : offset + length]


class _Tiff:
    def __init__(self, src, base: int):
        self.src = src
        self.base = base
        head = src.read(base, 8)
        if len(head) < 8 or head[:2] not in (b"II", b"MM"):
            raise ValueError("not a TIFF structure")
        self.e = "<" if head[:2] == b"II" else ">"
        self.magic = head[2:4]
        self.first = struct.unpack(self.e + "I", head[4:8])[0]

    def ifd(self, offset: int) -> tuple[dict, int]:
        """{tag: (type, count, raw value field)} and the next IFD's offset."""
        start = self.base + offset
        raw = self.src.read(start, 2)
        if len(raw) < 2:
            return {}, 0
        count = struct.unpack(self.e + "H", raw)[0]
        if not 0 < count <= 1000:
            return {}, 0
        data = self.src.read(start + 2, count * 12 + 4)
        if len(data) < count * 12:
            return {}, 0
        entries = {}
        for index in range(count):
            row = data[index * 12 : index * 12 + 12]
            tag, kind, n = struct.unpack(self.e + "HHI", row[:8])
            entries[tag] = (kind, n, row[8:12])
        following = struct.unpack(self.e + "I", data[count * 12 : count * 12 + 4])[0] if len(data) >= count * 12 + 4 else 0
        return entries, following

    def where(self, entry) -> tuple[int, int]:
        """Absolute offset and byte length of an entry's data (offset -1 when inline)."""
        kind, n, raw = entry
        size = _TYPE_SIZE.get(kind, 1) * n
        if size <= 4:
            return -1, size
        return self.base + struct.unpack(self.e + "I", raw)[0], size

    def values(self, entry, limit: int = 1) -> list:
        kind, n, raw = entry
        unit = _TYPE_SIZE.get(kind, 1)
        count = min(n, limit)
        size = unit * count
        if unit * n <= 4:
            data = raw[:size]
        else:
            data = self.src.read(self.base + struct.unpack(self.e + "I", raw)[0], size)
        if len(data) < size or count <= 0:
            return []
        e = self.e
        if kind == 2:
            return [data.split(b"\0", 1)[0].decode("utf-8", "replace").strip()]
        if kind in (1, 7):
            return list(data)
        if kind == 6:
            return list(struct.unpack(f"{e}{count}b", data))
        if kind == 3:
            return list(struct.unpack(f"{e}{count}H", data))
        if kind == 8:
            return list(struct.unpack(f"{e}{count}h", data))
        if kind in (4, 13):
            return list(struct.unpack(f"{e}{count}I", data))
        if kind == 9:
            return list(struct.unpack(f"{e}{count}i", data))
        if kind in (5, 10):
            nums = struct.unpack(f"{e}{count * 2}{'I' if kind == 5 else 'i'}", data)
            return [nums[i] / nums[i + 1] if nums[i + 1] else 0.0 for i in range(0, len(nums), 2)]
        if kind == 11:
            return list(struct.unpack(f"{e}{count}f", data))
        if kind == 12:
            return list(struct.unpack(f"{e}{count}d", data))
        return []

    def get(self, entries: dict, tag: int, default=None):
        entry = entries.get(tag)
        if entry is None:
            return default
        found = self.values(entry, limit=256 if entry[0] == 2 else 1)
        return found[0] if found else default


def _jpeg_header(src, offset: int, limit: int, want_sos: bool = False):
    """(width, height, orientation, exif offset, sos offset) of a decodable JPEG, else None."""
    if src.read(offset, 2) != b"\xff\xd8":
        return None
    end = offset + limit if limit > 0 else src.size
    pos = offset + 2
    orientation = exif = 0
    dims = None
    for _ in range(160):
        head = src.read(pos, 4)
        if len(head) < 4 or head[0] != 0xFF:
            return None
        marker = head[1]
        if marker == 0xFF:  # fill byte
            pos += 1
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        if marker in (0xD8, 0xD9):
            return None
        length = struct.unpack(">H", head[2:4])[0]
        if length < 2:
            return None
        if marker == 0xE1 and not exif:
            payload = src.read(pos + 4, min(length - 2, 65533))
            if payload[:6] == b"Exif\x00\x00":
                exif = pos + 10
                orientation = _tiff_orientation(payload[6:])
        elif marker in _SOF_ALL and dims is None:
            sof = src.read(pos + 4, 5)
            if len(sof) < 5 or marker not in _SOF_OK:
                return None  # lossless/arithmetic: the sensor data in CR2/DNG, not a preview
            height, width = struct.unpack(">HH", sof[1:5])
            dims = (width, height)
            if not want_sos:
                break
        elif marker == 0xDA:
            if dims is None:
                return None
            return dims[0], dims[1], orientation, exif, pos
        pos += 2 + length
        if pos >= end:
            return None
    if dims is None or want_sos:
        return None
    return dims[0], dims[1], orientation, exif, 0


def _tiff_orientation(buf: bytes) -> int:
    """Orientation tag from an in-memory TIFF (EXIF APP1 payload)."""
    if len(buf) < 8 or buf[:2] not in (b"II", b"MM"):
        return 0
    e = "<" if buf[:2] == b"II" else ">"
    offset = struct.unpack(e + "I", buf[4:8])[0]
    if offset + 2 > len(buf):
        return 0
    count = struct.unpack(e + "H", buf[offset : offset + 2])[0]
    for index in range(min(count, 300)):
        row = offset + 2 + index * 12
        if row + 12 > len(buf):
            break
        tag, kind = struct.unpack(e + "HH", buf[row : row + 4])
        if tag == 0x0112 and kind == 3:
            value = struct.unpack(e + "H", buf[row + 8 : row + 10])[0]
            return value if 1 <= value <= 8 else 0
    return 0


# ------------------------------------------------------------------ structure


def _read_ifd0(t: _Tiff, entries: dict, info: RawInfo):
    orientation = t.get(entries, 0x0112, 0)
    if isinstance(orientation, int) and 1 <= orientation <= 8 and not info.orientation:
        info.orientation = orientation
    info.make = info.make or str(t.get(entries, 0x010F, "") or "")
    info.model = info.model or str(t.get(entries, 0x0110, "") or "")
    taken = t.get(entries, 0x0132, "")
    if taken and not info.taken:
        info.taken = str(taken)


def _read_exif(t: _Tiff, entries: dict, info: RawInfo):
    info.exposure = info.exposure or float(t.get(entries, 0x829A, 0.0) or 0.0)
    info.fnumber = info.fnumber or float(t.get(entries, 0x829D, 0.0) or 0.0)
    iso = t.get(entries, 0x8827, 0) or t.get(entries, 0x8832, 0)
    if iso and not info.iso:
        info.iso = int(iso)
    info.focal = info.focal or float(t.get(entries, 0x920A, 0.0) or 0.0)
    taken = t.get(entries, 0x9003, "")
    if taken:
        info.taken = str(taken)
    width, height = t.get(entries, 0xA002, 0), t.get(entries, 0xA003, 0)
    if width and height and width * height > info.width * info.height:
        info.width, info.height = int(width), int(height)


def _walk_tiff(src, base: int, info: RawInfo, found: list[tuple[int, int]]):
    t = _Tiff(src, base)
    queue = [(t.first, 0, True)]
    seen: set[int] = set()
    first = True
    while queue and len(seen) < 32:
        offset, depth, chain = queue.pop(0)
        if not offset or offset in seen:
            continue
        seen.add(offset)
        entries, following = t.ifd(offset)
        if not entries:
            continue
        if first:
            first = False
            _read_ifd0(t, entries, info)
            exif = t.get(entries, 0x8769, 0)
            if exif:
                exif_entries, _ = t.ifd(exif)
                if exif_entries:
                    _read_exif(t, exif_entries, info)
        # Every place cameras put a JPEG: the classic thumbnail pointer, a
        # one-strip JPEG image (CR2, DNG previews), Panasonic's JpgFromRaw.
        start, size = t.get(entries, 0x0201, 0), t.get(entries, 0x0202, 0)
        if start and size:
            found.append((base + int(start), int(size)))
        if t.get(entries, 0x0103, 0) in (6, 7) and 0x0111 in entries and 0x0117 in entries:
            strips, counts = t.values(entries[0x0111], 2), t.values(entries[0x0117], 2)
            if len(strips) == 1 and len(counts) == 1:
                found.append((base + int(strips[0]), int(counts[0])))
        if 0x002E in entries and entries[0x002E][0] == 7:
            where, size = t.where(entries[0x002E])
            if where > 0 and size > 1024:
                found.append((where, size))
        crop = t.values(entries[0xC620], 2) if 0xC620 in entries else []
        width, height = (crop if len(crop) == 2 else (t.get(entries, 0x0100, 0), t.get(entries, 0x0101, 0)))
        try:
            width, height = int(width or 0), int(height or 0)
        except (TypeError, ValueError):
            width = height = 0
        if width * height > info.width * info.height and width >= 320 and height >= 240:
            info.width, info.height = width, height
        if depth < 3 and 0x014A in entries:
            for sub in t.values(entries[0x014A], 16):
                queue.append((int(sub), depth + 1, False))
        if chain and following:
            queue.append((following, depth, True))


def _boxes(src, start: int, end: int):
    pos = start
    for _ in range(4096):
        if pos + 8 > end:
            return
        head = src.read(pos, 16)
        if len(head) < 8:
            return
        size, kind = struct.unpack(">I4s", head[:8])
        header = 8
        if size == 1 and len(head) >= 16:
            size = struct.unpack(">Q", head[8:16])[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header:
            return
        yield kind, pos + header, min(pos + size, end)
        pos += size


def _first_jpeg_in(src, start: int, end: int) -> tuple[int, int] | None:
    data = src.read(start, min(end - start, 512))
    index = data.find(b"\xff\xd8\xff")
    if index < 0:
        return None
    return start + index, end - (start + index)


def _cr3(src, info: RawInfo, found: list[tuple[int, int]]):
    trak_done = False
    for kind, start, end in _boxes(src, 0, src.size):
        if kind == b"moov":
            for inner, s2, e2 in _boxes(src, start, end):
                if inner == b"uuid" and src.read(s2, 16) == CANON_UUID:
                    for item, s3, e3 in _boxes(src, s2 + 16, e2):
                        if item == b"CMT1":
                            t = _Tiff(src, s3)
                            entries, _ = t.ifd(t.first)
                            _read_ifd0(t, entries, info)
                        elif item == b"CMT2":
                            t = _Tiff(src, s3)
                            entries, _ = t.ifd(t.first)
                            _read_exif(t, entries, info)
                        elif item == b"THMB":
                            hit = _first_jpeg_in(src, s3, e3)
                            if hit:
                                found.append(hit)
                elif inner == b"trak" and not trak_done:
                    trak_done = True  # track 1 holds a full-size JPEG
                    hit = _first_sample(src, s2, e2)
                    if hit:
                        found.append(hit)
        elif kind == b"uuid" and src.read(start, 16) == CANON_PRVW_UUID:
            hit = _first_jpeg_in(src, start + 16, end)
            if hit:
                found.append(hit)
        elif kind == b"mdat":
            break


def _first_sample(src, start: int, end: int) -> tuple[int, int] | None:
    box = (start, end)
    for name in (b"mdia", b"minf", b"stbl"):
        for kind, s, e in _boxes(src, *box):
            if kind == name:
                box = (s, e)
                break
        else:
            return None
    size = offset = 0
    for kind, s, e in _boxes(src, *box):
        if kind == b"stsz":
            head = src.read(s, 16)
            if len(head) >= 16:
                size = struct.unpack(">I", head[4:8])[0] or struct.unpack(">I", head[12:16])[0]
        elif kind == b"co64":
            head = src.read(s, 16)
            if len(head) >= 16:
                offset = struct.unpack(">Q", head[8:16])[0]
        elif kind == b"stco":
            head = src.read(s, 12)
            if len(head) >= 12:
                offset = struct.unpack(">I", head[8:12])[0]
    return (offset, size) if offset and size else None


def _raf(src, info: RawInfo, found: list[tuple[int, int]]):
    head = src.read(0, 100)
    if len(head) < 92:
        return
    offset, size = struct.unpack(">II", head[84:92])
    if offset and size:
        found.append((offset, size))


def _mrw(src) -> int:
    """Offset of the TIFF block inside a Minolta MRW header, else 0."""
    head = src.read(0, 8)
    end = 8 + struct.unpack(">I", head[4:8])[0]
    pos = 8
    for _ in range(16):
        block = src.read(pos, 8)
        if len(block) < 8 or pos >= end:
            return 0
        if block[:4] == b"\x00TTW":
            return pos + 8
        pos += 8 + struct.unpack(">I", block[4:8])[0]
    return 0


def _scan(handle, size: int, info: RawInfo):
    """Last resort: look through the bytes for embedded JPEGs."""
    handle.seek(0)
    data = handle.read(min(size, MAX_SCAN))
    mem = _Mem(data)
    start = 0
    known = {p.offset for p in info.previews}
    for _ in range(64):
        index = data.find(b"\xff\xd8\xff", start)
        if index < 0:
            break
        start = index + 3
        if index in known:
            continue
        head = _jpeg_header(mem, index, len(data) - index, want_sos=True)
        if not head:
            continue
        width, height, orientation, exif, sos = head
        end = data.find(b"\xff\xd9", sos)
        if end < 0:
            continue
        if width * height >= 160 * 120:
            info.previews.append(Preview(index, end + 2 - index, width, height, orientation, exif))
        start = end + 2  # skip the entropy-coded data and any nested thumbnail
    info.scanned = True


# ---------------------------------------------------------------------- public

_cache: OrderedDict[tuple, RawInfo] = OrderedDict()
_cache_lock = threading.Lock()


def inspect(path: str, need: int = 0) -> RawInfo:
    """Header facts and every usable embedded preview. Cached per file version."""
    st = os.stat(path)
    key = (os.path.normcase(os.path.abspath(path)), st.st_mtime_ns, st.st_size)
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None:
            _cache.move_to_end(key)
    if cached is not None and (cached.scanned or not need or (cached.best() and cached.best().long_side >= need)):
        return cached
    info = cached or RawInfo()
    with open_shared(path, sequential=False) as handle:
        src = _File(handle, st.st_size)
        if cached is None:
            found: list[tuple[int, int]] = []
            head = src.read(0, 16)
            try:
                if head.startswith(b"FUJIFILMCCD-RAW"):
                    _raf(src, info, found)
                elif head[4:8] == b"ftyp":
                    _cr3(src, info, found)
                elif head[:4] == b"\x00MRM":
                    base = _mrw(src)
                    if base:
                        _walk_tiff(src, base, info, found)
                elif head[:2] in (b"II", b"MM"):
                    info.tiff = head[2:4] in (b"*\x00", b"\x00*")
                    _walk_tiff(src, 0, info, found)
            except (ValueError, struct.error, OSError):
                pass
            seen: set[int] = set()
            for offset, size in found:
                if offset in seen or offset <= 0 or offset + size > st.st_size + 16:
                    continue
                seen.add(offset)
                head = _jpeg_header(src, offset, size)
                if head and head[0] * head[1] >= 64 * 64:
                    info.previews.append(Preview(offset, size, head[0], head[1], head[2], head[3]))
            best = info.best()
            if best is not None and best.exif and not (info.make or info.model):
                # RAF (and scanned formats): the camera facts live in the preview's EXIF.
                try:
                    _walk_exif_only(src, best.exif, info)
                except (ValueError, struct.error):
                    pass
        best = info.best()
        if not info.scanned and (best is None or (need and best.long_side < need)):
            try:
                _scan(handle, st.st_size, info)
            except (OSError, MemoryError):
                info.scanned = True
            best = info.best()
            if best is not None and best.exif and not (info.make or info.model):
                try:
                    _walk_exif_only(_File(handle, st.st_size), best.exif, info)
                except (ValueError, struct.error):
                    pass
    if not info.width and info.previews:
        top = info.best()
        info.width, info.height = top.width, top.height
    with _cache_lock:
        _cache[key] = info
        while len(_cache) > 128:
            _cache.popitem(last=False)
    return info


def _walk_exif_only(src, base: int, info: RawInfo):
    t = _Tiff(src, base)
    entries, _ = t.ifd(t.first)
    if not entries:
        return
    _read_ifd0(t, entries, info)
    exif = t.get(entries, 0x8769, 0)
    if exif:
        exif_entries, _ = t.ifd(exif)
        _read_exif(t, exif_entries, info)


def orient(im: Image.Image, orientation: int) -> Image.Image:
    method = _TRANSPOSE.get(orientation)
    return im.transpose(method) if method is not None else im


def load(path: str, want: int = 0) -> Image.Image | None:
    """The camera's own preview, upright. `want` = longest side needed (0 = biggest)."""
    info = inspect(path, need=want or 1200)
    preview = info.best(want)
    if preview is None:
        return None
    with open_shared(path, sequential=False) as handle:
        handle.seek(preview.offset)
        data = handle.read(preview.length)
    im = Image.open(io.BytesIO(data))
    if want:
        im.draft("RGB", (want, want))
    im.load()
    return orient(im, info.orientation_for(preview))
