# Audio to Video

A small Windows desktop app. Pick an image once, then turn any audio file into an
MP4 that shows that image for the whole track — ready to upload to YouTube.

Double-click `AudioToVideo.exe`, or drop an audio file straight onto it.

## Using it

1. **Choose image...** — do this once. The app remembers it.
2. **Choose audio...** — or drop an audio file onto the exe to skip this step.
3. **Create video** — the MP4 lands next to the audio file, with the same name.

An existing `song.mp4` is never overwritten; you get `song (2).mp4` instead.

Your image is stored in `%APPDATA%\AudioToVideo`, not next to the exe, so it
survives replacing the app with a newer build.

## Framing

Output is always 1920x1080. The whole image is scaled to fit inside the frame and
the leftover space is filled with a blurred, darkened copy of the same image, so a
square or portrait picture looks deliberate rather than broken. Nothing is cropped.

That frame is rendered **once**, when you pick the image — not on every conversion.
It is by far the most expensive step, and keeping it out of the per-video path is
what makes conversions fast.

## Building it

Needs Python 3.11+ on Windows. From a clone of this repo:

```bash
python -m venv .venv
```

```bash
.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-build.txt
```

```bash
.venv\Scripts\python.exe build.py
```

The result is `dist\AudioToVideo\` — about 110 MB, most of it ffmpeg. Keep the
folder together; `AudioToVideo.exe` needs the `_internal` folder beside it.

To run from source without building, `.venv\Scripts\python.exe app.py`.

### About the ffmpeg bundling

`build.py` copies imageio-ffmpeg's binary to a plain `ffmpeg.exe` before handing it
to PyInstaller, because that package names it `ffmpeg-win-x86_64-v7.1.exe` and
`--add-binary` preserves the name. It also excludes the `imageio_ffmpeg` package
from the bundle — otherwise a second 88 MB copy of ffmpeg ships inside it.

## Things worth knowing

- **First launch shows a SmartScreen warning.** The exe is unsigned, so Windows
  offers "More info" -> "Run anyway" the first time. Signing it requires a paid
  certificate.
- **No YouTube upload, on purpose.** Videos uploaded through the YouTube Data API
  by an unverified API project are [locked as
  private](https://support.google.com/youtube/answer/7300965) by YouTube, and that
  lock cannot be appealed or switched off in Studio. An automatic uploader would
  have produced videos you could never publish. Upload the MP4 yourself instead.

## Tuning

In `media.py`: `VIDEO_FPS` (default 5 — nothing in the frame moves, so this only
trades encode time against file size), and `BLUR_SIGMA` / `BACKGROUND_DIM` for how
the filled edges look.

## Layout

| File | Role |
| --- | --- |
| `app.py` | The window. Runs conversions on a worker thread. |
| `media.py` | Every ffmpeg call: probing, the background frame, encoding. |
| `config.py` | Remembering the chosen image between runs. |
| `build.py` | Packages it all into `dist\AudioToVideo\`. |
