"""
YT-DLP GUI Downloader (modern UI)
-------------------------------
A desktop GUI (Tkinter) wrapper around yt-dlp for normal video downloads.

Features:
- Paste any YouTube (or yt-dlp supported site) URL
- Choose download folder
- Download single video OR full playlist
- Batch mode: paste multiple URLs (one per line) into a textarea and queue
  them all up to download one after another, with per-URL ⏳/⬇/✅/❌ status
- Choose video quality (Best / 1080p / 720p / 480p / 360p)
- Optional Audio only (MP3) mode
- Live progress bar + percentage, download speed (KB/s or MB/s), ETA, log window, Stop button
- Pause / Resume: pause a running download and resume it later from the same
  point (via yt-dlp's continuedl range-resume) instead of starting over
- Per-video playlist status icons: each title shows ⏳ pending / ⬇ downloading /
  ✅ done / ❌ failed
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
import re
import datetime
import sys
import json
import shutil
import socket
import subprocess
import threading
import urllib.request
import urllib.error
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    import winsound  # Windows-only; used for the IDM-style completion beep
except ImportError:
    winsound = None

try:
    import yt_dlp
    from yt_dlp.utils import sanitize_filename
except ImportError:
    yt_dlp = None
    sanitize_filename = None

# Matches youtube.com/watch, youtu.be, shorts, playlist and live links,
# with or without scheme/www - used for clipboard auto-detect + paste.
YOUTUBE_URL_RE = re.compile(
    r'(https?://)?(www\.|m\.|music\.)?'
    r'(youtube\.com/(watch\?v=|shorts/|playlist\?list=|live/)|youtu\.be/)[\w\-]+',
    re.IGNORECASE
)

# Config file used to remember the last download location between sessions.
# Stored in the user's home folder so it persists across app updates.
CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".ytdlp_gui_config.json")
HISTORY_FILE = os.path.join(os.path.expanduser("~"), ".ytdlp_gui_history.json")


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


def find_bundled_aria2c():
    """Look for a bundled aria2c binary (project\\aria2c\\aria2c.exe) so users
    never need aria2c installed / on their system PATH. Returns the FULL
    PATH to the exe (not just the folder), or None if not found -- in
    which case we fall back to a plain PATH lookup."""
    exe_name = "aria2c.exe" if os.name == "nt" else "aria2c"
    for base in _bundle_base_dirs():
        for sub in ("", "aria2c", "bin"):
            folder = os.path.join(base, sub) if sub else base
            candidate = os.path.join(folder, exe_name)
            if os.path.isfile(candidate):
                return candidate
    return None


FFMPEG_DIR = find_bundled_ffmpeg()
ARIA2C_PATH = find_bundled_aria2c()


BEST_AVAILABLE_LABEL = "Best available"
BEST_AVAILABLE_FORMAT = "bestvideo[ext=mp4]+bestaudio/best/best"

# Shown in the quality dropdown before any real format data has been fetched
# for the pasted URL (or if the fetch fails) -- replaced by the video's/
# playlist's actual available resolutions as soon as they're detected.
DEFAULT_QUALITY_LABELS = [BEST_AVAILABLE_LABEL, "1080p", "720p", "480p", "360p", "240p", "144p"]


def height_label(height):
    """Turn a raw pixel height into a dropdown label, e.g. 2160 -> '4K', 1080 -> '1080p'."""
    if height >= 2160:
        return "4K"
    return f"{height}p"


def label_to_height(label):
    """Reverse of height_label -- turns a dropdown label back into a target
    pixel height for the format selector. Returns a very large number for
    'Best available' so every available height counts as a candidate."""
    if label == BEST_AVAILABLE_LABEL:
        return 999999
    if label.upper() == "4K":
        return 2160
    digits = "".join(ch for ch in label if ch.isdigit())
    return int(digits) if digits else 999999


def build_format_selector(height):
    """yt-dlp format selector capped at `height`, with an automatic fallback
    to that video's lowest available quality (worst) when nothing is <=
    height -- this is what makes 'download at least X but no lower than
    whatever exists' work per-video across a whole playlist in one string,
    since yt-dlp evaluates the selector separately for each video."""
    return (f"bestvideo[height<={height}]+bestaudio/best[height<={height}]"
            f"/worstvideo+bestaudio/worst")


def format_filesize(num_bytes):
    """Human readable filesize with the unit auto-picked from KB/MB/GB
    (e.g. 150KB, 512MB, 1.4GB) -- bumps to the next unit once the value
    would round to >= 1024 of the current one, so 1024MB shows as 1GB
    instead of 1024.0MB. Returns '' for unknown/zero size."""
    if not num_bytes or num_bytes <= 0:
        return ""
    kb = num_bytes / 1024
    mb = kb / 1024
    gb = mb / 1024
    if gb >= 1 or round(mb, 1) >= 1024:
        return f"{gb:.2f} GB"
    if mb >= 1 or round(kb, 0) >= 1024:
        return f"{mb:.1f} MB"
    return f"{kb:.0f} KB"

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


def parse_speed_limit(value_text, unit):
    """Turn a plain number (e.g. '5') plus a unit ('KB/s' or 'MB/s') into raw
    bytes/sec for yt-dlp's `ratelimit` option. Returns None if blank/invalid/zero
    (meaning: no limit / unlimited speed)."""
    if not value_text:
        return None
    value_text = value_text.strip()
    if not value_text:
        return None
    try:
        value = float(value_text)
    except ValueError:
        return None
    if value <= 0:
        return None
    multiplier = 1024 * 1024 if unit == "MB/s" else 1024
    return int(value * multiplier)


def format_eta(seconds):
    if seconds is None:
        return "--:--"
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:d}:{s:02d}"


def parse_m3u_text(text):
    """
    Parse M3U/M3U8 playlist content (raw text, from a local file OR fetched
    from a URL) and extract media entries.

    Returns: List of dicts with structure:
    {
        'title': str,
        'url': str,
        'referer': str or None,
        'user_agent': str or None,
        'group': str or None,
        'logo': str or None
    }
    """
    entries = []
    lines = [line.rstrip('\r\n') for line in text.splitlines()]

    current_entry = {}

    for i, line in enumerate(lines):
        line_stripped = line.strip()
        if not line_stripped:
            continue

        if line_stripped.startswith('#EXTM3U'):
            continue

        if line_stripped.startswith('#EXTINF:'):
            # Reset current entry
            current_entry = {}
            # Extract tvg-logo
            logo_match = re.search(r'tvg-logo="([^"]*)"', line_stripped)
            if logo_match:
                current_entry['logo'] = logo_match.group(1)
            # Extract group-title
            group_match = re.search(r'group-title="([^"]*)"', line_stripped)
            if group_match:
                current_entry['group'] = group_match.group(1)
            # Extract title (after comma)
            if ',' in line_stripped:
                title = line_stripped.split(',', 1)[1].strip()
                current_entry['title'] = title
            else:
                current_entry['title'] = f"Media {i+1}"

        elif line_stripped.startswith('#EXTVLCOPT:http-referrer='):
            current_entry['referer'] = line_stripped.split('=', 1)[1].strip()

        elif line_stripped.startswith('#EXTVLCOPT:http-user-agent='):
            current_entry['user_agent'] = line_stripped.split('=', 1)[1].strip()

        elif line_stripped.startswith('#'):
            # Ignore other comment lines (including auto_search_update)
            continue

        else:
            # This is a media URL line
            if line_stripped.startswith('http'):
                current_entry['url'] = line_stripped
                # Set defaults for missing fields
                current_entry.setdefault('title', f"Media {len(entries)+1}")
                current_entry.setdefault('referer', None)
                current_entry.setdefault('user_agent', None)
                current_entry.setdefault('group', "Default")
                current_entry.setdefault('logo', None)
                entries.append(current_entry.copy())
                current_entry = {}

    return entries


def parse_m3u_file(filepath):
    """Read a local M3U/M3U8 file and parse it via parse_m3u_text."""
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            text = f.read()
    except Exception as e:
        raise Exception(f"M3U file পড়তে সমস্যা হয়েছে: {e}")
    return parse_m3u_text(text)


def fetch_m3u_from_url(url, timeout=20):
    """Fetch raw text from an M3U playlist URL. Also works with URLs that
    aren't a plain .m3u file link but instead an API/page endpoint whose
    response body IS the M3U-formatted text (e.g. some Vercel-hosted
    playlist generators) -- we just GET it and check the content looks
    like M3U, regardless of the URL's extension or Content-Type."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raise Exception(f"HTTP {e.code} error — URL থেকে data আনা যায়নি।")
    except urllib.error.URLError as e:
        raise Exception(f"Network error — URL-এ পৌঁছানো যায়নি: {e.reason}")
    except Exception as e:
        raise Exception(f"URL fetch ব্যর্থ: {e}")

    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        text = raw.decode('latin-1', errors='ignore')

    if '#EXTM3U' not in text and '#EXTINF' not in text:
        raise Exception("এই URL থেকে valid M3U playlist content পাওয়া যায়নি "
                         "(কোনো #EXTM3U / #EXTINF tag খুঁজে পাওয়া যায়নি)।")

    return text


