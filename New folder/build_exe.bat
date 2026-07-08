@echo off
REM ============================================================
REM Build script: yt_dlp_gui.py -> YTDLPDownloader\ (onedir build)
REM Run this on Windows, inside the project folder.
REM Folder must look like this before running:
REM
REM   project\
REM     yt_dlp_gui.py
REM     requirements.txt
REM     build_exe.bat        (this file)
REM     version.txt          (recommended - embeds Company/Product/Version info)
REM     icon.ico             (optional - your own app icon)
REM     ffmpeg\
REM       ffmpeg.exe
REM       ffprobe.exe
REM     aria2c\
REM       aria2c.exe
REM
REM Get ffmpeg.exe + ffprobe.exe from:
REM   https://www.gyan.dev/ffmpeg/builds/ (choose the "essentials" build,
REM   unzip it, copy the two .exe files from its "bin" folder into ffmpeg\)
REM
REM Get aria2c.exe from:
REM   https://github.com/aria2/aria2/releases (grab the win-64bit-build zip,
REM   copy aria2c.exe from it into the "aria2c" folder next to this script).
REM   This is OPTIONAL -- only needed for the "Fast Download" multi-connection
REM   feature. If it's missing, the app still works fine using yt-dlp's own
REM   native concurrent-fragment downloader instead.
REM
REM NOTE: This is an --onedir build, NOT --onefile. That means the output
REM is a FOLDER (dist\YTDLPDownloader\), not a single .exe. You must give
REM users the WHOLE folder (or the zip this script creates at the end) --
REM the exe alone will not run without the files next to it. This trades
REM "single file" convenience for two real benefits:
REM   1) it doesn't self-extract to a temp folder on every launch (that
REM      self-extracting behavior is exactly what makes --onefile exes
REM      look more suspicious to some antivirus heuristics), and
REM   2) it starts up faster, since there's nothing to unpack each run.
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
    echo Download it from https://www.gyan.dev/ffmpeg/builds/ (essentials build),
    echo then copy ffmpeg.exe and ffprobe.exe into the "ffmpeg" folder next to this script.
    goto :end
)

set ARIA2C_FLAG=
if exist aria2c\aria2c.exe (
    echo Found aria2c\aria2c.exe - it will be bundled for Fast Download.
    set ARIA2C_FLAG=--add-binary "aria2c\aria2c.exe;aria2c"
) else (
    echo No aria2c\aria2c.exe found - building without it.
    echo   ^(Optional. Fast Download will fall back to yt-dlp's native
    echo    concurrent-fragment downloader instead. To include aria2c,
    echo    grab aria2c.exe from https://github.com/aria2/aria2/releases
    echo    and put it in an "aria2c" folder next to this script.^)
)

set ICON_FLAG=
if exist icon.ico (
    echo Found icon.ico - it will be used as the app icon.
    set ICON_FLAG=--icon "icon.ico"
) else (
    echo No icon.ico found - building with the default PyInstaller icon.
    echo   ^(Add an icon.ico file next to this script to use your own icon.^)
)

set VERSION_FLAG=
if exist version.txt (
    echo Found version.txt - Company/Product/Version info will be embedded in the exe.
    set VERSION_FLAG=--version-file "version.txt"
) else (
    echo No version.txt found - building without embedded version info.
    echo   ^(A version.txt with real Company/Product info makes the exe look less
    echo    like an anonymous binary to Windows SmartScreen / some antivirus tools.^)
)

echo.
echo === Step 3: Build with PyInstaller (--onedir) ===
pyinstaller --noconfirm --onedir --windowed ^
    --name "YTDLPDownloader" ^
    %ICON_FLAG% ^
    %VERSION_FLAG% ^
    --add-binary "ffmpeg\ffmpeg.exe;ffmpeg" ^
    --add-binary "ffmpeg\ffprobe.exe;ffmpeg" ^
    %ARIA2C_FLAG% ^
    yt_dlp_gui.py

if not exist "dist\YTDLPDownloader\YTDLPDownloader.exe" (
    echo.
    echo [ERROR] Build failed - dist\YTDLPDownloader\YTDLPDownloader.exe not found.
    goto :end
)

echo.
echo === Step 4: Zip the output folder for easy sharing ===
set ZIP_NAME=YTDLPDownloader.zip
if exist "%ZIP_NAME%" del "%ZIP_NAME%"
powershell -NoProfile -Command "Compress-Archive -Path 'dist\YTDLPDownloader\*' -DestinationPath '%ZIP_NAME%' -Force"

if exist "%ZIP_NAME%" (
    echo.
    echo === Done ===
    echo Your app folder is here:      dist\YTDLPDownloader\
    echo Your shareable zip is here:   %ZIP_NAME%
    echo.
    echo IMPORTANT: give users the WHOLE zip / WHOLE folder, not just the
    echo .exe file by itself. ffmpeg.exe, ffprobe.exe, aria2c.exe ^(if bundled^),
    echo and the bundled Python runtime files all live next to the exe and
    echo are required for it to start. The user should unzip and double-click
    echo the YTDLPDownloader.exe INSIDE the extracted YTDLPDownloader folder.
) else (
    echo.
    echo [WARNING] Zip step failed - PowerShell Compress-Archive may not be
    echo available. You can still manually zip the dist\YTDLPDownloader\
    echo folder and share that instead.
)

echo.
echo === Step 5: Build a proper Windows installer (optional) ===
echo Looking for Inno Setup (ISCC.exe)...
set ISCC=
for %%V in (7 6) do (
    if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup %%V\ISCC.exe" set ISCC="%ProgramFiles(x86)%\Inno Setup %%V\ISCC.exe"
    if not defined ISCC if exist "%ProgramFiles%\Inno Setup %%V\ISCC.exe" set ISCC="%ProgramFiles%\Inno Setup %%V\ISCC.exe"
)
REM Fallback: maybe ISCC.exe is on PATH
if not defined ISCC (
    where ISCC.exe >nul 2>nul && set ISCC=ISCC.exe
)

if defined ISCC (
    echo Found: %ISCC%
    if exist installer.iss (
        echo Found Inno Setup - building Setup.exe...
        %ISCC% installer.iss
        if exist "Output\YTDLPDownloader-Setup.exe" (
            echo.
            echo === Installer built ===
            echo Give users THIS single file instead of the zip:
            echo   Output\YTDLPDownloader-Setup.exe
            echo It installs to Program Files, adds Start Menu / Desktop
            echo shortcuts, and shows up in Windows Search - just like VLC.
        ) else (
            echo [WARNING] Inno Setup ran but Output\YTDLPDownloader-Setup.exe
            echo was not found - check the ISCC output above for errors.
        )
    ) else (
        echo [SKIP] installer.iss not found next to this script - skipping
        echo installer build. Add installer.iss to this folder to enable it.
    )
) else (
    echo [SKIP] Inno Setup not found - skipping installer build.
    echo Checked:
    echo   "%ProgramFiles(x86)%\Inno Setup 7\ISCC.exe"
    echo   "%ProgramFiles%\Inno Setup 7\ISCC.exe"
    echo   "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
    echo   "%ProgramFiles%\Inno Setup 6\ISCC.exe"
    echo   ISCC.exe on PATH
    echo If Inno Setup is installed somewhere else, either add its folder to
    echo PATH, or open installer.iss directly in the Inno Setup IDE and
    echo press Compile ^(F9^) there instead.
    echo Install it free from https://jrsoftware.org/isdl.php if needed.
)

:end
endlocal
pause