# Building YTDLPDownloader.exe (no Python / no ffmpeg needed for end users)

The end result: a single `YTDLPDownloader.exe` file. Users just double-click it —
no Python install, no `pip install`, no ffmpeg download.

**Important:** a Windows `.exe` has to be built _on Windows_ (PyInstaller packages
for whatever OS it runs on). If you're on Windows already, just follow the steps
below. If you only have Mac/Linux, see the "No Windows machine?" section at the
bottom — you can still get an exe without buying a Windows PC.

## What you need

- A Windows 10/11 machine
- Python 3.9+ installed from https://www.python.org/downloads/ (tick "Add to PATH" during install)
- These files in one folder:
  - `yt_dlp_gui.py`
  - `requirements.txt`
  - `build_exe.bat`
  - `icon.ico` (optional — your own app icon, see below)
  - a `ffmpeg` subfolder containing `ffmpeg.exe` and `ffprobe.exe`

## Step 0 — (Optional) Add your own icon

PyInstaller needs the icon in **`.ico`** format specifically (not `.png`/`.jpg`).

1. If you have a `.png` logo, convert it to `.ico` — easiest way is a free
   online converter like https://icoconvert.com or https://convertio.co/png-ico/
   (use a square image, ideally 256x256 for best quality)
2. Name the file `icon.ico`
3. Put it directly inside the project folder, next to `yt_dlp_gui.py`

`build_exe.bat` automatically detects it and applies it to the exe. If you
skip this step, the exe just gets PyInstaller's default icon — everything
else still works fine.

## Step 1 — Get ffmpeg binaries

1. Go to https://www.gyan.dev/ffmpeg/builds/
2. Download the **"release essentials"** build (a `.zip` or `.7z` file)
3. Extract it, open the `bin` folder inside
4. Copy `ffmpeg.exe` and `ffprobe.exe` into a new folder named `ffmpeg`
   next to `yt_dlp_gui.py`

Your folder should now look like:

```
project\
  yt_dlp_gui.py
  requirements.txt
  build_exe.bat
  ffmpeg\
    ffmpeg.exe
    ffprobe.exe
```

## Step 2 — Run the build script

Double-click `build_exe.bat` (or run it from a Command Prompt in that folder).
It will automatically:

1. Create a virtual environment
2. Install `yt-dlp` and `pyinstaller`
3. Package everything — script + yt-dlp + ffmpeg — into one `.exe`

This takes a few minutes. When it finishes you'll have:

```
project\dist\YTDLPDownloader.exe
```

That single file is everything your users need. Send them just that file
(or zip it up). No Python, no pip, no ffmpeg download required on their end.

## Do end users need the whole project folder, or just the .exe?

**Just the one `.exe` file.** That's the entire point of `--onefile`:

- Python interpreter → bundled inside the exe
- yt-dlp library → bundled inside the exe
- ffmpeg.exe + ffprobe.exe → bundled inside the exe
- Your app code → bundled inside the exe

`dist\YTDLPDownloader.exe` is fully self-contained. You (or your users) can
copy _only that file_ anywhere — another folder, a USB stick, another PC —
and double-click to run it. No `venv`, no `ffmpeg` folder, no `.py` file,
nothing else needs to travel with it.

(Behind the scenes, when it runs, Windows silently unpacks everything to a
temp folder and cleans it up after — that's why the exe takes a second or
two longer to open than a normal small program. That's expected.)

## Notes

- **First run may trigger a Windows SmartScreen / antivirus warning.** This is
  normal for unsigned PyInstaller executables, not a sign anything is wrong.
  Users can click "More info" → "Run anyway". To avoid this entirely you'd
  need a paid code-signing certificate — not required, just optional polish.
- **File size** will be roughly 60–100 MB, mostly ffmpeg + the Python runtime
  bundled inside. That's expected for a fully self-contained exe.
- **Rebuilding after code changes:** just re-run `build_exe.bat`. It reuses
  the same `venv` folder so it's faster the second time.
- Antivirus false positives are a known PyInstaller quirk (it flags how
  onefile exes self-extract at startup) — not specific to this app.

## Optional: turn it into a proper installer

If you want a real "Setup.exe" with a Start Menu shortcut, uninstaller, etc.,
use **Inno Setup** (free): https://jrsoftware.org/isinfo.php
Point it at `dist\YTDLPDownloader.exe` as the single file to install — takes
about 10 minutes to set up a basic installer script.

## No Windows machine?

You can still produce a Windows .exe using a free GitHub Actions runner:

1. Push `yt_dlp_gui.py`, `requirements.txt`, and the `ffmpeg` folder to a
   GitHub repo
2. Add a workflow file (`.github/workflows/build.yml`) that runs on
   `windows-latest`, does `pip install -r requirements.txt pyinstaller`,
   then runs the same `pyinstaller` command from `build_exe.bat`
3. Download the built `.exe` from the workflow's "Artifacts" section

Say the word if you want, and a ready-to-use GitHub Actions workflow file can
be put together for this.