def sanitize_folder_name(name):
    """Sanitize folder name for filesystem."""
    name = re.sub(r'[<>:"/\\|?*]', '', name or "")
    name = re.sub(r'\s+', ' ', name)
    return name.strip() or "Default"


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
        self.playlist_numbering_var = tk.BooleanVar(value=True)
        self.audio_only_var = tk.BooleanVar(value=False)
        self.embed_thumbnail_var = tk.BooleanVar(value=True)
        self.quality_var = tk.StringVar(value="Best available")
        self.browser_var = tk.StringVar(value="None")
        self.speed_limit_value_var = tk.StringVar(value="")  # plain number, e.g. "5"
        self.speed_limit_unit_var = tk.StringVar(value="MB/s")  # KB/s or MB/s
        self.schedule_enabled_var = tk.BooleanVar(value=False)
        self.schedule_time_var = tk.StringVar(value=datetime.datetime.now().strftime("%H:%M"))
        self.auto_shutdown_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Idle")
        self.percent_var = tk.StringVar(value="0%")
        self.speed_var = tk.StringVar(value="-- KB/s")
        self.eta_var = tk.StringVar(value="ETA --:--")
        self.filesize_var = tk.StringVar(value="")
        self.playlist_progress_var = tk.StringVar(value="")

        # Batch / multiple-URL queue state
        self.batch_mode_var = tk.BooleanVar(value=False)
        self.batch_queue = []  # [{"url":..., "status_var": tk.StringVar()}, ...]
        self._batch_current_index = None

        self.stop_flag = False
        self.pause_flag = False
        self.worker_thread = None
        self._retry_suffix = ""  # e.g. " (1)" when user chooses "Download Again"
        self._last_clipboard = ""
        self._loaded_titles = []  # [(idx, title), ...] from the most recent playlist load
        self._current_output_file = None  # final downloaded file path (for the "Play" button)
        self._paused_context = None  # {"url":..., "out_dir":...} saved when paused, for Resume
        self.video_status_vars = {}  # idx -> tk.StringVar() holding the status icon per playlist row
        self._current_downloading_idx = None  # playlist index currently being downloaded (for pause/resume)
        self._current_aria2c_rpc_port = None  # set by _fast_download_opt() when aria2c is in use

        # ---- Dynamic format detection / size estimate state ----
        self.video_size_vars = {}  # idx -> tk.StringVar() with per-video "720p · ≈120 MB" label
        self._single_format_heights = {}   # {height:int -> filesize_bytes or None} for single-video mode
        self._single_audio_size = None     # best audio-only track size (bytes) for single-video mode
        self._playlist_format_heights = {}  # idx -> {height:int -> filesize_bytes or None}
        self._playlist_audio_size = {}      # idx -> best audio-only track size (bytes)
        self._format_fetch_generation = 0   # bumped on every new URL so stale background fetches self-cancel
        self.estimated_size_var = tk.StringVar(value="")            # shown next to the Download button
        self.estimated_size_progress_var = tk.StringVar(value="--")  # shown in the Progress card

        # ---- Tab 2: Direct / M3U Downloader state ----
        self.direct_url_var = tk.StringVar()
        self.direct_referer_var = tk.StringVar()
        self.direct_user_agent_var = tk.StringVar(
            value="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        # Fast/multi-connection download for direct CDN/ISP-FTP links (Tab 2 only).
        # These are single continuous files (not HLS/DASH), so speed comes from
        # opening several parallel range-request connections to the same URL --
        # exactly what aria2c or yt-dlp's chunked+concurrent native downloader do.
        self.fast_download_var = tk.BooleanVar(value=False)
        self.fast_download_connections_var = tk.StringVar(value="16")
        self.m3u_file_path_var = tk.StringVar()
        self.m3u_url_var = tk.StringVar()
        self.m3u_entries = []          # list of parsed M3U entries (dicts)
        self.m3u_check_vars = []       # [tk.BooleanVar, ...] per entry
        self.m3u_status_vars = {}      # idx -> tk.StringVar for status icon
        self._m3u_group_names = []     # listbox index -> group name (for the group filter)
        self.m3u_status_label_var = tk.StringVar(value="M3U file browse করে Parse করুন — entries এখানে দেখা যাবে।")
        self._current_m3u_index = None
        self._tab_canvases = []        # populated in _build_ui, used by mousewheel handler

        self._setup_styles()
        self._build_ui()

        # Ctrl+R -> refresh the app (acts like a fresh restart)
        self.bind_all("<Control-r>", self._on_refresh_shortcut)
        self.bind_all("<Control-R>", self._on_refresh_shortcut)

        # Watch the clipboard so a copied YouTube link auto-fills the URL box
        self._start_clipboard_watcher()

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

        if ARIA2C_PATH:
            self._log(f"aria2c found: {ARIA2C_PATH}")
        else:
            self._log("aria2c not found (optional, only needed for Fast Download; "
                       "packaged .exe builds bundle it automatically).")

        # Auto-check for a newer yt-dlp version shortly after opening (quiet
        # unless an update is actually available -- see _check_ytdlp_update).
        self.after(1500, lambda: self._check_ytdlp_update(manual=False))

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

    def _save_to_history(self, title, url, filepath):
        """Append a successful download record to the history JSON file."""
        if not title or not url:
            return
        new_entry = {
            "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "title": title,
            "url": url,
            "path": filepath or ""
        }
        try:
            history = []
            if os.path.exists(HISTORY_FILE):
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    history = json.load(f)
            # Safety net: don't log an exact repeat (same url + path) of the
            # most recent entry -- guards against any duplicate call site
            # writing the same completed download twice.
            if history:
                last = history[-1]
                if last.get("url") == new_entry["url"] and last.get("path") == new_entry["path"]:
                    return
            history.append(new_entry)
            # Keep only the last 500 entries to avoid bloating
            if len(history) > 500:
                history = history[-500:]
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(history, f, indent=4, ensure_ascii=False)
        except Exception as e:
            self._log(f"History save failed: {e}")

    def _load_history(self):
        """Populate the history Treeview from the JSON file."""
        if not hasattr(self, "history_tree"):
            return
        for iid in self.history_tree.get_children():
            self.history_tree.delete(iid)
        try:
            if os.path.exists(HISTORY_FILE):
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    history = json.load(f)
                for entry in reversed(history):
                    self.history_tree.insert(
                        "", "end",
                        values=(entry.get("date"), entry.get("title"), entry.get("path"))
                    )
        except Exception as e:
            self._log(f"History load failed: {e}")

    def _clear_history(self):
        """Delete history file and clear the Treeview."""
        if not messagebox.askyesno("Clear History", "আপনি কি নিশ্চিতভাবে সব হিস্টোরি মুছে ফেলতে চান?"):
            return
        try:
            if os.path.exists(HISTORY_FILE):
                os.remove(HISTORY_FILE)
            for iid in self.history_tree.get_children():
                self.history_tree.delete(iid)
            self._log("History cleared.")
        except Exception as e:
            self._log(f"History clear failed: {e}")

    def _on_history_open_file(self):
        selected = self.history_tree.selection()
        if not selected:
            messagebox.showinfo("No selection", "প্রথমে একটি লিস্ট আইটেম সিলেক্ট করুন।")
            return
        item = self.history_tree.item(selected[0])
        path = item["values"][2]
        self._open_file(path)

    def _on_history_open_folder(self):
        selected = self.history_tree.selection()
        if not selected:
            messagebox.showinfo("No selection", "প্রথমে একটি লিস্ট আইটেম সিলেক্ট করুন।")
            return
        item = self.history_tree.item(selected[0])
        path = item["values"][2]
        self._open_folder(os.path.dirname(path) if os.path.isfile(path) else path)

    def _get_delay_ms(self):
        """Calculate milliseconds until the scheduled download time."""
        try:
            target_str = self.schedule_time_var.get().strip()
            target_time = datetime.datetime.strptime(target_str, "%H:%M").time()
            now = datetime.datetime.now()
            target_dt = datetime.datetime.combine(now.date(), target_time)
            if target_dt <= now:
                target_dt += datetime.timedelta(days=1)
            delta = target_dt - now
            return int(delta.total_seconds() * 1000)
        except Exception as e:
            self._log(f"Schedule time parse error: {e}")
            return 0

    def _maybe_auto_shutdown(self):
        """Perform OS shutdown if auto-shutdown is enabled and we are not in batch mode (or at end of batch)."""
        if self.auto_shutdown_var.get():
            self._log("Auto-shutdown enabled. PC will shutdown in 10 seconds...")
            self.after(10000, self._auto_shutdown)

    def _auto_shutdown(self):
        """Execute platform-specific shutdown command."""
        self._log("Shutting down PC...")
        if os.name == "nt":
            os.system("shutdown /s /t 1")
        else:
            os.system("shutdown -h now")

    # ---------------- yt-dlp self-update check ----------------
    @staticmethod
    def _installed_ytdlp_version():
        if yt_dlp is None:
            return None
        try:
            from yt_dlp.version import __version__ as v
            return v
        except Exception:
            return getattr(yt_dlp, "__version__", None)

    def _check_ytdlp_update(self, manual=False):
        """Check PyPI for a newer yt-dlp release. Silent on the automatic
        startup check unless an update is actually found; manual checks
        (via the header button) always report back to the user."""
        if yt_dlp is None:
            if manual:
                messagebox.showerror("Missing dependency",
                                      "yt-dlp ইনস্টল করা নেই।\n\npip install yt-dlp চালান।")
            return
        if manual:
            self.update_check_btn.config(state="disabled", text="🔄 Checking...")
        threading.Thread(target=self._check_ytdlp_update_worker, args=(manual,), daemon=True).start()

    def _check_ytdlp_update_worker(self, manual):
        current = self._installed_ytdlp_version() or "unknown"
        try:
            req = urllib.request.Request(
                "https://pypi.org/pypi/yt-dlp/json",
                headers={"User-Agent": "yt-dlp-gui-update-checker"}
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            latest = (data.get("info", {}) or {}).get("version")
        except Exception as ex:
            self.after(0, lambda: self._on_update_check_result(current, None, manual, str(ex)))
            return
        self.after(0, lambda: self._on_update_check_result(current, latest, manual, None))

    @staticmethod
    def _version_tuple(v):
        """Turn a version string into a tuple of ints for correct numeric
        comparison, e.g. '2026.6.9' and '2026.06.09' both become
        (2026, 6, 9) -- a plain string compare would wrongly treat
        '2026.6.9' as "newer" than '2026.06.09' since '6' > '0'."""
        return tuple(int(p) for p in re.findall(r'\d+', v or ""))

    def _on_update_check_result(self, current, latest, manual, error):
        if manual:
            self.update_check_btn.config(state="normal", text="⬆ yt-dlp Update Check")

        if error:
            self._log(f"yt-dlp update check ব্যর্থ: {error}")
            if manual:
                messagebox.showwarning("Update check ব্যর্থ",
                                        f"লেটেস্ট ভার্সন চেক করা যায়নি (ইন্টারনেট কানেকশন চেক করুন)।\n\n{error}")
            return

        if not latest:
            self._log("yt-dlp update check: PyPI থেকে ভার্সন তথ্য পাওয়া যায়নি।")
            return

        try:
            is_newer = (current != "unknown"
                        and self._version_tuple(latest) > self._version_tuple(current))
        except Exception:
            # Fallback for any version string that doesn't parse as expected.
            is_newer = current != "unknown" and latest != current

        if is_newer:
            self._log(f"yt-dlp নতুন ভার্সন পাওয়া গেছে: {latest} (আপনার আছে: {current})")
            self._show_ytdlp_update_modal(current, latest)
        else:
            self._log(f"yt-dlp আপ-টু-ডেট আছে (version {current}).")
            if manual:
                self._show_ytdlp_uptodate_modal(current)

    def _show_ytdlp_uptodate_modal(self, current):
        """Shown only for a manual check when no update is available --
        just an info notice with a single Close button."""
        modal = tk.Toplevel(self)
        modal.title("yt-dlp")
        modal.configure(bg=BG_CARD)
        modal.transient(self)
        modal.resizable(False, False)

        wrap = ttk.Frame(modal, style="Card.TFrame", padding=20)
        wrap.pack(fill="both", expand=True)

        ttk.Label(wrap, text="✅ তোমার yt-dlp আপডেট আছে",
                  style="Body.TLabel", font=("Segoe UI Semibold", 11, "bold")
                  ).pack(anchor="w", pady=(0, 8))
        ttk.Label(
            wrap, text=f"বর্তমান ভার্সন: {current}\nএটাই সর্বশেষ ভার্সন — নতুন কিছু নেই।",
            style="Subtle.TLabel", wraplength=380, justify="left"
        ).pack(anchor="w", pady=(0, 14))

        ttk.Button(wrap, text="Close", style="Stop.TButton",
                   command=modal.destroy).pack(anchor="w")

        modal.protocol("WM_DELETE_WINDOW", modal.destroy)
        modal.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - modal.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - modal.winfo_height()) // 2
        modal.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        modal.grab_set()

    def _show_ytdlp_update_modal(self, current, latest):
        modal = tk.Toplevel(self)
        modal.title("yt-dlp Update Available")
        modal.configure(bg=BG_CARD)
        modal.transient(self)
        modal.resizable(False, False)

        wrap = ttk.Frame(modal, style="Card.TFrame", padding=20)
        wrap.pack(fill="both", expand=True)

        ttk.Label(wrap, text="⬆ নতুন yt-dlp ভার্সন পাওয়া গেছে",
                  style="Body.TLabel", font=("Segoe UI Semibold", 11, "bold")
                  ).pack(anchor="w", pady=(0, 8))
        ttk.Label(
            wrap,
            text=f"আপনার বর্তমান ভার্সন: {current}\nনতুন ভার্সন: {latest}",
            style="Subtle.TLabel", wraplength=380, justify="left"
        ).pack(anchor="w", pady=(0, 4))
        ttk.Label(
            wrap,
            text="YouTube মাঝেমধ্যে তাদের সাইট পরিবর্তন করে, যার কারণে পুরনো yt-dlp ভার্সনে "
                 "ডাউনলোড ফেইল করতে পারে। আপডেট করে নেওয়াই ভালো।",
            style="Subtle.TLabel", wraplength=380, justify="left"
        ).pack(anchor="w", pady=(0, 14))

        status_var = tk.StringVar(value="")
        ttk.Label(wrap, textvariable=status_var, style="Subtle.TLabel",
                  wraplength=380, justify="left").pack(anchor="w", pady=(0, 10))

        btn_row = ttk.Frame(wrap, style="Card.TFrame")
        btn_row.pack(fill="x")

        close_btn = ttk.Button(btn_row, text="Close", style="Stop.TButton", command=modal.destroy)
        close_btn.pack(side="left")

        update_btn = ttk.Button(btn_row, text="🔄 Update Now", style="Accent.TButton")
        update_btn.pack(side="left", padx=(10, 0))

        def do_update():
            update_btn.config(state="disabled", text="Updating...")
            close_btn.config(state="disabled")
            status_var.set("pip install --upgrade yt-dlp চলছে... একটু অপেক্ষা করুন।")
            threading.Thread(
                target=self._run_ytdlp_update,
                args=(status_var, update_btn, close_btn),
                daemon=True
            ).start()

        update_btn.config(command=do_update)
        modal.protocol("WM_DELETE_WINDOW", modal.destroy)

        modal.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - modal.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - modal.winfo_height()) // 2
        modal.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        modal.grab_set()

    def _run_ytdlp_update(self, status_var, update_btn, close_btn):
        """Runs in a worker thread: pip install --upgrade yt-dlp, with an
        automatic retry using --break-system-packages if pip refuses on an
        externally-managed Python (common on modern Linux distros)."""
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            output = (result.stdout or "") + (result.stderr or "")
            ok = result.returncode == 0
            if not ok and "externally-managed-environment" in output:
                result2 = subprocess.run(cmd + ["--break-system-packages"], capture_output=True, text=True)
                output += "\n--- retry with --break-system-packages ---\n"
                output += (result2.stdout or "") + (result2.stderr or "")
                ok = result2.returncode == 0
        except Exception as ex:
            output = str(ex)
            ok = False

        self.after(0, lambda: self._on_ytdlp_update_done(ok, output, status_var, update_btn, close_btn))

    def _on_ytdlp_update_done(self, ok, output, status_var, update_btn, close_btn):
        for line in output.strip().splitlines()[-15:]:
            self._log(f"[pip] {line}")

        if not ok:
            status_var.set("❌ আপডেট ব্যর্থ হয়েছে। Log-এ বিস্তারিত দেখুন।")
            update_btn.config(state="normal", text="🔄 Retry Update")
            close_btn.config(state="normal")
            return

        status_var.set("✅ আপডেট সম্পন্ন! পরিবর্তন কার্যকর করতে অ্যাপ Restart করুন।")
        close_btn.config(state="normal", text="পরে করব")
        update_btn.config(text="🔁 Restart Now", state="normal", command=self._restart_app_for_update)
        self._log("yt-dlp সফলভাবে আপডেট হয়েছে। পরিবর্তন কার্যকর করতে অ্যাপ restart করুন।")

    def _restart_app_for_update(self):
        python = sys.executable
        try:
            if getattr(sys, "frozen", False):
                os.execv(python, [python] + sys.argv[1:])
            else:
                os.execv(python, [python] + sys.argv)
        except Exception as ex:
            messagebox.showerror(
                "Restart ব্যর্থ",
                f"অ্যাপ automatically restart করা যায়নি। ম্যানুয়ালি বন্ধ করে আবার চালু করুন।\n\n{ex}"
            )

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

        # ------- Notebook (Tabs) styling -------
        # The default 'clam' notebook has a light grey unselected tab strip that
        # clashes badly with our dark theme. Fully re-skin it to match.
        style.configure("TNotebook", background=BG_MAIN, borderwidth=0,
                         tabmargins=(0, 6, 0, 0))
        style.configure("TNotebook.Tab",
                         background=BG_CARD_ALT,
                         foreground=FG_SUBTLE,
                         padding=(22, 10),
                         font=("Segoe UI Semibold", 10, "bold"),
                         borderwidth=0)
        style.map("TNotebook.Tab",
                  background=[("selected", ACCENT), ("active", BG_CARD)],
                  foreground=[("selected", "#ffffff"), ("active", FG_TEXT)],
                  expand=[("selected", (0, 0, 0, 0))])
        # Remove the default focus dotted rectangle that ttk draws over tabs
        style.layout("TNotebook.Tab", [
            ("Notebook.tab", {"sticky": "nswe", "children": [
                ("Notebook.padding", {"side": "top", "sticky": "nswe", "children": [
                    ("Notebook.label", {"side": "top", "sticky": ""})
                ]})
            ]})
        ])

        # ------- Treeview (used by the M3U media list) -------
        style.configure("Treeview",
                         background=BG_INPUT,
                         foreground=FG_TEXT,
                         fieldbackground=BG_INPUT,
                         borderwidth=0,
                         rowheight=26,
                         font=("Segoe UI", 10))
        style.map("Treeview",
                  background=[("selected", ACCENT)],
                  foreground=[("selected", "#ffffff")])
        style.configure("Treeview.Heading",
                         background=BG_CARD,
                         foreground=FG_TEXT,
                         font=("Segoe UI Semibold", 9, "bold"),
                         borderwidth=0,
                         relief="flat",
                         padding=(8, 6))
        style.map("Treeview.Heading",
                  background=[("active", BG_CARD_ALT)])

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
        # Outer wrapper - contains header (top), notebook (middle), everything else
        outer = tk.Frame(self, bg=BG_MAIN)
        outer.pack(fill="both", expand=True)

        # ---- Persistent Header (above the notebook, always visible) ----
        header = ttk.Frame(outer, style="Main.TFrame", padding=(18, 14, 18, 4))
        header.pack(fill="x")
        header_top = ttk.Frame(header, style="Main.TFrame")
        header_top.pack(fill="x")
        title_box = ttk.Frame(header_top, style="Main.TFrame")
        title_box.pack(side="left", fill="x", expand=True)
        ttk.Label(title_box, text="⬇ YT-DLP Downloader", style="Header.TLabel").pack(anchor="w")
        ttk.Label(title_box,
                  text="YouTube/playlist ডাউনলোড + Direct URL / M3U playlist ডাউনলোড",
                  style="SubHeader.TLabel").pack(anchor="w", pady=(2, 0))
        ttk.Button(header_top, text="🔄 Refresh  (Ctrl+R)", style="Ghost.TButton",
                   command=self._refresh_app).pack(side="right", anchor="n")
        self.update_check_btn = ttk.Button(header_top, text="⬆ yt-dlp Update Check", style="Ghost.TButton",
                                            command=lambda: self._check_ytdlp_update(manual=True))
        self.update_check_btn.pack(side="right", anchor="n", padx=(0, 8))

        # ---- Notebook (Tabs) - each tab has its own scrollable canvas ----
        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(6, 10))

        # ===== TAB 1: YouTube / Video Downloader (original features) =====
        tab1 = ttk.Frame(self.notebook, style="Main.TFrame")
        self.notebook.add(tab1, text="  🎬  YouTube / Video Downloader  ")

        # Scrollable canvas for Tab 1
        self.canvas = tk.Canvas(tab1, bg=BG_MAIN, highlightthickness=0, bd=0)
        vscroll1 = ttk.Scrollbar(tab1, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vscroll1.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        vscroll1.pack(side="right", fill="y")

        root = ttk.Frame(self.canvas, style="Main.TFrame")
        self._canvas_window = self.canvas.create_window((0, 0), window=root, anchor="nw")

        def _on_frame_configure(event=None):
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))

        def _on_canvas_configure(event):
            self.canvas.itemconfig(self._canvas_window, width=event.width)

        root.bind("<Configure>", _on_frame_configure)
        self.canvas.bind("<Configure>", _on_canvas_configure)

        root.configure(padding=(18, 12))

        # URL card
        url_outer, url_inner = self._card(root, "Video / Playlist URL")
        url_outer.pack(fill="x", pady=(0, 12))

        ttk.Checkbutton(url_inner, text="🗂 Batch Mode: একসাথে একাধিক URL (এক লাইনে একটি করে)",
                         variable=self.batch_mode_var,
                         command=self._on_batch_mode_toggle).pack(anchor="w", pady=(0, 8))

        # ---- Single URL mode (default) ----
        self.single_url_frame = ttk.Frame(url_inner, style="Card.TFrame")
        self.single_url_frame.pack(fill="x")
        url_row = ttk.Frame(self.single_url_frame, style="Card.TFrame")
        url_row.pack(fill="x")
        self.url_entry = ttk.Entry(url_row, textvariable=self.url_var, style="TEntry")
        self.url_entry.pack(side="left", fill="x", expand=True, ipady=3)
        self.url_entry.bind("<FocusOut>", lambda e: self._on_url_committed())
        self.url_entry.bind("<Return>", lambda e: self._on_url_committed())
        ttk.Button(url_row, text="📋 Paste", style="Ghost.TButton",
                   command=self._paste_url).pack(side="left", padx=(10, 0))
        ttk.Label(self.single_url_frame, text="YouTube লিংক কপি করলে এখানে automatically বসে যাবে, "
                                   "অথবা Paste বাটনে ক্লিক করুন.",
                  style="Subtle.TLabel").pack(anchor="w", pady=(6, 0))

        # ---- Batch URL mode (multiple links, one per line) ----
        self.batch_url_frame = ttk.Frame(url_inner, style="Card.TFrame")
        # not packed by default; _on_batch_mode_toggle shows/hides this vs single_url_frame

        batch_text_wrap = tk.Frame(self.batch_url_frame, bg=BG_INPUT,
                                    highlightbackground=BORDER, highlightthickness=1)
        batch_text_wrap.pack(fill="x")
        self.batch_text = tk.Text(batch_text_wrap, height=6, wrap="none",
                                   bg=BG_INPUT, fg=FG_TEXT, insertbackground=FG_TEXT,
                                   bd=0, padx=10, pady=8, font=("Consolas", 9))
        self.batch_text.pack(fill="both", expand=True)
        self.batch_text.bind("<KeyRelease>", lambda e: self._update_batch_count())

        batch_btn_row = ttk.Frame(self.batch_url_frame, style="Card.TFrame")
        batch_btn_row.pack(fill="x", pady=(6, 0))
        ttk.Button(batch_btn_row, text="📋 Paste", style="Ghost.TButton",
                   command=self._paste_batch_urls).pack(side="left")
        ttk.Button(batch_btn_row, text="🧹 Clear", style="Ghost.TButton",
                   command=self._clear_batch_urls).pack(side="left", padx=(8, 0))
        self.batch_count_var = tk.StringVar(value="0 URL")
        ttk.Label(batch_btn_row, textvariable=self.batch_count_var,
                  style="Subtle.TLabel").pack(side="left", padx=(10, 0))

        ttk.Label(self.batch_url_frame,
                  text="প্রতি লাইনে একটি করে ভিডিও/প্লেলিস্ট লিংক পেস্ট করুন — সবগুলো একে একে (queue) "
                       "ডাউনলোড হবে। '#' দিয়ে শুরু হওয়া লাইন উপেক্ষা করা হবে।",
                  style="Subtle.TLabel", wraplength=640, justify="left").pack(anchor="w", pady=(6, 0))

        # Live queue status list (rendered once a batch download starts)
        self.batch_queue_frame = ttk.Frame(self.batch_url_frame, style="Card.TFrame")
        self.batch_queue_frame.pack(fill="x", pady=(8, 0))

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

        ttk.Checkbutton(self.playlist_select_frame, text="Number filenames (1. 2. 3. ...)",
                         variable=self.playlist_numbering_var).pack(anchor="w", pady=(6, 0))

        ttk.Checkbutton(opt_inner, text="Audio only (save as MP3)",
                         variable=self.audio_only_var,
                         command=self._toggle_quality_state).pack(anchor="w", pady=(0, 6))

        ttk.Checkbutton(opt_inner, text="🖼 Embed video thumbnail as poster/cover art",
                         variable=self.embed_thumbnail_var).pack(anchor="w", pady=(0, 6))

        sched_row = ttk.Frame(opt_inner, style="Card.TFrame")
        sched_row.pack(fill="x", pady=(0, 6))
        ttk.Checkbutton(sched_row, text="🕒 Scheduled Download:", variable=self.schedule_enabled_var).pack(side="left")
        ttk.Entry(sched_row, textvariable=self.schedule_time_var, width=8, style="TEntry").pack(side="left", padx=(10, 0))
        ttk.Label(sched_row, text="(HH:MM)", style="Subtle.TLabel").pack(side="left", padx=(5, 0))

        ttk.Checkbutton(opt_inner, text="🔌 Shutdown PC when finished", variable=self.auto_shutdown_var).pack(anchor="w", pady=(0, 10))

        grid = ttk.Frame(opt_inner, style="Card.TFrame")
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        grid.columnconfigure(3, weight=1)

        ttk.Label(grid, text="Video quality", style="Subtle.TLabel").grid(row=0, column=0, sticky="w")
        self.quality_combo = ttk.Combobox(
            grid, textvariable=self.quality_var, values=DEFAULT_QUALITY_LABELS,
            state="readonly", width=18
        )
        self.quality_combo.grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.quality_combo.bind("<<ComboboxSelected>>", self._on_quality_changed)

        ttk.Label(grid, text="Cookies from browser", style="Subtle.TLabel").grid(row=0, column=2, sticky="w", padx=(20, 0))
        self.browser_combo = ttk.Combobox(
            grid, textvariable=self.browser_var,
            values=["None", "firefox", "edge", "chrome", "brave", "opera", "vivaldi"],
            state="readonly", width=14
        )
        self.browser_combo.grid(row=1, column=2, sticky="w", padx=(20, 0), pady=(4, 0))

        ttk.Label(grid, text="Speed limit (throttle)", style="Subtle.TLabel").grid(
            row=2, column=0, sticky="w", pady=(10, 0)
        )
        speed_limit_row = ttk.Frame(grid, style="Card.TFrame")
        speed_limit_row.grid(row=3, column=0, sticky="w", pady=(4, 0))
        self.speed_limit_entry = ttk.Entry(
            speed_limit_row, textvariable=self.speed_limit_value_var, style="TEntry", width=8
        )
        self.speed_limit_entry.pack(side="left", ipady=2)
        self.speed_limit_unit_combo = ttk.Combobox(
            speed_limit_row, textvariable=self.speed_limit_unit_var,
            values=["KB/s", "MB/s"], state="readonly", width=7
        )
        self.speed_limit_unit_combo.pack(side="left", padx=(6, 0))
        ttk.Label(grid, text="e.g. 5 + MB/s — blank = unlimited", style="Subtle.TLabel").grid(
            row=4, column=0, sticky="w", pady=(3, 0)
        )

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

        # -------- Bounded scrollable container for the title checkboxes --------
        # Fixed height so a 50-video playlist doesn't push everything else off
        # the screen. Users scroll INSIDE this box, not the whole page.
        self.titles_container = tk.Frame(
            pv_inner, bg=BG_INPUT, highlightbackground=BORDER,
            highlightthickness=1, height=280
        )
        self.titles_container.pack(fill="x", pady=(8, 0))
        self.titles_container.pack_propagate(False)  # keep the fixed 280px height

        self.titles_canvas = tk.Canvas(
            self.titles_container, bg=BG_INPUT,
            highlightthickness=0, bd=0
        )
        titles_scroll = ttk.Scrollbar(
            self.titles_container, orient="vertical",
            command=self.titles_canvas.yview
        )
        self.titles_canvas.configure(yscrollcommand=titles_scroll.set)
        self.titles_canvas.pack(side="left", fill="both", expand=True)
        titles_scroll.pack(side="right", fill="y")

        self.titles_list_frame = ttk.Frame(self.titles_canvas, style="Card.TFrame")
        self._titles_canvas_window = self.titles_canvas.create_window(
            (0, 0), window=self.titles_list_frame, anchor="nw"
        )

        def _on_titles_frame_configure(event=None):
            self.titles_canvas.configure(scrollregion=self.titles_canvas.bbox("all"))

        def _on_titles_canvas_configure(event):
            self.titles_canvas.itemconfig(self._titles_canvas_window, width=event.width)

        self.titles_list_frame.bind("<Configure>", _on_titles_frame_configure)
        self.titles_canvas.bind("<Configure>", _on_titles_canvas_configure)

        # Placeholder text shown before any titles are loaded (so the box
        # doesn't look empty/broken to the user before they hit "Load Titles")
        self.titles_placeholder = ttk.Label(
            self.titles_list_frame,
            text="Playlist URL paste করে '🔄 Load / Refresh Titles' চাপুন —\n"
                 "video titles এখানে checkbox list হিসেবে দেখা যাবে।",
            style="Subtle.TLabel", justify="left"
        )
        self.titles_placeholder.pack(anchor="w", padx=10, pady=14)

        self.video_check_vars = []

        # Action buttons
        btn_frame = ttk.Frame(root, style="Main.TFrame")
        btn_frame.pack(fill="x", pady=(4, 14))
        self.download_btn = ttk.Button(btn_frame, text="⬇  Download", style="Accent.TButton",
                                        command=self._start_download)
        self.download_btn.pack(side="left")
        self.pause_btn = ttk.Button(btn_frame, text="⏸  Pause", style="Ghost.TButton",
                                     command=self._pause_download, state="disabled")
        self.pause_btn.pack(side="left", padx=(10, 0))
        self.stop_btn = ttk.Button(btn_frame, text="■  Stop", style="Stop.TButton",
                                    command=self._stop_download, state="disabled")
        self.stop_btn.pack(side="left", padx=(10, 0))
        ttk.Label(btn_frame, textvariable=self.estimated_size_var, style="Subtle.TLabel").pack(
            side="left", padx=(14, 0)
        )

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

        est_box = ttk.Frame(stats_row, style="Card.TFrame")
        est_box.pack(side="left", padx=(30, 0))
        ttk.Label(est_box, text="📥 EST. SIZE", style="Subtle.TLabel").pack(anchor="w")
        ttk.Label(est_box, textvariable=self.estimated_size_progress_var, style="Stat.TLabel",
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

        # ===== TAB 2: Direct URL / M3U Downloader =====
        tab2 = ttk.Frame(self.notebook, style="Main.TFrame")
        self.notebook.add(tab2, text="  🔗  Direct URL / M3U Downloader  ")
        self._build_tab2(tab2)

        # ===== TAB 3: Download History =====
        tab3 = ttk.Frame(self.notebook, style="Main.TFrame")
        self.notebook.add(tab3, text="  📜  History  ")
        self._build_history_tab(tab3)

        # Register both canvases for mousewheel routing (see _on_mousewheel_global)
        self._tab_canvases = [self.canvas, self.tab2_canvas, self.history_canvas]

        # Bind mousewheel ONCE globally, routed to the active tab's canvas.
        # (Previous versions used self.canvas.bind_all which broke after adding
        # a second canvas — the second canvas would silently steal all wheel
        # events. Using a single handler + tab detection fixes that.)
        self.bind_all("<MouseWheel>", self._on_mousewheel_global)
        self.bind_all("<Button-4>", self._on_mousewheel_global)
        self.bind_all("<Button-5>", self._on_mousewheel_global)

    # ================================================================
    # TAB 3: DOWNLOAD HISTORY
    # ================================================================
    def _build_history_tab(self, parent):
        """Build the content of Tab 3 (Download History)."""
        self.history_canvas = tk.Canvas(parent, bg=BG_MAIN, highlightthickness=0, bd=0)
        vscroll3 = ttk.Scrollbar(parent, orient="vertical", command=self.history_canvas.yview)
        self.history_canvas.configure(yscrollcommand=vscroll3.set)
        self.history_canvas.pack(side="left", fill="both", expand=True)
        vscroll3.pack(side="right", fill="y")

        root = ttk.Frame(self.history_canvas, style="Main.TFrame")
        self._history_canvas_window = self.history_canvas.create_window((0, 0), window=root, anchor="nw")

        def _on_frame_configure(event=None):
            self.history_canvas.configure(scrollregion=self.history_canvas.bbox("all"))
        def _on_canvas_configure(event):
            self.history_canvas.itemconfig(self._history_canvas_window, width=event.width)

        root.bind("<Configure>", _on_frame_configure)
        self.history_canvas.bind("<Configure>", _on_canvas_configure)
        root.configure(padding=(18, 12))

        hist_outer, hist_inner = self._card(root, "Download History")
        hist_outer.pack(fill="both", expand=True, pady=(0, 12))

        # Action buttons
        btn_row = ttk.Frame(hist_inner, style="Card.TFrame")
        btn_row.pack(fill="x", pady=(0, 10))
        ttk.Button(btn_row, text="🔄 Refresh", style="Ghost.TButton", command=self._load_history).pack(side="left")
        ttk.Button(btn_row, text="🗑 Clear History", style="Stop.TButton", command=self._clear_history).pack(side="left", padx=(10, 0))

        # History Treeview
        tree_wrap = tk.Frame(hist_inner, bg=BG_INPUT, highlightbackground=BORDER, highlightthickness=1)
        tree_wrap.pack(fill="both", expand=True)

        self.history_tree = ttk.Treeview(
            tree_wrap,
            columns=("date", "title", "path"),
            show="headings",
            height=20,
            selectmode="browse"
        )
        self.history_tree.heading("date", text="Date")
        self.history_tree.heading("title", text="Title")
        self.history_tree.heading("path", text="Local Path")

        self.history_tree.column("date", width=140, anchor="w", stretch=False)
        self.history_tree.column("title", width=300, anchor="w", stretch=True)
        self.history_tree.column("path", width=300, anchor="w", stretch=True)

        h_scroll = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.history_tree.yview)
        self.history_tree.configure(yscrollcommand=h_scroll.set)
        self.history_tree.pack(side="left", fill="both", expand=True)
        h_scroll.pack(side="right", fill="y")

        # Bottom buttons for selected item
        action_row = ttk.Frame(hist_inner, style="Card.TFrame")
        action_row.pack(fill="x", pady=(10, 0))
        ttk.Button(action_row, text="▶ Play File", style="Accent.TButton", command=self._on_history_open_file).pack(side="left")
        ttk.Button(action_row, text="📁 Open Folder", style="Ghost.TButton", command=self._on_history_open_folder).pack(side="left", padx=(10, 0))

        self._load_history()

    # ================================================================
    # TAB 2: DIRECT URL / M3U DOWNLOADER
    # ================================================================
    def _build_tab2(self, parent):
        """Build the content of Tab 2 (Direct URL + M3U playlist downloader)."""
        # Scrollable canvas for Tab 2
        self.tab2_canvas = tk.Canvas(parent, bg=BG_MAIN, highlightthickness=0, bd=0)
        vscroll2 = ttk.Scrollbar(parent, orient="vertical", command=self.tab2_canvas.yview)
        self.tab2_canvas.configure(yscrollcommand=vscroll2.set)
        self.tab2_canvas.pack(side="left", fill="both", expand=True)
        vscroll2.pack(side="right", fill="y")

        root = ttk.Frame(self.tab2_canvas, style="Main.TFrame")
        self._tab2_canvas_window = self.tab2_canvas.create_window((0, 0), window=root, anchor="nw")

        def _on_frame_configure(event=None):
            self.tab2_canvas.configure(scrollregion=self.tab2_canvas.bbox("all"))

        def _on_canvas_configure(event):
            self.tab2_canvas.itemconfig(self._tab2_canvas_window, width=event.width)

        root.bind("<Configure>", _on_frame_configure)
        self.tab2_canvas.bind("<Configure>", _on_canvas_configure)

        root.configure(padding=(18, 12))

        # ---- Section A: Direct CDN URL Download ----
        direct_outer, direct_inner = self._card(root, "Direct CDN URL (Referer সহ)")
        direct_outer.pack(fill="x", pady=(0, 12))

        ttk.Label(direct_inner,
                  text="403 Forbidden দেয় এমন URL Referer header দিয়ে ডাউনলোড করুন (auto-referrer)।",
                  style="Subtle.TLabel", wraplength=760, justify="left").pack(anchor="w", pady=(0, 8))

        ttk.Label(direct_inner, text="Media URL", style="Subtle.TLabel").pack(anchor="w")
        url_row = ttk.Frame(direct_inner, style="Card.TFrame")
        url_row.pack(fill="x", pady=(4, 8))
        ttk.Entry(url_row, textvariable=self.direct_url_var, style="TEntry").pack(
            side="left", fill="x", expand=True, ipady=3
        )
        ttk.Button(url_row, text="📋 Paste", style="Ghost.TButton",
                   command=lambda: self._paste_into_var(self.direct_url_var)).pack(side="left", padx=(10, 0))

        ttk.Label(direct_inner, text="Referer URL", style="Subtle.TLabel").pack(anchor="w")
        ref_row = ttk.Frame(direct_inner, style="Card.TFrame")
        ref_row.pack(fill="x", pady=(4, 8))
        ttk.Entry(ref_row, textvariable=self.direct_referer_var, style="TEntry").pack(
            side="left", fill="x", expand=True, ipady=3
        )
        ttk.Button(ref_row, text="📋 Paste", style="Ghost.TButton",
                   command=lambda: self._paste_into_var(self.direct_referer_var)).pack(side="left", padx=(10, 0))

        ttk.Label(direct_inner, text="User-Agent (optional)", style="Subtle.TLabel").pack(anchor="w")
        ttk.Entry(direct_inner, textvariable=self.direct_user_agent_var, style="TEntry").pack(
            fill="x", pady=(4, 10), ipady=3
        )

        direct_btn_row = ttk.Frame(direct_inner, style="Card.TFrame")
        direct_btn_row.pack(fill="x")
        self.direct_download_btn = ttk.Button(
            direct_btn_row, text="⬇  Download (Direct URL)", style="Accent.TButton",
            command=self._start_direct_download
        )
        self.direct_download_btn.pack(side="left")

        # ---- Section A2: Fast / multi-connection download (Direct URL + M3U) ----
        fast_outer, fast_inner = self._card(root, "⚡ Fast Download (Multi-connection)")
        fast_outer.pack(fill="x", pady=(0, 12))

        ttk.Label(fast_inner,
                  text="ISP/FTP direct video link (M3U entries বা Direct URL) হলে multiple connection দিয়ে "
                       "parallel এ download করে speed বহুগুণ বাড়ানো যায়। aria2c ইনস্টল থাকলে সেটা ব্যবহার হবে, "
                       "না থাকলে yt-dlp-এর নিজস্ব concurrent-chunk downloader ব্যবহার হবে।",
                  style="Subtle.TLabel", wraplength=760, justify="left").pack(anchor="w", pady=(0, 8))

        fast_row = ttk.Frame(fast_inner, style="Card.TFrame")
        fast_row.pack(fill="x")
        ttk.Checkbutton(fast_row, text="Fast download মোড চালু করো",
                         variable=self.fast_download_var, style="TCheckbutton").pack(side="left")

        ttk.Checkbutton(fast_row, text="🔌 Auto Shutdown",
                         variable=self.auto_shutdown_var, style="TCheckbutton").pack(side="left", padx=(15, 0))

        ttk.Label(fast_row, text="Connections:", style="Subtle.TLabel").pack(side="left", padx=(20, 4))
        conn_combo = ttk.Combobox(
            fast_row, textvariable=self.fast_download_connections_var,
            values=["4", "8", "16", "32"], width=4, state="readonly", style="TCombobox"
        )
        conn_combo.pack(side="left")

        self.fast_download_status_var = tk.StringVar(value=self._fast_download_engine_label())
        ttk.Label(fast_inner, textvariable=self.fast_download_status_var,
                  style="Subtle.TLabel").pack(anchor="w", pady=(6, 0))

        # ---- Section B: M3U/M3U8 file loader ----
        m3u_outer, m3u_inner = self._card(root, "M3U / M3U8 Playlist File")
        m3u_outer.pack(fill="x", pady=(0, 12))

        ttk.Label(m3u_inner,
                  text="M3U/M3U8 ফাইল লোড করুন — প্রতিটি entry-এর title, referer, user-agent এবং "
                       "group auto detect হবে।",
                  style="Subtle.TLabel", wraplength=760, justify="left").pack(anchor="w", pady=(0, 8))

        # ---- Load from online URL (e.g. raw GitHub .m3u link, or a page/API
        # endpoint whose response body is the M3U content itself) ----
        ttk.Label(m3u_inner, text="Online M3U URL", style="Subtle.TLabel").pack(anchor="w")
        m3u_url_row = ttk.Frame(m3u_inner, style="Card.TFrame")
        m3u_url_row.pack(fill="x", pady=(4, 4))
        ttk.Entry(m3u_url_row, textvariable=self.m3u_url_var, style="TEntry").pack(
            side="left", fill="x", expand=True, ipady=3
        )
        ttk.Button(m3u_url_row, text="📋 Paste", style="Ghost.TButton",
                   command=lambda: self._paste_into_var(self.m3u_url_var)).pack(side="left", padx=(6, 0))
        self.m3u_url_load_btn = ttk.Button(m3u_url_row, text="🌐 Load from URL", style="Accent.TButton",
                                            command=self._load_m3u_from_url)
        self.m3u_url_load_btn.pack(side="left", padx=(6, 0))
        ttk.Label(m3u_inner,
                  text="e.g. https://raw.githubusercontent.com/user/repo/main/playlist.m3u  বা  "
                       "https://your-m3u-generator.vercel.app/  (এমন URL-ও কাজ করবে যেটা একটা "
                       "page/API — hit করলে response-এই M3U content থাকে)।",
                  style="Subtle.TLabel", wraplength=760, justify="left").pack(anchor="w", pady=(0, 10))

        ttk.Label(m3u_inner, text="অথবা — Local M3U ফাইল", style="Subtle.TLabel").pack(anchor="w")
        m3u_file_row = ttk.Frame(m3u_inner, style="Card.TFrame")
        m3u_file_row.pack(fill="x", pady=(4, 8))
        ttk.Entry(m3u_file_row, textvariable=self.m3u_file_path_var, style="TEntry",
                  state="readonly").pack(side="left", fill="x", expand=True, ipady=3)
        ttk.Button(m3u_file_row, text="📂 Browse M3U", style="Ghost.TButton",
                   command=self._browse_m3u_file).pack(side="left", padx=(10, 0))
        ttk.Button(m3u_file_row, text="🔄 Parse", style="Accent.TButton",
                   command=self._parse_m3u).pack(side="left", padx=(6, 0))

        ttk.Label(m3u_inner, textvariable=self.m3u_status_label_var,
                  style="Subtle.TLabel", wraplength=760, justify="left").pack(anchor="w", pady=(4, 0))

        # ---- Section C: Media list (Treeview) + action buttons ----
        list_outer, list_inner = self._card(root, "Media List (M3U entries)")
        list_outer.pack(fill="both", expand=True, pady=(0, 12))

        list_actions = ttk.Frame(list_inner, style="Card.TFrame")
        list_actions.pack(fill="x", pady=(0, 8))
        ttk.Button(list_actions, text="Select All", style="Ghost.TButton",
                   command=lambda: self._m3u_set_all(True)).pack(side="left")
        ttk.Button(list_actions, text="Deselect All", style="Ghost.TButton",
                   command=lambda: self._m3u_set_all(False)).pack(side="left", padx=(6, 0))

        # Range input (like playlist range: "1,5,10-15")
        range_row = ttk.Frame(list_inner, style="Card.TFrame")
        range_row.pack(fill="x", pady=(0, 8))
        ttk.Label(range_row, text="Range select:", style="Subtle.TLabel").pack(side="left")
        self.m3u_range_var = tk.StringVar()
        self.m3u_range_entry = ttk.Entry(range_row, textvariable=self.m3u_range_var, style="TEntry")
        self.m3u_range_entry.pack(side="left", fill="x", expand=True, padx=(8, 0), ipady=2)
        ttk.Button(range_row, text="✔ Apply Range", style="Ghost.TButton",
                   command=self._m3u_apply_range).pack(side="left", padx=(6, 0))
        ttk.Label(list_inner, text="e.g. 1,3,5-10  or  1-5,7-9,20",
                  style="Subtle.TLabel").pack(anchor="w", pady=(0, 8))

        # Group filter list — only shown after Parse when the M3U file has
        # more than one group. User picks one or more group names and hits
        # Apply to auto-select every entry belonging to those group(s).
        self.m3u_group_frame = ttk.Frame(list_inner, style="Card.TFrame")
        # Not packed yet — _parse_m3u packs it only when >1 group exists.

        ttk.Label(self.m3u_group_frame, text="Group filter:", style="Subtle.TLabel").pack(anchor="w")
        group_list_row = ttk.Frame(self.m3u_group_frame, style="Card.TFrame")
        group_list_row.pack(fill="x", pady=(4, 0))

        group_listbox_wrap = tk.Frame(group_list_row, bg=BG_INPUT, highlightbackground=BORDER, highlightthickness=1)
        group_listbox_wrap.pack(side="left", fill="x", expand=True)
        self.m3u_group_listbox = tk.Listbox(
            group_listbox_wrap, selectmode="extended", height=5,
            bg=BG_INPUT, fg=FG_TEXT, relief="flat", highlightthickness=0,
            activestyle="none", exportselection=False,
        )
        self.m3u_group_listbox.pack(side="left", fill="x", expand=True, padx=(4, 0), pady=2)
        group_scroll = ttk.Scrollbar(group_listbox_wrap, orient="vertical", command=self.m3u_group_listbox.yview)
        self.m3u_group_listbox.configure(yscrollcommand=group_scroll.set)
        group_scroll.pack(side="right", fill="y")

        group_btn_col = ttk.Frame(group_list_row, style="Card.TFrame")
        group_btn_col.pack(side="left", padx=(8, 0), fill="y")
        ttk.Button(group_btn_col, text="✔ Apply Group", style="Ghost.TButton",
                   command=self._m3u_apply_group_filter).pack(anchor="n")
        ttk.Label(group_btn_col, text="(Ctrl/Shift দিয়ে একাধিক group select করা যায়)",
                  style="Subtle.TLabel", wraplength=140, justify="left").pack(anchor="n", pady=(6, 0))

        # Treeview to show media list with checkboxes
        tree_wrap = tk.Frame(list_inner, bg=BG_INPUT, highlightbackground=BORDER, highlightthickness=1)
        tree_wrap.pack(fill="both", expand=True)
        self.m3u_tree_wrap = tree_wrap  # kept so _parse_m3u can pack the group filter frame right before it

        self.m3u_tree = ttk.Treeview(
            tree_wrap,
            columns=("status", "title", "group"),
            show="tree headings",
            height=12,
            selectmode="none"
        )
        self.m3u_tree.heading("#0", text="✓")
        self.m3u_tree.heading("status", text="Status")
        self.m3u_tree.heading("title", text="Title")
        self.m3u_tree.heading("group", text="Group")

        self.m3u_tree.column("#0", width=48, anchor="center", stretch=False)
        self.m3u_tree.column("status", width=70, anchor="center", stretch=False)
        self.m3u_tree.column("title", width=440, anchor="w", stretch=True)
        self.m3u_tree.column("group", width=160, anchor="w", stretch=False)

        tree_scroll = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.m3u_tree.yview)
        self.m3u_tree.configure(yscrollcommand=tree_scroll.set)

        self.m3u_tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")

        # Click on the ✓ column toggles that row's checkbox
        self.m3u_tree.bind("<Button-1>", self._on_m3u_tree_click)

        # Action buttons for M3U batch
        m3u_btn_row = ttk.Frame(list_inner, style="Card.TFrame")
        m3u_btn_row.pack(fill="x", pady=(10, 0))
        self.m3u_download_btn = ttk.Button(
            m3u_btn_row, text="⬇  Download Selected (All)", style="Accent.TButton",
            command=self._start_m3u_batch_download
        )
        self.m3u_download_btn.pack(side="left")
        ttk.Label(m3u_btn_row,
                  text="প্রতিটি entry-র জন্য group অনুযায়ী subfolder auto তৈরি হবে।",
                  style="Subtle.TLabel").pack(side="left", padx=(14, 0))

    # ---------------- Mousewheel routing (fix for multi-tab scrolling) ----------------
    def _on_mousewheel_global(self, event):
        """Route mouse wheel to whichever tab's canvas is currently visible.
        Special case: when the cursor is over the inner titles_canvas (the
        bounded playlist-titles box), scroll THAT instead of the outer tab
        canvas, so the user can scroll through 50+ video titles without
        moving the whole page. Widgets that manage their own scroll (Text,
        Listbox, Treeview) are skipped."""
        widget = event.widget
        try:
            wclass = widget.winfo_class() if widget else ""
        except tk.TclError:
            wclass = ""
        if wclass in ("Text", "Listbox", "Treeview", "TCombobox"):
            return

        # Walk up: any parent Text/Listbox/Treeview should also block us
        w = widget
        try:
            while w is not None:
                if w.winfo_class() in ("Text", "Listbox", "Treeview"):
                    return
                w = w.master
        except (tk.TclError, AttributeError):
            pass

        # If the mouse is over the inner titles list (or any of its children),
        # scroll THAT canvas instead of the outer tab canvas.
        target_canvas = None
        try:
            titles_canvas = getattr(self, "titles_canvas", None)
            if titles_canvas is not None:
                w2 = widget
                while w2 is not None:
                    if w2 is titles_canvas or w2 is self.titles_list_frame:
                        target_canvas = titles_canvas
                        break
                    w2 = w2.master
        except (tk.TclError, AttributeError):
            pass

        # Fall back to the active tab's outer canvas
        if target_canvas is None:
            try:
                idx = self.notebook.index("current")
            except (AttributeError, tk.TclError):
                return
            if not self._tab_canvases or idx >= len(self._tab_canvases):
                return
            target_canvas = self._tab_canvases[idx]

        try:
            if event.num == 4:
                target_canvas.yview_scroll(-1, "units")
            elif event.num == 5:
                target_canvas.yview_scroll(1, "units")
            else:
                target_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        except tk.TclError:
            pass

    # ---------------- Tab 2: Helper methods ----------------
    def _paste_into_var(self, string_var):
        """Paste clipboard content into a StringVar (used by Direct URL + Referer)."""
        try:
            text = self.clipboard_get().strip()
        except tk.TclError:
            text = ""
        if not text:
            messagebox.showinfo("Clipboard খালি", "ক্লিপবোর্ডে কোনো লেখা পাওয়া যায়নি।")
            return
        string_var.set(text)

    def _browse_m3u_file(self):
        """Open file dialog to pick an M3U/M3U8 file."""
        filepath = filedialog.askopenfilename(
            title="Select M3U / M3U8 File",
            filetypes=[("M3U/M3U8 Files", "*.m3u *.m3u8"), ("All Files", "*.*")]
        )
        if filepath:
            self.m3u_file_path_var.set(filepath)

    def _parse_m3u(self):
        """Parse the loaded local M3U file and populate the Treeview."""
        filepath = self.m3u_file_path_var.get().strip()
        if not filepath or not os.path.isfile(filepath):
            messagebox.showwarning("No file", "প্রথমে একটি M3U/M3U8 ফাইল select করুন।")
            return

        try:
            entries = parse_m3u_file(filepath)
        except Exception as e:
            self.m3u_status_label_var.set(f"❌ Parse ব্যর্থ: {e}")
            messagebox.showerror("Parse Error", str(e))
            return

        self._populate_m3u_entries(entries, source_desc=f"local file: {os.path.basename(filepath)}")

    def _load_m3u_from_url(self):
        """Fetch an M3U playlist from an online URL (a raw .m3u link, or a
        page/API endpoint whose response body is M3U content) and parse it.
        Runs the network fetch in a background thread so the UI doesn't freeze."""
        url = self.m3u_url_var.get().strip()
        if not url:
            messagebox.showwarning("No URL", "একটি M3U playlist URL দিন।")
            return
        if not (url.startswith("http://") or url.startswith("https://")):
            messagebox.showwarning("Invalid URL", "http:// বা https:// দিয়ে শুরু হওয়া একটি URL দিন।")
            return

        self.m3u_url_load_btn.config(state="disabled")
        self.m3u_status_label_var.set("⏳ URL থেকে playlist fetch করা হচ্ছে...")
        self._log(f"M3U: fetching playlist from URL — {url}")

        def worker():
            try:
                text = fetch_m3u_from_url(url)
                entries = parse_m3u_text(text)
            except Exception as e:
                self.after(0, lambda: self._on_m3u_url_load_error(str(e)))
                return
            self.after(0, lambda: self._on_m3u_url_load_success(entries, url))

        threading.Thread(target=worker, daemon=True).start()

    def _on_m3u_url_load_error(self, msg):
        self.m3u_url_load_btn.config(state="normal")
        self.m3u_status_label_var.set(f"❌ URL থেকে load ব্যর্থ: {msg}")
        self._log(f"M3U: URL fetch failed — {msg}")
        messagebox.showerror("Load Error", msg)

    def _on_m3u_url_load_success(self, entries, url):
        self.m3u_url_load_btn.config(state="normal")
        self.m3u_file_path_var.set("")  # clear local-file indicator; source is now this URL
        self._populate_m3u_entries(entries, source_desc=f"URL: {url}")

    def _populate_m3u_entries(self, entries, source_desc=""):
        """Shared by both local-file Parse and Load-from-URL: resets M3U
        state, fills the Treeview, and (re)builds the group filter list."""
        if not entries:
            self.m3u_status_label_var.set("⚠ কোনো media entry পাওয়া যায়নি।")
            return

        # De-duplicate entries that share the same media URL. Some source
        # playlists (e.g. auto-generated ones) end up with the same URL
        # listed multiple times, which previously caused the same file to
        # be downloaded/logged over and over (duplicate History rows).
        # Keep the first occurrence of each URL, in original order.
        original_count = len(entries)
        seen_urls = set()
        deduped = []
        for entry in entries:
            key = (entry.get('url') or "").strip().lower()
            if key and key in seen_urls:
                continue
            if key:
                seen_urls.add(key)
            deduped.append(entry)
        entries = deduped
        duplicates_removed = original_count - len(entries)

        # Reset state
        self.m3u_entries = entries
        self.m3u_check_vars = [tk.BooleanVar(value=True) for _ in entries]
        self.m3u_status_vars = {}

        # Clear existing treeview
        for iid in self.m3u_tree.get_children():
            self.m3u_tree.delete(iid)

        # Populate treeview
        for i, entry in enumerate(entries):
            title = entry.get('title') or f"Media {i+1}"
            group = entry.get('group') or "Default"
            status_var = tk.StringVar(value="⏳")
            self.m3u_status_vars[i] = status_var
            self.m3u_tree.insert(
                "", "end",
                iid=str(i),
                text="☑",
                values=(status_var.get(), f"{i+1}. {title}", group),
            )

        groups = sorted(set((e.get('group') or 'Default') for e in entries))
        dup_note = f" ({duplicates_removed}টি duplicate বাদ দেওয়া হয়েছে)" if duplicates_removed else ""
        self.m3u_status_label_var.set(
            f"✓ {len(entries)}টি entry parse হয়েছে{dup_note}। Groups: {', '.join(groups)}"
        )
        self._log(
            f"M3U parsed ({source_desc}): {len(entries)} entries"
            + (f", {duplicates_removed} duplicate URL(s) skipped" if duplicates_removed else "")
            + f", groups = {groups}"
        )

        # Populate the group filter list. Only show it when there's more
        # than one group — with a single group, filtering by group is
        # pointless (it would just be "select all").
        self.m3u_group_listbox.delete(0, "end")
        if len(groups) > 1:
            for g in groups:
                count = sum(1 for e in entries if (e.get('group') or 'Default') == g)
                self.m3u_group_listbox.insert("end", f"{g}  ({count})")
            self._m3u_group_names = groups  # parallel list: listbox index -> group name
            self.m3u_group_frame.pack(fill="x", pady=(0, 8), before=self.m3u_tree_wrap)
        else:
            self._m3u_group_names = []
            self.m3u_group_frame.pack_forget()

    def _m3u_apply_group_filter(self):
        """Select only the M3U entries whose group matches one of the
        group name(s) picked in the group listbox (multi-select)."""
        if not self.m3u_check_vars:
            messagebox.showinfo("No entries", "প্রথমে একটি M3U ফাইল parse করুন।")
            return
        picked_idxs = self.m3u_group_listbox.curselection()
        if not picked_idxs:
            messagebox.showinfo("No group selected", "অন্তত একটি group select করুন।")
            return
        picked_groups = {self._m3u_group_names[i] for i in picked_idxs}

        for i, entry in enumerate(self.m3u_entries):
            entry_group = entry.get('group') or "Default"
            should_select = entry_group in picked_groups
            self.m3u_check_vars[i].set(should_select)
            self.m3u_tree.item(str(i), text="☑" if should_select else "☐")

        self._log(f"M3U group filter applied: {', '.join(sorted(picked_groups))}")

    def _on_m3u_tree_click(self, event):
        """Toggle checkbox when the ✓ column is clicked."""
        region = self.m3u_tree.identify("region", event.x, event.y)
        if region != "tree":
            return  # Only toggle when clicking the tree column (the ✓ box)
        row_iid = self.m3u_tree.identify_row(event.y)
        if not row_iid:
            return
        try:
            idx = int(row_iid)
        except ValueError:
            return
        if idx >= len(self.m3u_check_vars):
            return
        new_val = not self.m3u_check_vars[idx].get()
        self.m3u_check_vars[idx].set(new_val)
        self.m3u_tree.item(row_iid, text="☑" if new_val else "☐")

    def _m3u_set_all(self, value):
        """Bulk select/deselect every M3U entry."""
        for i, var in enumerate(self.m3u_check_vars):
            var.set(value)
            self.m3u_tree.item(str(i), text="☑" if value else "☐")

    def _m3u_apply_range(self):
        """Apply a text range like '1,3,5-10' to the M3U selection."""
        text = self.m3u_range_var.get().strip()
        if not text:
            messagebox.showinfo("Empty range", "একটি range লিখুন (e.g. 1,3,5-10)।")
            return
        if not self.m3u_check_vars:
            messagebox.showinfo("No entries", "প্রথমে একটি M3U ফাইল parse করুন।")
            return
        selected_1based = self._parse_playlist_items(text)
        if not selected_1based:
            messagebox.showwarning("Invalid range", "Range parse করা যায়নি।")
            return
        # Convert to 0-based and select only those
        for i, var in enumerate(self.m3u_check_vars):
            should_select = (i + 1) in selected_1based
            var.set(should_select)
            self.m3u_tree.item(str(i), text="☑" if should_select else "☐")

    def _m3u_update_tree_status(self, idx, symbol):
        """Update the Status column icon for one M3U row."""
        try:
            self.m3u_tree.set(str(idx), "status", symbol)
        except tk.TclError:
            pass

    # ---------------- Tab 2: Direct URL download ----------------
    def _start_direct_download(self):
        if yt_dlp is None:
            messagebox.showerror("Missing dependency",
                                 "Please install yt-dlp first:\n\npip install yt-dlp")
            return

        if self.schedule_enabled_var.get():
            delay = self._get_delay_ms()
            if delay > 0:
                self.status_var.set(f"Scheduled: starts in {delay//1000}s")
                self._log(f"Direct download scheduled at {self.schedule_time_var.get()}. Waiting...")
                self.direct_download_btn.config(state="disabled")
                self.after(delay, self._start_direct_download_actual)
                return

        self._start_direct_download_actual()

    def _start_direct_download_actual(self):
        url = self.direct_url_var.get().strip()
        referer = self.direct_referer_var.get().strip()
        if not url:
            messagebox.showwarning("No URL", "Media URL দিন।")
            return
        # No confirmation needed if referer is blank -- _download_url_with_headers
        # will auto-fill it from the URL's own origin.

        out_dir = self.download_dir.get().strip()
        if not out_dir:
            messagebox.showwarning("No folder", "Download folder select করুন।")
            return
        os.makedirs(out_dir, exist_ok=True)
        self._save_last_dir()

        user_agent = self.direct_user_agent_var.get().strip() or None

        self.stop_flag = False
        self.pause_flag = False
        self._current_output_file = None
        self.progress["value"] = 0
        self._reset_stats()
        self.status_var.set("Starting direct download...")

        self.direct_download_btn.config(state="disabled")
        self.download_btn.config(state="disabled")
        self.pause_btn.config(state="disabled")   # pause not supported for direct URL yet
        self.stop_btn.config(state="normal")

        self._log(f"[Direct] Downloading: {url}")
        if referer:
            self._log(f"[Direct] Referer: {referer}")

        self.worker_thread = threading.Thread(
            target=self._direct_download_worker,
            args=(url, referer, user_agent, out_dir),
            daemon=True,
        )
        self.worker_thread.start()

    def _direct_download_worker(self, url, referer, user_agent, out_dir):
        try:
            self._download_url_with_headers(url, referer, user_agent, out_dir)
            if self.stop_flag:
                self.status_var.set("Stopped.")
                self._log("[Direct] Stopped by user.")
            else:
                self.status_var.set("Done!")
                self.progress["value"] = 100
                self.percent_var.set("100%")
                self.speed_var.set("-- KB/s")
                self.eta_var.set("ETA --:--")
                self._log("[Direct] ✅ Download completed.")
                self._save_to_history(os.path.basename(self._current_output_file or "Direct Download"), url, self._current_output_file)
                is_playlist = False
                output_file = self._current_output_file
                self.after(0, lambda: self._show_completion_modal(is_playlist, output_file, out_dir))
        except Exception as e:
            if self.stop_flag:
                self.status_var.set("Stopped.")
                self._log("[Direct] Stopped by user.")
            else:
                self.status_var.set("Error occurred.")
                self._log(f"[Direct] ❌ Error: {e}")
                err_msg = str(e)
                self.after(0, lambda m=err_msg: messagebox.showerror("Download error", m))
        finally:
            self.after(0, self._reset_tab2_download_buttons)

    def _reset_tab2_download_buttons(self):
        self.direct_download_btn.config(state="normal")
        self.m3u_download_btn.config(state="normal")
        self.download_btn.config(state="normal")
        self.pause_btn.config(text="⏸  Pause", command=self._pause_download,
                              state="disabled", style="Ghost.TButton")
        self.stop_btn.config(state="disabled")

    @staticmethod
    def _auto_referer_from_url(url):
        """Derive a plausible Referer from a media URL's own origin, e.g.
        'https://cdn.example.com/videos/x.mp4?token=..' -> 'https://cdn.example.com/'.
        Returns '' if the URL can't be parsed into scheme+host."""
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            if parsed.scheme and parsed.netloc:
                return f"{parsed.scheme}://{parsed.netloc}/"
        except Exception:
            pass
        return ""

    def _download_url_with_headers(self, url, referer, user_agent, out_dir):
        """Download a single URL through yt-dlp with custom HTTP headers.

        If no referer was given (Direct URL box left empty, or an M3U entry
        with no #EXTVLCOPT:http-referrer= tag), auto-fill it with the URL's
        own origin (scheme://host/). Many CDN/ISP-FTP servers only check that
        *some* referer from their own domain is present, so this alone fixes
        a lot of 403 Forbidden errors without the user typing anything."""
        referer = referer.strip() if referer else ""
        if not referer:
            auto_referer = self._auto_referer_from_url(url)
            if auto_referer:
                referer = auto_referer
                self._log(f"[Auto-referer] কোনো Referer দেওয়া হয়নি — auto বসানো হলো: {referer}")

        headers = {}
        if referer:
            headers['Referer'] = referer
        if user_agent:
            headers['User-Agent'] = user_agent

        # Try to extract a filename from the URL, otherwise use %(title)s
        filename_from_url = os.path.basename(url.split('?')[0].rstrip('/'))
        if filename_from_url and '.' in filename_from_url:
            # URL has a proper filename with extension - use it as-is
            try:
                from urllib.parse import unquote
                filename_from_url = unquote(filename_from_url)
            except Exception:
                pass
            outtmpl = os.path.join(out_dir, filename_from_url)
        else:
            outtmpl = os.path.join(out_dir, "%(title)s.%(ext)s")

        ydl_opts = {
            "outtmpl": outtmpl,
            "progress_hooks": [self._progress_hook],
            "postprocessor_hooks": [self._postprocessor_hook],
            "http_headers": headers,
            "continuedl": True,
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": False,
            **self._ffmpeg_opt(),
            **self._ratelimit_opt(),
            **self._fast_download_opt(),
        }

        # _fast_download_opt() sets this when aria2c (with its RPC interface
        # enabled) is the downloader for this file -- start a background
        # poller so the progress bar / speed / ETA / size boxes actually
        # update during Fast Download (see _poll_aria2c_progress docstring).
        aria2c_port = self._current_aria2c_rpc_port
        poll_thread = None
        stop_poll = threading.Event()
        if aria2c_port:
            poll_thread = threading.Thread(
                target=self._poll_aria2c_progress,
                args=(aria2c_port, stop_poll),
                daemon=True,
            )
            poll_thread.start()

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        finally:
            if poll_thread:
                stop_poll.set()
                poll_thread.join(timeout=2)

    # ---------------- Tab 2: M3U batch download ----------------
    def _start_m3u_batch_download(self):
        if yt_dlp is None:
            messagebox.showerror("Missing dependency",
                                 "Please install yt-dlp first:\n\npip install yt-dlp")
            return
        if not self.m3u_entries:
            messagebox.showwarning("No entries", "প্রথমে একটি M3U ফাইল parse করুন।")
            return

        selected_indices = [i for i, var in enumerate(self.m3u_check_vars) if var.get()]
        if not selected_indices:
            messagebox.showwarning("Nothing selected", "অন্তত একটি entry select করুন।")
            return

        out_dir = self.download_dir.get().strip()
        if not out_dir:
            messagebox.showwarning("No folder", "Download folder select করুন।")
            return
        os.makedirs(out_dir, exist_ok=True)
        self._save_last_dir()

        self.stop_flag = False
        self.pause_flag = False
        self._current_output_file = None
        self.progress["value"] = 0
        self._reset_stats()
        self.status_var.set("Starting M3U batch download...")

        self.direct_download_btn.config(state="disabled")
        self.m3u_download_btn.config(state="disabled")
        self.download_btn.config(state="disabled")
        self.pause_btn.config(state="disabled")  # not supported for batch
        self.stop_btn.config(state="normal")

        self._log(f"[M3U] Batch শুরু — {len(selected_indices)}টি entry download হবে।")

        self.worker_thread = threading.Thread(
            target=self._m3u_batch_worker,
            args=(selected_indices, out_dir),
            daemon=True,
        )
        self.worker_thread.start()

    def _m3u_batch_worker(self, selected_indices, out_dir):
        """Runs in a worker thread: download every selected M3U entry into
        subfolders named after its 'group' field."""
        total = len(selected_indices)
        success = 0
        failed = 0

        for i, idx in enumerate(selected_indices, start=1):
            if self.stop_flag:
                break
            entry = self.m3u_entries[idx]
            title = entry.get('title') or f"Media {idx+1}"
            group = entry.get('group') or "Default"
            group_folder = sanitize_folder_name(group)
            target_dir = os.path.join(out_dir, group_folder)
            os.makedirs(target_dir, exist_ok=True)

            self._current_m3u_index = idx
            status_var = self.m3u_status_vars.get(idx)
            if status_var:
                status_var.set("⬇")
            self.after(0, lambda i=idx: self._m3u_update_tree_status(i, "⬇"))

            self.playlist_progress_var.set(f"{i}/{total}")
            self.status_var.set(f"[M3U {i}/{total}] {title}")
            self._log(f"[M3U {i}/{total}] Downloading: {title}  →  {group_folder}/")

            try:
                self._download_url_with_headers(
                    entry['url'],
                    entry.get('referer'),
                    entry.get('user_agent'),
                    target_dir,
                )
                if self.stop_flag:
                    self.after(0, lambda i=idx: self._m3u_update_tree_status(i, "⏳"))
                    if status_var:
                        status_var.set("⏳")
                    break
                success += 1
                if status_var:
                    status_var.set("✅")
                self.after(0, lambda i=idx: self._m3u_update_tree_status(i, "✅"))
                self._log(f"[M3U {i}/{total}] ✅ Done: {title}")
                self._save_to_history(title, entry['url'], self._current_output_file)
            except Exception as e:
                if self.stop_flag:
                    self.after(0, lambda i=idx: self._m3u_update_tree_status(i, "⏳"))
                    if status_var:
                        status_var.set("⏳")
                    break
                failed += 1
                if status_var:
                    status_var.set("❌")
                self.after(0, lambda i=idx: self._m3u_update_tree_status(i, "❌"))
                self._log(f"[M3U {i}/{total}] ❌ Failed: {title}  →  {e}")

        self._current_m3u_index = None

        if self.stop_flag:
            self.status_var.set("Stopped.")
            self._log(f"[M3U] Stopped by user. Success: {success}, Failed: {failed}, Total: {total}")
        else:
            self.status_var.set("M3U batch finished!")
            self.progress["value"] = 100
            self.percent_var.set("100%")
            self.speed_var.set("-- KB/s")
            self.eta_var.set("ETA --:--")
            self._log(f"[M3U] ✅ Batch complete — Success: {success}, Failed: {failed}, Total: {total}")
            self.after(0, lambda: messagebox.showinfo(
                "M3U Batch Complete",
                f"মোট {total}টি entry:\n✅ সফল: {success}\n❌ ব্যর্থ: {failed}"
            ))

        self.after(0, self._reset_tab2_download_buttons)

    def _cookies_opt(self):
        browser = self.browser_var.get()
        if browser and browser != "None":
            return {"cookiesfrombrowser": (browser,)}
        return {}

    def _ffmpeg_opt(self):
        if FFMPEG_DIR:
            return {"ffmpeg_location": FFMPEG_DIR}
        return {}

    def _ratelimit_opt(self):
        """Throttle download speed to whatever the user set in the Speed
        limit box (a plain number + KB/s or MB/s dropdown). yt-dlp's
        `ratelimit` caps bytes/sec — e.g. if the connection could do 8MB/s
        but the user sets 5 MB/s, yt-dlp automatically paces the download
        down to ~5MB/s max (like --limit-rate 5M)."""
        limit_bytes = parse_speed_limit(self.speed_limit_value_var.get(), self.speed_limit_unit_var.get())
        if limit_bytes:
            return {"ratelimit": limit_bytes}
        return {}

    @staticmethod
    def _aria2c_available():
        # Prefer the bundled aria2c\aria2c.exe next to the app; fall back to
        # a system PATH lookup only if no bundled copy was found (e.g. when
        # running yt_dlp_gui.py directly from source without the aria2c\ folder).
        return ARIA2C_PATH is not None or shutil.which("aria2c") is not None

    def _fast_download_engine_label(self):
        """Text shown under the Fast Download checkbox saying which engine
        will actually be used (aria2c if available, else yt-dlp native)."""
        if self._aria2c_available():
            return "✓ aria2c পাওয়া গেছে — চালু করলে aria2c দিয়ে multi-connection download হবে।"
        return ("ℹ aria2c পাওয়া যায়নি — চালু করলে yt-dlp-এর নিজস্ব concurrent-chunk downloader ব্যবহার হবে "
                "(aria2c থাকলে সাধারণত আরও ভালো speed পাওয়া যায়)।")

    @staticmethod
    def _pick_free_port():
        """Ask the OS for a free local TCP port (bind to port 0, read it
        back, then release it) -- used for aria2c's RPC listener."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def _fast_download_opt(self):
        """Multi-connection download options for direct CDN/ISP-FTP links
        (Direct URL + M3U tab only). These are plain, non-fragmented HTTP
        files, so parallel range-request connections are what actually
        speeds things up -- equivalent to:
            yt-dlp --downloader aria2c --downloader-args "aria2c: -x N -s N -k 1M" <URL>
        or, when aria2c isn't available, yt-dlp's own native equivalent:
            yt-dlp --concurrent-fragments N --http-chunk-size 10M --buffer-size 16M <URL>

        NOTE on progress: when yt-dlp hands a download off to an external
        downloader like aria2c, it does NOT get live progress back from it
        (no speed/eta/downloaded-bytes updates -- yt-dlp only finds out once
        the whole file is done). So aria2c is started with its own RPC
        interface enabled on a free local port, and _poll_aria2c_progress()
        polls that RPC endpoint in a background thread and feeds the numbers
        into the same _progress_hook() the native downloader uses, which is
        what actually keeps the progress bar / speed / ETA / size boxes
        updating during Fast Download.
        """
        self._current_aria2c_rpc_port = None
        if not self.fast_download_var.get():
            return {}
        try:
            n = int(self.fast_download_connections_var.get())
        except (TypeError, ValueError):
            n = 16
        n = max(1, min(n, 32))

        if self._aria2c_available():
            # Use the full path to the bundled aria2c.exe when we have one, so
            # this works even though we tell users NOT to add aria2c to PATH.
            # yt-dlp accepts either a bare name (PATH lookup) or a full path here.
            aria2c_exe = ARIA2C_PATH or "aria2c"
            rpc_port = self._pick_free_port()
            self._current_aria2c_rpc_port = rpc_port
            self._log(f"⚡ Fast download: aria2c (-x {n} -s {n} -k 1M) [{aria2c_exe}]")
            return {
                "external_downloader": aria2c_exe,
                "external_downloader_args": {
                    "aria2c": [
                        "-x", str(n), "-s", str(n), "-k", "1M",
                        # Local-only RPC so we can poll real progress (see note above).
                        "--enable-rpc=true",
                        f"--rpc-listen-port={rpc_port}",
                        "--rpc-listen-all=false",
                    ]
                },
            }

        self._log(f"⚡ Fast download: yt-dlp native (concurrent-fragments {n}, chunk 10M, buffer 16M)")
        return {
            "concurrent_fragment_downloads": n,
            "http_chunk_size": 10 * 1024 * 1024,   # 10M — splits the file into range-request chunks
            "buffersize": 16 * 1024 * 1024,        # 16M
        }

    def _aria2c_rpc_call(self, port, method, params=None):
        """Minimal JSON-RPC 2.0 client for aria2c's local RPC interface
        (stdlib-only, no extra dependency needed)."""
        payload = json.dumps({
            "jsonrpc": "2.0", "id": "ytdlpgui",
            "method": method, "params": params or [],
        }).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/jsonrpc",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _poll_aria2c_progress(self, port, stop_event):
        """Runs in a background thread for the lifetime of one aria2c
        download. yt-dlp's progress_hooks stay silent while an external
        downloader is running (see _fast_download_opt's docstring), so this
        polls aria2c's own RPC endpoint every ~0.3s instead and pushes the
        numbers into _progress_hook() -- the same function the native
        downloader uses -- so the progress bar / speed / ETA / size boxes
        keep updating during Fast Download too.

        It also watches self.stop_flag. aria2c runs as its own separate OS
        process (not inside yt-dlp's normal download loop), so just raising
        DownloadCancelled from _progress_hook -- which is what stops the
        native downloader -- does nothing to it; aria2c would keep
        downloading in the background even after the UI says "Stopped".
        So when Stop is clicked, this sends aria2c a forceShutdown command
        over the same RPC connection, which actually kills it.
        """
        keys = ["totalLength", "completedLength", "downloadSpeed", "files"]
        shutdown_sent = False
        while not stop_event.is_set():
            if self.stop_flag and not shutdown_sent:
                try:
                    self._aria2c_rpc_call(port, "aria2.forceShutdown")
                    shutdown_sent = True
                    self._log("⏹ Fast download (aria2c) বন্ধ করা হচ্ছে...")
                except Exception:
                    pass  # RPC may not be up yet -- retry on the next loop tick
            try:
                result = self._aria2c_rpc_call(port, "aria2.tellActive", [keys])
                active = result.get("result") or []
                if active:
                    d = active[0]
                    total = int(d.get("totalLength") or 0)
                    downloaded = int(d.get("completedLength") or 0)
                    speed = int(d.get("downloadSpeed") or 0)
                    eta = int((total - downloaded) / speed) if speed and total else None
                    filename = ""
                    files = d.get("files") or []
                    if files:
                        filename = os.path.basename(files[0].get("path", ""))
                    self._progress_hook({
                        "status": "downloading",
                        "total_bytes": total or None,
                        "downloaded_bytes": downloaded,
                        "speed": speed or None,
                        "eta": eta,
                        "filename": filename,
                    })
            except Exception:
                pass  # RPC not up yet / already torn down -- just retry or exit
            stop_event.wait(0.3)

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
        self._recompute_estimated_size()

    # ---------------- Dynamic quality dropdown + size estimate ----------------
    def _update_quality_dropdown_values(self, heights_sorted_desc):
        """Rebuild the quality dropdown from real detected heights (union of
        all playlist videos, or just the one video's heights in single mode).
        'Best available' is always kept as the first/default entry."""
        labels = [BEST_AVAILABLE_LABEL]
        seen = set()
        for h in heights_sorted_desc:
            lbl = height_label(h)
            if lbl not in seen:
                seen.add(lbl)
                labels.append(lbl)
        if not heights_sorted_desc:
            labels = list(DEFAULT_QUALITY_LABELS)

        current = self.quality_var.get()
        self.quality_combo.config(values=labels)
        if current not in labels:
            self.quality_var.set(BEST_AVAILABLE_LABEL)

    def _on_quality_changed(self, event=None):
        for idx in self._playlist_format_heights.keys():
            self._update_video_size_label(idx)
        self._recompute_estimated_size()

    def _selected_height_target(self):
        """The pixel height the current quality selection maps to, for
        building the format selector / size estimate. None means audio-only
        (no video track needed)."""
        if self.audio_only_var.get():
            return None
        return label_to_height(self.quality_var.get())

    @staticmethod
    def _estimate_for_heights(heights, audio_size, target_height):
        """Given one video's {height: filesize} map, figure out which height
        actually gets downloaded for `target_height` (best available <=
        target, or that video's lowest/worst if nothing qualifies -- same
        fallback the real format selector uses) and its approximate total
        size in bytes. Returns (size_bytes_or_None, chosen_height_or_None)."""
        if target_height is None:
            return audio_size, None
        if not heights:
            return None, None
        candidates = [h for h in heights if h <= target_height]
        chosen_h = max(candidates) if candidates else min(heights)
        size = heights.get(chosen_h)
        if not size:
            # This exact height has no known/estimated size (rare now that
            # tbr*duration is used as a fallback, but still possible) --
            # borrow the closest other height's size rather than showing
            # nothing at all.
            known = {h: s for h, s in heights.items() if s}
            if known:
                closest_h = min(known, key=lambda h: abs(h - chosen_h))
                size = known[closest_h]
        if size and audio_size:
            size += audio_size  # approximate: covers the common DASH video+audio merge case
        return size, chosen_h

    def _selected_playlist_indices_for_estimate(self):
        selected = self._selected_playlist_indices()
        if selected is not None:
            return selected
        return set(self._playlist_format_heights.keys())

    def _update_video_size_label(self, idx):
        var = self.video_size_vars.get(idx)
        if not var:
            return
        heights = self._playlist_format_heights.get(idx)
        audio_size = self._playlist_audio_size.get(idx)
        if not heights:
            var.set("")
            return
        target = self._selected_height_target()
        size, chosen_h = self._estimate_for_heights(heights, audio_size, target)
        if not size:
            var.set("")
            return
        label = height_label(chosen_h) if chosen_h else "audio"
        var.set(f"{label} · ≈{format_filesize(size)}")

    def _recompute_estimated_size(self):
        """Recalculate the total estimated download size for the current
        quality/audio-only selection, over whichever videos are actually
        selected right now, and push it to both display spots (next to the
        Download button, and the Progress card's Est. Size stat)."""
        target = self._selected_height_target()
        total_bytes = 0
        any_known = False

        if self.playlist_var.get():
            for idx in self._selected_playlist_indices_for_estimate():
                heights = self._playlist_format_heights.get(idx)
                audio_size = self._playlist_audio_size.get(idx)
                if not heights:
                    continue
                size, _h = self._estimate_for_heights(heights, audio_size, target)
                if size:
                    total_bytes += size
                    any_known = True
        else:
            heights = self._single_format_heights
            audio_size = self._single_audio_size
            if heights:
                size, _h = self._estimate_for_heights(heights, audio_size, target)
                if size:
                    total_bytes = size
                    any_known = True

        if any_known:
            text = format_filesize(total_bytes)
            self.estimated_size_var.set(f"Est. size: ≈{text}")
            self.estimated_size_progress_var.set(f"≈{text}")
        else:
            self.estimated_size_var.set("")
            self.estimated_size_progress_var.set("--")

    def _selected_format_string(self):
        """Build the actual yt-dlp format selector for the current quality
        choice. Audio-only mode ignores this (uses bestaudio/best directly);
        'Best available' uses the original unconstrained selector; anything
        else gets the height-capped selector with a per-video worst fallback."""
        if self.quality_var.get() == BEST_AVAILABLE_LABEL:
            return BEST_AVAILABLE_FORMAT
        return build_format_selector(self._selected_height_target())

    # ---------------- Clipboard auto-detect + Paste ----------------
    @staticmethod
    def _looks_like_youtube_url(text):
        if not text:
            return False
        text = text.strip()
        if not text or len(text) > 500 or "\n" in text:
            return False
        return bool(YOUTUBE_URL_RE.search(text))

    def _paste_url(self):
        try:
            text = self.clipboard_get().strip()
        except tk.TclError:
            text = ""
        if not text:
            messagebox.showinfo("Clipboard খালি", "ক্লিপবোর্ডে কোনো লেখা পাওয়া যায়নি।")
            return
        self.url_var.set(text)
        self._last_clipboard = text
        self._on_url_committed()

    # ---------------- Batch / multiple-URL mode ----------------
    def _on_batch_mode_toggle(self):
        """Switch the URL card between the single-URL entry and the
        multi-line batch textarea."""
        if self.batch_mode_var.get():
            self.single_url_frame.pack_forget()
            self.batch_url_frame.pack(fill="x")
        else:
            self.batch_url_frame.pack_forget()
            self.single_url_frame.pack(fill="x")
        self._sync_scrollregion()

    def _parse_batch_urls(self):
        """Read the batch textarea, one URL per line. Blank lines and lines
        starting with '#' are ignored; duplicate lines are only queued once."""
        raw = self.batch_text.get("1.0", "end")
        urls = []
        seen = set()
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line not in seen:
                seen.add(line)
                urls.append(line)
        return urls

    def _update_batch_count(self):
        count = len(self._parse_batch_urls())
        self.batch_count_var.set(f"{count} URL" if count != 1 else "1 URL")

    def _paste_batch_urls(self):
        try:
            text = self.clipboard_get().strip()
        except tk.TclError:
            text = ""
        if not text:
            messagebox.showinfo("Clipboard খালি", "ক্লিপবোর্ডে কোনো লেখা পাওয়া যায়নি।")
            return
        current = self.batch_text.get("1.0", "end").strip()
        if current:
            self.batch_text.insert("end", "\n" + text)
        else:
            self.batch_text.insert("end", text)
        self._update_batch_count()

    def _clear_batch_urls(self):
        self.batch_text.delete("1.0", "end")
        self._update_batch_count()
        self._clear_batch_queue_list()

    def _clear_batch_queue_list(self):
        parent = self.batch_queue_frame.master
        self.batch_queue_frame.destroy()
        self.batch_queue_frame = ttk.Frame(parent, style="Card.TFrame")
        self.batch_queue_frame.pack(fill="x", pady=(8, 0))
        self.batch_queue = []
        self._sync_scrollregion()

    def _render_batch_queue_list(self, urls):
        """Build the pending/downloading/done/failed status rows for every
        queued URL, shown live under the batch textarea while it downloads."""
        self._clear_batch_queue_list()
        for i, url in enumerate(urls, start=1):
            row = ttk.Frame(self.batch_queue_frame, style="Card.TFrame")
            row.pack(fill="x", pady=1)
            status_var = tk.StringVar(value="⏳")
            ttk.Label(row, textvariable=status_var, style="Body.TLabel", width=2).pack(side="left")
            display = url if len(url) <= 70 else url[:67] + "..."
            ttk.Label(row, text=f"{i}. {display}", style="Body.TLabel").pack(side="left", anchor="w")
            self.batch_queue.append({"url": url, "status_var": status_var})
        self._sync_scrollregion()

    def _start_clipboard_watcher(self):
        try:
            self._last_clipboard = self.clipboard_get()
        except tk.TclError:
            self._last_clipboard = ""
        self._poll_clipboard()

    def _poll_clipboard(self):
        try:
            current = self.clipboard_get()
        except tk.TclError:
            current = ""
        if current != self._last_clipboard:
            self._last_clipboard = current
            candidate = current.strip()
            # Don't clobber the URL box while the user is actively typing/editing it.
            focused_elsewhere = self.focus_get() is not self.url_entry
            if (self._looks_like_youtube_url(candidate)
                    and focused_elsewhere
                    and candidate != self.url_var.get().strip()):
                self.url_var.set(candidate)
                self._log(f"Clipboard থেকে YouTube URL auto-paste হয়েছে: {candidate}")
                self._on_url_committed()
        # Keep polling every second for as long as the app is open.
        self.after(1000, self._poll_clipboard)

    # ---------------- Refresh (button + Ctrl+R) ----------------
    def _on_refresh_shortcut(self, event=None):
        self._refresh_app()
        return "break"

    def _refresh_app(self):
        if self.worker_thread and self.worker_thread.is_alive():
            if not messagebox.askyesno(
                "Refresh",
                "একটি ডাউনলোড চলছে। রিফ্রেশ করলে সেটি বন্ধ হয়ে যাবে। আপনি কি নিশ্চিত?"
            ):
                return
            self.stop_flag = True

        # Reset every field back to its startup default, like a fresh app open.
        self.url_var.set("")
        self.playlist_var.set(False)
        self.select_all_var.set(True)
        self.playlist_items_var.set("")
        self.playlist_numbering_var.set(True)
        self.audio_only_var.set(False)
        self.embed_thumbnail_var.set(True)
        self.quality_var.set("Best available")
        self.browser_var.set("None")
        self.speed_limit_value_var.set("")
        self._retry_suffix = ""
        self._loaded_titles = []
        self.pause_flag = False
        self._paused_context = None
        self._current_downloading_idx = None

        self._format_fetch_generation += 1  # cancel any in-flight format probes
        self._single_format_heights = {}
        self._single_audio_size = None
        self._playlist_format_heights = {}
        self._playlist_audio_size = {}
        self.video_size_vars = {}
        self.estimated_size_var.set("")
        self.estimated_size_progress_var.set("--")
        self.quality_combo.config(values=DEFAULT_QUALITY_LABELS)

        self.batch_mode_var.set(False)
        self.batch_text.delete("1.0", "end")
        self._update_batch_count()
        self._clear_batch_queue_list()
        self.batch_url_frame.pack_forget()
        self.single_url_frame.pack(fill="x")
        self._batch_current_index = None

        self.status_var.set("Idle")
        self._reset_stats()
        self.progress["value"] = 0

        self._clear_titles_list()
        self.titles_status_var.set(
            "Tick 'Download entire playlist' and add a URL to load video titles here."
        )
        self.select_all_titles_btn.config(state="disabled")
        self.deselect_all_titles_btn.config(state="disabled")

        self.log_box.config(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.config(state="disabled")

        self.download_btn.config(state="normal")
        self.pause_btn.config(text="⏸  Pause", command=self._pause_download, state="disabled", style="Ghost.TButton")
        self.stop_btn.config(state="disabled")
        self._toggle_quality_state()
        self._sync_playlist_controls()
        self._sync_scrollregion()

        self._log("App refreshed.")
        if yt_dlp is None:
            self._log("yt-dlp is not installed. Run: pip install yt-dlp")
        if FFMPEG_DIR:
            self._log(f"ffmpeg found: {FFMPEG_DIR}")
        else:
            self._log("ffmpeg not found (only needed for merging/MP3 conversion).")

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
        self._maybe_autoload_formats()

    def _maybe_autoload_titles(self):
        if self.playlist_var.get():
            url = self.url_var.get().strip()
            if url:
                self._load_playlist_titles(url)

    def _on_url_committed(self):
        """Called whenever the URL box gets a new value (typed + Enter/blur,
        Paste button, or clipboard auto-fill). Kicks off both the existing
        playlist-title load (if playlist mode is on) and the new real
        format/resolution detection for the size preview + dynamic quality
        dropdown."""
        self._maybe_autoload_titles()
        self._maybe_autoload_formats()

    def _maybe_autoload_formats(self):
        """Fetch the real available resolutions (and approximate sizes) for
        whatever is in the URL box right now, in the background, and use them
        to populate the quality dropdown + size estimate. Runs automatically
        every time the URL changes."""
        if yt_dlp is None:
            return
        url = self.url_var.get().strip()
        if not url:
            return

        # Bump the generation counter so any still-running fetch for a
        # previous URL notices it's stale and quietly gives up instead of
        # overwriting the UI with old data.
        self._format_fetch_generation += 1
        gen = self._format_fetch_generation

        self._single_format_heights = {}
        self._single_audio_size = None
        self._playlist_format_heights = {}
        self._playlist_audio_size = {}
        self.estimated_size_var.set("")
        self.estimated_size_progress_var.set("--")

        # Wipe the dropdown back to the generic placeholder list right away
        # (instead of leaving the *previous* video's real resolutions sitting
        # there) so a slow or failed fetch for the new URL never leaves a
        # stale "4K" (or any other) option visible from the old video.
        self._update_quality_dropdown_values([])

        if self.playlist_var.get():
            threading.Thread(target=self._fetch_playlist_formats_worker, args=(url, gen), daemon=True).start()
        else:
            threading.Thread(target=self._fetch_single_format_worker, args=(url, gen), daemon=True).start()

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
        """Wipe every existing title-row from the bounded titles_list_frame,
        but keep the frame + its parent canvas intact (they're now permanent
        widgets, not re-created every time)."""
        for child in list(self.titles_list_frame.winfo_children()):
            child.destroy()
        self.video_check_vars = []
        self.video_status_vars = {}
        self.video_size_vars = {}
        # Reset the inner titles-canvas scroll position so a fresh load starts
        # from the top of the list, not wherever the user last scrolled to.
        self.update_idletasks()
        self.titles_canvas.configure(scrollregion=self.titles_canvas.bbox("all"))
        self.titles_canvas.yview_moveto(0.0)
        self._sync_scrollregion()

    def _show_titles_placeholder(self, message=None):
        """Show a friendly placeholder inside the (now empty) titles box so
        the user knows what to do next."""
        text = message or (
            "Playlist URL paste করে '🔄 Load / Refresh Titles' চাপুন —\n"
            "video titles এখানে checkbox list হিসেবে দেখা যাবে।"
        )
        ttk.Label(
            self.titles_list_frame, text=text,
            style="Subtle.TLabel", justify="left"
        ).pack(anchor="w", padx=10, pady=14)
        self.update_idletasks()
        self.titles_canvas.configure(scrollregion=self.titles_canvas.bbox("all"))

    def _sync_scrollregion(self, reset_scroll=True):
        """Force Tk to recompute geometry right now (not on the next idle
        tick) and refresh the canvas scrollregion. Without this, destroying
        widgets (e.g. clearing the playlist title checkboxes) leaves the
        'Playlist Videos' card showing its old, larger height until some
        unrelated event happens to trigger a redraw.

        Also resets the scroll position back to the top by default. This
        matters because if the user had scrolled down while a long title
        list was showing, and that list then shrinks/empties, the canvas
        keeps its old scroll offset and ends up pointed at blank space below
        the now-smaller content -- looking exactly like the section never
        shrank at all, even though it did."""
        self.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        if reset_scroll:
            self.canvas.yview_moveto(0.0)

    def _load_playlist_titles(self, url):
        if yt_dlp is None:
            return
        self.titles_status_var.set("Loading playlist titles...")
        self.load_titles_btn.config(state="disabled")
        self.select_all_titles_btn.config(state="disabled")
        self.deselect_all_titles_btn.config(state="disabled")
        self._clear_titles_list()
        threading.Thread(target=self._fetch_playlist_titles_worker, args=(url,), daemon=True).start()

    def _extract_format_heights(self, url):
        """Full (non-flat) format probe for ONE video URL. Returns
        (heights, audio_size):
          heights: {height:int -> best known filesize in bytes, or None}
          audio_size: best audio-only track's filesize in bytes, or None
        Runs synchronously -- caller must invoke from a background thread."""
        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            **self._cookies_opt(),
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        formats = (info or {}).get("formats") or []
        duration = (info or {}).get("duration")  # seconds, used as a fallback size estimate

        def _size_of(f):
            """Real filesize if yt-dlp reports one; otherwise approximate it
            from the format's bitrate * the video's duration -- YouTube
            frequently omits filesize/filesize_approx on DASH formats, which
            was leaving the size estimate blank almost every time."""
            size = f.get("filesize") or f.get("filesize_approx")
            if size:
                return size
            tbr = f.get("tbr") or f.get("vbr") or f.get("abr")
            if tbr and duration:
                return int(tbr * 1000 / 8 * duration)  # tbr is in kbps
            return None

        heights = {}
        audio_size = None
        for f in formats:
            vcodec = f.get("vcodec")
            acodec = f.get("acodec")
            size = _size_of(f)
            h = f.get("height")
            if h and vcodec not in (None, "none"):
                prev = heights.get(h)
                if size:
                    if prev is None or size > prev:
                        heights[h] = size
                elif h not in heights:
                    heights[h] = None
            elif vcodec in (None, "none") and acodec not in (None, "none"):
                if size and (audio_size is None or size > audio_size):
                    audio_size = size
        return heights, audio_size

    @staticmethod
    def _entry_watch_url(entry):
        """Build a playable video URL from a flat-extracted playlist entry."""
        url = entry.get("url") or entry.get("webpage_url")
        if url and str(url).startswith("http"):
            return url
        vid = entry.get("id")
        if vid:
            return f"https://www.youtube.com/watch?v={vid}"
        return url

    def _extract_playlist_entries(self, url):
        """(idx, watch_url) pairs for every entry in a playlist -- a fast
        flat listing, used to know which URLs to probe for real formats."""
        opts = {
            "extract_flat": True,
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            **self._cookies_opt(),
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        entries = (info or {}).get("entries") or []
        result = []
        for idx, entry in enumerate(entries, start=1):
            if not entry:
                continue
            result.append((idx, self._entry_watch_url(entry)))
        return result

    def _fetch_single_format_worker(self, url, gen):
        try:
            heights, audio_size = self._extract_format_heights(url)
        except Exception as ex:
            if gen == self._format_fetch_generation:
                self.after(0, lambda: self._log(f"Format তথ্য আনা যায়নি: {ex}"))
                # Fetch failed for this URL -- make sure no stale resolution
                # list from a *previous* URL is left showing.
                self.after(0, lambda: self._update_quality_dropdown_values([]))
            return
        if gen != self._format_fetch_generation:
            return
        self.after(0, lambda: self._on_single_formats_loaded(heights, audio_size))

    def _on_single_formats_loaded(self, heights, audio_size):
        self._single_format_heights = heights
        self._single_audio_size = audio_size
        self._update_quality_dropdown_values(sorted(heights.keys(), reverse=True))
        self._recompute_estimated_size()
        if heights:
            found = ", ".join(height_label(h) for h in sorted(heights, reverse=True))
            self._log(f"Available resolutions detected: {found}")

    def _fetch_playlist_formats_worker(self, url, gen):
        try:
            entries = self._extract_playlist_entries(url)
        except Exception as ex:
            if gen == self._format_fetch_generation:
                self.after(0, lambda: self._log(f"Playlist format তথ্য আনা যায়নি: {ex}"))
                self.after(0, lambda: self._update_quality_dropdown_values([]))
            return
        total = len(entries)
        if not total:
            return
        if gen == self._format_fetch_generation:
            self.after(0, lambda: self._log(
                f"{total}টি ভিডিওর real resolution scan হচ্ছে (background এ) — বড় প্লেলিস্টে সময় লাগতে পারে।"
            ))
        for i, (idx, video_url) in enumerate(entries, start=1):
            if gen != self._format_fetch_generation:
                return
            try:
                heights, audio_size = self._extract_format_heights(video_url)
            except Exception:
                heights, audio_size = {}, None
            if gen != self._format_fetch_generation:
                return
            self.after(0, lambda idx=idx, heights=heights, audio_size=audio_size, i=i, total=total:
                       self._on_playlist_video_formats_loaded(idx, heights, audio_size, i, total))

    def _on_playlist_video_formats_loaded(self, idx, heights, audio_size, i, total):
        self._playlist_format_heights[idx] = heights
        self._playlist_audio_size[idx] = audio_size

        union = set()
        for h_map in self._playlist_format_heights.values():
            union.update(h_map.keys())
        self._update_quality_dropdown_values(sorted(union, reverse=True))
        self._update_video_size_label(idx)
        self._recompute_estimated_size()

        if i < total:
            self.titles_status_var.set(f"Formats স্ক্যান হচ্ছে: {i}/{total} ভিডিও...")
        else:
            self.titles_status_var.set(f"{total} videos scanned — quality list ready.")
            self._log("Playlist format scan complete.")

    def _extract_playlist_titles(self, url, raise_on_error=False):
        """Fetch (idx, title) pairs for every entry in a playlist. Runs
        synchronously -- caller must invoke this from a worker thread.
        Returns [] on failure (or if raise_on_error=True, re-raises)."""
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
                return []
            titles = []
            for idx, entry in enumerate(entries, start=1):
                if not entry:
                    continue
                title = entry.get("title") or f"Video {idx}"
                titles.append((idx, title))
            return titles
        except Exception:
            if raise_on_error:
                raise
            return []

    def _fetch_playlist_titles_worker(self, url):
        try:
            titles = self._extract_playlist_titles(url, raise_on_error=True)
            if not titles:
                self.after(0, lambda: self._on_titles_loaded([], "This doesn't look like a playlist URL."))
                return
            self.after(0, lambda: self._on_titles_loaded(titles, None))
        except Exception as ex:
            err = str(ex)
            self.after(0, lambda: self._on_titles_loaded([], f"Could not load titles: {err}"))

    def _on_titles_loaded(self, titles, error):
        self.load_titles_btn.config(state="normal")

        if error:
            self._clear_titles_list()
            self._show_titles_placeholder(f"⚠  {error}")
            self.titles_status_var.set(error)
            self._log(error)
            return
        if not titles:
            self._clear_titles_list()
            self._show_titles_placeholder("এই URL-এ কোনো playlist video পাওয়া যায়নি।")
            self.titles_status_var.set("No videos found in this playlist.")
            return

        self._loaded_titles = list(titles)
        self.select_all_titles_btn.config(state="normal")
        self.deselect_all_titles_btn.config(state="normal")
        self._render_title_checkboxes(titles)

    def _render_title_checkboxes(self, titles):
        """(Re)build the tick-box list for the given playlist titles.

        Any manual selection already typed into the 'Or select videos' box is
        respected first. Otherwise, videos whose title already matches a file
        in the current download folder are auto-unchecked (skipped) so only
        the missing/new videos stay ticked for download."""
        self._clear_titles_list()

        manual_preselected = None
        if not self.select_all_var.get() and self.playlist_items_var.get().strip():
            manual_preselected = self._parse_playlist_items(self.playlist_items_var.get())

        skipped_count = 0
        out_dir = self.download_dir.get().strip()
        existing_keys = self._scan_existing_titles(out_dir)

        for idx, title in titles:
            is_duplicate = self._normalize_title_key(title) in existing_keys
            if manual_preselected is not None:
                checked = idx in manual_preselected
            else:
                checked = not is_duplicate
                if is_duplicate:
                    skipped_count += 1
            var = tk.BooleanVar(value=checked)

            row = ttk.Frame(self.titles_list_frame, style="Card.TFrame")
            row.pack(fill="x", pady=1)

            # Status icon: ⏳ pending / ⬇ downloading / ✅ done / ❌ failed
            status_var = tk.StringVar(value="✅" if is_duplicate else "⏳")
            ttk.Label(row, textvariable=status_var, style="Body.TLabel", width=2).pack(side="left")

            cb = ttk.Checkbutton(row, text=f"{idx}. {title}",
                                  variable=var, command=self._on_title_check_changed)
            cb.pack(side="left", anchor="w")

            size_var = tk.StringVar(value="")
            ttk.Label(row, textvariable=size_var, style="Subtle.TLabel").pack(side="right", padx=(6, 0))

            self.video_check_vars.append((idx, var))
            self.video_status_vars[idx] = status_var
            self.video_size_vars[idx] = size_var
            self._update_video_size_label(idx)  # in case its formats were already scanned

        self._recompute_estimated_size()

        if manual_preselected is None and skipped_count:
            self.titles_status_var.set(
                f"{len(titles)} videos found — {skipped_count}টি folder-এ আগে থেকেই আছে (auto-skip), "
                f"{len(titles) - skipped_count}টি ডাউনলোড হবে।"
            )
            self._log(f"Folder-এর সাথে title মিলিয়ে {skipped_count}টি ভিডিও auto-skip করা হয়েছে।")
        else:
            self.titles_status_var.set(
                f"{len(titles)} videos found — tick the ones you want, or use the text box above."
            )

        # Sync select_all_var / playlist_items_var with whatever ended up checked above.
        self._on_title_check_changed()
        # Refresh the inner titles-canvas scrollregion so it can scroll the
        # newly-added rows (independent of the outer page scroll).
        self.update_idletasks()
        self.titles_canvas.configure(scrollregion=self.titles_canvas.bbox("all"))
        self.titles_canvas.yview_moveto(0.0)
        self._sync_scrollregion()

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
        self._recompute_estimated_size()

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

    @staticmethod
    def _normalize_title_key(text):
        """Collapse a title down to a bare comparable key so a playlist title
        and an on-disk filename compare equal even if numbering, quality
        suffixes, retry markers, or punctuation differ (e.g. a '/' in the
        title vs the '⧸' character some filesystems/yt-dlp save it as)."""
        text = (text or "").strip()
        text = re.sub(r'^\d+[\.\-\)]\s*', '', text)  # leading "01. " / "01- " / "01) "
        text = re.sub(r'\s+(1080p|720p|480p|360p)$', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\s+\(\d+\)$', '', text)  # trailing retry marker " (1)"
        return re.sub(r'[^a-z0-9]+', '', text.lower())

    @classmethod
    def _scan_existing_titles(cls, out_dir):
        """Return a set of normalized title-keys for every file already in
        out_dir, used to auto-skip playlist videos already downloaded there."""
        keys = set()
        if not out_dir or not os.path.isdir(out_dir):
            return keys
        for fname in os.listdir(out_dir):
            name, _ext = os.path.splitext(fname)
            key = cls._normalize_title_key(name)
            if key:
                keys.add(key)
        return keys

    def _choose_folder(self):
        folder = filedialog.askdirectory(initialdir=self.download_dir.get())
        if folder:
            self.download_dir.set(folder)
            self._save_last_dir()
            # Folder changed: re-run the duplicate check against the new folder
            # for any playlist titles that are already loaded.
            if self._loaded_titles:
                self._render_title_checkboxes(self._loaded_titles)

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

        if self.schedule_enabled_var.get():
            delay = self._get_delay_ms()
            if delay > 0:
                self.status_var.set(f"Scheduled: starts in {delay//1000}s")
                self._log(f"Download scheduled at {self.schedule_time_var.get()}. Waiting...")
                self.download_btn.config(state="disabled")
                self.after(delay, self._start_download_actual)
                return

        self._start_download_actual()

    def _start_download_actual(self):
        if self.batch_mode_var.get():
            self._start_batch_download()
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
        self.pause_flag = False
        self._retry_suffix = ""
        self._current_output_file = None
        self._current_downloading_idx = None
        self._paused_context = {"url": url, "out_dir": out_dir}
        self.download_btn.config(state="disabled")
        self.pause_btn.config(text="⏸  Pause", command=self._pause_download, state="normal", style="Ghost.TButton")
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
                target=self._prepare_playlist_download, args=(url, out_dir), daemon=True
            )
        else:
            self.worker_thread = threading.Thread(
                target=self._prepare_single_download, args=(url, out_dir), daemon=True
            )
        self.worker_thread.start()

    def _start_batch_download(self):
        """Queue every URL pasted into the batch textarea and download them
        one after another in a single worker thread."""
        urls = self._parse_batch_urls()
        if not urls:
            messagebox.showwarning(
                "No URL",
                "একটি বা একাধিক URL পেস্ট করুন (প্রতি লাইনে একটি করে)।"
            )
            return

        out_dir = self.download_dir.get().strip()
        if not out_dir:
            messagebox.showwarning("No folder", "Please choose a download location.")
            return

        os.makedirs(out_dir, exist_ok=True)
        self._save_last_dir()

        self.stop_flag = False
        self.pause_flag = False
        self._retry_suffix = ""
        self._current_output_file = None
        self._paused_context = None

        self._render_batch_queue_list(urls)

        self.download_btn.config(state="disabled")
        # Pause/Resume isn't supported for batch queues -- keep it disabled;
        # Stop still works and cancels the rest of the queue.
        self.pause_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.progress["value"] = 0
        self._reset_stats()
        self.status_var.set("Starting batch download...")
        self._log(f"Batch download শুরু হচ্ছে — মোট {len(urls)}টি URL queue-তে আছে।")

        self.worker_thread = threading.Thread(
            target=self._run_batch_queue, args=(urls, out_dir), daemon=True
        )
        self.worker_thread.start()

    def _run_batch_queue(self, urls, out_dir):
        """Runs in a worker thread: download each queued URL one by one,
        updating that row's status icon and the overall queue progress.
        A failure on one URL doesn't stop the rest of the queue; Stop does."""
        total = len(urls)
        success_count = 0
        fail_count = 0

        for i, url in enumerate(urls, start=1):
            if self.stop_flag:
                break

            self._batch_current_index = i
            self.playlist_progress_var.set(f"{i}/{total}")
            status_var = self.batch_queue[i - 1]["status_var"] if i - 1 < len(self.batch_queue) else None
            if status_var:
                status_var.set("⬇")

            self._retry_suffix = ""
            self._current_output_file = None
            self.status_var.set(f"Downloading ({i}/{total})...")
            self._log(f"[{i}/{total}] শুরু হচ্ছে: {url}")

            try:
                if self.audio_only_var.get():
                    self._download_audio_only(url, out_dir)
                else:
                    self._download_video(url, out_dir)

                if self.stop_flag:
                    if status_var:
                        status_var.set("⏳")
                    self._log(f"[{i}/{total}] থামানো হয়েছে (user stop)।")
                    break

                if status_var:
                    status_var.set("✅")
                success_count += 1
                self._log(f"[{i}/{total}] সম্পন্ন ✅")
            except Exception as e:
                if self.stop_flag:
                    if status_var:
                        status_var.set("⏳")
                    self._log(f"[{i}/{total}] থামানো হয়েছে (user stop)।")
                    break
                if status_var:
                    status_var.set("❌")
                fail_count += 1
                self._log(f"[{i}/{total}] ব্যর্থ ❌ — Error: {e}")

        self._cleanup_partial_files(out_dir)
        self._batch_current_index = None

        if self.stop_flag:
            self.status_var.set("Stopped.")
            self._log("Batch download বন্ধ করা হয়েছে।")
        else:
            self.status_var.set("Batch finished!")
            self.progress["value"] = 100
            self.percent_var.set("100%")
            self.speed_var.set("-- KB/s")
            self.eta_var.set("ETA --:--")
            self._log(f"Batch download শেষ — সফল: {success_count}, ব্যর্থ: {fail_count}, মোট: {total}")
            self.after(0, lambda: messagebox.showinfo(
                "Batch সম্পন্ন",
                f"মোট {total}টি URL এর মধ্যে:\n✅ সফল: {success_count}\n❌ ব্যর্থ: {fail_count}"
            ))

        self.after(0, self._reset_download_buttons)

    def _reset_download_buttons(self):
        self.download_btn.config(state="normal")
        self.pause_btn.config(text="⏸  Pause", command=self._pause_download, state="disabled", style="Ghost.TButton")
        self.stop_btn.config(state="disabled")

    def _fetch_title(self, url):
        """Lightweight metadata fetch to learn the video's title before downloading."""
        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            **self._cookies_opt(),
            **self._ffmpeg_opt(),
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        return (info or {}).get("title")

    @staticmethod
    def _find_existing_files(out_dir, base_name):
        """Find files in out_dir matching base_name or 'base_name (n)', any extension."""
        if not base_name or not os.path.isdir(out_dir):
            return []
        pattern = re.compile(r"^" + re.escape(base_name) + r"( \(\d+\))?$")
        matches = []
        for fname in os.listdir(out_dir):
            name, _ext = os.path.splitext(fname)
            if pattern.match(name):
                matches.append(fname)
        return matches

    def _ask_duplicate_decision(self, existing_filename):
        """Blocks the calling (worker) thread until the user answers the modal
        shown on the main thread. Returns 'cancel' or 'again'."""
        result = {"decision": "cancel"}
        event = threading.Event()

        def show_modal():
            self._show_duplicate_modal(existing_filename, result, event)

        self.after(0, show_modal)
        event.wait()
        return result["decision"]

    def _show_duplicate_modal(self, existing_filename, result, event):
        modal = tk.Toplevel(self)
        modal.title("Already downloaded")
        modal.configure(bg=BG_CARD)
        modal.transient(self)
        modal.resizable(False, False)

        wrap = ttk.Frame(modal, style="Card.TFrame", padding=20)
        wrap.pack(fill="both", expand=True)

        ttk.Label(wrap, text="⚠ এই ভিডিওটি আগে থেকেই ডাউনলোড করা আছে",
                  style="Body.TLabel", font=("Segoe UI Semibold", 11, "bold")
                  ).pack(anchor="w", pady=(0, 8))
        ttk.Label(wrap, text=f"ফাইল: {existing_filename}", style="Subtle.TLabel",
                  wraplength=380, justify="left").pack(anchor="w", pady=(0, 4))
        ttk.Label(wrap, text="আবার ডাউনলোড করতে চান, নাকি বাতিল করবেন?",
                  style="Subtle.TLabel", wraplength=380, justify="left").pack(anchor="w", pady=(0, 14))

        btn_row = ttk.Frame(wrap, style="Card.TFrame")
        btn_row.pack(fill="x")

        def on_cancel():
            result["decision"] = "cancel"
            event.set()
            modal.destroy()

        def on_again():
            result["decision"] = "again"
            event.set()
            modal.destroy()

        ttk.Button(btn_row, text="Cancel", style="Stop.TButton", command=on_cancel).pack(side="left")
        ttk.Button(btn_row, text="⬇ Download Again", style="Accent.TButton",
                   command=on_again).pack(side="left", padx=(10, 0))
        modal.protocol("WM_DELETE_WINDOW", on_cancel)

        modal.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - modal.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - modal.winfo_height()) // 2
        modal.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        modal.grab_set()

    # ---------------- Open folder / play file (OS default apps) ----------------
    def _open_folder(self, folder):
        if not folder or not os.path.isdir(folder):
            messagebox.showwarning("ফোল্ডার পাওয়া যায়নি", "ডাউনলোড ফোল্ডারটি খুঁজে পাওয়া যায়নি।")
            return
        try:
            if os.name == "nt":
                os.startfile(folder)  # noqa: S606 - intentional, opens in Explorer
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception as ex:
            messagebox.showerror("Error", f"ফোল্ডার খুলতে সমস্যা হয়েছে:\n{ex}")

    def _open_file(self, filepath):
        if not filepath or not os.path.isfile(filepath):
            messagebox.showwarning("ফাইল পাওয়া যায়নি", "ভিডিও ফাইলটি খুঁজে পাওয়া যায়নি।")
            return
        try:
            if os.name == "nt":
                os.startfile(filepath)  # noqa: S606 - intentional, opens default player
            elif sys.platform == "darwin":
                subprocess.Popen(["open", filepath])
            else:
                subprocess.Popen(["xdg-open", filepath])
        except Exception as ex:
            messagebox.showerror("Error", f"ফাইলটি চালাতে সমস্যা হয়েছে:\n{ex}")

    # ---------------- Download-complete popup ----------------
    @staticmethod
    def _play_completion_sound():
        """IDM-style alert beep when a download finishes. Uses the Windows
        system notification sound where available; falls back to Tk's
        cross-platform bell() everywhere else (or if winsound errors out for
        any reason, e.g. no sound device)."""
        if winsound is not None:
            try:
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
                return
            except Exception:
                pass
        try:
            tk._default_root.bell()
        except Exception:
            pass

    def _show_completion_modal(self, is_playlist, output_file, out_dir):
        """Shown once a download finishes successfully.

        Playlist downloads: Close / Open Folder.
        Single video downloads: Close / Open Folder / Play.
        The app auto-refreshes as soon as the popup is closed.

        IDM-style behaviour: this pops up centered on the *physical screen*
        (not relative to the main window) and forces itself on top, so it
        still shows up front and center even if the main app window is
        minimized/in the taskbar -- the main window is deliberately left
        minimized; only this small popup appears.
        """
        self._play_completion_sound()

        modal = tk.Toplevel(self)
        modal.title("Download Complete")
        modal.configure(bg=BG_CARD)
        modal.resizable(False, False)
        # Intentionally NOT modal.transient(self): a transient window's
        # visibility is tied to its owner on most platforms, so if the main
        # window is minimized, a transient child gets hidden along with it.
        # Keeping this as an independent toplevel lets it show up even while
        # the main app stays minimized, exactly like IDM's popup.
        modal.attributes("-topmost", True)

        wrap = ttk.Frame(modal, style="Card.TFrame", padding=20)
        wrap.pack(fill="both", expand=True)

        ttk.Label(wrap, text="✅ Download সম্পন্ন হয়েছে!",
                  style="Body.TLabel", font=("Segoe UI Semibold", 12, "bold")
                  ).pack(anchor="w", pady=(0, 8))

        if is_playlist:
            msg = "প্লেলিস্টের সবগুলো ভিডিও ডাউনলোড হয়ে গেছে।"
        else:
            fname = os.path.basename(output_file) if output_file else ""
            msg = f"ফাইল সফলভাবে ডাউনলোড হয়েছে:\n{fname}" if fname else "ভিডিওটি সফলভাবে ডাউনলোড হয়েছে।"
        ttk.Label(wrap, text=msg, style="Subtle.TLabel",
                  wraplength=380, justify="left").pack(anchor="w", pady=(0, 14))

        btn_row = ttk.Frame(wrap, style="Card.TFrame")
        btn_row.pack(fill="x")

        def do_close():
            modal.destroy()
            self._refresh_app()

        def do_open_folder():
            self._open_folder(out_dir)

        def do_play():
            self._open_file(output_file)

        ttk.Button(btn_row, text="Close", style="Stop.TButton",
                   command=do_close).pack(side="left")
        ttk.Button(btn_row, text="📁 Open Folder", style="Ghost.TButton",
                   command=do_open_folder).pack(side="left", padx=(10, 0))
        if not is_playlist:
            ttk.Button(btn_row, text="▶ Play", style="Accent.TButton",
                       command=do_play).pack(side="left", padx=(10, 0))

        modal.protocol("WM_DELETE_WINDOW", do_close)

        # Center on the physical screen (horizontally AND vertically), not
        # relative to the main window -- the main window's geometry can't be
        # trusted while it's minimized, and IDM-style popups always appear
        # dead-center on screen regardless of where the main app sits.
        modal.update_idletasks()
        sw = modal.winfo_screenwidth()
        sh = modal.winfo_screenheight()
        mw = modal.winfo_width()
        mh = modal.winfo_height()
        x = (sw - mw) // 2
        y = (sh - mh) // 2
        modal.geometry(f"+{max(x, 0)}+{max(y, 0)}")

        # Force it to the front even though the main window may be
        # minimized: deiconify (in case it somehow started iconified),
        # lift above other windows, and grab keyboard focus.
        modal.deiconify()
        modal.lift()
        modal.focus_force()
        modal.grab_set()
        # Drop the always-on-top flag shortly after showing so it doesn't
        # stay glued above every other window forever once the user is
        # actively looking at it -- just needed it for the initial pop-up.
        modal.after(500, lambda: modal.attributes("-topmost", False))

    def _prepare_playlist_download(self, url, out_dir):
        """Runs in a worker thread: figure out which of the *selected*
        playlist videos already exist in out_dir, and if any do, let the
        user choose to skip them, download everything anyway, or cancel --
        the same kind of protection single-video downloads already have."""
        self.status_var.set("Checking for already-downloaded videos...")

        titles = self._loaded_titles if self._loaded_titles else self._extract_playlist_titles(url)
        if not titles:
            self._log("Playlist duplicate-check skipped (couldn't read the playlist's video list).")
            self._run_download(url, out_dir)
            return

        title_by_idx = dict(titles)

        if self.select_all_var.get():
            selected_indices = set(title_by_idx.keys())
        else:
            selected_indices = self._parse_playlist_items(self.playlist_items_var.get())
            if not selected_indices:
                self._run_download(url, out_dir)
                return

        # Rescan the folder fresh -- files may have been added since titles were loaded.
        existing_keys = self._scan_existing_titles(out_dir)
        duplicate_indices = sorted(
            idx for idx in selected_indices
            if idx in title_by_idx and self._normalize_title_key(title_by_idx[idx]) in existing_keys
        )

        if not duplicate_indices:
            self._run_download(url, out_dir)
            return

        decision = self._ask_playlist_duplicate_decision(len(duplicate_indices), len(selected_indices))

        if decision == "cancel":
            self.status_var.set("Cancelled.")
            self._log(f"Playlist download cancelled: {len(duplicate_indices)}টি ভিডিও আগে থেকেই ডাউনলোড করা আছে।")
            self.after(0, self._reset_download_buttons)
            return

        if decision == "skip":
            remaining = sorted(selected_indices - set(duplicate_indices))
            if not remaining:
                self.status_var.set("Cancelled.")
                self._log("সিলেক্ট করা সব ভিডিও আগে থেকেই ডাউনলোড করা আছে — নতুন কিছু ডাউনলোড করার নেই।")
                self.after(0, self._reset_download_buttons)
                return
            self.select_all_var.set(False)
            self.playlist_items_var.set(self._compress_indices(remaining))
            self._log(f"{len(duplicate_indices)}টি আগে থেকে থাকা ভিডিও স্কিপ করা হলো, "
                      f"{len(remaining)}টি ডাউনলোড হবে।")
        else:  # "again" -- download everything, including duplicates
            self._log(f"{len(duplicate_indices)}টি ভিডিও আগে থেকেই আছে, তবুও সবগুলো আবার ডাউনলোড করা হচ্ছে।")

        self._run_download(url, out_dir)

    def _ask_playlist_duplicate_decision(self, duplicate_count, total_selected):
        """Blocks the calling (worker) thread until the user answers the modal
        shown on the main thread. Returns 'cancel', 'skip', or 'again'."""
        result = {"decision": "cancel"}
        event = threading.Event()

        def show_modal():
            self._show_playlist_duplicate_modal(duplicate_count, total_selected, result, event)

        self.after(0, show_modal)
        event.wait()
        return result["decision"]

    def _show_playlist_duplicate_modal(self, duplicate_count, total_selected, result, event):
        modal = tk.Toplevel(self)
        modal.title("Already downloaded")
        modal.configure(bg=BG_CARD)
        modal.transient(self)
        modal.resizable(False, False)

        wrap = ttk.Frame(modal, style="Card.TFrame", padding=20)
        wrap.pack(fill="both", expand=True)

        ttk.Label(wrap, text="⚠ কিছু ভিডিও আগে থেকেই ডাউনলোড করা আছে",
                  style="Body.TLabel", font=("Segoe UI Semibold", 11, "bold")
                  ).pack(anchor="w", pady=(0, 8))
        ttk.Label(
            wrap,
            text=f"সিলেক্ট করা {total_selected}টি ভিডিওর মধ্যে {duplicate_count}টি ফোল্ডারে আগে থেকেই আছে।",
            style="Subtle.TLabel", wraplength=380, justify="left"
        ).pack(anchor="w", pady=(0, 4))
        ttk.Label(wrap, text="আগে থেকে থাকা ভিডিওগুলো স্কিপ করবেন, নাকি সবগুলো আবার ডাউনলোড করবেন?",
                  style="Subtle.TLabel", wraplength=380, justify="left").pack(anchor="w", pady=(0, 14))

        btn_row = ttk.Frame(wrap, style="Card.TFrame")
        btn_row.pack(fill="x")

        def on_cancel():
            result["decision"] = "cancel"
            event.set()
            modal.destroy()

        def on_skip():
            result["decision"] = "skip"
            event.set()
            modal.destroy()

        def on_again():
            result["decision"] = "again"
            event.set()
            modal.destroy()

        ttk.Button(btn_row, text="Cancel", style="Stop.TButton", command=on_cancel).pack(side="left")
        ttk.Button(btn_row, text="⏭ Skip Duplicates", style="Ghost.TButton",
                   command=on_skip).pack(side="left", padx=(10, 0))
        ttk.Button(btn_row, text="⬇ Download All Anyway", style="Accent.TButton",
                   command=on_again).pack(side="left", padx=(10, 0))
        modal.protocol("WM_DELETE_WINDOW", on_cancel)

        modal.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - modal.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - modal.winfo_height()) // 2
        modal.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        modal.grab_set()

    def _prepare_single_download(self, url, out_dir):
        """Runs in a worker thread: fetch the title, check for an existing file
        with the same name, ask the user if a duplicate is found, then download."""
        title = None
        if yt_dlp is not None and sanitize_filename is not None:
            try:
                self.status_var.set("Fetching video info...")
                title = self._fetch_title(url)
            except Exception as ex:
                self._log(f"Could not pre-check title ({ex}); continuing without duplicate check.")

        if title:
            base_name = sanitize_filename(title) + self._quality_suffix()
            existing = self._find_existing_files(out_dir, base_name)
            if existing:
                decision = self._ask_duplicate_decision(existing[0])
                if decision == "cancel":
                    self.status_var.set("Cancelled.")
                    self._log("Download cancelled: video already exists.")
                    self.after(0, self._reset_download_buttons)
                    return
                self._retry_suffix = f" ({len(existing)})"
                self._log(f"Downloading again as: {base_name}{self._retry_suffix}")

        self._run_download(url, out_dir)

    def _stop_download(self):
        if self.pause_flag and not (self.worker_thread and self.worker_thread.is_alive()):
            # Fully cancelling from a paused state: no worker is running right
            # now, so handle cleanup here instead of waiting for a hook.
            out_dir = self._paused_context.get("out_dir") if self._paused_context else None
            self.pause_flag = False
            self._paused_context = None
            self.status_var.set("Stopped.")
            self._log("Paused download বাতিল করা হলো।")
            self._cleanup_partial_files(out_dir)
            self._reset_current_video_status_to_pending()
            self.download_btn.config(state="normal")
            self.pause_btn.config(text="⏸  Pause", command=self._pause_download, state="disabled", style="Ghost.TButton")
            self.stop_btn.config(state="disabled")
            return

        self.stop_flag = True
        self.status_var.set("Stopping...")
        self._log("Stop requested. It will halt after the current step.")

    def _progress_hook(self, d):
        # IMPORTANT: use DownloadCancelled (not DownloadError) here.
        # ydl_opts has "ignoreerrors": True so a single bad video in a
        # playlist doesn't kill the whole queue -- but that also means
        # a plain DownloadError raised here gets swallowed per-entry by
        # yt-dlp, which then just moves on and starts the NEXT playlist
        # video (creating more .part/.webp files) instead of stopping.
        # DownloadCancelled is explicitly exempted from ignoreerrors
        # handling in yt-dlp, so it aborts the whole queue immediately.
        if self.stop_flag:
            raise yt_dlp.utils.DownloadCancelled("Cancelled by user")
        if self.pause_flag:
            raise yt_dlp.utils.DownloadCancelled("Paused by user")

        info = d.get("info_dict", {}) or {}
        pl_index = d.get("playlist_index") or info.get("playlist_index")
        pl_count = (d.get("playlist_count") or info.get("playlist_count")
                    or info.get("n_entries") or info.get("playlist_n_entries"))

        if pl_index:
            status_var = self.video_status_vars.get(pl_index)
            if d["status"] == "downloading":
                self._current_downloading_idx = pl_index
                if status_var:
                    status_var.set("⬇")
            elif d["status"] == "finished" and status_var:
                status_var.set("✅")

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
                self.filesize_var.set(f"{format_filesize(downloaded) or '0 KB'} / {format_filesize(total)}")
                self.status_var.set(f"Downloading {playlist_tag}{filename}")
            else:
                self.filesize_var.set(format_filesize(downloaded) or "0 KB")
                self.status_var.set(f"Downloading {playlist_tag}{filename}...")
        elif d["status"] == "finished":
            self.status_var.set(f"Processing {playlist_tag}(merging/converting)...")
            self.speed_var.set("-- KB/s")
            self.eta_var.set("ETA --:--")
            filename = d.get("filename")
            if filename:
                # Fallback in case no postprocessor hook fires (e.g. no merge/convert needed)
                self._current_output_file = filename
            self._log(f"Finished downloading: {os.path.basename(d.get('filename', ''))}")

    def _postprocessor_hook(self, d):
        """Fires after each postprocessing step (merge, thumbnail embed, mp3
        conversion, etc). We keep overwriting with the latest reported path so
        that by the end we have the real final output file for the Play button."""
        if d.get("status") == "finished":
            info = d.get("info_dict", {}) or {}
            filepath = info.get("filepath") or info.get("_filename")
            if filepath:
                self._current_output_file = filepath
                title = info.get("title") or os.path.basename(filepath)
                url = info.get("webpage_url") or info.get("url") or "N/A"
                self._save_to_history(title, url, filepath)

    def _run_download(self, url, out_dir):
        try:
            if self.audio_only_var.get():
                self._download_audio_only(url, out_dir)
            else:
                self._download_video(url, out_dir)

            if self.stop_flag:
                self._handle_stopped(out_dir)
            elif self.pause_flag:
                self._handle_paused()
            else:
                self.status_var.set("Done!")
                self.progress["value"] = 100
                self.percent_var.set("100%")
                self.speed_var.set("-- KB/s")
                self.eta_var.set("ETA --:--")
                self._log("Download completed successfully.")
                self._finalize_playlist_statuses()

                is_playlist = self.playlist_var.get()
                output_file = self._current_output_file
                self.after(0, lambda: self._show_completion_modal(is_playlist, output_file, out_dir))
        except Exception as e:
            if self.stop_flag:
                self._handle_stopped(out_dir)
            elif self.pause_flag:
                self._handle_paused()
            else:
                self.status_var.set("Error occurred.")
                self._log(f"Error: {e}")
                self._finalize_playlist_statuses()
                messagebox.showerror("Download error", str(e))
        finally:
            if not self.pause_flag:
                self.download_btn.config(state="normal")
                self.pause_btn.config(text="⏸  Pause", command=self._pause_download, state="disabled", style="Ghost.TButton")
                self.stop_btn.config(state="disabled")

    def _handle_stopped(self, out_dir):
        """Common handling for a user-initiated stop: update status/log, then
        clean up leftover .part fragments and orphaned thumbnail files."""
        self.status_var.set("Stopped.")
        self._log("Download stopped by user.")
        self._cleanup_partial_files(out_dir)
        self._reset_current_video_status_to_pending()

    def _handle_paused(self):
        """Common handling for a user-initiated pause: unlike Stop, this keeps
        the partially-downloaded (.part) file on disk untouched so Resume can
        continue it later via yt-dlp's range-request resume (continuedl,
        which is on by default in yt-dlp)."""
        self.status_var.set("Paused.")
        self.speed_var.set("-- KB/s")
        self.eta_var.set("ETA --:--")
        self._log("⏸ Download paused. একই জায়গা থেকে আবার শুরু করতে 'Resume' বাটনে ক্লিক করুন।")
        self._reset_current_video_status_to_pending()

        self.download_btn.config(state="disabled")
        self.pause_btn.config(text="▶  Resume", command=self._resume_download, state="normal", style="Accent.TButton")
        self.stop_btn.config(state="normal")

    def _reset_current_video_status_to_pending(self):
        """When a playlist download is paused/stopped mid-file, put that
        file's status icon back to pending so it doesn't look 'stuck'
        downloading forever."""
        idx = self._current_downloading_idx
        if idx is not None:
            status_var = self.video_status_vars.get(idx)
            if status_var and status_var.get() != "✅":
                status_var.set("⏳")
        self._current_downloading_idx = None

    def _selected_playlist_indices(self):
        """Which playlist indices were actually meant to be downloaded in the
        current run, used to know which status icons to finalize."""
        if not self.playlist_var.get():
            return None
        if self.select_all_var.get():
            if self.video_check_vars:
                return {idx for idx, _var in self.video_check_vars}
            return None
        return self._parse_playlist_items(self.playlist_items_var.get())

    def _finalize_playlist_statuses(self):
        """After a playlist run finishes (successfully or with per-item
        errors swallowed by ignoreerrors=True), any selected video that never
        reached ✅ is marked ❌ failed."""
        selected = self._selected_playlist_indices()
        if not selected:
            return
        for idx in selected:
            status_var = self.video_status_vars.get(idx)
            if status_var and status_var.get() != "✅":
                status_var.set("❌")

    def _pause_download(self):
        """Request a pause. The running yt-dlp download raises inside the
        next progress-hook callback, which we catch in _run_download without
        deleting the partial file, so Resume can continue it later."""
        if not (self.worker_thread and self.worker_thread.is_alive()):
            return
        self.pause_flag = True
        self.pause_btn.config(state="disabled")
        self.status_var.set("Pausing...")
        self._log("Pause requested. বর্তমান ফাইলের ডাউনলোড partial অবস্থায় রেখে থামানো হচ্ছে।")

    def _resume_download(self):
        """Restart the same download (same URL/folder/options). Already
        finished files are detected and skipped by yt-dlp automatically, and
        the in-progress file resumes from where it left off since we never
        deleted its .part fragment."""
        if not self._paused_context:
            return
        ctx = self._paused_context
        self.pause_flag = False
        self.stop_flag = False

        self.download_btn.config(state="disabled")
        self.pause_btn.config(text="⏸  Pause", command=self._pause_download, state="normal", style="Ghost.TButton")
        self.stop_btn.config(state="normal")
        self.status_var.set("Resuming...")
        self._log("▶ Resume করা হচ্ছে — আগের জায়গা থেকে ডাউনলোড আবার শুরু হচ্ছে।")

        self.worker_thread = threading.Thread(
            target=self._run_download, args=(ctx["url"], ctx["out_dir"]), daemon=True
        )
        self.worker_thread.start()

    def _cleanup_partial_files(self, out_dir):
        """After a stopped download, remove half-downloaded fragments
        (*.part, *.ytdl) and orphaned thumbnail images (*.webp/.jpg/.jpeg/.png)
        that belong to videos which never finished, so they don't clutter the
        folder or confuse the title-matching/dedup check next time."""
        if not out_dir or not os.path.isdir(out_dir):
            return
        thumb_exts = {".webp", ".jpg", ".jpeg", ".png"}
        media_exts = {".mp4", ".mkv", ".webm", ".m4a", ".mp3", ".opus", ".avi", ".mov"}
        try:
            entries = os.listdir(out_dir)
        except OSError:
            return

        # A thumbnail is only "orphaned" if there's no genuinely finished
        # media file with the same base name sitting next to it.
        completed_bases = set()
        for fname in entries:
            base, ext = os.path.splitext(fname)
            if ext.lower() in media_exts:
                completed_bases.add(base)

        deleted = []
        for fname in entries:
            full_path = os.path.join(out_dir, fname)
            lower = fname.lower()
            if lower.endswith(".part") or lower.endswith(".ytdl"):
                try:
                    os.remove(full_path)
                    deleted.append(fname)
                except OSError:
                    pass
                continue
            base, ext = os.path.splitext(fname)
            if ext.lower() in thumb_exts and base not in completed_bases:
                try:
                    os.remove(full_path)
                    deleted.append(fname)
                except OSError:
                    pass

        if deleted:
            self._log(f"Cleanup: {len(deleted)}টি অসম্পূর্ণ ফাইল (.part / thumbnail) মুছে ফেলা হয়েছে —")
            for f in deleted:
                self._log(f"   ✕ {f}")

    def _quality_suffix(self):
        """Text appended to the end of the title for the selected quality,
        e.g. ' 1080p'. Empty for 'Best available' and for audio-only mode."""
        if self.audio_only_var.get():
            return ""
        quality = self.quality_var.get()
        if quality and quality != "Best available":
            return f" {quality}"
        return ""

    def _title_suffix(self):
        """Quality suffix + retry marker (e.g. ' 1080p (1)'), used in the filename."""
        return self._quality_suffix() + (self._retry_suffix or "")

    def _outtmpl(self, out_dir):
        suffix = self._title_suffix()
        if self.playlist_var.get() and self.playlist_numbering_var.get():
            # Numbers files in playlist order: "1. Title.ext", "2. Title.ext", ...
            return os.path.join(out_dir, "%(playlist_index)s. %(title)s" + suffix + ".%(ext)s")
        return os.path.join(out_dir, "%(title)s" + suffix + ".%(ext)s")

    def _download_video(self, url, out_dir):
        fmt = self._selected_format_string()
        thumb_opts, thumb_postprocessors = self._thumbnail_opts()

        ydl_opts = {
            "outtmpl": self._outtmpl(out_dir),
            "noplaylist": not self.playlist_var.get(),
            "progress_hooks": [self._progress_hook],
            "postprocessor_hooks": [self._postprocessor_hook],
            "format": fmt,
            "merge_output_format": "mp4",
            "postprocessors": thumb_postprocessors,
            "ignoreerrors": True,
            "continuedl": True,  # resume partially-downloaded (.part) files instead of restarting
            "quiet": True,
            "no_warnings": True,
            **self._cookies_opt(),
            **self._playlist_items_opt(),
            **self._ffmpeg_opt(),
            **self._ratelimit_opt(),
            **thumb_opts,
        }

        self._log(f"Format selector: {fmt}")
        limit_bytes = parse_speed_limit(self.speed_limit_value_var.get(), self.speed_limit_unit_var.get())
        if limit_bytes:
            self._log(f"Speed limit active: max {format_speed(limit_bytes)} (throttled).")
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
            "postprocessor_hooks": [self._postprocessor_hook],
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
            "continuedl": True,  # resume partially-downloaded (.part) files instead of restarting
            "quiet": True,
            "no_warnings": True,
            **self._cookies_opt(),
            **self._playlist_items_opt(),
            **self._ffmpeg_opt(),
            **self._ratelimit_opt(),
            **thumb_opts,
        }
        limit_bytes = parse_speed_limit(self.speed_limit_value_var.get(), self.speed_limit_unit_var.get())
        if limit_bytes:
            self._log(f"Speed limit active: max {format_speed(limit_bytes)} (throttled).")
        if self.embed_thumbnail_var.get():
            self._log("Embedding video thumbnail as MP3 cover art.")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])


if __name__ == "__main__":
    app = YTDLPGui()
    app.mainloop()