#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Music Forge — Professional Audio Compiler & Processor (Pro UI, Store-ready)
Version: 1.1.5  (dark theme, no View menu, robust Windows title-bar icon)

- Robust top-bar icon on Windows (uses iconbitmap + iconphoto, PNG→ICO auto-convert with 16x16).
- Forces a clean dark UI (ttkbootstrap 'darkly' if available, else ttk fallback).
- Keeps all pro features: presets, progress, logs, FFmpeg auto-detect, safe threading,
  HiDPI awareness, and Windows taskbar AppUserModelID for correct icon grouping.
"""

import os, sys, json, threading, subprocess, shutil, queue
from pathlib import Path

# ---------- Windows HiDPI ----------
def _enable_windows_dpi_awareness():
    try:
        if sys.platform.startswith("win"):
            import ctypes
            # SYSTEM_AWARE (1) is safe across Tk versions
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
_enable_windows_dpi_awareness()

# ---------- Optional PNG→ICO ----------
def _ensure_ico_from_png(png_path: Path, ico_path: Path) -> bool:
    try:
        from PIL import Image  # pillow
        if png_path.is_file():
            png = Image.open(png_path).convert("RGBA")
            sizes = [(16,16),(20,20),(24,24),(32,32),(40,40),(48,48),(64,64),(128,128),(256,256)]
            png.save(ico_path, format="ICO", sizes=sizes)
            return True
    except Exception:
        pass
    return False

# ---------- Theming ----------
try:
    import ttkbootstrap as tb
    from ttkbootstrap.constants import *
except Exception:
    tb = None

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP_INFO = {
    "name": "Music Forge",
    "version": "1.1.5",
    "developer": "Guillaume Lessard",
    "company": "iD01t Productions",
    "contact": "itechinfomtl@gmail.com",
    "website": "https://www.id01t.ca",
    "appid": "iD01tProductions.MusicForge"
}

def _base_dir() -> Path:
    # When frozen by PyInstaller, assets live under _MEIPASS
    return Path(getattr(sys, "_MEIPASS", Path(__file__).parent)).resolve()

BASE_DIR = _base_dir()

# Preferred assets folder name (ship it with --add-data "assets_music_forge;assets_music_forge")
ASSETS_DIR = BASE_DIR / "assets_music_forge"

# Candidate icon locations
def _find_icon_candidates():
    return {
        "ico": [
            BASE_DIR / "icon.ico",
            ASSETS_DIR / "icon.ico",
        ],
        "png": [
            BASE_DIR / "icon.png",
            ASSETS_DIR / "icon.png",
            ASSETS_DIR / "icon_256.png",
        ]
    }

def _resolve_icons():
    cand = _find_icon_candidates()
    ico = next((p for p in cand["ico"] if p.is_file()), None)
    png = next((p for p in cand["png"] if p.is_file()), None)

    # Auto-build ICO from PNG if needed
    if ico is None and png is not None:
        target = BASE_DIR / "icon_auto.ico"
        if _ensure_ico_from_png(png, target) and target.exists():
            ico = target

    return ico, png

# ---------- FFmpeg discovery ----------
def find_ffmpeg() -> str:
    env = os.environ.get("FFMPEG_PATH")
    if env and Path(env).is_file():
        return str(Path(env).resolve())
    exe_names = ["ffmpeg.exe", "ffmpeg"]
    candidates = []
    for name in exe_names:
        candidates += [BASE_DIR / name, BASE_DIR / "bin" / name, ASSETS_DIR / name]
    for c in candidates:
        if c.is_file():
            return str(c.resolve())
    which = shutil.which("ffmpeg")
    return which if which else "ffmpeg"
FFMPEG_BIN = find_ffmpeg()

# ---------- AppID ----------
def _set_taskbar_appid(app_id: str):
    try:
        if sys.platform.startswith("win"):
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass

# ---------- Worker ----------
class Worker(threading.Thread):
    def __init__(self, app):
        super().__init__(daemon=True)
        self.app = app
        self.q: "queue.Queue[tuple]" = queue.Queue()
        self._stop = threading.Event()

    def run(self):
        while not self._stop.is_set():
            try:
                job = self.q.get(timeout=0.2)
            except queue.Empty:
                continue
            if job is None:
                break
            func, args, kwargs = job
            try:
                func(*args, **kwargs)
            except Exception as e:
                self.app.log(f"[error] {e}", "error")
            finally:
                self.q.task_done()

    def stop(self):
        self._stop.set()
        try: self.q.put_nowait(None)
        except Exception: pass

    def submit(self, func, *args, **kwargs):
        self.q.put((func, args, kwargs))

# ---------- App ----------
class MusicForgePro:
    def __init__(self):
        # Force dark UI
        if tb:
            self.root = tb.Window(themename="darkly")
        else:
            self.root = tk.Tk()

        self.root.title("Music Forge — Audio Compiler & Processor")
        self.root.geometry("1100x720")
        self.root.minsize(980, 640)

        _set_taskbar_appid(APP_INFO["appid"])
        self._apply_icons()   # <- apply icons early

        # State
        self.file_queue = []
        self.output_format = tk.StringVar(value="mp3")
        self.quality_setting = tk.StringVar(value="high")
        self.normalize = tk.BooleanVar(value=False)
        self.trim_silence = tk.BooleanVar(value=False)
        self.sample_rate = tk.IntVar(value=44100)
        self.channels = tk.IntVar(value=2)
        self.output_directory = tk.StringVar(value=str(Path.home() / "Music" / "MusicForge_Output"))
        self.progress_var = tk.DoubleVar(value=0.0)
        self.count_var = tk.StringVar(value="0 files")
        self.status_var = tk.StringVar(value="Ready — Add audio files to begin")

        # Simple presets
        self.presets = {
            "High MP3":   {"format":"mp3","quality":"high","normalize":False,"trim_silence":False,"samplerate":44100,"channels":2},
            "Lossless":   {"format":"flac","quality":"lossless","normalize":False,"trim_silence":False,"samplerate":48000,"channels":2},
            "Podcast":    {"format":"m4a","quality":"medium","normalize":True,"trim_silence":True,"samplerate":44100,"channels":1},
            "Voice Note": {"format":"ogg","quality":"medium","normalize":True,"trim_silence":True,"samplerate":32000,"channels":1}
        }
        self.current_preset = tk.StringVar(value="High MP3")

        # Background worker
        self.worker = Worker(self); self.worker.start()

        # Build dark UI
        self._build_layout()
        self._apply_preset("High MP3")
        self._check_ffmpeg()

    # ----- Icons -----
    def _apply_icons(self):
        ico_path, png_path = _resolve_icons()

        # On Windows, iconbitmap controls the *title bar* icon. Must be an .ico with 16x16 present.
        try:
            if sys.platform.startswith("win") and ico_path and ico_path.exists():
                # Use both default and specific calls for reliability
                self.root.iconbitmap(default=str(ico_path))
                self.root.iconbitmap(str(ico_path))
        except Exception:
            pass

        # iconphoto controls the taskbar/dock and Alt-Tab preview in many cases
        try:
            if png_path and png_path.exists():
                logo = tk.PhotoImage(file=str(png_path))
                # Ensure it's applied as the window's photo icon (cover multi-platform cases)
                self.root.iconphoto(True, logo)
        except Exception:
            pass

    # ----- Layout (no View menu) -----
    def _build_layout(self):
        # Header
        top = ttk.Frame(self.root, padding=(16, 12, 16, 8))
        top.pack(fill="x")
        ttk.Label(top, text="🎵  Music Forge", font=("Segoe UI Variable", 22, "bold")).pack(side="left")
        ttk.Label(top, text="Professional Audio Compiler", font=("Segoe UI", 11)).pack(side="left", padx=(10, 0))
        ttk.Button(top, text="About", command=self._show_about).pack(side="right", padx=(8,0))
        ttk.Button(top, text="Help", command=self._show_help).pack(side="right")

        # Body
        body = ttk.Frame(self.root, padding=(16, 0, 16, 12))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=0)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        # Sidebar
        sidebar = ttk.LabelFrame(body, text="Actions", padding=12)
        sidebar.grid(row=0, column=0, sticky="ns", padx=(0, 12))
        ttk.Button(sidebar, text="➕ Add Files", style="Accent.TButton", command=self._add_files, width=22).pack(pady=4)
        ttk.Button(sidebar, text="📁 Add Folder", command=self._add_folder, width=22).pack(pady=4)
        ttk.Button(sidebar, text="🧹 Clear Queue", command=self._clear_queue, width=22).pack(pady=4)
        ttk.Separator(sidebar).pack(fill="x", pady=10)

        ttk.Label(sidebar, text="Preset").pack(anchor="w")
        preset_cb = ttk.Combobox(sidebar, textvariable=self.current_preset,
                                 values=list(self.presets.keys()), state="readonly", width=20)
        preset_cb.pack(pady=(2,8))
        ttk.Button(sidebar, text="Apply Preset", command=lambda: self._apply_preset(self.current_preset.get()),
                   width=22).pack(pady=(0,8))

        ttk.Label(sidebar, text="Output Format").pack(anchor="w")
        ttk.Combobox(sidebar, textvariable=self.output_format,
                     values=['mp3','wav','flac','ogg','m4a'], state='readonly', width=20).pack(pady=(2,8))

        ttk.Label(sidebar, text="Quality").pack(anchor="w")
        ttk.Combobox(sidebar, textvariable=self.quality_setting,
                     values=['low','medium','high','lossless'], state='readonly', width=20).pack(pady=(2,8))

        ttk.Label(sidebar, text="Sample Rate (Hz)").pack(anchor="w")
        ttk.Combobox(sidebar, textvariable=self.sample_rate,
                     values=[22050,32000,44100,48000], state='readonly', width=20).pack(pady=(2,8))

        ttk.Label(sidebar, text="Channels").pack(anchor="w")
        ttk.Combobox(sidebar, textvariable=self.channels, values=[1,2], state='readonly', width=20).pack(pady=(2,8))

        ttk.Checkbutton(sidebar, text="🔊 Loudness Normalize", variable=self.normalize).pack(anchor="w", pady=(6,2))
        ttk.Checkbutton(sidebar, text="✂️ Trim Silence", variable=self.trim_silence).pack(anchor="w", pady=(0,8))

        ttk.Label(sidebar, text="Output Directory").pack(anchor="w", pady=(8,0))
        ttk.Entry(sidebar, textvariable=self.output_directory, width=24).pack(pady=2)
        ttk.Button(sidebar, text="Browse", command=self._browse_output, width=22).pack()
        ttk.Button(sidebar, text="Open", command=self._open_output, width=22).pack(pady=(4,10))

        # Queue
        main = ttk.LabelFrame(body, text="Processing Queue", padding=12)
        main.grid(row=0, column=1, sticky="nsew")
        main.rowconfigure(1, weight=1)
        main.columnconfigure(0, weight=1)

        stats = ttk.Frame(main)
        stats.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(stats, textvariable=self.count_var).pack(side="left")
        ttk.Label(stats, text=" | ").pack(side="left")
        self.ffmpeg_label = ttk.Label(stats, text="Detecting FFmpeg…")
        self.ffmpeg_label.pack(side="left")

        columns = ("file","ext","size","path")
        self.tree = ttk.Treeview(main, columns=columns, show="headings", height=12, selectmode="extended")
        self.tree.heading("file", text="File Name")
        self.tree.heading("ext", text="Type")
        self.tree.heading("size", text="Size")
        self.tree.heading("path", text="Full Path")
        self.tree.column("file", width=380)
        self.tree.column("ext", width=80, anchor="center")
        self.tree.column("size", width=100, anchor="e")
        self.tree.column("path", width=380)
        vsb = ttk.Scrollbar(main, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=1, column=0, sticky="nsew")
        vsb.grid(row=1, column=1, sticky="ns")

        # Footer
        footer = ttk.Frame(main)
        footer.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        footer.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(footer, variable=self.progress_var, maximum=100)
        self.progress.grid(row=0, column=0, sticky="ew")
        self.progress_label = ttk.Label(footer, text="0%")
        self.progress_label.grid(row=0, column=1, padx=(10,0))
        self.process_btn = ttk.Button(footer, text="🚀 Compile Music", command=self._start_processing, style="Accent.TButton", width=25)
        self.process_btn.grid(row=0, column=2, padx=(12,0))

        # Logs
        logs = ttk.LabelFrame(self.root, text="Activity Log", padding=12)
        logs.pack(fill="both", expand=False, padx=16, pady=(0,14))
        self.log_widget = tk.Text(logs, height=8, wrap="word")
        self.log_widget.pack(fill="both", expand=True)
        self.log_widget.configure(state="disabled")
        self.log_widget.tag_configure("warn", foreground="#caa300")
        self.log_widget.tag_configure("error", foreground="#d04d4d")

        # Status bar
        status = ttk.Frame(self.root, padding=(16, 6))
        status.pack(fill="x", side="bottom")
        ttk.Label(status, textvariable=self.status_var).pack(side="left")

    # ----- Presets -----
    def _apply_preset(self, name: str):
        p = self.presets.get(name)
        if not p: return
        self.output_format.set(p["format"])
        self.quality_setting.set(p["quality"])
        self.normalize.set(p["normalize"])
        self.trim_silence.set(p["trim_silence"])
        self.sample_rate.set(p["samplerate"])
        self.channels.set(p["channels"])
        self.current_preset.set(name)
        self.log(f"Preset applied: {name}")

    # ----- Helpers -----
    def _check_ffmpeg(self):
        try:
            subprocess.run([FFMPEG_BIN, "-version"], capture_output=True, check=True)
            self.ffmpeg_available = True
            self.ffmpeg_label.config(text=f"FFmpeg → {FFMPEG_BIN}")
            self.log(f"FFmpeg detected: {FFMPEG_BIN}")
        except Exception:
            self.ffmpeg_available = False
            self.ffmpeg_label.config(text="FFmpeg not found")
            self.log("FFmpeg not found — place ffmpeg next to the app or set FFMPEG_PATH", "warn")

    def _browse_output(self):
        directory = filedialog.askdirectory(title="Select Output Directory", initialdir=self.output_directory.get())
        if directory:
            self.output_directory.set(directory)

    def _open_output(self):
        path = self.output_directory.get()
        if not path: return
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.run(["open", path])
            else:
                subprocess.run(["xdg-open", path])
        except Exception as e:
            self.log(f"Failed to open: {e}", "error")

    def _add_files(self):
        files = filedialog.askopenfilenames(
            title="Select Audio Files",
            filetypes=[("Audio Files", "*.mp3 *.wav *.flac *.ogg *.m4a *.aac *.wma"), ("All Files", "*.*")]
        )
        self._add_paths(files)

    def _add_folder(self):
        folder = filedialog.askdirectory(title="Select Folder with Audio Files")
        if not folder: return
        audio_ext = {'.mp3','.wav','.flac','.ogg','.m4a','.aac','.wma'}
        files = [str(p) for p in Path(folder).rglob('*') if p.suffix.lower() in audio_ext]
        self._add_paths(files)

    def _add_paths(self, paths):
        added = 0
        for f in paths:
            if f and f not in self.file_queue:
                self.file_queue.append(f)
                self._insert_file_row(f)
                added += 1
        self.count_var.set(f"{len(self.file_queue)} files")
        if added:
            self.status_var.set(f"Added {added} file(s)")
            self.log(f"Added {added} item(s) to queue")

    def _clear_queue(self):
        self.file_queue.clear()
        for row in self.tree.get_children(): self.tree.delete(row)
        self.progress_var.set(0); self.progress_label.config(text="0%")
        self.count_var.set("0 files"); self.status_var.set("Queue cleared")
        self.log("Queue cleared")

    def _insert_file_row(self, path_str):
        p = Path(path_str)
        size = p.stat().st_size if p.exists() else 0
        size_mb = f"{size/1024/1024:.2f} MB"
        ext = p.suffix.lower().replace('.', '').upper()
        self.tree.insert('', 'end', values=(p.name, ext, size_mb, str(p)))

    def log(self, text: str, level="info"):
        self.log_widget.configure(state="normal")
        tag = {"info":None, "warn":"warn", "error":"error"}.get(level)
        if tag: self.log_widget.insert("end", text + "\n", tag)
        else:   self.log_widget.insert("end", text + "\n")
        self.log_widget.see("end")
        self.log_widget.configure(state="disabled")

    # ----- Processing -----
    def _start_processing(self):
        if not self.file_queue:
            messagebox.showwarning("No Files", "Please add audio files to process"); return
        if not getattr(self, "ffmpeg_available", False):
            messagebox.showerror("FFmpeg Required", "FFmpeg is required for audio processing. Place ffmpeg next to the app or set FFMPEG_PATH."); return
        outdir = self.output_directory.get()
        if not outdir:
            messagebox.showwarning("Output Folder", "Choose an output directory"); return

        os.makedirs(outdir, exist_ok=True)
        self.process_btn.configure(text="Processing...", state='disabled')
        self.progress_var.set(0); self.progress_label.config(text="0%")
        self.status_var.set("Processing…"); self.log("Processing started")
        self.worker.submit(self._process_files)

    def _process_files(self):
        total = len(self.file_queue)
        for i, input_file in enumerate(self.file_queue, start=1):
            try:
                pct = int(((i-1) / total) * 100)
                self._ui_progress(pct)
                self.status_var.set(f"Processing: {Path(input_file).name}")

                output = Path(self.output_directory.get()) / f"{Path(input_file).stem}.{self.output_format.get()}"
                cmd = self._build_ffmpeg_command(input_file, str(output))
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    self.log(f"[ffmpeg] error for {input_file}:\n{result.stderr}", "error")
                else:
                    self.log(f"OK → {output}")
            except Exception as e:
                self.log(f"[error] {input_file}: {e}", "error")

        self._ui_progress(100)
        self.status_var.set(f"Done — Processed {total} file(s) → {self.output_directory.get()}")
        self.root.after(0, lambda: [self.process_btn.configure(text="🚀 Compile Music", state='normal'),
                                    messagebox.showinfo("Processing Complete",
                                                        f"Successfully processed {total} audio file(s)!\n\nOutput: {self.output_directory.get()}")])
        self.log("Processing complete")

    def _build_ffmpeg_command(self, input_file, output_file):
        fmt = self.output_format.get()
        qual = self.quality_setting.get()
        sr = int(self.sample_rate.get())
        ch = int(self.channels.get())

        cmd = [FFMPEG_BIN, "-y", "-i", input_file, "-ac", str(ch), "-ar", str(sr)]

        # Filters
        afilters = []
        if self.trim_silence.get():
            afilters.append("silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.4")
        if self.normalize.get():
            afilters.append("loudnorm=I=-14:TP=-1.5:LRA=11")
        if afilters:
            cmd.extend(["-af", ",".join(afilters)])

        # Format presets
        if fmt == "mp3":
            qmap = {"low":["-b:a","128k"],"medium":["-b:a","192k"],"high":["-b:a","320k"],"lossless":["-b:a","320k"]}
            cmd.extend(qmap.get(qual, ["-b:a", "192k"]))
        elif fmt == "wav":
            cmd.extend(["-acodec", "pcm_s16le"])
        elif fmt == "flac":
            cmd.extend(["-acodec", "flac", "-compression_level", "5"])
        elif fmt == "ogg":
            qmap = {"low":["-q:a","3"],"medium":["-q:a","6"],"high":["-q:a","9"],"lossless":["-q:a","10"]}
            cmd.extend(qmap.get(qual, ["-q:a", "6"]))
        elif fmt == "m4a":
            qmap = {"low":["-c:a","aac","-b:a","128k"],"medium":["-c:a","aac","-b:a","192k"],"high":["-c:a","aac","-b:a","256k"],"lossless":["-c:a","aac","-b:a","320k"]}
            cmd.extend(qmap.get(qual, ["-c:a", "aac", "-b:a", "192k"]))

        cmd.append(output_file)
        return cmd

    # ----- UI thread helpers -----
    def _ui_progress(self, pct: int):
        self.root.after(0, lambda: [self.progress_var.set(pct), self.progress_label.config(text=f"{pct}%")])

    # ----- Dialogs -----
    def _show_about(self):
        info = (
            f"{APP_INFO['name']} v{APP_INFO['version']}\n"
            f"Developed by {APP_INFO['developer']} — {APP_INFO['company']}\n"
            f"Contact: {APP_INFO['contact']} | Web: {APP_INFO['website']}"
        )
        messagebox.showinfo("About Music Forge", info)

    def _show_help(self):
        text = (
            "How to Use:\n"
            "1) Add Files or Add Folder to queue.\n"
            "2) Pick a Preset or set Format/Quality.\n"
            "3) Optional: Normalize, Trim Silence, Sample Rate, Channels.\n"
            "4) Choose Output Directory.\n"
            "5) Click 'Compile Music' to start.\n\n"
            "FFmpeg: place it next to the app, in ./bin, or in PATH. "
            "Set FFMPEG_PATH env var to force a specific binary."
        )
        messagebox.showinfo("Help", text)

def main():
    app = MusicForgePro()
    app.root.protocol("WM_DELETE_WINDOW", lambda: (app.worker.stop(), app.root.destroy()))
    app.root.mainloop()

if __name__ == "__main__":
    main()
