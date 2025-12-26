from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from PIL import Image

from .config import AppConfig
from .discord_bot import PNGTuberDiscordClient, VoiceMuteDeafState, VoicePresenceChange
from .layout import assign_slots, compute_user_layout
from .obs_client import ObsClient, ObsError, SceneItemHandle
from .voice_activity import RmsAudioSink, RmsVoiceActivityDetector, join_voice_channel_for_listening


log = logging.getLogger(__name__)


@dataclass
class _UserRuntime:
    present: bool = False
    muted: bool = False
    deafened: bool = False


class PNGTuberBotRuntime:
    """
    Coordinates Discord events + voice receive + OBS actions.

    MVP assumptions:
    - Single OBS scene (cfg.obs.scene_name)
    - Sources created/managed by the bot
    """

    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.scene_name = cfg.obs.scene_name

        self.users_by_id = {u.discord_id: u for u in cfg.users}
        self._user_state: dict[int, _UserRuntime] = {uid: _UserRuntime() for uid in self.users_by_id}

        self.obs = ObsClient(cfg.obs.websocket_host, cfg.obs.websocket_port, cfg.obs.websocket_password)
        self._scene_items: dict[int, dict[str, SceneItemHandle]] = {}

        self.discord: PNGTuberDiscordClient | None = None
        self._voice_detector: RmsVoiceActivityDetector | None = None
        self._voice_client = None

    def _avatar_source(self, user_id: int) -> str:
        return f"pngtuber_{user_id}"

    def _mute_source(self, user_id: int) -> str:
        return f"pngtuber_{user_id}_mute"

    def _deaf_source(self, user_id: int) -> str:
        return f"pngtuber_{user_id}_deaf"

    async def setup_obs_sources(self) -> None:
        await self.obs.connect()
        try:
            ver = await self.obs.get_version()
            log.info("OBS: %s", ver.get("obsVersion", ver))
        except Exception:
            pass

        # Create/resolve scene items for each user.
        for uid, ucfg in self.users_by_id.items():
            mute_icon = ucfg.custom_mute_icon or self.cfg.icons.mute_default
            deaf_icon = ucfg.custom_deaf_icon or self.cfg.icons.deaf_default

            avatar = await self.obs.ensure_image_source_in_scene(
                scene_name=self.scene_name,
                source_name=self._avatar_source(uid),
                file_path=ucfg.idle_animation,
                enabled=False,
            )
            mute = await self.obs.ensure_image_source_in_scene(
                scene_name=self.scene_name,
                source_name=self._mute_source(uid),
                file_path=mute_icon,
                enabled=False,
            )
            deaf = await self.obs.ensure_image_source_in_scene(
                scene_name=self.scene_name,
                source_name=self._deaf_source(uid),
                file_path=deaf_icon,
                enabled=False,
            )

            self._scene_items[uid] = {"avatar": avatar, "mute": mute, "deaf": deaf}

        # Apply layout transforms (simple 6-slot mode).
        slot_map = assign_slots(list(self.cfg.users))

        def _img_size(p) -> tuple[int, int]:
            try:
                with Image.open(p) as im:
                    return int(im.size[0]), int(im.size[1])
            except Exception:
                return 0, 0

        for user in self.cfg.users:
            uid = user.discord_id
            handles = self._scene_items.get(uid)
            if not handles:
                continue

            slot = slot_map[uid]
            ul = compute_user_layout(user=user, slot=slot, layout=self.cfg.layout, icon_size=self.cfg.icons.size)

            # Avatar: position only (scale left as-is).
            await self.obs.set_scene_item_transform(
                scene_name=self.scene_name,
                scene_item_id=handles["avatar"].scene_item_id,
                x=ul.avatar_x,
                y=ul.avatar_y,
                scale_x=1.0,
                scale_y=1.0,
            )

            # Icons: force configured pixel size via scale, then place.
            mute_icon_path = user.custom_mute_icon or self.cfg.icons.mute_default
            deaf_icon_path = user.custom_deaf_icon or self.cfg.icons.deaf_default

            mw, mh = _img_size(mute_icon_path)
            dw, dh = _img_size(deaf_icon_path)
            mute_sx = (self.cfg.icons.size / mw) if mw else 1.0
            mute_sy = (self.cfg.icons.size / mh) if mh else 1.0
            deaf_sx = (self.cfg.icons.size / dw) if dw else 1.0
            deaf_sy = (self.cfg.icons.size / dh) if dh else 1.0

            await self.obs.set_scene_item_transform(
                scene_name=self.scene_name,
                scene_item_id=handles["mute"].scene_item_id,
                x=ul.mute_x,
                y=ul.mute_y,
                scale_x=mute_sx,
                scale_y=mute_sy,
            )
            await self.obs.set_scene_item_transform(
                scene_name=self.scene_name,
                scene_item_id=handles["deaf"].scene_item_id,
                x=ul.deaf_x,
                y=ul.deaf_y,
                scale_x=deaf_sx,
                scale_y=deaf_sy,
            )

            # Ensure icon ordering above avatar (best-effort).
            try:
                avatar_id = handles["avatar"].scene_item_id
                mute_id = handles["mute"].scene_item_id
                deaf_id = handles["deaf"].scene_item_id

                items = await self.obs.get_scene_item_list(self.scene_name)
                id_to_index = {int(it.get("sceneItemId")): idx for idx, it in enumerate(items) if "sceneItemId" in it}
                avatar_idx = id_to_index.get(int(avatar_id))
                if avatar_idx is None:
                    continue

                # Insert into the list around the avatar: mute, then deaf, then avatar.
                await self.obs.set_scene_item_index(self.scene_name, avatar_id, avatar_idx)
                await self.obs.set_scene_item_index(self.scene_name, deaf_id, avatar_idx)
                await self.obs.set_scene_item_index(self.scene_name, mute_id, avatar_idx)
            except Exception as e:
                log.debug("Could not enforce icon ordering for %s: %s", uid, e)

    async def _fade_or_toggle(self, handle: SceneItemHandle, show: bool) -> None:
        try:
            await self.obs.fade_scene_item(
                scene_name=handle.scene_name,
                scene_item_id=handle.scene_item_id,
                source_name=handle.source_name,
                show=show,
                duration_s=self.cfg.advanced.animation_duration,
            )
        except ObsError:
            await self.obs.set_scene_item_enabled(handle.scene_name, handle.scene_item_id, show)

    async def on_presence_change(self, change: VoicePresenceChange) -> None:
        uid = change.user_id
        if uid not in self.users_by_id:
            return

        st = self._user_state[uid]
        st.present = bool(change.joined)

        handles = self._scene_items.get(uid)
        if not handles:
            return

        # Always reset avatar to idle on join/leave.
        await self.obs.set_image_file(self._avatar_source(uid), self.users_by_id[uid].idle_animation)

        if change.joined:
            await self._fade_or_toggle(handles["avatar"], True)

            # Apply current mute/deaf state on join.
            await self.obs.set_scene_item_enabled(self.scene_name, handles["mute"].scene_item_id, st.muted)
            await self.obs.set_scene_item_enabled(self.scene_name, handles["deaf"].scene_item_id, st.deafened)
        else:
            # Hide everything on leave.
            await self._fade_or_toggle(handles["avatar"], False)
            await self.obs.set_scene_item_enabled(self.scene_name, handles["mute"].scene_item_id, False)
            await self.obs.set_scene_item_enabled(self.scene_name, handles["deaf"].scene_item_id, False)

    async def on_mute_deaf_change(self, state: VoiceMuteDeafState) -> None:
        uid = state.user_id
        if uid not in self.users_by_id:
            return

        st = self._user_state[uid]
        st.muted = bool(state.muted)
        st.deafened = bool(state.deafened)

        handles = self._scene_items.get(uid)
        if not handles:
            return

        if not st.present:
            # Cache only; don't show icons while hidden.
            return

        await self.obs.set_scene_item_enabled(self.scene_name, handles["mute"].scene_item_id, st.muted)
        await self.obs.set_scene_item_enabled(self.scene_name, handles["deaf"].scene_item_id, st.deafened)

        # If muted and we don't allow talking while muted, force idle.
        if st.muted and not self.cfg.advanced.talking_while_muted:
            await self.obs.set_image_file(self._avatar_source(uid), self.users_by_id[uid].idle_animation)

    async def on_speaking_change(self, user_id: int, speaking: bool) -> None:
        if user_id not in self.users_by_id:
            return

        st = self._user_state[user_id]
        if not st.present:
            return

        if st.muted and not self.cfg.advanced.talking_while_muted:
            speaking = False

        path = self.users_by_id[user_id].talking_animation if speaking else self.users_by_id[user_id].idle_animation
        await self.obs.set_image_file(self._avatar_source(user_id), path)

    async def _on_ready_hook(self, client: PNGTuberDiscordClient) -> None:
        # Join voice for listening, then start RMS-based speaking detection.
        loop = asyncio.get_running_loop()
        vc = await join_voice_channel_for_listening(
            client=client,
            guild_id=self.cfg.discord.guild_id,
            voice_channel_id=self.cfg.discord.voice_channel_id,
        )

        detector = RmsVoiceActivityDetector(
            threshold=self.cfg.advanced.talking_threshold,
            hangover_ms=self.cfg.advanced.talking_hangover_ms,
            loop=loop,
            on_speaking_change=self.on_speaking_change,
        )
        detector.start()
        sink = RmsAudioSink(detector)
        vc.listen(sink)

        self._voice_client = vc
        self._voice_detector = detector
        log.info("Voice receive started (RMS threshold=%.4f)", self.cfg.advanced.talking_threshold)

    async def run(self) -> None:
        await self.setup_obs_sources()

        tracked = set(self.users_by_id.keys())
        self.discord = PNGTuberDiscordClient(
            guild_id=self.cfg.discord.guild_id,
            voice_channel_id=self.cfg.discord.voice_channel_id,
            tracked_user_ids=tracked,
            on_presence_change=self.on_presence_change,
            on_mute_deaf_change=self.on_mute_deaf_change,
            on_ready_hook=self._on_ready_hook,
        )

        try:
            await self.discord.start(self.cfg.discord.bot_token)
        finally:
            try:
                if self._voice_detector:
                    self._voice_detector.close()
            except Exception:
                pass

            try:
                if self._voice_client:
                    await self._voice_client.disconnect(force=True)  # type: ignore[func-returns-value]
            except Exception:
                pass

            try:
                if self.discord:
                    await self.discord.close()
            except Exception:
                pass

            try:
                await self.obs.disconnect()
            except Exception:
                pass


