"""
FocusAudio Mixer GUI — A sleek volume mixer popup for per-app audio control.
"""

import tkinter as tk
from tkinter import ttk
import threading
import io
import ctypes
import ctypes.wintypes

try:
    from PIL import Image, ImageTk, ImageDraw, ImageFilter
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

import focus_audio

# ── Theme ──────────────────────────────────────────────────────────────────────

COLORS = {
    "bg":              "#0f0f17",
    "bg_card":         "#17172200",   # used as logical card color; drawn on canvas
    "bg_card_solid":   "#1a1a28",
    "bg_hover":        "#232338",
    "accent":          "#6c5ce7",
    "accent_rgb":      (108, 92, 231),
    "accent_light":    "#a29bfe",
    "accent_glow":     "#6c5ce722",
    "text":            "#f0f0f5",
    "text_secondary":  "#9898ab",
    "text_muted":      "#6b6b80",
    "border":          "#252538",
    "slider_trough":   "#252538",
    "green":           "#4caf50",
    "green_dim":       "#2d6b30",
    "red":             "#ef5350",
    "playing_bg":      "#1e1b35",
}

FONT = "Segoe UI"

# ── Helpers ────────────────────────────────────────────────────────────────────

def _apply_dwm_style(hwnd):
    """Apply rounded corners + drop-shadow via DWM (Windows 11+)."""
    try:
        # DWMWA_WINDOW_CORNER_PREFERENCE = 33, DWMWCP_ROUND = 2
        attr = ctypes.c_int(2)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 33, ctypes.byref(attr), ctypes.sizeof(attr)
        )
    except Exception:
        pass
    try:
        # Enable NC rendering for drop-shadow
        attr2 = ctypes.c_int(2)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 2, ctypes.byref(attr2), ctypes.sizeof(attr2)
        )
    except Exception:
        pass


def create_rounded_image(image_bytes, size=(64, 64), radius=12):
    if not HAS_PIL:
        return None
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        img = img.resize(size, Image.LANCZOS)

        mask = Image.new("L", size, 0)
        draw = ImageDraw.Draw(mask)
        draw.rounded_rectangle((0, 0, size[0], size[1]), radius=radius, fill=255)

        output = Image.new("RGBA", size, (0, 0, 0, 0))
        output.paste(img, (0, 0), mask=mask)
        return ImageTk.PhotoImage(output)
    except Exception:
        return None


def _make_pill(text, bg_hex, fg_hex, width=None, height=22, radius=11, font_size=8):
    """Render a pill-shaped label as a PhotoImage."""
    if not HAS_PIL:
        return None
    try:
        pad_x = 10
        # measure text width roughly
        char_w = font_size - 1
        w = width or (len(text) * char_w + pad_x * 2 + 4)
        h = height
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        r, g, b = int(bg_hex[1:3], 16), int(bg_hex[3:5], 16), int(bg_hex[5:7], 16)
        draw.rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=(r, g, b, 220))
        return ImageTk.PhotoImage(img)
    except Exception:
        return None


