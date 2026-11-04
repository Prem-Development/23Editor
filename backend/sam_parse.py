"""SAM 3.1 output parsing and subject-framing windows.

Verified against https://dev.meta.ai/docs/sam/reading-segmentation
(Nov 2026). Segmentation output arrives as special-token text in one
``output_text`` lane: one line per frame::

    <Nf>id<|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..|><|mask;...|>,...

- Frame index N is zero-based but may skip or run out of order.
- Object ids are stable across frames but sparse; key by id, never position.
- Box coordinates are integers in frame pixels; w/h repeat the frame size.
- Mask payloads are base85 (not base64); only the raster dims are kept.

Masks are parsed but not needed for reframing: crop/zoom windows are
derived from boxes only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_BOX_RE = re.compile(
    r"<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=(\d+);h=(\d+)"
    r"(?:;c=([0-9.]+))?\|>"
)
_MASK_RE = re.compile(r"<\|mask;x=0;y=0(?:;c=[0-9.]+)?;data=(\d+),(\d+),")
_LINE_RE = re.compile(r"^<(\d+)f>(.*)$")
_RECORD_RE = re.compile(r"(\d+)(<\|box;.*?\|>)(<\|mask;.*?\|>)")


@dataclass(frozen=True)
class Box:
    x1: int
    y1: int
    x2: int
    y2: int
    frame_w: int
    frame_h: int

    @property
    def width(self) -> int:
        return max(0, self.x2 - self.x1)

    @property
    def height(self) -> int:
        return max(0, self.y2 - self.y1)

    @property
    def area(self) -> int:
        return self.width * self.height

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2.0

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) / 2.0


@dataclass(frozen=True)
class FrameDetections:
    frame_index: int
    boxes: dict[int, Box]  # object id -> box


@dataclass
class SamParseResult:
    frames: list[FrameDetections] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def parse_output_text(payload: str) -> SamParseResult:
    """Parse SAM's one-line-per-frame output text."""
    result = SamParseResult()
    for raw_line in payload.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        head = _LINE_RE.match(line)
        if not head:
            result.warnings.append(f"Skipped malformed line: {line[:60]!r}.")
            continue
        frame_index = int(head.group(1))
        boxes: dict[int, Box] = {}
        for record in _RECORD_RE.finditer(head.group(2)):
            obj_id = int(record.group(1))
            box_match = _BOX_RE.match(record.group(2))
            if not box_match:
                result.warnings.append(
                    f"Frame {frame_index}: skipped malformed box."
                )
                continue
            x1, y1, x2, y2, fw, fh = (
                int(box_match.group(i)) for i in range(1, 7)
            )
            if x2 <= x1 or y2 <= y1 or fw <= 0 or fh <= 0:
                result.warnings.append(
                    f"Frame {frame_index}: skipped degenerate box."
                )
                continue
            boxes[obj_id] = Box(x1, y1, x2, y2, fw, fh)
        if boxes:
            result.frames.append(
                FrameDetections(frame_index=frame_index, boxes=boxes)
            )
    return result


def union_box(boxes: dict[int, Box]) -> Box | None:
    """Smallest box containing every match (keeps all subjects in frame)."""
    if not boxes:
        return None
    items = list(boxes.values())
    return Box(
        x1=min(b.x1 for b in items),
        y1=min(b.y1 for b in items),
        x2=max(b.x2 for b in items),
        y2=max(b.y2 for b in items),
        frame_w=items[0].frame_w,
        frame_h=items[0].frame_h,
    )


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _fit_aspect(
    cx: float, cy: float, w: float, h: float, ratio: float
) -> tuple[float, float]:
    if w / max(h, 1e-6) > ratio:
        return w, w / ratio
    return h * ratio, h


def window_for_segment(
    frames: list[FrameDetections],
    fps: float,
    start: float,
    end: float,
    aspect: str,
) -> tuple[tuple[int, int, int, int] | None, str | None]:
    """Compute one crop window (x, y, w, h) for a tracked segment.

    Uses the median subject center and the largest observed extent inside
    [start, end), expanded by a margin and fitted to ``aspect``. Returns
    (None, reason) when no detections fall inside the segment.
    """
    if fps <= 0:
        return None, "unknown frame rate"
    first = int(start * fps)
    last = int(end * fps)
    unions: list[Box] = []
    for frame in frames:
        if first <= frame.frame_index < last:
            box = union_box(frame.boxes)
            if box is not None:
                unions.append(box)
    if not unions:
        return None, "no tracked subject visible in segment"
    frame_w, frame_h = unions[0].frame_w, unions[0].frame_h
    margin = 1.18
    half_w = max(b.width for b in unions) / 2.0 * margin
    half_h = max(b.height for b in unions) / 2.0 * margin
    cx = _median([b.cx for b in unions])
    cy = _median([b.cy for b in unions])
    ratio = {"9:16": 9 / 16, "16:9": 16 / 9, "1:1": 1.0}.get(aspect, 0.0)
    if ratio > 0:
        fit_w, fit_h = _fit_aspect(cx, cy, half_w * 2, half_h * 2, ratio)
        half_w, half_h = fit_w / 2.0, fit_h / 2.0
    half_w = min(half_w, frame_w / 2.0)
    half_h = min(half_h, frame_h / 2.0)
    cx = min(max(cx, half_w), frame_w - half_w)
    cy = min(max(cy, half_h), frame_h - half_h)
    x = int(cx - half_w) // 2 * 2
    y = int(cy - half_h) // 2 * 2
    w = int(half_w * 2) // 2 * 2
    h = int(half_h * 2) // 2 * 2
    w = max(2, (min(w, frame_w - x) // 2) * 2)
    h = max(2, (min(h, frame_h - y) // 2) * 2)
    return (x, y, w, h), None
