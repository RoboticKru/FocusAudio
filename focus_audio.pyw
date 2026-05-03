"""
FocusAudio - Only the focused window makes sound, everything else fades out.
Requirements: pip install pycaw pystray pillow comtypes
"""

import threading
import time
import ctypes
import ctypes.wintypes
import sys
import os
import re
from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume
from comtypes import CLSCTX_ALL
from pycaw.pycaw import IAudioMeterInformation
import logging
import json
import urllib.request
import tempfile
import subprocess
import asyncio

try:
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager
    HAS_WINSDK = True
except ImportError:
    HAS_WINSDK = False

VERSION = "2.2.0"
REPO_OWNER = "RoboticKru"
REPO_NAME = "FocusAudio"

# Debug log file — delete or set to False to disable
DEBUG_LOG = False
if DEBUG_LOG:
    logging.basicConfig(
        filename=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'focusaudio_debug.log'),
        level=logging.DEBUG,
        format='%(asctime)s.%(msecs)03d %(message)s',
        datefmt='%H:%M:%S',
    )
    log = logging.getLogger('FocusAudio')
else:
    log = logging.getLogger('FocusAudio')
    log.addHandler(logging.NullHandler())

try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:
    pystray = None
    Image = None
    ImageDraw = None

HAS_TRAY = pystray is not None and Image is not None and ImageDraw is not None

# ── Config ────────────────────────────────────────────────────────────────────
FADE_DURATION   = 1.2   # seconds to fade out background audio
FADE_STEPS      = 30    # smoothness of fade
POLL_INTERVAL   = 0.15  # seconds between focus checks
RESTORE_VOLUME  = 1.0   # volume to restore to when window gains focus
SILENCE_THRESHOLD = 0.005  # peak level below this = silent
SILENCE_DEBOUNCE  = 0.8    # seconds of silence before triggering fallback

# Apps to NEVER mute (process name, lowercase, no .exe needed)
WHITELIST = set()   # add your own here e.g. {"spotify"}

# Browsers often expose multiple concurrent audio sessions.
# For these apps, keep only one best-matching session audible.
SINGLE_FOCUS_APPS = {"chrome", "msedge", "firefox", "brave", "opera", "vivaldi"}

# If the focused browser session is inactive (for example, paused lecture),
# temporarily restore one active background session.
RESUME_BACKGROUND_WHEN_FOCUS_SILENT = True
# ─────────────────────────────────────────────────────────────────────────────

user32   = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

enabled = True          # global on/off toggle
_session_volumes = {}   # session_key -> last known volume before mute
_fade_tokens = {}       # session_key -> latest fade token (cancels stale fades)
_global_token_counter = 0
_last_focus_by_app = {} # app_name -> session_key last chosen for focus
_session_targets = {}   # session_key -> last requested target volume
_active_fades = set()   # tokens of currently active fade threads
_silence_timers = {}    # session_key -> timestamp when silence was first detected
_current_sessions = []  # latest session snapshot for the GUI
_lock = threading.Lock()
_refresh_requested = threading.Event()
_mixer_lock = threading.Lock()  # guards mixer window creation

# ── Per-app config & roles ───────────────────────────────────────────────────
_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'focusaudio_config.json')
_app_config = {}     # app_name -> {"role": "auto", "vol": 1.0}
_global_config = {}  # "ducking_level": 0.20


def load_config():
    global _app_config, _global_config
    try:
        if os.path.exists(_CONFIG_FILE):
            with open(_CONFIG_FILE, 'r') as f:
                data = json.load(f)
                _app_config = data.get("apps", {})
                _global_config = data.get("global", {})
    except Exception:
        _app_config = {}
        _global_config = {}


def save_config():
    try:
        with open(_CONFIG_FILE, 'w') as f:
            json.dump({"apps": _app_config, "global": _global_config}, f, indent=2)
    except Exception:
        pass


def get_global_ducking():
    return _global_config.get("ducking_level", 0.20)

def set_global_ducking(level):
    _global_config["ducking_level"] = max(0.0, min(1.0, float(level)))
    save_config()

def get_pause_background():
    return _global_config.get("pause_background", False)

def set_pause_background(enabled):
    _global_config["pause_background"] = bool(enabled)
    save_config()

def get_pause_after_fade():
    """Fade to silence first, then pause playback so the track doesn't skip."""
    return _global_config.get("pause_after_fade", False)

