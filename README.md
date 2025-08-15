# 🎵 Music Forge — Professional Audio Compiler & Processor

**Version:** 1.1.5
**Developer:** Guillaume Lessard — iD01t Productions
**Website:** [https://www.id01t.ca](https://www.id01t.ca)
**Support:** [itechinfomtl@gmail.com](mailto:itechinfomtl@gmail.com)

Music Forge is a **store-ready, professional-grade audio batch processor** with a modern dark UI. Built for Windows with full FFmpeg integration, it compiles and processes audio locally, ensuring **speed, quality, and privacy**.

---

## ✨ Features

* **Batch Processing** — Add multiple files or entire folders at once.
* **High-Quality Audio Output** — MP3, WAV, FLAC, OGG, and M4A formats.
* **Professional Presets** — High MP3, Lossless, Podcast, Voice Note.
* **Audio Enhancements**:

  * Loudness normalization
  * Silence trimming
  * Custom sample rates and channels
* **FFmpeg Auto-Detection** — Works if `ffmpeg.exe` is in the same folder, in `./bin`, or in your PATH.
* **Dark Mode UI** — Powered by [`ttkbootstrap`](https://github.com/israel-dryer/ttkbootstrap) with graceful fallback to native Tkinter.
* **Windows Taskbar Integration** — Proper AppID and icon grouping.
* **HiDPI Awareness** — Crisp UI on modern displays.
* **Local Processing Only** — No file uploads, full offline privacy.

---

## 📦 Requirements

* **OS:** Windows 10/11 (x64)
* **Optional:** `ffmpeg.exe` for local bundling and processing.

---

## 🚀 Quick Start

1. **Launch** Music Forge.
2. **Add Files** or an entire folder.
3. **Choose a Preset** or manually set:

   * Output format
   * Quality
   * Sample rate
   * Channels
   * Optional normalization and silence trimming
4. **Select Output Folder**.
5. **Click** `🚀 Compile Music`.

---

## 🔧 Advanced Usage

* **Custom Presets:** You can create your own by modifying the in-app settings.
* **FFmpeg Path Override:**
  Set the environment variable `FFMPEG_PATH` to your preferred `ffmpeg` binary.
* **Icon Customization:**
  Place `icon.ico` or `icon.png` in the app folder or in `assets_music_forge`.

---

## 📂 File Processing Notes

* Progress bar and log output keep you updated.
* All logs are stored in-app (not written to disk unless exported).
* Output files are saved to your selected folder without overwriting originals.

---

## 🛠 Building From Source

```bash
# Install dependencies
pip install pillow ttkbootstrap

# Optional: For Windows EXE build
pip install pyinstaller

# Run directly
python main.py

# Build executable
pyinstaller --noconsole --onefile ^
  --name "MusicForge" ^
  --icon "assets_music_forge/icon.ico" ^
  main.py
```

---

## 📄 License

MIT License © 2025 Guillaume Lessard — iD01t Productions

