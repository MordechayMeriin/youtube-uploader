"""Where uploaded images live between runs, and which one is picked for the next video.

Everything is kept beside the user's other app data (%APPDATA%\\AudioToVideo on Windows)
rather than next to the executable, so the app keeps working from a read-only folder and
survives being replaced by a newer build.

Each image lives in its own subfolder under images/<id>/ (source + pre-rendered background
+ thumbnail); images.json lists them in the order they were added and records which one is
currently selected for the next conversion.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path

import media

APP_NAME = "AudioToVideo"

MANIFEST_NAME = "images.json"
IMAGES_DIR = "images"

SOURCE_NAME = "source_image.png"
BACKGROUND_NAME = "background.png"
THUMB_NAME = "thumbnail.png"


def directory() -> Path:
    base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    path = root / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _images_dir() -> Path:
    path = directory() / IMAGES_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def _manifest_path() -> Path:
    return directory() / MANIFEST_NAME


def _load_manifest() -> dict:
    path = _manifest_path()
    if not path.exists():
        return {"images": [], "selected": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"images": [], "selected": None}
    data.setdefault("images", [])
    data.setdefault("selected", None)
    return data


def _save_manifest(data: dict) -> None:
    _manifest_path().write_text(json.dumps(data, indent=2), encoding="utf-8")


class ImageEntry:
    """One stored image: its id, display name, and the paths rendered for it."""

    def __init__(self, id: str, name: str):
        self.id = id
        self.name = name

    @property
    def background(self) -> Path:
        """The 1920x1080 frame videos using this image are built from."""
        return _images_dir() / self.id / BACKGROUND_NAME

    @property
    def thumbnail(self) -> Path:
        """A small copy of that frame, for showing in the window."""
        return _images_dir() / self.id / THUMB_NAME


def list_images() -> list[ImageEntry]:
    return [ImageEntry(item["id"], item["name"]) for item in _load_manifest()["images"]]


def get(image_id: str) -> ImageEntry | None:
    for item in _load_manifest()["images"]:
        if item["id"] == image_id:
            return ImageEntry(item["id"], item["name"])
    return None


def selected_id() -> str | None:
    """The id of the image to use for the next video, or None if none is chosen yet."""
    data = _load_manifest()
    ids = {item["id"] for item in data["images"]}
    return data["selected"] if data["selected"] in ids else None


def selected() -> ImageEntry | None:
    sid = selected_id()
    return get(sid) if sid is not None else None


def set_selected(image_id: str) -> None:
    data = _load_manifest()
    if image_id not in {item["id"] for item in data["images"]}:
        raise KeyError(image_id)
    data["selected"] = image_id
    _save_manifest(data)


def add_image(chosen) -> ImageEntry:
    """Add a new image: normalise it, pre-render the frame and thumbnail, store all three.

    Rendering here rather than per conversion is the whole point -- the blur filter is the
    expensive step and it should run once per image, not once per video.
    """
    image_id = uuid.uuid4().hex
    name = Path(chosen).stem

    staging = Path(tempfile.mkdtemp())
    source = media.normalize_image(chosen, staging / SOURCE_NAME)
    frame = media.build_background(source, staging / BACKGROUND_NAME)
    thumb = media.build_thumbnail(frame, staging / THUMB_NAME)

    # Only touch the real directory once everything above has succeeded, so a bad image
    # doesn't leave a half-written entry behind.
    dest_dir = _images_dir() / image_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    for path in (source, frame, thumb):
        shutil.copyfile(path, dest_dir / path.name)
    shutil.rmtree(staging, ignore_errors=True)

    data = _load_manifest()
    data["images"].append({"id": image_id, "name": name})
    if data["selected"] is None:
        data["selected"] = image_id
    _save_manifest(data)
    return ImageEntry(image_id, name)


def remove_image(image_id: str) -> None:
    data = _load_manifest()
    data["images"] = [item for item in data["images"] if item["id"] != image_id]
    if data["selected"] == image_id:
        data["selected"] = data["images"][0]["id"] if data["images"] else None
    _save_manifest(data)
    shutil.rmtree(_images_dir() / image_id, ignore_errors=True)