def set_pause_after_fade(enabled):
    _global_config["pause_after_fade"] = bool(enabled)
    save_config()

def get_app_config(app_name):
    """Get per-app config, creating defaults if needed."""
    if app_name not in _app_config:
        _app_config[app_name] = {"role": "auto", "vol": 1.0}
    return _app_config[app_name]


def set_app_config(app_name, role=None, vol=None):
    """Update per-app config and persist."""
    cfg = get_app_config(app_name)
    if role is not None:
        cfg["role"] = str(role)
    if vol is not None:
        cfg["vol"] = max(0.0, min(1.0, float(vol)))
    _app_config[app_name] = cfg
    save_config()


def get_current_sessions():
    """Return the latest session snapshot for the GUI."""
    with _lock:
        return list(_current_sessions)


def get_foreground_pid():
    hwnd = user32.GetForegroundWindow()
    pid  = ctypes.wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def get_foreground_title():
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""

    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""

    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return (buf.value or "").strip().lower()


def get_process_name(pid):
    PROCESS_QUERY_LIMITED = 0x1000
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED, False, pid)
    if not h:
        return ""
    buf = ctypes.create_unicode_buffer(260)
    ctypes.windll.psapi.GetProcessImageFileNameW(h, buf, 260)
    kernel32.CloseHandle(h)
    return os.path.basename(buf.value).lower().replace(".exe", "")


def get_audio_sessions():
    try:
        sessions = AudioUtilities.GetAllSessions()
        return sessions
    except Exception:
        return []


def get_session_pid(session):
    pid = getattr(session, "ProcessId", None)
    if isinstance(pid, int):
        return pid

    proc = getattr(session, "Process", None)
    if proc is not None:
        try:
            return int(proc.pid)
        except Exception:
            return 0

    return 0


def get_session_key(session, pid):
    for attr in ("InstanceIdentifier", "Identifier"):
        try:
            value = getattr(session, attr, "")
            if value:
                return str(value)
        except Exception:
            continue

    return f"pid:{pid}"


def get_session_display_name(session):
    try:
        return (session.DisplayName or "").strip().lower()
    except Exception:
        return ""


# ── Friendly process-name lookup ─────────────────────────────────────────────

_FRIENDLY_NAMES = {
    # Browsers
    "chrome":               "Chrome",
    "firefox":              "Firefox",
    "msedge":               "Microsoft Edge",
    "msedgewebview2":       "Edge WebView",
    "opera":                "Opera",
    "brave":                "Brave",
    "iexplore":             "Internet Explorer",
    # Music / media players
    "spotify":              "Spotify",
    "vlc":                  "VLC",
    "wmplayer":             "Windows Media Player",
    "groove":               "Groove Music",
    "itunes":               "iTunes",
    "winamp":               "Winamp",
    "foobar2000":           "foobar2000",
    "musicbee":             "MusicBee",
    "aimp":                 "AIMP",
    "amazon music":         "Amazon Music",
    "amazonmusic":          "Amazon Music",
    "applemusic":           "Apple Music",
    "tidal":                "TIDAL",
    "deezer":               "Deezer",
    "youtubemusic":         "YouTube Music",
    # Communication
    "discord":              "Discord",
    "teams":                "Microsoft Teams",
    "slack":                "Slack",
    "zoom":                 "Zoom",
    "skype":                "Skype",
    "telegram":             "Telegram",
    "whatsapp":             "WhatsApp",
    "whatsapp.root":        "WhatsApp",
    # System / drivers
    "rtkuwp":               "Realtek Audio",
    "audiodg":              "Windows Audio",
    "svchost":              "Windows Service",
    "msedge webview2":      "Edge WebView",
    # Streaming / games
    "obs64":                "OBS Studio",
    "obs32":                "OBS Studio",
    "obs":                  "OBS Studio",
    "netflix":              "Netflix",
    "amazonprimevideo":     "Prime Video",
    "disneyplus":           "Disney+",
    "steam":                "Steam",
    "epicgameslauncher":    "Epic Games",
    "battle.net":           "Battle.net",
}


def get_friendly_name(process_name: str) -> str:
    """Return a human-readable display name for a process name."""
    key = process_name.lower().replace(".exe", "").strip()
    return _FRIENDLY_NAMES.get(key, process_name.replace("_", " ").title())


