# 🎬 YT-DLP Downloader

A powerful, modern desktop GUI (Python + Tkinter, powered by **yt-dlp**) to download YouTube — and any yt-dlp supported site's — videos, audio, and playlists with ease.

---

## 🚀 Features

This application allows you to:

- 🎥 Download any video in high quality (single video or **batch mode** — multiple URLs at once, one per line)
- 🎚️ Choose video quality from a **dynamic dropdown** that auto-detects the real resolutions actually available for the pasted video/playlist (no more guessing — no fake options that don't exist for that video)
- 📋 **Clipboard auto-detect**: paste a YouTube link anywhere and it's picked up automatically, or use the manual **Paste** button
- 🎧 Extract and download audio directly (MP3 format, Audio-only mode)
- 📂 Download full playlists easily
- ✅ Select specific videos from a playlist (custom item numbers/ranges, Select All / Deselect All)
- 🔁 **Auto-dedup for playlists**: already-downloaded videos are detected by comparing titles against files in your download folder and can be skipped automatically
- 🖼️ Embed the video's thumbnail as poster/cover art in the downloaded file
- 🍪 Use browser cookies for age-restricted or private videos
- 🐢 Optional **speed limit / throttle** for downloads
- 📍 Set a custom download location
- 🔢 Optional numbered filenames for playlist downloads (1. 2. 3. ...)
- 📊 Real-time progress tracking: speed, ETA, downloaded size, and estimated total size
- ⏸️ **Pause / Resume** downloads mid-way, or **Stop** at any time
- 🧹 Automatic cleanup of leftover partial files when a download is stopped
- 🔁 Duplicate-file handling: choose to re-download, skip, or save with an auto-incremented counter
- 🔔 **IDM-style "Download Complete" popup**: pops up centered on your screen with a beep alert — even while the app is minimized — with **Open Folder** and **Play** shortcuts
- 🔄 One-click **Refresh** (Ctrl+R) to reset the app for a new download
- 🆙 Built-in yt-dlp version check with one-click update
- 📝 Detailed logs for every download task

---

## 💡 Key Highlights

- Simple, modern dark-themed interface
- Supports video, audio-only, and full-playlist downloads
- Smart, real-data-driven quality selection — not a static guess list
- Playlist management with selective download + auto-dedup
- Real-time download status tracking with pause/resume
- Desktop notification-style completion popup, IDM-style
- Lightweight, portable, and easy to use — ffmpeg bundled/auto-detected

---

## 📌 How It Works

Using the application is simple and straightforward:

1. 🎬 Paste the **video or playlist URL** in the input field at the top (or just copy a link — it's auto-detected), or switch to **Batch Mode** to queue multiple URLs at once.

2. 📁 Choose your **download location** where files will be saved.

3. ⚙️ Select your preferred options:
   - Download entire playlist or specific videos (by number/range)
   - Enable **Audio only (MP3)** if you want only sound
   - Embed video thumbnail as poster/cover art (optional)
   - Choose **video quality** from the dropdown — it fills in automatically with that video/playlist's real available resolutions
   - Add browser cookies if required (for restricted videos)
   - Set a speed limit if you want to throttle bandwidth usage
   - Turn on numbered filenames for playlists if you prefer ordered files

4. 📋 If it's a playlist:
   - Click **Load / Refresh Titles**
   - Select specific videos, or use **Select All / Deselect All**
   - Already-downloaded videos in your target folder are detected automatically so you can skip re-downloading them

5. ⬇️ Click the **Download** button to start downloading

6. 📊 Monitor real-time progress:
   - Download speed ⚡
   - ETA ⏳
   - Downloaded size / estimated total size 📦
   - Current status

7. ⏸️ Use **Pause**/**Resume** if you need to free up bandwidth temporarily, or **Stop** to cancel — partial files are cleaned up automatically.

8. 🔔 When the download finishes, a **Download Complete** popup appears centered on your screen (even if the app window is minimized), with a beep alert and quick **Open Folder** / **Play** buttons.

9. 📝 View logs at the bottom to track everything in detail.

---

## 📌 Notes

- Make sure `ffmpeg.exe` and `ffprobe.exe` are placed in the same folder as the application (or bundled alongside it) — required for audio extraction, thumbnail embedding, and merging video/audio streams.
- Internet connection is required for downloading content.
- Some videos may have restrictions based on the source site's policies — use the cookies option for those.
- Keep yt-dlp updated using the built-in update checker for best compatibility with site changes.

---

## 🛠️ Built With

- Python 3 + Tkinter (GUI)
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) (download engine)
- FFmpeg (audio extraction, thumbnail embedding, stream merging)

---

## 📄 License

This project is for educational and personal use only.

---

⭐ If you like this project, don't forget to star the repository!
