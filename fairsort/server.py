"""Tiny threaded HTTP server: JSON API + static UI + media streaming.

One server instance always listens on 127.0.0.1 for the desktop window.
"Share on Wi-Fi" starts a second listener on 0.0.0.0 that requires a PIN.

Security model
--------------
* Every /api call needs the ``fs_auth`` cookie: the desktop gets it through a
  one-time ``?key=`` URL, LAN devices by entering the PIN (or scanning the QR).
* POSTs must carry ``X-Fair: 1`` (custom header -> cross-site pages can't send it).
* The Host header must be an IP / localhost / this machine (DNS-rebinding guard).
* Remote devices may only read files inside the source/destination folders.
"""

from __future__ import annotations

import gzip
import hmac
import ipaddress
import json
import os
import re
import secrets
import socket
import sys
import threading
import time
import traceback
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import APP_NAME, __version__, media, updater, winshell
from .env import FROZEN, ICON_FILE, IS_WINDOWS, WEB_DIR, norm_key, open_shared
from .workspace import OpError, Settings, Workspace

STATIC_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".json": "application/json",
    ".woff2": "font/woff2",
}
CSP = (
    "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
    "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------- app


class App:
    """Everything a request handler needs, shared by all listeners."""

    def __init__(self, mode: str = "desktop"):
        self.mode = mode
        self.settings = Settings()
        self.workspace = Workspace(self.settings)
        self.converter = media.Converter()
        self.installer = media.FFmpegInstaller()
        self.updater = updater.Updater()
        self.local_token = secrets.token_urlsafe(24)
        self.window = None  # pywebview window in desktop mode
        self.on_theme = None  # callback that recolors the native title bar
        self.main_port = 0
        self.last_ping = time.monotonic()
        self.lan = LanShare(self)
        # Before a file is moved/renamed/deleted, stop our own helpers reading it.
        self.workspace.release = self.release_file
        self._failures: dict[str, list[float]] = {}
        self._failures_lock = threading.Lock()
        threading.Thread(target=self._warmup, name="fs-warmup", daemon=True).start()

    @staticmethod
    def _warmup():
        media.TOOLS.resolve()
        media.prune_cache()

    # ------------------------------------------------------------------- auth

    def lan_token(self) -> str:
        secret = str(self.settings.get("lan_secret") or "")
        return hmac.new(secret.encode(), b"fair-sorting-lan", "sha256").hexdigest()

    def token_ok(self, value: str | None) -> bool:
        if not value:
            return False
        return hmac.compare_digest(value, self.local_token) or hmac.compare_digest(value, self.lan_token())

    def check_pin(self, client: str, pin: str) -> bool:
        now = time.monotonic()
        with self._failures_lock:
            recent = [stamp for stamp in self._failures.get(client, []) if now - stamp < 60]
            if len(recent) >= 8:
                raise ApiError(429, "Too many wrong PINs. Wait a minute and try again.")
            if hmac.compare_digest(str(pin).strip(), str(self.settings.get("lan_pin"))):
                self._failures.pop(client, None)
                return True
            recent.append(now)
            self._failures[client] = recent
            return False

    def regenerate_pin(self):
        self.settings.update(lan_pin=f"{secrets.randbelow(10**6):06d}", lan_secret=secrets.token_hex(16))

    def quit(self):
        """Close the app (used right after an updater has been started)."""
        if self.window is not None:
            try:
                self.window.destroy()
                return
            except Exception:
                pass
        self.shutdown()
        os._exit(0)

    def release_file(self, path: str):
        self.converter.cancel(path)
        media.release(path)

    # --------------------------------------------------------------- window

    def toggle_fullscreen(self) -> bool:
        if self.window is None:
            return False
        try:
            self.window.toggle_fullscreen()
            return True
        except Exception:
            return False

    def shutdown(self):
        self.lan.stop()
        self.converter.shutdown()


# --------------------------------------------------------------------- LAN share


def lan_addresses() -> list[str]:
    found: list[str] = []
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("10.255.255.255", 1))  # no packet is sent
            found.append(probe.getsockname()[0])
        finally:
            probe.close()
    except OSError:
        pass
    try:
        for address in socket.gethostbyname_ex(socket.gethostname())[2]:
            if address not in found:
                found.append(address)
    except OSError:
        pass
    usable = []
    for address in found:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if ip.is_loopback or ip.is_link_local or ip.is_unspecified:
            continue
        usable.append(address)
    # Keep the default-route address (found first) on top; virtual adapters
    # (VMware/Hyper-V/WSL) usually end in .1 and are rarely what a phone can reach.
    first = usable[0] if usable else ""
    usable.sort(key=lambda value: (not ipaddress.ip_address(value).is_private, value != first and value.endswith(".1")))
    return usable