def get_session_peak(session):
    """Get the current audio peak level (0.0-1.0) from the session's meter."""
    try:
        meter = session._ctl.QueryInterface(IAudioMeterInformation)
        return meter.GetPeakValue()
    except Exception:
        return 0.0


def is_session_active(session, session_key=None):
    """Check if session is producing audible sound.
    For sessions we've muted (volume near 0), trusts session.State since the
    peak meter always reads 0 when volume is 0 even if audio is playing.
    For audible sessions, uses the peak meter for near-instant silence detection
    with a short debounce to avoid flickering on brief pauses."""
    try:
        # First check: is the session even in an active state?
        state_active = int(session.State) == 1
        if not state_active:
            return False

        # If session volume is near zero (muted by us), the peak meter
        # will always read 0 regardless of whether audio is playing.
        # Trust session.State in this case.
        current_vol = get_session_volume(session)
        if current_vol < 0.05:
            return True

        # Session volume is audible — use peak meter for precise detection
        peak = get_session_peak(session)
        is_audible = peak > SILENCE_THRESHOLD

        if session_key is not None:
            now = time.monotonic()
            with _lock:
                if is_audible:
                    # Audio is playing, clear any silence timer
                    _silence_timers.pop(session_key, None)
                    return True
                else:
                    # Audio stopped, start or check debounce timer
                    first_silent = _silence_timers.get(session_key)
                    if first_silent is None:
                        _silence_timers[session_key] = now
                        return True  # still "active" during debounce
                    elif now - first_silent < SILENCE_DEBOUNCE:
                        return True  # still within debounce window
                    else:
                        return False  # confirmed silent

        return is_audible
    except Exception:
        return True


def tokenize_text(text):
    if not text:
        return set()
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2}


def title_match_score(session_display, fg_title):
    if not session_display or not fg_title:
        return 0

    if session_display in fg_title or fg_title in session_display:
        return 8

    a = tokenize_text(session_display)
    b = tokenize_text(fg_title)
    if not a or not b:
        return 0

    return min(4, len(a.intersection(b)))


def choose_focused_session(candidates, app_name, fg_title):
    if not candidates:
        return None

    prev_key = _last_focus_by_app.get(app_name)
    best_info = None
    best_score = -1

    for info in candidates:
        score = title_match_score(info["display"], fg_title)
        if prev_key and info["key"] == prev_key:
            score += 1

        if score > best_score:
            best_score = score
            best_info = info

    if best_info is None:
        return None

    if best_score <= 0:
        best_info = max(candidates, key=lambda item: item["volume"])

    _last_focus_by_app[app_name] = best_info["key"]
    return best_info["key"]


def choose_background_session(candidates):
    if not candidates:
        return None

    def score(info):
        return max(info["remembered"], info["volume"])

    best = max(candidates, key=score)
    return best["key"]


def set_session_volume(session, vol):
    try:
        vol = max(0.0, min(1.0, vol))
        interface = session._ctl.QueryInterface(ISimpleAudioVolume)
        interface.SetMasterVolume(vol, None)
    except Exception:
        pass


def get_session_volume(session):
    try:
        interface = session._ctl.QueryInterface(ISimpleAudioVolume)
        return interface.GetMasterVolume()
    except Exception:
        return 1.0


def fade_session(session, session_key, target_vol, token,
                 steps=FADE_STEPS, duration=FADE_DURATION, post_complete_fn=None):
    _completed = False
    try:
        steps = max(1, int(steps))
        duration = max(0.0, float(duration))
        start_vol = get_session_volume(session)
        if abs(start_vol - target_vol) < 0.01:
            # Already at target — still counts as "completed" for post-fade actions
            with _lock:
                if _fade_tokens.get(session_key) == token:
                    _completed = True
            return

        step_size = (target_vol - start_vol) / steps
        delay = duration / steps if duration else 0.0

        for i in range(steps):
            with _lock:
                if _fade_tokens.get(session_key) != token:
                    return
            set_session_volume(session, start_vol + step_size * (i + 1))
            if delay > 0:
                time.sleep(delay)

        with _lock:
            if _fade_tokens.get(session_key) != token:
                return
        set_session_volume(session, target_vol)
        # Fade reached its target without being superseded
        with _lock:
            if _fade_tokens.get(session_key) == token:
                _completed = True
    finally:
        with _lock:
            if token in _active_fades:
                _active_fades.remove(token)
        if _completed and post_complete_fn:
            try:
                post_complete_fn()
            except Exception:
                pass


