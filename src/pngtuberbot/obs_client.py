from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import simpleobsws


log = logging.getLogger(__name__)


class ObsError(RuntimeError):
    pass


@dataclass(frozen=True)
class OpacityFilterSpec:
    filter_name: str
    filter_kind: str
    opacity_key: str
    max_opacity: float


@dataclass
class SceneItemHandle:
    scene_name: str
    source_name: str
    scene_item_id: int


class ObsClient:
    """
    Minimal OBS WebSocket v5 wrapper (async) using simpleobsws.

    Notes:
    - Filters are attached to SOURCES (inputs). If a source is reused across scenes,
      opacity changes affect all scenes. MVP uses a single scene to avoid surprises.
    """

    def __init__(self, host: str, port: int, password: str) -> None:
        self._url = f"ws://{host}:{port}"
        self._password = password or ""
        self._ws: simpleobsws.WebSocketClient | None = None
        self._opacity_filter_cache: dict[str, OpacityFilterSpec] = {}

    async def connect(self) -> None:
        if self._ws and self._ws.ws_open:
            return
        self._ws = simpleobsws.WebSocketClient(url=self._url, password=self._password)
        await self._ws.connect()
        ok = await self._ws.wait_until_identified(timeout=10)
        if not ok:
            raise ObsError("Failed to identify with OBS WebSocket (timeout)")
        log.info("Connected to OBS (%s)", self._url)

    async def get_version(self) -> dict[str, Any]:
        """Return raw version info from OBS (GetVersion)."""
        return await self._call("GetVersion")

    async def disconnect(self) -> None:
        if self._ws:
            await self._ws.disconnect()
            self._ws = None

    def _require_ws(self) -> simpleobsws.WebSocketClient:
        if not self._ws:
            raise ObsError("OBS client not connected")
        return self._ws

    async def _call(self, request_type: str, request_data: dict[str, Any] | None = None) -> dict[str, Any]:
        ws = self._require_ws()
        req = simpleobsws.Request(request_type, requestData=request_data or {})
        resp = await ws.call(req)
        if not resp.ok():
            msg = resp.requestStatus.comment or "Unknown OBS error"
            raise ObsError(f"{request_type} failed: {msg}")
        return resp.responseData or {}

    async def get_scene_item_list(self, scene_name: str) -> list[dict[str, Any]]:
        data = await self._call("GetSceneItemList", {"sceneName": scene_name})
        return list(data.get("sceneItems") or [])

    async def get_scene_item_id(self, scene_name: str, source_name: str) -> int | None:
        try:
            data = await self._call("GetSceneItemId", {"sceneName": scene_name, "sourceName": source_name})
            return int(data["sceneItemId"])
        except ObsError:
            return None
        except Exception:
            return None

    async def ensure_image_source_in_scene(
        self,
        *,
        scene_name: str,
        source_name: str,
        file_path: Path,
        enabled: bool,
    ) -> SceneItemHandle:
        """
        Ensure an Image source exists IN the given scene.

        For MVP we use CreateInput (kind=image_source) in the target scene.
        """
        # Fast-path: already in scene
        existing_id = await self.get_scene_item_id(scene_name, source_name)
        if existing_id is None:
            # CreateInput will create the input and add it to the scene.
            data = await self._call(
                "CreateInput",
                {
                    "sceneName": scene_name,
                    "inputName": source_name,
                    "inputKind": "image_source",
                    "inputSettings": {"file": str(file_path)},
                    "sceneItemEnabled": bool(enabled),
                },
            )
            scene_item_id = int(data["sceneItemId"])
        else:
            scene_item_id = existing_id
            # Ensure correct file is set
            await self.set_image_file(source_name, file_path)
            await self.set_scene_item_enabled(scene_name, scene_item_id, enabled)

        return SceneItemHandle(scene_name=scene_name, source_name=source_name, scene_item_id=scene_item_id)

    async def set_image_file(self, source_name: str, file_path: Path) -> None:
        await self._call(
            "SetInputSettings",
            {"inputName": source_name, "inputSettings": {"file": str(file_path)}, "overlay": True},
        )

    async def set_scene_item_enabled(self, scene_name: str, scene_item_id: int, enabled: bool) -> None:
        await self._call(
            "SetSceneItemEnabled",
            {"sceneName": scene_name, "sceneItemId": int(scene_item_id), "sceneItemEnabled": bool(enabled)},
        )

    async def set_scene_item_transform(
        self,
        *,
        scene_name: str,
        scene_item_id: int,
        x: float,
        y: float,
        scale_x: float = 1.0,
        scale_y: float = 1.0,
    ) -> None:
        await self._call(
            "SetSceneItemTransform",
            {
                "sceneName": scene_name,
                "sceneItemId": int(scene_item_id),
                "sceneItemTransform": {
                    "positionX": float(x),
                    "positionY": float(y),
                    "scaleX": float(scale_x),
                    "scaleY": float(scale_y),
                },
            },
        )

    async def set_scene_item_index(self, scene_name: str, scene_item_id: int, scene_item_index: int) -> None:
        await self._call(
            "SetSceneItemIndex",
            {"sceneName": scene_name, "sceneItemId": int(scene_item_id), "sceneItemIndex": int(scene_item_index)},
        )

    async def _get_opacity_filter_spec(self, source_name: str) -> OpacityFilterSpec:
        cached = self._opacity_filter_cache.get(source_name)
        if cached:
            return cached

        # We use Color Correction filter ("color_filter") because it exposes an opacity control.
        filter_kind = "color_filter"
        filter_name = "PNGTuberBotOpacity"

        # Best-effort: detect the opacity key + scale from default settings.
        opacity_key = "opacity"
        max_opacity = 100.0
        try:
            defaults = await self._call("GetSourceFilterDefaultSettings", {"filterKind": filter_kind})
            default_settings = (
                defaults.get("defaultFilterSettings")
                or defaults.get("defaultSettings")
                or defaults.get("filterSettings")
                or {}
            )
            if isinstance(default_settings, dict):
                # Common: {"opacity": 100.0, ...}
                if "opacity" in default_settings:
                    opacity_key = "opacity"
                    v = default_settings.get("opacity")
                    if isinstance(v, (int, float)) and float(v) > 0:
                        max_opacity = float(v)
                elif "alpha" in default_settings:
                    opacity_key = "alpha"
                    v = default_settings.get("alpha")
                    if isinstance(v, (int, float)) and float(v) > 0:
                        max_opacity = float(v)
        except ObsError:
            # If OBS doesn't support the request (older versions), we fall back to defaults.
            pass

        spec = OpacityFilterSpec(
            filter_name=filter_name,
            filter_kind=filter_kind,
            opacity_key=opacity_key,
            max_opacity=max_opacity,
        )
        self._opacity_filter_cache[source_name] = spec
        return spec

    async def ensure_opacity_filter(self, source_name: str) -> OpacityFilterSpec:
        spec = await self._get_opacity_filter_spec(source_name)
        try:
            data = await self._call("GetSourceFilterList", {"sourceName": source_name})
            filters = data.get("filters") or []
            if isinstance(filters, list) and any(isinstance(f, dict) and f.get("filterName") == spec.filter_name for f in filters):
                return spec
        except ObsError:
            # Fall through: try to create.
            pass

        # Create filter with full opacity by default.
        await self._call(
            "CreateSourceFilter",
            {
                "sourceName": source_name,
                "filterName": spec.filter_name,
                "filterKind": spec.filter_kind,
                "filterSettings": {spec.opacity_key: spec.max_opacity},
            },
        )
        return spec

    async def set_opacity(self, source_name: str, opacity_0_to_1: float) -> None:
        spec = await self.ensure_opacity_filter(source_name)
        opacity = max(0.0, min(1.0, float(opacity_0_to_1))) * spec.max_opacity
        await self._call(
            "SetSourceFilterSettings",
            {"sourceName": source_name, "filterName": spec.filter_name, "filterSettings": {spec.opacity_key: opacity}},
        )

    async def fade_scene_item(
        self,
        *,
        scene_name: str,
        scene_item_id: int,
        source_name: str,
        show: bool,
        duration_s: float,
        fps: int = 30,
    ) -> None:
        """
        Fade a scene item in/out using a source-level opacity filter.

        If anything fails, caller should fall back to instant toggle.
        """
        duration_s = max(0.0, float(duration_s))
        if duration_s == 0:
            await self.set_scene_item_enabled(scene_name, scene_item_id, show)
            return

        steps = max(1, int(duration_s * fps))
        delay = duration_s / steps

        if show:
            await self.set_opacity(source_name, 0.0)
            await self.set_scene_item_enabled(scene_name, scene_item_id, True)
            for i in range(1, steps + 1):
                await self.set_opacity(source_name, i / steps)
                await asyncio.sleep(delay)
        else:
            for i in range(steps - 1, -1, -1):
                await self.set_opacity(source_name, i / steps)
                await asyncio.sleep(delay)
            await self.set_scene_item_enabled(scene_name, scene_item_id, False)


