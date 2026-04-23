"""
FocusAudio Mixer GUI — A sleek volume mixer popup for per-app audio control.
"""

import tkinter as tk
from tkinter import ttk
import threading

# Import the core module for config and session data
import focus_audio


# ── Theme ─────────────────────────────────────────────────────────────────────

COLORS = {
    "bg": "#0f0f17",
    "bg_card": "#1a1a28",
    "bg_hover": "#222235",
    "accent": "#6c5ce7",
    "accent_light": "#a29bfe",
    "text": "#f0f0f5",
    "text_secondary": "#9898ab",
    "text_muted": "#6b6b80",
    "border": "#2a2a3d",
    "slider_trough": "#2a2a3d",
    "green": "#4caf50",
    "red": "#ef5350",
}

FONT_FAMILY = "Segoe UI"


class MixerWindow:
    """Dark-themed popup mixer window with per-app volume controls."""

    def __init__(self):
        self._root = None
        self._open = False
        self._app_rows = {}
        self._refresh_job = None
        self._thread = None

    def is_open(self):
        return self._open

    def show(self):
        """Launch the mixer window in its own thread."""
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
        self._root.title("FocusAudio Mixer")
        self._root.configure(bg=COLORS["bg"])
        self._root.resizable(False, False)
        self._root.attributes("-topmost", True)
        self._root.overrideredirect(True)  # borderless

        self._open = True

        # Position near system tray (bottom-right of screen)
        screen_w = self._root.winfo_screenwidth()
        screen_h = self._root.winfo_screenheight()
        win_w, win_h = 380, 500
        x = screen_w - win_w - 16
        y = screen_h - win_h - 60
        self._root.geometry(f"{win_w}x{win_h}+{x}+{y}")

        # Close on Escape or focus loss
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
        """Close when clicking outside the window."""
        if event.widget == self._root:
            # Small delay to avoid closing when interacting with sliders
            self._root.after(200, self._check_focus)

    def _check_focus(self):
        try:
            if not self._root.focus_get():
                self.close()
        except Exception:
            pass

    def _build_ui(self):
        root = self._root

        # ── Title bar ──
        title_frame = tk.Frame(root, bg=COLORS["bg"], padx=16, pady=12)
        title_frame.pack(fill="x")

        tk.Label(
            title_frame, text="🎧", font=(FONT_FAMILY, 16),
            bg=COLORS["bg"], fg=COLORS["text"]
        ).pack(side="left")

        tk.Label(
            title_frame, text=" FocusAudio", font=(FONT_FAMILY, 14, "bold"),
            bg=COLORS["bg"], fg=COLORS["text"]
        ).pack(side="left", padx=(4, 0))

        # Close button
        close_btn = tk.Button(
            title_frame, text="✕", font=(FONT_FAMILY, 12),
            bg=COLORS["bg"], fg=COLORS["text_muted"], relief="flat",
            cursor="hand2", command=self.close,
            activebackground=COLORS["bg_card"],
            activeforeground=COLORS["red"],
            bd=0,
        )
        close_btn.pack(side="right")

        # Toggle switch (iOS-style)
        self._toggle_canvas = tk.Canvas(
            title_frame, width=52, height=28,
            bg=COLORS["bg"], highlightthickness=0, bd=0,
        )
        self._toggle_canvas.pack(side="right", padx=(0, 12))
        self._toggle_canvas.bind("<Button-1>", lambda e: self._toggle())
        self._toggle_canvas.configure(cursor="hand2")
        self._draw_toggle(focus_audio.enabled)

        # Divider
        tk.Frame(root, bg=COLORS["border"], height=1).pack(fill="x")

        # ── Global Ducking Footer ──
        footer = tk.Frame(root, bg=COLORS["bg"], padx=16, pady=8)
        footer.pack(fill="x", side="bottom")

        tk.Frame(footer, bg=COLORS["border"], height=1).pack(fill="x", pady=(0, 8))

        tk.Label(
            footer, text="Background Ducking Volume",
            font=(FONT_FAMILY, 10, "bold"), bg=COLORS["bg"], fg=COLORS["text"]
        ).pack(anchor="w")

        ducking_val = int(focus_audio.get_global_ducking() * 100)
        duck_frame = tk.Frame(footer, bg=COLORS["bg"])
        duck_frame.pack(fill="x", pady=(4, 0))
        
        duck_slider = tk.Scale(
            duck_frame, from_=0, to=100, orient="horizontal",
            showvalue=False,
            bg=COLORS["bg"], fg=COLORS["text_secondary"],
            troughcolor=COLORS["slider_trough"],
            highlightthickness=0, bd=0,
            activebackground=COLORS["accent"],
            sliderrelief="flat", length=290,
            command=lambda val: self._on_duck_change(int(float(val)))
        )
        duck_slider.set(ducking_val)
        duck_slider.pack(side="left")

        self._duck_label = tk.Label(
            duck_frame, text=f"{ducking_val}%",
            font=(FONT_FAMILY, 9), bg=COLORS["bg"], fg=COLORS["text_muted"], width=4
        )
        self._duck_label.pack(side="right")

        # ── Scrollable session area ──
        container = tk.Frame(root, bg=COLORS["bg"])
        container.pack(fill="both", expand=True)

        canvas = tk.Canvas(
            container, bg=COLORS["bg"], highlightthickness=0, bd=0
        )
        scrollbar = tk.Scrollbar(
            container, orient="vertical", command=canvas.yview,
            bg=COLORS["bg"], troughcolor=COLORS["bg"],
        )
        self._session_frame = tk.Frame(canvas, bg=COLORS["bg"])

        self._session_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=self._session_frame, anchor="nw", width=362)
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True, padx=(8, 0))
        scrollbar.pack(side="right", fill="y")

        # Mouse wheel scrolling
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind_all("<MouseWheel>", _on_mousewheel)

    def _draw_toggle(self, is_on):
        """Draw an anti-aliased iOS-style toggle using Pillow supersampling."""
        from PIL import Image, ImageDraw, ImageTk

        w, h = 52, 28
        scale = 4  # supersample for smooth edges
        sw, sh = w * scale, h * scale
        pad = 3 * scale
        r = sh // 2

        img = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        if is_on:
            bg_color = (108, 92, 231)     # accent purple
            knob_color = (255, 255, 255)
            # Pill
            draw.rounded_rectangle([0, 0, sw - 1, sh - 1], radius=r, fill=bg_color)
            # Knob on the right
            knob_x = sw - sh + pad
            draw.ellipse([knob_x, pad, sw - pad, sh - pad], fill=knob_color)
        else:
            bg_color = (58, 58, 77)       # dark grey
            knob_color = (138, 138, 157)
            # Pill
            draw.rounded_rectangle([0, 0, sw - 1, sh - 1], radius=r, fill=bg_color)
            # Knob on the left
            draw.ellipse([pad, pad, sh - pad, sh - pad], fill=knob_color)

        # Downsample with anti-aliasing
        img = img.resize((w, h), Image.LANCZOS)

        # Keep a reference so tkinter doesn't garbage collect it
        self._toggle_photo = ImageTk.PhotoImage(img)

        c = self._toggle_canvas
        c.delete("all")
        c.create_image(0, 0, anchor="nw", image=self._toggle_photo)

    def _toggle(self):
        focus_audio.enabled = not focus_audio.enabled
        if not focus_audio.enabled:
            focus_audio.restore_all_volumes()
        focus_audio._refresh_requested.set()
        self._draw_toggle(focus_audio.enabled)

    def _refresh_sessions(self):
        """Refresh the session list every 2 seconds."""
        if not self._open:
            return

        try:
            sessions = focus_audio.get_current_sessions()

            # Deduplicate by app name — keep the most relevant session
            apps = {}
            for s in sessions:
                name = s["name"]
                if name not in apps or s["focused"]:
                    apps[name] = s

            # Remove rows for apps that no longer exist
            for name in list(self._app_rows.keys()):
                if name not in apps:
                    self._app_rows[name]["frame"].destroy()
                    del self._app_rows[name]

            # Add or update rows
            for name, info in apps.items():
                if name not in self._app_rows:
                    self._create_app_row(name, info)
                else:
                    self._update_app_row(name, info)
                    
            self._root.title(f"FocusAudio Mixer ({len(apps)} apps)")
        except Exception as e:
            import traceback
            with open("mixer_error.log", "w") as f:
                f.write(traceback.format_exc())

        # Schedule next refresh
        try:
            self._refresh_job = self._root.after(2000, self._refresh_sessions)
        except Exception:
            pass

    def _create_app_row(self, app_name, info):
        """Create a row for an audio app."""
        cfg = focus_audio.get_app_config(app_name)

        frame = tk.Frame(self._session_frame, bg=COLORS["bg_card"], padx=14, pady=12)
        frame.pack(fill="x", padx=8, pady=4)

        # App name + status
        header = tk.Frame(frame, bg=COLORS["bg_card"])
        header.pack(fill="x")

        name_label = tk.Label(
            header, text=app_name.title(),
            font=(FONT_FAMILY, 11, "bold"),
            bg=COLORS["bg_card"], fg=COLORS["text"],
        )
        name_label.pack(side="left")

        status_label = tk.Label(
            header, text="",
            font=(FONT_FAMILY, 9),
            bg=COLORS["bg_card"], fg=COLORS["accent_light"],
        )
        status_label.pack(side="left", padx=(8, 0))

        # Role cycling button
        role_btn = tk.Button(
            header, text=cfg["role"].upper(),
            font=(FONT_FAMILY, 9, "bold"),
            bg=COLORS["bg_hover"], fg=COLORS["text_secondary"],
            relief="flat", cursor="hand2", padx=8, pady=2,
        )
        role_btn.config(command=lambda n=app_name, btn=role_btn: self._cycle_role(n, btn))
        role_btn.pack(side="right")

        # Volume slider
        f_frame = tk.Frame(frame, bg=COLORS["bg_card"])
        f_frame.pack(fill="x", pady=(10, 2))

        tk.Label(
            f_frame, text="Volume",
            font=(FONT_FAMILY, 9), bg=COLORS["bg_card"], fg=COLORS["text_secondary"],
            width=6, anchor="w",
        ).pack(side="left")

        vol_slider = tk.Scale(
            f_frame, from_=0, to=100, orient="horizontal",
            showvalue=False,
            bg=COLORS["bg_card"], fg=COLORS["accent"],
            troughcolor=COLORS["slider_trough"],
            highlightthickness=0, bd=0,
            activebackground=COLORS["accent"],
            sliderrelief="flat", length=210,
            command=lambda val, n=app_name: self._on_vol_change(n, int(float(val))),
        )
        vol_slider.set(int(cfg["vol"] * 100))
        vol_slider.pack(side="left", padx=(4, 4))

        vol_label = tk.Label(
            f_frame, text=f"{int(cfg['vol'] * 100)}%",
            font=(FONT_FAMILY, 9, "bold"),
            bg=COLORS["bg_card"], fg=COLORS["text"], width=4,
        )
        vol_label.pack(side="left")

        self._app_rows[app_name] = {
            "frame": frame,
            "status_label": status_label,
            "role_btn": role_btn,
            "vol_label": vol_label,
        }

        self._update_app_row(app_name, info)

    def _update_app_row(self, app_name, info):
        """Update status indicators for an app row."""
        row = self._app_rows[app_name]

        if info["active"]:
            row["status_label"].config(text="♫ PLAYING", fg=COLORS["accent_light"])
        else:
            row["status_label"].config(text="", fg=COLORS["text_muted"])

    def _cycle_role(self, app_name, btn):
        roles = ["AUTO", "MAIN", "BACKGROUND", "IGNORE"]
        current = btn.cget("text")
        try:
            idx = (roles.index(current) + 1) % len(roles)
        except ValueError:
            idx = 0
        new_role = roles[idx]
        btn.config(text=new_role)
        focus_audio.set_app_config(app_name, role=new_role.lower())

    def _on_vol_change(self, app_name, value):
        focus_audio.set_app_config(app_name, vol=value / 100.0)
        if app_name in self._app_rows:
            self._app_rows[app_name]["vol_label"].config(text=f"{value}%")

    def _on_duck_change(self, value):
        focus_audio.set_global_ducking(value / 100.0)
        if hasattr(self, "_duck_label"):
            self._duck_label.config(text=f"{value}%")