def _lerp_color(c1_hex, c2_hex, t):
    r1, g1, b1 = int(c1_hex[1:3], 16), int(c1_hex[3:5], 16), int(c1_hex[5:7], 16)
    r2, g2, b2 = int(c2_hex[1:3], 16), int(c2_hex[3:5], 16), int(c2_hex[5:7], 16)
    r = int(r1 + (r2 - r1) * t)
    g = int(g1 + (g2 - g1) * t)
    b = int(b1 + (b2 - b1) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


# ── VolumeBar: custom canvas-based slider ─────────────────────────────────────

class VolumeBar(tk.Canvas):
    """A sleek custom volume bar that replaces the default tk.Scale."""

    TRACK_H = 4
    THUMB_R = 7
    HEIGHT   = 22

    def __init__(self, parent, initial=100, on_change=None, width=200, **kw):
        super().__init__(
            parent,
            width=width,
            height=self.HEIGHT,
            bg=COLORS["bg_card_solid"],
            highlightthickness=0,
            bd=0,
            cursor="hand2",
            **kw,
        )
        self._value = max(0, min(100, initial))
        self._on_change = on_change
        self._width = width
        self._dragging = False
        self._hover = False

        self.bind("<Button-1>",        self._on_click)
        self.bind("<B1-Motion>",       self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Enter>",           lambda e: self._set_hover(True))
        self.bind("<Leave>",           lambda e: self._set_hover(False))
        self.bind("<MouseWheel>",      self._on_wheel)

        self._draw()

    def _set_hover(self, v):
        self._hover = v
        self._draw()

    def _x_from_value(self):
        return self.THUMB_R + int((self._value / 100) * (self._width - self.THUMB_R * 2))

    def _value_from_x(self, x):
        usable = self._width - self.THUMB_R * 2
        return max(0, min(100, int((x - self.THUMB_R) / usable * 100)))

    def _draw(self):
        self.delete("all")
        cy = self.HEIGHT // 2
        r  = self.THUMB_R
        tx = self._x_from_value()

        # Track background
        self.create_rounded_rect(
            self.THUMB_R, cy - self.TRACK_H // 2,
            self._width - self.THUMB_R, cy + self.TRACK_H // 2,
            radius=self.TRACK_H // 2,
            fill=COLORS["slider_trough"],
        )
        # Filled portion
        if tx > self.THUMB_R:
            self.create_rounded_rect(
                self.THUMB_R, cy - self.TRACK_H // 2,
                tx, cy + self.TRACK_H // 2,
                radius=self.TRACK_H // 2,
                fill=COLORS["accent"],
            )

        # Thumb
        thumb_r = r + 1 if (self._hover or self._dragging) else r
        self.create_oval(
            tx - thumb_r, cy - thumb_r,
            tx + thumb_r, cy + thumb_r,
            fill=COLORS["accent_light"] if (self._hover or self._dragging) else COLORS["accent"],
            outline="",
        )

    def create_rounded_rect(self, x1, y1, x2, y2, radius=4, **kw):
        x2 = max(x1 + radius * 2, x2)
        pts = [
            x1 + radius, y1,
            x2 - radius, y1,
            x2, y1,
            x2, y1 + radius,
            x2, y2 - radius,
            x2, y2,
            x2 - radius, y2,
            x1 + radius, y2,
            x1, y2,
            x1, y2 - radius,
            x1, y1 + radius,
            x1, y1,
        ]
        return self.create_polygon(pts, smooth=True, **kw)

    def _on_click(self, e):
        self._dragging = True
        self._set_value(self._value_from_x(e.x))

    def _on_drag(self, e):
        if self._dragging:
            self._set_value(self._value_from_x(e.x))

    def _on_release(self, e):
        self._dragging = False
        self._draw()

    def _on_wheel(self, e):
        delta = 5 if e.delta > 0 else -5
        self._set_value(max(0, min(100, self._value + delta)))

    def _set_value(self, v):
        if v != self._value:
            self._value = v
            self._draw()
            if self._on_change:
                self._on_change(v)

    def set(self, v):
        self._value = max(0, min(100, int(v)))
        self._draw()

    def get(self):
        return self._value


# ── MixerWindow ────────────────────────────────────────────────────────────────

class MixerWindow:
    """Dark-themed popup mixer window with per-app volume controls."""

    WIN_W = 380
    WIN_H = 580

    def __init__(self):
        self._root = None
        self._open = False
        self._app_rows = {}
        self._refresh_job = None
        self._thread = None

        self._media_frame = None
        self._album_art_label = None
        self._song_title_label = None
        self._artist_label = None
        self._play_btn = None
        self._current_thumb_bytes = None
        self._tk_thumb = None

    def is_open(self):
        return self._open

    def show(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def close(self):
        if self._root and self._open:
            try:
                self._root.after(0, self._root.destroy)
            except Exception:
                pass

    def _run(self):
        self._root = tk.Tk()
        self._root.title("FocusAudio")
        self._root.configure(bg=COLORS["bg"])
        self._root.resizable(False, False)
        self._root.attributes("-topmost", True)
        self._root.overrideredirect(True)

        self._open = True

        # Position near system tray (bottom-right)
        screen_w = self._root.winfo_screenwidth()
        screen_h = self._root.winfo_screenheight()
        x = screen_w - self.WIN_W - 16
        y = screen_h - self.WIN_H - 60
        self._root.geometry(f"{self.WIN_W}x{self.WIN_H}+{x}+{y}")

        # Apply DWM rounded corners + shadow (Windows 11+)
        self._root.update_idletasks()
        try:
            hwnd = ctypes.windll.user32.GetParent(self._root.winfo_id())
            if not hwnd:
                hwnd = self._root.winfo_id()
            _apply_dwm_style(hwnd)
        except Exception:
            pass

        self._root.bind("<Escape>", lambda e: self.close())
        self._root.bind("<FocusOut>", self._on_focus_out)

        self._build_ui()
        self._refresh_sessions()

        self._root.protocol("WM_DELETE_WINDOW", self.close)
        try:
            self._root.mainloop()
        except Exception:
            pass
        finally:
            self._open = False

    def _on_focus_out(self, event):
        """Close when the root window itself loses focus (not a child widget)."""
        if event.widget is not self._root:
            return
        self._root.after(150, self._check_focus)

    def _check_focus(self):
        try:
            focused = self._root.focus_get()
            # If focus is None or belongs to a widget outside our window, close
            if focused is None:
                self.close()
        except Exception:
            pass

    # ── Build UI ───────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = self._root

        # ── Outer border frame (simulates rounded card on Windows < 11) ──
        outer = tk.Frame(root, bg=COLORS["border"], padx=1, pady=1)
        outer.pack(fill="both", expand=True)

        inner = tk.Frame(outer, bg=COLORS["bg"])
        inner.pack(fill="both", expand=True)

        self._inner = inner

        # ── Title bar ──
        self._build_titlebar(inner)

        # Thin accent line under title
        accent_line = tk.Canvas(inner, height=2, bg=COLORS["bg"], highlightthickness=0, bd=0)
        accent_line.pack(fill="x")
        accent_line.bind("<Configure>", lambda e: self._draw_accent_line(accent_line))

        # ── Media card ──
        self._build_media_card(inner)

        # ── Footer (packed BEFORE session area so it is always visible) ──
        self._build_footer(inner)

        # ── Scrollable session area (takes remaining space) ──
        self._build_session_area(inner)

    def _draw_accent_line(self, canvas):
        w = canvas.winfo_width()
        if w < 2:
            return
        canvas.delete("all")
        # Gradient from accent to transparent
        steps = max(1, w)
        for i in range(steps):
            t = i / steps
            alpha = int(255 * (1 - t * 0.6))
            r, g, b = COLORS["accent_rgb"]
            color = f"#{r:02x}{g:02x}{b:02x}"
            canvas.create_line(i, 0, i, 2, fill=color)

    def _build_titlebar(self, parent):
        bar = tk.Frame(parent, bg=COLORS["bg"], pady=14)
        bar.pack(fill="x", padx=16)

        # Drag support
        bar.bind("<Button-1>",  self._start_drag)
        bar.bind("<B1-Motion>", self._do_drag)

        # Logo + title
        logo = tk.Label(bar, text="🎧", font=(FONT, 15), bg=COLORS["bg"], fg=COLORS["text"])
        logo.pack(side="left")
        logo.bind("<Button-1>",  self._start_drag)
        logo.bind("<B1-Motion>", self._do_drag)

        title_lbl = tk.Label(
            bar, text="FocusAudio",
            font=(FONT, 13, "bold"), bg=COLORS["bg"], fg=COLORS["text"]
        )
        title_lbl.pack(side="left", padx=(6, 0))
        title_lbl.bind("<Button-1>",  self._start_drag)
        title_lbl.bind("<B1-Motion>", self._do_drag)

        # Close button
        close_btn = tk.Label(
            bar, text="✕", font=(FONT, 11),
            bg=COLORS["bg"], fg=COLORS["text_muted"],
            cursor="hand2", padx=4
        )
        close_btn.pack(side="right")
        close_btn.bind("<Enter>",    lambda e: close_btn.config(fg=COLORS["red"]))
        close_btn.bind("<Leave>",    lambda e: close_btn.config(fg=COLORS["text_muted"]))
        close_btn.bind("<Button-1>", lambda e: self.close())

        # Toggle switch
        self._toggle_canvas = tk.Canvas(
            bar, width=46, height=24,
            bg=COLORS["bg"], highlightthickness=0, bd=0, cursor="hand2",
        )
        self._toggle_canvas.pack(side="right", padx=(0, 10))
        self._toggle_canvas.bind("<Button-1>", lambda e: self._toggle())
        self._draw_toggle(focus_audio.enabled)

        # Version label
        ver_lbl = tk.Label(
            bar, text=f"v{focus_audio.VERSION}",
            font=(FONT, 8), bg=COLORS["bg"], fg=COLORS["text_muted"]
        )
        ver_lbl.pack(side="right", padx=(0, 8))

    def _start_drag(self, e):
        self._drag_x = e.x_root - self._root.winfo_x()
        self._drag_y = e.y_root - self._root.winfo_y()

    def _do_drag(self, e):
        try:
            x = e.x_root - self._drag_x
            y = e.y_root - self._drag_y
            self._root.geometry(f"+{x}+{y}")
        except Exception:
            pass

    def _build_media_card(self, parent):
        wrap = tk.Frame(parent, bg=COLORS["bg"], padx=12, pady=0)
        wrap.pack(fill="x")

        card = tk.Frame(wrap, bg="#1c1c2c", pady=12, padx=14)
        card.pack(fill="x")

        # Thin left accent bar on media card
        accent = tk.Frame(card, bg=COLORS["accent"], width=3)
        accent.pack(side="left", fill="y", padx=(0, 12))

        self._album_art_label = tk.Label(card, bg="#1c1c2c", width=0)
        self._album_art_label.pack(side="left")

        info_col = tk.Frame(card, bg="#1c1c2c")
        info_col.pack(side="left", fill="both", expand=True)

        self._song_title_label = tk.Label(
            info_col, text="No media playing",
            font=(FONT, 10, "bold"), bg="#1c1c2c", fg=COLORS["text"],
            anchor="w", wraplength=200, justify="left"
        )
        self._song_title_label.pack(fill="x")

        self._artist_label = tk.Label(
            info_col, text="—",
            font=(FONT, 9), bg="#1c1c2c", fg=COLORS["text_secondary"],
            anchor="w"
        )
        self._artist_label.pack(fill="x")

        ctrl = tk.Frame(info_col, bg="#1c1c2c")
        ctrl.pack(fill="x", pady=(6, 0))

        btn_kw = dict(
            bg="#1c1c2c", fg=COLORS["text_secondary"],
            relief="flat", cursor="hand2", bd=0,
            font=(FONT, 13),
            activebackground="#252540",
            activeforeground=COLORS["accent_light"],
        )
        prev_btn = tk.Button(ctrl, text="⏮", command=focus_audio.media_prev, **btn_kw)
        prev_btn.pack(side="left")
        self._play_btn = tk.Button(ctrl, text="▶", command=focus_audio.media_play_pause, **btn_kw)
        self._play_btn.pack(side="left", padx=4)
        next_btn = tk.Button(ctrl, text="⏭", command=focus_audio.media_next, **btn_kw)
        next_btn.pack(side="left")

        self._media_frame = wrap

    def _build_session_area(self, parent):
        spacer = tk.Frame(parent, bg=COLORS["bg"], height=8)
        spacer.pack(fill="x")

        container = tk.Frame(parent, bg=COLORS["bg"])
        container.pack(fill="both", expand=True)

        self._canvas = tk.Canvas(
            container, bg=COLORS["bg"], highlightthickness=0, bd=0
        )
        scrollbar = tk.Scrollbar(
            container, orient="vertical", command=self._canvas.yview,
            bg=COLORS["bg"], troughcolor=COLORS["bg"],
        )
        self._session_frame = tk.Frame(self._canvas, bg=COLORS["bg"])

        self._session_frame.bind(
            "<Configure>",
            lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all"))
        )

        self._canvas.create_window(
            (0, 0), window=self._session_frame, anchor="nw", width=self.WIN_W - 4
        )
        self._canvas.configure(yscrollcommand=scrollbar.set)

        self._canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self._canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _on_mousewheel(self, e):
        try:
            self._canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        except Exception:
            pass

    def _build_footer(self, parent):
        sep = tk.Frame(parent, bg=COLORS["border"], height=1)
        sep.pack(fill="x", padx=12, pady=(4, 0))

        footer = tk.Frame(parent, bg=COLORS["bg"], padx=16, pady=10)
        footer.pack(fill="x")

        # Pause background toggle
        pause_val = focus_audio.get_pause_background()
        self._pause_var = tk.BooleanVar(value=pause_val)

        pause_row = tk.Frame(footer, bg=COLORS["bg"])
        pause_row.pack(fill="x", pady=(0, 8))

        def _on_pause_toggle():
            new_val = not focus_audio.get_pause_background()
            focus_audio.set_pause_background(new_val)
            self._pause_var.set(new_val)
            _update_pause_btn(new_val)

        def _update_pause_btn(val):
            icon = "■" if val else "□"
            color = COLORS["accent_light"] if val else COLORS["text_muted"]
            pause_icon_lbl.config(text=icon, fg=color)

        pause_icon_lbl = tk.Label(
            pause_row, text="■" if pause_val else "□",
            font=(FONT, 10), bg=COLORS["bg"],
            fg=COLORS["accent_light"] if pause_val else COLORS["text_muted"],
            cursor="hand2"
        )
        pause_icon_lbl.pack(side="left")
        pause_icon_lbl.bind("<Button-1>", lambda e: _on_pause_toggle())

        pause_text = tk.Label(
            pause_row, text="  Pause background apps",
            font=(FONT, 9), bg=COLORS["bg"], fg=COLORS["text_secondary"],
            cursor="hand2", anchor="w"
        )
        pause_text.pack(side="left")
        pause_text.bind("<Button-1>", lambda e: _on_pause_toggle())

        # ── Pause after fade toggle ──
        paf_val = focus_audio.get_pause_after_fade()

        paf_row = tk.Frame(footer, bg=COLORS["bg"])
        paf_row.pack(fill="x", pady=(0, 8))

        def _on_paf_toggle():
            new_val = not focus_audio.get_pause_after_fade()
            focus_audio.set_pause_after_fade(new_val)
            icon = "■" if new_val else "□"
            color = COLORS["accent_light"] if new_val else COLORS["text_muted"]
            paf_icon_lbl.config(text=icon, fg=color)

        paf_icon_lbl = tk.Label(
            paf_row, text="■" if paf_val else "□",
            font=(FONT, 10), bg=COLORS["bg"],
            fg=COLORS["accent_light"] if paf_val else COLORS["text_muted"],
            cursor="hand2"
        )
        paf_icon_lbl.pack(side="left")
        paf_icon_lbl.bind("<Button-1>", lambda e: _on_paf_toggle())

        paf_text = tk.Label(
            paf_row, text="  Fade then pause (no skipping)",
            font=(FONT, 9), bg=COLORS["bg"], fg=COLORS["text_secondary"],
            cursor="hand2", anchor="w"
        )
        paf_text.pack(side="left")
        paf_text.bind("<Button-1>", lambda e: _on_paf_toggle())

        # ── Ducking volume row ──
        duck_row = tk.Frame(footer, bg=COLORS["bg"])
        duck_row.pack(fill="x")

        tk.Label(
            duck_row, text="Duck volume",
            font=(FONT, 9), bg=COLORS["bg"], fg=COLORS["text_muted"],
        ).pack(side="left")

        self._duck_label = tk.Label(
            duck_row, text=f"{int(focus_audio.get_global_ducking() * 100)}%",
            font=(FONT, 9, "bold"), bg=COLORS["bg"], fg=COLORS["accent_light"], width=5, anchor="e"
        )
        self._duck_label.pack(side="right")

        duck_init = int(focus_audio.get_global_ducking() * 100)
        self._duck_bar = VolumeBar(
            footer,
            initial=duck_init,
            width=self.WIN_W - 48,
            on_change=self._on_duck_change,
        )
        self._duck_bar.pack(fill="x", pady=(4, 0))

    # ── Toggle ─────────────────────────────────────────────────────────────────

    def _draw_toggle(self, is_on):
        from PIL import Image, ImageDraw, ImageTk

        w, h = 46, 24
        scale = 4
        sw, sh = w * scale, h * scale
        pad = 3 * scale
        r = sh // 2

        img = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        if is_on:
            draw.rounded_rectangle([0, 0, sw - 1, sh - 1], radius=r, fill=(108, 92, 231))
            knob_x = sw - sh + pad
            draw.ellipse([knob_x, pad, sw - pad, sh - pad], fill=(255, 255, 255))
        else:
            draw.rounded_rectangle([0, 0, sw - 1, sh - 1], radius=r, fill=(52, 52, 68))
            draw.ellipse([pad, pad, sh - pad, sh - pad], fill=(110, 110, 130))

        img = img.resize((w, h), Image.LANCZOS)
        self._toggle_photo = ImageTk.PhotoImage(img)

        c = self._toggle_canvas
        c.delete("all")
        c.create_image(0, 0, anchor="nw", image=self._toggle_photo)

    def _toggle(self):
        focus_audio.enabled = not focus_audio.enabled
        if not focus_audio.enabled:
            focus_audio.restore_all_volumes_and_resume()
        focus_audio._refresh_requested.set()
        self._draw_toggle(focus_audio.enabled)

    # ── Refresh ────────────────────────────────────────────────────────────────

    def _refresh_sessions(self):
        if not self._open:
            return

        try:
            sessions = focus_audio.get_current_sessions()

            apps = {}
            for s in sessions:
                name = s["name"]
                if name not in apps or s["focused"]:
                    apps[name] = s

            for name in list(self._app_rows.keys()):
                if name not in apps:
                    self._app_rows[name]["frame"].destroy()
                    del self._app_rows[name]

            for name, info in apps.items():
                if name not in self._app_rows:
                    self._create_app_row(name, info)
                else:
                    self._update_app_row(name, info)

        except Exception:
            pass

        # Refresh media flyout.
        # SMTC call is isolated so a timeout/exception still lets the fallback run.
        media_info = None
        try:
            media_info = focus_audio.get_current_media_info()
        except Exception:
            pass

        try:
            if media_info and media_info.get("title"):
                # ── SMTC gave us proper track info (Spotify, Windows Media Player, etc.) ──
                self._song_title_label.config(text=media_info["title"])
                artist = media_info.get("artist") or media_info.get("app") or "Unknown"
                self._artist_label.config(text=artist)

                is_playing_now = media_info.get("status") == 4
                self._play_btn.config(text="⏸" if is_playing_now else "▶")

                thumb = media_info.get("thumbnail_bytes")
                if thumb and thumb != self._current_thumb_bytes:
                    self._current_thumb_bytes = thumb
                    new_img = create_rounded_image(thumb, size=(52, 52), radius=8)
                    if new_img:
                        self._tk_thumb = new_img
                        self._album_art_label.config(image=self._tk_thumb, width=52)
            else:
                # ── Fallback: show whichever audio session is currently active ──
                # Covers Chrome, SimplyMusic, and any app not registered with SMTC.
                fallback = focus_audio.get_active_session_fallback()
                if fallback:
                    self._song_title_label.config(text=fallback["title"])
                    self._artist_label.config(text=fallback["artist"])
                    self._play_btn.config(text="▶")
                    self._album_art_label.config(image="", width=0)
                    self._current_thumb_bytes = None
                else:
                    self._song_title_label.config(text="No media playing")
                    self._artist_label.config(text="—")
                    self._play_btn.config(text="▶")
                    self._album_art_label.config(image="", width=0)
                    self._current_thumb_bytes = None
        except Exception:
            pass

        try:
            self._refresh_job = self._root.after(1000, self._refresh_sessions)
        except Exception:
            pass

    # ── App rows ───────────────────────────────────────────────────────────────

    def _create_app_row(self, app_name, info):
        cfg = focus_audio.get_app_config(app_name)

        # Card frame
        outer = tk.Frame(self._session_frame, bg=COLORS["bg"], pady=2)
        outer.pack(fill="x", padx=10)

        card = tk.Frame(outer, bg=COLORS["bg_card_solid"], pady=10, padx=0)
        card.pack(fill="x")

        # Left accent stripe (colored based on role)
        stripe = tk.Frame(card, width=3, bg=self._role_color(cfg["role"]))
        stripe.pack(side="left", fill="y", padx=(10, 10))

        content = tk.Frame(card, bg=COLORS["bg_card_solid"])
        content.pack(side="left", fill="both", expand=True)

        # ── Header row ──
        header = tk.Frame(content, bg=COLORS["bg_card_solid"])
        header.pack(fill="x", padx=(0, 10))

        # App name
        name_lbl = tk.Label(
            header,
            text=app_name.replace("_", " ").title(),
            font=(FONT, 10, "bold"),
            bg=COLORS["bg_card_solid"],
            fg=COLORS["text"],
            anchor="w",
        )
        name_lbl.pack(side="left")

        # Status badge
        status_lbl = tk.Label(
            header, text="",
            font=(FONT, 8),
            bg=COLORS["bg_card_solid"],
            fg=COLORS["accent_light"],
            anchor="w",
        )
        status_lbl.pack(side="left", padx=(6, 0))

        # Role button (pill style)
        role_btn = tk.Label(
            header,
            text=cfg["role"].upper(),
            font=(FONT, 8, "bold"),
            bg=self._role_bg(cfg["role"]),
            fg=self._role_fg(cfg["role"]),
            cursor="hand2",
            padx=8, pady=2,
        )
        role_btn.pack(side="right")
        role_btn.bind("<Button-1>", lambda e, n=app_name, b=role_btn, s=stripe: self._cycle_role(n, b, s))

        # ── Volume row ──
        vol_row = tk.Frame(content, bg=COLORS["bg_card_solid"])
        vol_row.pack(fill="x", padx=(0, 10), pady=(8, 0))

        init_vol = int(cfg["vol"] * 100)
        vol_label = tk.Label(
            vol_row,
            text=f"{init_vol}%",
            font=(FONT, 9, "bold"),
            bg=COLORS["bg_card_solid"],
            fg=COLORS["text_muted"],
            width=4, anchor="e",
        )
        vol_label.pack(side="right")

        def _on_vol(v, n=app_name, lbl=vol_label):
            focus_audio.set_app_config(n, vol=v / 100.0)
            lbl.config(text=f"{v}%")
            if n in self._app_rows:
                self._app_rows[n]["vol_label"].config(text=f"{v}%")

        vol_bar = VolumeBar(
            vol_row,
            initial=init_vol,
            width=self.WIN_W - 100,
            on_change=_on_vol,
        )
        vol_bar.pack(side="left", fill="x", expand=True)

        self._app_rows[app_name] = {
            "frame": outer,
            "card": card,
            "stripe": stripe,
            "status_label": status_lbl,
            "role_btn": role_btn,
            "vol_label": vol_label,
            "vol_bar": vol_bar,
        }

        self._update_app_row(app_name, info)

    def _update_app_row(self, app_name, info):
        row = self._app_rows[app_name]

        if info.get("active"):
            row["status_label"].config(text="♫ playing", fg=COLORS["accent_light"])
            row["card"].config(bg="#1a1a2c")
        else:
            row["status_label"].config(text="", fg=COLORS["text_muted"])
            row["card"].config(bg=COLORS["bg_card_solid"])

    def _cycle_role(self, app_name, btn, stripe):
        roles   = ["AUTO", "MAIN", "BACKGROUND", "IGNORE"]
        current = btn.cget("text")
        try:
            idx = (roles.index(current) + 1) % len(roles)
        except ValueError:
            idx = 0
        new_role = roles[idx]
        btn.config(
            text=new_role,
            bg=self._role_bg(new_role.lower()),
            fg=self._role_fg(new_role.lower()),
        )
        stripe.config(bg=self._role_color(new_role.lower()))
        focus_audio.set_app_config(app_name, role=new_role.lower())

    @staticmethod
    def _role_color(role):
        return {
            "main":       "#6c5ce7",
            "background": "#00b894",
            "ignore":     "#636e72",
            "auto":       "#4a4a6a",
        }.get(role, "#4a4a6a")

    @staticmethod
    def _role_bg(role):
        return {
            "main":       "#2d2550",
            "background": "#1a3a30",
            "ignore":     "#2a2a2a",
            "auto":       "#252535",
        }.get(str(role).lower(), "#252535")

    @staticmethod
    def _role_fg(role):
        return {
            "main":       "#a29bfe",
            "background": "#55efc4",
            "ignore":     "#888",
            "auto":       "#9898ab",
        }.get(str(role).lower(), "#9898ab")

    def _on_vol_change(self, app_name, value):
        focus_audio.set_app_config(app_name, vol=value / 100.0)
        if app_name in self._app_rows:
            self._app_rows[app_name]["vol_label"].config(text=f"{value}%")

    def _on_duck_change(self, value):
        focus_audio.set_global_ducking(value / 100.0)
        if hasattr(self, "_duck_label"):
            self._duck_label.config(text=f"{value}%")
