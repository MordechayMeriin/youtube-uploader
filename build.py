"""Build the standalone app into dist/AudioToVideo/.

Run with the project venv:  .venv\\Scripts\\python.exe build.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg

ROOT = Path(__file__).resolve().parent
STAGING = ROOT / "build" / "bundled"
SEP = ";" if os.name == "nt" else ":"
NAME = "AudioToVideo"


def staged_ffmpeg() -> Path:
    """A copy of ffmpeg named plainly.

    imageio-ffmpeg's binary carries its version in the filename
    (ffmpeg-win-x86_64-v7.1.exe) and --add-binary preserves that name, so the frozen app
    would never find it. Copy it to a predictable name first.
    """
    STAGING.mkdir(parents=True, exist_ok=True)
    dest = STAGING / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    shutil.copyfile(imageio_ffmpeg.get_ffmpeg_exe(), dest)
    dest.chmod(0o755)
    return dest


def main() -> int:
    ffmpeg = staged_ffmpeg()
    print(f"bundling ffmpeg: {ffmpeg} ({ffmpeg.stat().st_size / 1e6:.0f} MB)")

    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onedir",
        "--windowed",  # no console window behind the GUI
        "--name", NAME,
        "--add-binary", f"{ffmpeg}{SEP}.",
        # The bundled binary above is the one we use, so shipping the pip package as well
        # would put a second copy of ffmpeg in the build for nothing.
        "--exclude-module", "imageio_ffmpeg",
        "--exclude-module", "numpy",
        str(ROOT / "app.py"),
    ]
    result = subprocess.run(args, cwd=ROOT)
    if result.returncode != 0:
        return result.returncode

    out = ROOT / "dist" / NAME
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"\nBuilt {out}  ({size / 1e6:.0f} MB)")
    print(f"Run: {out / (NAME + '.exe')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
