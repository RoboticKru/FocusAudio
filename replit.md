# FocusAudio

## Project Overview

FocusAudio is a Windows desktop application that dynamically manages audio so only the focused application makes sound. Background apps gracefully fade out and back in.

This repository contains:
- **Python desktop app** (`focus_audio.pyw`, `mixer_gui.py`) — Windows-only, uses `pycaw`, `pystray`, `comtypes`, `pillow`
- **Static landing page** (`docs/`) — HTML/CSS website served via Replit

## Running on Replit

The Windows desktop app cannot run on Replit (requires Windows audio APIs). Instead, the **static landing page** in `docs/` is served using a simple Python HTTP server.

### Workflow

- **Start application**: `python serve.py` — serves `docs/` on port 5000

### Files

- `serve.py` — simple Python HTTP server for the static docs site
- `docs/index.html` — landing page HTML
- `docs/style.css` — landing page styles
- `focus_audio.pyw` — main Windows desktop app (not runnable on Replit)
- `mixer_gui.py` — volume mixer GUI (Windows only)
- `requirements.txt` — Python dependencies for the desktop app

## Recent Changes (v2.0.1)

### Bug Fixes (`focus_audio.pyw`)
- **Stale session targets**: `start_fade` now only skips a fade if one is *actively in progress* to the same target. Previously, a stale `_session_targets` entry for a gone-and-returned session would suppress all new fades.
- **Silence timer / volume leaks**: Added `_cleanup_stale_session_data()` called every ~5s to prune `_session_targets`, `_fade_tokens`, `_silence_timers`, and `_session_volumes` for sessions that no longer exist.
- **Paused apps not resumed on disable**: Added `restore_all_volumes_and_resume()` which also drains `_paused_by_us` and calls `play_app_media`. Used on toggle-off and quit.
- **`RESUME_BACKGROUND_WHEN_FOCUS_SILENT` implemented**: If the focused "main" app is silent/paused, background apps are no longer ducked (music fades back in automatically).
- **Mixer window race condition**: `open_mixer` is now guarded by `_mixer_lock` to prevent double-instantiation on rapid clicks.
- **`_on_focus_out` false positives**: Fixed to only close when the root window itself loses focus, not child widgets.
- **Paused app resumption on role change**: Main apps that were paused by FocusAudio are now resumed when they regain "main" status.

### UI Redesign (`mixer_gui.py`)
- **Custom `VolumeBar` widget**: Canvas-drawn slider with hover/drag effects replacing the blocky `tk.Scale`.
- **Rounded window corners + drop shadow**: Applied via Windows DWM API (`DwmSetWindowAttribute`).
- **Left accent stripe on cards**: Each app card has a colored stripe (purple = main, teal = background, grey = ignore).
- **Role-colored pill buttons**: Role badges are colored to match their meaning.
- **Draggable window**: Title bar drag support.
- **Better media card**: Compact layout with left accent bar, smaller album art, cleaner controls.
- **Footer redesign**: Cleaner ducking slider using the new `VolumeBar`, icon-based pause toggle.
- **"Playing" state highlight**: Active cards get a subtle background tint.

## Deployment

Configured as a **static** deployment serving the `docs/` directory.
