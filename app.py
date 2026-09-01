"""Audio to Video -- a small desktop app.

Add one or more images, pick which one is active, then turn any audio file into an MP4
that shows the active image for the whole track. The heavy work happens on a worker
thread and reports back through a queue, so the window keeps repainting while ffmpeg runs.
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
        self.images: list[config.ImageEntry] = []
        self.events: queue.Queue = queue.Queue()
        self.thumb: tk.PhotoImage | None = None  # a live reference, or Tk discards it
        self._tick: str | None = None

        self._build()
        self._refresh_images()
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

        ttk.Label(frame, text="Images", font=("", 10, "bold")).grid(sticky="w")
        ttk.Label(
            frame, text="Add one or more; pick which one to use below.", foreground=MUTED
        ).grid(sticky="w", pady=(0, 6))

        self.preview = ttk.Label(
            frame, text=NO_IMAGE, relief="solid", borderwidth=1, anchor="center", width=44
        )
        self.preview.grid(sticky="ew", ipady=30)

        list_frame = ttk.Frame(frame)
        list_frame.grid(sticky="ew", pady=(6, 0))
        list_frame.columnconfigure(0, weight=1)
        self.image_list = tk.Listbox(list_frame, height=4, exportselection=False)
        self.image_list.grid(row=0, column=0, sticky="ew")
        self.image_list.bind("<<ListboxSelect>>", self._on_image_select)
        list_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.image_list.yview)
        list_scroll.grid(row=0, column=1, sticky="ns")
        self.image_list.configure(yscrollcommand=list_scroll.set)

        image_buttons = ttk.Frame(frame)
        image_buttons.grid(sticky="ew", pady=(6, 16))
        image_buttons.columnconfigure(0, weight=1)
        image_buttons.columnconfigure(1, weight=1)
        ttk.Button(image_buttons, text="Add image...", command=self._pick_image).grid(
            row=0, column=0, sticky="ew", padx=(0, 4)
        )
        ttk.Button(image_buttons, text="Remove", command=self._remove_image).grid(
            row=0, column=1, sticky="ew", padx=(4, 0)
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

    def _refresh_images(self) -> None:
        self.images = config.list_images()
        self.image_list.delete(0, tk.END)
        for entry in self.images:
            self.image_list.insert(tk.END, entry.name)

        selected_id = config.selected_id()
        self.image_list.selection_clear(0, tk.END)
        for i, entry in enumerate(self.images):
            if entry.id == selected_id:
                self.image_list.selection_set(i)
                self.image_list.activate(i)
                break

        self._show_selected_preview()
        self._refresh_go()

    def _show_selected_preview(self) -> None:
        entry = config.selected()
        if entry is None:
            self.preview.configure(text=NO_IMAGE, image="")
            self.thumb = None
        else:
            self.thumb = tk.PhotoImage(file=str(entry.thumbnail))
            self.preview.configure(image=self.thumb, text="")

    def _accept_audio(self, path: Path) -> None:
        self.audio = path
        self.audio_label.configure(text=path.name, foreground="")
        self._refresh_go()

    def _refresh_go(self) -> None:
        has_image = config.selected_id() is not None
        self.go.configure(state="normal" if (self.audio and has_image) else "disabled")
        if not has_image:
            self.status.configure(text="Add an image to get started.")
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
            entry = config.add_image(chosen)
        except media.MediaError as exc:
            messagebox.showerror(
                "Audio to Video", f"That file could not be read as an image.\n\n{exc}"
            )
            self.status.configure(text="")
            return
        config.set_selected(entry.id)
        self._refresh_images()
        self.status.configure(text="Image added.")

    def _remove_image(self) -> None:
        selection = self.image_list.curselection()
        if not selection:
            return
        entry = self.images[selection[0]]
        if not messagebox.askyesno("Audio to Video", f'Remove "{entry.name}"?'):
            return
        config.remove_image(entry.id)
        self._refresh_images()

    def _on_image_select(self, event) -> None:
        selection = self.image_list.curselection()
        if not selection:
            return
        entry = self.images[selection[0]]
        config.set_selected(entry.id)
        self._show_selected_preview()
        self._refresh_go()

    def _pick_audio(self) -> None:
        chosen = filedialog.askopenfilename(title="Choose an audio file", filetypes=AUDIO_TYPES)
        if chosen:
            self._accept_audio(Path(chosen))

    def _start(self) -> None:
        if not self.audio:
            return
        entry = config.selected()
        if entry is None:
            return
        self.reveal_button.grid_remove()
        self.bar.configure(value=0)
        self.status.configure(text="Reading audio...")
        self._busy(True)
        threading.Thread(
            target=self._work, args=(self.audio, entry.background), daemon=True
        ).start()

    def _work(self, audio: Path, background: Path) -> None:
        try:
            duration, has_audio = media.probe(audio)
            if not has_audio:
                self.events.put(("error", "That file has no audio track in it."))
                return
            dest = unique(audio.with_suffix(".mp4"))
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
