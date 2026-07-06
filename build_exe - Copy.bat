@echo off
REM ============================================================
REM  Build script: yt_dlp_gui.py  ->  YTDLPDownloader.exe
REM  Run this on Windows, inside the project folder.
REM  Folder must look like this before running:
REM
REM    project\
REM      yt_dlp_gui.py
REM      requirements.txt
REM      build_exe.bat   (this file)
REM      icon.ico        (optional - your own app icon)
REM      ffmpeg\
REM        ffmpeg.exe
REM        ffprobe.exe
REM
REM  Get ffmpeg.exe + ffprobe.exe from:
REM    https://www.gyan.dev/ffmpeg/builds/  (choose the "essentials" build,
REM    unzip it, copy the two .exe files from its "bin" folder into ffmpeg\)
REM ============================================================

setlocal

echo.
echo === Step 1: Create/activate a virtual environment ===
if not exist venv (
    python -m venv venv
)
call venv\Scripts\activate.bat

echo.
echo === Step 2: Install dependencies ===
pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller

if not exist ffmpeg\ffmpeg.exe (
    echo.
    echo [ERROR] ffmpeg\ffmpeg.exe not found.
    echo Download it from https://www.gyan.dev/ffmpeg/builds/ ^(essentials build^),
    echo then copy ffmpeg.exe and ffprobe.exe into the "ffmpeg" folder next to this script.
    goto :end
)

set ICON_FLAG=
if exist icon.ico (
    echo Found icon.ico - it will be used as the app icon.
    set ICON_FLAG=--icon "icon.ico"
) else (
    echo No icon.ico found - building with the default PyInstaller icon.
    echo ^(Add an icon.ico file next to this script to use your own icon.^)
)

echo.
echo === Step 3: Build the .exe with PyInstaller ===
pyinstaller --noconfirm --onefile --windowed ^
    --name "YTDLPDownloader" ^
    %ICON_FLAG% ^
    --add-binary "ffmpeg\ffmpeg.exe;ffmpeg" ^
    --add-binary "ffmpeg\ffprobe.exe;ffmpeg" ^
    yt_dlp_gui.py

echo.
echo === Done ===
echo Your standalone exe is here:  dist\YTDLPDownloader.exe
echo Give the user ONLY that one file - ffmpeg and Python are bundled inside it.

:end
endlocal
pause