"""Request routing: call only the models each job actually needs.

- Transcription runs only when audio was provided.
- A Spark editing plan is built for every render.
- SAM tracking runs only when the plan keeps a frame_subject operation.
- Image generation runs only when the user explicitly asked for artwork.
- Wonder escalation exists but stays off unless explicitly enabled; normal
  edits never touch it.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RouteDecision:
    steps: tuple[str, ...]


def decide(
    *,
    has_audio: bool,
    plan_operations: list[dict[str, object]],
    thumbnail_requested: bool,
    allow_wonder: bool = False,
    plan_failures: int = 0,
) -> RouteDecision:
    steps: list[str] = []
    if has_audio:
        steps.append("transcribe")
    if allow_wonder and plan_failures >= 2:
        steps.append("wonder_plan")
    else:
        steps.append("spark_plan")
    if any(op.get("op") == "frame_subject" for op in plan_operations):
        steps.append("sam_track")
    steps.append("render")
    if thumbnail_requested:
        steps.append("thumbnail")
    return RouteDecision(steps=tuple(steps))
