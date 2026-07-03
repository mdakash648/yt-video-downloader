"""
YT-DLP GUI Downloader (modern UI)
-------------------------------
A desktop GUI (Tkinter) wrapper around yt-dlp for normal video downloads.

Features:
- Paste any YouTube (or yt-dlp supported site) URL
- Choose download folder
- Download single video OR full playlist
- Choose video quality (Best / 1080p / 720p / 480p / 360p)
- Optional Audio only (MP3) mode
- Live progress bar + percentage, download speed (KB/s or MB/s), ETA, log window, Stop button
- Modern flat dark UI, fully scrollable on small screens
- Remembers last used download folder automatically

Requirements:
    pip install -U yt-dlp
    ffmpeg must be installed and available in PATH
      - Windows: https://www.gyan.dev/ffmpeg/builds/ (add bin folder to PATH)
      - Linux:   sudo apt install ffmpeg
      - Mac:     brew install ffmpeg

Run:
    python yt_dlp_gui.py
"""

import os
import sys
import json
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

# Config file used to remember the last download location between sessions.
# Stored in the user's home folder so it persists across app updates.
CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".ytdlp_gui_config.json")


def _bundle_base_dirs():
    """Directories to search for a bundled ffmpeg, in priority order."""
    dirs = []
    if getattr(sys, "frozen", False):
        # PyInstaller --onefile extracts bundled binaries to a temp dir (_MEIPASS)
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            dirs.append(meipass)
        # Folder containing the exe itself (covers --onedir builds, or ffmpeg
        # manually placed next to a --onefile exe)
        dirs.append(os.path.dirname(sys.executable))
    else:
        dirs.append(os.path.dirname(os.path.abspath(__file__)))
    return dirs


def find_bundled_ffmpeg():
    """Look for a bundled ffmpeg binary so users never need to install ffmpeg themselves."""
    exe_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    for base in _bundle_base_dirs():
        for sub in ("", "ffmpeg", "bin"):
            folder = os.path.join(base, sub) if sub else base
            if os.path.isfile(os.path.join(folder, exe_name)):
                return folder
    return None


FFMPEG_DIR = find_bundled_ffmpeg()


QUALITY_OPTIONS = {
    "Best available": "bestvideo[ext=mp4]+bestaudio/best/best",
    "1080p": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
    "720p": "bestvideo[height<=720]+bestaudio/best[height<=720]",
    "480p": "bestvideo[height<=480]+bestaudio/best[height<=480]",
    "360p": "bestvideo[height<=360]+bestaudio/best[height<=360]",
}

# ---------------- Color palette (modern dark theme) ----------------
BG_MAIN = "#121417"
BG_CARD = "#1b1f24"
BG_CARD_ALT = "#20252b"
BG_INPUT = "#262c33"
FG_TEXT = "#e6e9ec"
FG_SUBTLE = "#8b96a3"
ACCENT = "#5b8cff"
ACCENT_HOVER = "#4a76e0"
ACCENT_GREEN = "#3ecf8e"
ACCENT_RED = "#ff5c5c"
BORDER = "#2b3138"


def format_speed(bytes_per_sec):
    """Format raw bytes/sec into a human readable string like 512KB/s or 2.3MB/s."""
    if not bytes_per_sec or bytes_per_sec <= 0:
        return "-- KB/s"
    if bytes_per_sec >= 1024 * 1024:
        return f"{bytes_per_sec / (1024 * 1024):.2f} MB/s"
    return f"{bytes_per_sec / 1024:.0f} KB/s"


def format_eta(seconds):
    if seconds is None:
        return "--:--"
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:d}:{s:02d}"


