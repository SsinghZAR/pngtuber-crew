from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

import discord


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class VoicePresenceChange:
    user_id: int
    joined: bool  # True=joined target channel, False=left target channel


@dataclass(frozen=True)
class VoiceMuteDeafState:
    user_id: int
    muted: bool
    deafened: bool


PresenceCallback = Callable[[VoicePresenceChange], Awaitable[None]]
MuteDeafCallback = Callable[[VoiceMuteDeafState], Awaitable[None]]


def _is_muted(vs: discord.VoiceState) -> bool:
    # self_mute: user muted themselves; mute: server muted them
    return bool(vs.self_mute or vs.mute)


def _is_deafened(vs: discord.VoiceState) -> bool:
    # self_deaf: user deafened themselves; deaf: server deafened them
    return bool(vs.self_deaf or vs.deaf)


class PNGTuberDiscordClient(discord.Client):
    def __init__(
        self,
        *,
        guild_id: int,
        voice_channel_id: int,
        tracked_user_ids: set[int],
        on_presence_change: PresenceCallback,
        on_mute_deaf_change: MuteDeafCallback,
        on_ready_hook: Optional[Callable[["PNGTuberDiscordClient"], Awaitable[None]]] = None,
    ) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        intents.voice_states = True
        intents.members = True  # needed to resolve members reliably

        super().__init__(intents=intents)
        self.guild_id = int(guild_id)
        self.voice_channel_id = int(voice_channel_id)
        self.tracked_user_ids = tracked_user_ids
        self.on_presence_change_cb = on_presence_change
        self.on_mute_deaf_change_cb = on_mute_deaf_change
        self.on_ready_hook = on_ready_hook

        # Cache last known mute/deaf to avoid duplicate callbacks.
        self._mute_deaf_cache: dict[int, tuple[bool, bool]] = {}

    async def on_ready(self) -> None:
        log.info("Discord connected as %s (id=%s)", self.user, getattr(self.user, "id", "?"))

        guild = self.get_guild(self.guild_id)
        if guild is None:
            log.error("Configured guild_id not found in cache: %s", self.guild_id)
            return

        channel = guild.get_channel(self.voice_channel_id)
        if channel is None:
            log.error("Configured voice_channel_id not found in guild: %s", self.voice_channel_id)
            return

        if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            log.error("Configured voice_channel_id is not a voice/stage channel: %s", self.voice_channel_id)
            return

        # Initialize state for users already present.
        for member in list(channel.members):
            if member.id not in self.tracked_user_ids:
                continue
            await self.on_presence_change_cb(VoicePresenceChange(user_id=member.id, joined=True))

            if member.voice:
                muted = _is_muted(member.voice)
                deafened = _is_deafened(member.voice)
                self._mute_deaf_cache[member.id] = (muted, deafened)
                await self.on_mute_deaf_change_cb(
                    VoiceMuteDeafState(user_id=member.id, muted=muted, deafened=deafened)
                )

        if self.on_ready_hook is not None:
            await self.on_ready_hook(self)

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.id not in self.tracked_user_ids:
            return

        before_chan = before.channel.id if before.channel else None
        after_chan = after.channel.id if after.channel else None

        was_in_target = before_chan == self.voice_channel_id
        is_in_target = after_chan == self.voice_channel_id

        # Join/leave
        if not was_in_target and is_in_target:
            await self.on_presence_change_cb(VoicePresenceChange(user_id=member.id, joined=True))
        elif was_in_target and not is_in_target:
            await self.on_presence_change_cb(VoicePresenceChange(user_id=member.id, joined=False))

        # Mute/deaf (only while in target channel)
        if not is_in_target:
            return

        muted = _is_muted(after)
        deafened = _is_deafened(after)

        prev = self._mute_deaf_cache.get(member.id)
        cur = (muted, deafened)
        if prev != cur:
            self._mute_deaf_cache[member.id] = cur
            await self.on_mute_deaf_change_cb(VoiceMuteDeafState(user_id=member.id, muted=muted, deafened=deafened))


