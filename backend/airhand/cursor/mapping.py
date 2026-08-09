"""Hand position to screen position.

Pure functions, no I/O and no OS calls, so every rule below is testable without moving a real
cursor.

Two things are easy to get wrong here and both are load-bearing:

**Mirroring.** The camera faces the user, so moving a hand to the user's right makes it appear
further left in the frame and `x` *decreases*. Without mirroring the cursor would move the wrong
way — which reads as the app being broken rather than as a sign convention.

Everything in this module is in **raw frame coordinates**, the same space MediaPipe's landmarks and
the pointer anchor live in; the mirror is applied to the final screen fraction, once, in
:func:`to_screen`. Mirroring the input instead is equivalent only while the active area is centred,
and silently wrong the moment it is not.

**Gain uniformity.** The active area's aspect ratio must match the *screen's*, not the camera's.
Mapping a 4:3 region onto a 21:9 display would amplify horizontal hand movement roughly 1.8x more
than vertical, so the same gesture would travel further sideways than up. The formula in
:func:`active_area_for` is what keeps a centimetre of hand movement worth the same number of
pixels in both axes.

**The centre is adjustable, the size is not negotiable.** A user sitting to one side of the camera,
or with the camera mounted off to one side, has a comfortable hand position that is not the middle
of the frame — the Calibration screen measures it. When the requested centre would push the region
past the frame edge the *position* is clamped and the size left alone: shrinking it there would
silently undo the gain uniformity the rest of this module exists to protect.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ActiveArea:
    """Sub-rectangle of the camera frame that maps onto the whole screen.

    Coordinates are normalized frame units (0..1). Reaching the screen edges must not require
    reaching the frame edges, where hands are half out of view and tracking degrades.
    """

    left: float
    top: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.left + self.width

    @property
    def bottom(self) -> float:
        return self.top + self.height


def active_area_for(
    *,
    screen_aspect: float,
    frame_aspect: float,
    coverage: float = 0.7,
    center: tuple[float, float] = (0.5, 0.5),
) -> ActiveArea:
    """Active area of the given size, centred where the user asked, whose pixel aspect matches
    the screen.

    `coverage` is the fraction of frame *width* to use — effectively the sensitivity control:
    a smaller area means less hand movement per screen pixel. `center` is where that region sits
    in the frame; the default is the middle, which is what every caller wanted before the
    Calibration screen existed.

    Derivation: a normalized rect w x h covers w*frame_w by h*frame_h pixels, so its pixel aspect
    is (w/h) * frame_aspect. Setting that equal to screen_aspect gives
    h = w * frame_aspect / screen_aspect.
    """
    if not 0.0 < coverage <= 1.0:
        raise ValueError(f"coverage must be in (0, 1], got {coverage}")

    width = coverage
    height = width * frame_aspect / screen_aspect

    # A very wide screen paired with a narrow frame can demand a taller region than exists.
    # Shrink both axes rather than silently distorting the gain we just went to trouble to equalise.
    if height > 1.0:
        width /= height
        height = 1.0

    # Clamped so the region stays wholly inside the frame. Position only: a centre near the edge
    # slides the region back rather than trimming it, because trimming would change the aspect and
    # with it the gain uniformity established above.
    return ActiveArea(
        left=_clamp(center[0] - width / 2.0, 0.0, 1.0 - width),
        top=_clamp(center[1] - height / 2.0, 0.0, 1.0 - height),
        width=width,
        height=height,
    )


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def to_screen(
    x: float,
    y: float,
    *,
    area: ActiveArea,
    screen_width: int,
    screen_height: int,
    mirror: bool = True,
) -> tuple[int, int]:
    """Map a normalized frame position to absolute screen pixels.

    Positions outside the active area clamp to the screen edge, which is what lets a user park the
    cursor in a corner without pushing their hand out of the camera's view.

    **Mirroring happens after the area, not before it.** Both orders behave identically while the
    area is centred, which is why this went unnoticed until the area could move: mirroring `x`
    first puts :class:`ActiveArea` in mirrored coordinates, while landmarks, the pointer anchor and
    the centre the Calibration screen measures are all raw frame coordinates. An area centred on a
    measured resting position then landed on the opposite side of the frame.
    """
    u = _clamp((x - area.left) / area.width)
    v = _clamp((y - area.top) / area.height)

    if mirror:
        u = 1.0 - u

    # Scale by size-1 so u=1 lands on the last addressable pixel rather than one past the edge.
    return round(u * (screen_width - 1)), round(v * (screen_height - 1))