def start_fade(session, session_key, target_vol, post_complete_fn=None):
    global _global_token_counter
    target_vol = max(0.0, min(1.0, float(target_vol)))

    with _lock:
        previous_target = _session_targets.get(session_key)
        # Only skip if a fade to this exact target is already actively running.
        current_token = _fade_tokens.get(session_key)
        fade_in_progress = current_token is not None and current_token in _active_fades
        if fade_in_progress and previous_target is not None and abs(previous_target - target_vol) < 0.01:
            return

        _session_targets[session_key] = target_vol
        _global_token_counter += 1
        token = _global_token_counter
        _fade_tokens[session_key] = token
        _active_fades.add(token)

    threading.Thread(
        target=fade_session,
        args=(session, session_key, target_vol, token),
        kwargs={"post_complete_fn": post_complete_fn},
        daemon=True,
    ).start()


def _cleanup_stale_session_data(live_keys):
    """Remove tracking data for sessions that are no longer present.
    Call this periodically from the monitor loop to prevent stale targets
    from blocking future fades when a session disappears and reappears."""
    with _lock:
        stale = set(_session_targets.keys()) - live_keys
        for key in stale:
            _session_targets.pop(key, None)
            _fade_tokens.pop(key, None)
            _silence_timers.pop(key, None)
            # Keep _session_volumes — it holds the pre-mute volume we want to
            # restore if the session reappears. Prune only very old entries.

        # Prune _session_volumes that haven't been seen for a long time
        # (not in live_keys AND not referenced by any active fade)
        vol_stale = set(_session_volumes.keys()) - live_keys
        for key in vol_stale:
            _session_volumes.pop(key, None)


def restore_all_volumes():
    """Restore all sessions to their pre-mute volumes and resume any paused apps."""
    for session in get_audio_sessions():
        try:
            pid = get_session_pid(session)
            session_key = get_session_key(session, pid)
            with _lock:
                saved = _session_volumes.get(session_key, RESTORE_VOLUME)
            set_session_volume(session, saved)
        except Exception:
            continue


def restore_all_volumes_and_resume():
    """Restore volumes AND resume any media apps we paused. Used on disable."""
    restore_all_volumes()
    if HAS_WINSDK:
        for app_name in list(_paused_by_us_ref) | list(_paused_after_fade_ref):
            try:
                play_app_media(app_name)
            except Exception:
                pass
        _paused_by_us_ref.clear()
        _paused_after_fade_ref.clear()

# Shared sets so restore/disable can drain them from outside the monitor thread
_paused_by_us_ref = set()
_paused_after_fade_ref = set()


def get_active_session_fallback():
    """Return {title, artist} for the most active audio session.
    Used as a fallback in the media card when SMTC (winsdk) is unavailable
    or when the app (e.g. Chrome, SimplyMusic) isn't registered with SMTC."""
    with _lock:
        sessions = list(_current_sessions)
    if not sessions:
        return None
    active = [s for s in sessions if s.get("active")]
    if not active:
        return None
    focused = [s for s in active if s.get("focused")]
    best = focused[0] if focused else active[0]
    title = best["name"].replace("_", " ").title()
    display = (best.get("display") or "").strip()
    artist = display if display else "Playing audio"
    return {"title": title, "artist": artist}


# ── SMTC Media Manager ───────────────────────────────────────────────────────
_media_manager = None
_media_loop = None

def _init_media_manager():
    global _media_manager, _media_loop
    if not HAS_WINSDK: return
    _media_loop = asyncio.new_event_loop()
    threading.Thread(target=_media_loop.run_forever, daemon=True).start()
    try:
        _media_manager = asyncio.run_coroutine_threadsafe(
            GlobalSystemMediaTransportControlsSessionManager.request_async(),
            _media_loop
        ).result()
    except Exception as e:
        log.error(f"Failed to init media manager: {e}")

def _get_media_session(app_name):
    if not _media_manager: return None
    try:
        for session in _media_manager.get_sessions():
            if app_name.lower() in session.source_app_user_model_id.lower():
                return session
    except Exception:
        pass
    return None

def is_playing(app_name):
    session = _get_media_session(app_name)
    if session:
        try:
            info = session.get_playback_info()
            return info and info.playback_status == 4 # 4 = Playing
        except Exception:
            pass
    return False

