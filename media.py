"""ffmpeg wrappers: probe audio, render the static background frame, encode the video.

Only the `ffmpeg` binary is required. Duration and stream detection are parsed out of
`ffmpeg -i` rather than shelling out to `ffprobe`, because the ffmpeg we ship is a lone
binary with no ffprobe beside it.
"""

from __future__ import annotations

import functools
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

WIDTH, HEIGHT = 1920, 1080

# Nothing in the frame moves, so this only trades encode time against file size --
# it has no effect on how the video looks. Raise it to 24 or 30 if you ever need to.
VIDEO_FPS = 5

AUDIO_BITRATE = "192k"
BLUR_SIGMA = 40
BACKGROUND_DIM = 0.25
THUMB_WIDTH = 320

# Without this every ffmpeg call flashes a console window, because the GUI is built
# with --noconsole and therefore has no console of its own to inherit.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d\d):(\d\d(?:\.\d+)?)")
_AUDIO_STREAM_RE = re.compile(r"^\s*Stream #\d+:\d+.*?: Audio: ", re.M)
_OUT_TIME_RE = re.compile(r"^out_time=(\d+):(\d\d):(\d\d(?:\.\d+)?)")

# `split` is required -- a filter output cannot be consumed by two filters.
#
# The foreground is scaled down to fit the frame if it's larger, but never scaled up if
# it's smaller -- clamping the target box to the source's own size (via if(gt(...))) turns
# `decrease` from "fit to exactly WIDTHxHEIGHT" into "fit, but don't upscale".
_BACKGROUND_FILTER = (
    "[0:v]split=2[wide][fit];"
    f"[wide]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
    f"crop={WIDTH}:{HEIGHT},gblur=sigma={BLUR_SIGMA},eq=brightness=-{BACKGROUND_DIM}[bg];"
    f"[fit]scale=w='if(gt(iw,{WIDTH}),{WIDTH},iw)':h='if(gt(ih,{HEIGHT}),{HEIGHT},ih)':"
    "force_original_aspect_ratio=decrease[fg];"
    "[bg][fg]overlay=(W-w)/2:(H-h)/2"
)


class MediaError(RuntimeError):
    """ffmpeg failed, or an input file is not something we can use."""


@functools.lru_cache(maxsize=1)
def ffmpeg_exe() -> str:
    """The ffmpeg we bundled, else one on PATH, else the imageio-ffmpeg download."""
    if getattr(sys, "frozen", False):
        root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        bundled = root / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        if bundled.exists():
            return str(bundled)

    found = shutil.which("ffmpeg")
    if found:
        return found

    try:
        import imageio_ffmpeg
    except ImportError as exc:
        raise MediaError("No ffmpeg available and imageio-ffmpeg is not installed.") from exc
    return imageio_ffmpeg.get_ffmpeg_exe()


def _tail(text: str, lines: int = 15) -> str:
    kept = [line for line in text.strip().splitlines() if line.strip()][-lines:]
    return "\n".join(kept) or "ffmpeg produced no output."


def _seconds(match: re.Match) -> float:
    return int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))


def _run(args: list[str]) -> None:
    proc = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-y", *args],
        capture_output=True,
        text=True,
        errors="replace",
        creationflags=_NO_WINDOW,
    )
    if proc.returncode != 0:
        raise MediaError(_tail(proc.stderr))


def probe(path) -> tuple[float, bool]:
    """Return (duration in seconds, whether the file has an audio stream).

    Duration is 0.0 when ffmpeg cannot determine it, which only costs us the progress bar.
    """
    # `ffmpeg -i` with no output file always exits non-zero; what we want is on stderr.
    proc = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        errors="replace",
        creationflags=_NO_WINDOW,
    )
    match = _DURATION_RE.search(proc.stderr)
    return (_seconds(match) if match else 0.0), bool(_AUDIO_STREAM_RE.search(proc.stderr))


def normalize_image(source, dest) -> Path:
    """Re-save an uploaded image as PNG so it has a predictable name and format."""
    dest = Path(dest)
    _run(["-i", str(source), "-frames:v", "1", str(dest)])
    return dest


def build_background(source, dest) -> Path:
    """Render `source` into a 1920x1080 frame, gaps filled with a blurred copy of itself.

    Run once when the image changes, never per conversion -- this filter graph is by far
    the most expensive part of the job.
    """
    dest = Path(dest)
    _run(["-i", str(source), "-filter_complex", _BACKGROUND_FILTER, "-frames:v", "1", str(dest)])
    return dest


def build_thumbnail(source, dest, width: int = THUMB_WIDTH) -> Path:
    """Shrink a frame for display in the window. Tk can only show PNG/GIF, so PNG it is."""
    dest = Path(dest)
    _run(["-i", str(source), "-vf", f"scale={width}:-2", "-frames:v", "1", str(dest)])
    return dest


def convert(
    audio,
    background,
    dest,
    duration: float = 0.0,
    on_progress: Callable[[float], None] | None = None,
) -> Path:
    """Loop `background` for the length of `audio` and mux the two into an MP4 at `dest`."""
    dest = Path(dest)
    args = [
        ffmpeg_exe(), "-hide_banner", "-y",
        "-loop", "1", "-framerate", str(VIDEO_FPS), "-i", str(background),
        "-i", str(audio),
        "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage",
        "-crf", "23", "-pix_fmt", "yuv420p", "-r", str(VIDEO_FPS),
        "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ar", "44100", "-ac", "2",
        "-shortest", "-movflags", "+faststart", "-max_muxing_queue_size", "1024",
        "-progress", "pipe:1", "-nostats",
        str(dest),
    ]

    # stderr goes to a file rather than a pipe: a chatty ffmpeg filling a pipe we are not
    # draining would deadlock us while we sit reading progress off stdout.
    with tempfile.TemporaryFile() as errors:
        proc = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=errors, text=True, bufsize=1,
            creationflags=_NO_WINDOW,
        )
        for line in proc.stdout:
            if on_progress and duration > 0:
                match = _OUT_TIME_RE.match(line)
                if match:
                    on_progress(min(_seconds(match) / duration, 1.0))
        if proc.wait() != 0:
            errors.seek(0)
            raise MediaError(_tail(errors.read().decode("utf-8", "replace")))
    return dest
