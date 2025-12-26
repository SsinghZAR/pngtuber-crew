from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any

import yaml
from PIL import Image, ImageDraw

from .obs_client import ObsClient


@dataclass
class UserRowVars:
    name: tk.StringVar
    discord_id: tk.StringVar
    idle_animation: tk.StringVar
    talking_animation: tk.StringVar
    position_slot: tk.StringVar
    icon_position: tk.StringVar
    custom_mute_icon: tk.StringVar
    custom_deaf_icon: tk.StringVar


def _is_snowflake(s: str) -> bool:
    s = (s or "").strip()
    return s.isdigit() and 17 <= len(s) <= 20


def _read_yaml_lenient(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _ensure_default_icons(base_dir: Path, mute_path: Path, deaf_path: Path, size: int) -> None:
    (base_dir / "assets" / "icons").mkdir(parents=True, exist_ok=True)

    def make_icon(path: Path, label: str) -> None:
        if path.exists():
            return
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        # Red circle + slash + label
        draw.ellipse((2, 2, size - 2, size - 2), outline=(220, 40, 40, 255), width=4)
        draw.line((8, size - 8, size - 8, 8), fill=(220, 40, 40, 255), width=5)
        draw.text((size // 2 - 6, size // 2 - 7), label, fill=(220, 40, 40, 255))
        img.save(path)

    make_icon(mute_path, "M")
    make_icon(deaf_path, "D")


class SetupApp(tk.Tk):
    def __init__(self, config_path: Path) -> None:
        super().__init__()
        self.title("PNGTuberBot Setup")
        self.geometry("980x720")

        self.config_path = config_path
        self.base_dir = config_path.parent

        self.discord_token = tk.StringVar()
        self.guild_id = tk.StringVar()
        self.voice_channel_id = tk.StringVar()

        self.obs_host = tk.StringVar(value="localhost")
        self.obs_port = tk.StringVar(value="4455")
        self.obs_password = tk.StringVar()
        self.obs_scene = tk.StringVar(value="Gaming")

        self.icon_mute_default = tk.StringVar(value="assets/icons/default_mute.png")
        self.icon_deaf_default = tk.StringVar(value="assets/icons/default_deaf.png")
        self.icon_size = tk.StringVar(value="64")

        self.animation_duration = tk.StringVar(value="0.5")
        self.talking_threshold = tk.StringVar(value="0.02")
        self.talking_hangover_ms = tk.StringVar(value="300")
        self.talking_while_muted = tk.BooleanVar(value=False)

        self.user_rows: list[UserRowVars] = []

        self._build_ui()
        self._load_into_form()

    # ---------------- UI ----------------

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.pack(fill=tk.BOTH, expand=True)

        nb = ttk.Notebook(root)
        nb.pack(fill=tk.BOTH, expand=True)

        conn = ttk.Frame(nb, padding=12)
        users = ttk.Frame(nb, padding=12)
        adv = ttk.Frame(nb, padding=12)

        nb.add(conn, text="Connections")
        nb.add(users, text="Participants")
        nb.add(adv, text="Advanced")

        # Connections
        ttk.Label(conn, text="Discord Bot Token").grid(row=0, column=0, sticky="w")
        ttk.Entry(conn, textvariable=self.discord_token, width=70).grid(row=0, column=1, sticky="we", padx=8)

        ttk.Label(conn, text="Guild ID").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(conn, textvariable=self.guild_id, width=30).grid(row=1, column=1, sticky="w", padx=8, pady=(8, 0))

        ttk.Label(conn, text="Voice Channel ID").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(conn, textvariable=self.voice_channel_id, width=30).grid(row=2, column=1, sticky="w", padx=8, pady=(8, 0))

        ttk.Separator(conn).grid(row=3, column=0, columnspan=2, sticky="we", pady=12)

        ttk.Label(conn, text="OBS Host").grid(row=4, column=0, sticky="w")
        ttk.Entry(conn, textvariable=self.obs_host, width=30).grid(row=4, column=1, sticky="w", padx=8)

        ttk.Label(conn, text="OBS Port").grid(row=5, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(conn, textvariable=self.obs_port, width=10).grid(row=5, column=1, sticky="w", padx=8, pady=(8, 0))

        ttk.Label(conn, text="OBS Password").grid(row=6, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(conn, textvariable=self.obs_password, show="*", width=30).grid(row=6, column=1, sticky="w", padx=8, pady=(8, 0))

        ttk.Label(conn, text="OBS Scene Name").grid(row=7, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(conn, textvariable=self.obs_scene, width=30).grid(row=7, column=1, sticky="w", padx=8, pady=(8, 0))

        conn.columnconfigure(1, weight=1)

        # Participants
        users_toolbar = ttk.Frame(users)
        users_toolbar.pack(fill=tk.X)

        ttk.Button(users_toolbar, text="Add Person", command=self._add_user_row).pack(side=tk.LEFT)
        ttk.Button(users_toolbar, text="Save Config", command=self._save_config).pack(side=tk.LEFT, padx=8)
        ttk.Button(users_toolbar, text="Test OBS Connection", command=self._test_obs).pack(side=tk.LEFT, padx=8)

        self.users_canvas = tk.Canvas(users, highlightthickness=0)
        self.users_scroll = ttk.Scrollbar(users, orient="vertical", command=self.users_canvas.yview)
        self.users_canvas.configure(yscrollcommand=self.users_scroll.set)

        self.users_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.users_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.users_inner = ttk.Frame(self.users_canvas, padding=(0, 12))
        self.users_canvas.create_window((0, 0), window=self.users_inner, anchor="nw")
        self.users_inner.bind("<Configure>", lambda e: self.users_canvas.configure(scrollregion=self.users_canvas.bbox("all")))

        # Advanced
        row = 0
        ttk.Label(adv, text="Default mute icon").grid(row=row, column=0, sticky="w")
        ttk.Entry(adv, textvariable=self.icon_mute_default, width=60).grid(row=row, column=1, sticky="we", padx=8)
        ttk.Button(adv, text="Browse", command=lambda: self._browse_to(self.icon_mute_default)).grid(row=row, column=2)
        row += 1

        ttk.Label(adv, text="Default deaf icon").grid(row=row, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(adv, textvariable=self.icon_deaf_default, width=60).grid(row=row, column=1, sticky="we", padx=8, pady=(8, 0))
        ttk.Button(adv, text="Browse", command=lambda: self._browse_to(self.icon_deaf_default)).grid(row=row, column=2, pady=(8, 0))
        row += 1

        ttk.Label(adv, text="Icon size (px)").grid(row=row, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(adv, textvariable=self.icon_size, width=10).grid(row=row, column=1, sticky="w", padx=8, pady=(8, 0))
        row += 1

        ttk.Separator(adv).grid(row=row, column=0, columnspan=3, sticky="we", pady=12)
        row += 1

        ttk.Label(adv, text="Fade duration (s)").grid(row=row, column=0, sticky="w")
        ttk.Entry(adv, textvariable=self.animation_duration, width=10).grid(row=row, column=1, sticky="w", padx=8)
        row += 1

        ttk.Label(adv, text="Talking threshold (RMS)").grid(row=row, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(adv, textvariable=self.talking_threshold, width=10).grid(row=row, column=1, sticky="w", padx=8, pady=(8, 0))
        row += 1

        ttk.Label(adv, text="Talking hangover (ms)").grid(row=row, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(adv, textvariable=self.talking_hangover_ms, width=10).grid(row=row, column=1, sticky="w", padx=8, pady=(8, 0))
        row += 1

        ttk.Checkbutton(adv, text="Allow talking animation while muted", variable=self.talking_while_muted).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )
        row += 1

        adv.columnconfigure(1, weight=1)

    def _browse_to(self, var: tk.StringVar, *, filetypes: list[tuple[str, str]] | None = None) -> None:
        path = filedialog.askopenfilename(filetypes=filetypes or [("All files", "*.*")])
        if path:
            var.set(path)

    def _add_user_row(self, data: dict[str, Any] | None = None) -> None:
        data = data or {}

        vars_ = UserRowVars(
            name=tk.StringVar(value=data.get("name", "")),
            discord_id=tk.StringVar(value=str(data.get("discord_id", ""))),
            idle_animation=tk.StringVar(value=data.get("idle_animation", "")),
            talking_animation=tk.StringVar(value=data.get("talking_animation", "")),
            position_slot=tk.StringVar(value=str(data.get("position_slot", "")) if data.get("position_slot") else ""),
            icon_position=tk.StringVar(value=data.get("icon_position", "top-right")),
            custom_mute_icon=tk.StringVar(value=data.get("custom_mute_icon") or ""),
            custom_deaf_icon=tk.StringVar(value=data.get("custom_deaf_icon") or ""),
        )
        self.user_rows.append(vars_)

        row = len(self.user_rows) - 1
        frm = ttk.Labelframe(self.users_inner, text=f"Person {row + 1}", padding=10)
        frm.grid(row=row, column=0, sticky="we", pady=8)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="Name").grid(row=0, column=0, sticky="w")
        ttk.Entry(frm, textvariable=vars_.name, width=30).grid(row=0, column=1, sticky="we", padx=8)

        ttk.Label(frm, text="Discord User ID").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(frm, textvariable=vars_.discord_id, width=30).grid(row=1, column=1, sticky="we", padx=8, pady=(8, 0))

        ttk.Label(frm, text="Idle GIF").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(frm, textvariable=vars_.idle_animation, width=50).grid(row=2, column=1, sticky="we", padx=8, pady=(8, 0))
        ttk.Button(frm, text="Browse", command=lambda v=vars_.idle_animation: self._browse_to(v)).grid(
            row=2, column=2, pady=(8, 0)
        )

        ttk.Label(frm, text="Talking GIF").grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(frm, textvariable=vars_.talking_animation, width=50).grid(row=3, column=1, sticky="we", padx=8, pady=(8, 0))
        ttk.Button(frm, text="Browse", command=lambda v=vars_.talking_animation: self._browse_to(v)).grid(
            row=3, column=2, pady=(8, 0)
        )

        ttk.Label(frm, text="Position slot (1-6 or blank=auto)").grid(row=4, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(frm, textvariable=vars_.position_slot, width=10).grid(row=4, column=1, sticky="w", padx=8, pady=(8, 0))

        ttk.Label(frm, text="Icon position").grid(row=5, column=0, sticky="w", pady=(8, 0))
        ttk.Combobox(
            frm,
            textvariable=vars_.icon_position,
            values=["top-right", "top-left", "bottom-right", "bottom-left"],
            width=15,
            state="readonly",
        ).grid(row=5, column=1, sticky="w", padx=8, pady=(8, 0))

        ttk.Label(frm, text="Custom mute icon (optional)").grid(row=6, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(frm, textvariable=vars_.custom_mute_icon, width=50).grid(row=6, column=1, sticky="we", padx=8, pady=(8, 0))
        ttk.Button(frm, text="Browse", command=lambda v=vars_.custom_mute_icon: self._browse_to(v)).grid(
            row=6, column=2, pady=(8, 0)
        )

        ttk.Label(frm, text="Custom deaf icon (optional)").grid(row=7, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(frm, textvariable=vars_.custom_deaf_icon, width=50).grid(row=7, column=1, sticky="we", padx=8, pady=(8, 0))
        ttk.Button(frm, text="Browse", command=lambda v=vars_.custom_deaf_icon: self._browse_to(v)).grid(
            row=7, column=2, pady=(8, 0)
        )

        def remove_row() -> None:
            try:
                idx = self.user_rows.index(vars_)
            except ValueError:
                return
            self.user_rows.pop(idx)
            frm.destroy()
            self._renumber_user_frames()

        ttk.Button(frm, text="Remove", command=remove_row).grid(row=0, column=2, padx=(8, 0))

    def _renumber_user_frames(self) -> None:
        # Re-label frames after removals.
        for i, child in enumerate(self.users_inner.winfo_children()):
            if isinstance(child, ttk.Labelframe):
                child.configure(text=f"Person {i + 1}")

    # ---------------- IO ----------------

    def _load_into_form(self) -> None:
        raw = _read_yaml_lenient(self.config_path)

        discord = raw.get("discord") if isinstance(raw.get("discord"), dict) else {}
        obs = raw.get("obs") if isinstance(raw.get("obs"), dict) else {}
        icons = raw.get("icons") if isinstance(raw.get("icons"), dict) else {}
        advanced = raw.get("advanced") if isinstance(raw.get("advanced"), dict) else {}

        self.discord_token.set(str(discord.get("bot_token", self.discord_token.get() or "")))
        self.guild_id.set(str(discord.get("guild_id", "")))
        self.voice_channel_id.set(str(discord.get("voice_channel_id", "")))

        self.obs_host.set(str(obs.get("websocket_host", self.obs_host.get())))
        self.obs_port.set(str(obs.get("websocket_port", self.obs_port.get())))
        self.obs_password.set(str(obs.get("websocket_password", self.obs_password.get() or "")))
        self.obs_scene.set(str(obs.get("scene_name", self.obs_scene.get())))

        self.icon_mute_default.set(str(icons.get("mute_default", self.icon_mute_default.get())))
        self.icon_deaf_default.set(str(icons.get("deaf_default", self.icon_deaf_default.get())))
        self.icon_size.set(str(icons.get("size", self.icon_size.get())))

        self.animation_duration.set(str(advanced.get("animation_duration", self.animation_duration.get())))
        self.talking_threshold.set(str(advanced.get("talking_threshold", self.talking_threshold.get())))
        self.talking_hangover_ms.set(str(advanced.get("talking_hangover_ms", self.talking_hangover_ms.get())))
        self.talking_while_muted.set(bool(advanced.get("talking_while_muted", False)))

        users = raw.get("users") if isinstance(raw.get("users"), list) else []
        for u in users:
            if isinstance(u, dict):
                self._add_user_row(u)

        if not self.user_rows:
            self._add_user_row()

    def _validate(self) -> list[str]:
        errs: list[str] = []
        if not self.discord_token.get().strip():
            errs.append("Discord bot token is required.")
        if not _is_snowflake(self.guild_id.get()):
            errs.append("Guild ID must be a Discord snowflake (17-20 digits).")
        if not _is_snowflake(self.voice_channel_id.get()):
            errs.append("Voice Channel ID must be a Discord snowflake (17-20 digits).")

        try:
            int(self.obs_port.get())
        except Exception:
            errs.append("OBS port must be an integer.")

        if not self.obs_scene.get().strip():
            errs.append("OBS scene name is required.")

        # Participants
        if not self.user_rows:
            errs.append("At least one participant is required.")
        for i, u in enumerate(self.user_rows):
            prefix = f"Person {i + 1}: "
            if not u.name.get().strip():
                errs.append(prefix + "name is required.")
            if not _is_snowflake(u.discord_id.get()):
                errs.append(prefix + "Discord User ID must be 17-20 digits.")
            if not u.idle_animation.get().strip():
                errs.append(prefix + "idle GIF path is required.")
            if not u.talking_animation.get().strip():
                errs.append(prefix + "talking GIF path is required.")

        return errs

    def _build_config_dict(self) -> dict[str, Any]:
        users_out: list[dict[str, Any]] = []
        for u in self.user_rows:
            slot_s = u.position_slot.get().strip()
            slot = int(slot_s) if slot_s.isdigit() else None
            users_out.append(
                {
                    "discord_id": u.discord_id.get().strip(),
                    "name": u.name.get().strip(),
                    "idle_animation": u.idle_animation.get().strip(),
                    "talking_animation": u.talking_animation.get().strip(),
                    "position_slot": slot,
                    "icon_position": u.icon_position.get().strip() or "top-right",
                    "custom_mute_icon": u.custom_mute_icon.get().strip() or None,
                    "custom_deaf_icon": u.custom_deaf_icon.get().strip() or None,
                }
            )

        # Keep existing layout positions if present; otherwise default.
        existing = _read_yaml_lenient(self.config_path)
        layout = existing.get("layout") if isinstance(existing.get("layout"), dict) else None
        if not layout:
            layout = {
                "mode": "simple",
                "positions": {
                    "slot_1": [100, 100],
                    "slot_2": [300, 100],
                    "slot_3": [500, 100],
                    "slot_4": [100, 400],
                    "slot_5": [300, 400],
                    "slot_6": [500, 400],
                },
            }

        return {
            "discord": {
                "bot_token": self.discord_token.get().strip(),
                "guild_id": int(self.guild_id.get().strip()),
                "voice_channel_id": int(self.voice_channel_id.get().strip()),
            },
            "obs": {
                "websocket_host": self.obs_host.get().strip(),
                "websocket_port": int(self.obs_port.get().strip()),
                "websocket_password": self.obs_password.get(),
                "scene_name": self.obs_scene.get().strip(),
            },
            "users": users_out,
            "layout": layout,
            "icons": {
                "mute_default": self.icon_mute_default.get().strip(),
                "deaf_default": self.icon_deaf_default.get().strip(),
                "size": int(self.icon_size.get().strip() or "64"),
            },
            "advanced": {
                "animation_duration": float(self.animation_duration.get().strip() or "0.5"),
                "reconnect_attempts": 3,
                "log_level": "INFO",
                "talking_threshold": float(self.talking_threshold.get().strip() or "0.02"),
                "talking_hangover_ms": int(self.talking_hangover_ms.get().strip() or "300"),
                "talking_while_muted": bool(self.talking_while_muted.get()),
            },
        }

    def _save_config(self) -> None:
        errs = self._validate()
        if errs:
            messagebox.showerror("Fix config errors", "\n".join(errs))
            return

        cfg = self._build_config_dict()

        # Ensure default icons exist (relative to base_dir).
        mute_p = Path(cfg["icons"]["mute_default"])
        deaf_p = Path(cfg["icons"]["deaf_default"])
        if not mute_p.is_absolute():
            mute_p = (self.base_dir / mute_p).resolve()
        if not deaf_p.is_absolute():
            deaf_p = (self.base_dir / deaf_p).resolve()

        _ensure_default_icons(self.base_dir, mute_p, deaf_p, int(cfg["icons"]["size"]))

        self.config_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
        messagebox.showinfo("Saved", f"Saved configuration to:\n{self.config_path}")

    def _test_obs(self) -> None:
        errs = []
        try:
            port = int(self.obs_port.get().strip())
        except Exception:
            port = 4455
            errs.append("OBS port must be an integer.")
        if errs:
            messagebox.showerror("Fix config errors", "\n".join(errs))
            return

        host = self.obs_host.get().strip() or "localhost"
        password = self.obs_password.get()

        def worker() -> None:
            async def _run() -> dict[str, Any]:
                obs = ObsClient(host, port, password)
                await obs.connect()
                ver = await obs.get_version()
                await obs.disconnect()
                return ver

            try:
                ver = asyncio.run(_run())
                msg = f"Connected to OBS.\nOBS: {ver.get('obsVersion', ver)}"
                self.after(0, lambda: messagebox.showinfo("OBS OK", msg))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("OBS connection failed", str(e)))

        threading.Thread(target=worker, daemon=True).start()


def main() -> int:
    app = SetupApp(Path("config.yaml").resolve())
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