def pause_app_media(app_name):
    """Pause app via SMTC if registered, otherwise simulate the media Play/Pause key."""
    session = _get_media_session(app_name)
    if session and _media_loop:
        asyncio.run_coroutine_threadsafe(session.try_pause_async(), _media_loop)
    else:
        # Chrome, SimplyMusic etc. don't register SMTC sessions.
        # Sending the global media key is the only reliable way to reach them.
        _send_media_key(_VK_MEDIA_PLAY_PAUSE)

def play_app_media(app_name):
    """Resume app via SMTC if registered, otherwise simulate the media Play/Pause key."""
    session = _get_media_session(app_name)
    if session and _media_loop:
        asyncio.run_coroutine_threadsafe(session.try_play_async(), _media_loop)
    else:
        _send_media_key(_VK_MEDIA_PLAY_PAUSE)

def get_current_media_info():
    """Returns a dict with title, artist, status, thumbnail_bytes, and source_app."""
    if not _media_manager: return None
    try:
        session = _media_manager.get_current_session()
        if not session: return None

        future = asyncio.run_coroutine_threadsafe(session.try_get_media_properties_async(), _media_loop)
        props = future.result(timeout=1.0)

        info = session.get_playback_info()
        status = info.playback_status if info else 0

        thumb_bytes = None
        if props.thumbnail:
            try:
                async def fetch_thumb():
                    from winrt.windows.storage.streams import Buffer, InputStreamOptions
                    stream = await props.thumbnail.open_read_async()
                    buf = Buffer(stream.size)
                    await stream.read_async(buf, stream.size, InputStreamOptions.NONE)
                    return memoryview(buf).tobytes()

                thumb_future = asyncio.run_coroutine_threadsafe(fetch_thumb(), _media_loop)
                thumb_bytes = thumb_future.result(timeout=1.0)
            except Exception as e:
                log.debug(f"Failed to read thumbnail: {e}")

        return {
            "title": props.title,
            "artist": props.artist,
            "status": status,
            "thumbnail_bytes": thumb_bytes,
            "app": session.source_app_user_model_id
        }
    except Exception as e:
        log.debug(f"get_current_media_info failed: {e}")
        return None

# Virtual-key codes for media keys (work system-wide via keybd_event)
_VK_MEDIA_PLAY_PAUSE = 0xB3
_VK_MEDIA_NEXT_TRACK = 0xB0
_VK_MEDIA_PREV_TRACK = 0xB1
_KEYEVENTF_KEYUP     = 0x0002


def _send_media_key(vk: int):
    """Simulate a hardware media key press via the Win32 keybd_event API.
    This works for any app that captures global media keys (Chrome, VLC, etc.)."""
    try:
        import ctypes
        ctypes.windll.user32.keybd_event(vk, 0, 0, 0)           # key down
        ctypes.windll.user32.keybd_event(vk, 0, _KEYEVENTF_KEYUP, 0)  # key up
    except Exception as e:
        log.debug(f"_send_media_key({vk:#x}) failed: {e}")


def media_play_pause():
    """Toggle play/pause: tries SMTC first, falls back to simulated media key."""
    sent = False
    if _media_manager and _media_loop:
        try:
            session = _media_manager.get_current_session()
            if session:
                asyncio.run_coroutine_threadsafe(
                    session.try_toggle_play_pause_async(), _media_loop)
                sent = True
        except Exception:
            pass
    if not sent:
        _send_media_key(_VK_MEDIA_PLAY_PAUSE)


def media_next():
    """Skip to next track: tries SMTC first, falls back to simulated media key."""
    sent = False
    if _media_manager and _media_loop:
        try:
            session = _media_manager.get_current_session()
            if session:
                asyncio.run_coroutine_threadsafe(
                    session.try_skip_next_async(), _media_loop)
                sent = True
        except Exception:
            pass
    if not sent:
        _send_media_key(_VK_MEDIA_NEXT_TRACK)


def media_prev():
    """Skip to previous track: tries SMTC first, falls back to simulated media key."""
    sent = False
    if _media_manager and _media_loop:
        try:
            session = _media_manager.get_current_session()
            if session:
                asyncio.run_coroutine_threadsafe(
                    session.try_skip_previous_async(), _media_loop)
                sent = True
        except Exception:
            pass
    if not sent:
        _send_media_key(_VK_MEDIA_PREV_TRACK)

# ── Monitor Loop ─────────────────────────────────────────────────────────────

