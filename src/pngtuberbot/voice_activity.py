from __future__ import annotations

import asyncio
import audioop
import logging
import threading
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

import discord
from discord.ext.voice_recv import AudioSink, VoiceData, VoiceRecvClient


log = logging.getLogger(__name__)


SpeakingCallback = Callable[[int, bool], Awaitable[None]]  # (user_id, speaking)


@dataclass
class _UserSpeakingState:
    speaking: bool = False
    last_loud_t: float = 0.0  # monotonic time


class RmsVoiceActivityDetector:
    """
    Very small VAD-ish detector: compute RMS of PCM frames and apply a threshold + hangover.

    Important: `AudioSink.write()` is called from a background thread (packet router), so any
    async side effects must be scheduled onto the asyncio event loop.
    """

    def __init__(
        self,
        *,
        threshold: float,
        hangover_ms: int,
        loop: asyncio.AbstractEventLoop,
        on_speaking_change: SpeakingCallback,
        tick_interval_s: float = 0.05,
    ) -> None:
        self.threshold = float(threshold)
        self.hangover_s = max(0.0, hangover_ms / 1000.0)
        self.loop = loop
        self.on_speaking_change = on_speaking_change
        self.tick_interval_s = tick_interval_s

        self._lock = threading.Lock()
        self._states: dict[int, _UserSpeakingState] = {}
        self._task: asyncio.Task[None] | None = None
        self._closed = False

    def close(self) -> None:
        self._closed = True
        if self._task:
            self._task.cancel()
            self._task = None

    def start(self) -> None:
        if self._task or self._closed:
            return
        self._task = self.loop.create_task(self._tick_loop(), name="pngtuberbot:voice_activity_tick")

    def _schedule(self, coro: Awaitable[None]) -> None:
        # Called from non-async threads (sink write).
        try:
            asyncio.run_coroutine_threadsafe(coro, self.loop)
        except RuntimeError:
            # Loop is probably closing.
            pass

    def on_pcm(self, user_id: int, pcm: bytes) -> None:
        if self._closed or not pcm:
            return

        # audioop.rms returns 0..~32768 for 16-bit audio.
        try:
            rms = audioop.rms(pcm, 2) / 32768.0
        except Exception:
            return

        now = time.monotonic()

        with self._lock:
            st = self._states.get(user_id)
            if st is None:
                st = _UserSpeakingState(speaking=False, last_loud_t=0.0)
                self._states[user_id] = st

            if rms >= self.threshold:
                st.last_loud_t = now
                if not st.speaking:
                    st.speaking = True
                    self._schedule(self.on_speaking_change(user_id, True))
            # If below threshold, don't flip immediately. Tick loop handles hangover timeout.

    async def _tick_loop(self) -> None:
        try:
            while not self._closed:
                await asyncio.sleep(self.tick_interval_s)
                now = time.monotonic()
                to_stop: list[int] = []
                with self._lock:
                    for user_id, st in self._states.items():
                        if not st.speaking:
                            continue
                        if now - st.last_loud_t > self.hangover_s:
                            st.speaking = False
                            to_stop.append(user_id)

                for user_id in to_stop:
                    await self.on_speaking_change(user_id, False)
        except asyncio.CancelledError:
            return


class RmsAudioSink(AudioSink):
    def __init__(self, detector: RmsVoiceActivityDetector):
        super().__init__()
        self._detector = detector

    def wants_opus(self) -> bool:
        return False

    def write(self, user: Optional[discord.abc.User], data: VoiceData):
        if user is None:
            return
        pcm = data.pcm
        if not pcm:
            return
        self._detector.on_pcm(int(user.id), pcm)

    def cleanup(self):
        # Detector is owned externally.
        return


async def join_voice_channel_for_listening(
    *,
    client: discord.Client,
    guild_id: int,
    voice_channel_id: int,
) -> VoiceRecvClient:
    """
    Join the configured voice channel using VoiceRecvClient so we can receive audio.
    """
    guild = client.get_guild(int(guild_id))
    if guild is None:
        raise RuntimeError(f"Guild not found in cache: {guild_id}")

    channel = guild.get_channel(int(voice_channel_id))
    if channel is None:
        raise RuntimeError(f"Voice channel not found in guild: {voice_channel_id}")
    if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
        raise RuntimeError(f"Configured channel is not a voice/stage channel: {voice_channel_id}")

    vc = await channel.connect(cls=VoiceRecvClient)  # type: ignore[arg-type]
    log.info("Joined voice channel for listening: %s", channel.name)
    return vc


