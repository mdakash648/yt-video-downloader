# FFmpeg Setup

This project requires **FFmpeg** to work properly.

The `ffmpeg.exe` and `ffprobe.exe` files are **not included** in this repository because GitHub has file size limitations.

## Download FFmpeg

1. Visit the official FFmpeg download page:
   - https://www.ffmpeg.org/download.html
   - or https://drive.google.com/file/d/1Ey0e-330TRA0gmNq8VnrO5gcvJOPZlNM/view?usp=sharing

2. Under **Get packages & executable files**, click:
   - **Windows builds from gyan.dev**

3. Download the latest **ffmpeg-release-full.7z** package.

4. Extract the downloaded archive.

5. Open the extracted folder and navigate to:

   ```
   ffmpeg-xxxx-full_build\bin\
   ```

6. Copy the following files:
   - `ffmpeg.exe`
   - `ffprobe.exe`

7. Paste both files into **this folder** (the same folder where this `README.md` is located).

Your folder structure should look like this:

```
ffmpeg/
├── README.md
├── ffmpeg.exe
└── ffprobe.exe
```

## Done!

After placing both files in this folder, the project will work correctly and you can build/run the application without any additional configuration.
