# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

"Audio to Video" — a small Windows desktop app (Tkinter GUI). Pick an image once, then
turn any audio file into an MP4 that displays that image for the whole track, ready to
upload to YouTube manually. There is deliberately no YouTube upload feature: videos
uploaded via the YouTube Data API from an unverified API project are locked private by
YouTube with no way to appeal, so an automatic uploader would produce unpublishable
videos.

## Commands

Set up the venv and install deps:

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-build.txt
```

Run from source:

```bash
.venv\Scripts\python.exe app.py
```

Build the standalone app (produces `dist\AudioToVideo\`, ~110 MB mostly ffmpeg):

```bash
.venv\Scripts\python.exe build.py
```

There is no test suite, linter, or CI config in this repo.

## Architecture

Four files, each with one job:

- **`app.py`** — the Tkinter window. All ffmpeg work runs on a background `threading.Thread`;
  progress/results are passed back to the main thread via a `queue.Queue` drained by a
  `self.after(100, ...)` polling loop (`_drain`). Tk objects must only be touched from that
  main-thread loop, never from the worker thread.
- **`media.py`** — every ffmpeg invocation: probing duration/audio-stream presence, building
  the background frame, encoding the final video. Uses `ffmpeg -i` output parsing instead of
  `ffprobe`, because the bundled ffmpeg build ships as a single binary with no ffprobe
  alongside it.
- **`config.py`** — persists the chosen image between runs in `%APPDATA%\AudioToVideo` (not
  next to the exe), so the app survives being replaced by a newer build and still works from
  a read-only install folder.
- **`build.py`** — drives PyInstaller to produce `dist\AudioToVideo\`.

### The key design decision: pre-render the background once

Output video is always 1920x1080. The source image is scaled to fit inside the frame; any
leftover space is filled with a blurred, darkened copy of the same image (see
`_BACKGROUND_FILTER` in [media.py](media.py)) so non-16:9 images don't look broken. Nothing
is cropped.

This blur/composite filter graph is the most expensive ffmpeg operation in the app, so it
runs exactly once, when the user picks an image (`config.set_image` → `media.build_background`)
— never per conversion. Each video conversion (`media.convert`) just loops the pre-rendered
background PNG for the audio's duration and muxes in the audio; that's why conversions are
fast even though picking a new image is not. When touching either of these paths, preserve
this split — don't fold background rendering back into the per-conversion path.

`config.set_image` renders everything into a temp staging directory first and only copies
into the real `%APPDATA%` directory once every step succeeds, so a bad image can't clobber a
previously working setup.

### ffmpeg binary resolution (`media.ffmpeg_exe`)

Checked in order: bundled `ffmpeg.exe` next to the frozen exe (PyInstaller `_MEIPASS`) →
`ffmpeg` on `PATH` → `imageio_ffmpeg`'s downloaded copy (source/dev runs). `build.py` copies
imageio-ffmpeg's binary to a plain `ffmpeg.exe` before bundling (imageio-ffmpeg names it
`ffmpeg-win-x86_64-v7.1.exe`, and PyInstaller's `--add-binary` preserves that name), and
excludes the `imageio_ffmpeg` package from the bundle so a second full copy of ffmpeg doesn't
also ship inside it.

### Tuning knobs (`media.py`)

`VIDEO_FPS` (default 5 — nothing in the frame moves, so this only trades encode time against
file size), `BLUR_SIGMA` / `BACKGROUND_DIM` for how the filled edges look, `THUMB_WIDTH` for
the in-window preview size.