def monitor_loop():
    global _current_sessions, _paused_by_us_ref, _paused_after_fade_ref
    _init_media_manager()
    _paused_by_us = _paused_by_us_ref          # shared reference for instant-pause mode
    _paused_after_fade = _paused_after_fade_ref # shared reference for fade-then-pause mode

    # COM must be initialized on each thread that uses Windows audio APIs.
    import comtypes
    comtypes.CoInitialize()

    _stale_cleanup_counter = 0

    while True:
        if _refresh_requested.is_set():
            _refresh_requested.clear()

        if not enabled:
            time.sleep(POLL_INTERVAL)
            continue

        fg_pid  = get_foreground_pid()
        fg_name = get_process_name(fg_pid)
        fg_title = get_foreground_title()
        sessions    = get_audio_sessions()

        session_infos = []

        for session in sessions:
            try:
                pid = get_session_pid(session)
                if pid <= 0:
                    continue

                name = get_process_name(pid)

                if name in WHITELIST:
                    continue

                session_key = get_session_key(session, pid)
                display = get_session_display_name(session)
                volume = get_session_volume(session)
                active = is_session_active(session, session_key)
                same_app_focus = (pid == fg_pid) or (fg_name and name == fg_name)

                with _lock:
                    if session_key not in _session_volumes:
                        if volume > 0.05:
                            _session_volumes[session_key] = volume
                        else:
                            _session_volumes[session_key] = RESTORE_VOLUME
                    remembered = _session_volumes[session_key]

                session_infos.append(
                    {
                        "session": session,
                        "pid": pid,
                        "name": name,
                        "key": session_key,
                        "display": display,
                        "volume": volume,
                        "active": active,
                        "remembered": remembered,
                        "same_app_focus": bool(same_app_focus),
                    }
                )

            except Exception:
                continue

        # Update session snapshot for the GUI
        with _lock:
            _current_sessions.clear()
            for info in session_infos:
                cfg = get_app_config(info["name"])
                _current_sessions.append(
                    {"name": info["name"], "display": info["display"],
                     "active": info["active"], "focused": info["same_app_focus"],
                     "volume": info["volume"], "role": cfg["role"], "manual_vol": cfg["vol"]}
                )

        # Periodically clean up stale session tracking data (every ~5 seconds)
        _stale_cleanup_counter += 1
        if _stale_cleanup_counter >= int(5.0 / POLL_INTERVAL):
            _stale_cleanup_counter = 0
            live_keys = {info["key"] for info in session_infos}
            _cleanup_stale_session_data(live_keys)

        # ── Role-based Ducking Logic ──
        ducking_level = get_global_ducking()
        main_is_active = False
        focused_main_is_silent = False

        # 1. Detect if ANY Main app is currently playing audio, and whether
        #    the focused main app is silent (for RESUME_BACKGROUND_WHEN_FOCUS_SILENT)
        for info in session_infos:
            app_name = info["name"]
            cfg = get_app_config(app_name)
            role = cfg["role"]

            is_main = False
            if role == "main":
                is_main = True
            elif role == "auto":
                is_main = info["same_app_focus"]

            if is_main:
                if info["active"]:
                    main_is_active = True
                else:
                    # Main app is focused but silent (e.g. paused lecture)
                    focused_main_is_silent = True

        # If RESUME_BACKGROUND_WHEN_FOCUS_SILENT is on, a focused-but-silent main
        # does NOT count as "main active" — background audio should come back.
        if RESUME_BACKGROUND_WHEN_FOCUS_SILENT and focused_main_is_silent and not main_is_active:
            main_is_active = False

        # 2. Apply volumes to all sessions based on roles
        for info in session_infos:
            try:
                session = info["session"]
                session_key = info["key"]
                app_name = info["name"]

                cfg = get_app_config(app_name)
                role = cfg["role"]
                manual_vol = cfg["vol"]

                if role == "ignore":
                    continue

                is_main = False
                if role == "main":
                    is_main = True
                elif role == "auto":
                    is_main = info["same_app_focus"]

                # A Background app ducks or pauses if Main is active
                should_duck = False
                if role == "background":
                    is_main = False
                    should_duck = main_is_active

                if is_main:
                    # Resume if we had paused this app by either method
                    if app_name in _paused_by_us:
                        play_app_media(app_name)
                        _paused_by_us.discard(app_name)
                    if app_name in _paused_after_fade:
                        play_app_media(app_name)
                        _paused_after_fade.discard(app_name)
                    start_fade(session, session_key, manual_vol)

                else:
                    if role == "background" and get_pause_background():
                        # ── Mode 1: Instant pause ──
                        if should_duck:
                            if app_name not in _paused_by_us:
                                if is_playing(app_name):
                                    pause_app_media(app_name)
                                    _paused_by_us.add(app_name)
                        else:
                            if app_name in _paused_by_us:
                                play_app_media(app_name)
                                _paused_by_us.discard(app_name)
                        start_fade(session, session_key, manual_vol)

                    elif get_pause_after_fade():
                        # ── Mode 2: Fade to silence, then pause ──
                        if should_duck:
                            if app_name not in _paused_after_fade:
                                # Arm the pause: add to set now so we don't re-trigger,
                                # then fade to 0 and pause via post_complete_fn.
                                _paused_after_fade.add(app_name)
                                start_fade(session, session_key, 0.0,
                                           post_complete_fn=lambda n=app_name: pause_app_media(n))
                            # else: already fading/paused — leave it alone
                        else:
                            if app_name in _paused_after_fade:
                                # Un-duck: resume playback, then fade back up
                                play_app_media(app_name)
                                _paused_after_fade.discard(app_name)
                                start_fade(session, session_key, manual_vol)
                            else:
                                start_fade(session, session_key, manual_vol)

                    else:
                        # ── Mode 3: Volume ducking only ──
                        target = manual_vol * ducking_level if should_duck else manual_vol
                        start_fade(session, session_key, target)

            except Exception:
                continue

        time.sleep(POLL_INTERVAL)


