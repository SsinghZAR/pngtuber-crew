from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path
from typing import Any, Literal

import yaml


class ConfigError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("\n".join(errors))
        self.errors = errors


IconPosition = Literal["top-right", "top-left", "bottom-right", "bottom-left"]
LayoutMode = Literal["simple"]  # smart mode intentionally deferred


_SNOWFLAKE_RE = re.compile(r"^[0-9]{17,20}$")


def _require_mapping(obj: Any, where: str) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise ConfigError([f"{where} must be a mapping/object"])
    return obj


def _require_list(obj: Any, where: str) -> list[Any]:
    if not isinstance(obj, list):
        raise ConfigError([f"{where} must be a list"])
    return obj


def _require_str(obj: Any, where: str) -> str:
    if not isinstance(obj, str) or not obj.strip():
        raise ConfigError([f"{where} must be a non-empty string"])
    return obj


def _require_int(obj: Any, where: str) -> int:
    if isinstance(obj, bool) or not isinstance(obj, int):
        raise ConfigError([f"{where} must be an integer"])
    return obj


def _require_number(obj: Any, where: str) -> float:
    if isinstance(obj, bool) or not isinstance(obj, (int, float)):
        raise ConfigError([f"{where} must be a number"])
    return float(obj)


def _optional_path(obj: Any, where: str, base_dir: Path) -> Path | None:
    if obj is None:
        return None
    s = _require_str(obj, where)
    p = Path(s)
    if not p.is_absolute():
        p = (base_dir / p).resolve()
    return p


def _required_path(obj: Any, where: str, base_dir: Path) -> Path:
    p = _optional_path(obj, where, base_dir)
    if p is None:
        raise ConfigError([f"{where} is required"])
    return p


def _parse_snowflake(obj: Any, where: str) -> int:
    if isinstance(obj, int):
        if obj <= 0:
            raise ConfigError([f"{where} must be > 0"])
        return obj
    s = _require_str(obj, where)
    if not _SNOWFLAKE_RE.match(s):
        raise ConfigError([f"{where} must be a Discord snowflake (17-20 digits)"])
    return int(s)


def _parse_icon_position(obj: Any, where: str) -> IconPosition:
    s = _require_str(obj, where)
    if s not in ("top-right", "top-left", "bottom-right", "bottom-left"):
        raise ConfigError([f"{where} must be one of: top-right, top-left, bottom-right, bottom-left"])
    return s  # type: ignore[return-value]


@dataclass(frozen=True)
class DiscordConfig:
    bot_token: str
    guild_id: int
    voice_channel_id: int


@dataclass(frozen=True)
class ObsConfig:
    websocket_host: str
    websocket_port: int
    websocket_password: str
    scene_name: str


@dataclass(frozen=True)
class UserConfig:
    discord_id: int
    name: str
    idle_animation: Path
    talking_animation: Path
    position_slot: int | None
    icon_position: IconPosition
    custom_mute_icon: Path | None
    custom_deaf_icon: Path | None


@dataclass(frozen=True)
class LayoutConfig:
    mode: LayoutMode
    positions: dict[int, tuple[int, int]]  # slot -> (x, y)


@dataclass(frozen=True)
class IconsConfig:
    mute_default: Path
    deaf_default: Path
    size: int


@dataclass(frozen=True)
class AdvancedConfig:
    animation_duration: float
    reconnect_attempts: int
    log_level: str

    talking_threshold: float
    talking_hangover_ms: int
    talking_while_muted: bool


