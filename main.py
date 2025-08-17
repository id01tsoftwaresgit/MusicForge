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

import os, sys, json, threading, subprocess, shutil, queue, re
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
from tkinter import ttk, filedialog, messagebox, simpledialog
from mutagen import File
from tkinterdnd2 import DND_FILES, TkinterDnD
import pygame

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
CUSTOM_PRESETS_FILE = BASE_DIR / "presets.json"
CONFIG_FILE = BASE_DIR / "config.json"

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
            self.root = TkinterDnD.Tk() # Use DND-aware Tk root
            self.style = tb.Style()
        else:
            self.root = TkinterDnD.Tk()
            self.style = ttk.Style()

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
        self.naming_pattern = tk.StringVar(value="[artist] - [title]")
        self.progress_var = tk.DoubleVar(value=0.0)
        self.count_var = tk.StringVar(value="0 files")
        self.status_var = tk.StringVar(value="Ready — Add audio files to begin")

        # Player state
        self.player_track_var = tk.StringVar(value="No track selected")
        self.player_time_var = tk.StringVar(value="00:00 / 00:00")
        self.player_slider_var = tk.DoubleVar(value=0)
        self.is_playing = False
        self.selected_track_path = None
        self.track_length_sec = 0
        self.seeking = False

        # Init Pygame Mixer
        try:
            pygame.mixer.init()
        except Exception as e:
            self.log(f"Could not initialize audio player: {e}", "error")

        # Presets
        self.default_presets = {
            "High MP3":   {"format":"mp3","quality":"high","normalize":False,"trim_silence":False,"samplerate":44100,"channels":2},
            "Lossless":   {"format":"flac","quality":"lossless","normalize":False,"trim_silence":False,"samplerate":48000,"channels":2},
            "Podcast":    {"format":"m4a","quality":"medium","normalize":True,"trim_silence":True,"samplerate":44100,"channels":1},
            "Voice Note": {"format":"ogg","quality":"medium","normalize":True,"trim_silence":True,"samplerate":32000,"channels":1}
        }
        self.custom_presets = {}
        self.current_preset = tk.StringVar(value="High MP3")
        self.theme_var = tk.StringVar(value="darkly")

        # Background worker
        self.worker = Worker(self); self.worker.start()

        # Build UI and load settings
        self._build_layout()
        self._load_config() # This will set the theme
        self._load_presets()
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

        # Theme menu
        theme_menu_btn = ttk.Menubutton(top, text="Theme")
        theme_menu_btn.pack(side="right", padx=(8,0))
        theme_menu = tk.Menu(theme_menu_btn, tearoff=0)
        theme_menu_btn["menu"] = theme_menu

        light_themes = ['cosmo', 'flatly', 'journal', 'litera', 'lumen', 'minty', 'pulse', 'sandstone', 'united', 'yeti']
        dark_themes = ['cyborg', 'darkly', 'solar', 'superhero']

        light_menu = tk.Menu(theme_menu, tearoff=0)
        dark_menu = tk.Menu(theme_menu, tearoff=0)
        theme_menu.add_cascade(label="Light Themes", menu=light_menu)
        theme_menu.add_cascade(label="Dark Themes", menu=dark_menu)

        for theme in light_themes:
            light_menu.add_radiobutton(label=theme, variable=self.theme_var, value=theme, command=self._change_theme)
        for theme in dark_themes:
            dark_menu.add_radiobutton(label=theme, variable=self.theme_var, value=theme, command=self._change_theme)

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
        action_buttons = ttk.Frame(sidebar)
        action_buttons.pack(fill="x", pady=4)
        ttk.Button(action_buttons, text="➕ Add Files", style="Accent.TButton", command=self._add_files).pack(side="left", expand=True, fill="x", padx=(0,2))
        ttk.Button(action_buttons, text="📁 Add Folder", command=self._add_folder).pack(side="left", expand=True, fill="x", padx=(2,0))

        action_buttons2 = ttk.Frame(sidebar)
        action_buttons2.pack(fill="x", pady=4)
        ttk.Button(action_buttons2, text="📝 Edit Tags", command=self._open_tag_editor).pack(side="left", expand=True, fill="x", padx=(0,2))
        ttk.Button(action_buttons2, text="🧹 Clear Queue", command=self._clear_queue).pack(side="left", expand=True, fill="x", padx=(2,0))

        ttk.Separator(sidebar).pack(fill="x", pady=10)

        ttk.Label(sidebar, text="Preset").pack(anchor="w")
        self.preset_cb = ttk.Combobox(sidebar, textvariable=self.current_preset, state="readonly", width=20)
        self.preset_cb.pack(pady=(2, 4), fill="x")

        preset_buttons = ttk.Frame(sidebar)
        preset_buttons.pack(pady=(0, 8), fill="x")
        ttk.Button(preset_buttons, text="Apply", command=lambda: self._apply_preset(self.current_preset.get()), width=6).pack(side="left", expand=True, fill="x", padx=(0,2))
        ttk.Button(preset_buttons, text="💾 Save", command=self._save_preset, width=7).pack(side="left", expand=True, fill="x", padx=2)
        ttk.Button(preset_buttons, text="🗑️ Del", command=self._delete_preset, width=7).pack(side="left", expand=True, fill="x", padx=(2,0))

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

        ttk.Separator(sidebar).pack(fill="x", pady=10)

        ttk.Label(sidebar, text="Output File Naming").pack(anchor="w")
        ttk.Entry(sidebar, textvariable=self.naming_pattern, width=24).pack(pady=2, fill="x")

        ttk.Label(sidebar, text="Output Directory").pack(anchor="w", pady=(8,0))
        ttk.Entry(sidebar, textvariable=self.output_directory, width=24).pack(pady=2, fill="x")
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

        columns = ("file", "title", "artist", "album", "ext", "size", "path")
        self.tree = ttk.Treeview(main, columns=columns, show="headings", height=12, selectmode="extended")
        self.tree.heading("file", text="File Name")
        self.tree.heading("title", text="Title")
        self.tree.heading("artist", text="Artist")
        self.tree.heading("album", text="Album")
        self.tree.heading("ext", text="Type")
        self.tree.heading("size", text="Size")
        self.tree.heading("path", text="Full Path")

        self.tree.column("file", width=250)
        self.tree.column("title", width=200)
        self.tree.column("artist", width=150)
        self.tree.column("album", width=150)
        self.tree.column("ext", width=60, anchor="center")
        self.tree.column("size", width=80, anchor="e")
        self.tree.column("path", width=300) # Hidden or less prominent

        vsb = ttk.Scrollbar(main, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.bind("<Double-1>", self._open_tag_editor)
        self.tree.bind("<<TreeviewSelect>>", self._on_track_select)
        self.tree.drop_target_register(DND_FILES)
        self.tree.dnd_bind('<<Drop>>', self._on_drop)
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

        # Player
        player = ttk.LabelFrame(self.root, text="▶️ Music Player", padding=12)
        player.pack(fill="x", padx=16, pady=(10,0))
        player.columnconfigure(1, weight=1)

        self.player_track_label = ttk.Label(player, textvariable=self.player_track_var, font=("Segoe UI", 10, "bold"))
        self.player_track_label.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0,5))

        self.player_time_label = ttk.Label(player, textvariable=self.player_time_var, font=("Segoe UI", 9))
        self.player_time_label.grid(row=1, column=2, sticky="e")

        self.player_slider = ttk.Scale(player, variable=self.player_slider_var, from_=0, to=100)
        self.player_slider.bind("<ButtonPress-1>", self._start_seek)
        self.player_slider.bind("<ButtonRelease-1>", self._end_seek)
        self.player_slider.grid(row=1, column=1, sticky="ew", padx=10)

        player_btns = ttk.Frame(player)
        player_btns.grid(row=1, column=0)
        self.play_btn = ttk.Button(player_btns, text="▶ Play", width=8, command=self._play_pause_track)
        self.play_btn.pack(side="left")
        self.stop_btn = ttk.Button(player_btns, text="⏹ Stop", width=8, command=self._stop_track)
        self.stop_btn.pack(side="left", padx=5)

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
    def _load_presets(self):
        if CUSTOM_PRESETS_FILE.exists():
            try:
                with open(CUSTOM_PRESETS_FILE, "r") as f:
                    self.custom_presets = json.load(f)
            except (json.JSONDecodeError, IOError):
                self.custom_presets = {}
                self.log("Could not load custom presets from file.", "warn")
        else:
            self.custom_presets = {}
        self._update_preset_combobox()

    def _update_preset_combobox(self):
        default_names = list(self.default_presets.keys())
        custom_names = list(self.custom_presets.keys())
        all_presets = default_names + custom_names
        self.preset_cb["values"] = all_presets
        if not self.current_preset.get() in all_presets:
            self.current_preset.set(default_names[0] if default_names else "")

    def _save_preset(self):
        name = simpledialog.askstring("Save Preset", "Enter a name for the new preset:", parent=self.root)
        if not name:
            return
        if name in self.default_presets:
            messagebox.showwarning("Cannot Overwrite", f'"{name}" is a default preset and cannot be overwritten.', parent=self.root)
            return

        preset_data = {
            "format": self.output_format.get(),
            "quality": self.quality_setting.get(),
            "normalize": self.normalize.get(),
            "trim_silence": self.trim_silence.get(),
            "samplerate": self.sample_rate.get(),
            "channels": self.channels.get()
        }
        self.custom_presets[name] = preset_data

        try:
            with open(CUSTOM_PRESETS_FILE, "w") as f:
                json.dump(self.custom_presets, f, indent=4)
            self.log(f'Preset "{name}" saved.')
            self._update_preset_combobox()
            self.current_preset.set(name)
        except IOError:
            self.log(f'Failed to save preset "{name}".', "error")
            messagebox.showerror("Save Failed", "Could not write presets to file.", parent=self.root)

    def _delete_preset(self):
        name = self.current_preset.get()
        if not name:
            return
        if name in self.default_presets:
            messagebox.showwarning("Cannot Delete", f'"{name}" is a default preset and cannot be deleted.', parent=self.root)
            return
        if name not in self.custom_presets:
            messagebox.showinfo("Not Found", f'Custom preset "{name}" not found.', parent=self.root)
            return

        if messagebox.askyesno("Confirm Delete", f'Are you sure you want to delete the preset "{name}"?', parent=self.root):
            del self.custom_presets[name]
            try:
                with open(CUSTOM_PRESETS_FILE, "w") as f:
                    json.dump(self.custom_presets, f, indent=4)
                self.log(f'Preset "{name}" deleted.')
                self._update_preset_combobox()
                self.current_preset.set(list(self.default_presets.keys())[0])
            except IOError:
                self.log(f'Failed to save presets after deleting "{name}".', "error")
                messagebox.showerror("Save Failed", "Could not write presets to file.", parent=self.root)

    def _apply_preset(self, name: str):
        p = self.default_presets.get(name) or self.custom_presets.get(name)
        if not p: return
        self.output_format.set(p["format"])
        self.quality_setting.set(p["quality"])
        self.normalize.set(p["normalize"])
        self.trim_silence.set(p["trim_silence"])
        self.sample_rate.set(p["samplerate"])
        self.channels.set(p["channels"])
        self.current_preset.set(name)
        self.log(f"Preset applied: {name}")

    # ----- Theming -----
    def _load_config(self):
        try:
            with open(CONFIG_FILE, "r") as f:
                config = json.load(f)
                self.theme_var.set(config.get("theme", "darkly"))
        except (IOError, json.JSONDecodeError):
            self.theme_var.set("darkly") # Default theme

        self.style.theme_use(self.theme_var.get())
        self.log(f"Theme '{self.theme_var.get()}' loaded.")

    def _save_config(self):
        config = {"theme": self.theme_var.get()}
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(config, f, indent=4)
        except IOError:
            self.log("Error saving config file.", "error")

    def _change_theme(self):
        theme = self.theme_var.get()
        self.style.theme_use(theme)
        self.log(f"Theme changed to '{theme}'")
        self._save_config()

    # ----- Tag Editing -----
    def _open_tag_editor(self, event=None):
        selected_ids = self.tree.selection()
        if not selected_ids:
            messagebox.showinfo("No Selection", "Please select one or more files to edit.", parent=self.root)
            return

        file_items = [item for item in self.file_queue if item["id"] in selected_ids]

        if file_items:
            TagEditorWindow(self.root, file_items, self._update_tags_for_items)

    def _update_tags_for_items(self, file_items, new_tags_to_apply):
        for item in file_items:
            # Update the data model
            for key, value in new_tags_to_apply.items():
                item["tags"][key] = value

            # Update the treeview
            p = Path(item["path"])
            size = p.stat().st_size if p.exists() else 0
            size_mb = f"{size/1024/1024:.2f} MB"
            ext = p.suffix.lower().replace('.', '').upper()

            self.tree.item(item["id"], values=(
                p.name, item["tags"].get('title',''), item["tags"].get('artist',''), item["tags"].get('album',''),
                ext, size_mb, str(p)
            ))

        self.log(f"Updated tags for {len(file_items)} item(s).")

    # ----- Player -----
    def _format_time(self, seconds):
        m, s = divmod(int(seconds), 60)
        return f"{m:02d}:{s:02d}"

    def _update_player_progress(self):
        if pygame.mixer.music.get_busy() and not self.seeking:
            current_time = pygame.mixer.music.get_pos() / 1000
            self.player_slider_var.set(current_time)

            time_str = f"{self._format_time(current_time)} / {self._format_time(self.track_length_sec)}"
            self.player_time_var.set(time_str)

        if self.is_playing:
            self.root.after(500, self._update_player_progress)

    def _on_track_select(self, event=None):
        selected_ids = self.tree.selection()
        if not selected_ids:
            return

        item_id = selected_ids[0]
        file_item = next((item for item in self.file_queue if item["id"] == item_id), None)

        if file_item and file_item["path"] != self.selected_track_path:
            self._stop_track()
            try:
                sound = pygame.mixer.Sound(file_item["path"])
                self.track_length_sec = sound.get_length()
                self.player_slider.config(to=self.track_length_sec)
                self.selected_track_path = file_item["path"]
                self.player_track_var.set(Path(self.selected_track_path).name)
                self.player_time_var.set(f"00:00 / {self._format_time(self.track_length_sec)}")
                self.log(f"Player loaded: {self.player_track_var.get()}")
            except Exception as e:
                self.log(f"Player error loading sound: {e}", "error")
                messagebox.showerror("Player Error", f"Could not load file:\n{e}", parent=self.root)


    def _play_pause_track(self):
        if not self.selected_track_path:
            messagebox.showinfo("No Track", "Please select a track from the queue to play.", parent=self.root)
            return

        try:
            if not pygame.mixer.music.get_busy() and not self.is_playing: # Not playing, so start
                pygame.mixer.music.load(self.selected_track_path)
                pygame.mixer.music.play()
                self.play_btn.configure(text="⏸ Pause")
                self.is_playing = True
                self.log(f"Playing: {self.player_track_var.get()}")
                self._update_player_progress() # Start the update loop
            elif self.is_playing: # Playing, so pause
                pygame.mixer.music.pause()
                self.play_btn.configure(text="▶ Play")
                self.is_playing = False
                self.log("Player paused.")
            else: # Paused, so unpause
                pygame.mixer.music.unpause()
                self.play_btn.configure(text="⏸ Pause")
                self.is_playing = True
                self.log("Player resumed.")
                self._update_player_progress() # Resume the update loop
        except Exception as e:
            self.log(f"Player error: {e}", "error")
            messagebox.showerror("Player Error", f"Could not play file:\n{e}", parent=self.root)

    def _start_seek(self, event=None):
        self.seeking = True

    def _end_seek(self, event=None):
        self.seeking = False
        self._seek_track()

    def _seek_track(self):
        if self.selected_track_path:
            pos = self.player_slider_var.get()
            pygame.mixer.music.play(start=pos)
            # Instantly update time display after seek
            time_str = f"{self._format_time(pos)} / {self._format_time(self.track_length_sec)}"
            self.player_time_var.set(time_str)

    def _stop_track(self):
        pygame.mixer.music.stop()
        self.play_btn.configure(text="▶ Play")
        self.is_playing = False
        self.player_track_var.set("No track selected")
        self.player_time_var.set("00:00 / 00:00")
        self.player_slider_var.set(0)
        self.selected_track_path = None
        self.track_length_sec = 0
        self.log("Player stopped.")

    # ----- Helpers -----
    def _on_drop(self, event):
        # The data is a string of file paths, sometimes with curly braces
        path_str = event.data.replace("{", "").replace("}", "")
        paths = self.root.splitlist(path_str) # Use tk's splitlist to handle spaces in paths
        self._add_paths(paths)

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
        existing_paths = {item['path'] for item in self.file_queue}
        for f_path in paths:
            if f_path and f_path not in existing_paths:
                p = Path(f_path)
                tags = self._read_tags(f_path)
                file_item = {
                    "id": None, # Treeview item ID
                    "path": f_path,
                    "tags": tags
                }
                self.file_queue.append(file_item)
                self._insert_file_row(file_item)
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

    def _read_tags(self, path_str):
        p = Path(path_str)
        tags = {"title": "", "artist": "", "album": ""}
        try:
            audio = File(path_str, easy=True)
            if audio:
                tags["title"] = audio.get("title", [""])[0]
                tags["artist"] = audio.get("artist", [""])[0]
                tags["album"] = audio.get("album", [""])[0]
        except Exception as e:
            self.log(f"Could not read tags for {p.name}: {e}", "warn")
        return tags

    def _insert_file_row(self, file_item):
        path_str = file_item["path"]
        tags = file_item["tags"]
        p = Path(path_str)
        size = p.stat().st_size if p.exists() else 0
        size_mb = f"{size/1024/1024:.2f} MB"
        ext = p.suffix.lower().replace('.', '').upper()

        item_id = self.tree.insert('', 'end', values=(
            p.name, tags.get('title',''), tags.get('artist',''), tags.get('album',''),
            ext, size_mb, str(p)
        ))
        file_item["id"] = item_id

    def log(self, text: str, level="info"):
        self.log_widget.configure(state="normal")
        tag = {"info":None, "warn":"warn", "error":"error"}.get(level)
        if tag: self.log_widget.insert("end", text + "\n", tag)
        else:   self.log_widget.insert("end", text + "\n")
        self.log_widget.see("end")
        self.log_widget.configure(state="disabled")

    # ----- Processing -----
    def _sanitize_filename(self, name: str) -> str:
        # Remove illegal characters for Windows filenames, which is the most restrictive
        return re.sub(r'[\\/*?:"<>|]', "_", name)

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
        pattern = self.naming_pattern.get()

        for i, file_item in enumerate(self.file_queue, start=1):
            input_path = file_item["path"]
            tags = file_item["tags"]
            p_in = Path(input_path)

            try:
                pct = int(((i-1) / total) * 100)
                self._ui_progress(pct)
                self.status_var.set(f"Processing: {p_in.name}")

                # Generate filename from pattern
                name = pattern.lower()
                name = name.replace("[artist]", tags.get("artist") or "Unknown Artist")
                name = name.replace("[album]", tags.get("album") or "Unknown Album")
                name = name.replace("[title]", tags.get("title") or p_in.stem)
                name = name.replace("[filename]", p_in.stem)
                sanitized_name = self._sanitize_filename(name)

                output_path = Path(self.output_directory.get()) / f"{sanitized_name}.{self.output_format.get()}"
                cmd = self._build_ffmpeg_command(input_path, str(output_path), tags)
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    self.log(f"[ffmpeg] error for {p_in.name}:\n{result.stderr}", "error")
                else:
                    self.log(f"OK → {output_path.name}")
            except Exception as e:
                self.log(f"[error] {p_in.name}: {e}", "error")

        self._ui_progress(100)
        self.status_var.set(f"Done — Processed {total} file(s) → {self.output_directory.get()}")
        self.root.after(0, lambda: [self.process_btn.configure(text="🚀 Compile Music", state='normal'),
                                    messagebox.showinfo("Processing Complete",
                                                        f"Successfully processed {total} audio file(s)!\n\nOutput: {self.output_directory.get()}")])
        self.log("Processing complete")

    def _build_ffmpeg_command(self, input_file, output_file, tags=None):
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

        # Metadata
        if tags:
            for key, value in tags.items():
                if value: # Only add metadata if it's not empty
                    cmd.extend(["-metadata", f"{key}={value}"])

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
            "1. Add Files: Click 'Add Files', 'Add Folder', or drag and drop files onto the queue.\n"
            "2. Set Options: Pick a preset or manually set the format, quality, and other options.\n"
            "3. Customize (Optional):\n"
            "   - Presets: Save current settings as a new preset or delete custom ones.\n"
            "   - Naming: Define an output filename pattern using tags like [artist], [title], etc.\n"
            "   - Tags: Double-click a file or use 'Edit Tags' to modify its metadata for the output.\n"
            "4. Choose Output Directory.\n"
            "5. Click 'Compile Music' to start.\n\n"
            "File Naming Tags:\n"
            "Use [artist], [album], [title], and [filename] in the naming pattern field.\n\n"
            "FFmpeg: place it next to the app, in ./bin, or in PATH. "
            "Set FFMPEG_PATH env var to force a specific binary."
        )
        messagebox.showinfo("Help", text)

