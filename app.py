"""Audio to Video -- a small desktop app.

Pick an image once, then turn any audio file into an MP4 that shows that image for the
whole track. The heavy work happens on a worker thread and reports back through a queue,
so the window keeps repainting while ffmpeg runs.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import config
import media

AUDIO_TYPES = [
    ("Audio files", "*.mp3 *.m4a *.wav *.flac *.aac *.ogg *.opus *.wma *.aif *.aiff"),
    ("All files", "*.*"),
]
IMAGE_TYPES = [
    ("Images", "*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tif *.tiff"),
    ("All files", "*.*"),
]

NO_IMAGE = "No image chosen yet"
NO_AUDIO = "No audio chosen yet"
MUTED = "#666666"


def output_path(audio: Path) -> Path:
    """Where the finished video for this audio file should be written."""
    return config.output_directory() / audio.with_suffix(".mp4").name


def unique(path: Path) -> Path:
    """A path that does not exist yet, so we never silently overwrite an earlier video."""
    if not path.exists():
        return path
    for n in range(2, 1000):
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not candidate.exists():
            return candidate
    return path.with_name(f"{path.stem} (new){path.suffix}")


def reveal(path: Path) -> None:
    """Show the finished file in the system file manager."""
    try:
        if os.name == "nt":
            subprocess.run(["explorer", "/select,", str(path)])
        elif sys.platform == "darwin":
            subprocess.run(["open", "-R", str(path)])
        else:
            subprocess.run(["xdg-open", str(path.parent)])
    except OSError:
        pass


class App(tk.Tk):
    def __init__(self, audio: str | None = None):
        super().__init__()
        self.title("Audio to Video")
        self.resizable(False, False)

        self.audio: Path | None = None
        self.result: Path | None = None
        self.events: queue.Queue = queue.Queue()
        self.thumb: tk.PhotoImage | None = None  # a live reference, or Tk discards it
        self._tick: str | None = None

        self._build()
        self._show_image()
        if audio:
            self._accept_audio(Path(audio))
        self._tick = self.after(100, self._drain)

    def destroy(self) -> None:
        # Without this the queued _drain fires at a half-destroyed window on close and
        # Tk complains about an invalid command name.
        if self._tick is not None:
            self.after_cancel(self._tick)
            self._tick = None
        super().destroy()

    # -- layout ----------------------------------------------------------------

    def _build(self) -> None:
        frame = ttk.Frame(self, padding=16)
        frame.grid(sticky="nsew")

        ttk.Label(frame, text="Image", font=("", 10, "bold")).grid(sticky="w")
        ttk.Label(
            frame, text="Used for every video until you change it.", foreground=MUTED
        ).grid(sticky="w", pady=(0, 6))

        self.preview = ttk.Label(
            frame, text=NO_IMAGE, relief="solid", borderwidth=1, anchor="center", width=44
        )
        self.preview.grid(sticky="ew", ipady=30)
        ttk.Button(frame, text="Choose image...", command=self._pick_image).grid(
            sticky="ew", pady=(6, 16)
        )

        ttk.Separator(frame).grid(sticky="ew", pady=(0, 16))

        ttk.Label(frame, text="Save videos to", font=("", 10, "bold")).grid(sticky="w")
        self.output_label = ttk.Label(
            frame, text=str(config.output_directory()), foreground=MUTED, wraplength=380
        )
        self.output_label.grid(sticky="w", pady=(0, 6))
        ttk.Button(frame, text="Change folder...", command=self._pick_output_dir).grid(
            sticky="ew", pady=(0, 16)
        )

        ttk.Separator(frame).grid(sticky="ew", pady=(0, 16))

        ttk.Label(frame, text="Audio", font=("", 10, "bold")).grid(sticky="w")
        self.audio_label = ttk.Label(frame, text=NO_AUDIO, foreground=MUTED, wraplength=380)
        self.audio_label.grid(sticky="w", pady=(0, 6))
        ttk.Button(frame, text="Choose audio...", command=self._pick_audio).grid(
            sticky="ew", pady=(0, 16)
        )

        self.go = ttk.Button(frame, text="Create video", command=self._start, state="disabled")
        self.go.grid(sticky="ew")

        self.bar = ttk.Progressbar(frame, mode="determinate", maximum=1000)
        self.bar.grid(sticky="ew", pady=(12, 4))
        self.status = ttk.Label(frame, text="", foreground=MUTED, wraplength=380)
        self.status.grid(sticky="w")

        self.reveal_button = ttk.Button(
            frame, text="Show in folder", command=self._reveal_result
        )
        # Added to the grid only once there is something to show.

        frame.columnconfigure(0, weight=1)

    # -- state -----------------------------------------------------------------

    def _show_image(self) -> None:
        thumb = config.thumbnail()
        if not thumb:
            self.preview.configure(text=NO_IMAGE, image="")
            self.thumb = None
        else:
            self.thumb = tk.PhotoImage(file=str(thumb))
            self.preview.configure(image=self.thumb, text="")
        self._refresh_go()

    def _accept_audio(self, path: Path) -> None:
        self.audio = path
        self.audio_label.configure(text=path.name, foreground="")
        self._refresh_go()

    def _refresh_go(self) -> None:
        has_image = config.background() is not None
        self.go.configure(state="normal" if (self.audio and has_image) else "disabled")
        if not has_image:
            self.status.configure(text="Choose an image to get started.")
        elif not self.audio:
            self.status.configure(text="Choose an audio file.")

    def _busy(self, busy: bool) -> None:
        self.go.configure(state="disabled" if busy else "normal")
        if not busy:
            self._refresh_go()

    def _reveal_result(self) -> None:
        if self.result:
            reveal(self.result)

    # -- actions ---------------------------------------------------------------

    def _pick_image(self) -> None:
        chosen = filedialog.askopenfilename(title="Choose an image", filetypes=IMAGE_TYPES)
        if not chosen:
            return
        self.status.configure(text="Preparing image...")
        self.update_idletasks()
        try:
            config.set_image(chosen)
        except media.MediaError as exc:
            messagebox.showerror(
                "Audio to Video", f"That file could not be read as an image.\n\n{exc}"
            )
            self.status.configure(text="")
            return
        self._show_image()
        self.status.configure(text="Image saved.")

    def _pick_audio(self) -> None:
        chosen = filedialog.askopenfilename(title="Choose an audio file", filetypes=AUDIO_TYPES)
        if chosen:
            self._accept_audio(Path(chosen))

    def _pick_output_dir(self) -> None:
        chosen = filedialog.askdirectory(
            title="Choose a folder for finished videos",
            initialdir=str(config.output_directory()),
        )
        if not chosen:
            return
        path = config.set_output_directory(chosen)
        self.output_label.configure(text=str(path))

    def _start(self) -> None:
        if not self.audio:
            return
        self.reveal_button.grid_remove()
        self.bar.configure(value=0)
        self.status.configure(text="Reading audio...")
        self._busy(True)
        threading.Thread(target=self._work, args=(self.audio,), daemon=True).start()

    def _work(self, audio: Path) -> None:
        try:
            background = config.background()
            duration, has_audio = media.probe(audio)
            if not has_audio:
                self.events.put(("error", "That file has no audio track in it."))
                return
            dest = unique(output_path(audio))
            self.events.put(("stage", f"Encoding {dest.name}"))
            media.convert(
                audio,
                background,
                dest,
                duration,
                on_progress=lambda done: self.events.put(("progress", done)),
            )
            self.events.put(("done", dest))
        except media.MediaError as exc:
            self.events.put(("error", f"Encoding failed.\n\n{exc}"))
        except Exception as exc:  # a worker dying silently would just look like a hang
            self.events.put(("error", f"{type(exc).__name__}: {exc}"))

    def _drain(self) -> None:
        """Apply whatever the worker reported. Tk is only safe to touch from here."""
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "progress":
                    self.bar.configure(value=payload * 1000)
                elif kind == "stage":
                    self.status.configure(text=payload)
                elif kind == "done":
                    self.result = payload
                    self.bar.configure(value=1000)
                    self.status.configure(text=f"Saved {payload.name}")
                    self.reveal_button.grid(sticky="ew", pady=(8, 0))
                    self._busy(False)
                elif kind == "error":
                    self.bar.configure(value=0)
                    self.status.configure(text="")
                    self._busy(False)
                    messagebox.showerror("Audio to Video", payload)
        except queue.Empty:
            pass
        self._tick = self.after(100, self._drain)


def main() -> None:
    # Dropping an audio file onto the executable arrives here.
    dropped = sys.argv[1] if len(sys.argv) > 1 else None
    App(dropped).mainloop()


if __name__ == "__main__":
    main()
