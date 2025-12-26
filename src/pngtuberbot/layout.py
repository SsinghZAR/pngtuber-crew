from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .config import IconPosition, LayoutConfig, UserConfig


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class UserLayout:
    user_id: int
    slot: int
    avatar_x: int
    avatar_y: int
    avatar_w: int
    avatar_h: int
    mute_x: int
    mute_y: int
    deaf_x: int
    deaf_y: int


def assign_slots(users: list[UserConfig]) -> dict[int, int]:
    """
    Assign each user to a slot (1-6) using:
    - explicit `position_slot` when unique
    - otherwise first free slot, in user list order
    """
    taken: set[int] = set()
    out: dict[int, int] = {}

    # First pass: explicit slots
    for u in users:
        if u.position_slot is None:
            continue
        if u.position_slot in taken:
            log.warning("Duplicate position_slot=%s; user %s will be auto-assigned", u.position_slot, u.discord_id)
            continue
        if not (1 <= u.position_slot <= 6):
            log.warning("Invalid position_slot=%s; user %s will be auto-assigned", u.position_slot, u.discord_id)
            continue
        taken.add(u.position_slot)
        out[u.discord_id] = u.position_slot

    # Second pass: auto assign
    for u in users:
        if u.discord_id in out:
            continue
        for slot in range(1, 7):
            if slot not in taken:
                taken.add(slot)
                out[u.discord_id] = slot
                break
        else:
            raise ValueError("No free slots remaining (simple layout supports max 6 users)")

    return out


def _get_image_size(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path) as im:
            return int(im.size[0]), int(im.size[1])
    except Exception:
        return 200, 200


def _icon_anchor(
    *,
    avatar_x: int,
    avatar_y: int,
    avatar_w: int,
    avatar_h: int,
    icon_size: int,
    icon_position: IconPosition,
    stack_index: int,
) -> tuple[int, int]:
    """
    Compute icon position for a given corner and stack index (0=first icon, 1=second icon).

    Stacking direction:
    - top corners: stack down
    - bottom corners: stack up
    """
    if icon_position.startswith("top"):
        dy = stack_index * (icon_size + 2)
    else:
        dy = -stack_index * (icon_size + 2)

    if icon_position.endswith("left"):
        x = avatar_x
    else:
        x = avatar_x + max(0, avatar_w - icon_size)

    if icon_position.startswith("top"):
        y = avatar_y + dy
    else:
        y = avatar_y + max(0, avatar_h - icon_size) + dy

    return int(x), int(y)


def compute_user_layout(
    *,
    user: UserConfig,
    slot: int,
    layout: LayoutConfig,
    icon_size: int,
) -> UserLayout:
    if slot not in layout.positions:
        raise ValueError(f"Missing position for slot_{slot}")

    avatar_x, avatar_y = layout.positions[slot]
    avatar_w, avatar_h = _get_image_size(user.idle_animation)

    mute_x, mute_y = _icon_anchor(
        avatar_x=avatar_x,
        avatar_y=avatar_y,
        avatar_w=avatar_w,
        avatar_h=avatar_h,
        icon_size=icon_size,
        icon_position=user.icon_position,
        stack_index=0,
    )
    deaf_x, deaf_y = _icon_anchor(
        avatar_x=avatar_x,
        avatar_y=avatar_y,
        avatar_w=avatar_w,
        avatar_h=avatar_h,
        icon_size=icon_size,
        icon_position=user.icon_position,
        stack_index=1,
    )

    return UserLayout(
        user_id=user.discord_id,
        slot=slot,
        avatar_x=avatar_x,
        avatar_y=avatar_y,
        avatar_w=avatar_w,
        avatar_h=avatar_h,
        mute_x=mute_x,
        mute_y=mute_y,
        deaf_x=deaf_x,
        deaf_y=deaf_y,
    )