# ---------- Tag Editor Window ----------
class TagEditorWindow(tk.Toplevel):
    def __init__(self, parent, file_items, callback):
        super().__init__(parent)
        self.transient(parent)
        self.grab_set()
        self.title(f"Edit Tags for {len(file_items)} Item(s)")
        self.geometry("450x230")
        self.resizable(False, False)

        self.file_items = file_items
        self.callback = callback

        # --- Determine initial values ---
        def get_common_value(tag_name):
            first_value = self.file_items[0]["tags"].get(tag_name, "")
            if all(item["tags"].get(tag_name, "") == first_value for item in self.file_items):
                return first_value
            return "[Multiple Values]"

        initial_title = get_common_value("title")
        initial_artist = get_common_value("artist")
        initial_album = get_common_value("album")

        # --- UI Elements ---
        frame = ttk.Frame(self, padding=15)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(2, weight=1)

        # Checkboxes
        self.apply_title = tk.BooleanVar()
        self.apply_artist = tk.BooleanVar()
        self.apply_album = tk.BooleanVar()
        ttk.Checkbutton(frame, variable=self.apply_title).grid(row=0, column=0, sticky="w", padx=(0,5))
        ttk.Checkbutton(frame, variable=self.apply_artist).grid(row=1, column=0, sticky="w", padx=(0,5))
        ttk.Checkbutton(frame, variable=self.apply_album).grid(row=2, column=0, sticky="w", padx=(0,5))

        # Labels
        ttk.Label(frame, text="Title:").grid(row=0, column=1, sticky="w", pady=5)
        ttk.Label(frame, text="Artist:").grid(row=1, column=1, sticky="w", pady=5)
        ttk.Label(frame, text="Album:").grid(row=2, column=1, sticky="w", pady=5)

        # Entries
        self.title_var = tk.StringVar(value=initial_title)
        ttk.Entry(frame, textvariable=self.title_var).grid(row=0, column=2, sticky="ew", pady=5)
        self.artist_var = tk.StringVar(value=initial_artist)
        ttk.Entry(frame, textvariable=self.artist_var).grid(row=1, column=2, sticky="ew", pady=5)
        self.album_var = tk.StringVar(value=initial_album)
        ttk.Entry(frame, textvariable=self.album_var).grid(row=2, column=2, sticky="ew", pady=5)

        ttk.Label(frame, text="Check boxes to apply changes.", font=("Segoe UI", 8)).grid(row=3, column=1, columnspan=2, sticky="w", pady=(10,0))

        # --- Buttons ---
        btn_frame = ttk.Frame(self, padding=(0, 0, 15, 15))
        btn_frame.pack(fill="x")
        ttk.Button(btn_frame, text="Save", command=self.save, style="Accent.TButton").pack(side="right")
        ttk.Button(btn_frame, text="Cancel", command=self.destroy).pack(side="right", padx=10)

    def save(self):
        tags_to_apply = {}
        if self.apply_title.get():
            tags_to_apply["title"] = self.title_var.get()
        if self.apply_artist.get():
            tags_to_apply["artist"] = self.artist_var.get()
        if self.apply_album.get():
            tags_to_apply["album"] = self.album_var.get()

        if tags_to_apply:
            self.callback(self.file_items, tags_to_apply)

        self.destroy()

def main():
    app = MusicForgePro()
    app.root.protocol("WM_DELETE_WINDOW", lambda: (app.worker.stop(), app.root.destroy()))
    app.root.mainloop()

if __name__ == "__main__":
    main()
