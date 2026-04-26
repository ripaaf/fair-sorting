from __future__ import annotations

import concurrent.futures
import hashlib
import importlib.util
import json
import math
import os
import random
import shutil
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from tkinter import filedialog, simpledialog, ttk

import cv2
import numpy as np
import pygame
from PIL import Image, ImageDraw, ImageTk, UnidentifiedImageError

try:
    import psutil
except Exception:
    psutil = None

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIF_ENABLED = True
except Exception:
    HEIF_ENABLED = False

try:
    import imageio.v3 as iio

    IMAGEIO_ENABLED = True
except Exception:
    iio = None
    IMAGEIO_ENABLED = False

IMAGE_EXTENSIONS = (
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
)

VIDEO_EXTENSIONS = (
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
)

AUDIO_EXTENSIONS = (
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
)

SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS + VIDEO_EXTENSIONS + AUDIO_EXTENSIONS


@dataclass
class MoveRecord:
    source_path: str
    destination_path: str


class PhotoVideoViewer:
    ZOOM_MIN = 20
    ZOOM_MAX = 800
    ZOOM_STEP = 10

    AUDIO_BARS = 21
    AUDIO_VIS_INTERVAL_MS = 120
    FFPROBE_TIMEOUT_SEC = 5.0
    FFMPEG_POSTER_TIMEOUT_SEC = 10.0
    FFMPEG_PROXY_TIMEOUT_SEC = 45.0
    FFMPEG_AUDIO_TIMEOUT_SEC = 30.0
    VIDEO_PREP_POLL_INTERVAL_MS = 90
    PREVIEW_RESIZE_SETTLE_MS = 120
    WEBSERVER_POLL_INTERVAL_MS = 500

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Fair Sorting")
        self.root.geometry("1220x790")
        self.root.minsize(1060, 720)

        icon_path = os.path.join(os.path.dirname(__file__), "cocute.ico")
        if os.path.exists(icon_path):
            try:
                self.root.iconbitmap(icon_path)
            except tk.TclError:
                pass

        self.themes = {
            "light": {
                "bg": "#F3F6FB",
                "panel": "#FFFFFF",
                "display": "#E7EEF8",
                "accent": "#0F6CBD",
                "text": "#10243D",
                "muted": "#5F7288",
                "danger": "#B42318",
                "overlay": "#F8FBFF",
                "overlay_border": "#D0DDEE",
                "panel_border": "#D4E0F0",
                "button_bg": "#E9F0FA",
                "button_hover": "#DCE9F8",
                "button_pressed": "#D0E2F7",
                "primary_bg": "#0F6CBD",
                "primary_hover": "#115B9B",
                "primary_pressed": "#0E4D83",
                "danger_btn_bg": "#FDECE9",
                "danger_btn_hover": "#FAD9D3",
                "danger_btn_pressed": "#F8C9C1",
                "progress_trough": "#DCE7F5",
                "dest_card": "#EEF3FB",
                "listbox_bg": "#F4F8FE",
                "status_bg": "#DDF8E7",
                "status_fg": "#11663B",
                "toast_info_bg": "#E8F2FF",
                "toast_info_fg": "#103B72",
                "toast_success_bg": "#DDF8E7",
                "toast_success_fg": "#11663B",
                "toast_error_bg": "#FDECE9",
                "toast_error_fg": "#7A1A12",
            },
            "dark": {
                "bg": "#0F1218",
                "panel": "#161B23",
                "display": "#0E1420",
                "accent": "#4EA1FF",
                "text": "#E8EEF7",
                "muted": "#9CADC1",
                "danger": "#FF8A80",
                "overlay": "#1B2430",
                "overlay_border": "#2F3B4E",
                "panel_border": "#2B3647",
                "button_bg": "#263446",
                "button_hover": "#31445D",
                "button_pressed": "#3A5070",
                "primary_bg": "#2B86F6",
                "primary_hover": "#2578DC",
                "primary_pressed": "#1E67BF",
                "danger_btn_bg": "#4E2628",
                "danger_btn_hover": "#633033",
                "danger_btn_pressed": "#7A3A3E",
                "progress_trough": "#2A3445",
                "dest_card": "#1A2534",
                "listbox_bg": "#131A25",
                "status_bg": "#1F4E35",
                "status_fg": "#C8F2D8",
                "toast_info_bg": "#1D2A3C",
                "toast_info_fg": "#CDE3FF",
                "toast_success_bg": "#1F4E35",
                "toast_success_fg": "#C8F2D8",
                "toast_error_bg": "#4A2528",
                "toast_error_fg": "#FFD4D0",
            },
        }
        self.theme_mode = "dark"
        self._apply_theme_palette(self.theme_mode)

        if getattr(sys, "frozen", False):
            self.app_home = os.path.dirname(sys.executable)
            self.assets_dir = getattr(sys, "_MEIPASS", self.app_home)
        else:
            self.app_home = os.path.dirname(os.path.abspath(__file__))
            self.assets_dir = self.app_home
        self.script_dir = self.app_home
        self.output_dir = os.path.join(self.app_home, "output")
        os.makedirs(self.output_dir, exist_ok=True)
        self.config_file = os.path.join(self.app_home, "fair_config.txt")
        self.logfile = ""
        self.web_server_runtime_file = os.path.join(self.output_dir, "web_server_runtime.json")
        self.ffmpeg_path = self._find_binary("ffmpeg")
        self.ffprobe_path = self._find_binary("ffprobe")
        self.preview_cache_dir = os.path.join(self.output_dir, "desktop_preview_cache")

        self.file_paths: list[str] = []
        self.current_index = 0
        self.current_file_path: str | None = None
        self.current_media_kind = "none"
        self.loaded_folder_path = ""
        self.last_folder_path = os.path.expanduser("~")

        self.destination_folders: list[str] = []
        self.undo_record: MoveRecord | None = None
        self.log_entries: list[MoveRecord] = []
        self.log_display_to_record_indices: list[int] = []
        self.log_context_index: int | None = None

        self.zoom_level = 100
        self.pan_x = 0
        self.pan_y = 0
        self.drag_start_x = 0
        self.drag_start_y = 0

        self.current_pil_image: Image.Image | None = None
        self.display_photo: ImageTk.PhotoImage | None = None
        self.current_media_resolution: tuple[int, int] | None = None
        self.current_media_duration_sec = 0.0
        self.current_media_fps = 0.0

        self.video_capture: cv2.VideoCapture | None = None
        self.video_after_id: str | None = None
        self.playing_video = False
        self.video_audio_process: subprocess.Popen | None = None
        self.video_total_frames = 0
        self.video_last_rendered_frame_index = -1

        self.audio_ready = False
        self.audio_paused = False
        self.audio_visualizer_after_id: str | None = None
        self.audio_poll_after_id: str | None = None
        self.audio_bar_ids: list[int] = []
        self.audio_phase = 0.0
        self.audio_seek_offset_sec = 0.0
        self.audio_last_pos_sec = 0.0
        self.current_preview_audio_path: str | None = None
        self.playback_started_at = 0.0
        self.playback_start_offset_sec = 0.0
        self.playback_current_position_sec = 0.0
        self.audio_duration_cache: dict[str, float] = {}
        self.preview_audio_cache: dict[str, str] = {}
        self.video_preview_state: dict[str, dict[str, object]] = {}
        self.current_video_preview_available = False
        self.current_video_playback_available = False
        self.current_video_proxy_path: str | None = None
        self.current_video_poster_path: str | None = None
        self.current_video_capture_source: str | None = None
        self.current_video_prepared_audio_path: str | None = None
        self.current_video_failure_reason = ""
        self.current_video_prepare_request_id = 0
        self.video_prepare_future: concurrent.futures.Future | None = None
        self.video_prepare_poll_after_id: str | None = None

        self.resize_after_id: str | None = None
        self.preview_resize_settle_after_id: str | None = None
        self.preview_resize_in_progress = False

        self.overlay_content_anim_after_id: str | None = None
        self.overlay_slide_anim_after_id: str | None = None
        self.overlay_autohide_after_id: str | None = None

        self.overlay_content_target_height = 0
        self.overlay_content_current_height = 0
        self.overlay_visible_y = 12
        self.overlay_hidden_y = -120
        self.overlay_current_y = self.overlay_visible_y
        self.overlay_visible = True
        self.overlay_mouse_inside = False
        self.overlay_expanded_width = 780
        self.overlay_docked_width = 420
        self.overlay_compact_width = 360
        self.overlay_min_width = 430
        self.overlay_docked_min_width = 340
        self.overlay_max_width = 1120
        self.overlay_side_margin = 16
        self.overlay_anchor_mode = "center"
        self.overlay_resize_active = False
        self.overlay_resize_hover = False
        self.overlay_resize_start_x = 0
        self.overlay_resize_start_width = 0
        self.overlay_resize_handle_width = 16
        self.overlay_resize_visual_width = 2
        self.overlay_anim_frame_ms = 12
        self.overlay_slide_duration_ms = 190
        self.overlay_content_duration_ms = 170
        self.seekbar_percent = 0.0
        self.seekbar_enabled = False

        self.immersive_mode = False
        self.immersive_idle_ms = 2200
        self.display_min_height = 330
        self.bottom_panels_min_height = 170
        self.destination_shell_min_width = 320
        self.log_card_min_width = 280
        self.icon_images: dict[str, ImageTk.PhotoImage] = {}
        self.notification_widgets: list[tk.Frame] = []
        self.notification_after_ids: dict[tk.Frame, str] = {}
        self.status_chip_reset_after_id: str | None = None
        self.shortcut_items = self._build_shortcut_items()
        self.shortcut_dropdown_visible = False
        self.current_placeholder_title = "Choose a folder to start sorting"
        self.current_placeholder_subtitle = "Images, videos, and audio are supported."
        self.background_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=4,
            thread_name_prefix="fair-sorting",
        )
        self.web_server_state = "stopped"
        self.web_server_port: int | None = None
        self.web_server_url = ""
        self.web_server_lan_url = ""
        self.web_server_process: subprocess.Popen | None = None
        self.web_server_python = ""
        self.web_server_poll_after_id: str | None = None
        self.web_server_logs: list[str] = []
        self.web_server_ready = False
        self.web_server_error = ""
        self.web_server_log_thread: threading.Thread | None = None
        self.web_server_bind_address = "0.0.0.0"
        self.web_server_browser_opened = False

        self._setup_styles()
        self._create_button_icons()
        self._build_layout()
        self._create_context_menus()
        self._bind_events()
        self._cleanup_stale_web_server()

        self.last_folder_path, saved_destinations = self._load_config()
        self.logfile = self._resolve_logfile_for_folder(self.last_folder_path)
        for destination_folder in saved_destinations:
            self._add_destination_path(destination_folder, save=False)

        self._refresh_destination_buttons()
        self._load_log_entries()
        self._refresh_log_list()
        self._update_logfile_label()
        self.choose_file_message()

        if self.last_folder_path and os.path.isdir(self.last_folder_path):
            self.load_folder_from_path(self.last_folder_path, persist=False)

        self._refresh_theme_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        if HEIF_ENABLED:
            self._set_status("HEIF/HEIC codec support enabled", 2600)

    @staticmethod
    def _hex_to_rgb(color: str) -> tuple[int, int, int]:
        color = color.lstrip("#")
        return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)

    @staticmethod
    def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
        return "#{:02X}{:02X}{:02X}".format(*rgb)

    def _blend_hex(self, foreground: str, background: str, alpha: float) -> str:
        alpha = max(0.0, min(1.0, alpha))
        fg = self._hex_to_rgb(foreground)
        bg = self._hex_to_rgb(background)
        mixed = tuple(int((fg[idx] * alpha) + (bg[idx] * (1.0 - alpha))) for idx in range(3))
        return self._rgb_to_hex(mixed)

    def _apply_theme_palette(self, mode: str):
        mode = mode if mode in self.themes else "light"
        self.theme_mode = mode
        palette = self.themes[mode]

        self.color_bg = palette["bg"]
        self.color_panel = palette["panel"]
        self.color_display = palette["display"]
        self.color_accent = palette["accent"]
        self.color_text = palette["text"]
        self.color_muted = palette["muted"]
        self.color_danger = palette["danger"]

        self.overlay_glass_color = self._blend_hex(
            palette["panel"],
            palette["display"],
            0.16 if mode == "light" else 0.22,
        )
        self.overlay_border_color = self._blend_hex(
            palette["overlay_border"],
            palette["display"],
            0.5 if mode == "light" else 0.58,
        )
        self.overlay_button_bg = self._blend_hex(
            palette["button_bg"],
            palette["overlay"],
            0.44 if mode == "light" else 0.54,
        )
        self.overlay_button_hover = self._blend_hex(
            palette["button_hover"],
            palette["overlay"],
            0.58 if mode == "light" else 0.66,
        )
        self.overlay_button_pressed = self._blend_hex(
            palette["button_pressed"],
            palette["overlay"],
            0.66 if mode == "light" else 0.74,
        )
        self.overlay_handle_color = self._blend_hex(
            palette["panel_border"],
            self.overlay_glass_color,
            0.16 if mode == "light" else 0.24,
        )
        self.overlay_handle_hover_color = self._blend_hex(
            palette["accent"],
            self.overlay_glass_color,
            0.18 if mode == "light" else 0.28,
        )
        self.overlay_handle_active_color = self._blend_hex(
            palette["accent"],
            self.overlay_glass_color,
            0.34 if mode == "light" else 0.46,
        )
        self.panel_border_color = palette["panel_border"]
        self.destination_card_color = palette["dest_card"]
        self.listbox_bg_color = palette["listbox_bg"]

    def _find_binary(self, executable_name: str) -> str | None:
        names: list[str] = [executable_name]
        if os.name == "nt" and not executable_name.endswith(".exe"):
            names.insert(0, f"{executable_name}.exe")

        search_roots = [
            self.script_dir,
            self.assets_dir,
            os.path.join(self.script_dir, "ffmpeg"),
            os.path.join(self.script_dir, "ffmpeg", "bin"),
            os.path.join(self.script_dir, "tools", "ffmpeg"),
            os.path.join(self.script_dir, "tools", "ffmpeg", "bin"),
            os.path.join(self.assets_dir, "ffmpeg"),
            os.path.join(self.assets_dir, "ffmpeg", "bin"),
            os.path.join(self.assets_dir, "tools", "ffmpeg"),
            os.path.join(self.assets_dir, "tools", "ffmpeg", "bin"),
        ]

        for name in names:
            from_path = shutil.which(name)
            if from_path:
                return from_path

            for root in search_roots:
                candidate = os.path.join(root, name)
                if os.path.isfile(candidate):
                    return candidate

        return None

    def _setup_styles(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(
            "TButton",
            font=("Segoe UI", 10),
            padding=(12, 7),
            relief="flat",
            borderwidth=0,
            background=self.themes[self.theme_mode]["button_bg"],
            foreground=self.color_text,
            lightcolor=self.themes[self.theme_mode]["button_bg"],
            darkcolor=self.themes[self.theme_mode]["button_bg"],
            bordercolor=self.themes[self.theme_mode]["button_bg"],
            focuscolor=self.themes[self.theme_mode]["button_bg"],
        )
        style.map(
            "TButton",
            background=[
                ("active", self.themes[self.theme_mode]["button_hover"]),
                ("pressed", self.themes[self.theme_mode]["button_pressed"]),
            ],
            foreground=[("disabled", "#8EA2B7")],
        )

        style.configure(
            "Primary.TButton",
            font=("Segoe UI Semibold", 10),
            padding=(12, 7),
            background=self.color_accent,
            foreground="#FFFFFF",
            relief="flat",
            borderwidth=0,
            lightcolor=self.color_accent,
            darkcolor=self.color_accent,
            bordercolor=self.color_accent,
            focuscolor=self.color_accent,
        )
        style.map(
            "Primary.TButton",
            background=[
                ("active", self.themes[self.theme_mode]["primary_hover"]),
                ("pressed", self.themes[self.theme_mode]["primary_pressed"]),
            ],
            foreground=[("disabled", "#D4DFEC")],
        )

        style.configure(
            "Danger.TButton",
            font=("Segoe UI Semibold", 9),
            padding=(5, 3),
            foreground=self.color_danger,
            background=self.themes[self.theme_mode]["danger_btn_bg"],
            relief="flat",
            borderwidth=0,
            lightcolor=self.themes[self.theme_mode]["danger_btn_bg"],
            darkcolor=self.themes[self.theme_mode]["danger_btn_bg"],
            bordercolor=self.themes[self.theme_mode]["danger_btn_bg"],
            focuscolor=self.themes[self.theme_mode]["danger_btn_bg"],
        )
        style.map(
            "Danger.TButton",
            background=[
                ("active", self.themes[self.theme_mode]["danger_btn_hover"]),
                ("pressed", self.themes[self.theme_mode]["danger_btn_pressed"]),
            ],
        )

        style.configure(
            "Small.TButton",
            font=("Segoe UI", 9),
            padding=(8, 4),
            background=self.themes[self.theme_mode]["button_bg"],
            foreground=self.color_text,
            lightcolor=self.themes[self.theme_mode]["button_bg"],
            darkcolor=self.themes[self.theme_mode]["button_bg"],
            bordercolor=self.themes[self.theme_mode]["button_bg"],
            focuscolor=self.themes[self.theme_mode]["button_bg"],
        )
        style.map(
            "Small.TButton",
            background=[
                ("active", self.themes[self.theme_mode]["button_hover"]),
                ("pressed", self.themes[self.theme_mode]["button_pressed"]),
            ],
            foreground=[("disabled", "#8EA2B7")],
        )
        style.configure(
            "Icon.TButton",
            font=("Segoe UI Semibold", 9),
            padding=(7, 4),
            relief="flat",
            borderwidth=0,
            background=self.themes[self.theme_mode]["button_bg"],
            foreground=self.color_text,
            lightcolor=self.themes[self.theme_mode]["button_bg"],
            darkcolor=self.themes[self.theme_mode]["button_bg"],
            bordercolor=self.themes[self.theme_mode]["button_bg"],
            focuscolor=self.themes[self.theme_mode]["button_bg"],
        )
        style.map(
            "Icon.TButton",
            background=[
                ("active", self.themes[self.theme_mode]["button_hover"]),
                ("pressed", self.themes[self.theme_mode]["button_pressed"]),
            ],
            foreground=[("disabled", "#8EA2B7")],
        )
        style.configure(
            "Overlay.TButton",
            font=("Segoe UI", 10),
            padding=(12, 6),
            relief="flat",
            borderwidth=0,
            background=self.overlay_button_bg,
            foreground=self.color_text,
            lightcolor=self.overlay_button_bg,
            darkcolor=self.overlay_button_bg,
            bordercolor=self.overlay_button_bg,
            focuscolor=self.overlay_button_bg,
        )
        style.map(
            "Overlay.TButton",
            background=[
                ("active", self.overlay_button_hover),
                ("pressed", self.overlay_button_pressed),
            ],
            foreground=[("disabled", "#8EA2B7")],
        )
        style.configure(
            "OverlaySmall.TButton",
            font=("Segoe UI", 9),
            padding=(8, 4),
            relief="flat",
            borderwidth=0,
            background=self.overlay_button_bg,
            foreground=self.color_text,
            lightcolor=self.overlay_button_bg,
            darkcolor=self.overlay_button_bg,
            bordercolor=self.overlay_button_bg,
            focuscolor=self.overlay_button_bg,
        )
        style.map(
            "OverlaySmall.TButton",
            background=[
                ("active", self.overlay_button_hover),
                ("pressed", self.overlay_button_pressed),
            ],
            foreground=[("disabled", "#8EA2B7")],
        )
        style.configure(
            "OverlayIcon.TButton",
            font=("Segoe UI Semibold", 9),
            padding=(7, 4),
            relief="flat",
            borderwidth=0,
            background=self.overlay_button_bg,
            foreground=self.color_text,
            lightcolor=self.overlay_button_bg,
            darkcolor=self.overlay_button_bg,
            bordercolor=self.overlay_button_bg,
            focuscolor=self.overlay_button_bg,
        )
        style.map(
            "OverlayIcon.TButton",
            background=[
                ("active", self.overlay_button_hover),
                ("pressed", self.overlay_button_pressed),
            ],
            foreground=[("disabled", "#8EA2B7")],
        )
        style.configure(
            "QuickDest.TButton",
            font=("Segoe UI", 9),
            padding=(10, 5) if self.immersive_mode else (12, 7),
            relief="flat",
            borderwidth=0,
            background=self.themes[self.theme_mode]["button_bg"],
            foreground=self.color_text,
            lightcolor=self.themes[self.theme_mode]["button_bg"],
            darkcolor=self.themes[self.theme_mode]["button_bg"],
            bordercolor=self.themes[self.theme_mode]["button_bg"],
            focuscolor=self.themes[self.theme_mode]["button_bg"],
        )
        style.map(
            "QuickDest.TButton",
            background=[
                ("active", self.themes[self.theme_mode]["button_hover"]),
                ("pressed", self.themes[self.theme_mode]["button_pressed"]),
            ],
            foreground=[("disabled", "#8EA2B7")],
        )
        style.configure(
            "HeaderInfo.TButton",
            font=("Segoe UI Semibold", 9),
            padding=(7, 3),
            relief="flat",
            borderwidth=0,
            background=self.themes[self.theme_mode]["button_bg"],
            foreground=self.color_text,
            lightcolor=self.themes[self.theme_mode]["button_bg"],
            darkcolor=self.themes[self.theme_mode]["button_bg"],
            bordercolor=self.themes[self.theme_mode]["button_bg"],
            focuscolor=self.themes[self.theme_mode]["button_bg"],
        )
        style.map(
            "HeaderInfo.TButton",
            background=[
                ("active", self.themes[self.theme_mode]["button_hover"]),
                ("pressed", self.themes[self.theme_mode]["button_pressed"]),
            ],
            foreground=[("disabled", "#8EA2B7")],
        )
        style.configure(
            "Media.Horizontal.TProgressbar",
            troughcolor=self.themes[self.theme_mode]["progress_trough"],
            bordercolor=self.themes[self.theme_mode]["progress_trough"],
        )

    def _create_button_icons(self):
        size = 18
        accent = self.color_accent

        play_img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(play_img)
        draw.polygon([(5, 3), (15, 9), (5, 15)], fill=accent)

        pause_img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(pause_img)
        draw.rounded_rectangle((4, 3, 8, 15), radius=1, fill=accent)
        draw.rounded_rectangle((10, 3, 14, 15), radius=1, fill=accent)

        external_img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(external_img)
        draw.rounded_rectangle((2, 6, 12, 16), radius=2, outline=accent, width=2)
        draw.line((8, 3, 15, 3), fill=accent, width=2)
        draw.line((15, 3, 15, 10), fill=accent, width=2)
        draw.line((8, 10, 15, 3), fill=accent, width=2)

        explorer_img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(explorer_img)
        draw.rounded_rectangle((2, 6, 16, 15), radius=3, outline=accent, width=2)
        draw.line((4, 6, 7, 3), fill=accent, width=2)
        draw.line((7, 3, 12, 3), fill=accent, width=2)

        web_img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(web_img)
        draw.ellipse((2, 2, 16, 16), outline=accent, width=2)
        draw.arc((3, 3, 15, 15), start=90, end=270, fill=accent, width=2)
        draw.arc((3, 3, 15, 15), start=-90, end=90, fill=accent, width=2)
        draw.line((2, 9, 16, 9), fill=accent, width=2)
        draw.line((9, 2, 9, 16), fill=accent, width=2)

        self.icon_images = {
            "play": ImageTk.PhotoImage(play_img),
            "pause": ImageTk.PhotoImage(pause_img),
            "external": ImageTk.PhotoImage(external_img),
            "explorer": ImageTk.PhotoImage(explorer_img),
            "web": ImageTk.PhotoImage(web_img),
        }

    def _set_play_icon(self):
        if hasattr(self, "play_pause_button") and self.icon_images:
            self.play_pause_button.config(image=self.icon_images["play"], text="")

    def _set_pause_icon(self):
        if hasattr(self, "play_pause_button") and self.icon_images:
            self.play_pause_button.config(image=self.icon_images["pause"], text="")

    def _focus_preview_canvas(self):
        target = getattr(self, "preview_canvas", None)
        if target is not None:
            try:
                target.focus_set()
                return
            except tk.TclError:
                pass
        try:
            self.root.focus_set()
        except tk.TclError:
            pass

    def _defer_preview_focus(self, event=None):
        self.root.after_idle(self._focus_preview_canvas)

    def _make_widget_unfocusable(self, widget: tk.Widget):
        try:
            widget.configure(takefocus=False)
        except tk.TclError:
            pass
        if isinstance(widget, (ttk.Button, tk.Button)):
            widget.bind("<ButtonRelease-1>", self._defer_preview_focus, add="+")

    def _prepare_clickable_controls(self, root_widget: tk.Widget):
        self._make_widget_unfocusable(root_widget)
        for child in root_widget.winfo_children():
            self._prepare_clickable_controls(child)

    def _dispatch_shortcut(self, handler, event=None):
        result = handler(event)
        self._defer_preview_focus()
        if self.immersive_mode:
            self._set_overlay_visibility(True, animate=True)
            self._schedule_overlay_autohide()
        return "break" if result is None else result

    @staticmethod
    def _ease_out_cubic(progress: float) -> float:
        progress = max(0.0, min(1.0, progress))
        return 1.0 - pow(1.0 - progress, 3)

    @staticmethod
    def _paned_has_pane(paned: tk.PanedWindow, widget: tk.Widget) -> bool:
        return str(widget) in {str(pane) for pane in paned.panes()}

    def _ensure_paned_pane(self, paned: tk.PanedWindow, widget: tk.Widget, **options):
        if self._paned_has_pane(paned, widget):
            if options:
                paned.paneconfigure(widget, **options)
            return
        paned.add(widget, **options)

    def _forget_paned_pane(self, paned: tk.PanedWindow, widget: tk.Widget):
        if self._paned_has_pane(paned, widget):
            paned.forget(widget)

    def _apply_initial_pane_sashes(self):
        if not hasattr(self, "workspace_pane") or not hasattr(self, "bottom_panels"):
            return

        self.root.update_idletasks()
        if self._paned_has_pane(self.workspace_pane, self.bottom_panels) and len(self.workspace_pane.panes()) > 1:
            total_height = max(1, self.workspace_pane.winfo_height())
            target_y = int(total_height * (0.76 if self.immersive_mode else 0.72))
            max_y = total_height - self.bottom_panels_min_height
            target_y = max(self.display_min_height, min(target_y, max_y))
            try:
                self.workspace_pane.sash_place(0, 0, target_y)
            except tk.TclError:
                pass

        has_destination = self._paned_has_pane(self.bottom_panels, self.destination_shell)
        has_log = self._paned_has_pane(self.bottom_panels, self.log_card)
        if has_destination and has_log and len(self.bottom_panels.panes()) > 1:
            total_width = max(1, self.bottom_panels.winfo_width())
            target_x = int(total_width * 0.58)
            max_x = total_width - self.log_card_min_width
            target_x = max(self.destination_shell_min_width, min(target_x, max_x))
            try:
                self.bottom_panels.sash_place(0, target_x, 0)
            except tk.TclError:
                pass

    def _theme_palette(self) -> dict[str, str]:
        return self.themes[self.theme_mode]

    def _build_shortcut_items(self) -> list[dict[str, object]]:
        return [
            {
                "section": "Workspace",
                "display": "L",
                "keys": ["l"],
                "description": "Load source folder",
                "binder": "direct",
                "handler": self.load_folder,
            },
            {
                "section": "Workspace",
                "display": "A",
                "keys": ["a"],
                "description": "Add destination folder",
                "binder": "direct",
                "handler": self.set_destination,
            },
            {
                "section": "Playback",
                "display": "P / Space",
                "keys": ["p", "<space>"],
                "description": "Play or pause current media",
                "binder": "direct",
                "handler": self.play_pause,
            },
            {
                "section": "Playback",
                "display": "S",
                "keys": ["s"],
                "description": "Stop playback and go to start",
                "binder": "direct",
                "handler": self.stop_playback,
            },
            {
                "section": "Playback",
                "display": "O",
                "keys": ["o"],
                "description": "Open current file in external player",
                "binder": "lambda",
                "handler": lambda event=None: self.open_external_player(),
            },
            {
                "section": "Navigation",
                "display": "Left / Right",
                "keys": ["<Left>", "<Right>"],
                "description": "Previous or next file",
                "binder": "paired_navigation",
            },
            {
                "section": "Navigation",
                "display": "+ / -",
                "keys": ["<plus>", "<KP_Add>", "<minus>", "<KP_Subtract>"],
                "description": "Zoom in or out",
                "binder": "paired_zoom",
            },
            {
                "section": "Navigation",
                "display": "Ctrl+0",
                "keys": ["<Control-0>"],
                "description": "Reset zoom",
                "binder": "lambda",
                "handler": lambda event=None: self.reset_zoom(),
            },
            {
                "section": "Sorting",
                "display": "Ctrl+Z",
                "keys": ["<Control-z>"],
                "description": "Undo last move",
                "binder": "direct",
                "handler": self.undo_move,
            },
            {
                "section": "Sorting",
                "display": "1-0",
                "keys": ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"],
                "description": "Move to quick destination 1-10",
                "binder": "destination_digits",
            },
            {
                "section": "Viewer",
                "display": "H",
                "keys": ["h"],
                "description": "Show or hide viewer controls",
                "binder": "lambda",
                "handler": lambda event=None: self._toggle_overlay_controls(),
            },
            {
                "section": "Viewer",
                "display": "F",
                "keys": ["f"],
                "description": "Toggle immersive mode",
                "binder": "lambda",
                "handler": lambda event=None: self._toggle_immersive_mode(),
            },
            {
                "section": "Viewer",
                "display": "T",
                "keys": ["t"],
                "description": "Toggle night mode",
                "binder": "lambda",
                "handler": lambda event=None: self._toggle_theme_mode(),
            },
            {
                "section": "Viewer",
                "display": "C",
                "keys": ["c"],
                "description": "Cycle viewer control position",
                "binder": "direct",
                "handler": self._cycle_overlay_position,
            },
            {
                "section": "Viewer",
                "display": "Esc",
                "keys": ["<Escape>"],
                "description": "Close shortcut panel, exit immersive, or show controls",
                "binder": "direct",
                "handler": self._handle_escape,
            },
        ]

    def _shortcut_sections(self) -> list[tuple[str, list[dict[str, object]]]]:
        ordered_sections = ("Workspace", "Playback", "Navigation", "Sorting", "Viewer")
        grouped: dict[str, list[dict[str, object]]] = {name: [] for name in ordered_sections}
        for item in self.shortcut_items:
            grouped.setdefault(str(item["section"]), []).append(item)
        return [(name, grouped[name]) for name in ordered_sections if grouped.get(name)]

    def _bind_shortcuts(self):
        for item in self.shortcut_items:
            binder = str(item.get("binder", "direct"))
            keys = [str(key) for key in item.get("keys", [])]
            if binder in {"direct", "lambda"}:
                handler = item.get("handler")
                for key in keys:
                    self.root.bind(key, lambda event, current_handler=handler: self._dispatch_shortcut(current_handler, event))
            elif binder == "paired_navigation":
                self.root.bind("<Left>", lambda event: self._dispatch_shortcut(self.previous_file, event))
                self.root.bind("<Right>", lambda event: self._dispatch_shortcut(self.next_file, event))
            elif binder == "paired_zoom":
                self.root.bind("<plus>", lambda event: self._dispatch_shortcut(lambda _=None: self.zoom_in(), event))
                self.root.bind("<KP_Add>", lambda event: self._dispatch_shortcut(lambda _=None: self.zoom_in(), event))
                self.root.bind("<minus>", lambda event: self._dispatch_shortcut(lambda _=None: self.zoom_out(), event))
                self.root.bind("<KP_Subtract>", lambda event: self._dispatch_shortcut(lambda _=None: self.zoom_out(), event))
            elif binder == "destination_digits":
                for key in keys:
                    index = 9 if key == "0" else int(key) - 1
                    self.root.bind(
                        key,
                        lambda event, idx=index: self._dispatch_shortcut(
                            lambda _=None, current_idx=idx: self._move_to_destination_index(current_idx),
                            event,
                        ),
                    )

    def _toggle_shortcut_dropdown(self):
        if self.shortcut_dropdown_visible:
            self._hide_shortcut_dropdown()
        else:
            self._show_shortcut_dropdown()

    def _show_shortcut_dropdown(self):
        if not hasattr(self, "shortcut_dropdown_panel") or self.shortcut_dropdown_visible:
            return
        self.shortcut_dropdown_panel.pack(fill=tk.X, pady=(10, 0))
        self.shortcut_dropdown_visible = True

    def _hide_shortcut_dropdown(self):
        if not hasattr(self, "shortcut_dropdown_panel") or not self.shortcut_dropdown_visible:
            return
        self.shortcut_dropdown_panel.pack_forget()
        self.shortcut_dropdown_visible = False

    def _widget_is_shortcut_related(self, widget: tk.Widget | None) -> bool:
        targets = {
            getattr(self, "shortcut_info_button", None),
            getattr(self, "shortcut_dropdown_panel", None),
            getattr(self, "title_wrap", None),
        }
        while widget is not None:
            if widget in targets:
                return True
            widget = getattr(widget, "master", None)
        return False

    def _handle_global_click(self, event=None):
        if not self.shortcut_dropdown_visible:
            return
        widget = getattr(event, "widget", None)
        if self._widget_is_shortcut_related(widget):
            return
        self._hide_shortcut_dropdown()

    def _widget_is_inside(self, widget: tk.Widget | None, root_widget: tk.Widget | None) -> bool:
        if widget is None or root_widget is None:
            return False
        while widget is not None:
            if widget is root_widget:
                return True
            widget = getattr(widget, "master", None)
        return False

    def _status_level_for_text(self, text: str) -> str:
        lowered = text.lower()
        error_tokens = ("error", "fail", "unable", "not found", "invalid", "gagal", "cannot", "missing")
        success_tokens = ("loaded", "enabled", "disabled", "added", "removed", "undo", "restored", "using", "logfile")

        if lowered == "ready":
            return "success"
        if any(token in lowered for token in error_tokens):
            return "error"
        if any(token in lowered for token in success_tokens):
            return "success"
        return "info"

    def _dismiss_toast(self, toast: tk.Frame):
        after_id = self.notification_after_ids.pop(toast, None)
        if after_id:
            try:
                self.root.after_cancel(after_id)
            except tk.TclError:
                pass

        if toast in self.notification_widgets:
            self.notification_widgets.remove(toast)

        try:
            toast.destroy()
        except tk.TclError:
            pass

    def _restyle_single_toast(self, toast: tk.Frame):
        level = getattr(toast, "_toast_level", "info")
        palette = self._theme_palette()
        bg = palette[f"toast_{level}_bg"]
        fg = palette[f"toast_{level}_fg"]

        toast.config(bg=bg, highlightbackground=fg)
        for child in toast.winfo_children():
            if isinstance(child, tk.Label):
                child.config(bg=bg, fg=fg)
            elif isinstance(child, tk.Button):
                child.config(
                    bg=bg,
                    fg=fg,
                    activebackground=bg,
                    activeforeground=fg,
                    highlightbackground=bg,
                )

    def _show_toast(self, text: str, level: str = "info", timeout_ms: int = 4200):
        if not hasattr(self, "toast_container"):
            return

        toast = tk.Frame(
            self.toast_container,
            bg=self._theme_palette()[f"toast_{level}_bg"],
            highlightthickness=1,
            highlightbackground=self._theme_palette()[f"toast_{level}_fg"],
            bd=0,
            padx=10,
            pady=8,
        )
        toast._toast_level = level
        toast.pack(fill=tk.X, pady=(0, 8))

        text_label = tk.Label(
            toast,
            text=text,
            bg=toast.cget("bg"),
            fg=self._theme_palette()[f"toast_{level}_fg"],
            font=("Segoe UI", 9),
            justify="left",
            anchor="w",
            wraplength=280,
        )
        text_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        close_button = tk.Button(
            toast,
            text="x",
            command=lambda current=toast: self._dismiss_toast(current),
            relief=tk.FLAT,
            bd=0,
            padx=6,
            pady=0,
            font=("Segoe UI Semibold", 9),
            cursor="hand2",
        )
        close_button.pack(side=tk.RIGHT, padx=(8, 0))
        self._make_widget_unfocusable(close_button)
        self._restyle_single_toast(toast)

        self.notification_widgets.append(toast)
        if timeout_ms > 0:
            self.notification_after_ids[toast] = self.root.after(
                timeout_ms, lambda current=toast: self._dismiss_toast(current)
            )

    def _set_inline_message(self, text: str = "", level: str = "info", timeout_ms: int = 0):
        if not hasattr(self, "message_label"):
            return

        color_map = {
            "success": self._theme_palette()["status_fg"],
            "error": self.color_danger,
            "info": self.color_muted,
        }
        self.message_label.config(text=text, fg=color_map.get(level, self.color_muted))
        if timeout_ms > 0:
            self.root.after(timeout_ms, self.clear_message)
        self._recalculate_overlay_content_height()

    def _map_theme_bg(self, current_color: str | None) -> str | None:
        if not current_color:
            return None
        current_color = str(current_color)

        palette = self._theme_palette()
        mappings = (
            ("bg", {theme["bg"] for theme in self.themes.values()}),
            ("panel", {theme["panel"] for theme in self.themes.values()}),
            ("display", {theme["display"] for theme in self.themes.values()}),
            ("overlay", {theme["overlay"] for theme in self.themes.values()}),
            ("dest_card", {theme["dest_card"] for theme in self.themes.values()}),
            ("listbox_bg", {theme["listbox_bg"] for theme in self.themes.values()}),
            ("status_bg", {theme["status_bg"] for theme in self.themes.values()}),
        )

        for key, known_values in mappings:
            if current_color in known_values:
                return palette[key]
        return None

    def _map_theme_fg(self, current_color: str | None) -> str | None:
        if not current_color:
            return None
        current_color = str(current_color)

        palette = self._theme_palette()
        mappings = (
            ("text", {theme["text"] for theme in self.themes.values()}),
            ("muted", {theme["muted"] for theme in self.themes.values()}),
            ("danger", {theme["danger"] for theme in self.themes.values()}),
            ("status_fg", {theme["status_fg"] for theme in self.themes.values()}),
            ("toast_info_fg", {theme["toast_info_fg"] for theme in self.themes.values()}),
            ("toast_success_fg", {theme["toast_success_fg"] for theme in self.themes.values()}),
            ("toast_error_fg", {theme["toast_error_fg"] for theme in self.themes.values()}),
        )

        for key, known_values in mappings:
            if current_color in known_values:
                return palette[key]
        return None

    def _apply_theme_recursive(self, widget: tk.Widget):
        if widget in self.notification_widgets:
            self._restyle_single_toast(widget)
            return

        try:
            current_bg = widget.cget("bg")
        except tk.TclError:
            current_bg = None

        try:
            current_fg = widget.cget("fg")
        except tk.TclError:
            current_fg = None

        bg_update = self._map_theme_bg(current_bg)
        fg_update = self._map_theme_fg(current_fg)

        config: dict[str, str] = {}
        if bg_update is not None:
            config["bg"] = bg_update
        if fg_update is not None:
            config["fg"] = fg_update

        overlay_panel = getattr(self, "overlay_panel", None)
        if widget is getattr(self, "preview_canvas", None):
            config["bg"] = self.color_display
        elif self._widget_is_inside(widget, overlay_panel):
            config["bg"] = self.overlay_glass_color
        elif widget is getattr(self, "seekbar_canvas", None):
            config["bg"] = self.overlay_glass_color
        elif isinstance(widget, tk.Listbox):
            config.update(
                bg=self.listbox_bg_color,
                fg=self.color_text,
                selectbackground=self.color_accent,
                selectforeground="white",
                highlightbackground=self.panel_border_color,
                highlightcolor=self.panel_border_color,
            )
        elif isinstance(widget, tk.Menu):
            config.update(
                bg=self.color_panel,
                fg=self.color_text,
                activebackground=self.themes[self.theme_mode]["button_hover"],
                activeforeground=self.color_text,
            )
        elif widget is getattr(self, "status_chip", None):
            config.update(
                bg=self._theme_palette()["status_bg"],
                fg=self._theme_palette()["status_fg"],
            )

        for key in ("highlightbackground", "highlightcolor"):
            try:
                current_highlight = widget.cget(key)
            except tk.TclError:
                continue
            current_highlight = str(current_highlight)

            if widget is overlay_panel:
                config[key] = self.overlay_border_color
            elif current_highlight in {theme["overlay_border"] for theme in self.themes.values()}:
                config[key] = self.overlay_border_color
            elif current_highlight in {theme["panel_border"] for theme in self.themes.values()}:
                config[key] = self.panel_border_color
            elif current_highlight in {theme["progress_trough"] for theme in self.themes.values()}:
                config[key] = self._theme_palette()["progress_trough"]

        if config:
            try:
                widget.config(**config)
            except tk.TclError:
                pass

        for child in widget.winfo_children():
            self._apply_theme_recursive(child)

    def _refresh_overlay_width(self, rerender_preview: bool = True, reflow_content: bool = True):
        if not hasattr(self, "overlay_panel") or not hasattr(self, "display_frame"):
            return

        display_width = max(480, self.display_frame.winfo_width())
        display_height = max(320, self.display_frame.winfo_height())
        docked = self.overlay_anchor_mode in {"left", "right"}
        position_map = {
            "left": {"relx": 0.0, "x": self.overlay_side_margin, "anchor": "nw"},
            "center": {"relx": 0.5, "x": 0, "anchor": "n"},
            "right": {"relx": 1.0, "x": -self.overlay_side_margin, "anchor": "ne"},
        }
        placement = position_map.get(self.overlay_anchor_mode, position_map["center"])

        if self.overlay_controls_collapsed:
            min_width = 260
            preferred_width = self.overlay_compact_width
        elif docked:
            min_width = self.overlay_docked_min_width
            preferred_width = self.overlay_docked_width
        else:
            min_width = self.overlay_min_width
            preferred_width = self.overlay_expanded_width

        max_width = max(min_width, min(self.overlay_max_width, display_width - self.overlay_side_margin * 2))
        width = max(min_width, min(preferred_width, max_width))

        self.overlay_panel.place_configure(y=int(self.overlay_current_y))
        for key in ("height", "relheight"):
            try:
                self.overlay_panel.place_configure(**{key: ""})
            except tk.TclError:
                pass

        if docked and not self.overlay_controls_collapsed:
            docked_height = max(220, display_height - self.overlay_visible_y - self.overlay_side_margin)
            self.overlay_panel.place_configure(height=docked_height)

        self.overlay_panel.place_configure(width=width, **placement)
        self._refresh_overlay_info_layout(width)
        self._refresh_overlay_responsive_layout(width)
        if reflow_content:
            self._recalculate_overlay_content_height(apply_if_expanded=not self.overlay_controls_collapsed)
        self._refresh_overlay_resize_handle()
        self._update_overlay_hidden_position()
        if rerender_preview:
            self._rerender_preview_for_overlay_layout()

    def _refresh_overlay_info_layout(self, width: int):
        if not hasattr(self, "overlay_left_info") or not hasattr(self, "overlay_right_info"):
            return

        wrap_width = max(220, width - 44)
        self.file_name_label.config(wraplength=wrap_width)
        self.file_info_label.config(wraplength=wrap_width)
        self.message_label.config(wraplength=wrap_width)
        self.loaded_path_label.config(wraplength=wrap_width)

        compact_layout = self.overlay_anchor_mode in {"left", "right"} or width < 620
        self.overlay_left_info.pack_forget()
        self.overlay_right_info.pack_forget()
        if compact_layout:
            self.overlay_left_info.pack(fill=tk.X)
            self.overlay_right_info.pack(fill=tk.X, pady=(10, 0))
        else:
            self.overlay_left_info.pack(side=tk.LEFT, fill=tk.X, expand=True)
            self.overlay_right_info.pack(side=tk.LEFT, fill=tk.Y, padx=(14, 0))

    @staticmethod
    def _clear_widget_geometry(widget: tk.Widget):
        try:
            widget.pack_forget()
        except tk.TclError:
            pass
        try:
            widget.grid_forget()
        except tk.TclError:
            pass

    def _reset_overlay_grid(self, container: tk.Widget):
        for child in container.winfo_children():
            self._clear_widget_geometry(child)
        for index in range(6):
            container.grid_columnconfigure(index, weight=0, minsize=0)
            container.grid_rowconfigure(index, weight=0, minsize=0)

    def _refresh_overlay_responsive_layout(self, width: int):
        if not hasattr(self, "overlay_action_row"):
            return

        compact = self.overlay_anchor_mode in {"left", "right"} or width < 620

        self._reset_overlay_grid(self.overlay_action_row)
        if compact:
            self.overlay_action_row.grid_columnconfigure(0, weight=1)
            self.load_button.grid(row=0, column=0, sticky="ew", pady=(0, 6))
            self.set_destination_button.grid(row=1, column=0, sticky="ew", pady=(0, 6))
            self.undo_button.grid(row=2, column=0, sticky="ew")
        else:
            for column in range(3):
                self.overlay_action_row.grid_columnconfigure(column, weight=1)
            self.load_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))
            self.set_destination_button.grid(row=0, column=1, sticky="ew", padx=(0, 6))
            self.undo_button.grid(row=0, column=2, sticky="ew")

        self._reset_overlay_grid(self.overlay_transport_row)
        if compact:
            self.overlay_transport_row.grid_columnconfigure(0, weight=1)
            self.overlay_transport_row.grid_columnconfigure(1, weight=0)
            self.overlay_transport_row.grid_columnconfigure(2, weight=1)
            self.prev_button.grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=(0, 6))
            self.index_label.grid(row=0, column=1, padx=(0, 6), pady=(0, 6))
            self.next_button.grid(row=0, column=2, sticky="ew", pady=(0, 6))
            self.overlay_zoom_wrap.grid(row=1, column=0, columnspan=3, sticky="w")
        else:
            self.overlay_transport_row.grid_columnconfigure(0, weight=1)
            self.overlay_transport_row.grid_columnconfigure(1, weight=0)
            self.overlay_transport_row.grid_columnconfigure(2, weight=1)
            self.overlay_transport_row.grid_columnconfigure(3, weight=0)
            self.prev_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))
            self.index_label.grid(row=0, column=1, padx=(0, 6))
            self.next_button.grid(row=0, column=2, sticky="ew", padx=(0, 10))
            self.overlay_zoom_wrap.grid(row=0, column=3, sticky="w")

        self._reset_overlay_grid(self.overlay_timeline_row)
        if compact:
            self.overlay_timeline_row.grid_columnconfigure(0, weight=1)
            self.overlay_timeline_row.grid_columnconfigure(1, weight=0)
            self.overlay_timeline_row.grid_columnconfigure(2, weight=0)
            self.overlay_timeline_row.grid_columnconfigure(3, weight=0)
            self.overlay_timeline_row.grid_columnconfigure(4, weight=0)
            self.seekbar_canvas.grid(row=0, column=0, columnspan=5, sticky="ew", pady=(0, 6))
            self.video_time_label.grid(row=1, column=0, sticky="w", padx=(0, 8))
            self.play_pause_button.grid(row=1, column=2, padx=(0, 5))
            self.open_player_button.grid(row=1, column=3)
            self.show_in_explorer_button.grid(row=1, column=4)
        else:
            self.overlay_timeline_row.grid_columnconfigure(0, weight=1)
            self.overlay_timeline_row.grid_columnconfigure(1, weight=0)
            self.overlay_timeline_row.grid_columnconfigure(2, weight=0)
            self.overlay_timeline_row.grid_columnconfigure(3, weight=0)
            self.overlay_timeline_row.grid_columnconfigure(4, weight=0)
            self.seekbar_canvas.grid(row=0, column=0, sticky="ew")
            self.video_time_label.grid(row=0, column=1, padx=(8, 8), sticky="e")
            self.play_pause_button.grid(row=0, column=2, padx=(0, 5))
            self.open_player_button.grid(row=0, column=3, padx=(0, 5))
            self.show_in_explorer_button.grid(row=0, column=4)

    def _refresh_overlay_resize_handle(self):
        if not hasattr(self, "overlay_resize_handle"):
            return

        self._refresh_overlay_resize_handle_visual()
        handle_height = max(56, self.overlay_panel.winfo_height() - 20)
        if self.overlay_anchor_mode == "right":
            # Handle di sisi kiri, keluar ke luar panel (ke kiri)
            self.overlay_resize_handle.place(
                relx=0.0,
                x=-self.overlay_resize_handle_width,  # fully outside ke kiri
                y=10,
                anchor="nw",
                height=handle_height,
                width=self.overlay_resize_handle_width,
            )
        else:
            # Handle di sisi kanan, keluar ke luar panel (ke kanan)
            self.overlay_resize_handle.place(
                relx=1.0,
                x=0,  # fully outside ke kanan
                y=10,
                anchor="nw",
                height=handle_height,
                width=self.overlay_resize_handle_width,
            )

    def _refresh_overlay_resize_handle_visual(self):
        if not hasattr(self, "overlay_resize_handle") or not hasattr(self, "overlay_resize_hint"):
            return

        if self.overlay_resize_active:
            hint_color = self.overlay_handle_active_color
        elif self.overlay_resize_hover:
            hint_color = self.overlay_handle_hover_color
        else:
            hint_color = self.panel_border_color  # kontras terhadap canvas bg

        hint_width = self.overlay_resize_visual_width + (1 if (self.overlay_resize_hover or self.overlay_resize_active) else 0)
        self.overlay_resize_handle.config(bg=self.color_display)  # match canvas biar "transparan"
        self.overlay_resize_hint.config(bg=hint_color, width=hint_width)

    def _on_overlay_resize_enter(self, event=None):
        self.overlay_resize_hover = True
        self._refresh_overlay_resize_handle_visual()

    def _on_overlay_resize_leave(self, event=None):
        if self.overlay_resize_active:
            return
        self.overlay_resize_hover = False
        self._refresh_overlay_resize_handle_visual()

    def _preview_safe_bounds(self) -> tuple[int, int, int, int, int, int]:
        canvas_w = max(200, self.preview_canvas.winfo_width())
        canvas_h = max(200, self.preview_canvas.winfo_height())

        left_inset = 0
        right_inset = 0
        overlay_active = (
            hasattr(self, "overlay_panel")
            and self.overlay_visible
            and self.overlay_anchor_mode in {"left", "right"}
        )
        if overlay_active:
            overlay_width = max(0, self.overlay_panel.winfo_width())
            inset = min(max(0, canvas_w - 140), overlay_width + self.overlay_side_margin + 10)
            if self.overlay_anchor_mode == "left":
                left_inset = inset
            else:
                right_inset = inset

        usable_w = max(140, canvas_w - left_inset - right_inset)
        usable_h = max(140, canvas_h)
        center_x = left_inset + usable_w // 2
        center_y = usable_h // 2
        return left_inset, 0, usable_w, usable_h, center_x, center_y

    def _rerender_preview_for_overlay_layout(self):
        if not hasattr(self, "preview_canvas"):
            return
        if self.current_media_kind in ("image", "video") and self.current_pil_image is not None:
            self._render_current_image_on_canvas()
        elif self.current_media_kind == "audio" and self.current_file_path:
            self._render_audio_indicator(os.path.basename(self.current_file_path), paused=self.audio_paused)
            self._start_audio_visualizer()
        elif self.current_media_kind == "none" and not self.current_file_path:
            self._render_placeholder(
                "Choose a folder to start sorting",
                "Images, videos, and audio are supported.",
            )

    def _apply_overlay_position_label(self):
        if hasattr(self, "overlay_position_button"):
            prefix = "Pos" if self.overlay_controls_collapsed else "Position"
            self.overlay_position_button.config(text=f"{prefix}: {self.overlay_anchor_mode.title()}")

    def _cycle_overlay_position(self, event=None):
        order = ("center", "left", "right")
        current_index = order.index(self.overlay_anchor_mode) if self.overlay_anchor_mode in order else 0
        self.overlay_anchor_mode = order[(current_index + 1) % len(order)]
        self._apply_overlay_position_label()
        self._refresh_overlay_width()
        self._set_status(f"Viewer controls moved to {self.overlay_anchor_mode}", 1500)

    def _refresh_overlay_header_mode(self):
        if not hasattr(self, "overlay_toggle_button"):
            return

        if self.overlay_controls_collapsed:
            self.overlay_toggle_button.config(text="Open")
            self.viewer_title_label.config(text="Viewer")
            self.media_badge_label.config(padx=6)
            self.theme_toggle_button.pack_forget()
            self.immersive_toggle_button.pack_forget()
            self.overlay_position_button.pack_forget()
            self.overlay_position_button.pack(side=tk.LEFT, padx=(0, 6))
            self.overlay_toggle_button.pack_forget()
            self.overlay_toggle_button.pack(side=tk.LEFT)
        else:
            self.overlay_toggle_button.config(text="Hide Controls")
            self.viewer_title_label.config(text="Viewer Controls")
            self.media_badge_label.config(padx=8)
            self.theme_toggle_button.pack_forget()
            self.overlay_position_button.pack_forget()
            self.immersive_toggle_button.pack_forget()
            self.theme_toggle_button.pack(side=tk.LEFT, padx=(0, 6))
            self.overlay_position_button.pack(side=tk.LEFT, padx=(0, 6))
            self.immersive_toggle_button.pack(side=tk.LEFT, padx=(0, 6))
            self.overlay_toggle_button.pack_forget()
            self.overlay_toggle_button.pack(side=tk.LEFT)

    def _refresh_theme_widgets(self):
        self.root.configure(bg=self.color_bg)
        self._setup_styles()
        self._create_button_icons()
        self._apply_theme_recursive(self.root)
        if hasattr(self, "workspace_pane"):
            self.workspace_pane.config(bg=self.color_bg)
        if hasattr(self, "bottom_panels"):
            self.bottom_panels.config(bg=self.color_bg)
        self.toast_container.config(bg=self.color_bg)
        self.app_header.config(bg=self.color_bg)
        self._set_media_badge(self.current_media_kind)
        self._refresh_destination_buttons()
        self._refresh_log_list()
        self._apply_overlay_position_label()
        self._refresh_overlay_header_mode()
        self._refresh_overlay_width()
        self._apply_bottom_panel_layout()
        self._prepare_clickable_controls(self.root)
        self.display_frame.config(
            highlightthickness=0 if self.immersive_mode else 1,
            highlightbackground=self.color_display if self.immersive_mode else self.panel_border_color,
        )
        if hasattr(self, "theme_toggle_button"):
            self.theme_toggle_button.config(text="Light Mode" if self.theme_mode == "dark" else "Night Mode")
        self._set_video_progress(self.playback_current_position_sec, self.current_media_duration_sec)
        self._refresh_current_canvas()
        self.open_player_button.config(image=self.icon_images.get("external"))
        if hasattr(self, "show_in_explorer_button"):
            self.show_in_explorer_button.config(image=self.icon_images.get("explorer"))
        if hasattr(self, "log_hint_label"):
            self.log_hint_label.config(bg=self.color_panel, fg=self.color_muted)
        self._refresh_overlay_resize_handle_visual()
        self._refresh_web_controls()
        if self.current_media_kind == "video" and self.playing_video:
            self._set_pause_icon()
        elif self.current_media_kind == "audio" and not self.audio_paused and self.audio_ready:
            self._set_pause_icon()
        else:
            self._set_play_icon()

    def _toggle_theme_mode(self):
        next_mode = "dark" if self.theme_mode == "light" else "light"
        self._apply_theme_palette(next_mode)
        self._refresh_theme_widgets()
        self._set_status(f"{next_mode.title()} mode enabled", 1600)

    def _build_layout(self):
        self.root.configure(bg=self.color_bg)

        self.main_frame = tk.Frame(self.root, bg=self.color_bg)
        self.main_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=14)

        self.toast_container = tk.Frame(self.root, bg=self.color_bg)
        self.toast_container.place(relx=0.98, y=12, anchor="ne")

        self.app_header = tk.Frame(self.main_frame, bg=self.color_bg)
        self.app_header.pack(fill=tk.X, pady=(0, 10))

        self.title_wrap = tk.Frame(self.app_header, bg=self.color_bg)
        self.title_wrap.pack(side=tk.LEFT, fill=tk.X, expand=True)

        title_row = tk.Frame(self.title_wrap, bg=self.color_bg)
        title_row.pack(fill=tk.X)

        title_label = tk.Label(
            title_row,
            text="Fair Sorting",
            font=("Segoe UI Semibold", 20),
            fg=self.color_text,
            bg=self.color_bg,
            anchor="w",
        )
        title_label.pack(side=tk.LEFT)

        self.shortcut_info_button = ttk.Button(
            title_row,
            text="!",
            style="HeaderInfo.TButton",
            width=2,
            command=self._toggle_shortcut_dropdown,
        )
        self.shortcut_info_button.pack(side=tk.LEFT, padx=(8, 0))

        subtitle_label = tk.Label(
            self.title_wrap,
            text="Cleaner workspace for sorting images, videos, and audio",
            font=("Segoe UI", 9),
            fg=self.color_muted,
            bg=self.color_bg,
            anchor="w",
        )
        subtitle_label.pack(fill=tk.X, pady=(2, 0))

        self.shortcut_dropdown_panel = tk.Frame(
            self.title_wrap,
            bg=self.color_panel,
            highlightthickness=1,
            highlightbackground=self.panel_border_color,
            bd=0,
        )
        dropdown_header = tk.Label(
            self.shortcut_dropdown_panel,
            text="Keyboard shortcuts",
            font=("Segoe UI Semibold", 10),
            fg=self.color_text,
            bg=self.color_panel,
            anchor="w",
        )
        dropdown_header.pack(fill=tk.X, padx=12, pady=(10, 4))

        for section_name, items in self._shortcut_sections():
            section_frame = tk.Frame(self.shortcut_dropdown_panel, bg=self.color_panel)
            section_frame.pack(fill=tk.X, padx=12, pady=(0, 8))

            section_label = tk.Label(
                section_frame,
                text=section_name,
                font=("Segoe UI Semibold", 9),
                fg=self.color_muted,
                bg=self.color_panel,
                anchor="w",
            )
            section_label.pack(fill=tk.X, pady=(0, 4))

            for item in items:
                row = tk.Frame(section_frame, bg=self.color_panel)
                row.pack(fill=tk.X, pady=1)

                key_label = tk.Label(
                    row,
                    text=str(item["display"]),
                    font=("Segoe UI Semibold", 9),
                    fg=self.color_text,
                    bg=self.color_panel,
                    width=12,
                    anchor="w",
                )
                key_label.pack(side=tk.LEFT)

                desc_label = tk.Label(
                    row,
                    text=str(item["description"]),
                    font=("Segoe UI", 9),
                    fg=self.color_text,
                    bg=self.color_panel,
                    anchor="w",
                    justify="left",
                    wraplength=500,
                )
                desc_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.status_chip = tk.Label(
            self.app_header,
            text="Ready",
            bg=self.themes[self.theme_mode]["status_bg"],
            fg=self.themes[self.theme_mode]["status_fg"],
            font=("Segoe UI Semibold", 9),
            padx=12,
            pady=4,
        )
        self.status_chip.pack(side=tk.RIGHT)

        self.web_controls_frame = tk.Frame(self.app_header, bg=self.color_bg)
        self.web_controls_frame.pack(side=tk.RIGHT, padx=(0, 10))

        self.web_status_label = tk.Label(
            self.web_controls_frame,
            text="Web off",
            bg=self.color_bg,
            fg=self.color_muted,
            font=("Segoe UI", 9),
            anchor="e",
        )
        self.web_status_label.pack(side=tk.RIGHT)

        self.web_open_button = ttk.Button(
            self.web_controls_frame,
            text="Open",
            style="Small.TButton",
            command=self._open_web_server_url,
            state=tk.DISABLED,
        )
        self.web_open_button.pack(side=tk.RIGHT, padx=(0, 6))
        self._make_widget_unfocusable(self.web_open_button)

        self.web_toggle_button = ttk.Button(
            self.web_controls_frame,
            text="Run Web",
            style="Small.TButton",
            command=self._toggle_web_server,
        )
        self.web_toggle_button.pack(side=tk.RIGHT, padx=(0, 6))
        self._make_widget_unfocusable(self.web_toggle_button)

        self.workspace_pane = tk.PanedWindow(
            self.main_frame,
            orient=tk.VERTICAL,
            sashwidth=10,
            showhandle=False,
            opaqueresize=False,
            bd=0,
            relief=tk.FLAT,
            bg=self.color_bg,
        )
        self.workspace_pane.pack(fill=tk.BOTH, expand=True)

        self.display_frame = tk.Frame(
            self.workspace_pane,
            bg=self.color_display,
            highlightthickness=1,
            highlightbackground=self.panel_border_color,
            bd=0,
        )
        self.workspace_pane.add(self.display_frame, minsize=self.display_min_height)

        self.preview_canvas = tk.Canvas(
            self.display_frame,
            bg=self.color_display,
            highlightthickness=0,
            bd=0,
        )
        self.preview_canvas.pack(fill=tk.BOTH, expand=True)

        self.overlay_panel = tk.Frame(
            self.display_frame,
            bg=self.overlay_glass_color,
            bd=0,
            highlightthickness=1,
            highlightbackground=self.overlay_border_color,
        )
        self.overlay_panel.place(relx=0.5, y=self.overlay_visible_y, anchor="n", width=self.overlay_expanded_width)

        overlay_header = tk.Frame(self.overlay_panel, bg=self.overlay_glass_color)
        overlay_header.pack(fill=tk.X, padx=12, pady=(10, 6))

        overlay_title_wrap = tk.Frame(overlay_header, bg=self.overlay_glass_color)
        overlay_title_wrap.pack(side=tk.LEFT)

        self.viewer_title_label = tk.Label(
            overlay_title_wrap,
            text="Viewer Controls",
            font=("Segoe UI Semibold", 11),
            bg=self.overlay_glass_color,
            fg=self.color_text,
            anchor="w",
        )
        self.viewer_title_label.pack(side=tk.LEFT)

        self.media_badge_label = tk.Label(
            overlay_title_wrap,
            text="IDLE",
            bg="#D8E4F3",
            fg="#21405F",
            font=("Segoe UI Semibold", 8),
            padx=8,
            pady=2,
        )
        self.media_badge_label.pack(side=tk.LEFT, padx=(8, 0))

        self.header_actions = tk.Frame(overlay_header, bg=self.overlay_glass_color)
        self.header_actions.pack(side=tk.RIGHT)

        self.theme_toggle_button = ttk.Button(
            self.header_actions,
            text="Night Mode",
            style="OverlaySmall.TButton",
            command=self._toggle_theme_mode,
        )
        self.theme_toggle_button.pack(side=tk.LEFT, padx=(0, 6))

        self.overlay_position_button = ttk.Button(
            self.header_actions,
            text="Position: Center",
            style="OverlaySmall.TButton",
            command=self._cycle_overlay_position,
        )
        self.overlay_position_button.pack(side=tk.LEFT, padx=(0, 6))

        self.immersive_toggle_button = ttk.Button(
            self.header_actions,
            text="Immersive Off",
            style="OverlaySmall.TButton",
            command=self._toggle_immersive_mode,
        )
        self.immersive_toggle_button.pack(side=tk.LEFT, padx=(0, 6))

        self.overlay_toggle_button = ttk.Button(
            self.header_actions,
            text="Hide Controls",
            style="OverlaySmall.TButton",
            command=self._toggle_overlay_controls,
        )
        self.overlay_toggle_button.pack(side=tk.LEFT)

        self.overlay_content_host = tk.Frame(self.overlay_panel, bg=self.overlay_glass_color, height=1)
        self.overlay_content_host.pack(fill=tk.X, pady=(0, 8))
        self.overlay_content_host.pack_propagate(False)

        self.overlay_content = tk.Frame(self.overlay_content_host, bg=self.overlay_glass_color)
        self.overlay_content.pack(fill=tk.X)

        self.overlay_action_row = tk.Frame(self.overlay_content, bg=self.overlay_glass_color)
        self.overlay_action_row.pack(fill=tk.X, padx=12, pady=(0, 8))

        self.load_button = ttk.Button(
            self.overlay_action_row,
            text="Load Folder",
            command=self.load_folder,
            style="Primary.TButton",
        )
        self.load_button.pack(side=tk.LEFT, padx=(0, 6))

        self.set_destination_button = ttk.Button(
            self.overlay_action_row,
            text="Add Destination",
            command=self.set_destination,
            style="Overlay.TButton",
        )
        self.set_destination_button.pack(side=tk.LEFT, padx=(0, 6))

        self.undo_button = ttk.Button(
            self.overlay_action_row,
            text="Undo Last Move",
            command=self.undo_move,
            state=tk.DISABLED,
            style="Overlay.TButton",
        )
        self.undo_button.pack(side=tk.LEFT)

        self.overlay_info_row = tk.Frame(self.overlay_content, bg=self.overlay_glass_color)
        self.overlay_info_row.pack(fill=tk.X, padx=12)

        self.overlay_left_info = tk.Frame(self.overlay_info_row, bg=self.overlay_glass_color)
        self.overlay_left_info.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.file_name_label = tk.Label(
            self.overlay_left_info,
            text="File Name\n-",
            bg=self.overlay_glass_color,
            fg=self.color_text,
            font=("Segoe UI Semibold", 10),
            wraplength=420,
            justify="left",
            anchor="w",
        )
        self.file_name_label.pack(fill=tk.X)

        self.file_info_label = tk.Label(
            self.overlay_left_info,
            text="",
            bg=self.overlay_glass_color,
            fg=self.color_muted,
            font=("Segoe UI", 9),
            wraplength=500,
            justify="left",
            anchor="w",
        )
        self.file_info_label.pack(fill=tk.X, pady=(2, 0))

        self.message_label = tk.Label(
            self.overlay_left_info,
            text="",
            bg=self.overlay_glass_color,
            fg=self.color_danger,
            font=("Segoe UI", 9),
            wraplength=500,
            justify="left",
            anchor="w",
        )
        self.message_label.pack(fill=tk.X, pady=(2, 0))

        self.overlay_right_info = tk.Frame(self.overlay_info_row, bg=self.overlay_glass_color)
        self.overlay_right_info.pack(side=tk.LEFT, fill=tk.Y, padx=(14, 0))

        loaded_title = tk.Label(
            self.overlay_right_info,
            text="Loaded Folder",
            bg=self.overlay_glass_color,
            fg=self.color_muted,
            font=("Segoe UI", 9),
            anchor="w",
            justify="left",
        )
        loaded_title.pack(fill=tk.X)

        self.loaded_path_label = tk.Label(
            self.overlay_right_info,
            text="-",
            bg=self.overlay_glass_color,
            fg=self.color_text,
            font=("Segoe UI", 9),
            wraplength=340,
            justify="left",
            anchor="w",
        )
        self.loaded_path_label.pack(fill=tk.X, pady=(2, 0))

        self.overlay_transport_row = tk.Frame(self.overlay_content, bg=self.overlay_glass_color)
        self.overlay_transport_row.pack(fill=tk.X, padx=12, pady=(8, 4))

        self.prev_button = ttk.Button(
            self.overlay_transport_row,
            text="Previous",
            command=self.previous_file,
            style="Overlay.TButton",
        )
        self.prev_button.pack(side=tk.LEFT, padx=(0, 6))

        self.index_label = tk.Label(
            self.overlay_transport_row,
            text="0 / 0",
            bg=self.overlay_glass_color,
            fg=self.color_text,
            font=("Segoe UI Semibold", 10),
            width=10,
        )
        self.index_label.pack(side=tk.LEFT, padx=(0, 6))

        self.next_button = ttk.Button(
            self.overlay_transport_row,
            text="Next",
            command=self.next_file,
            style="Overlay.TButton",
        )
        self.next_button.pack(side=tk.LEFT, padx=(0, 10))

        self.overlay_zoom_wrap = tk.Frame(self.overlay_transport_row, bg=self.overlay_glass_color)
        self.overlay_zoom_wrap.pack(side=tk.LEFT)

        self.zoom_out_button = ttk.Button(
            self.overlay_zoom_wrap,
            text="-",
            style="OverlaySmall.TButton",
            width=3,
            command=self.zoom_out,
        )
        self.zoom_out_button.pack(side=tk.LEFT, padx=(0, 4))

        self.zoom_reset_button = ttk.Button(
            self.overlay_zoom_wrap,
            text="Reset",
            style="OverlaySmall.TButton",
            command=self.reset_zoom,
        )
        self.zoom_reset_button.pack(side=tk.LEFT, padx=(0, 4))

        self.zoom_in_button = ttk.Button(
            self.overlay_zoom_wrap,
            text="+",
            style="OverlaySmall.TButton",
            width=3,
            command=self.zoom_in,
        )
        self.zoom_in_button.pack(side=tk.LEFT, padx=(0, 6))

        self.zoom_value_label = tk.Label(
            self.overlay_zoom_wrap,
            text="100%",
            bg=self.overlay_glass_color,
            fg=self.color_text,
            font=("Segoe UI Semibold", 9),
            width=5,
            anchor="e",
        )
        self.zoom_value_label.pack(side=tk.LEFT)

        self.overlay_timeline_row = tk.Frame(self.overlay_content, bg=self.overlay_glass_color)
        self.overlay_timeline_row.pack(fill=tk.X, padx=12, pady=(2, 2))

        self.seekbar_canvas = tk.Canvas(
            self.overlay_timeline_row,
            height=22,
            bg=self.overlay_glass_color,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        self.seekbar_canvas.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.video_time_label = tk.Label(
            self.overlay_timeline_row,
            text="00:00 / 00:00",
            bg=self.overlay_glass_color,
            fg=self.color_muted,
            font=("Segoe UI", 9),
            width=14,
            anchor="e",
        )
        self.video_time_label.pack(side=tk.LEFT, padx=(8, 8))

        self.play_pause_button = ttk.Button(
            self.overlay_timeline_row,
            text="",
            image=self.icon_images.get("play"),
            command=self.play_pause,
            state=tk.DISABLED,
            style="OverlayIcon.TButton",
        )
        self.play_pause_button.pack(side=tk.LEFT, padx=(0, 5))

        self.open_player_button = ttk.Button(
            self.overlay_timeline_row,
            text="",
            image=self.icon_images.get("external"),
            command=self.open_external_player,
            state=tk.DISABLED,
            style="OverlayIcon.TButton",
        )
        self.open_player_button.pack(side=tk.LEFT, padx=(0, 5))

        self.show_in_explorer_button = ttk.Button(
            self.overlay_timeline_row,
            text="",
            image=self.icon_images.get("explorer"),
            command=self.show_image_in_explorer,
            state=tk.DISABLED,
            style="OverlayIcon.TButton",
        )
        self.show_in_explorer_button.pack(side=tk.LEFT)

        self.overlay_resize_handle = tk.Frame(
            self.overlay_panel,
            width=self.overlay_resize_handle_width,
            bg=self.overlay_glass_color,
            bd=0,
            highlightthickness=0,
            cursor="sb_h_double_arrow",
        )
        self.overlay_resize_hint = tk.Frame(
            self.overlay_resize_handle,
            width=self.overlay_resize_visual_width,
            bg=self.overlay_handle_color,
            bd=0,
            highlightthickness=0,
        )
        self.overlay_resize_hint.place(relx=0.5, rely=0.5, anchor="center", relheight=0.72)
        self.overlay_resize_handle.bind("<Enter>", self._on_overlay_resize_enter)
        self.overlay_resize_handle.bind("<Leave>", self._on_overlay_resize_leave)
        self.overlay_resize_handle.bind("<ButtonPress-1>", self._on_overlay_resize_start)
        self.overlay_resize_handle.bind("<B1-Motion>", self._on_overlay_resize_drag)
        self.overlay_resize_handle.bind("<ButtonRelease-1>", self._on_overlay_resize_end)

        self.bottom_panels = tk.PanedWindow(
            self.workspace_pane,
            orient=tk.HORIZONTAL,
            sashwidth=10,
            showhandle=False,
            opaqueresize=False,
            bd=0,
            relief=tk.FLAT,
            bg=self.color_bg,
        )
        self.workspace_pane.add(self.bottom_panels, minsize=self.bottom_panels_min_height)

        self.destination_shell = tk.Frame(
            self.bottom_panels,
            bg=self.color_panel,
            bd=0,
            highlightthickness=1,
            highlightbackground=self.panel_border_color,
        )
        self.bottom_panels.add(self.destination_shell, minsize=self.destination_shell_min_width)

        self.destination_header = tk.Frame(self.destination_shell, bg=self.color_panel)
        self.destination_header.pack(fill=tk.X, padx=12, pady=(10, 6))

        self.destination_title_label = tk.Label(
            self.destination_header,
            text="Quick Destinations",
            bg=self.color_panel,
            fg=self.color_text,
            font=("Segoe UI Semibold", 10),
            anchor="w",
        )
        self.destination_title_label.pack(side=tk.LEFT)

        self.destination_hint_label = tk.Label(
            self.destination_header,
            text="1-0 move | Space play | S stop",
            bg=self.color_panel,
            fg=self.color_muted,
            font=("Segoe UI", 9),
            anchor="e",
        )
        self.destination_hint_label.pack(side=tk.RIGHT)

        self.destination_buttons_frame = tk.Frame(self.destination_shell, bg=self.color_panel)
        self.destination_buttons_frame.pack(fill=tk.X, padx=12, pady=(0, 10))

        self.log_card = tk.Frame(
            self.bottom_panels,
            bg=self.color_panel,
            bd=0,
            highlightthickness=1,
            highlightbackground=self.panel_border_color,
        )
        self.bottom_panels.add(self.log_card, minsize=self.log_card_min_width)

        log_header = tk.Frame(self.log_card, bg=self.color_panel)
        log_header.pack(fill=tk.X, padx=12, pady=(10, 8))

        log_title = tk.Label(
            log_header,
            text="Move Log",
            bg=self.color_panel,
            fg=self.color_text,
            font=("Segoe UI Semibold", 10),
            anchor="w",
        )
        log_title.pack(side=tk.LEFT)

        self.log_hint_label = tk.Label(
            log_header,
            text="Newest first (#1 latest)",
            bg=self.color_panel,
            fg=self.color_muted,
            font=("Segoe UI", 9),
            anchor="w",
        )
        self.log_hint_label.pack(side=tk.LEFT, padx=(10, 0))

        self.log_toggle_button = ttk.Button(
            log_header,
            text="Hide",
            style="Small.TButton",
            command=self._toggle_log_panel,
        )
        self.log_toggle_button.pack(side=tk.RIGHT)

        self.log_body = tk.Frame(self.log_card, bg=self.color_panel)

        logfile_buttons = tk.Frame(self.log_body, bg=self.color_panel)
        logfile_buttons.pack(fill=tk.X, pady=(0, 6))

        self.select_logfile_button = ttk.Button(logfile_buttons, text="Select Log", command=self.select_logfile)
        self.select_logfile_button.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

        self.use_last_logfile_button = ttk.Button(logfile_buttons, text="Use Last", command=self.use_last_logfile)
        self.use_last_logfile_button.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.logfile_name_label = tk.Label(
            self.log_body,
            text="Log file: -",
            bg=self.color_panel,
            fg=self.color_muted,
            font=("Segoe UI", 9),
            anchor="w",
            justify="left",
            wraplength=460,
        )
        self.logfile_name_label.pack(fill=tk.X, pady=(0, 6))

        log_list_frame = tk.Frame(self.log_body, bg=self.color_panel)
        log_list_frame.pack(fill=tk.BOTH, expand=True)

        self.log_listbox = tk.Listbox(
            log_list_frame,
            height=7,
            bg=self.listbox_bg_color,
            fg=self.color_text,
            selectbackground=self.color_accent,
            selectforeground="white",
            activestyle="none",
            relief=tk.FLAT,
            bd=0,
        )
        self.log_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        log_scrollbar = ttk.Scrollbar(log_list_frame, orient=tk.VERTICAL, command=self.log_listbox.yview)
        log_scrollbar.pack(side=tk.LEFT, fill=tk.Y)
        self.log_listbox.config(yscrollcommand=log_scrollbar.set)

        log_actions = tk.Frame(self.log_body, bg=self.color_panel)
        log_actions.pack(fill=tk.X, pady=(8, 0))

        self.log_detail_button = ttk.Button(
            log_actions,
            text="Show Detail + Image",
            command=self._show_selected_log_detail_with_preview,
            state=tk.DISABLED,
        )
        self.log_detail_button.pack(side=tk.LEFT, padx=(0, 4))

        self.log_undo_button = ttk.Button(
            log_actions,
            text="Undo Selected",
            command=self.undo_selected_move,
            state=tk.DISABLED,
        )
        self.log_undo_button.pack(side=tk.LEFT)

        self._set_play_icon()
        self.overlay_controls_collapsed = False
        self.log_panel_collapsed = False
        self._bind_overlay_hover_tracking(self.overlay_panel)
        self.log_body.pack(fill=tk.BOTH, padx=12, pady=(0, 10))
        self._prepare_clickable_controls(self.root)
        self._focus_preview_canvas()
        self._apply_overlay_position_label()
        self._refresh_overlay_header_mode()
        self._apply_bottom_panel_layout()
        self.root.after(80, self._finalize_initial_layout)

    def _toggle_overlay_controls(self):
        self.overlay_controls_collapsed = not self.overlay_controls_collapsed
        if not self.overlay_controls_collapsed:
            self._recalculate_overlay_content_height(apply_if_expanded=False)
        target_height = 0 if self.overlay_controls_collapsed else self.overlay_content_target_height
        self._refresh_overlay_header_mode()
        self._refresh_overlay_width()
        self._animate_overlay_content_height(target_height)

    def _toggle_log_panel(self):
        self.log_panel_collapsed = not self.log_panel_collapsed
        if self.log_panel_collapsed:
            self.log_body.pack_forget()
            self.log_toggle_button.config(text="Show")
        else:
            self.log_body.pack(fill=tk.BOTH, padx=12, pady=(0, 10))
            self.log_toggle_button.config(text="Hide")

    def _apply_bottom_panel_layout(self):
        if not hasattr(self, "bottom_panels"):
            return

        self._ensure_paned_pane(self.workspace_pane, self.bottom_panels, minsize=self.bottom_panels_min_height)
        self._ensure_paned_pane(self.bottom_panels, self.destination_shell, minsize=self.destination_shell_min_width)

        if self.immersive_mode:
            self._forget_paned_pane(self.bottom_panels, self.log_card)
            self.destination_header.pack_configure(padx=10, pady=(6, 4))
            self.destination_buttons_frame.pack_configure(fill=tk.X, padx=10, pady=(0, 6))
            self.destination_title_label.config(text="Quick Move")
            self.destination_hint_label.config(text="1-0 move")
        else:
            restore_default_split = not self._paned_has_pane(self.bottom_panels, self.log_card)
            self._ensure_paned_pane(self.bottom_panels, self.log_card, minsize=self.log_card_min_width)
            self.destination_header.pack_configure(padx=12, pady=(10, 6))
            self.destination_buttons_frame.pack_configure(fill=tk.X, padx=12, pady=(0, 10))
            self.destination_title_label.config(text="Quick Destinations")
            self.destination_hint_label.config(text="1-0 move | Space play | S stop")
            if restore_default_split:
                self.root.after_idle(self._apply_initial_pane_sashes)

    def _finalize_initial_layout(self):
        self._apply_initial_pane_sashes()
        self._finalize_overlay_geometry()

    def _on_overlay_resize_start(self, event):
        self.overlay_resize_active = True
        self.overlay_resize_start_x = event.x_root
        self.overlay_resize_start_width = self.overlay_panel.winfo_width()
        self._refresh_overlay_resize_handle_visual()
        self._focus_preview_canvas()

    def _on_overlay_resize_drag(self, event):
        if not self.overlay_resize_active:
            return

        delta = event.x_root - self.overlay_resize_start_x
        if self.overlay_anchor_mode == "right":
            delta *= -1

        if self.overlay_controls_collapsed:
            min_width = 260
            max_width = max(min_width, min(self.overlay_max_width, self.display_frame.winfo_width() - self.overlay_side_margin * 2))
            self.overlay_compact_width = max(min_width, min(self.overlay_resize_start_width + delta, max_width))
        elif self.overlay_anchor_mode in {"left", "right"}:
            min_width = self.overlay_docked_min_width
            max_width = max(min_width, min(self.overlay_max_width, self.display_frame.winfo_width() - self.overlay_side_margin * 2))
            self.overlay_docked_width = max(min_width, min(self.overlay_resize_start_width + delta, max_width))
        else:
            min_width = self.overlay_min_width
            max_width = max(min_width, min(self.overlay_max_width, self.display_frame.winfo_width() - self.overlay_side_margin * 2))
            self.overlay_expanded_width = max(min_width, min(self.overlay_resize_start_width + delta, max_width))

        if self.resize_after_id:
            try:
                self.root.after_cancel(self.resize_after_id)
            except tk.TclError:
                pass
        self._refresh_overlay_width(rerender_preview=False)
        self.resize_after_id = self.root.after(24, self._refresh_current_canvas)

    def _on_overlay_resize_end(self, event=None):
        self.overlay_resize_active = False
        self.overlay_resize_hover = False
        self._refresh_overlay_resize_handle_visual()

    def _bind_overlay_hover_tracking(self, widget: tk.Widget):
        widget.bind("<Enter>", self._on_overlay_enter, add="+")
        widget.bind("<Leave>", self._on_overlay_leave, add="+")
        for child in widget.winfo_children():
            self._bind_overlay_hover_tracking(child)

    def _finalize_overlay_geometry(self):
        self._recalculate_overlay_content_height(apply_if_expanded=True)
        self.overlay_current_y = self.overlay_visible_y
        self.overlay_panel.place_configure(y=self.overlay_visible_y)
        self._refresh_overlay_width()
        self._update_overlay_hidden_position()

    def _recalculate_overlay_content_height(self, apply_if_expanded: bool = True):
        self.overlay_content.update_idletasks()
        self.overlay_content_target_height = max(1, self.overlay_content.winfo_reqheight())

        if apply_if_expanded and not self.overlay_controls_collapsed:
            self.overlay_content_current_height = self.overlay_content_target_height
            self.overlay_content_host.config(height=self.overlay_content_target_height)

        self._update_overlay_hidden_position()

    def _update_overlay_hidden_position(self):
        panel_h = max(40, self.overlay_panel.winfo_height())
        self.overlay_hidden_y = -panel_h + 36

    def _animate_overlay_content_height(self, target_height: int):
        if self.overlay_content_anim_after_id:
            try:
                self.root.after_cancel(self.overlay_content_anim_after_id)
            except tk.TclError:
                pass
            self.overlay_content_anim_after_id = None

        target = max(0, int(target_height))
        start = int(self.overlay_content_current_height)
        if start == target:
            self.overlay_content_host.config(height=target)
            self._update_overlay_hidden_position()
            return
        started_at = time.perf_counter()
        duration = max(0.08, self.overlay_content_duration_ms / 1000.0)

        def step():
            progress = min(1.0, (time.perf_counter() - started_at) / duration)
            eased = self._ease_out_cubic(progress)
            current = round(start + ((target - start) * eased))
            self.overlay_content_current_height = current
            self.overlay_content_host.config(height=max(0, current))
            self._update_overlay_hidden_position()
            if progress >= 1.0:
                self.overlay_content_anim_after_id = None
                return
            self.overlay_content_anim_after_id = self.root.after(self.overlay_anim_frame_ms, step)

        step()

    def _set_overlay_visibility(self, visible: bool, animate: bool = True):
        self.overlay_visible = visible
        self._update_overlay_hidden_position()
        target_y = self.overlay_visible_y if visible else self.overlay_hidden_y

        if not animate:
            self.overlay_current_y = target_y
            self.overlay_panel.place_configure(y=int(target_y))
            return

        self._animate_overlay_y(target_y)

    def _animate_overlay_y(self, target_y: int):
        if self.overlay_slide_anim_after_id:
            try:
                self.root.after_cancel(self.overlay_slide_anim_after_id)
            except tk.TclError:
                pass
            self.overlay_slide_anim_after_id = None

        target = int(target_y)
        start = int(self.overlay_current_y)
        if start == target:
            self.overlay_panel.place_configure(y=target)
            return
        started_at = time.perf_counter()
        duration = max(0.1, self.overlay_slide_duration_ms / 1000.0)

        def step():
            progress = min(1.0, (time.perf_counter() - started_at) / duration)
            eased = self._ease_out_cubic(progress)
            current = round(start + ((target - start) * eased))
            self.overlay_current_y = current
            self.overlay_panel.place_configure(y=current)
            if progress >= 1.0:
                self.overlay_slide_anim_after_id = None
                return
            self.overlay_slide_anim_after_id = self.root.after(self.overlay_anim_frame_ms, step)

        step()

    def _toggle_immersive_mode(self):
        self.immersive_mode = not self.immersive_mode
        if self.immersive_mode:
            self._hide_shortcut_dropdown()
            if self.app_header.winfo_manager():
                self.app_header.pack_forget()
            self.main_frame.pack_configure(padx=0, pady=0)
            self.display_frame.config(highlightthickness=0)
            self._setup_styles()
            self._apply_bottom_panel_layout()
            self._refresh_destination_buttons()
            self.immersive_toggle_button.config(text="Immersive On")
            self._set_overlay_visibility(True, animate=False)
            self._schedule_overlay_autohide()
            self._set_status("Immersive mode enabled", 1800)
        else:
            self._cancel_overlay_autohide()
            self.main_frame.pack_configure(padx=16, pady=14)
            if not self.app_header.winfo_manager():
                self.app_header.pack(fill=tk.X, pady=(0, 10), before=self.workspace_pane)
            self.display_frame.config(highlightthickness=1, highlightbackground=self.panel_border_color)
            self._setup_styles()
            self._apply_bottom_panel_layout()
            self._refresh_destination_buttons()
            self.immersive_toggle_button.config(text="Immersive Off")
            self._set_overlay_visibility(True, animate=True)
            self._refresh_overlay_width()
            self._set_status("Immersive mode disabled", 1800)

    def _schedule_overlay_autohide(self):
        self._cancel_overlay_autohide()
        if not self.immersive_mode:
            return
        self.overlay_autohide_after_id = self.root.after(self.immersive_idle_ms, self._auto_hide_overlay_if_idle)

    def _cancel_overlay_autohide(self):
        if self.overlay_autohide_after_id:
            self.root.after_cancel(self.overlay_autohide_after_id)
            self.overlay_autohide_after_id = None

    def _auto_hide_overlay_if_idle(self):
        self.overlay_autohide_after_id = None
        if not self.immersive_mode:
            return
        if self.overlay_mouse_inside:
            self._schedule_overlay_autohide()
            return
        self._set_overlay_visibility(False, animate=True)

    def _on_overlay_enter(self, event=None):
        self.overlay_mouse_inside = True
        if self.immersive_mode:
            self._set_overlay_visibility(True, animate=True)
            self._cancel_overlay_autohide()

    def _on_overlay_leave(self, event=None):
        self.overlay_mouse_inside = False
        if self.immersive_mode:
            self._schedule_overlay_autohide()

    def _on_user_activity(self, event=None):
        if not self.immersive_mode:
            return
        if event is not None and getattr(event, "keysym", ""):
            return
        self._set_overlay_visibility(True, animate=True)
        self._schedule_overlay_autohide()

    def _build_left_panel(self):
        header_frame = tk.Frame(self.left_frame, bg=self.color_panel)
        header_frame.pack(fill=tk.X, padx=14, pady=(14, 8))

        title_label = tk.Label(
            header_frame,
            text="Fair Sorting",
            font=("Segoe UI Semibold", 18),
            fg=self.color_text,
            bg=self.color_panel,
            anchor="w",
        )
        title_label.pack(fill=tk.X)

        subtitle_label = tk.Label(
            header_frame,
            text="Modern media sorter for image, video, and audio",
            font=("Segoe UI", 9),
            fg=self.color_muted,
            bg=self.color_panel,
            anchor="w",
        )
        subtitle_label.pack(fill=tk.X, pady=(2, 0))

        controls_frame = tk.Frame(self.left_frame, bg=self.color_panel)
        controls_frame.pack(fill=tk.X, padx=14, pady=8)

        self.load_button = ttk.Button(
            controls_frame,
            text="Load Folder",
            command=self.load_folder,
            style="Primary.TButton",
        )
        self.load_button.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        self.set_destination_button = ttk.Button(
            controls_frame,
            text="Add Destination",
            command=self.set_destination,
        )
        self.set_destination_button.pack(side=tk.LEFT, fill=tk.X, expand=True)

        path_title = tk.Label(
            self.left_frame,
            text="Loaded Folder",
            bg=self.color_panel,
            fg=self.color_muted,
            font=("Segoe UI", 9),
            anchor="w",
        )
        path_title.pack(fill=tk.X, padx=14, pady=(6, 0))

        self.loaded_path_label = tk.Label(
            self.left_frame,
            text="-",
            bg=self.color_panel,
            fg=self.color_text,
            font=("Segoe UI", 9),
            wraplength=314,
            justify="left",
            anchor="w",
        )
        self.loaded_path_label.pack(fill=tk.X, padx=14, pady=(2, 8))

        self.file_name_label = tk.Label(
            self.left_frame,
            text="File Name\n-",
            bg=self.color_panel,
            fg=self.color_text,
            font=("Segoe UI Semibold", 10),
            wraplength=314,
            justify="left",
            anchor="w",
        )
        self.file_name_label.pack(fill=tk.X, padx=14, pady=(2, 4))

        self.file_info_label = tk.Label(
            self.left_frame,
            text="",
            bg=self.color_panel,
            fg=self.color_muted,
            font=("Segoe UI", 9),
            wraplength=314,
            justify="left",
            anchor="w",
        )
        self.file_info_label.pack(fill=tk.X, padx=14, pady=(0, 8))

        self.message_label = tk.Label(
            self.left_frame,
            text="",
            bg=self.color_panel,
            fg=self.color_danger,
            font=("Segoe UI", 9),
            wraplength=314,
            justify="left",
            anchor="w",
        )
        self.message_label.pack(fill=tk.X, padx=14, pady=(0, 8))

        self.undo_button = ttk.Button(
            self.left_frame,
            text="Undo Last Move",
            command=self.undo_move,
            state=tk.DISABLED,
        )
        self.undo_button.pack(fill=tk.X, padx=14, pady=(0, 12))

        self.destination_card = tk.LabelFrame(
            self.left_frame,
            text="Destinations",
            bg=self.color_panel,
            fg=self.color_text,
            font=("Segoe UI Semibold", 10),
            padx=10,
            pady=8,
            bd=1,
            relief=tk.SOLID,
        )
        self.destination_card.pack(fill=tk.X, padx=14, pady=(0, 10))

        self.destination_buttons_frame = tk.Frame(self.destination_card, bg=self.color_panel)
        self.destination_buttons_frame.pack(fill=tk.X)

        self.log_card = tk.LabelFrame(
            self.left_frame,
            text="Move Log",
            bg=self.color_panel,
            fg=self.color_text,
            font=("Segoe UI Semibold", 10),
            padx=10,
            pady=8,
            bd=1,
            relief=tk.SOLID,
        )
        self.log_card.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 14))

        logfile_buttons = tk.Frame(self.log_card, bg=self.color_panel)
        logfile_buttons.pack(fill=tk.X, pady=(0, 6))

        self.select_logfile_button = ttk.Button(logfile_buttons, text="Select Log", command=self.select_logfile)
        self.select_logfile_button.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

        self.use_last_logfile_button = ttk.Button(logfile_buttons, text="Use Last", command=self.use_last_logfile)
        self.use_last_logfile_button.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.logfile_name_label = tk.Label(
            self.log_card,
            text="Log file: -",
            bg=self.color_panel,
            fg=self.color_muted,
            font=("Segoe UI", 9),
            anchor="w",
            justify="left",
            wraplength=300,
        )
        self.logfile_name_label.pack(fill=tk.X, pady=(0, 6))

        log_list_frame = tk.Frame(self.log_card, bg=self.color_panel)
        log_list_frame.pack(fill=tk.BOTH, expand=True)

        self.log_listbox = tk.Listbox(
            log_list_frame,
            height=10,
            bg="#F8FBFF",
            fg=self.color_text,
            selectbackground=self.color_accent,
            selectforeground="white",
            activestyle="none",
            relief=tk.SOLID,
            bd=1,
        )
        self.log_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        log_scrollbar = ttk.Scrollbar(log_list_frame, orient=tk.VERTICAL, command=self.log_listbox.yview)
        log_scrollbar.pack(side=tk.LEFT, fill=tk.Y)
        self.log_listbox.config(yscrollcommand=log_scrollbar.set)

        self.log_undo_button = ttk.Button(
            self.log_card,
            text="Undo Selected",
            command=self.undo_selected_move,
            state=tk.DISABLED,
        )
        self.log_undo_button.pack(fill=tk.X, pady=(8, 0))

    def _build_right_panel(self):
        viewer_header = tk.Frame(self.right_frame, bg=self.color_panel)
        viewer_header.pack(fill=tk.X, padx=14, pady=(14, 8))

        title_wrap = tk.Frame(viewer_header, bg=self.color_panel)
        title_wrap.pack(side=tk.LEFT)

        viewer_title = tk.Label(
            title_wrap,
            text="Preview Gallery",
            font=("Segoe UI Semibold", 12),
            bg=self.color_panel,
            fg=self.color_text,
            anchor="w",
        )
        viewer_title.pack(side=tk.LEFT)

        self.media_badge_label = tk.Label(
            title_wrap,
            text="IDLE",
            bg="#D8E3F2",
            fg="#2A3B4F",
            font=("Segoe UI Semibold", 8),
            padx=8,
            pady=2,
        )
        self.media_badge_label.pack(side=tk.LEFT, padx=(8, 0))

        zoom_wrap = tk.Frame(viewer_header, bg=self.color_panel)
        zoom_wrap.pack(side=tk.RIGHT)

        self.zoom_out_button = ttk.Button(zoom_wrap, text="-", style="Small.TButton", width=3, command=self.zoom_out)
        self.zoom_out_button.pack(side=tk.LEFT, padx=(0, 4))

        self.zoom_reset_button = ttk.Button(
            zoom_wrap,
            text="Reset",
            style="Small.TButton",
            command=self.reset_zoom,
        )
        self.zoom_reset_button.pack(side=tk.LEFT, padx=(0, 4))

        self.zoom_in_button = ttk.Button(zoom_wrap, text="+", style="Small.TButton", width=3, command=self.zoom_in)
        self.zoom_in_button.pack(side=tk.LEFT, padx=(0, 6))

        self.zoom_value_label = tk.Label(
            zoom_wrap,
            text="100%",
            bg=self.color_panel,
            fg=self.color_text,
            font=("Segoe UI Semibold", 9),
            width=5,
            anchor="e",
        )
        self.zoom_value_label.pack(side=tk.LEFT)

        hint_label = tk.Label(
            self.right_frame,
            text="Wheel: zoom  |  Drag: pan when zoomed  |  Arrows: navigate",
            font=("Segoe UI", 9),
            bg=self.color_panel,
            fg=self.color_muted,
            anchor="w",
        )
        hint_label.pack(fill=tk.X, padx=14)

        self.display_frame = tk.Frame(
            self.right_frame,
            bg=self.color_display,
            highlightthickness=1,
            highlightbackground="#D6E2F0",
        )
        self.display_frame.pack(fill=tk.BOTH, expand=True, padx=14, pady=(6, 0))

        self.preview_canvas = tk.Canvas(
            self.display_frame,
            bg=self.color_display,
            highlightthickness=0,
            bd=0,
        )
        self.preview_canvas.pack(fill=tk.BOTH, expand=True)

        controls_frame = tk.Frame(self.right_frame, bg=self.color_panel)
        controls_frame.pack(fill=tk.X, padx=14, pady=12)

        nav_frame = tk.Frame(controls_frame, bg=self.color_panel)
        nav_frame.pack(fill=tk.X)

        self.prev_button = ttk.Button(nav_frame, text="Previous", command=self.previous_file)
        self.prev_button.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        self.index_label = tk.Label(
            nav_frame,
            text="0 / 0",
            bg=self.color_panel,
            fg=self.color_text,
            font=("Segoe UI Semibold", 10),
            width=12,
        )
        self.index_label.pack(side=tk.LEFT)

        self.next_button = ttk.Button(nav_frame, text="Next", command=self.next_file)
        self.next_button.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))

        media_controls = tk.Frame(controls_frame, bg=self.color_panel)
        media_controls.pack(fill=tk.X, pady=(8, 0))

        self.open_player_button = ttk.Button(
            media_controls,
            text="Open External Player",
            command=self.open_external_player,
            state=tk.DISABLED,
        )
        self.open_player_button.pack(side=tk.RIGHT)

        self.play_pause_button = ttk.Button(
            media_controls,
            text="Play",
            command=self.play_pause,
            state=tk.DISABLED,
        )
        self.play_pause_button.pack(side=tk.RIGHT, padx=(0, 6))

        timeline_frame = tk.Frame(controls_frame, bg=self.color_panel)
        timeline_frame.pack(fill=tk.X, pady=(8, 0))

        self.video_progress = ttk.Progressbar(timeline_frame, orient=tk.HORIZONTAL, mode="determinate", maximum=100)
        self.video_progress.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.video_time_label = tk.Label(
            timeline_frame,
            text="00:00 / 00:00",
            bg=self.color_panel,
            fg=self.color_muted,
            font=("Segoe UI", 9),
            width=14,
            anchor="e",
        )
        self.video_time_label.pack(side=tk.LEFT, padx=(8, 0))

    def _create_context_menus(self):
        self.image_context_menu = tk.Menu(self.root, tearoff=0)
        self.image_context_menu.add_command(label="Show in Explorer", command=self.show_image_in_explorer)

        self.log_context_menu = tk.Menu(self.root, tearoff=0)
        self.log_context_menu.add_command(label="Show Detail + Preview", command=self._show_selected_log_detail_with_preview)

    def _bind_events(self):
        self._bind_shortcuts()
        self.root.bind("<Button-1>", self._handle_global_click, add="+")

        self.preview_canvas.bind("<MouseWheel>", self.zoom)
        self.preview_canvas.bind("<Button-4>", self.zoom)
        self.preview_canvas.bind("<Button-5>", self.zoom)
        self.preview_canvas.bind("<Button-1>", self._on_canvas_press)
        self.preview_canvas.bind("<B1-Motion>", self._on_canvas_drag)
        self.preview_canvas.bind("<Double-Button-1>", lambda event: self.reset_zoom())
        self.preview_canvas.bind("<Button-3>", self._show_image_context_menu)
        self.preview_canvas.bind("<Configure>", self._on_preview_resize)
        self.preview_canvas.bind("<Motion>", self._on_user_activity, add="+")
        self.preview_canvas.bind("<Button-1>", self._on_user_activity, add="+")
        self.preview_canvas.bind("<MouseWheel>", self._on_user_activity, add="+")
        self.seekbar_canvas.bind("<Configure>", self._on_seekbar_configure)
        self.seekbar_canvas.bind("<Button-1>", self._on_seekbar_click)

        self.log_listbox.bind("<<ListboxSelect>>", self.on_log_select)
        self.log_listbox.bind("<Button-3>", self.show_log_context_menu)

    def _handle_escape(self, event=None):
        if self.shortcut_dropdown_visible:
            self._hide_shortcut_dropdown()
            return
        if self.immersive_mode:
            self._toggle_immersive_mode()
            return
        if not self.overlay_visible:
            self._set_overlay_visibility(True, animate=True)

    def stop_playback(self, event=None):
        if not self.current_file_path:
            return

        if self.current_media_kind == "video":
            self._pause_video()
            self.playback_current_position_sec = 0.0
            self._render_video_frame_at(0.0, force=True)
            self._set_video_progress(0.0, self.current_media_duration_sec)
            self._set_play_icon()
            self._set_status("Video stopped", 1400)
            return

        if self.current_media_kind == "audio":
            self._stop_audio()
            self.playback_current_position_sec = 0.0
            self._set_video_progress(0.0, self.current_media_duration_sec)
            self._render_audio_indicator(os.path.basename(self.current_file_path), paused=True)
            self._set_play_icon()
            self._set_status("Audio stopped", 1400)

    def _set_status(self, text: str, timeout_ms: int = 0):
        if self.status_chip_reset_after_id:
            try:
                self.root.after_cancel(self.status_chip_reset_after_id)
            except tk.TclError:
                pass
            self.status_chip_reset_after_id = None

        level = self._status_level_for_text(text)
        palette = self._theme_palette()
        self.status_chip.config(
            text=text,
            bg=palette["status_bg"] if level != "error" else palette["toast_error_bg"],
            fg=palette["status_fg"] if level != "error" else palette["toast_error_fg"],
        )

        lowered = text.lower()
        if level == "error" and not lowered.startswith("moved"):
            self._show_toast(text, level=level, timeout_ms=max(3200, timeout_ms or 0))

        if timeout_ms > 0 and text != "Ready":
            self.status_chip_reset_after_id = self.root.after(timeout_ms, lambda: self._set_status("Ready"))

    def _refresh_web_controls(self):
        if not hasattr(self, "web_toggle_button"):
            return

        if self.web_server_state == "running":
            toggle_text = "Stop Web"
            if self.web_server_lan_url:
                status_text = f"LAN {self.web_server_lan_url}"
            else:
                status_text = self.web_server_url or "Web ready"
            status_fg = self.color_accent
            open_state = tk.NORMAL if self.web_server_url else tk.DISABLED
        elif self.web_server_state == "starting":
            toggle_text = "Stop Web"
            status_text = self.web_server_error or "Starting web..."
            status_fg = self.color_muted
            open_state = tk.DISABLED
        elif self.web_server_state == "error":
            toggle_text = "Run Web"
            status_text = self.web_server_error or "Web error"
            status_fg = self.color_danger
            open_state = tk.NORMAL if self.web_server_url else tk.DISABLED
        else:
            toggle_text = "Run Web"
            status_text = "Web off"
            status_fg = self.color_muted
            open_state = tk.DISABLED

        self.web_toggle_button.config(text=toggle_text)
        self.web_open_button.config(state=open_state)
        self.web_status_label.config(text=status_text, fg=status_fg, bg=self.color_bg)
        self.web_controls_frame.config(bg=self.color_bg)

    def _set_web_server_state(self, state: str, text: str = "", url: str = ""):
        self.web_server_state = state
        self.web_server_error = text if state in {"starting", "error"} else ""
        if url:
            self.web_server_url = url
        elif state in {"stopped", "error"}:
            self.web_server_url = ""
            self.web_server_lan_url = ""
        self.web_server_ready = state == "running"
        self._refresh_web_controls()

    @staticmethod
    def _is_port_available(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe_socket:
            try:
                probe_socket.bind(("0.0.0.0", port))
            except OSError:
                return False
        return True

    @staticmethod
    def _detect_local_ip() -> str | None:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe_socket:
                probe_socket.connect(("8.8.8.8", 80))
                address = probe_socket.getsockname()[0]
                if address and not address.startswith("127."):
                    return address
        except OSError:
            pass

        try:
            addresses = socket.gethostbyname_ex(socket.gethostname())[2]
        except OSError:
            return None

        for address in addresses:
            if address and not address.startswith("127."):
                return address
        return None

    def _build_lan_url(self, port: int) -> str:
        local_ip = self._detect_local_ip()
        if not local_ip:
            return ""
        return f"http://{local_ip}:{port}"

    @staticmethod
    def _normalize_existing_dir(path: str | None) -> str:
        if not path:
            return ""
        expanded = os.path.abspath(os.path.expanduser(path))
        return expanded if os.path.isdir(expanded) else ""

    def _win_hidden_process_kwargs(self) -> dict[str, object]:
        if os.name != "nt":
            return {}

        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        return {
            "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
            "startupinfo": startupinfo,
        }

    def _expected_web_server_markers(self) -> tuple[str, ...]:
        markers = ["web_sorter"]
        builder_script = os.path.normcase(os.path.join(self.script_dir, "web_sorter.py"))
        if builder_script:
            markers.append(builder_script)
        helper_exe = os.path.normcase(os.path.join(self.script_dir, "web_sorter.exe"))
        if helper_exe:
            markers.append(helper_exe)
        return tuple(markers)

    def _cleanup_orphaned_web_server_processes(self):
        if psutil is None:
            return

        markers = tuple(marker.lower() for marker in self._expected_web_server_markers())
        current_pid = os.getpid()
        for process in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
            try:
                pid = int(process.info["pid"])
            except Exception:
                continue
            if pid == current_pid:
                continue

            try:
                name = (process.info.get("name") or "").lower()
            except Exception:
                name = ""
            try:
                exe = os.path.normcase(process.info.get("exe") or "").lower()
            except Exception:
                exe = ""
            try:
                cmdline = " ".join(process.info.get("cmdline") or []).lower()
            except Exception:
                cmdline = ""

            if not any(marker in name or marker in exe or marker in cmdline for marker in markers):
                continue
            self._terminate_pid_tree(pid, self._expected_web_server_markers())

    def _read_web_server_runtime_record(self) -> dict[str, object] | None:
        if not os.path.exists(self.web_server_runtime_file):
            return None
        try:
            with open(self.web_server_runtime_file, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def _write_web_server_runtime_record(self, process: subprocess.Popen, launch_command: list[str], port: int):
        record: dict[str, object] = {
            "pid": process.pid,
            "port": port,
            "command": launch_command,
        }
        if psutil is not None:
            try:
                record["create_time"] = psutil.Process(process.pid).create_time()
            except Exception:
                pass
        try:
            with open(self.web_server_runtime_file, "w", encoding="utf-8") as handle:
                json.dump(record, handle)
        except OSError:
            pass

    def _clear_web_server_runtime_record(self):
        try:
            os.remove(self.web_server_runtime_file)
        except FileNotFoundError:
            return
        except OSError:
            return

    def _terminate_pid_tree(self, pid: int, expected_markers: tuple[str, ...] | None = None):
        markers = tuple(marker for marker in (expected_markers or ()) if marker)
        if psutil is not None:
            try:
                process = psutil.Process(pid)
            except psutil.NoSuchProcess:
                return
            except Exception:
                process = None

            if process is not None:
                try:
                    cmdline = " ".join(process.cmdline()).lower()
                except Exception:
                    cmdline = ""
                try:
                    name = process.name().lower()
                except Exception:
                    name = ""

                if markers and not any(marker.lower() in cmdline or marker.lower() in name for marker in markers):
                    return

                try:
                    descendants = process.children(recursive=True)
                except Exception:
                    descendants = []
                for child in descendants:
                    try:
                        child.terminate()
                    except Exception:
                        continue
                try:
                    process.terminate()
                except Exception:
                    pass
                _, alive = psutil.wait_procs([*descendants, process], timeout=3)
                for leftover in alive:
                    try:
                        leftover.kill()
                    except Exception:
                        continue
                psutil.wait_procs(alive, timeout=2)
                return

        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    **self._win_hidden_process_kwargs(),
                )
            except Exception:
                return
            return

        try:
            os.kill(pid, 15)
        except OSError:
            return

    def _cleanup_stale_web_server(self):
        record = self._read_web_server_runtime_record()
        if not record:
            return

        pid = record.get("pid")
        if not isinstance(pid, int):
            self._clear_web_server_runtime_record()
            return

        if psutil is not None:
            try:
                process = psutil.Process(pid)
            except psutil.NoSuchProcess:
                self._clear_web_server_runtime_record()
                return
            except Exception:
                process = None

            if process is not None:
                recorded_create_time = record.get("create_time")
                if isinstance(recorded_create_time, (float, int)):
                    try:
                        if abs(process.create_time() - float(recorded_create_time)) > 2:
                            self._clear_web_server_runtime_record()
                            return
                    except Exception:
                        pass

        self._terminate_pid_tree(pid, self._expected_web_server_markers())
        self._cleanup_orphaned_web_server_processes()
        self._clear_web_server_runtime_record()

    def _resolve_web_server_command(self) -> list[str] | None:
        if getattr(sys, "frozen", False):
            helper_names = ["web_sorter.exe"] if os.name == "nt" else ["web_sorter"]
            for root in (self.script_dir, self.assets_dir):
                for helper_name in helper_names:
                    candidate = os.path.join(root, helper_name)
                    if os.path.isfile(candidate):
                        return [candidate]
            return None

        if importlib.util.find_spec("streamlit") is None:
            return None
        return [sys.executable, os.path.join(self.script_dir, "web_sorter.py")]

    def _prompt_web_server_port(self) -> int | None:
        return simpledialog.askinteger(
            "Run Web",
            "Port for Fair Sorting Web:",
            parent=self.root,
            initialvalue=self.web_server_port or 8501,
            minvalue=1,
            maxvalue=65535,
        )

    def _toggle_web_server(self):
        if self.web_server_state in {"starting", "running"}:
            self._stop_web_server()
            return
        self._start_web_server()

    def _start_web_server(self):
        port = self._prompt_web_server_port()
        if port is None:
            return
        self._cleanup_stale_web_server()
        if not self._is_port_available(port):
            self._set_web_server_state("error", f"Port {port} is already in use")
            self._set_status(f"Port {port} is already in use", 3200)
            return

        self.web_server_port = port
        self.web_server_url = f"http://127.0.0.1:{port}"
        self.web_server_lan_url = self._build_lan_url(port)
        self.web_server_logs = []
        self.web_server_browser_opened = False
        self._stop_web_server(clear_status=False)
        launch_command = self._resolve_web_server_command()
        if launch_command is None:
            self._set_web_server_state("error", "Streamlit runtime not available")
            self._set_status("Streamlit runtime not available", 3200)
            return

        self._save_config()
        self._launch_web_server_process(launch_command, port)

    def _launch_web_server_process(self, launch_command: list[str], port: int):
        self._stop_web_server(clear_status=False)
        self.web_server_python = launch_command[0]
        self.web_server_port = port
        self.web_server_url = f"http://127.0.0.1:{port}"
        self.web_server_lan_url = self._build_lan_url(port)
        self.web_server_logs = []

        environment = os.environ.copy()
        environment["FAIR_SORTING_HOME"] = self.app_home
        environment["FAIR_SORTING_PORT"] = str(port)
        environment["FAIR_SORTING_BIND_ADDRESS"] = self.web_server_bind_address
        environment["FAIR_SORTING_LOGFILE"] = self.logfile
        try:
            self.web_server_process = subprocess.Popen(
                launch_command,
                cwd=self.app_home,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=environment,
                **self._win_hidden_process_kwargs(),
            )
        except OSError as exc:
            self._set_web_server_state("error", f"Unable to start web server: {exc}")
            return

        self._write_web_server_runtime_record(self.web_server_process, launch_command, port)
        self._set_web_server_state("starting", f"Starting web on {self.web_server_url}", url=self.web_server_url)
        if self.web_server_process.stdout is not None:
            self.web_server_log_thread = threading.Thread(
                target=self._consume_web_server_logs,
                args=(self.web_server_process,),
                daemon=True,
            )
            self.web_server_log_thread.start()
        self._poll_web_server_state()

    def _consume_web_server_logs(self, process: subprocess.Popen):
        stream = process.stdout
        if stream is None:
            return
        try:
            for line in stream:
                cleaned = line.strip()
                if not cleaned:
                    continue
                self.web_server_logs.append(cleaned)
                if len(self.web_server_logs) > 60:
                    self.web_server_logs = self.web_server_logs[-60:]
        except Exception:
            return

    def _probe_web_server_ready(self) -> bool:
        if not self.web_server_url:
            return False
        try:
            with urllib.request.urlopen(self.web_server_url, timeout=0.6) as response:
                return 200 <= getattr(response, "status", 200) < 500
        except (urllib.error.URLError, TimeoutError, OSError):
            return False

    def _poll_web_server_state(self):
        process = self.web_server_process
        if process is None:
            return
        if self._probe_web_server_ready():
            self.web_server_poll_after_id = None
            self._set_web_server_state("running", self.web_server_url, url=self.web_server_url)
            if not self.web_server_browser_opened:
                self.web_server_browser_opened = True
                self._open_web_server_url()
            status_text = f"Web ready: {self.web_server_url}"
            if self.web_server_lan_url:
                status_text = f"{status_text} | LAN {self.web_server_lan_url}"
            self._set_status(status_text, 3600)
            return

        return_code = process.poll()
        if return_code is not None:
            self.web_server_poll_after_id = None
            last_log = self.web_server_logs[-1] if self.web_server_logs else f"Web server exited with code {return_code}"
            self.web_server_process = None
            self._clear_web_server_runtime_record()
            self._set_web_server_state("error", last_log)
            self._set_status("Web server failed to start", 3200)
            return

        self.web_server_poll_after_id = self.root.after(
            self.WEBSERVER_POLL_INTERVAL_MS,
            self._poll_web_server_state,
        )

    def _stop_web_server(self, clear_status: bool = True):
        if self.web_server_poll_after_id:
            try:
                self.root.after_cancel(self.web_server_poll_after_id)
            except tk.TclError:
                pass
            self.web_server_poll_after_id = None

        process = self.web_server_process
        self.web_server_process = None
        if process is not None:
            try:
                self._terminate_pid_tree(process.pid, self._expected_web_server_markers())
                process.wait(timeout=3)
            except Exception:
                pass

        self._cleanup_orphaned_web_server_processes()
        self._clear_web_server_runtime_record()

        self.web_server_ready = False
        self.web_server_browser_opened = False
        if clear_status:
            self._set_web_server_state("stopped")
            self._set_status("Web server stopped", 1800)
        else:
            self._set_web_server_state("stopped")

    def _open_web_server_url(self):
        if not self.web_server_url:
            return
        try:
            webbrowser.open(self.web_server_url)
        except Exception:
            self._set_status("Unable to open web browser", 2500)

    def _set_media_badge(self, media_kind: str):
        palette = (
            {
                "image": ("IMAGE", "#1F4E35", "#C8F2D8"),
                "video": ("VIDEO", "#5B421B", "#FFD79C"),
                "audio": ("AUDIO", "#1F3957", "#CDE3FF"),
                "none": ("IDLE", "#273447", "#D5E1F0"),
                "unknown": ("UNKNOWN", "#303A46", "#D8E0EA"),
            }
            if self.theme_mode == "dark"
            else {
                "image": ("IMAGE", "#DDF8E7", "#11663B"),
                "video": ("VIDEO", "#FFEFC9", "#8A5108"),
                "audio": ("AUDIO", "#DDEAFE", "#1D4BA6"),
                "none": ("IDLE", "#D8E4F3", "#21405F"),
                "unknown": ("UNKNOWN", "#E4EAF2", "#334A67"),
            }
        )
        label, bg, fg = palette.get(media_kind, palette["unknown"])
        self.media_badge_label.config(text=label, bg=bg, fg=fg)

    def _preferred_log_directory(self, folder_path: str | None = None) -> str:
        for candidate in (
            folder_path,
            self.loaded_folder_path,
            self.last_folder_path,
        ):
            normalized = self._normalize_existing_dir(candidate)
            if normalized:
                return normalized
        return self.output_dir

    def _create_default_logfile(self, folder_path: str | None = None) -> str:
        log_dir = self._preferred_log_directory(folder_path)
        os.makedirs(log_dir, exist_ok=True)
        logfile_name = f"move_log_{datetime.now().strftime('%Y%m%d%H%M%S')}.txt"
        return os.path.join(log_dir, logfile_name)

    def _discover_logfiles(self, folder_path: str | None = None) -> list[str]:
        candidates: list[str] = []
        seen_dirs: set[str] = set()
        for base_dir in (
            self._preferred_log_directory(folder_path),
            self.loaded_folder_path,
            self.last_folder_path,
            self.app_home,
            self.output_dir,
        ):
            normalized_dir = self._normalize_existing_dir(base_dir)
            if not normalized_dir:
                continue
            casefold_dir = os.path.normcase(normalized_dir)
            if casefold_dir in seen_dirs:
                continue
            seen_dirs.add(casefold_dir)
            try:
                for filename in os.listdir(normalized_dir):
                    if filename.startswith("move_log") and filename.endswith(".txt"):
                        candidates.append(os.path.join(normalized_dir, filename))
            except OSError:
                continue
        return sorted(set(candidates), key=os.path.getctime, reverse=True)

    def _resolve_logfile_for_folder(self, folder_path: str | None = None) -> str:
        preferred_dir = os.path.normcase(self._preferred_log_directory(folder_path))
        for logfile in self._discover_logfiles(folder_path):
            if os.path.normcase(os.path.dirname(logfile)) == preferred_dir:
                return logfile
        return self._create_default_logfile(folder_path)

    def _load_config(self) -> tuple[str, list[str]]:
        if not os.path.exists(self.config_file):
            return os.path.expanduser("~"), []

        try:
            with open(self.config_file, "r", encoding="utf-8") as config:
                lines = [line.strip() for line in config.readlines() if line.strip()]
        except OSError:
            return os.path.expanduser("~"), []

        if not lines:
            return os.path.expanduser("~"), []

        last_folder = lines[0]
        destinations: list[str] = []
        for path in lines[1:]:
            if path not in destinations and os.path.isdir(path):
                destinations.append(path)

        return last_folder, destinations

    def _save_config(self):
        try:
            with open(self.config_file, "w", encoding="utf-8") as config:
                config.write(f"{self.last_folder_path}\n")
                for destination in self.destination_folders:
                    config.write(f"{destination}\n")
        except OSError:
            self._set_status("Unable to save config", 3000)

    def _update_logfile_for_folder(self, folder_path: str):
        self.logfile = self._resolve_logfile_for_folder(folder_path)
        self._load_log_entries()
        self._refresh_log_list()
        self._update_logfile_label()

    def _load_log_entries(self):
        self.log_entries = []
        if not os.path.exists(self.logfile):
            return

        try:
            with open(self.logfile, "r", encoding="utf-8") as log_file:
                for line in log_file:
                    record = self._parse_log_line(line)
                    if record:
                        self.log_entries.append(record)
        except OSError:
            self._set_status("Unable to read logfile", 3000)

    def _write_log_entries(self):
        try:
            with open(self.logfile, "w", encoding="utf-8") as log_file:
                for record in self.log_entries:
                    log_file.write(f"{record.source_path} -> {record.destination_path}\n")
        except OSError:
            self._set_status("Unable to write logfile", 3000)

    @staticmethod
    def _parse_log_line(line: str) -> MoveRecord | None:
        parts = line.strip().split(" -> ", 1)
        if len(parts) != 2:
            return None
        return MoveRecord(parts[0].strip(), parts[1].strip())

    def _update_logfile_label(self):
        self.logfile_name_label.config(text=f"Log file: {os.path.basename(self.logfile)}")

    def _display_log_index_to_record_index(self, display_index: int) -> int | None:
        if 0 <= display_index < len(self.log_display_to_record_indices):
            return self.log_display_to_record_indices[display_index]
        return None

    def _selected_log_record(self) -> tuple[int | None, MoveRecord | None]:
        selection = self.log_listbox.curselection()
        if not selection:
            return None, None
        record_index = self._display_log_index_to_record_index(selection[0])
        if record_index is None or not (0 <= record_index < len(self.log_entries)):
            return None, None
        return record_index, self.log_entries[record_index]

    def _refresh_log_list(self):
        self.log_listbox.delete(0, tk.END)
        self.log_display_to_record_indices = list(range(len(self.log_entries) - 1, -1, -1))
        for display_index, record_index in enumerate(self.log_display_to_record_indices, start=1):
            record = self.log_entries[record_index]
            src_name = os.path.basename(record.source_path)
            dst_folder = os.path.basename(os.path.dirname(record.destination_path))
            if not dst_folder:
                dst_folder = os.path.basename(record.destination_path) or "-"
            self.log_listbox.insert(tk.END, f"#{display_index}  {src_name} -> {dst_folder}")
        self.on_log_select(None)

    def choose_file_message(self):
        self._cancel_pending_video_prepare()
        self.current_file_path = None
        self.current_media_kind = "none"
        self.current_pil_image = None
        self.current_media_resolution = None
        self.current_media_duration_sec = 0.0
        self.current_media_fps = 0.0
        self.playback_current_position_sec = 0.0
        self.current_preview_audio_path = None
        self._reset_current_video_state()
        self._set_media_badge("none")

        self.index_label.config(text="0 / 0")
        self.file_name_label.config(text="File Name\n-")
        self.file_info_label.config(text="")
        self.loaded_path_label.config(text=self.loaded_folder_path or "-")
        self._set_inline_message("")
        self.play_pause_button.config(state=tk.DISABLED)
        self._set_play_icon()
        self.open_player_button.config(state=tk.DISABLED)
        self.show_in_explorer_button.config(state=tk.DISABLED)
        self._set_video_progress(0.0, 0.0)
        self._update_zoom_label()
        self._render_placeholder(
            "Choose a folder to start sorting",
            "Images, videos, and audio are supported.",
        )
        self._recalculate_overlay_content_height()

    def _scan_supported_files(self, folder_path: str) -> list[str]:
        files: list[str] = []
        try:
            names = sorted(os.listdir(folder_path), key=str.lower)
        except OSError:
            return files

        for name in names:
            full_path = os.path.join(folder_path, name)
            if os.path.isfile(full_path) and name.lower().endswith(SUPPORTED_EXTENSIONS):
                files.append(full_path)

        return files

    def load_folder(self, event=None):
        initial_dir = self.last_folder_path if os.path.isdir(self.last_folder_path) else os.path.expanduser("~")
        folder_path = filedialog.askdirectory(initialdir=initial_dir)
        if folder_path:
            self.load_folder_from_path(folder_path, persist=True)

    def _count_media_types(self, paths: list[str]) -> tuple[int, int, int]:
        image_count = 0
        video_count = 0
        audio_count = 0
        for path in paths:
            kind = self._media_kind(path)
            if kind == "image":
                image_count += 1
            elif kind == "video":
                video_count += 1
            elif kind == "audio":
                audio_count += 1
        return image_count, video_count, audio_count

    def load_folder_from_path(self, folder_path: str, persist: bool = True):
        if not os.path.isdir(folder_path):
            self._set_status("Folder not found", 3000)
            return

        self._stop_media()
        self.file_paths = self._scan_supported_files(folder_path)
        self.current_index = 0
        self.zoom_level = 100
        self.pan_x = 0
        self.pan_y = 0
        self.loaded_folder_path = folder_path
        self.loaded_path_label.config(text=folder_path)
        self._update_logfile_for_folder(folder_path)

        if persist:
            self.last_folder_path = folder_path
            self._save_config()

        if not self.file_paths:
            self._show_empty_state("No files with supported extensions found in this folder")
            self._set_status("No media files found", 3000)
            return

        image_count, video_count, audio_count = self._count_media_types(self.file_paths)
        self.show_current_file()
        self._set_status(
            f"Loaded {len(self.file_paths)} files  |  image: {image_count}, video: {video_count}, audio: {audio_count}",
            3200,
        )

    def _show_empty_state(self, message: str):
        self._cancel_pending_video_prepare()
        self.current_file_path = None
        self.current_media_kind = "none"
        self.current_pil_image = None
        self.current_media_resolution = None
        self.current_media_duration_sec = 0.0
        self.current_media_fps = 0.0
        self.playback_current_position_sec = 0.0
        self.current_preview_audio_path = None
        self._reset_current_video_state()

        self._set_media_badge("none")
        self.index_label.config(text="0 / 0")
        self.file_name_label.config(text="File Name\n-")
        self.file_info_label.config(text="")
        self._set_inline_message("")
        self.play_pause_button.config(state=tk.DISABLED)
        self._set_play_icon()
        self.open_player_button.config(state=tk.DISABLED)
        self.show_in_explorer_button.config(state=tk.DISABLED)
        self._set_video_progress(0.0, 0.0)
        self._render_placeholder(message)
        self._recalculate_overlay_content_height()

    def set_destination(self, event=None):
        destination_folder = filedialog.askdirectory(initialdir=self.last_folder_path)
        if destination_folder:
            self._add_destination_path(destination_folder, save=True)

    def _add_destination_path(self, destination_folder: str, save: bool = True):
        if destination_folder in self.destination_folders:
            self._set_status("Destination already added", 2500)
            return

        if not os.path.isdir(destination_folder):
            self._set_status("Invalid destination folder", 2500)
            return

        self.destination_folders.append(destination_folder)
        self._refresh_destination_buttons()
        if save:
            self._save_config()
        self._set_status(f"Destination added: {os.path.basename(destination_folder)}", 2500)

    def _refresh_destination_buttons(self):
        for child in self.destination_buttons_frame.winfo_children():
            child.destroy()

        if not self.destination_folders:
            empty_label = tk.Label(
                self.destination_buttons_frame,
                text="No destination yet. Add one to unlock quick move.",
                bg=self.color_panel,
                fg=self.color_muted,
                font=("Segoe UI", 9),
                anchor="w",
            )
            empty_label.pack(fill=tk.X)
            return

        max_cols = 5 if self.immersive_mode and len(self.destination_folders) > 4 else 4 if len(self.destination_folders) > 3 else len(self.destination_folders)
        max_cols = max(1, max_cols)
        for col in range(max_cols):
            self.destination_buttons_frame.grid_columnconfigure(col, weight=1, uniform="dest")

        for index, destination_folder in enumerate(self.destination_folders, start=1):
            folder_name = os.path.basename(destination_folder) or destination_folder
            row_index = (index - 1) // max_cols
            col_index = (index - 1) % max_cols

            card = tk.Frame(self.destination_buttons_frame, bg=self.destination_card_color)
            card.grid(row=row_index, column=col_index, sticky="ew", padx=4, pady=4)

            key_text = f"[{index if index < 10 else 0}]"
            max_len = 14 if self.immersive_mode else 18
            move_text = f"{key_text} {self._compact_text(folder_name, max_len=max_len)}"

            move_button = ttk.Button(
                card,
                text=move_text,
                command=lambda folder=destination_folder: self.move_file(folder),
                style="QuickDest.TButton",
            )
            move_button.pack(side=tk.LEFT, fill=tk.X, expand=True)
            self._make_widget_unfocusable(move_button)

            remove_button = ttk.Button(
                card,
                text="x",
                width=2,
                style="Danger.TButton",
                command=lambda folder=destination_folder: self.remove_destination(folder),
            )
            remove_button.pack(side=tk.LEFT, padx=(4, 0))
            self._make_widget_unfocusable(remove_button)

    def _move_to_destination_index(self, destination_index: int):
        if 0 <= destination_index < len(self.destination_folders):
            self.move_file(self.destination_folders[destination_index])

    @staticmethod
    def _compact_text(text: str, max_len: int = 20) -> str:
        if len(text) <= max_len:
            return text
        return text[: max_len - 3] + "..."

    def remove_destination(self, destination_folder: str):
        if destination_folder not in self.destination_folders:
            return

        self.destination_folders.remove(destination_folder)
        self._refresh_destination_buttons()
        self._save_config()
        self._set_status(f"Destination removed: {os.path.basename(destination_folder)}", 2500)

    @staticmethod
    def convert_bytes(size_bytes: int) -> str:
        if size_bytes <= 0:
            return "0 B"
        size_name = ("B", "KB", "MB", "GB", "TB")
        i = int(math.floor(math.log(size_bytes, 1024)))
        p = math.pow(1024, i)
        s = round(size_bytes / p, 2)
        return f"{s} {size_name[i]}"

    @staticmethod
    def get_modify_date(path: str) -> str:
        try:
            ts = os.path.getmtime(path)
        except OSError:
            return "-"
        return "Null" if ts < 0 else datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")

    def _media_kind(self, path: str) -> str:
        extension = os.path.splitext(path)[1].lower()
        if extension in IMAGE_EXTENSIONS:
            return "image"
        if extension in VIDEO_EXTENSIONS:
            return "video"
        if extension in AUDIO_EXTENSIONS:
            return "audio"
        return "unknown"

    def _build_file_info(self, file_path: str, extra_lines: list[str] | None = None) -> str:
        try:
            size_text = self.convert_bytes(os.path.getsize(file_path))
        except OSError:
            size_text = "-"

        lines = [
            f"Size: {size_text}",
            f"Modified: {self.get_modify_date(file_path)}",
        ]

        if self.current_media_kind in ("image", "video"):
            lines.append(f"Zoom: {self.zoom_level}%")

        if extra_lines:
            lines.extend(extra_lines)

        return "\n".join(lines)

    def _refresh_media_info(self):
        if not self.current_file_path:
            self.file_info_label.config(text="")
            return

        extra_lines: list[str] = []
        if self.current_media_kind == "image" and self.current_media_resolution:
            extra_lines.append(f"Resolution: {self.current_media_resolution[0]} x {self.current_media_resolution[1]}")
            if HEIF_ENABLED:
                extra_lines.append("Codec fallback: Pillow + HEIF + OpenCV")
            else:
                extra_lines.append("Codec fallback: Pillow + OpenCV")
        elif self.current_media_kind == "video":
            if self.current_media_resolution:
                extra_lines.append(f"Resolution: {self.current_media_resolution[0]} x {self.current_media_resolution[1]}")
            if self.current_media_duration_sec > 0:
                extra_lines.append(f"Duration: {self._format_seconds(self.current_media_duration_sec)}")
            if self.current_media_fps > 0:
                extra_lines.append(f"FPS: {self.current_media_fps:.1f}")
        elif self.current_media_kind == "audio":
            extra_lines.append("Audio marker: visualizer active in preview")
            if self.current_media_duration_sec > 0:
                extra_lines.append(f"Duration: {self._format_seconds(self.current_media_duration_sec)}")
            extra_lines.append("Tip: press P to pause/resume")

        self.file_info_label.config(text=self._build_file_info(self.current_file_path, extra_lines))
        self._recalculate_overlay_content_height()

    def show_current_file(self):
        if not self.file_paths:
            self._show_empty_state("No media loaded")
            return

        self.current_index = max(0, min(self.current_index, len(self.file_paths) - 1))
        file_path = self.file_paths[self.current_index]
        file_name = os.path.basename(file_path)

        self.current_file_path = file_path
        self.current_media_kind = self._media_kind(file_path)
        self.current_media_resolution = None
        self.current_media_duration_sec = 0.0
        self.current_media_fps = 0.0

        self._stop_media()
        self._cancel_pending_video_prepare()
        self._reset_current_video_state()
        self._set_inline_message("")
        self._reset_view_transform(reset_zoom=True)

        self.root.title(f"Fair Sorting - {file_name}")
        self.index_label.config(text=f"{self.current_index + 1} / {len(self.file_paths)}")
        self.file_name_label.config(text=f"File Name\n{file_name}")
        self._set_media_badge(self.current_media_kind)

        if self.current_media_kind == "image":
            self.play_pause_button.config(state=tk.DISABLED)
            self._set_play_icon()
            self.open_player_button.config(state=tk.NORMAL)
            self.show_in_explorer_button.config(state=tk.NORMAL)
            self._display_image(file_path)
        elif self.current_media_kind == "video":
            self.play_pause_button.config(state=tk.DISABLED)
            self._set_play_icon()
            self.open_player_button.config(state=tk.NORMAL)
            self.show_in_explorer_button.config(state=tk.NORMAL)
            self._show_video_ready_state(file_path)
        elif self.current_media_kind == "audio":
            self.play_pause_button.config(state=tk.NORMAL)
            self._set_pause_icon()
            self.open_player_button.config(state=tk.NORMAL)
            self.show_in_explorer_button.config(state=tk.NORMAL)
            self._play_audio(file_path)
        else:
            self.current_pil_image = None
            self.play_pause_button.config(state=tk.DISABLED)
            self._set_play_icon()
            self.open_player_button.config(state=tk.DISABLED)
            self.show_in_explorer_button.config(state=tk.DISABLED)
            self._set_video_progress(0.0, 0.0)
            self._render_placeholder("Unsupported file type")

        self._recalculate_overlay_content_height()
        self._focus_preview_canvas()

    def _autoplay_video_if_current(self, file_path: str):
        if self.current_file_path != file_path or self.current_media_kind != "video":
            return
        if not self.current_video_preview_available or not self.current_video_playback_available or self.playing_video:
            return
        self._start_video(0.0)

    def _load_image_with_fallback(self, file_path: str) -> Image.Image | None:
        try:
            with Image.open(file_path) as pil_image:
                if pil_image.mode not in ("RGB", "RGBA"):
                    pil_image = pil_image.convert("RGB")
                return pil_image.copy()
        except (OSError, UnidentifiedImageError):
            pass

        try:
            raw_data = np.fromfile(file_path, dtype=np.uint8)
            cv_image = cv2.imdecode(raw_data, cv2.IMREAD_UNCHANGED)
            if cv_image is not None:
                if cv_image.ndim == 2:
                    rgb = cv2.cvtColor(cv_image, cv2.COLOR_GRAY2RGB)
                elif cv_image.shape[2] == 4:
                    rgb = cv2.cvtColor(cv_image, cv2.COLOR_BGRA2RGBA)
                else:
                    rgb = cv2.cvtColor(cv_image, cv2.COLOR_BGR2RGB)
                return Image.fromarray(rgb)
        except Exception:
            pass

        if IMAGEIO_ENABLED and iio is not None:
            try:
                img_array = np.asarray(iio.imread(file_path))
                if img_array.size == 0:
                    return None

                if img_array.dtype != np.uint8:
                    img_max = float(np.max(img_array))
                    if img_max > 0:
                        img_array = (img_array / img_max * 255.0).astype(np.uint8)
                    else:
                        img_array = img_array.astype(np.uint8)

                if img_array.ndim == 2:
                    return Image.fromarray(img_array, mode="L").convert("RGB")

                if img_array.ndim == 3:
                    channels = img_array.shape[2]
                    if channels == 1:
                        return Image.fromarray(img_array[:, :, 0], mode="L").convert("RGB")
                    if channels >= 4:
                        return Image.fromarray(img_array[:, :, :4], mode="RGBA")
                    return Image.fromarray(img_array[:, :, :3], mode="RGB")
            except Exception:
                return None

        return None

    def _display_image(self, file_path: str):
        image = self._load_image_with_fallback(file_path)
        if image is None:
            self.current_pil_image = None
            self._render_placeholder("Unable to decode image", "Try opening in external player")
            self.file_info_label.config(text="")
            return

        self.current_pil_image = image
        self.current_media_resolution = (image.width, image.height)
        self.current_preview_audio_path = None
        self.playback_current_position_sec = 0.0
        self.video_last_rendered_frame_index = -1
        self._render_current_image_on_canvas()
        self._set_video_progress(0.0, 0.0)
        self._refresh_media_info()

    def _cancel_pending_video_prepare(self):
        self.current_video_prepare_request_id += 1
        if self.video_prepare_poll_after_id:
            try:
                self.root.after_cancel(self.video_prepare_poll_after_id)
            except tk.TclError:
                pass
            self.video_prepare_poll_after_id = None
        if self.video_prepare_future and not self.video_prepare_future.done():
            self.video_prepare_future.cancel()
        self.video_prepare_future = None

    def _show_video_loading_state(self, file_path: str):
        self.current_pil_image = None
        self.current_media_resolution = None
        self.current_media_duration_sec = 0.0
        self.current_media_fps = 0.0
        self.video_total_frames = 0
        self.playback_current_position_sec = 0.0
        self.current_preview_audio_path = None
        self.video_last_rendered_frame_index = -1
        self.play_pause_button.config(state=tk.DISABLED)
        self._set_play_icon()
        self._set_video_progress(0.0, 0.0)
        self._render_placeholder("Preparing video preview", os.path.basename(file_path))
        self._refresh_media_info()
        self._set_inline_message("Preparing poster, proxy, and metadata...", level="info", timeout_ms=2500)

    def _show_video_ready_state(self, file_path: str):
        self._reset_current_video_state()
        if self._restore_cached_video_preview_state(file_path):
            if self.current_video_playback_available:
                self.root.after_idle(lambda current_path=file_path: self._autoplay_video_if_current(current_path))
            return

        self._cancel_pending_video_prepare()
        self._show_video_loading_state(file_path)
        request_id = self.current_video_prepare_request_id
        self.video_prepare_future = self.background_executor.submit(
            self._prepare_video_preview_job,
            file_path,
            request_id,
        )
        self._poll_video_prepare_job(request_id, file_path)

    @staticmethod
    def _frame_to_pil(frame_bgr: np.ndarray) -> Image.Image:
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        return Image.fromarray(frame_rgb)

    def _ffmpeg_cache_key(self, source_file: str, profile: str) -> str:
        stat = os.stat(source_file)
        payload = f"{os.path.abspath(source_file)}|{stat.st_mtime_ns}|{stat.st_size}|{profile}"
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()
    
    def _cleanup_preview_cache(self):
        # Release video capture
        if self.video_capture is not None:
            try:
                self.video_capture.release()
            except Exception:
                pass
            self.video_capture = None

        # Stop pygame
        if self.audio_ready:
            try:
                pygame.mixer.music.stop()
                pygame.mixer.quit()
            except Exception:
                pass
            self.audio_ready = False

        # Clear in-memory caches
        self.video_preview_state.clear()
        self.preview_audio_cache.clear()
        self.audio_duration_cache.clear()

        # Hapus di background thread, app tidak hang
        cache_dir = self.preview_cache_dir
        def _delete_cache_in_background():
            if not os.path.exists(cache_dir):
                return
            for filename in os.listdir(cache_dir):
                file_path = os.path.join(cache_dir, filename)
                try:
                    os.remove(file_path)
                except OSError:
                    pass
            try:
                os.rmdir(cache_dir)
            except OSError:
                pass
            output_dir = os.path.dirname(cache_dir)
            try:
                if os.path.isdir(output_dir) and not os.listdir(output_dir):
                    os.rmdir(output_dir)
            except OSError:
                pass

        threading.Thread(target=_delete_cache_in_background, daemon=True).start()

    def _reset_current_video_state(self):
        self.current_video_preview_available = False
        self.current_video_playback_available = False
        self.current_video_proxy_path = None
        self.current_video_poster_path = None
        self.current_video_capture_source = None
        self.current_video_prepared_audio_path = None
        self.current_video_failure_reason = ""

    @staticmethod
    def _sanitize_media_error(error_text: str, fallback: str = "Preview decoder unavailable") -> str:
        lines = [line.strip() for line in error_text.splitlines() if line.strip()]
        if not lines:
            return fallback
        message = lines[-1]
        if len(message) > 140:
            message = message[:137].rstrip() + "..."
        return message

    @staticmethod
    def _parse_frame_rate(rate_text: str | None) -> float:
        if not rate_text:
            return 0.0
        if "/" in rate_text:
            try:
                numerator_text, denominator_text = rate_text.split("/", 1)
                numerator = float(numerator_text or "0")
                denominator = float(denominator_text or "1")
                return numerator / denominator if denominator else 0.0
            except ValueError:
                return 0.0
        try:
            return float(rate_text)
        except ValueError:
            return 0.0

    def _probe_video_metadata(
        self,
        source_file: str,
        timeout_sec: float | None = None,
    ) -> dict[str, float | int]:
        if not self.ffprobe_path or not os.path.exists(source_file):
            return {}

        try:
            result = subprocess.run(
                [
                    self.ffprobe_path,
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=width,height,avg_frame_rate,r_frame_rate,nb_frames,duration",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "json",
                    source_file,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=timeout_sec,
                **self._win_hidden_process_kwargs(),
            )
        except subprocess.TimeoutExpired:
            return {}
        if result.returncode != 0:
            return {}

        try:
            payload = json.loads(result.stdout.decode("utf-8", errors="ignore") or "{}")
        except json.JSONDecodeError:
            return {}

        stream = (payload.get("streams") or [{}])[0]
        format_info = payload.get("format") or {}

        fps = self._parse_frame_rate(str(stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "0"))

        try:
            frame_count = int(float(stream.get("nb_frames") or 0))
        except (TypeError, ValueError):
            frame_count = 0

        duration_raw = stream.get("duration") or format_info.get("duration") or 0
        try:
            duration = float(duration_raw or 0)
        except (TypeError, ValueError):
            duration = 0.0

        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
        if duration <= 0 and frame_count > 0 and fps > 0:
            duration = frame_count / fps

        return {
            "width": width,
            "height": height,
            "fps": max(0.0, fps),
            "frame_count": max(0, frame_count),
            "duration": max(0.0, duration),
        }

    def _remember_video_preview_state(
        self,
        source_file: str,
        available: bool,
        *,
        proxy_path: str | None = None,
        poster_path: str | None = None,
        capture_source: str | None = None,
        audio_path: str | None = None,
        metadata: dict[str, float | int] | None = None,
        playback_available: bool = False,
        reason: str = "",
    ):
        self.video_preview_state[source_file] = {
            "available": available,
            "proxy_path": proxy_path,
            "poster_path": poster_path,
            "capture_source": capture_source,
            "audio_path": audio_path,
            "metadata": dict(metadata or {}),
            "playback_available": playback_available,
            "reason": reason,
        }

    def _apply_video_ready_preview(
        self,
        file_path: str,
        preview_image: Image.Image,
        metadata: dict[str, float | int] | None = None,
        *,
        poster_path: str | None = None,
        proxy_path: str | None = None,
        capture_source: str | None = None,
        audio_path: str | None = None,
        playback_available: bool = True,
    ):
        metadata = metadata or {}
        width = int(metadata.get("width") or preview_image.width or 0)
        height = int(metadata.get("height") or preview_image.height or 0)
        fps = float(metadata.get("fps") or 0.0)
        frame_count = int(metadata.get("frame_count") or 0)
        duration = float(metadata.get("duration") or 0.0)

        self.current_pil_image = preview_image
        self.current_media_resolution = (width, height) if width > 0 and height > 0 else (preview_image.width, preview_image.height)
        self.current_media_duration_sec = max(0.0, duration)
        self.current_media_fps = max(0.0, fps)
        self.video_total_frames = max(0, frame_count)
        self.playback_current_position_sec = 0.0
        self.current_preview_audio_path = None
        self.video_last_rendered_frame_index = 0
        self.current_video_preview_available = True
        self.current_video_playback_available = playback_available
        self.current_video_proxy_path = proxy_path
        self.current_video_poster_path = poster_path
        self.current_video_capture_source = capture_source or file_path
        self.current_video_prepared_audio_path = audio_path
        if playback_available:
            self.current_video_failure_reason = ""
        self.play_pause_button.config(state=tk.NORMAL if playback_available else tk.DISABLED)

        self._remember_video_preview_state(
            file_path,
            True,
            proxy_path=proxy_path,
            poster_path=poster_path,
            capture_source=self.current_video_capture_source,
            audio_path=audio_path,
            metadata=metadata,
            playback_available=playback_available,
        )
        if playback_available:
            self._set_inline_message("")
        else:
            self._set_inline_message(
                self.current_video_failure_reason or "Preview ready, but playback is unavailable",
                level="error",
                timeout_ms=4200,
            )
        self._render_current_image_on_canvas()
        self._refresh_media_info()
        self._set_video_progress(0.0, self.current_media_duration_sec)

    def _mark_video_preview_unavailable(self, file_path: str, reason: str, remember: bool = True):
        reason = reason or "Preview decoder unavailable"
        self._stop_video()
        self.current_pil_image = None
        self.current_media_resolution = None
        self.current_media_duration_sec = 0.0
        self.current_media_fps = 0.0
        self.video_total_frames = 0
        self.playback_current_position_sec = 0.0
        self.current_preview_audio_path = None
        self.video_last_rendered_frame_index = -1
        self.current_video_preview_available = False
        self.current_video_playback_available = False
        self.current_video_proxy_path = None
        self.current_video_poster_path = None
        self.current_video_capture_source = None
        self.current_video_prepared_audio_path = None
        self.current_video_failure_reason = reason
        self.play_pause_button.config(state=tk.DISABLED)
        self._set_play_icon()
        self._set_video_progress(0.0, 0.0)
        self._render_placeholder("Video preview unavailable", "Try Open External, Next, or Move")
        self._refresh_media_info()
        self._set_inline_message(reason, level="error")
        self._set_status("Video preview unavailable for this file", 3600)
        if remember and file_path:
            self._remember_video_preview_state(file_path, False, reason=reason)

    def _restore_cached_video_preview_state(self, file_path: str) -> bool:
        cached = self.video_preview_state.get(file_path)
        if not cached:
            return False

        available = bool(cached.get("available"))
        poster_path = str(cached.get("poster_path") or "") or None
        proxy_path = str(cached.get("proxy_path") or "") or None
        capture_source = str(cached.get("capture_source") or "") or None
        audio_path = str(cached.get("audio_path") or "") or None
        metadata = cached.get("metadata") if isinstance(cached.get("metadata"), dict) else {}
        playback_available = bool(cached.get("playback_available"))
        reason = str(cached.get("reason") or "")

        if poster_path and not os.path.exists(poster_path):
            poster_path = None
        if proxy_path and not os.path.exists(proxy_path):
            proxy_path = None
        if audio_path and not os.path.exists(audio_path):
            audio_path = None

        if not available:
            self._mark_video_preview_unavailable(file_path, reason or "Preview decoder unavailable", remember=False)
            return True

        if poster_path:
            image = self._load_image_with_fallback(poster_path)
            if image is not None:
                self.current_video_failure_reason = reason
                self._apply_video_ready_preview(
                    file_path,
                    image,
                    metadata if isinstance(metadata, dict) else {},
                    poster_path=poster_path,
                    proxy_path=proxy_path,
                    capture_source=capture_source or file_path,
                    audio_path=audio_path,
                    playback_available=playback_available or bool(capture_source or proxy_path),
                )
                return True

        return False

    def _prepare_video_preview_job(self, source_file: str, request_id: int) -> dict[str, object]:
        result: dict[str, object] = {
            "request_id": request_id,
            "file_path": source_file,
            "available": False,
            "playback_available": False,
            "poster_path": None,
            "proxy_path": None,
            "audio_path": None,
            "capture_source": None,
            "metadata": {},
            "reason": "Preview decoder unavailable",
        }
        if not os.path.exists(source_file):
            result["reason"] = "File no longer exists"
            return result

        metadata = self._probe_video_metadata(source_file, timeout_sec=self.FFPROBE_TIMEOUT_SEC)
        result["metadata"] = metadata

        poster_path = self._ensure_preview_video_poster(
            source_file,
            timeout_sec=self.FFMPEG_POSTER_TIMEOUT_SEC,
        )
        reason = ""
        if not poster_path:
            poster_path, reason = self._extract_video_first_frame_poster(source_file)
        if not poster_path or not os.path.exists(poster_path):
            result["reason"] = reason or "Unable to decode the first video frame"
            return result

        proxy_path = None
        proxy_error = ""
        if self.ffmpeg_path:
            proxy_path, proxy_error = self._ensure_preview_video_proxy(
                source_file,
                timeout_sec=self.FFMPEG_PROXY_TIMEOUT_SEC,
            )

        audio_path = self._ensure_preview_audio(
            source_file,
            media_kind="video",
            timeout_sec=self.FFMPEG_AUDIO_TIMEOUT_SEC,
        )
        capture_source = proxy_path or source_file
        playback_available, capture_reason, capture_metadata = self._probe_video_playback_source(capture_source)
        if capture_metadata and not metadata:
            metadata = capture_metadata
            result["metadata"] = metadata

        result.update(
            {
                "available": True,
                "playback_available": playback_available,
                "poster_path": poster_path,
                "proxy_path": proxy_path,
                "audio_path": audio_path,
                "capture_source": capture_source,
                "reason": capture_reason or (proxy_error if not playback_available and proxy_error else ""),
            }
        )
        return result

    def _poll_video_prepare_job(self, request_id: int, file_path: str):
        future = self.video_prepare_future
        if future is None:
            return
        if not future.done():
            self.video_prepare_poll_after_id = self.root.after(
                self.VIDEO_PREP_POLL_INTERVAL_MS,
                lambda: self._poll_video_prepare_job(request_id, file_path),
            )
            return

        self.video_prepare_poll_after_id = None
        self.video_prepare_future = None
        if request_id != self.current_video_prepare_request_id:
            return
        if self.current_file_path != file_path or self.current_media_kind != "video":
            return

        try:
            payload = future.result()
        except Exception as exc:
            self._mark_video_preview_unavailable(file_path, f"Video prepare failed: {exc}")
            return

        poster_path = str(payload.get("poster_path") or "") or None
        preview_image = self._load_image_with_fallback(poster_path) if poster_path else None
        if not bool(payload.get("available")) or preview_image is None:
            self._mark_video_preview_unavailable(
                file_path,
                str(payload.get("reason") or "Preview decoder unavailable"),
            )
            return

        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        proxy_path = str(payload.get("proxy_path") or "") or None
        capture_source = str(payload.get("capture_source") or "") or None
        audio_path = str(payload.get("audio_path") or "") or None
        playback_available = bool(payload.get("playback_available"))
        self.current_video_failure_reason = str(payload.get("reason") or "")

        self._apply_video_ready_preview(
            file_path,
            preview_image,
            metadata,
            poster_path=poster_path,
            proxy_path=proxy_path,
            capture_source=capture_source or file_path,
            audio_path=audio_path,
            playback_available=playback_available,
        )
        if playback_available:
            self.root.after_idle(lambda current_path=file_path: self._autoplay_video_if_current(current_path))

    def _extract_video_first_frame_poster(self, source_file: str) -> tuple[str | None, str]:
        capture = cv2.VideoCapture(source_file)
        if not capture.isOpened():
            capture.release()
            return None, "Decoder unavailable or video file is damaged"

        ret, frame = capture.read()
        capture.release()
        if not ret:
            return None, "Unable to decode the first video frame"

        poster_path = self._write_video_frame_poster_to_cache(source_file, frame)
        if not poster_path:
            return None, "Unable to save video poster preview"
        return poster_path, ""

    def _write_video_frame_poster_to_cache(self, source_file: str, frame_bgr: np.ndarray) -> str | None:
        try:
            os.makedirs(self.preview_cache_dir, exist_ok=True)
            cached_path = os.path.join(
                self.preview_cache_dir,
                f"{self._ffmpeg_cache_key(source_file, 'video_poster_fallback_jpg')}.jpg",
            )
            pil_image = self._frame_to_pil(frame_bgr)
            pil_image.save(cached_path, format="JPEG", quality=92)
            return cached_path if os.path.exists(cached_path) else None
        except Exception:
            return None

    def _probe_video_playback_source(
        self,
        source_file: str | None,
    ) -> tuple[bool, str, dict[str, float | int]]:
        if not source_file or not os.path.exists(source_file):
            return False, "Prepared video stream is missing", {}

        capture = cv2.VideoCapture(source_file)
        if not capture.isOpened():
            capture.release()
            return False, "Unable to open prepared video stream", {}

        fps = capture.get(cv2.CAP_PROP_FPS)
        frame_count = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        ret, frame = capture.read()
        capture.release()
        if not ret or frame is None:
            return False, "Unable to decode the first prepared video frame", {}

        height, width = frame.shape[:2]
        metadata = {
            "width": int(width),
            "height": int(height),
            "fps": float(fps) if fps and fps > 0 else 0.0,
            "frame_count": int(frame_count) if frame_count and frame_count > 0 else 0,
            "duration": (float(frame_count) / float(fps)) if fps and fps > 0 and frame_count and frame_count > 0 else 0.0,
        }
        return True, "", metadata

    def _ensure_preview_video_poster(self, source_file: str, timeout_sec: float | None = None) -> str | None:
        if not self.ffmpeg_path:
            return None

        os.makedirs(self.preview_cache_dir, exist_ok=True)
        cached_path = os.path.join(
            self.preview_cache_dir,
            f"{self._ffmpeg_cache_key(source_file, 'video_poster_jpg')}.jpg",
        )
        if os.path.exists(cached_path):
            return cached_path

        ok, _ = self._run_ffmpeg(
            [
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                source_file,
                "-frames:v",
                "1",
                cached_path,
            ],
            timeout_sec=timeout_sec,
        )
        if ok and os.path.exists(cached_path):
            return cached_path
        return None

    def _ensure_preview_video_proxy(
        self,
        source_file: str,
        timeout_sec: float | None = None,
    ) -> tuple[str | None, str]:
        if not self.ffmpeg_path:
            return None, "ffmpeg unavailable"

        os.makedirs(self.preview_cache_dir, exist_ok=True)
        cached_path = os.path.join(
            self.preview_cache_dir,
            f"{self._ffmpeg_cache_key(source_file, 'video_proxy_mp4')}.mp4",
        )
        if os.path.exists(cached_path):
            return cached_path, ""

        ok, stderr = self._run_ffmpeg(
            [
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                source_file,
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
                cached_path,
            ],
            timeout_sec=timeout_sec,
        )
        if ok and os.path.exists(cached_path):
            return cached_path, ""
        return None, self._sanitize_media_error(stderr, "Unable to prepare browser-safe video proxy")

    def _run_ffmpeg(self, args: list[str], timeout_sec: float | None = None) -> tuple[bool, str]:
        if not self.ffmpeg_path:
            return False, "ffmpeg unavailable"

        command = [self.ffmpeg_path] + args
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=timeout_sec,
                **self._win_hidden_process_kwargs(),
            )
        except subprocess.TimeoutExpired:
            timeout_label = f"{timeout_sec:.0f}s" if timeout_sec else "timeout"
            return False, f"FFmpeg timed out after {timeout_label}"
        stderr = result.stderr.decode("utf-8", errors="ignore")
        return result.returncode == 0, stderr

    def _ensure_preview_audio(
        self,
        source_file: str,
        media_kind: str | None = None,
        timeout_sec: float | None = None,
    ) -> str | None:
        media_kind = media_kind or self._media_kind(source_file)
        if media_kind not in ("audio", "video"):
            return None

        cache_key = f"{source_file}|{media_kind}"
        cached_path = self.preview_audio_cache.get(cache_key)
        if cached_path and os.path.exists(cached_path):
            return cached_path

        if media_kind == "audio" and not self.ffmpeg_path:
            return source_file
        if media_kind == "video" and not self.ffmpeg_path:
            return None

        os.makedirs(self.preview_cache_dir, exist_ok=True)
        cached_path = os.path.join(
            self.preview_cache_dir,
            f"{self._ffmpeg_cache_key(source_file, f'{media_kind}_audio_mp3')}.mp3",
        )
        if os.path.exists(cached_path):
            self.preview_audio_cache[cache_key] = cached_path
            return cached_path

        ok, _ = self._run_ffmpeg(
            [
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                source_file,
                "-vn",
                "-c:a",
                "libmp3lame",
                "-q:a",
                "4",
                cached_path,
            ],
            timeout_sec=timeout_sec,
        )
        if ok and os.path.exists(cached_path):
            self.preview_audio_cache[cache_key] = cached_path
            return cached_path

        return source_file if media_kind == "audio" else None

    def _get_audio_duration(self, audio_file: str) -> float:
        if audio_file in self.audio_duration_cache:
            return self.audio_duration_cache[audio_file]

        duration = 0.0
        if self.ffprobe_path and os.path.exists(audio_file):
            try:
                result = subprocess.run(
                    [
                        self.ffprobe_path,
                        "-v",
                        "error",
                        "-show_entries",
                        "format=duration",
                        "-of",
                        "default=noprint_wrappers=1:nokey=1",
                        audio_file,
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                    timeout=self.FFPROBE_TIMEOUT_SEC,
                    **self._win_hidden_process_kwargs(),
                )
            except subprocess.TimeoutExpired:
                result = None
            if result is not None and result.returncode == 0:
                try:
                    duration = float(result.stdout.decode("utf-8", errors="ignore").strip() or "0")
                except ValueError:
                    duration = 0.0

        if duration <= 0 and self._init_audio():
            try:
                duration = float(pygame.mixer.Sound(audio_file).get_length())
            except pygame.error:
                duration = 0.0

        self.audio_duration_cache[audio_file] = max(0.0, duration)
        return self.audio_duration_cache[audio_file]

    def _prime_playback_clock(self, start_seconds: float):
        start_seconds = max(0.0, min(start_seconds, self.current_media_duration_sec or start_seconds))
        self.playback_start_offset_sec = start_seconds
        self.playback_current_position_sec = start_seconds
        self.audio_last_pos_sec = start_seconds
        self.audio_seek_offset_sec = start_seconds
        self.playback_started_at = time.perf_counter()

    def _current_playback_position(self) -> float:
        if self.current_media_kind == "video" and self.playing_video:
            position = self.playback_start_offset_sec + (time.perf_counter() - self.playback_started_at)
        elif self.current_media_kind == "audio" and not self.audio_paused:
            position = self.playback_start_offset_sec + (time.perf_counter() - self.playback_started_at)
        else:
            position = self.playback_current_position_sec

        if self.current_media_duration_sec > 0:
            position = min(position, self.current_media_duration_sec)
        return max(0.0, position)

    def _play_audio_stream(self, audio_file: str, start_seconds: float) -> bool:
        if not self._init_audio():
            return False

        try:
            pygame.mixer.music.load(audio_file)
            try:
                pygame.mixer.music.play(loops=0, start=max(0.0, start_seconds))
            except (TypeError, pygame.error):
                pygame.mixer.music.play()
                if start_seconds > 0:
                    try:
                        pygame.mixer.music.set_pos(start_seconds)
                    except pygame.error:
                        return False
            return True
        except pygame.error:
            return False

    def _render_video_frame_at(self, seconds: float, force: bool = False) -> bool:
        if not self.current_video_preview_available:
            return False

        capture_source = self.current_video_proxy_path or self.current_video_capture_source or self.current_file_path
        if self.video_capture is None and capture_source and self.current_media_kind == "video":
            self.video_capture = cv2.VideoCapture(capture_source)
            if not self.video_capture.isOpened():
                self.video_capture.release()
                self.video_capture = None
                self.current_video_failure_reason = "Unable to open prepared video stream"
                return False
        if self.video_capture is None:
            return False

        if self.current_media_fps <= 0:
            target_frame = 0
        elif self.video_total_frames > 0:
            target_frame = min(self.video_total_frames - 1, max(0, int(seconds * self.current_media_fps)))
        else:
            target_frame = max(0, int(seconds * self.current_media_fps))

        if not force and target_frame == self.video_last_rendered_frame_index:
            return True

        current_frame = max(0, int(round(self.video_capture.get(cv2.CAP_PROP_POS_FRAMES) - 1)))
        if force or abs(current_frame - target_frame) > 1:
            self.video_capture.set(cv2.CAP_PROP_POS_FRAMES, target_frame)

        ret, frame = self.video_capture.read()
        if not ret and target_frame > 0:
            self.video_capture.set(cv2.CAP_PROP_POS_FRAMES, max(0, target_frame - 1))
            ret, frame = self.video_capture.read()

        if not ret:
            self.current_video_failure_reason = "Unable to decode video frame"
            return False

        self.current_pil_image = self._frame_to_pil(frame)
        if self.current_media_resolution is None:
            self.current_media_resolution = (self.current_pil_image.width, self.current_pil_image.height)
        self.video_last_rendered_frame_index = target_frame
        self._render_current_image_on_canvas()
        return True

    def _on_seekbar_configure(self, event=None):
        self._draw_seekbar()

    def _draw_seekbar(self):
        if not hasattr(self, "seekbar_canvas"):
            return

        canvas_w = max(60, self.seekbar_canvas.winfo_width())
        canvas_h = max(18, self.seekbar_canvas.winfo_height())
        track_y = canvas_h // 2
        padding = 10
        start_x = padding
        end_x = canvas_w - padding
        track_width = max(8, end_x - start_x)
        progress_x = start_x + int(track_width * self.seekbar_percent)

        self.seekbar_canvas.delete("all")
        self.seekbar_canvas.create_line(
            start_x,
            track_y,
            end_x,
            track_y,
            fill=self._theme_palette()["progress_trough"],
            width=6,
            capstyle=tk.ROUND,
        )

        active_color = self.color_accent if self.seekbar_enabled else self.color_muted
        self.seekbar_canvas.create_line(
            start_x,
            track_y,
            progress_x,
            track_y,
            fill=active_color,
            width=6,
            capstyle=tk.ROUND,
        )
        self.seekbar_canvas.create_oval(
            progress_x - 6,
            track_y - 6,
            progress_x + 6,
            track_y + 6,
            fill="#FFFFFF" if self.theme_mode == "light" else self.color_text,
            outline=active_color,
            width=2,
        )

    def _on_seekbar_click(self, event):
        if not self.seekbar_enabled or self.current_media_duration_sec <= 0:
            return

        self._focus_preview_canvas()
        canvas_w = max(60, self.seekbar_canvas.winfo_width())
        padding = 10
        usable_width = max(1, canvas_w - padding * 2)
        ratio = (event.x - padding) / usable_width
        ratio = max(0.0, min(1.0, ratio))
        self._seek_media(ratio * self.current_media_duration_sec)

    def _seek_media(self, target_seconds: float):
        if self.current_media_kind not in ("audio", "video") or self.current_media_duration_sec <= 0:
            return

        target_seconds = max(0.0, min(target_seconds, self.current_media_duration_sec))
        self.playback_current_position_sec = target_seconds
        self.audio_last_pos_sec = target_seconds
        self.audio_seek_offset_sec = target_seconds

        if self.current_media_kind == "video":
            was_playing = self.playing_video
            if was_playing:
                self._start_video(start_seconds=target_seconds)
            else:
                self._render_video_frame_at(target_seconds, force=True)
                self._set_video_progress(target_seconds, self.current_media_duration_sec)
        else:
            if self.audio_paused:
                self._set_video_progress(target_seconds, self.current_media_duration_sec)
                self._render_audio_indicator(os.path.basename(self.current_file_path or ""), paused=True)
            else:
                self._play_audio(self.current_file_path or "", start_seconds=target_seconds)

    def _render_current_image_on_canvas(self):
        if self.current_pil_image is None:
            self._render_placeholder("No preview available")
            return

        canvas_w = max(200, self.preview_canvas.winfo_width())
        canvas_h = max(200, self.preview_canvas.winfo_height())
        safe_left, _, safe_w, safe_h, safe_center_x, safe_center_y = self._preview_safe_bounds()

        image_w, image_h = self.current_pil_image.size
        if image_w <= 0 or image_h <= 0:
            self._render_placeholder("Invalid image data")
            return

        fit_scale = min((safe_w - 24) / image_w, (safe_h - 24) / image_h)
        fit_scale = max(0.02, fit_scale)
        final_scale = fit_scale * (self.zoom_level / 100.0)

        target_w = int(max(24, image_w * final_scale))
        target_h = int(max(24, image_h * final_scale))

        resized = self.current_pil_image.resize((target_w, target_h), Image.LANCZOS)
        self.display_photo = ImageTk.PhotoImage(resized)

        self.preview_canvas.delete("all")
        center_x = safe_center_x + self.pan_x
        center_y = safe_center_y + self.pan_y
        self.preview_canvas.create_image(center_x, center_y, image=self.display_photo)

        if self.zoom_level > 100 and self.current_media_kind in ("image", "video"):
            self.preview_canvas.create_text(
                safe_left + safe_w - 12,
                canvas_h - 10,
                text="Drag to pan",
                fill=self.color_muted,
                font=("Segoe UI", 9),
                anchor="se",
            )

    def _render_placeholder(self, title: str, subtitle: str = ""):
        self.current_placeholder_title = title
        self.current_placeholder_subtitle = subtitle
        canvas_w = max(200, self.preview_canvas.winfo_width())
        canvas_h = max(200, self.preview_canvas.winfo_height())
        _, _, _, _, safe_center_x, safe_center_y = self._preview_safe_bounds()

        self.preview_canvas.delete("all")
        self.preview_canvas.create_text(
            safe_center_x,
            safe_center_y - 12,
            text=title,
            fill=self.color_text,
            font=("Segoe UI Semibold", 12),
            anchor="center",
        )
        if subtitle:
            self.preview_canvas.create_text(
                safe_center_x,
                safe_center_y + 14,
                text=subtitle,
                fill=self.color_muted,
                font=("Segoe UI", 10),
                anchor="center",
            )

    def _render_audio_indicator(self, file_name: str, paused: bool = False):
        self.preview_canvas.delete("all")
        self.audio_bar_ids = []

        canvas_w = max(200, self.preview_canvas.winfo_width())
        canvas_h = max(200, self.preview_canvas.winfo_height())
        safe_left, _, safe_w, _, safe_center_x, _ = self._preview_safe_bounds()

        self.preview_canvas.create_text(
            safe_center_x,
            int(canvas_h * 0.35),
            text="AUDIO",
            fill=self.color_accent,
            font=("Segoe UI Semibold", 26),
            anchor="center",
        )
        self.preview_canvas.create_text(
            safe_center_x,
            int(canvas_h * 0.43),
            text=file_name,
            fill=self.color_text,
            font=("Segoe UI", 11),
            anchor="center",
        )
        self.preview_canvas.create_text(
            safe_center_x,
            int(canvas_h * 0.50),
            text="Paused" if paused else "Playing",
            fill=self.color_muted,
            font=("Segoe UI", 10),
            anchor="center",
        )

        bar_total_width = min(safe_w - 80, 520)
        bar_gap = 4
        bar_width = max(8, int((bar_total_width - bar_gap * (self.AUDIO_BARS - 1)) / self.AUDIO_BARS))
        used_width = self.AUDIO_BARS * bar_width + (self.AUDIO_BARS - 1) * bar_gap
        start_x = safe_left + (safe_w - used_width) // 2
        base_y = int(canvas_h * 0.72)

        for index in range(self.AUDIO_BARS):
            x1 = start_x + index * (bar_width + bar_gap)
            bar_id = self.preview_canvas.create_rectangle(
                x1,
                base_y - 18,
                x1 + bar_width,
                base_y,
                fill=self._theme_palette()["progress_trough"],
                outline="",
            )
            self.audio_bar_ids.append(bar_id)

    def _start_audio_visualizer(self):
        self._stop_audio_visualizer()
        self.audio_phase = 0.0
        self._animate_audio_visualizer()

    def _stop_audio_visualizer(self):
        if self.audio_visualizer_after_id:
            self.root.after_cancel(self.audio_visualizer_after_id)
            self.audio_visualizer_after_id = None

    def _animate_audio_visualizer(self):
        if self.preview_resize_in_progress:
            self.audio_visualizer_after_id = self.root.after(
                self.AUDIO_VIS_INTERVAL_MS,
                self._animate_audio_visualizer,
            )
            return
        if self.current_media_kind != "audio" or not self.audio_bar_ids:
            return

        canvas_w = max(200, self.preview_canvas.winfo_width())
        canvas_h = max(200, self.preview_canvas.winfo_height())
        safe_left, _, safe_w, _, _, _ = self._preview_safe_bounds()
        bar_total_width = min(safe_w - 80, 520)
        bar_gap = 4
        bar_width = max(8, int((bar_total_width - bar_gap * (self.AUDIO_BARS - 1)) / self.AUDIO_BARS))
        used_width = self.AUDIO_BARS * bar_width + (self.AUDIO_BARS - 1) * bar_gap
        start_x = safe_left + (safe_w - used_width) // 2
        base_y = int(canvas_h * 0.72)

        is_active = self.audio_ready and not self.audio_paused and pygame.mixer.music.get_busy()
        max_h = max(20, int(canvas_h * 0.18))
        idle_color = self._theme_palette()["progress_trough"]

        for idx, bar_id in enumerate(self.audio_bar_ids):
            x1 = start_x + idx * (bar_width + bar_gap)

            if is_active:
                waveform = math.sin(self.audio_phase + idx * 0.45)
                dynamic = int(max_h * (0.35 + 0.65 * abs(waveform)))
                jitter = random.randint(0, 10)
                bar_h = min(max_h, dynamic + jitter)
                color = self.color_accent
            else:
                bar_h = 12 + int(8 * abs(math.sin(self.audio_phase + idx * 0.3)))
                color = idle_color

            self.preview_canvas.coords(bar_id, x1, base_y - bar_h, x1 + bar_width, base_y)
            self.preview_canvas.itemconfig(bar_id, fill=color)

        self.audio_phase += 0.32
        self.audio_visualizer_after_id = self.root.after(self.AUDIO_VIS_INTERVAL_MS, self._animate_audio_visualizer)

    def _init_audio(self) -> bool:
        if self.audio_ready:
            return True
        try:
            pygame.mixer.init()
            self.audio_ready = True
            return True
        except pygame.error:
            self._set_status("Audio device unavailable", 3000)
            return False

    def _play_audio(self, file_path: str, start_seconds: float | None = None):
        if not file_path:
            return

        preview_path = self._ensure_preview_audio(
            file_path,
            media_kind="audio",
            timeout_sec=self.FFMPEG_AUDIO_TIMEOUT_SEC,
        )
        if not preview_path:
            self.play_pause_button.config(state=tk.DISABLED)
            self._set_play_icon()
            self._render_placeholder("Unable to prepare audio")
            return

        duration = self._get_audio_duration(preview_path)
        target_start = self.playback_current_position_sec if start_seconds is None else start_seconds
        if duration > 0 and target_start >= duration - 0.08:
            target_start = 0.0
        target_start = max(0.0, min(target_start, duration or target_start))

        if not self._play_audio_stream(preview_path, target_start):
            self.play_pause_button.config(state=tk.DISABLED)
            self._set_play_icon()
            self._render_placeholder("Unable to play audio")
            self._set_status("Unable to play audio", 3000)
            return

        self.current_preview_audio_path = preview_path
        self.audio_paused = False
        self.current_pil_image = None
        self.current_media_resolution = None
        self.current_media_duration_sec = duration
        self.current_media_fps = 0.0
        self._prime_playback_clock(target_start)

        self._render_audio_indicator(os.path.basename(file_path), paused=False)
        self._start_audio_visualizer()
        self._start_audio_poll()
        self._refresh_media_info()
        self._set_video_progress(target_start, self.current_media_duration_sec)
        self._set_pause_icon()

    def _start_audio_poll(self):
        self._stop_audio_poll()
        self._poll_audio_state()

    def _stop_audio_poll(self):
        if self.audio_poll_after_id:
            self.root.after_cancel(self.audio_poll_after_id)
            self.audio_poll_after_id = None

    def _poll_audio_state(self):
        if self.current_media_kind != "audio":
            return

        current_seconds = self._current_playback_position()
        self.playback_current_position_sec = current_seconds
        self._set_video_progress(current_seconds, self.current_media_duration_sec)

        if self.audio_ready and not self.audio_paused and not pygame.mixer.music.get_busy():
            self.audio_paused = True
            self.playback_current_position_sec = self.current_media_duration_sec
            self._set_play_icon()
            self._render_audio_indicator(os.path.basename(self.current_file_path or ""), paused=True)
            self._set_video_progress(self.current_media_duration_sec, self.current_media_duration_sec)
            return

        self.audio_poll_after_id = self.root.after(160, self._poll_audio_state)

    def _start_video(self, start_seconds: float | None = None):
        if not self.current_file_path or self.current_media_kind != "video":
            return

        if not self.current_video_preview_available:
            self._mark_video_preview_unavailable(
                self.current_file_path,
                self.current_video_failure_reason or "Video preview is not available for this file",
                remember=False,
            )
            return
        if not self.current_video_playback_available:
            self.play_pause_button.config(state=tk.DISABLED)
            self._set_play_icon()
            self._set_inline_message(
                self.current_video_failure_reason or "Video playback unavailable for this file",
                level="error",
                timeout_ms=4200,
            )
            self._set_status("Video playback unavailable for this file", 3200)
            return

        if self.video_capture is None:
            capture_source = self.current_video_proxy_path or self.current_video_capture_source or self.current_file_path
            self.video_capture = cv2.VideoCapture(capture_source)
            if not self.video_capture.isOpened():
                self.video_capture.release()
                self.video_capture = None
                self._mark_video_preview_unavailable(
                    self.current_file_path,
                    self.current_video_failure_reason or "Unable to open prepared video stream",
                )
                return

            fps = self.video_capture.get(cv2.CAP_PROP_FPS)
            frame_count = self.video_capture.get(cv2.CAP_PROP_FRAME_COUNT)
            self.current_media_fps = fps if fps and fps > 0 else 30.0
            self.video_total_frames = int(frame_count) if frame_count and frame_count > 0 else 0
            self.current_media_duration_sec = (
                frame_count / self.current_media_fps if frame_count > 0 and self.current_media_fps > 0 else 0.0
            )
            if self.current_media_duration_sec < 0:
                self.current_media_duration_sec = 0.0

        target_start = self.playback_current_position_sec if start_seconds is None else start_seconds
        if self.current_media_duration_sec > 0 and target_start >= self.current_media_duration_sec - 0.12:
            target_start = 0.0
        target_start = max(0.0, min(target_start, self.current_media_duration_sec or target_start))

        self.current_preview_audio_path = self.current_video_prepared_audio_path
        if self.current_preview_audio_path:
            if not self._play_audio_stream(self.current_preview_audio_path, target_start):
                self.current_preview_audio_path = None
                self._set_status("Video audio unavailable, preview continues muted", 2600)
        elif not self.ffmpeg_path:
            self._set_status("FFmpeg not found, video preview is muted", 2600)

        self.playing_video = True
        self.audio_paused = False
        self._prime_playback_clock(target_start)
        self._set_pause_icon()
        if not self._render_video_frame_at(target_start, force=True):
            self._pause_video()
            self._mark_video_preview_unavailable(
                self.current_file_path,
                self.current_video_failure_reason or "Unable to render video frame",
            )
            return
        self._set_video_progress(target_start, self.current_media_duration_sec)
        self._update_video_frame()

    def _pause_video(self):
        self.playback_current_position_sec = self._current_playback_position()
        self.playing_video = False
        self._set_play_icon()
        if self.video_after_id:
            self.root.after_cancel(self.video_after_id)
            self.video_after_id = None
        if self.audio_ready:
            try:
                pygame.mixer.music.stop()
            except pygame.error:
                pass

    def _update_video_frame(self):
        if not self.playing_video or self.video_capture is None:
            return
        if self.preview_resize_in_progress:
            self.video_after_id = self.root.after(40, self._update_video_frame)
            return

        current_seconds = self._current_playback_position()
        if self.current_media_duration_sec > 0 and current_seconds >= self.current_media_duration_sec:
            self.playing_video = False
            self.playback_current_position_sec = self.current_media_duration_sec
            if self.audio_ready:
                try:
                    pygame.mixer.music.stop()
                except pygame.error:
                    pass
            self._set_play_icon()
            self._set_video_progress(self.current_media_duration_sec, self.current_media_duration_sec)
            self._render_video_frame_at(max(0.0, self.current_media_duration_sec - (1 / max(1.0, self.current_media_fps or 1.0))), force=True)
            return

        if not self._render_video_frame_at(current_seconds):
            self._pause_video()
            self._mark_video_preview_unavailable(
                self.current_file_path or "",
                self.current_video_failure_reason or "Unable to render video frame",
            )
            return

        self.playback_current_position_sec = current_seconds
        self._set_video_progress(current_seconds, self.current_media_duration_sec)

        delay = int(1000 / self.current_media_fps) if self.current_media_fps > 1 else 33
        delay = max(20, min(delay, 60))
        self.video_after_id = self.root.after(delay, self._update_video_frame)

    def _stop_video(self):
        self._pause_video()
        if self.video_capture is not None:
            self.video_capture.release()
            self.video_capture = None
        self.video_last_rendered_frame_index = -1
        self.video_total_frames = 0

    def _stop_audio(self):
        self._stop_audio_visualizer()
        self._stop_audio_poll()
        if self.audio_ready:
            try:
                pygame.mixer.music.stop()
            except pygame.error:
                pass
        self.audio_paused = False

    def _stop_media(self):
        self._stop_video()
        self._stop_audio()
        self.playback_current_position_sec = 0.0
        self.playback_start_offset_sec = 0.0
        self.current_preview_audio_path = None

    def play_pause(self, event=None):
        if not self.current_file_path:
            return

        if self.current_media_kind == "audio":
            self._toggle_audio()
            return

        if self.current_media_kind == "video":
            if self.video_prepare_future is not None and not self.current_video_preview_available:
                self._set_status("Video preview is still preparing", 1800)
                return
            if self.playing_video:
                self._pause_video()
            else:
                self._start_video()

    def _toggle_audio(self):
        if not self.current_file_path:
            return

        if self.audio_paused:
            self._play_audio(self.current_file_path, start_seconds=self.playback_current_position_sec)
            return

        current_position = self._current_playback_position()
        self.playback_current_position_sec = current_position
        self.audio_last_pos_sec = current_position
        self.audio_seek_offset_sec = current_position
        if self.audio_ready:
            try:
                pygame.mixer.music.stop()
            except pygame.error:
                pass
        self.audio_paused = True
        self._set_play_icon()
        self._render_audio_indicator(os.path.basename(self.current_file_path or ""), paused=True)
        self._set_video_progress(current_position, self.current_media_duration_sec)

    def open_external_player(self):
        if not self.current_file_path:
            return

        try:
            if os.name == "nt":
                os.startfile(self.current_file_path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", self.current_file_path])
            else:
                subprocess.Popen(["xdg-open", self.current_file_path])
        except Exception:
            self._set_status("Unable to open external player", 3000)

    def _show_image_context_menu(self, event):
        if not self.current_file_path:
            return
        try:
            self.image_context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.image_context_menu.grab_release()

    def show_image_in_explorer(self):
        if not self.current_file_path:
            return

        target_path = os.path.normpath(self.current_file_path)
        try:
            if os.name == "nt":
                subprocess.Popen(
                    [os.path.join(os.getenv("WINDIR", "C:\\Windows"), "explorer.exe"), "/select,", target_path]
                )
            else:
                folder = os.path.dirname(target_path)
                subprocess.Popen(["xdg-open", folder])
        except Exception:
            self._set_status("Unable to open file location", 3000)

    def _build_unique_destination(self, destination_folder: str, filename: str) -> str:
        base, ext = os.path.splitext(filename)
        candidate = os.path.join(destination_folder, filename)
        counter = 1
        while os.path.exists(candidate):
            candidate = os.path.join(destination_folder, f"{base}_{counter}{ext}")
            counter += 1
        return candidate

    def move_file(self, destination_folder: str):
        if not self.file_paths:
            self._set_status("No files to move", 2500)
            return

        if not os.path.isdir(destination_folder):
            self._set_status("Destination folder not found", 2500)
            return

        source_path = self.file_paths[self.current_index]
        destination_path = self._build_unique_destination(destination_folder, os.path.basename(source_path))

        self._stop_media()
        try:
            shutil.move(source_path, destination_path)
        except OSError as exc:
            self._set_status(f"Move failed: {exc}", 4200)
            return

        record = MoveRecord(source_path=source_path, destination_path=destination_path)
        self.undo_record = record
        self.undo_button.config(state=tk.NORMAL)

        self.log_entries.append(record)
        self._write_log_entries()
        self._refresh_log_list()

        moved_name = os.path.basename(source_path)
        self.file_paths.pop(self.current_index)
        if self.file_paths and self.current_index >= len(self.file_paths):
            self.current_index = len(self.file_paths) - 1
        elif not self.file_paths:
            self.current_index = 0

        self._set_status(f"Moved {moved_name}", 2500)

        if self.file_paths:
            self.show_current_file()
        else:
            self._show_empty_state("All files in this folder are moved")
        self._set_inline_message(
            f"Moved: {moved_name} -> {os.path.basename(destination_folder)}",
            level="success",
            timeout_ms=7000,
        )

    def _remove_log_record(self, record: MoveRecord):
        for idx in range(len(self.log_entries) - 1, -1, -1):
            candidate = self.log_entries[idx]
            if candidate.destination_path == record.destination_path:
                self.log_entries.pop(idx)
                break

    def _insert_back_to_current_list(self, file_path: str):
        if not self.loaded_folder_path:
            return

        source_folder = os.path.normcase(os.path.dirname(file_path))
        loaded_folder = os.path.normcase(self.loaded_folder_path)

        if source_folder != loaded_folder:
            return

        if file_path not in self.file_paths:
            self.file_paths.append(file_path)
            self.file_paths.sort(key=lambda p: os.path.basename(p).lower())

    def undo_move(self, event=None):
        if not self.undo_record:
            self._set_status("Nothing to undo", 2500)
            return

        record = self.undo_record
        if not os.path.exists(record.destination_path):
            self._set_status("Cannot undo, moved file not found", 3000)
            self.undo_record = None
            self.undo_button.config(state=tk.DISABLED)
            return

        restore_path = record.source_path
        if os.path.exists(restore_path):
            restore_path = self._build_unique_destination(
                os.path.dirname(restore_path), os.path.basename(restore_path)
            )

        self._stop_media()
        try:
            shutil.move(record.destination_path, restore_path)
        except OSError as exc:
            self._set_status(f"Undo failed: {exc}", 4200)
            return

        restored_record = MoveRecord(source_path=restore_path, destination_path=record.destination_path)
        self._remove_log_record(restored_record)
        self._write_log_entries()
        self._refresh_log_list()

        self._insert_back_to_current_list(restore_path)
        if self.file_paths and restore_path in self.file_paths:
            self.current_index = self.file_paths.index(restore_path)
            self.show_current_file()
        elif not self.file_paths:
            self._show_empty_state("No media loaded")

        self.undo_record = None
        self.undo_button.config(state=tk.DISABLED)
        self._set_status("Last move undone", 2500)
        self._set_inline_message(
            f"Undo: restored {os.path.basename(restore_path)}",
            level="success",
            timeout_ms=7000,
        )

    def undo_selected_move(self):
        record_index, record = self._selected_log_record()
        if record is None or record_index is None:
            self._set_status("Select a log entry first", 2500)
            return

        if not os.path.exists(record.destination_path):
            self._set_status("Moved file no longer exists", 3000)
            return

        restore_path = record.source_path
        if os.path.exists(restore_path):
            restore_path = self._build_unique_destination(
                os.path.dirname(restore_path), os.path.basename(restore_path)
            )

        self._stop_media()
        try:
            shutil.move(record.destination_path, restore_path)
        except OSError as exc:
            self._set_status(f"Undo failed: {exc}", 4200)
            return

        self.log_entries.pop(record_index)
        self._write_log_entries()
        self._refresh_log_list()

        self._insert_back_to_current_list(restore_path)
        if self.file_paths and restore_path in self.file_paths:
            self.current_index = self.file_paths.index(restore_path)
            self.show_current_file()
        elif not self.file_paths:
            self._show_empty_state("No media loaded")

        self._set_status("Selected log entry undone", 2500)
        self._set_inline_message(
            f"Undo selected: restored {os.path.basename(restore_path)}",
            level="success",
            timeout_ms=7000,
        )

    def clear_message(self):
        self._set_inline_message("")

    def on_log_select(self, event):
        has_selection = bool(self.log_listbox.curselection())
        self.log_undo_button.config(state=tk.NORMAL if has_selection else tk.DISABLED)
        if hasattr(self, "log_detail_button"):
            self.log_detail_button.config(state=tk.NORMAL if has_selection else tk.DISABLED)
        selected_index = self.log_listbox.curselection()[0] if has_selection else None
        self.log_context_index = (
            self._display_log_index_to_record_index(selected_index) if selected_index is not None else None
        )

    def show_log_context_menu(self, event):
        if not self.log_entries:
            return

        index = self.log_listbox.nearest(event.y)
        record_index = self._display_log_index_to_record_index(index)
        if record_index is None:
            return

        self.log_listbox.selection_clear(0, tk.END)
        self.log_listbox.selection_set(index)
        self.log_listbox.activate(index)
        self.log_context_index = record_index

        try:
            self.log_context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.log_context_menu.grab_release()

    def _show_selected_log_detail_with_preview(self):
        record_index, record = self._selected_log_record()
        if record is None or record_index is None:
            self._set_status("Select a log entry first", 2500)
            return

        self.log_context_index = record_index
        self.show_move_details(record, include_preview=True)

    def preview_image(self, file_path: str):
        if not os.path.exists(file_path):
            self._set_status("File does not exist", 2500)
            return
        if self._media_kind(file_path) != "image":
            self._set_status("Preview only available for images", 2500)
            return

        image = self._load_image_with_fallback(file_path)
        if image is None:
            self._set_status("Unable to decode image", 2500)
            return

        preview_window = tk.Toplevel(self.root)
        preview_window.title(os.path.basename(file_path))
        preview_window.geometry("940x680")
        preview_window.configure(bg=self.color_bg)

        preview_label = tk.Label(preview_window, bg=self.color_bg)
        preview_label.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        def redraw_preview(event=None):
            width = max(120, preview_window.winfo_width() - 20)
            height = max(120, preview_window.winfo_height() - 20)
            temp = image.copy()
            temp.thumbnail((width, height), Image.LANCZOS)
            image_tk = ImageTk.PhotoImage(temp)
            preview_label.config(image=image_tk)
            preview_label.image = image_tk

        preview_window.bind("<Configure>", redraw_preview)
        redraw_preview()

    def show_move_details(self, record: MoveRecord, include_preview: bool = False):
        details_window = tk.Toplevel(self.root)
        details_window.title("Move Details")
        details_window.geometry("920x620" if include_preview else "480x360")
        details_window.configure(bg=self.color_bg)

        content = tk.Frame(details_window, bg=self.color_bg)
        content.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        details_frame = tk.Frame(content, bg=self.color_panel, padx=12, pady=12)
        details_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        def add_row(text: str):
            label = tk.Label(
                details_frame,
                text=text,
                bg=self.color_panel,
                fg=self.color_text,
                font=("Segoe UI", 9),
                wraplength=438,
                justify="left",
                anchor="w",
            )
            label.pack(fill=tk.X, pady=3)

        newest_first_number = None
        for idx in range(len(self.log_entries) - 1, -1, -1):
            if self.log_entries[idx] == record:
                newest_first_number = len(self.log_entries) - idx
                break
        if newest_first_number is not None:
            add_row(f"Log Order: #{newest_first_number} (newest first)")
        add_row(f"Source: {record.source_path}")
        add_row(f"Destination: {record.destination_path}")

        preview_image_data: Image.Image | None = None
        if os.path.exists(record.destination_path):
            add_row(f"Size: {self.convert_bytes(os.path.getsize(record.destination_path))}")
            add_row(f"Modified: {self.get_modify_date(record.destination_path)}")
            if self._media_kind(record.destination_path) == "image":
                preview_image_data = self._load_image_with_fallback(record.destination_path)
                if preview_image_data is not None:
                    add_row(f"Resolution: {preview_image_data.width} x {preview_image_data.height}")
                else:
                    add_row("Resolution: unavailable")
        else:
            add_row("Destination file no longer exists")

        if include_preview:
            preview_shell = tk.Frame(content, bg=self.color_panel, padx=12, pady=12)
            preview_shell.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0))

            preview_title = tk.Label(
                preview_shell,
                text="Preview",
                bg=self.color_panel,
                fg=self.color_text,
                font=("Segoe UI Semibold", 11),
                anchor="w",
            )
            preview_title.pack(fill=tk.X, pady=(0, 8))

            preview_host = tk.Label(preview_shell, bg=self.color_display)
            preview_host.pack(fill=tk.BOTH, expand=True)

            if preview_image_data is None:
                placeholder = "Preview only available for image destinations."
                if not os.path.exists(record.destination_path):
                    placeholder = "Destination file no longer exists."
                preview_host.config(
                    text=placeholder,
                    fg=self.color_muted,
                    font=("Segoe UI", 10),
                    padx=16,
                    pady=16,
                )
            else:
                def redraw_preview(event=None):
                    width = max(160, preview_host.winfo_width() - 16)
                    height = max(160, preview_host.winfo_height() - 16)
                    temp = preview_image_data.copy()
                    temp.thumbnail((width, height), Image.LANCZOS)
                    image_tk = ImageTk.PhotoImage(temp)
                    preview_host.config(image=image_tk, text="")
                    preview_host.image = image_tk

                preview_host.bind("<Configure>", redraw_preview)
                redraw_preview()

    def previous_file(self, event=None):
        if not self.file_paths:
            self._show_empty_state("No files loaded. Please select a folder")
            return
        self.current_index = (self.current_index - 1) % len(self.file_paths)
        self.show_current_file()

    def next_file(self, event=None):
        if not self.file_paths:
            self._show_empty_state("No files loaded. Please select a folder")
            return
        self.current_index = (self.current_index + 1) % len(self.file_paths)
        self.show_current_file()

    def _on_preview_resize(self, event):
        if self.resize_after_id:
            try:
                self.root.after_cancel(self.resize_after_id)
            except tk.TclError:
                pass
        if self.preview_resize_settle_after_id:
            try:
                self.root.after_cancel(self.preview_resize_settle_after_id)
            except tk.TclError:
                pass
            self.preview_resize_settle_after_id = None
        self.preview_resize_in_progress = True
        if self.video_after_id:
            try:
                self.root.after_cancel(self.video_after_id)
            except tk.TclError:
                pass
            self.video_after_id = None
        self._refresh_overlay_width(rerender_preview=False, reflow_content=False)
        self.resize_after_id = self.root.after(36, self._refresh_current_canvas)
        self.preview_resize_settle_after_id = self.root.after(
            self.PREVIEW_RESIZE_SETTLE_MS,
            self._finish_preview_resize,
        )

    def _finish_preview_resize(self):
        self.preview_resize_settle_after_id = None
        self.preview_resize_in_progress = False
        self._refresh_overlay_width()
        self._refresh_current_canvas()
        if self.current_media_kind == "video" and self.playing_video:
            self._update_video_frame()

    def _refresh_current_canvas(self):
        if self.current_media_kind in ("image", "video") and self.current_pil_image is not None:
            self._render_current_image_on_canvas()
        elif self.current_media_kind == "audio" and self.current_file_path:
            self._render_audio_indicator(os.path.basename(self.current_file_path), paused=self.audio_paused)
            if not self.preview_resize_in_progress:
                self._start_audio_visualizer()
        else:
            self._render_placeholder(self.current_placeholder_title, self.current_placeholder_subtitle)

        if hasattr(self, "overlay_panel"):
            self._recalculate_overlay_content_height()

    def _on_canvas_press(self, event):
        self._focus_preview_canvas()
        self.drag_start_x = event.x
        self.drag_start_y = event.y

    def _on_canvas_drag(self, event):
        if self.current_media_kind not in ("image", "video"):
            return
        if self.zoom_level <= 100:
            return
        if self.current_pil_image is None:
            return

        delta_x = event.x - self.drag_start_x
        delta_y = event.y - self.drag_start_y
        self.drag_start_x = event.x
        self.drag_start_y = event.y

        self.pan_x += delta_x
        self.pan_y += delta_y
        self._render_current_image_on_canvas()

    def _reset_view_transform(self, reset_zoom: bool = False):
        self.pan_x = 0
        self.pan_y = 0
        if reset_zoom:
            self.zoom_level = 100
            self._update_zoom_label()

    def _update_zoom_label(self):
        self.zoom_value_label.config(text=f"{self.zoom_level}%")

    def _apply_zoom(self, delta: int):
        if self.current_media_kind not in ("image", "video"):
            return

        new_zoom = max(self.ZOOM_MIN, min(self.ZOOM_MAX, self.zoom_level + delta))
        if new_zoom == self.zoom_level:
            return

        self.zoom_level = new_zoom
        self._update_zoom_label()

        if self.current_pil_image is not None:
            self._render_current_image_on_canvas()
        self._refresh_media_info()
        self._set_status(f"Zoom: {self.zoom_level}%", 800)

    def zoom(self, event):
        direction = 0
        if hasattr(event, "delta") and event.delta:
            direction = 1 if event.delta > 0 else -1
        elif hasattr(event, "num"):
            if event.num == 4:
                direction = 1
            elif event.num == 5:
                direction = -1

        if direction == 0:
            return

        self._apply_zoom(self.ZOOM_STEP * direction)

    def zoom_in(self):
        self._apply_zoom(self.ZOOM_STEP)

    def zoom_out(self):
        self._apply_zoom(-self.ZOOM_STEP)

    def reset_zoom(self):
        if self.current_media_kind not in ("image", "video"):
            return
        self.zoom_level = 100
        self.pan_x = 0
        self.pan_y = 0
        self._update_zoom_label()
        if self.current_pil_image is not None:
            self._render_current_image_on_canvas()
        self._refresh_media_info()
        self._set_status("Zoom reset to 100%", 900)

    @staticmethod
    def _format_seconds(seconds: float) -> str:
        seconds = max(0, int(seconds))
        hours, rem = divmod(seconds, 3600)
        minutes, sec = divmod(rem, 60)
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{sec:02d}"
        return f"{minutes:02d}:{sec:02d}"

    def _set_video_progress(self, current_seconds: float, total_seconds: float):
        if total_seconds > 0:
            percent = max(0.0, min(1.0, current_seconds / total_seconds))
        else:
            percent = 0.0
        self.seekbar_percent = percent
        self.seekbar_enabled = total_seconds > 0 and self.current_media_kind in ("audio", "video")
        self._draw_seekbar()
        self.video_time_label.config(
            text=f"{self._format_seconds(current_seconds)} / {self._format_seconds(total_seconds)}"
        )

    def select_logfile(self):
        initialdir = os.path.dirname(self.logfile) if self.logfile else self._preferred_log_directory()
        logfile_path = filedialog.askopenfilename(
            initialdir=initialdir,
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            title="Select Logfile",
        )

        if not logfile_path:
            return

        self.logfile = logfile_path
        self._load_log_entries()
        self._refresh_log_list()
        self._update_logfile_label()
        self.use_last_logfile_button.config(state=tk.NORMAL)
        self._set_status("Logfile loaded", 2500)

    def use_last_logfile(self):
        log_files = self._discover_logfiles(self.loaded_folder_path or self.last_folder_path)

        if not log_files:
            self._set_status("No move_log file found", 2500)
            return

        latest_log_file = max(log_files, key=os.path.getctime)
        self.logfile = latest_log_file
        self._load_log_entries()
        self._refresh_log_list()
        self._update_logfile_label()
        self.use_last_logfile_button.config(state=tk.DISABLED)
        self._set_status("Using latest logfile", 2500)

    def _on_close(self):
        self._cancel_pending_video_prepare()
        self._stop_web_server(clear_status=False)
        self._stop_media()
        self._cancel_overlay_autohide()
        self._cleanup_preview_cache()

        for after_id in (
            self.overlay_content_anim_after_id,
            self.overlay_slide_anim_after_id,
            self.resize_after_id,
            self.preview_resize_settle_after_id,
            self.video_prepare_poll_after_id,
            self.web_server_poll_after_id,
        ):
            if after_id:
                try:
                    self.root.after_cancel(after_id)
                except tk.TclError:
                    pass

        if self.audio_ready:
            try:
                pygame.mixer.quit()
            except pygame.error:
                pass
        try:
            self.background_executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            self.background_executor.shutdown(wait=False)
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = PhotoVideoViewer(root)
    root.mainloop()