def qr_svg(text: str) -> str:
    try:
        import qrcode  # type: ignore
        import qrcode.image.svg  # type: ignore
    except Exception:
        return ""
    try:
        image = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
        svg = image.to_string(encoding="unicode")
    except Exception:
        return ""
    svg = re.sub(r"<\?xml[^>]*\?>", "", svg).strip()
    svg = re.sub(r'\s(width|height)="[^"]*"', "", svg, count=2)
    return svg


class LanShare:
    def __init__(self, app: App):
        self.app = app
        self.httpd: FairServer | None = None
        self.thread: threading.Thread | None = None
        self.port = 0

    @property
    def running(self) -> bool:
        return self.httpd is not None or self.app.mode == "web"

    def start(self, port: int):
        if self.app.mode == "web":
            return
        self.stop()
        try:
            httpd = FairServer(("0.0.0.0", port), self.app, lan=True)
        except OSError as exc:
            raise OpError(f"Port {port} is busy or blocked ({exc.strerror or exc}). Try another one.") from exc
        self.httpd = httpd
        self.port = httpd.server_address[1]
        self.thread = threading.Thread(target=httpd.serve_forever, name="fs-lan", daemon=True)
        self.thread.start()
        self.app.settings.update(lan_port=self.port)

    def stop(self):
        httpd, self.httpd = self.httpd, None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()

    def status(self) -> dict:
        port = self.app.main_port if self.app.mode == "web" else self.port
        running = self.running
        pin = str(self.app.settings.get("lan_pin"))
        urls = [f"http://{address}:{port}/" for address in lan_addresses()] if running else []
        return {
            "running": running,
            "fixed": self.app.mode == "web",
            "port": port or int(self.app.settings.get("lan_port") or 8501),
            "urls": urls,
            "pin": pin,
            "qr": qr_svg(f"{urls[0]}?pin={pin}") if urls else "",
        }


# ----------------------------------------------------------------------- routing

ROUTES: dict[tuple[str, str], tuple] = {}


def route(method: str, path: str, *, auth: bool = True, local: bool = False):
    def register(fn):
        ROUTES[(method, path)] = (fn, auth, local)
        return fn

    return register


class FairServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False  # on Windows SO_REUSEADDR would let two apps share a port
    request_queue_size = 64

    def __init__(self, address, app: App, lan: bool):
        self.app = app
        self.lan = lan
        super().__init__(address, Handler)

    def handle_error(self, request, client_address):
        # Browsers drop keep-alive sockets and abort media range requests all the
        # time; that's normal, not worth a traceback.
        error = sys.exc_info()[1]
        if isinstance(error, (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "FairSorting"
    sys_version = ""
    server: FairServer

    # ------------------------------------------------------------ plumbing

    def log_message(self, format, *args):  # noqa: A002 - signature from stdlib
        if os.environ.get("FAIR_SORTING_LOG"):
            sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))

    @property
    def app(self) -> App:
        return self.server.app

    @property
    def is_local(self) -> bool:
        host = self.client_address[0]
        return host in ("127.0.0.1", "::1") or host.startswith("::ffff:127.")

    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("HEAD")

    def do_POST(self):
        self._dispatch("POST")

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").strip().lower()
        if host.startswith("["):
            host = host[1:].split("]", 1)[0]
        else:
            host = host.rsplit(":", 1)[0]
        if host in ("127.0.0.1", "localhost", "::1"):
            return True
        if not self.server.lan:
            return False
        try:
            ipaddress.ip_address(host)
            return True
        except ValueError:
            pass
        machine = socket.gethostname().lower()
        return host in (machine, f"{machine}.local", f"{machine}.lan", f"{machine}.home")

    def _cookie_token(self) -> str | None:
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        try:
            jar = SimpleCookie(raw)
        except CookieError:
            return None
        morsel = jar.get("fs_auth")
        return morsel.value if morsel else None

    def _authed(self) -> bool:
        return self.app.token_ok(self._cookie_token())

    def _auth_cookie(self, token: str) -> str:
        return f"fs_auth={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=31536000"

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 2_000_000:
            raise ApiError(413, "Request too large")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise ApiError(400, "Invalid JSON") from exc
        if not isinstance(data, dict):
            raise ApiError(400, "Expected a JSON object")
        return data

    def _dispatch(self, method: str):
        self._sent = False
        self.body: dict = {}
        self.head_only = method == "HEAD"
        try:
            url = urlsplit(self.path)
            self.query = {key: values[-1] for key, values in parse_qs(url.query, keep_blank_values=True).items()}
            if method == "POST":
                self.body = self._read_body()
            if not self._host_ok():
                raise ApiError(403, "Unknown host name")
            if url.path.startswith("/api/"):
                entry = ROUTES.get(("GET" if method == "HEAD" else method, url.path))
                if entry is None:
                    raise ApiError(404, "Unknown endpoint")
                fn, needs_auth, local_only = entry
                if needs_auth and not self._authed():
                    raise ApiError(401, "Enter the PIN shown in Fair Sorting on your computer.")
                if method == "POST" and self.headers.get("X-Fair") != "1":
                    raise ApiError(403, "Missing X-Fair header")
                if local_only and not self.is_local:
                    raise ApiError(403, "This only works on the computer running Fair Sorting.")
                result = fn(self)
                if not self._sent:
                    self.send_json({"ok": True} if result is None else result)
            else:
                self._static(url.path, method)
        except OpError as exc:
            self._fail(exc.status, str(exc))
        except ApiError as exc:
            self._fail(exc.status, str(exc))
        except media.MediaError as exc:
            self._fail(422, str(exc))
        except winshell.DialogBusy as exc:
            self._fail(409, str(exc))
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True
        except Exception as exc:  # pragma: no cover - last resort
            traceback.print_exc()
            self._fail(500, f"Unexpected error: {exc}")

    def _fail(self, status: int, message: str):
        if self._sent:
            self.close_connection = True
            return
        try:
            self.send_json({"error": message}, status)
        except OSError:
            self.close_connection = True

    # ------------------------------------------------------------ responses

    def _begin(self, status: int, headers: dict):
        self.send_response(status)
        for key, value in headers.items():
            if isinstance(value, list):
                for item in value:
                    self.send_header(key, item)
            else:
                self.send_header(key, value)
        self.end_headers()
        self._sent = True

    def send_json(self, payload, status: int = 200, cookies: list[str] | None = None):
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", "Vary": "Accept-Encoding"}
        if len(data) > 4096 and "gzip" in (self.headers.get("Accept-Encoding") or ""):
            data = gzip.compress(data, compresslevel=5)
            headers["Content-Encoding"] = "gzip"
        headers["Content-Length"] = str(len(data))
        if cookies:
            headers["Set-Cookie"] = cookies
        self._begin(status, headers)
        if not getattr(self, "head_only", False):
            self.wfile.write(data)

    def redirect(self, location: str, cookies: list[str] | None = None):
        headers = {"Location": location, "Content-Length": "0", "Cache-Control": "no-store"}
        if cookies:
            headers["Set-Cookie"] = cookies
        self._begin(302, headers)

    def send_file(self, path: str, mime: str, cache: str = "no-cache", head: bool = False, extra: dict | None = None):
        try:
            st = os.stat(path)
        except OSError as exc:
            raise ApiError(404, "File not found") from exc
        size = st.st_size
        etag = f'"{st.st_mtime_ns:x}-{size:x}"'
        range_header = self.headers.get("Range")
        if not range_header and self.headers.get("If-None-Match") == etag:
            self._begin(304, {"ETag": etag, "Cache-Control": cache, "Content-Length": "0"})
            return
        start, end, status = 0, size - 1, 200
        if range_header and range_header.startswith("bytes=") and size > 0:
            spec = range_header[6:].split(",", 1)[0].strip()
            first, _, last = spec.partition("-")
            try:
                if first == "":
                    start = max(0, size - int(last))
                else:
                    start = int(first)
                    end = min(int(last), size - 1) if last else size - 1
                if start > end or start >= size:
                    self._begin(416, {"Content-Range": f"bytes */{size}", "Content-Length": "0"})
                    return
                status = 206
            except ValueError:
                start, end, status = 0, size - 1, 200
        length = max(0, end - start + 1)
        headers = {
            "Content-Type": mime,
            "Content-Length": str(length),
            "Accept-Ranges": "bytes",
            "ETag": etag,
            "Cache-Control": cache,
        }
        if status == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        if extra:
            headers.update(extra)
        self._begin(status, headers)
        if head or getattr(self, "head_only", False) or length == 0:
            return
        with open_shared(path) as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(262_144, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _static(self, path: str, method: str):
        if method not in ("GET", "HEAD"):
            raise ApiError(405, "Method not allowed")
        if path in ("/", "/index.html"):
            key = self.query.get("key")
            pin = self.query.get("pin")
            if key is not None:
                if self.app.token_ok(key):
                    return self.redirect("/", [self._auth_cookie(key)])
                return self.redirect("/")
            if pin is not None:
                try:
                    good = self.app.check_pin(self.client_address[0], pin)
                except ApiError:
                    good = False
                if good:
                    return self.redirect("/", [self._auth_cookie(self.app.lan_token())])
                return self.redirect("/?wrong=1" if pin else "/")
            return self.send_file(
                str(WEB_DIR / "index.html"),
                STATIC_TYPES[".html"],
                head=method == "HEAD",
                extra={"Content-Security-Policy": CSP, "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff"},
            )
        if path == "/favicon.ico" and ICON_FILE.exists():
            return self.send_file(str(ICON_FILE), "image/x-icon", cache="max-age=86400", head=method == "HEAD")
        name = path.lstrip("/")
        if not STATIC_NAME.match(name):
            raise ApiError(404, "Not found")
        target = WEB_DIR / name
        if not target.is_file():
            raise ApiError(404, "Not found")
        mime = STATIC_TYPES.get(target.suffix.lower(), "application/octet-stream")
        return self.send_file(str(target), mime, head=method == "HEAD", extra={"X-Content-Type-Options": "nosniff"})

    # -------------------------------------------------------------- helpers

    def arg(self, name: str, default=None, required: bool = False):
        value = self.body.get(name, self.query.get(name, default))
        if required and (value is None or value == ""):
            raise ApiError(400, f"Missing '{name}'")
        return value

    def media_path(self, name: str = "p") -> str:
        raw = self.arg(name, required=True)
        path = os.path.normpath(str(raw))
        if not self.is_local and not self.app.workspace.allowed(path):
            raise ApiError(403, "That file is outside the folders you're sorting.")
        if not os.path.isfile(path):
            raise ApiError(404, "File not found. It may have been moved.")
        if media.media_kind(path) is None:
            raise ApiError(415, "Not a supported media file")
        return path


# ------------------------------------------------------------------------- routes


@route("GET", "/api/hello", auth=False)
def api_hello(req: Handler):
    return {"app": APP_NAME, "version": __version__, "authed": req._authed(), "local": req.is_local}


@route("POST", "/api/auth", auth=False)
def api_auth(req: Handler):
    if req.headers.get("X-Fair") != "1":
        raise ApiError(403, "Missing X-Fair header")
    if not req.app.check_pin(req.client_address[0], str(req.arg("pin", ""))):
        raise ApiError(401, "That PIN isn't right.")
    req.send_json({"ok": True}, cookies=[req._auth_cookie(req.app.lan_token())])


@route("GET", "/api/boot")
def api_boot(req: Handler):
    media.TOOLS.resolve()
    app = req.app
    local = req.is_local
    return {
        "app": APP_NAME,
        "version": __version__,
        "mode": app.mode,
        "settings": app.settings.public(),
        "caps": {
            "local": local,
            "desktop": app.mode == "desktop" and local and app.window is not None,
            "native": local and IS_WINDOWS,
            "ffmpeg": bool(media.TOOLS.ffmpeg),
            "ffprobe": bool(media.TOOLS.ffprobe),
            "heif": media.HEIF_ENABLED,
            "can_install_ffmpeg": local and IS_WINDOWS and not media.TOOLS.ffmpeg,
            "share": local,
            "frozen": FROZEN,
            "platform": sys.platform,
            "sep": os.sep,
            "home": os.path.expanduser("~"),
        },
    }


@route("GET", "/api/state")
def api_state(req: Handler):
    return req.app.workspace.snapshot()


@route("GET", "/api/changes")
def api_changes(req: Handler):
    try:
        since = int(req.arg("since", 0))
    except (TypeError, ValueError):
        since = 0
    return req.app.workspace.changes_since(since)


@route("POST", "/api/ping")
def api_ping(req: Handler):
    req.app.last_ping = time.monotonic()


@route("POST", "/api/source")
def api_source(req: Handler):
    recursive = req.arg("recursive")
    return req.app.workspace.load_source(str(req.arg("path", required=True)), None if recursive is None else bool(recursive))


@route("POST", "/api/rescan")
def api_rescan(req: Handler):
    return req.app.workspace.rescan()


@route("POST", "/api/recent/forget")
def api_recent_forget(req: Handler):
    req.app.workspace.forget_recent(str(req.arg("path", required=True)))


@route("POST", "/api/settings")
def api_settings(req: Handler):
    app = req.app
    changes = {}
    for key in ("confirm_delete", "autoplay", "always_convert", "check_updates"):
        if key in req.body:
            changes[key] = bool(req.body[key])
    if changes:
        app.settings.update(**changes)
    if "keep_together" in req.body:
        together = bool(req.body["keep_together"])
        if together != bool(app.settings.get("keep_together", True)):
            app.settings.update(keep_together=together)
            if app.workspace.source:
                app.workspace.rescan()
    if "include_subfolders" in req.body:
        recursive = bool(req.body["include_subfolders"])
        app.settings.update(include_subfolders=recursive)
        if app.workspace.source and recursive != app.workspace.recursive:
            app.workspace.load_source(app.workspace.source, recursive, remember=False)
    return {"settings": app.settings.public()}


@route("POST", "/api/dest/add")
def api_dest_add(req: Handler):
    paths = req.arg("paths") or ([req.arg("path")] if req.arg("path") else [])
    if not isinstance(paths, list) or not paths:
        raise ApiError(400, "No folders given")
    return req.app.workspace.add_destinations([str(item) for item in paths])


@route("POST", "/api/dest/remove")
def api_dest_remove(req: Handler):
    req.app.workspace.remove_destination(str(req.arg("path", required=True)))


@route("POST", "/api/dest/label")
def api_dest_label(req: Handler):
    req.app.workspace.update_destination(str(req.arg("path", required=True)), str(req.arg("label", "")))


@route("POST", "/api/dest/order")
def api_dest_order(req: Handler):
    paths = req.arg("paths")
    if not isinstance(paths, list):
        raise ApiError(400, "Expected a list of paths")
    req.app.workspace.reorder_destinations([str(item) for item in paths])


@route("POST", "/api/move")
def api_move(req: Handler):
    return req.app.workspace.move(str(req.arg("path", required=True)), str(req.arg("dest", required=True)))


@route("POST", "/api/undo")
def api_undo(req: Handler):
    return req.app.workspace.undo()


@route("POST", "/api/delete")
def api_delete(req: Handler):
    owner = winshell.foreground_window() if req.is_local else 0
    return req.app.workspace.delete(str(req.arg("path", required=True)), lambda path: winshell.recycle(path, owner))


@route("POST", "/api/rename")
def api_rename(req: Handler):
    return req.app.workspace.rename(str(req.arg("path", required=True)), str(req.arg("name", required=True)))


@route("GET", "/api/log")
def api_log(req: Handler):
    try:
        offset = max(0, int(req.arg("offset", 0)))
        limit = max(1, min(200, int(req.arg("limit", 60))))
    except (TypeError, ValueError) as exc:
        raise ApiError(400, "Bad paging values") from exc
    return req.app.workspace.log_page(offset, limit, str(req.arg("q", "") or ""))


@route("GET", "/api/logs")
def api_logs(req: Handler):
    return {"logs": req.app.workspace.list_logs()}


@route("POST", "/api/log/select")
def api_log_select(req: Handler):
    path = str(req.arg("path", required=True))
    if not req.is_local and not req.app.workspace.allowed(path):
        raise ApiError(403, "That log is outside the folders you're sorting.")
    return req.app.workspace.select_log(path)


@route("POST", "/api/log/new")
def api_log_new(req: Handler):
    return req.app.workspace.new_log()


@route("POST", "/api/log/latest")
def api_log_latest(req: Handler):
    return req.app.workspace.latest_log()


@route("POST", "/api/log/undo")
def api_log_undo(req: Handler):
    try:
        index = int(req.arg("index", required=True))
    except (TypeError, ValueError) as exc:
        raise ApiError(400, "Bad log index") from exc
    return req.app.workspace.undo_log(index, str(req.arg("dst", required=True)))


@route("GET", "/api/info")
def api_info(req: Handler):
    return media.describe(req.media_path())


@route("GET", "/api/file")
def api_file(req: Handler):
    path = req.media_path()
    mime = media.MIME_BY_EXT.get(media.ext_of(path), "application/octet-stream")
    cache = "private, max-age=86400" if req.query.get("v") else "no-cache"
    req.send_file(path, mime, cache=cache)


@route("GET", "/api/view")
def api_view(req: Handler):
    path = req.media_path()
    if media.media_kind(path) != "image":
        raise ApiError(415, "Not an image")
    served, mime = media.ensure_view(path)
    cache = "private, max-age=86400" if req.query.get("v") else "no-cache"
    req.send_file(served, mime, cache=cache)


@route("GET", "/api/thumb")
def api_thumb(req: Handler):
    path = req.media_path()
    try:
        size = int(req.arg("s", 256))
    except (TypeError, ValueError):
        size = 256
    thumb = media.thumbnail(path, size)
    if not thumb:
        raise ApiError(404, "No preview")
    req.send_file(thumb, "image/jpeg", cache="private, max-age=604800")


@route("POST", "/api/prepare")
def api_prepare(req: Handler):
    path = req.media_path("path")
    kind = media.media_kind(path)
    if kind not in ("video", "audio"):
        raise ApiError(415, "Only video and audio need converting")
    return req.app.converter.request(path, kind, retry=bool(req.arg("retry", False)))


@route("GET", "/api/play")
def api_play(req: Handler):
    path = req.media_path()
    kind = media.media_kind(path) or ""
    output = req.app.converter.output_for(path, kind)
    if not output:
        raise ApiError(404, "Not converted yet")
    req.send_file(output[0], output[1], cache="private, max-age=86400")


@route("GET", "/api/browse")
def api_browse(req: Handler):
    raw = str(req.arg("path", "") or "").strip().strip('"')
    drives = winshell.list_drives()
    if not raw:
        home = os.path.expanduser("~")
        places = []
        for label, folder in (
            ("Home", home),
            ("Pictures", os.path.join(home, "Pictures")),
            ("Videos", os.path.join(home, "Videos")),
            ("Downloads", os.path.join(home, "Downloads")),
            ("Desktop", os.path.join(home, "Desktop")),
        ):
            if os.path.isdir(folder):
                places.append({"name": label, "path": folder})
        return {"path": "", "parent": None, "dirs": [], "drives": drives, "places": places, "media": 0}
    path = os.path.normpath(os.path.expanduser(raw))
    if IS_WINDOWS and re.fullmatch(r"[A-Za-z]:", path):
        path += "\\"
    if not os.path.isdir(path):
        raise ApiError(404, "That folder doesn't exist.")
    dirs = []
    media_count = 0
    try:
        with os.scandir(path) as iterator:
            for item in iterator:
                try:
                    if item.is_dir():
                        if item.name.startswith(".") or item.name.lower() in ("$recycle.bin", "system volume information"):
                            continue
                        attrs = getattr(item.stat(), "st_file_attributes", 0)
                        if attrs & 0x2:
                            continue
                        dirs.append({"name": item.name, "path": item.path})
                    elif media.media_kind(item.name):
                        media_count += 1
                except OSError:
                    continue
    except OSError as exc:
        raise ApiError(403, f"Can't open that folder: {exc.strerror or exc}") from exc
    from .workspace import natural_key

    dirs.sort(key=lambda item: natural_key(item["name"]))
    parent = os.path.dirname(path.rstrip("\\/"))
    if parent and norm_key(parent) == norm_key(path):
        parent = ""
    return {"path": path, "parent": parent or "", "dirs": dirs, "drives": drives, "places": [], "media": media_count}


@route("POST", "/api/mkdir")
def api_mkdir(req: Handler):
    parent = str(req.arg("parent", required=True))
    name = str(req.arg("name", required=True)).strip()
    if not name or re.search(r'[\\/:*?"<>|]', name) or name in (".", ".."):
        raise ApiError(400, 'Folder names can\'t contain \\ / : * ? " < > |')
    if not os.path.isdir(parent):
        raise ApiError(404, "Parent folder not found")
    target = os.path.join(parent, name)
    try:
        os.makedirs(target, exist_ok=False)
    except FileExistsError as exc:
        raise ApiError(409, "That folder already exists") from exc
    except OSError as exc:
        raise ApiError(400, f"Couldn't create the folder: {exc.strerror or exc}") from exc
    return {"path": target}


@route("POST", "/api/native/folders", local=True)
def api_native_folders(req: Handler):
    workspace = req.app.workspace
    initial = str(req.arg("initial", "") or workspace.source or os.path.expanduser("~"))
    multi = bool(req.arg("multi", False))
    title = str(req.arg("title", "Choose a folder"))[:120]
    try:
        picked = winshell.pick_folders(initial, title, multi=multi, owner=winshell.foreground_window())
    except OSError as exc:
        raise ApiError(501, str(exc)) from exc
    return {"paths": picked}


@route("POST", "/api/native/file", local=True)
def api_native_file(req: Handler):
    workspace = req.app.workspace
    initial = os.path.dirname(workspace.logfile) if workspace.logfile else workspace.source
    try:
        picked = winshell.pick_file(
            initial or os.path.expanduser("~"),
            "Choose a move log",
            [("Move logs (*.txt)", "*.txt"), ("All files", "*.*")],
            owner=winshell.foreground_window(),
        )
    except OSError as exc:
        raise ApiError(501, str(exc)) from exc
    return {"path": picked}


@route("POST", "/api/open", local=True)
def api_open(req: Handler):
    path = str(req.arg("path", required=True))
    if not os.path.exists(path):
        raise ApiError(404, "File not found")
    try:
        winshell.open_default(path)
    except OSError as exc:
        raise ApiError(500, f"Couldn't open it: {exc.strerror or exc}") from exc


@route("POST", "/api/open-url", local=True)
def api_open_url(req: Handler):
    url = str(req.arg("url", required=True))
    if not re.match(r"^https://github\.com/ripaaf/fair-sorting(/|$)", url):
        raise ApiError(400, "Only Fair Sorting's GitHub pages can be opened from here.")
    import webbrowser

    webbrowser.open(url)


@route("POST", "/api/reveal", local=True)
def api_reveal(req: Handler):
    path = str(req.arg("path", required=True))
    if not os.path.exists(path):
        raise ApiError(404, "Not found")
    try:
        winshell.reveal(path)
    except OSError as exc:
        raise ApiError(500, f"Couldn't open Explorer: {exc.strerror or exc}") from exc


@route("GET", "/api/lan")
def api_lan(req: Handler):
    if not req.is_local:
        return {"remote": True}
    return req.app.lan.status()


@route("POST", "/api/lan/start", local=True)
def api_lan_start(req: Handler):
    try:
        port = int(req.arg("port", req.app.settings.get("lan_port") or 8501))
    except (TypeError, ValueError) as exc:
        raise ApiError(400, "Port must be a number") from exc
    if not 1024 <= port <= 65535:
        raise ApiError(400, "Use a port between 1024 and 65535")
    req.app.lan.start(port)
    return req.app.lan.status()


@route("POST", "/api/lan/stop", local=True)
def api_lan_stop(req: Handler):
    req.app.lan.stop()
    return req.app.lan.status()


@route("POST", "/api/lan/pin", local=True)
def api_lan_pin(req: Handler):
    req.app.regenerate_pin()
    return req.app.lan.status()


@route("POST", "/api/window/fullscreen", local=True)
def api_fullscreen(req: Handler):
    return {"handled": req.app.toggle_fullscreen()}


@route("POST", "/api/window/theme", local=True)
def api_window_theme(req: Handler):
    theme = "light" if req.arg("theme") == "light" else "dark"
    if req.app.on_theme and req.app.window is not None:
        threading.Thread(target=req.app.on_theme, args=(theme,), daemon=True).start()


@route("GET", "/api/update")
def api_update(req: Handler):
    if not req.is_local:
        return {"remote": True}
    return req.app.updater.check(force=req.query.get("force") == "1")


@route("GET", "/api/update/status", local=True)
def api_update_status(req: Handler):
    return dict(req.app.updater.state)


@route("POST", "/api/update/download", local=True)
def api_update_download(req: Handler):
    try:
        return req.app.updater.start_download()
    except RuntimeError as exc:
        raise ApiError(409, str(exc)) from exc


@route("POST", "/api/update/apply", local=True)
def api_update_apply(req: Handler):
    try:
        kind = req.app.updater.apply()
    except (RuntimeError, OSError) as exc:
        raise ApiError(409, str(exc)) from exc
    threading.Timer(0.8, req.app.quit).start()
    return {"kind": kind}


@route("GET", "/api/cache")
def api_cache(req: Handler):
    return {"size": media.cache_size()}


@route("POST", "/api/cache/clear", local=True)
def api_cache_clear(req: Handler):
    media.clear_cache()
    return {"size": media.cache_size()}


@route("POST", "/api/ffmpeg/install", local=True)
def api_ffmpeg_install(req: Handler):
    return req.app.installer.start()


@route("GET", "/api/ffmpeg/status")
def api_ffmpeg_status(req: Handler):
    return {**req.app.installer.state, "ffmpeg": bool(media.TOOLS.ffmpeg)}


# ------------------------------------------------------------------------ startup


def start_server(app: App, host: str, port: int) -> FairServer:
    httpd = FairServer((host, port), app, lan=host not in ("127.0.0.1", "localhost"))
    app.main_port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, name="fs-http", daemon=True)
    thread.start()
    return httpd