# ── Tray icon ─────────────────────────────────────────────────────────────────

import math
import random

# Bar configuration for the equalizer icon
_ICON_SIZE = 64
_BAR_COUNT = 7
_BAR_WIDTH = 5
_BAR_GAP = 2
_BAR_MIN_H = 6
_BAR_MAX_H = 44
_BAR_Y_BOTTOM = 54
_ICON_ANIM_FPS = 6
_icon_frame = 0
_icon_anim_stop = threading.Event()

# Precomputed bar x-positions (centered)
_total_bar_width = _BAR_COUNT * _BAR_WIDTH + (_BAR_COUNT - 1) * _BAR_GAP
_bar_x_start = (_ICON_SIZE - _total_bar_width) // 2


def _bar_heights_animated(frame):
    """Generate equalizer bar heights for a given animation frame."""
    heights = []
    for i in range(_BAR_COUNT):
        phase = i * 1.3 + frame * 0.55
        h = _BAR_MIN_H + (_BAR_MAX_H - _BAR_MIN_H) * (0.5 + 0.5 * math.sin(phase))
        heights.append(int(h))
    return heights


def _bar_heights_static():
    """Static bar heights for the paused/disabled state."""
    pattern = [0.3, 0.5, 0.7, 1.0, 0.7, 0.5, 0.3]
    return [int(_BAR_MIN_H + (_BAR_MAX_H - _BAR_MIN_H) * p) for p in pattern]


