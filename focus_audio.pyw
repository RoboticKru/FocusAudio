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

# Debug log file — delete or set to False to disable
DEBUG_LOG = True
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
_lock = threading.Lock()
_refresh_requested = threading.Event()


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


def fade_session(session, session_key, target_vol, token, steps=FADE_STEPS, duration=FADE_DURATION):
    try:
        steps = max(1, int(steps))
        duration = max(0.0, float(duration))
        start_vol = get_session_volume(session)
        if abs(start_vol - target_vol) < 0.01:
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
    finally:
        with _lock:
            if token in _active_fades:
                _active_fades.remove(token)


def start_fade(session, session_key, target_vol):
    global _global_token_counter
    target_vol = max(0.0, min(1.0, float(target_vol)))

    with _lock:
        previous_target = _session_targets.get(session_key)
        if previous_target is not None and abs(previous_target - target_vol) < 0.01:
            return

        _session_targets[session_key] = target_vol
        _global_token_counter += 1
        token = _global_token_counter
        _fade_tokens[session_key] = token
        _active_fades.add(token)

    threading.Thread(
        target=fade_session,
        args=(session, session_key, target_vol, token),
        daemon=True,
    ).start()


def restore_all_volumes():
    for session in get_audio_sessions():
        try:
            pid = get_session_pid(session)
            session_key = get_session_key(session, pid)
            with _lock:
                saved = _session_volumes.get(session_key, RESTORE_VOLUME)
            set_session_volume(session, saved)
        except Exception:
            continue


def monitor_loop():
    # COM must be initialized on each thread that uses Windows audio APIs.
    import comtypes
    comtypes.CoInitialize()

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

        focused_single_key = None
        fg_is_active_audio = False

        if fg_name in SINGLE_FOCUS_APPS:
            focused_candidates = [
                info for info in session_infos if info["same_app_focus"]
            ]
            focused_single_key = choose_focused_session(focused_candidates, fg_name, fg_title)

            if focused_single_key is not None:
                chosen = next((info for info in focused_candidates if info["key"] == focused_single_key), None)
                fg_is_active_audio = bool(chosen and chosen["active"])
                if chosen:
                    peak = get_session_peak(chosen["session"])
                    log.debug(f"FOCUSED: {fg_name} | vol={chosen['volume']:.3f} | peak={peak:.5f} | state={chosen['active']} | fg_active={fg_is_active_audio}")
        else:
            fg_is_active_audio = any(info["active"] for info in session_infos if info["same_app_focus"])

        fallback_key = None
        if RESUME_BACKGROUND_WHEN_FOCUS_SILENT and not fg_is_active_audio:
            fallback_candidates = [
                info
                for info in session_infos
                if not info["same_app_focus"]
                and info["active"]
                and max(info["remembered"], info["volume"]) > 0.05
            ]
            fallback_key = choose_background_session(fallback_candidates)
            log.debug(f"FALLBACK: candidates={len(fallback_candidates)} | chosen={fallback_key is not None}")
        
        if fg_is_active_audio:
            log.debug(f"fg_active=True, no fallback needed")

        for info in session_infos:
            try:
                session = info["session"]
                session_key = info["key"]
                is_focused = info["same_app_focus"]

                if fg_name in SINGLE_FOCUS_APPS and info["name"] == fg_name:
                    is_focused = (
                        focused_single_key is not None
                        and session_key == focused_single_key
                    )

                if fallback_key is not None and session_key == fallback_key:
                    is_focused = True

                with _lock:
                    token = _fade_tokens.get(session_key)
                    is_fading = token in _active_fades

                if is_focused:
                    # Capture manual volume overrides during steady focus state
                    current = info["volume"]
                    if not is_fading and current > 0.05:
                        with _lock:
                            _session_volumes[session_key] = current

                    # Restore volume for focused process
                    with _lock:
                        saved = _session_volumes.get(session_key, RESTORE_VOLUME)
                    target = max(0.0, min(1.0, saved))
                    start_fade(session, session_key, target)
                else:
                    start_fade(session, session_key, 0.0)

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
        # Rounded bars via small circles on top and bottom
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


def toggle(icon, item):
    global enabled
    enabled = not enabled

    if not enabled:
        restore_all_volumes()

    _refresh_requested.set()
    icon.icon = make_icon(enabled)
    icon.title = f"FocusAudio — {'ON' if enabled else 'OFF'}"


def quit_app(icon, item):
    _icon_anim_stop.set()
    # Restore all volumes before quitting
    restore_all_volumes()
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
            pystray.MenuItem("Toggle on/off", toggle, default=True),
            pystray.MenuItem("Quit (restore volumes)", quit_app),
        )
    )

    # Start the animation thread
    anim = threading.Thread(target=_animate_icon, args=(icon,), daemon=True)
    anim.start()

    icon.run()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("FocusAudio starting...")
    print(f"  Fade duration : {FADE_DURATION}s")
    print(f"  Poll interval : {POLL_INTERVAL}s")
    print(f"  Whitelist     : {WHITELIST or 'none'}")
    print("Running — switch windows to hear the fade effect.")
    print("Close this window or use the tray icon to quit.\n")

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
            restore_all_volumes()
