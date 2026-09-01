"""Where the chosen image lives between runs.

Everything is kept beside the user's other app data (%APPDATA%\AudioToVideo on Windows)
rather than next to the executable, so the app keeps working from a read-only folder and
survives being replaced by a newer build.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

import media

APP_NAME = "AudioToVideo"

SOURCE_NAME = "source_image.png"
BACKGROUND_NAME = "background.png"
THUMB_NAME = "thumbnail.png"
SETTINGS_NAME = "settings.json"


def directory() -> Path:
    base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    path = root / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_output_directory() -> Path:
    """Where videos land unless the user has picked somewhere else."""
    docs = os.environ.get("USERPROFILE")
    root = Path(docs) / "Documents" if docs else Path.home() / "Documents"
    return root / APP_NAME


def _settings() -> dict:
    path = directory() / SETTINGS_NAME
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_settings(data: dict) -> None:
    path = directory() / SETTINGS_NAME
    path.write_text(json.dumps(data), encoding="utf-8")


def output_directory() -> Path:
    """The folder videos are saved into, created on demand."""
    saved = _settings().get("output_directory")
    path = Path(saved) if saved else default_output_directory()
    path.mkdir(parents=True, exist_ok=True)
    return path


def set_output_directory(path) -> Path:
    """Remember a new output folder, creating it if needed."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    settings = _settings()
    settings["output_directory"] = str(path)
    _save_settings(settings)
    return path


def background() -> Path | None:
    """The 1920x1080 frame every video is built from, or None if no image is set."""
    return _existing(BACKGROUND_NAME)


def thumbnail() -> Path | None:
    """A small copy of that frame, for showing in the window."""
    return _existing(THUMB_NAME)


def set_image(chosen) -> Path:
    """Adopt a new image: normalise it, pre-render the frame and thumbnail, store all three.

    Rendering here rather than per conversion is the whole point -- the blur filter is the
    expensive step and it should run once per image, not once per video.
    """
    staging = Path(tempfile.mkdtemp())
    source = media.normalize_image(chosen, staging / SOURCE_NAME)
    frame = media.build_background(source, staging / BACKGROUND_NAME)
    thumb = media.build_thumbnail(frame, staging / THUMB_NAME)

    # Only touch the real directory once everything above has succeeded, so a bad image
    # leaves the previously working one in place.
    for path in (source, frame, thumb):
        shutil.copyfile(path, directory() / path.name)
    shutil.rmtree(staging, ignore_errors=True)
    return directory() / BACKGROUND_NAME


def _existing(name: str) -> Path | None:
    path = directory() / name
    return path if path.exists() else None