def make_icon(active=True, frame=0):
    if not HAS_TRAY or Image is None or ImageDraw is None:
        return None

    img = Image.new("RGBA", (_ICON_SIZE, _ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    if active:
        heights = _bar_heights_animated(frame)
        color = (108, 92, 231)  # accent purple matching the website
    else:
        heights = _bar_heights_static()
        color = (100, 100, 115)  # muted grey

    for i, h in enumerate(heights):
        x = _bar_x_start + i * (_BAR_WIDTH + _BAR_GAP)
        y_top = _BAR_Y_BOTTOM - h
        r = _BAR_WIDTH // 2
        draw.rounded_rectangle(
            [x, y_top, x + _BAR_WIDTH, _BAR_Y_BOTTOM],
            radius=r,
            fill=color,
        )

    return img


def _animate_icon(icon):
    """Background thread that updates the tray icon to create animation."""
    global _icon_frame
    while not _icon_anim_stop.is_set():
        if enabled:
            _icon_frame += 1
            try:
                icon.icon = make_icon(True, _icon_frame)
            except Exception:
                pass
        _icon_anim_stop.wait(1.0 / _ICON_ANIM_FPS)


_mixer_window = None


def open_mixer(icon, item):
    """Open the mixer GUI window (thread-safe, single instance)."""
    global _mixer_window
    with _mixer_lock:
        try:
            from mixer_gui import MixerWindow
            if _mixer_window is None or not _mixer_window.is_open():
                _mixer_window = MixerWindow()
                _mixer_window.show()
            else:
                _mixer_window.close()
        except Exception as e:
            log.debug(f"Mixer error: {e}")


def toggle(icon, item):
    global enabled
    enabled = not enabled

    if not enabled:
        restore_all_volumes_and_resume()

    _refresh_requested.set()
    icon.icon = make_icon(enabled)
    icon.title = f"FocusAudio — {'ON' if enabled else 'OFF'}"


def quit_app(icon, item):
    _icon_anim_stop.set()
    restore_all_volumes_and_resume()
    icon.stop()
    sys.exit(0)


def run_tray():
    if not HAS_TRAY or pystray is None:
        return

    icon = pystray.Icon(
        "FocusAudio",
        make_icon(True),
        "FocusAudio — ON",
        menu=pystray.Menu(
            pystray.MenuItem("Open Mixer", open_mixer, default=True),
            pystray.MenuItem("Toggle on/off", toggle),
            pystray.MenuItem("Quit (restore volumes)", quit_app),
        )
    )

    # Start the animation thread
    anim = threading.Thread(target=_animate_icon, args=(icon,), daemon=True)
    anim.start()

    icon.run()


# ── Auto Updater ─────────────────────────────────────────────────────────────

def run_update(download_url):
    try:
        temp_dir = tempfile.gettempdir()
        new_exe_path = os.path.join(temp_dir, "FocusAudio_update.exe")

        log.debug("Downloading update...")
        urllib.request.urlretrieve(download_url, new_exe_path)

        current_exe = sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__)

        if not getattr(sys, 'frozen', False):
            log.info("Not running as compiled exe, skipping auto-update overwrite.")
            return

        bat_path = os.path.join(temp_dir, "update_focusaudio.bat")

        bat_content = f"""@echo off
timeout /t 2 /nobreak > NUL
move /y "{new_exe_path}" "{current_exe}"
start "" "{current_exe}"
del "%~f0"
"""
        with open(bat_path, "w") as f:
            f.write(bat_content)

        subprocess.Popen([bat_path], shell=True, creationflags=subprocess.CREATE_NO_WINDOW)

        # Terminate current app to allow overwrite
        os._exit(0)
    except Exception as e:
        log.error(f"Failed to run update: {e}")


def check_for_updates():
    try:
        url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/latest"
        req = urllib.request.Request(url, headers={'User-Agent': 'FocusAudio-Updater'})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())

        latest_tag = data.get("tag_name", "").lstrip('v')
        assets = data.get("assets", [])

        download_url = None
        for asset in assets:
            if asset.get("name", "").lower() == "focusaudio.exe":
                download_url = asset.get("browser_download_url")
                break

        if latest_tag and latest_tag != VERSION and download_url:
            import ctypes
            MB_YESNO = 0x04
            MB_ICONQUESTION = 0x20
            MB_SYSTEMMODAL = 0x1000
            IDYES = 6
            result = ctypes.windll.user32.MessageBoxW(
                0,
                f"A new version of FocusAudio (v{latest_tag}) is available!\n\nWould you like to download and install it now?",
                "FocusAudio Update",
                MB_YESNO | MB_ICONQUESTION | MB_SYSTEMMODAL
            )
            if result == IDYES:
                run_update(download_url)

    except Exception as e:
        log.info(f"Update check failed: {e}")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.modules["focus_audio"] = sys.modules["__main__"]

    load_config()

    print("FocusAudio starting...")
    print(f"  Fade duration : {FADE_DURATION}s")
    print(f"  Poll interval : {POLL_INTERVAL}s")
    print(f"  Whitelist     : {WHITELIST or 'none'}")
    print(f"  Version       : {VERSION}")
    print("Running — switch windows to hear the fade effect.")
    print("Close this window or use the tray icon to quit.\n")

    # Start update checker in the background
    threading.Thread(target=check_for_updates, daemon=True).start()

    t = threading.Thread(target=monitor_loop, daemon=True)
    t.start()

    if HAS_TRAY:
        run_tray()
    else:
        print("(No tray icon — pystray not available. Press Ctrl+C to quit.)")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("Restoring volumes and quitting...")
            restore_all_volumes_and_resume()