class YTDLPGui(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("YT-DLP Downloader")
        self.geometry("720x680")
        self.minsize(360, 420)
        self.configure(bg=BG_MAIN)

        default_dir = os.path.join(os.path.expanduser("~"), "Downloads")
        self.download_dir = tk.StringVar(value=self._load_last_dir(default_dir))
        self.url_var = tk.StringVar()
        self.playlist_var = tk.BooleanVar(value=False)
        self.select_all_var = tk.BooleanVar(value=True)
        self.playlist_items_var = tk.StringVar(value="")
        self.audio_only_var = tk.BooleanVar(value=False)
        self.embed_thumbnail_var = tk.BooleanVar(value=True)
        self.quality_var = tk.StringVar(value="Best available")
        self.browser_var = tk.StringVar(value="None")
        self.status_var = tk.StringVar(value="Idle")
        self.percent_var = tk.StringVar(value="0%")
        self.speed_var = tk.StringVar(value="-- KB/s")
        self.eta_var = tk.StringVar(value="ETA --:--")
        self.filesize_var = tk.StringVar(value="")
        self.playlist_progress_var = tk.StringVar(value="")

        self.stop_flag = False
        self.worker_thread = None

        self._setup_styles()
        self._build_ui()

        if yt_dlp is None:
            self._log("yt-dlp is not installed. Run: pip install yt-dlp")
            messagebox.showwarning(
                "yt-dlp not found",
                "yt-dlp module not found.\n\nPlease run:\n    pip install yt-dlp\n\nthen restart this app."
            )

        if FFMPEG_DIR:
            self._log(f"ffmpeg found: {FFMPEG_DIR}")
        else:
            self._log("ffmpeg not found (only needed for merging/MP3 conversion; "
                       "packaged .exe builds bundle it automatically).")

    # ---------------- Config persistence ----------------
    def _load_last_dir(self, fallback):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            saved = data.get("last_download_dir", "").strip()
            if saved and os.path.isdir(saved):
                return saved
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
        return fallback

    def _save_last_dir(self):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump({"last_download_dir": self.download_dir.get().strip()}, f)
        except OSError:
            pass

    # ---------------- Styling ----------------
    def _setup_styles(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(".", background=BG_MAIN, foreground=FG_TEXT,
                         font=("Segoe UI", 10))

        style.configure("Card.TFrame", background=BG_CARD)
        style.configure("Main.TFrame", background=BG_MAIN)

        style.configure("CardTitle.TLabel", background=BG_CARD, foreground=FG_SUBTLE,
                         font=("Segoe UI Semibold", 9, "bold"))
        style.configure("Body.TLabel", background=BG_CARD, foreground=FG_TEXT,
                         font=("Segoe UI", 10))
        style.configure("Subtle.TLabel", background=BG_CARD, foreground=FG_SUBTLE,
                         font=("Segoe UI", 9))
        style.configure("Status.TLabel", background=BG_MAIN, foreground=FG_TEXT,
                         font=("Segoe UI", 10, "bold"))
        style.configure("Stat.TLabel", background=BG_MAIN, foreground=ACCENT,
                         font=("Segoe UI", 10, "bold"))
        style.configure("Header.TLabel", background=BG_MAIN, foreground=FG_TEXT,
                         font=("Segoe UI Semibold", 16, "bold"))
        style.configure("SubHeader.TLabel", background=BG_MAIN, foreground=FG_SUBTLE,
                         font=("Segoe UI", 9))

        style.configure("TCheckbutton", background=BG_CARD, foreground=FG_TEXT,
                         font=("Segoe UI", 10))
        style.map("TCheckbutton", background=[("active", BG_CARD)])

        style.configure("TEntry", fieldbackground=BG_INPUT, background=BG_INPUT,
                         foreground=FG_TEXT, insertcolor=FG_TEXT,
                         bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                         padding=8, relief="flat")
        style.map("TEntry", fieldbackground=[("readonly", BG_INPUT)])

        style.configure("TCombobox", fieldbackground=BG_INPUT, background=BG_INPUT,
                         foreground=FG_TEXT, arrowcolor=FG_TEXT, bordercolor=BORDER,
                         padding=6, relief="flat")
        style.map("TCombobox", fieldbackground=[("readonly", BG_INPUT)],
                  foreground=[("readonly", FG_TEXT)])
        self.option_add("*TCombobox*Listbox.background", BG_INPUT)
        self.option_add("*TCombobox*Listbox.foreground", FG_TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT)

        # Primary action button
        style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff",
                         font=("Segoe UI Semibold", 10, "bold"), padding=(18, 10),
                         borderwidth=0, relief="flat")
        style.map("Accent.TButton",
                  background=[("active", ACCENT_HOVER), ("disabled", "#3a4048")],
                  foreground=[("disabled", "#8b96a3")])

        # Secondary / stop button
        style.configure("Stop.TButton", background=BG_INPUT, foreground=FG_TEXT,
                         font=("Segoe UI", 10, "bold"), padding=(18, 10),
                         borderwidth=1, relief="flat")
        style.map("Stop.TButton",
                  background=[("active", "#33393f"), ("disabled", BG_INPUT)],
                  foreground=[("disabled", "#5a626b"), ("!disabled", ACCENT_RED)])

        # Ghost / browse button
        style.configure("Ghost.TButton", background=BG_INPUT, foreground=FG_TEXT,
                         font=("Segoe UI", 9, "bold"), padding=(12, 8), borderwidth=0)
        style.map("Ghost.TButton", background=[("active", "#33393f")])

        # Progress bar
        style.configure("Modern.Horizontal.TProgressbar", troughcolor=BG_INPUT,
                         background=ACCENT, bordercolor=BG_INPUT,
                         lightcolor=ACCENT, darkcolor=ACCENT, thickness=14)

    def _card(self, parent, title=None):
        outer = ttk.Frame(parent, style="Main.TFrame")
        card = tk.Frame(outer, bg=BG_CARD, highlightbackground=BORDER,
                         highlightthickness=1, bd=0)
        card.pack(fill="both", expand=True)
        inner = ttk.Frame(card, style="Card.TFrame")
        inner.pack(fill="both", expand=True, padx=16, pady=14)
        if title:
            ttk.Label(inner, text=title.upper(), style="CardTitle.TLabel").pack(anchor="w", pady=(0, 10))
        return outer, inner

    # ---------------- UI ----------------
    def _build_ui(self):
        # Canvas + scrollbar wrapper so the whole layout is scrollable on small screens
        container = tk.Frame(self, bg=BG_MAIN)
        container.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(container, bg=BG_MAIN, highlightthickness=0, bd=0)
        vscroll = ttk.Scrollbar(container, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vscroll.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        vscroll.pack(side="right", fill="y")

        root = ttk.Frame(self.canvas, style="Main.TFrame")
        self._canvas_window = self.canvas.create_window((0, 0), window=root, anchor="nw")

        def _on_frame_configure(event=None):
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))

        def _on_canvas_configure(event):
            # Keep the inner frame the same width as the visible canvas
            self.canvas.itemconfig(self._canvas_window, width=event.width)

        root.bind("<Configure>", _on_frame_configure)
        self.canvas.bind("<Configure>", _on_canvas_configure)

        def _on_mousewheel(event):
            if event.num == 4:
                self.canvas.yview_scroll(-1, "units")
            elif event.num == 5:
                self.canvas.yview_scroll(1, "units")
            else:
                self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        # Windows / macOS
        self.canvas.bind_all("<MouseWheel>", _on_mousewheel)
        # Linux
        self.canvas.bind_all("<Button-4>", _on_mousewheel)
        self.canvas.bind_all("<Button-5>", _on_mousewheel)

        root.configure(padding=(18, 16))

        # Header
        header = ttk.Frame(root, style="Main.TFrame")
        header.pack(fill="x", pady=(0, 14))
        ttk.Label(header, text="⬇ YT-DLP Downloader", style="Header.TLabel").pack(anchor="w")
        ttk.Label(header, text="Download videos, playlists, or audio from any supported site",
                  style="SubHeader.TLabel").pack(anchor="w", pady=(2, 0))

        # URL card
        url_outer, url_inner = self._card(root, "Video / Playlist URL")
        url_outer.pack(fill="x", pady=(0, 12))
        self.url_entry = ttk.Entry(url_inner, textvariable=self.url_var, style="TEntry")
        self.url_entry.pack(fill="x", ipady=3)
        self.url_entry.bind("<FocusOut>", lambda e: self._maybe_autoload_titles())
        self.url_entry.bind("<Return>", lambda e: self._maybe_autoload_titles())

        # Folder card
        folder_outer, folder_inner = self._card(root, "Download Location")
        folder_outer.pack(fill="x", pady=(0, 12))
        frow = ttk.Frame(folder_inner, style="Card.TFrame")
        frow.pack(fill="x")
        ttk.Entry(frow, textvariable=self.download_dir, style="TEntry").pack(side="left", fill="x", expand=True, ipady=3)
        ttk.Button(frow, text="Browse", style="Ghost.TButton",
                   command=self._choose_folder).pack(side="left", padx=(10, 0))

        # Options card
        opt_outer, opt_inner = self._card(root, "Options")
        opt_outer.pack(fill="x", pady=(0, 12))

        ttk.Checkbutton(opt_inner, text="Download entire playlist",
                         variable=self.playlist_var,
                         command=self._on_playlist_toggle).pack(anchor="w", pady=(0, 6))

        # Playlist video-selection sub-panel (only meaningful when playlist mode is on)
        self.playlist_select_frame = ttk.Frame(opt_inner, style="Card.TFrame")
        self.playlist_select_frame.pack(fill="x", padx=(22, 0), pady=(0, 10))

        self.select_all_check = ttk.Checkbutton(
            self.playlist_select_frame, text="All videos",
            variable=self.select_all_var, command=self._sync_playlist_controls
        )
        self.select_all_check.pack(anchor="w")

        items_row = ttk.Frame(self.playlist_select_frame, style="Card.TFrame")
        items_row.pack(fill="x", pady=(4, 0))
        ttk.Label(items_row, text="Or select videos:", style="Subtle.TLabel").pack(side="left")
        self.playlist_items_entry = ttk.Entry(items_row, textvariable=self.playlist_items_var, style="TEntry")
        self.playlist_items_entry.pack(side="left", fill="x", expand=True, padx=(8, 0), ipady=2)
        ttk.Label(self.playlist_select_frame, text="e.g. 38  or  1,3,4,6  or  5-10,15",
                  style="Subtle.TLabel").pack(anchor="w", pady=(3, 0))

        ttk.Checkbutton(opt_inner, text="Audio only (save as MP3)",
                         variable=self.audio_only_var,
                         command=self._toggle_quality_state).pack(anchor="w", pady=(0, 6))

        ttk.Checkbutton(opt_inner, text="🖼 Embed video thumbnail as poster/cover art",
                         variable=self.embed_thumbnail_var).pack(anchor="w", pady=(0, 10))

        grid = ttk.Frame(opt_inner, style="Card.TFrame")
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        grid.columnconfigure(3, weight=1)

        ttk.Label(grid, text="Video quality", style="Subtle.TLabel").grid(row=0, column=0, sticky="w")
        self.quality_combo = ttk.Combobox(
            grid, textvariable=self.quality_var, values=list(QUALITY_OPTIONS.keys()),
            state="readonly", width=18
        )
        self.quality_combo.grid(row=1, column=0, sticky="w", pady=(4, 0))

        ttk.Label(grid, text="Cookies from browser", style="Subtle.TLabel").grid(row=0, column=2, sticky="w", padx=(20, 0))
        self.browser_combo = ttk.Combobox(
            grid, textvariable=self.browser_var,
            values=["None", "firefox", "edge", "chrome", "brave", "opera", "vivaldi"],
            state="readonly", width=14
        )
        self.browser_combo.grid(row=1, column=2, sticky="w", padx=(20, 0), pady=(4, 0))

        # Playlist Videos card - shows video titles so the user can tick which ones to grab.
        # This works alongside (not instead of) the manual "Or select videos" text entry above.
        pv_outer, pv_inner = self._card(root, "Playlist Videos")
        pv_outer.pack(fill="x", pady=(0, 12))

        pv_top = ttk.Frame(pv_inner, style="Card.TFrame")
        pv_top.pack(fill="x")
        self.load_titles_btn = ttk.Button(pv_top, text="🔄 Load / Refresh Titles", style="Ghost.TButton",
                                           command=self._manual_load_titles)
        self.load_titles_btn.pack(side="left")

        self.titles_status_var = tk.StringVar(
            value="Tick 'Download entire playlist' and add a URL to load video titles here."
        )
        ttk.Label(pv_top, textvariable=self.titles_status_var, style="Subtle.TLabel").pack(
            side="left", padx=(10, 0)
        )

        pv_actions = ttk.Frame(pv_inner, style="Card.TFrame")
        pv_actions.pack(fill="x", pady=(8, 0))
        self.select_all_titles_btn = ttk.Button(pv_actions, text="Select All", style="Ghost.TButton",
                                                  command=lambda: self._set_all_title_checks(True))
        self.select_all_titles_btn.pack(side="left")
        self.select_all_titles_btn.config(state="disabled")
        self.deselect_all_titles_btn = ttk.Button(pv_actions, text="Deselect All", style="Ghost.TButton",
                                                    command=lambda: self._set_all_title_checks(False))
        self.deselect_all_titles_btn.pack(side="left", padx=(8, 0))
        self.deselect_all_titles_btn.config(state="disabled")

        self.titles_list_frame = ttk.Frame(pv_inner, style="Card.TFrame")
        self.titles_list_frame.pack(fill="x", pady=(8, 0))
        self.video_check_vars = []

        # Action buttons
        btn_frame = ttk.Frame(root, style="Main.TFrame")
        btn_frame.pack(fill="x", pady=(4, 14))
        self.download_btn = ttk.Button(btn_frame, text="⬇  Download", style="Accent.TButton",
                                        command=self._start_download)
        self.download_btn.pack(side="left")
        self.stop_btn = ttk.Button(btn_frame, text="■  Stop", style="Stop.TButton",
                                    command=self._stop_download, state="disabled")
        self.stop_btn.pack(side="left", padx=(10, 0))

        # Progress card
        prog_outer, prog_inner = self._card(root, "Progress")
        prog_outer.pack(fill="x", pady=(0, 12))

        top_row = ttk.Frame(prog_inner, style="Card.TFrame")
        top_row.pack(fill="x")
        ttk.Label(top_row, textvariable=self.status_var, style="Body.TLabel").pack(side="left")
        ttk.Label(top_row, textvariable=self.percent_var, style="Body.TLabel").pack(side="right")

        self.progress = ttk.Progressbar(prog_inner, orient="horizontal", mode="determinate",
                                         style="Modern.Horizontal.TProgressbar")
        self.progress.pack(fill="x", pady=(8, 10))

        stats_row = ttk.Frame(prog_inner, style="Card.TFrame")
        stats_row.pack(fill="x")

        playlist_box = ttk.Frame(stats_row, style="Card.TFrame")
        playlist_box.pack(side="left")
        ttk.Label(playlist_box, text="🎬 VIDEO", style="Subtle.TLabel").pack(anchor="w")
        ttk.Label(playlist_box, textvariable=self.playlist_progress_var, style="Stat.TLabel",
                  background=BG_CARD).pack(anchor="w")

        speed_box = ttk.Frame(stats_row, style="Card.TFrame")
        speed_box.pack(side="left", padx=(30, 0))
        ttk.Label(speed_box, text="⚡ SPEED", style="Subtle.TLabel").pack(anchor="w")
        ttk.Label(speed_box, textvariable=self.speed_var, style="Stat.TLabel",
                  background=BG_CARD).pack(anchor="w")

        eta_box = ttk.Frame(stats_row, style="Card.TFrame")
        eta_box.pack(side="left", padx=(30, 0))
        ttk.Label(eta_box, text="⏱ ETA", style="Subtle.TLabel").pack(anchor="w")
        ttk.Label(eta_box, textvariable=self.eta_var, style="Stat.TLabel",
                  background=BG_CARD).pack(anchor="w")

        size_box = ttk.Frame(stats_row, style="Card.TFrame")
        size_box.pack(side="left", padx=(30, 0))
        ttk.Label(size_box, text="📦 SIZE", style="Subtle.TLabel").pack(anchor="w")
        ttk.Label(size_box, textvariable=self.filesize_var, style="Stat.TLabel",
                  background=BG_CARD).pack(anchor="w")

        # Log card
        log_outer, log_inner = self._card(root, "Log")
        log_outer.pack(fill="both", expand=True)
        log_wrap = tk.Frame(log_inner, bg=BG_INPUT, highlightbackground=BORDER, highlightthickness=1)
        log_wrap.pack(fill="both", expand=True)
        self.log_box = tk.Text(log_wrap, height=8, wrap="word", state="disabled",
                                bg=BG_INPUT, fg=FG_TEXT, insertbackground=FG_TEXT,
                                bd=0, padx=10, pady=8, font=("Consolas", 9))
        self.log_box.pack(fill="both", expand=True)

        self._sync_playlist_controls()

    def _cookies_opt(self):
        browser = self.browser_var.get()
        if browser and browser != "None":
            return {"cookiesfrombrowser": (browser,)}
        return {}

    def _ffmpeg_opt(self):
        if FFMPEG_DIR:
            return {"ffmpeg_location": FFMPEG_DIR}
        return {}

    def _thumbnail_opts(self):
        """Download the video's thumbnail and embed it as poster/cover art in the output file."""
        if not self.embed_thumbnail_var.get():
            return {}, []
        extra_opts = {"writethumbnail": True}
        postprocessors = [
            {"key": "FFmpegThumbnailsConvertor", "format": "jpg"},
            {"key": "EmbedThumbnail"},
        ]
        return extra_opts, postprocessors

    def _toggle_quality_state(self):
        self.quality_combo.config(state="disabled" if self.audio_only_var.get() else "readonly")

    def _sync_playlist_controls(self):
        """Enable/disable the video-selection controls based on playlist + All-videos state."""
        is_playlist = self.playlist_var.get()
        self.select_all_check.config(state="normal" if is_playlist else "disabled")

        entry_enabled = is_playlist and not self.select_all_var.get()
        self.playlist_items_entry.config(state="normal" if entry_enabled else "disabled")

        # Keep the title checkbox list visually consistent with "All videos"
        if self.select_all_var.get():
            for _idx, var in self.video_check_vars:
                if not var.get():
                    var.set(True)

    def _playlist_items_opt(self):
        """Return yt-dlp opts to restrict which playlist items get downloaded."""
        if not self.playlist_var.get():
            return {}
        if self.select_all_var.get():
            return {}
        items = self.playlist_items_var.get().strip()
        if not items:
            return {}
        return {"playlist_items": items}

    # ---------------- Playlist title list ----------------
    def _on_playlist_toggle(self):
        self._sync_playlist_controls()
        if self.playlist_var.get():
            url = self.url_var.get().strip()
            if url:
                self._load_playlist_titles(url)
            else:
                self.titles_status_var.set("Add a playlist URL, then click 'Load / Refresh Titles'.")
        else:
            self.titles_status_var.set("Tick 'Download entire playlist' and add a URL to load video titles here.")

    def _maybe_autoload_titles(self):
        if self.playlist_var.get():
            url = self.url_var.get().strip()
            if url:
                self._load_playlist_titles(url)

    def _manual_load_titles(self):
        if yt_dlp is None:
            messagebox.showerror("Missing dependency", "Please install yt-dlp first:\n\npip install yt-dlp")
            return
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("No URL", "Please paste a playlist URL first.")
            return
        if not self.playlist_var.get():
            self.playlist_var.set(True)
            self._sync_playlist_controls()
        self._load_playlist_titles(url)

    def _clear_titles_list(self):
        for child in self.titles_list_frame.winfo_children():
            child.destroy()
        self.video_check_vars = []

    def _load_playlist_titles(self, url):
        if yt_dlp is None:
            return
        self.titles_status_var.set("Loading playlist titles...")
        self.load_titles_btn.config(state="disabled")
        self.select_all_titles_btn.config(state="disabled")
        self.deselect_all_titles_btn.config(state="disabled")
        self._clear_titles_list()
        threading.Thread(target=self._fetch_playlist_titles_worker, args=(url,), daemon=True).start()

    def _fetch_playlist_titles_worker(self, url):
        try:
            opts = {
                "extract_flat": True,
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                **self._cookies_opt(),
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            entries = (info or {}).get("entries")
            if not entries:
                self.after(0, lambda: self._on_titles_loaded([], "This doesn't look like a playlist URL."))
                return
            titles = []
            for idx, entry in enumerate(entries, start=1):
                if not entry:
                    continue
                title = entry.get("title") or f"Video {idx}"
                titles.append((idx, title))
            self.after(0, lambda: self._on_titles_loaded(titles, None))
        except Exception as ex:
            err = str(ex)
            self.after(0, lambda: self._on_titles_loaded([], f"Could not load titles: {err}"))

    def _on_titles_loaded(self, titles, error):
        self.load_titles_btn.config(state="normal")
        self._clear_titles_list()

        if error:
            self.titles_status_var.set(error)
            self._log(error)
            return
        if not titles:
            self.titles_status_var.set("No videos found in this playlist.")
            return

        self.titles_status_var.set(f"{len(titles)} videos found — tick the ones you want, or use the text box above.")
        self.select_all_titles_btn.config(state="normal")
        self.deselect_all_titles_btn.config(state="normal")

        # Pre-check boxes based on any existing manual selection, otherwise check everything
        preselected = None
        if not self.select_all_var.get() and self.playlist_items_var.get().strip():
            preselected = self._parse_playlist_items(self.playlist_items_var.get())

        for idx, title in titles:
            checked = True if preselected is None else (idx in preselected)
            var = tk.BooleanVar(value=checked)
            cb = ttk.Checkbutton(self.titles_list_frame, text=f"{idx}. {title}",
                                  variable=var, command=self._on_title_check_changed)
            cb.pack(anchor="w", pady=1)
            self.video_check_vars.append((idx, var))

    def _set_all_title_checks(self, value):
        for _idx, var in self.video_check_vars:
            var.set(value)
        self._on_title_check_changed()

    def _on_title_check_changed(self):
        if not self.video_check_vars:
            return
        total = len(self.video_check_vars)
        selected = [idx for idx, var in self.video_check_vars if var.get()]

        if len(selected) == total:
            self.select_all_var.set(True)
            self.playlist_items_var.set("")
        elif not selected:
            # Nothing ticked: fall back to "All videos" off with an empty (invalid) selection,
            # user must tick at least one or Select All before downloading.
            self.select_all_var.set(False)
            self.playlist_items_var.set("")
        else:
            self.select_all_var.set(False)
            self.playlist_items_var.set(self._compress_indices(selected))

        self._sync_playlist_controls()

    @staticmethod
    def _compress_indices(indices):
        """Turn [1,2,3,5,7,8,9] into '1-3,5,7-9' for yt-dlp's playlist_items option."""
        if not indices:
            return ""
        indices = sorted(indices)
        ranges = []
        start = prev = indices[0]
        for n in indices[1:]:
            if n == prev + 1:
                prev = n
                continue
            ranges.append(f"{start}-{prev}" if start != prev else f"{start}")
            start = prev = n
        ranges.append(f"{start}-{prev}" if start != prev else f"{start}")
        return ",".join(ranges)

    @staticmethod
    def _parse_playlist_items(text):
        """Parse '1,3,4,6-10' style text into a set of integers."""
        result = set()
        text = (text or "").strip()
        if not text:
            return result
        for part in text.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                try:
                    a, b = part.split("-", 1)
                    a, b = int(a), int(b)
                    result.update(range(min(a, b), max(a, b) + 1))
                except ValueError:
                    continue
            else:
                try:
                    result.add(int(part))
                except ValueError:
                    continue
        return result

    def _choose_folder(self):
        folder = filedialog.askdirectory(initialdir=self.download_dir.get())
        if folder:
            self.download_dir.set(folder)
            self._save_last_dir()

    def _log(self, msg):
        self.log_box.config(state="normal")
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.config(state="disabled")

    def _reset_stats(self):
        self.speed_var.set("-- KB/s")
        self.eta_var.set("ETA --:--")
        self.filesize_var.set("")
        self.percent_var.set("0%")
        self.playlist_progress_var.set("--")

    # ---------------- Download logic ----------------
    def _start_download(self):
        if yt_dlp is None:
            messagebox.showerror("Missing dependency", "Please install yt-dlp first:\n\npip install yt-dlp")
            return

        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("No URL", "Please paste a video or playlist URL.")
            return

        out_dir = self.download_dir.get().strip()
        if not out_dir:
            messagebox.showwarning("No folder", "Please choose a download location.")
            return

        if self.playlist_var.get() and not self.select_all_var.get() and not self.playlist_items_var.get().strip():
            messagebox.showwarning(
                "No videos selected",
                "Please enter which videos to download (e.g. 38 or 1,3,4,6-10),\n"
                "or tick 'All videos' to download the whole playlist."
            )
            return

        os.makedirs(out_dir, exist_ok=True)
        self._save_last_dir()

        self.stop_flag = False
        self.download_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.progress["value"] = 0
        self._reset_stats()
        self.status_var.set("Starting...")

        if self.playlist_var.get():
            if self.select_all_var.get():
                self._log("Playlist mode: downloading all videos.")
            else:
                self._log(f"Playlist mode: downloading selected videos -> {self.playlist_items_var.get().strip()}")

        self.worker_thread = threading.Thread(
            target=self._run_download, args=(url, out_dir), daemon=True
        )
        self.worker_thread.start()

    def _stop_download(self):
        self.stop_flag = True
        self.status_var.set("Stopping...")
        self._log("Stop requested. It will halt after the current step.")

    def _progress_hook(self, d):
        if self.stop_flag:
            raise yt_dlp.utils.DownloadError("Cancelled by user")

        info = d.get("info_dict", {}) or {}
        pl_index = d.get("playlist_index") or info.get("playlist_index")
        pl_count = (d.get("playlist_count") or info.get("playlist_count")
                    or info.get("n_entries") or info.get("playlist_n_entries"))

        if pl_index and pl_count:
            self.playlist_progress_var.set(f"{pl_index}/{pl_count}")
            playlist_tag = f"({pl_index}/{pl_count}) "
        elif pl_index:
            self.playlist_progress_var.set(f"{pl_index}")
            playlist_tag = f"({pl_index}) "
        else:
            self.playlist_progress_var.set("--")
            playlist_tag = ""

        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes", 0)
            filename = os.path.basename(d.get("filename", ""))
            speed = d.get("speed")  # bytes/sec, provided by yt-dlp
            eta = d.get("eta")

            self.speed_var.set(format_speed(speed))
            self.eta_var.set(f"ETA {format_eta(eta)}")

            if total:
                percent = downloaded / total * 100
                self.progress["value"] = percent
                self.percent_var.set(f"{percent:.1f}%")
                self.filesize_var.set(f"{downloaded / (1024*1024):.1f} / {total / (1024*1024):.1f} MB")
                self.status_var.set(f"Downloading {playlist_tag}{filename}")
            else:
                self.filesize_var.set(f"{downloaded / (1024*1024):.1f} MB")
                self.status_var.set(f"Downloading {playlist_tag}{filename}...")
        elif d["status"] == "finished":
            self.status_var.set(f"Processing {playlist_tag}(merging/converting)...")
            self.speed_var.set("-- KB/s")
            self.eta_var.set("ETA --:--")
            self._log(f"Finished downloading: {os.path.basename(d.get('filename', ''))}")

    def _run_download(self, url, out_dir):
        try:
            if self.audio_only_var.get():
                self._download_audio_only(url, out_dir)
            else:
                self._download_video(url, out_dir)

            if self.stop_flag:
                self.status_var.set("Stopped.")
                self._log("Download stopped by user.")
            else:
                self.status_var.set("Done!")
                self.progress["value"] = 100
                self.percent_var.set("100%")
                self.speed_var.set("-- KB/s")
                self.eta_var.set("ETA --:--")
                self._log("Download completed successfully.")
        except Exception as e:
            if self.stop_flag:
                self.status_var.set("Stopped.")
                self._log("Download stopped by user.")
            else:
                self.status_var.set("Error occurred.")
                self._log(f"Error: {e}")
                messagebox.showerror("Download error", str(e))
        finally:
            self.download_btn.config(state="normal")
            self.stop_btn.config(state="disabled")

    def _outtmpl(self, out_dir):
        if self.playlist_var.get():
            # Numbers files in playlist order: "1. Title.ext", "2. Title.ext", ...
            return os.path.join(out_dir, "%(playlist_index)s. %(title)s.%(ext)s")
        return os.path.join(out_dir, "%(title)s.%(ext)s")

    def _download_video(self, url, out_dir):
        fmt = QUALITY_OPTIONS.get(self.quality_var.get(), "bestvideo[ext=mp4]+bestaudio/best/best")
        thumb_opts, thumb_postprocessors = self._thumbnail_opts()

        ydl_opts = {
            "outtmpl": self._outtmpl(out_dir),
            "noplaylist": not self.playlist_var.get(),
            "progress_hooks": [self._progress_hook],
            "format": fmt,
            "merge_output_format": "mp4",
            "postprocessors": thumb_postprocessors,
            "ignoreerrors": True,
            "quiet": True,
            "no_warnings": True,
            **self._cookies_opt(),
            **self._playlist_items_opt(),
            **self._ffmpeg_opt(),
            **thumb_opts,
        }

        self._log(f"Format selector: {fmt}")
        if self.embed_thumbnail_var.get():
            self._log("Embedding video thumbnail as poster/cover art.")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

    def _download_audio_only(self, url, out_dir):
        thumb_opts, thumb_postprocessors = self._thumbnail_opts()
        ydl_opts = {
            "outtmpl": self._outtmpl(out_dir),
            "noplaylist": not self.playlist_var.get(),
            "progress_hooks": [self._progress_hook],
            "format": "bestaudio/best",
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                },
                *thumb_postprocessors,
            ],
            "ignoreerrors": True,
            "quiet": True,
            "no_warnings": True,
            **self._cookies_opt(),
            **self._playlist_items_opt(),
            **self._ffmpeg_opt(),
            **thumb_opts,
        }
        if self.embed_thumbnail_var.get():
            self._log("Embedding video thumbnail as MP3 cover art.")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])


if __name__ == "__main__":
    app = YTDLPGui()
    app.mainloop()