@dataclass(frozen=True)
class AppConfig:
    config_path: Path
    discord: DiscordConfig
    obs: ObsConfig
    users: list[UserConfig]
    layout: LayoutConfig
    icons: IconsConfig
    advanced: AdvancedConfig

    @property
    def base_dir(self) -> Path:
        return self.config_path.parent


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path).resolve()
    base_dir = config_path.parent
    errors: list[str] = []

    try:
        raw_text = config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError([f"Config file not found: {config_path}"])

    try:
        raw = yaml.safe_load(raw_text) or {}
    except Exception as e:  # pragma: no cover - depends on yaml parser internals
        raise ConfigError([f"Failed to parse YAML: {e}"])

    if not isinstance(raw, dict):
        raise ConfigError(["Root of config must be a YAML mapping/object"])

    def collect(fn, *a):  # type: ignore[no-untyped-def]
        try:
            return fn(*a)
        except ConfigError as ce:
            errors.extend(ce.errors)
            return None

    discord_raw = collect(_require_mapping, raw.get("discord"), "discord") or {}
    obs_raw = collect(_require_mapping, raw.get("obs"), "obs") or {}
    layout_raw = collect(_require_mapping, raw.get("layout"), "layout") or {}
    icons_raw = collect(_require_mapping, raw.get("icons"), "icons") or {}
    advanced_raw = collect(_require_mapping, raw.get("advanced"), "advanced") or {}

    bot_token = collect(_require_str, discord_raw.get("bot_token"), "discord.bot_token") or ""
    guild_id = collect(_parse_snowflake, discord_raw.get("guild_id"), "discord.guild_id") or 0
    voice_channel_id = (
        collect(_parse_snowflake, discord_raw.get("voice_channel_id"), "discord.voice_channel_id") or 0
    )
    discord_cfg = DiscordConfig(bot_token=bot_token, guild_id=guild_id, voice_channel_id=voice_channel_id)

    websocket_host = collect(_require_str, obs_raw.get("websocket_host"), "obs.websocket_host") or "localhost"
    websocket_port = collect(_require_int, obs_raw.get("websocket_port"), "obs.websocket_port") or 4455
    websocket_password = obs_raw.get("websocket_password") or ""
    if websocket_password is None:
        websocket_password = ""
    if not isinstance(websocket_password, str):
        errors.append("obs.websocket_password must be a string")
        websocket_password = ""
    scene_name = collect(_require_str, obs_raw.get("scene_name"), "obs.scene_name") or ""
    obs_cfg = ObsConfig(
        websocket_host=websocket_host,
        websocket_port=websocket_port,
        websocket_password=websocket_password,
        scene_name=scene_name,
    )

    mode = layout_raw.get("mode", "simple")
    if mode != "simple":
        errors.append("layout.mode must be 'simple' for MVP")
        mode = "simple"

    positions_raw = collect(_require_mapping, layout_raw.get("positions"), "layout.positions") or {}
    positions: dict[int, tuple[int, int]] = {}
    for slot in range(1, 7):
        key = f"slot_{slot}"
        val = positions_raw.get(key)
        where = f"layout.positions.{key}"
        if val is None:
            errors.append(f"{where} is required")
            continue
        if (
            not isinstance(val, (list, tuple))
            or len(val) != 2
            or not isinstance(val[0], (int, float))
            or not isinstance(val[1], (int, float))
        ):
            errors.append(f"{where} must be [x, y] numbers")
            continue
        positions[slot] = (int(val[0]), int(val[1]))
    layout_cfg = LayoutConfig(mode=mode, positions=positions)

    mute_default = collect(_required_path, icons_raw.get("mute_default"), "icons.mute_default", base_dir)
    deaf_default = collect(_required_path, icons_raw.get("deaf_default"), "icons.deaf_default", base_dir)
    size = collect(_require_int, icons_raw.get("size"), "icons.size") or 64
    icons_cfg = IconsConfig(
        mute_default=mute_default or (base_dir / "assets/icons/default_mute.png").resolve(),
        deaf_default=deaf_default or (base_dir / "assets/icons/default_deaf.png").resolve(),
        size=size,
    )

    animation_duration = collect(_require_number, advanced_raw.get("animation_duration"), "advanced.animation_duration") or 0.5
    reconnect_attempts = collect(_require_int, advanced_raw.get("reconnect_attempts"), "advanced.reconnect_attempts") or 3
    log_level = advanced_raw.get("log_level", "INFO")
    if not isinstance(log_level, str):
        errors.append("advanced.log_level must be a string")
        log_level = "INFO"

    talking_threshold = collect(_require_number, advanced_raw.get("talking_threshold"), "advanced.talking_threshold") or 0.02
    talking_hangover_ms = collect(_require_int, advanced_raw.get("talking_hangover_ms"), "advanced.talking_hangover_ms") or 300
    talking_while_muted = bool(advanced_raw.get("talking_while_muted", False))

    if animation_duration <= 0:
        errors.append("advanced.animation_duration must be > 0")
    if talking_threshold <= 0:
        errors.append("advanced.talking_threshold must be > 0")
    if talking_hangover_ms < 0:
        errors.append("advanced.talking_hangover_ms must be >= 0")

    advanced_cfg = AdvancedConfig(
        animation_duration=animation_duration,
        reconnect_attempts=reconnect_attempts,
        log_level=log_level,
        talking_threshold=talking_threshold,
        talking_hangover_ms=talking_hangover_ms,
        talking_while_muted=talking_while_muted,
    )

    users_list = collect(_require_list, raw.get("users"), "users") or []
    users: list[UserConfig] = []
    seen_ids: set[int] = set()
    for idx, u_raw in enumerate(users_list):
        where = f"users[{idx}]"
        if not isinstance(u_raw, dict):
            errors.append(f"{where} must be an object")
            continue
        u_discord_id = collect(_parse_snowflake, u_raw.get("discord_id"), f"{where}.discord_id")
        if not u_discord_id:
            continue
        if u_discord_id in seen_ids:
            errors.append(f"{where}.discord_id duplicates another user")
            continue
        seen_ids.add(u_discord_id)

        name = collect(_require_str, u_raw.get("name"), f"{where}.name") or str(u_discord_id)
        idle_animation = collect(_required_path, u_raw.get("idle_animation"), f"{where}.idle_animation", base_dir)
        talking_animation = collect(
            _required_path, u_raw.get("talking_animation"), f"{where}.talking_animation", base_dir
        )

        slot = u_raw.get("position_slot", None)
        position_slot: int | None
        if slot is None:
            position_slot = None
        else:
            try:
                slot_int = _require_int(slot, f"{where}.position_slot")
                if not (1 <= slot_int <= 6):
                    raise ConfigError([f"{where}.position_slot must be 1-6 or null"])
                position_slot = slot_int
            except ConfigError as ce:
                errors.extend(ce.errors)
                position_slot = None

        icon_position = collect(_parse_icon_position, u_raw.get("icon_position", "top-right"), f"{where}.icon_position")
        custom_mute_icon = collect(_optional_path, u_raw.get("custom_mute_icon"), f"{where}.custom_mute_icon", base_dir)
        custom_deaf_icon = collect(_optional_path, u_raw.get("custom_deaf_icon"), f"{where}.custom_deaf_icon", base_dir)

        users.append(
            UserConfig(
                discord_id=u_discord_id,
                name=name,
                idle_animation=idle_animation or Path(),
                talking_animation=talking_animation or Path(),
                position_slot=position_slot,
                icon_position=icon_position or "top-right",
                custom_mute_icon=custom_mute_icon,
                custom_deaf_icon=custom_deaf_icon,
            )
        )

    # Final validation pass
    if not users:
        errors.append("At least one user must be configured under `users:`")

    if errors:
        raise ConfigError(errors)

    return AppConfig(
        config_path=config_path,
        discord=discord_cfg,
        obs=obs_cfg,
        users=users,
        layout=layout_cfg,
        icons=icons_cfg,
        advanced=advanced_cfg,
    )


