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

## Deployment

Configured as a **static** deployment serving the `docs/` directory.
