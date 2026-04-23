<div align="center">

# 🎧 FocusAudio

**One window. One sound.**

FocusAudio dynamically manages your Windows audio so only the focused application makes sound. Background apps gracefully fade out — and back in when you need them.

[![License: MIT](https://img.shields.io/badge/License-MIT-6c5ce7.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows-0078D6.svg)](https://github.com/RoboticKru/FocusAudio)

</div>

---

## ✨ Features

| Feature | Description |
|---|---|
| 🎵 **Smooth Crossfading** | Audio gracefully fades in and out over 1.2s instead of jarring, instant muting |
| 🧠 **Smart Fallback** | Paused your lecture? FocusAudio detects silence and fades your background music back in automatically |
| 🌐 **Multi-Browser** | Handles Chrome, Edge, Firefox, Brave, Opera, and Vivaldi — matching sessions to active tabs |
| 🛡️ **Whitelist** | Mark apps like Discord or Spotify to never be muted |
| 📌 **System Tray** | Runs silently with an animated equalizer icon. Toggle on/off with a right-click |
| ⚡ **Lightweight** | Single portable `.exe`. No installation, no services, no internet required |

## 📥 Installation

### Quick Start
1. Download **`FocusAudio.exe`** from the [latest release](https://github.com/RoboticKru/FocusAudio/releases).
2. Double-click to run. It launches silently into your system tray.
3. That's it! Switch between apps and hear the magic.

> **💡 Tip:** To run on startup, create a shortcut to `FocusAudio.exe`, press `Win + R`, type `shell:startup`, and drop the shortcut there.

### From Source
```bash
git clone https://github.com/RoboticKru/FocusAudio.git
cd FocusAudio
pip install -r requirements.txt
python focus_audio.pyw
```

## ⚙️ Configuration

Edit the top of `focus_audio.pyw` to customise behaviour:

```python
FADE_DURATION  = 1.2    # seconds for the fade effect
POLL_INTERVAL  = 0.15   # how often to check focus (seconds)
WHITELIST      = set()  # e.g. {"spotify", "discord"} — never mute these
```

## 🎯 Use Case

FocusAudio was built for a simple scenario: **watching lectures while listening to background music.** When you pause your lecture, your music smoothly fades back in. When you resume, the music fades away. No manual switching, no overlapping audio — just focus.

It works best with your lecture in one browser (e.g. Chrome) and music in another (e.g. Edge), since Windows can only distinguish audio at the application level.

## 📄 License

[MIT License](LICENSE) — free to use, modify, and distribute with attribution.
