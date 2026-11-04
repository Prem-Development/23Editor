"""Editing plans: JSON schema for Muse Spark + strict local validation.

The schema below is sent as ``response_format`` (type ``json_schema``) so
decoding itself is constrained to valid JSON. The validator then enforces
semantic rules (times within the media, crops within the frame, no
conflicting operations) before anything reaches FFmpeg.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

ASPECTS = ("original", "9:16", "16:9", "1:1")
TEXT_POSITIONS = ("top", "center", "bottom")
TEXT_ANIMATIONS = ("none", "fade", "slide")
TEXT_SIZES = ("small", "medium", "large")
GRADE_PRESETS = ("warm", "cool", "vivid", "mono")
TRANSITION_TYPES = ("fade",)
SFX_ASSETS = ("pop", "chime", "whoosh", "click")
RESIZE_FITS = ("pad", "crop")
CAPTION_SOURCES = ("auto",)
BANNER_POSITIONS = ("top", "bottom", "top_left", "top_right",
                    "bottom_left", "bottom_right", "center")
PIP_POSITIONS = ("top_left", "top_right", "bottom_left", "bottom_right")
INTRO_MIN_SEC = 0.5
INTRO_MAX_SEC = 10.0
OUTRO_MIN_SEC = 0.5
OUTRO_MAX_SEC = 10.0
BANNER_MIN_FRAC = 0.1
BANNER_MAX_FRAC = 1.0
BANNER_DEFAULT_FRAC = 0.4
MAX_BANNERS = 2
MAX_CUTAWAYS = 4
MAX_PIPS = 4
MAX_INSERTS = 4
PIP_DEFAULT_FRAC = 0.25
OVERLAY_MIN_SEC = 0.5
OVERLAY_MAX_SEC = 30.0
INSERT_MIN_SEC = 0.5
INSERT_MAX_SEC = 60.0
CUTAWAY_AUDIO = ("mute", "mix", "use")
PIP_AUDIO = ("mute", "mix")
INSERT_AUDIO = ("mute", "use")

def _op_branch(
    op: str, required: list[str], extra: dict[str, object]
) -> dict[str, object]:
    properties: dict[str, object] = {
        "op": {"type": "string", "enum": [op]}
    }
    properties.update(extra)
    return {
        "type": "object",
        "required": ["op"] + required,
        "additionalProperties": False,
        "properties": properties,
    }


_ASPECT_ENUM: dict[str, object] = {"type": "string", "enum": list(ASPECTS)}


EDIT_PLAN_JSON_SCHEMA: dict[str, object] = {
    "type": "object",
    "required": ["operations"],
    "additionalProperties": False,
    "properties": {
        "operations": {
            "type": "array",
            "minItems": 1,
            "maxItems": 24,
            "items": {
                # anyOf below the root is supported per the API docs;
                # oneOf/allOf are not. Each branch pins one op value plus
                # that op's required fields, so decoding cannot omit them.
                "anyOf": [
                    _op_branch("trim", ["start", "end"], {
                        "start": {"type": "number"},
                        "end": {"type": "number"},
                    }),
                    _op_branch("crop", ["x", "y", "w", "h"], {
                        "x": {"type": "integer"},
                        "y": {"type": "integer"},
                        "w": {"type": "integer"},
                        "h": {"type": "integer"},
                    }),
                    _op_branch(
                        "frame_subject", ["subject", "start", "end"], {
                            "subject": {"type": "string"},
                            "start": {"type": "number"},
                            "end": {"type": "number"},
                            "aspect": dict(_ASPECT_ENUM),
                        },
                    ),
                    _op_branch("resize_pad", ["aspect"], {
                        "aspect": dict(_ASPECT_ENUM),
                        "fit": {"type": "string",
                                "enum": list(RESIZE_FITS)},
                    }),
                    _op_branch("volume", ["gain_db"], {
                        "gain_db": {"type": "number"},
                    }),
                    _op_branch("fade", [], {
                        "in_sec": {"type": "number"},
                        "out_sec": {"type": "number"},
                    }),
                    _op_branch("keep", ["start", "end"], {
                        "start": {"type": "number"},
                        "end": {"type": "number"},
                    }),
                    _op_branch("remove", ["start", "end"], {
                        "start": {"type": "number"},
                        "end": {"type": "number"},
                    }),
                    _op_branch(
                        "text",
                        ["text", "start", "end", "position",
                         "animation", "size"],
                        {
                            "text": {"type": "string"},
                            "start": {"type": "number"},
                            "end": {"type": "number"},
                            "position": {"type": "string",
                                         "enum": list(TEXT_POSITIONS)},
                            "animation": {"type": "string",
                                          "enum": list(TEXT_ANIMATIONS)},
                            "size": {"type": "string",
                                     "enum": list(TEXT_SIZES)},
                        },
                    ),
                    _op_branch("zoom", ["start", "end", "to"], {
                        "start": {"type": "number"},
                        "end": {"type": "number"},
                        "from": {"type": "number"},
                        "to": {"type": "number"},
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                    }),
                    _op_branch("grade", ["preset", "intensity"], {
                        "preset": {"type": "string",
                                   "enum": list(GRADE_PRESETS)},
                        "intensity": {"type": "number"},
                    }),
                    _op_branch("transition", ["type", "duration"], {
                        "type": {"type": "string",
                                 "enum": list(TRANSITION_TYPES)},
                        "duration": {"type": "number"},
                    }),
                    _op_branch("captions", ["source"], {
                        "source": {"type": "string",
                                   "enum": list(CAPTION_SOURCES)},
                    }),
                    _op_branch("captions", ["segments"], {
                        "segments": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 120,
                            "items": {
                                "type": "object",
                                "required": ["start", "end", "text"],
                                "additionalProperties": False,
                                "properties": {
                                    "start": {"type": "number"},
                                    "end": {"type": "number"},
                                    "text": {"type": "string"},
                                },
                            },
                        },
                    }),
                    _op_branch("music", ["file_id", "volume_db", "duck"], {
                        "file_id": {"type": "string"},
                        "volume_db": {"type": "number"},
                        "duck": {"type": "boolean"},
                    }),
                    _op_branch("sfx", ["asset", "at"], {
                        "asset": {"type": "string",
                                  "enum": list(SFX_ASSETS)},
                        "at": {"type": "number"},
                    }),
                    _op_branch("sfx", ["file_id", "at"], {
                        "file_id": {"type": "string"},
                        "at": {"type": "number"},
                    }),
                ],
            },
        },
        "notes": {"type": "string"},
        "unsupported": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string"},
        },
    },
}


EDIT_PLAN_JSON_SCHEMA_COMPAT: dict[str, object] = {
    # Legacy flat shape: only "op" is required per operation. Used solely
    # as a fallback if the API ever rejects the strict schema above;
    # local validation stays strict either way.
    "type": "object",
    "required": ["operations"],
    "additionalProperties": False,
    "properties": {
        "operations": {
            "type": "array",
            "minItems": 1,
            "maxItems": 24,
            "items": {
                "type": "object",
                "required": ["op"],
                "additionalProperties": False,
                "properties": {
                    "op": {
                        "type": "string",
                        "enum": [
                            "trim",
                            "crop",
                            "frame_subject",
                            "resize_pad",
                            "volume",
                            "fade",
                            "keep",
                            "remove",
                            "text",
                            "zoom",
                            "grade",
                            "transition",
                            "captions",
                            "music",
                            "sfx",
                        ],
                    },
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "w": {"type": "integer"},
                    "h": {"type": "integer"},
                    "subject": {"type": "string"},
                    "aspect": {"type": "string", "enum": list(ASPECTS)},
                    "fit": {"type": "string", "enum": list(RESIZE_FITS)},
                    "gain_db": {"type": "number"},
                    "in_sec": {"type": "number"},
                    "out_sec": {"type": "number"},
                    "text": {"type": "string"},
                    "position": {"type": "string"},
                    "animation": {"type": "string"},
                    "size": {"type": "string"},
                    "from": {"type": "number"},
                    "to": {"type": "number"},
                    "preset": {"type": "string"},
                    "intensity": {"type": "number"},
                    "type": {"type": "string"},
                    "duration": {"type": "number"},
                    "source": {"type": "string"},
                    "segments": {"type": "array"},
                    "file_id": {"type": "string"},
                    "volume_db": {"type": "number"},
                    "duck": {"type": "boolean"},
                    "asset": {"type": "string"},
                    "at": {"type": "number"},
                },
            },
        },
        "notes": {"type": "string"},
        "unsupported": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string"},
        },
    },
}


ASPECT_ALIASES: dict[str, tuple[str, ...]] = {
    "original": ("original",),
    "9:16": ("9:16", "9x16", "9 x 16", "vertical", "portrait"),
    "16:9": ("16:9", "16x9", "16 x 9", "horizontal", "landscape", "widescreen"),
    "1:1": ("1:1", "1x1", "1 x 1", "square"),
}


def canonical_aspect(value: object) -> str:
    """Map a requested aspect to the exact canonical enum value."""
    text = value.strip().lower() if isinstance(value, str) else ""
    for canonical, aliases in ASPECT_ALIASES.items():
        if text in aliases:
            return canonical
    raise ValueError(
        "aspect_ratio must be one of " + ", ".join(ASPECTS) + "."
    )


def normalize_plan_aspects(plan: object) -> tuple[object, list[str]]:
    """Rewrite known aspect aliases to canonical enum values.

    Returns (plan, applied_notes). Unknown values are left untouched for
    strict validation to reject; missing values are never invented.
    """
    applied: list[str] = []
    if not isinstance(plan, dict):
        return plan, applied
    ops = plan.get("operations")
    if not isinstance(ops, list):
        return plan, applied
    for index, raw in enumerate(ops):
        if not isinstance(raw, dict):
            continue
        value = raw.get("aspect")
        if value is None:
            continue
        try:
            canonical = canonical_aspect(value)
        except ValueError:
            continue  # strict validation reports the unknown value
        if canonical != value:
            raw["aspect"] = canonical
            applied.append(
                f"operations[{index}].aspect {value!r} → {canonical!r}."
            )
    return plan, applied


@dataclass(frozen=True)
class MediaInfo:
    duration: float
    width: int
    height: int
    fps: float = 30.0
    has_audio: bool = False


@dataclass
class PlanValidation:
    plan: dict[str, object] | None = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.plan is not None and not self.errors


def _is_num(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_plan(obj: object, media: MediaInfo) -> PlanValidation:
    """Validate a decoded plan object against semantic rules."""
    result = PlanValidation()
    if not isinstance(obj, dict):
        result.errors.append("Plan must be a JSON object.")
        return result
    ops = obj.get("operations")
    if not isinstance(ops, list) or not ops:
        result.errors.append("Plan must contain at least one operation.")
        return result
    if len(ops) > 24:
        result.errors.append("Plan has too many operations (max 24).")
        return result

    counts: dict[str, int] = {}
    trims: list[dict[str, object]] = []
    segments: list[dict[str, object]] = []
    for index, raw in enumerate(ops):
        label = f"operations[{index}]"
        if not isinstance(raw, dict):
            result.errors.append(f"{label} must be an object.")
            continue
        op = raw.get("op")
        if not isinstance(op, str):
            result.errors.append(f"{label}.op must be a string.")
            continue
        counts[op] = counts.get(op, 0) + 1
        check = _CHECKS.get(op)
        if check is None:
            result.errors.append(f"{label}.op is not supported: {op!r}.")
            continue
        check(label, raw, media, result)
        if op == "trim":
            trims.append(raw)
        elif op == "frame_subject":
            segments.append(raw)

    if result.errors:
        return result
    if counts.get("trim", 0) > 1:
        result.errors.append("Only one trim is supported.")
    if counts.get("crop", 0) > 1:
        result.errors.append("Only one crop is supported.")
    if counts.get("resize_pad", 0) > 1:
        result.errors.append("Only one resize_pad is supported.")
    if counts.get("volume", 0) > 1:
        result.errors.append("Only one volume change is supported.")
    if counts.get("fade", 0) > 2:
        result.errors.append("At most two fades are supported.")
    if counts.get("crop", 0) and counts.get("frame_subject", 0):
        result.errors.append("Choose crop or frame_subject, not both.")
    cutting = [m for m in ("trim", "keep", "remove") if counts.get(m, 0)]
    if len(cutting) > 1:
        result.errors.append(
            f"Choose one cutting mode, not {', '.join(cutting)}."
        )
    if counts.get("frame_subject", 0) and (
        counts.get("keep", 0) or counts.get("remove", 0)
    ):
        result.errors.append(
            "frame_subject cannot combine with keep or remove."
        )
    if counts.get("zoom", 0) and (
        counts.get("crop", 0) or counts.get("frame_subject", 0)
    ):
        result.errors.append(
            "zoom cannot combine with crop or frame_subject yet."
        )
    if counts.get("frame_subject", 0) and (
        counts.get("text", 0) or counts.get("captions", 0)
    ):
        result.errors.append(
            "text and captions cannot combine with frame_subject yet."
        )
    if counts.get("grade", 0) > 1:
        result.errors.append("Only one grade is supported.")
    if counts.get("transition", 0) > 1:
        result.errors.append("Only one transition is supported.")
    if counts.get("captions", 0) > 1:
        result.errors.append("Only one captions track is supported.")
    if counts.get("music", 0) > 1:
        result.errors.append("Only one music track is supported.")
    if counts.get("sfx", 0) > 8:
        result.errors.append("At most 8 sound effects are supported.")
    if counts.get("keep", 0) > 8:
        result.errors.append("At most 8 kept segments are supported.")
    if counts.get("remove", 0) > 8:
        result.errors.append("At most 8 removed intervals are supported.")
    if counts.get("intro", 0) > 1:
        result.errors.append("Only one intro is supported.")
    if counts.get("outro", 0) > 1:
        result.errors.append("Only one outro is supported.")
    if counts.get("banner", 0) > MAX_BANNERS:
        result.errors.append(
            f"At most {MAX_BANNERS} banners are supported."
        )
    if counts.get("cutaway", 0) > MAX_CUTAWAYS:
        result.errors.append(
            f"At most {MAX_CUTAWAYS} cutaways are supported."
        )
    if counts.get("pip", 0) > MAX_PIPS:
        result.errors.append(
            f"At most {MAX_PIPS} picture-in-picture overlays are supported."
        )
    if counts.get("insert", 0) > MAX_INSERTS:
        result.errors.append(
            f"At most {MAX_INSERTS} inserts are supported."
        )
    if counts.get("frame_subject", 0) and (
        counts.get("intro", 0) or counts.get("outro", 0)
        or counts.get("banner", 0) or counts.get("cutaway", 0)
        or counts.get("pip", 0) or counts.get("insert", 0)
    ):
        result.errors.append(
            "intro, outro, banner, cutaway, pip, and insert cannot "
            "combine with frame_subject yet: tracked segments vary "
            "in size."
        )
    if counts.get("insert", 0) and counts.get("transition", 0):
        result.errors.append(
            "insert cannot combine with transition yet: dissolves "
            "across inserted footage are not supported."
        )
    if counts.get("insert", 0) and counts.get("zoom", 0):
        result.errors.append(
            "insert cannot combine with zoom yet: zooms cannot span "
            "inserted footage."
        )
    if result.errors:
        return result

    ranges = kept_ranges(ops, media.duration)
    keeps = sorted(
        (o for o in ops if o.get("op") == "keep"),
        key=lambda o: float(o["start"]),  # type: ignore[arg-type]
    )
    for first, second in zip(keeps, keeps[1:]):
        if float(second["start"]) < float(first["end"]):  # type: ignore[arg-type]
            result.errors.append("keep segments must not overlap.")
            break
    removes = sorted(
        (o for o in ops if o.get("op") == "remove"),
        key=lambda o: float(o["start"]),  # type: ignore[arg-type]
    )
    for first, second in zip(removes, removes[1:]):
        if float(second["start"]) < float(first["end"]):  # type: ignore[arg-type]
            result.errors.append("remove intervals must not overlap.")
            break
    if counts.get("zoom", 0) > 4:
        result.errors.append("At most 4 zooms are supported.")
        return result
    transition_op = next(
        (o for o in ops if o.get("op") == "transition"), None
    )
    trans_dur = (
        float(transition_op["duration"])  # type: ignore[arg-type]
        if transition_op is not None else 0.0
    )
    pieces = output_segments(ops, media.duration, trans_dur)
    out_len = segments_output_len(pieces)
    # Still-image lead-in/tail extend the final timeline: source effects
    # remap onto out_len, while output-second events (sfx, banner) use
    # the final span including the stills.
    intro_dur = sum(
        float(o["duration"])  # type: ignore[arg-type]
        for o in ops if o.get("op") == "intro"
    )
    outro_dur = sum(
        float(o["duration"])  # type: ignore[arg-type]
        for o in ops if o.get("op") == "outro"
    )
    # Inserts apply in ascending `at` on the growing timeline: each
    # `at` is content seconds including earlier inserts.
    insert_total = 0.0
    running = out_len
    for raw in sorted(
        (o for o in ops if o.get("op") == "insert"),
        key=lambda o: float(o["at"]),  # type: ignore[arg-type]
    ):
        at = float(raw["at"])  # type: ignore[arg-type]
        dur = float(raw["duration"])  # type: ignore[arg-type]
        if at > running + 1e-6:
            result.errors.append(
                f"insert at {at:.1f}s is past the {running:.1f}s timeline "
                "(content seconds); move it earlier."
            )
            break
        running += dur
        insert_total += dur
    # Full-screen cutaways must not overlap each other (ambiguous
    # picture); pips stack in plan order and may overlap.
    cutaways = sorted(
        ((i, o) for i, o in enumerate(ops) if o.get("op") == "cutaway"),
        key=lambda pair: float(pair[1]["at"]),  # type: ignore[arg-type]
    )
    for (first_i, first), (second_i, second) in zip(cutaways, cutaways[1:]):
        first_end = float(first["at"]) + float(first["duration"])  # type: ignore[arg-type]
        if float(second["at"]) < first_end - 1e-6:  # type: ignore[arg-type]
            result.errors.append(
                f"operations[{first_i}] overlaps operations[{second_i}] "
                "on the edited timeline; cutaways must not overlap."
            )
            break
    if result.errors:
        return result
    final_len = out_len + intro_dur + outro_dur + insert_total
    # Zoom is validated on the EDITED timeline: timestamps stay in source
    # seconds, but spanning a plain cut is allowed (the renderer keeps the
    # zoom continuous across the join) while overlapping zooms and zooms
    # crossing a dissolve are rejected with an actionable message.
    zoom_spans: list[tuple[str, list[tuple[float, float]]]] = []
    for index, raw in enumerate(ops):
        if raw.get("op") != "zoom":
            continue
        label = f"operations[{index}]"
        start = float(raw["start"])  # type: ignore[arg-type]
        end = float(raw["end"])  # type: ignore[arg-type]
        spans = remap_interval_output(start, end, pieces)
        if not spans:
            result.errors.append(
                f"{label} was cut from the render entirely; move the zoom "
                "into kept footage."
            )
            continue
        for first, second in zip(spans, spans[1:]):
            if second[0] < first[1] - 1e-6:
                result.errors.append(
                    f"{label} crosses a dissolve at {first[1]:.1f}s of the "
                    "edited video; split it into one zoom per side of the "
                    "transition."
                )
        kept = sum(high - low for low, high in spans)
        if kept < 0.25:
            result.warnings.append(
                f"{label} keeps only {kept:.2f}s of zoom after cuts."
            )
        zoom_spans.append((label, spans))
    for first in range(len(zoom_spans)):
        for second in range(first + 1, len(zoom_spans)):
            label_a, spans_a = zoom_spans[first]
            label_b, spans_b = zoom_spans[second]
            for low_a, high_a in spans_a:
                for low_b, high_b in spans_b:
                    if min(high_a, high_b) - max(low_a, low_b) > 1e-6:
                        result.errors.append(
                            f"{label_a} overlaps {label_b} on the edited "
                            "timeline; zooms must not overlap."
                        )
    for index, raw in enumerate(ops):
        if raw.get("op") != "sfx":
            continue
        # sfx.at is edited-output seconds (a timeline exception):
        # no remap, validated straight against the final span.
        at = float(raw["at"])  # type: ignore[arg-type]
        if at >= final_len - 1e-3:
            result.errors.append(
                f"operations[{index}].at {at:.1f}s is at or past the "
                f"{final_len:.1f}s output end (output seconds), so it "
                "would be inaudible; move it earlier."
            )
    for index, raw in enumerate(ops):
        if raw.get("op") != "banner":
            continue
        # Banner start/end are edited-output seconds, like sfx.at.
        start = float(raw["start"])  # type: ignore[arg-type]
        end = float(raw["end"])  # type: ignore[arg-type]
        if start >= final_len - 1e-3:
            result.errors.append(
                f"operations[{index}] starts at {start:.1f}s, at or past "
                f"the {final_len:.1f}s output end (output seconds); "
                "move it earlier."
            )
        elif end > final_len + 1e-6:
            result.errors.append(
                f"operations[{index}] ends at {end:.1f}s, past the "
                f"{final_len:.1f}s output end (output seconds); shorten "
                "it or the stills."
            )
    for index, raw in enumerate(ops):
        if raw.get("op") not in ("cutaway", "pip"):
            continue
        # Overlay at/duration are edited-output seconds, like sfx.at.
        at = float(raw["at"])  # type: ignore[arg-type]
        dur = float(raw["duration"])  # type: ignore[arg-type]
        if at >= final_len - 1e-3:
            result.errors.append(
                f"operations[{index}] starts at {at:.1f}s, at or past "
                f"the {final_len:.1f}s output end (output seconds); "
                "move it earlier."
            )
        elif at + dur > final_len + 1e-6:
            result.errors.append(
                f"operations[{index}] ends at {at + dur:.1f}s, past the "
                f"{final_len:.1f}s output end (output seconds); shorten "
                "it."
            )
    if counts.get("transition", 0):
        if segments:
            join_lengths = [
                float(s["end"]) - float(s["start"])  # type: ignore[arg-type]
                for s in segments
            ]
        else:
            join_lengths = [e - s for s, e in ranges]
        if len(join_lengths) < 2:
            result.errors.append(
                "transition needs at least two joined segments."
            )
        else:
            shortest = min(join_lengths)
            for raw in ops:
                if raw.get("op") != "transition":
                    continue
                if float(raw["duration"]) >= shortest:  # type: ignore[arg-type]
                    result.errors.append(
                        "transition duration must be shorter than "
                        "every joined segment."
                    )
    if result.errors:
        return result

    # Segments must sit inside the kept range and must not overlap.
    keep_start, keep_end = 0.0, media.duration
    if trims:
        keep_start = float(trims[0]["start"])
        keep_end = float(trims[0]["end"])
    ordered = sorted(segments, key=lambda s: float(s["start"]))  # type: ignore[arg-type]
    cursor = keep_start
    for raw in ordered:
        start = float(raw["start"])  # type: ignore[arg-type]
        end = float(raw["end"])  # type: ignore[arg-type]
        if start < keep_start or end > keep_end:
            result.errors.append(
                "frame_subject must sit inside the kept range "
                f"[{keep_start:.2f}, {keep_end:.2f}]."
            )
        if start < cursor:
            result.errors.append("frame_subject segments must not overlap.")
        cursor = max(cursor, end)
    if ordered and (ordered[0]["start"] != keep_start or cursor != keep_end):
        result.warnings.append(
            "Tracked segments do not cover the whole kept range; "
            "uncovered parts are cut from the render."
        )
    if result.errors:
        return result
    unsupported = obj.get("unsupported", [])
    if not isinstance(unsupported, list) or any(
        not isinstance(u, str) for u in unsupported
    ):
        result.errors.append("unsupported must be a list of strings.")
        return result
    result.plan = {
        "operations": ops,
        "notes": str(obj.get("notes", "")),
        "unsupported": [u[:200] for u in unsupported[:8]],
    }
    return result


def kept_ranges(
    ops: list[dict[str, object]], duration: float
) -> list[tuple[float, float]]:
    """Source-timeline ranges the plan keeps, in playout order.

    Exactly one cutting mode (trim, keep, remove) may be present; the
    validator enforces that before this is used for checks.
    """
    trims = [o for o in ops if o.get("op") == "trim"]
    if trims:
        return [(float(trims[0]["start"]), float(trims[0]["end"]))]  # type: ignore[arg-type]
    keeps = [o for o in ops if o.get("op") == "keep"]
    if keeps:
        ordered = sorted(keeps, key=lambda o: float(o["start"]))  # type: ignore[arg-type]
        return [(float(o["start"]), float(o["end"])) for o in ordered]  # type: ignore[arg-type]
    removes = [o for o in ops if o.get("op") == "remove"]
    if removes:
        cuts = sorted(
            (float(o["start"]), float(o["end"]))  # type: ignore[arg-type]
            for o in removes
        )
        ranges: list[tuple[float, float]] = []
        cursor = 0.0
        for start, end in cuts:
            if start > cursor:
                ranges.append((cursor, start))
            cursor = max(cursor, end)
        if cursor < duration:
            ranges.append((cursor, duration))
        return ranges
    return [(0.0, duration)]


def remap_time(
    moment: float, ranges: list[tuple[float, float]]
) -> float | None:
    """Map a source-timeline second to edited-output seconds.

    Returns None when the moment was cut. All effect timestamps in plans
    are source-referenced; the renderer remaps them with this function.
    """
    offset = 0.0
    for start, end in ranges:
        if start <= moment < end:
            return offset + (moment - start)
        offset += end - start
    if ranges and moment >= ranges[-1][1] - 1e-9:
        return offset
    return None


def remap_interval(
    start: float, end: float, ranges: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    """Clip a source interval to the kept ranges, shifted to output time."""
    pieces: list[tuple[float, float]] = []
    offset = 0.0
    for keep_start, keep_end in ranges:
        low = max(start, keep_start)
        high = min(end, keep_end)
        if high > low:
            pieces.append((offset + (low - keep_start),
                           offset + (high - keep_start)))
        offset += keep_end - keep_start
    return pieces


def content_ranges(
    ops: list[dict[str, object]], duration: float
) -> list[tuple[float, float]]:
    """Source ranges that actually reach the output, in playout order.

    Tracked frame_subject segments are the kept content when present;
    otherwise this is the cutting-mode ranges (trim/keep/remove/full).
    """
    segments = [o for o in ops if o.get("op") == "frame_subject"]
    if segments:
        ordered = sorted(segments, key=lambda o: float(o["start"]))  # type: ignore[arg-type]
        return [(float(o["start"]), float(o["end"])) for o in ordered]  # type: ignore[arg-type]
    return kept_ranges(ops, duration)


def output_segments(
    ops: list[dict[str, object]], duration: float, transition_dur: float = 0.0
) -> list[tuple[float, float, float]]:
    """(src_start, src_end, out_start) per joined piece.

    Crossfade transitions overlap joins by transition_dur, so later
    pieces start that much earlier on the output timeline.
    """
    pieces: list[tuple[float, float, float]] = []
    out = 0.0
    for index, (start, end) in enumerate(content_ranges(ops, duration)):
        if index > 0 and transition_dur > 0:
            out -= transition_dur
        pieces.append((start, end, out))
        out += end - start
    return pieces


def remap_interval_output(
    start: float, end: float, segments: list[tuple[float, float, float]]
) -> list[tuple[float, float]]:
    """Clip a source interval to joined pieces, on output time."""
    clipped: list[tuple[float, float]] = []
    for src_start, src_end, out_start in segments:
        low = max(start, src_start)
        high = min(end, src_end)
        if high > low:
            clipped.append((out_start + (low - src_start),
                            out_start + (high - src_start)))
    return clipped


def remap_moment_output(
    moment: float, segments: list[tuple[float, float, float]]
) -> float | None:
    """Map one source second to output time; None when cut."""
    for src_start, src_end, out_start in segments:
        if src_start <= moment < src_end:
            return out_start + (moment - src_start)
    if segments and abs(moment - segments[-1][1]) <= 1e-6:
        last = segments[-1]
        return last[2] + (last[1] - last[0])
    return None


def segments_output_len(
    segments: list[tuple[float, float, float]]
) -> float:
    """Total edited-output seconds covered by joined pieces."""
    total = 0.0
    for src_start, src_end, out_start in segments:
        total = max(total, out_start + (src_end - src_start))
    return total


# Catalog of operations the renderer implements. The planner prompt and
# GET /api/capabilities are generated from this single source so the
# model can never be offered an unimplemented operation.
OP_CATALOG: tuple[dict[str, object], ...] = (
    {"op": "trim", "fields": "start, end (source seconds)",
     "desc": "Keep exactly one interval."},
    {"op": "keep", "fields": "start, end; repeatable, joined in order",
     "desc": "Keep several intervals, joined in the given order."},
    {"op": "remove", "fields": "start, end; repeatable, non-overlapping",
     "desc": "Cut intervals out; the rest is joined."},
    {"op": "text",
     "fields": "text (1-140 chars), start, end, position (top/center/"
     "bottom), animation (none/fade/slide), size (small/medium/large)",
     "desc": "Timed overlay text with readable styling."},
    {"op": "zoom",
     "fields": "start, end (source seconds), to (1.0-2.5), "
     "from=1.0, x=0.5, y=0.5",
     "desc": "Smooth zoom over an interval; may span a plain cut "
     "(stays continuous), must not cross a dissolve or overlap "
     "another zoom; at most 4."},
    {"op": "grade", "fields": "preset (warm/cool/vivid/mono), intensity 0-1",
     "desc": "Color preset with adjustable intensity."},
    {"op": "transition", "fields": "type (fade), duration 0.25-1.0s",
     "desc": "Dissolve between joined segments."},
    {"op": "captions",
     "fields": 'source ("auto", transcribes speech) or segments '
     "[{start, end, text}]",
     "desc": "Phrase-level captions burned into the picture."},
    {"op": "music",
     "fields": "file_id (uploaded audio), volume_db (-30..0), duck",
     "desc": "Background music mixed under the voice; only when the "
     "instruction explicitly asks for music."},
    {"op": "sfx",
     "fields": "asset (pop/chime/whoosh/click) or file_id, "
     "at (edited-output seconds)",
     "desc": "Sound effect placed at an edited-video timestamp."},
    {"op": "intro",
     "fields": "file_id (uploaded image), duration (0.5-10s)",
     "desc": "Still-image lead-in before the video; attached in the app "
     "from a real image, never invented by the planner."},
    {"op": "outro",
     "fields": "file_id (uploaded image), duration (0.5-10s)",
     "desc": "Still-image tail after the video; attached in the app "
     "from a real image, never invented by the planner."},
    {"op": "banner",
     "fields": "file_id (uploaded image), start, end "
     "(edited-output seconds), position (top/bottom/top_left/"
     "top_right/bottom_left/bottom_right/center), width_frac=0.4 "
     "(0.1-1.0 of the frame width)",
     "desc": "Image overlaid on the edited video; attached in the app "
     "from a real image, never invented by the planner."},
    {"op": "cutaway",
     "fields": "file_id (uploaded video), at (edited-output seconds), "
     "duration (0.5-30s), src_start=0, audio (mute/mix/use)",
     "desc": "Supporting video full-screen while the main speech "
     "continues; placed in the app from a real upload, never "
     "invented by the planner."},
    {"op": "pip",
     "fields": "file_id (uploaded video), at (edited-output seconds), "
     "duration (0.5-30s), src_start=0, position (top_left/top_right/"
     "bottom_left/bottom_right), width_frac=0.25, audio (mute/mix)",
     "desc": "Smaller foreground video over the main video; placed in "
     "the app from a real upload, never invented by the planner."},
    {"op": "insert",
     "fields": "file_id (uploaded video), at (content seconds, before "
     "intro), duration (0.5-60s), src_start=0, audio (mute/use)",
     "desc": "Insert a supporting segment into the timeline, extending "
     "it; placed in the app from a real upload. Cannot combine with "
     "zoom, transition, or tracked segments."},
    {"op": "crop", "fields": "x, y, w, h (integer pixels)",
     "desc": "Crop the frame to a rectangle."},
    {"op": "frame_subject",
     "fields": "subject, start, end, aspect?",
     "desc": "Track a subject and keep it framed (needs tracking)."},
    {"op": "resize_pad",
     "fields": "aspect (original/9:16/16:9/1:1), fit (pad/crop)",
     "desc": "Output shape; pad letterboxes, crop fills."},
    {"op": "volume", "fields": "gain_db (-30..+12)",
     "desc": "Change voice loudness."},
    {"op": "fade", "fields": "in_sec (0-5), out_sec (0-5); up to two ops",
     "desc": "Fade the picture and sound in/out."},
)


def capability_catalog_text() -> str:
    """Planner-facing catalog generated from OP_CATALOG."""
    lines = ["Supported operations (every field is required unless marked "
             "optional):"]
    for entry in OP_CATALOG:
        lines.append(
            f"- {entry['op']}: {entry['fields']}. {entry['desc']}"
        )
    lines.append(
        "All times are source-footage seconds (float). Output timing is "
        "remapped automatically after cuts. Exceptions: sfx.at, banner "
        "start/end, and cutaway/pip at/duration are edited-output "
        "seconds; insert at is content seconds (after cuts and earlier "
        "inserts, before the intro still). intro, outro, banner, "
        "cutaway, pip, and insert are attached in the app from real "
        "uploads — never invent them or their file_id values; name an "
        "unattached request in \"unsupported\" instead. Never invent "
        "operations outside this list; name anything unsupported in "
        "\"unsupported\"."
    )
    return "\n".join(lines)


def _check_trim(
    label: str, raw: dict[str, object], media: MediaInfo, out: PlanValidation
) -> None:
    start, end = raw.get("start"), raw.get("end")
    if not _is_num(start) or not _is_num(end):
        out.errors.append(f"{label} needs numeric start and end.")
        return
    start_f, end_f = float(start), float(end)  # type: ignore[arg-type]
    if start_f < 0 or end_f <= start_f or end_f > media.duration + 1e-6:
        out.errors.append(
            f"{label} must satisfy 0 <= start < end <= {media.duration:.2f}."
        )


def _check_crop(
    label: str, raw: dict[str, object], media: MediaInfo, out: PlanValidation
) -> None:
    vals = [raw.get(k) for k in ("x", "y", "w", "h")]
    if any(not isinstance(v, int) or isinstance(v, bool) for v in vals):
        out.errors.append(f"{label} needs integer x, y, w, h.")
        return
    x, y, w, h = (int(v) for v in vals)  # type: ignore[arg-type]
    if w <= 0 or h <= 0 or x < 0 or y < 0:
        out.errors.append(f"{label} needs a positive in-frame rectangle.")
        return
    if x + w > media.width or y + h > media.height:
        out.errors.append(
            f"{label} exceeds the {media.width}x{media.height} frame."
        )


def _check_frame_subject(
    label: str, raw: dict[str, object], media: MediaInfo, out: PlanValidation
) -> None:
    subject = raw.get("subject")
    if not isinstance(subject, str) or not subject.strip() or len(subject) > 120:
        out.errors.append(f"{label}.subject must be 1-120 characters.")
    start, end = raw.get("start"), raw.get("end")
    if not _is_num(start) or not _is_num(end):
        out.errors.append(f"{label} needs numeric start and end.")
        return
    start_f, end_f = float(start), float(end)  # type: ignore[arg-type]
    if start_f < 0 or end_f <= start_f or end_f > media.duration + 1e-6:
        out.errors.append(
            f"{label} must satisfy 0 <= start < end <= {media.duration:.2f}."
        )


def _check_resize_pad(
    label: str, raw: dict[str, object], media: MediaInfo, out: PlanValidation
) -> None:
    if raw.get("aspect") not in ASPECTS:
        out.errors.append(f"{label}.aspect must be one of {', '.join(ASPECTS)}.")
    fit = raw.get("fit", "pad")
    if fit not in RESIZE_FITS:
        out.errors.append(
            f"{label}.fit must be one of {', '.join(RESIZE_FITS)}."
        )
    else:
        raw["fit"] = fit


def _check_volume(
    label: str, raw: dict[str, object], media: MediaInfo, out: PlanValidation
) -> None:
    gain = raw.get("gain_db")
    if not _is_num(gain):
        out.errors.append(f"{label}.gain_db must be a number.")
        return
    if not media.has_audio:
        out.errors.append(f"{label} needs an audio track, found none.")
    if float(gain) < -30 or float(gain) > 12:  # type: ignore[arg-type]
        out.errors.append(f"{label}.gain_db must be within -30..+12 dB.")


def _check_fade(
    label: str, raw: dict[str, object], media: MediaInfo, out: PlanValidation
) -> None:
    start_in, end_out = raw.get("in_sec", 0), raw.get("out_sec", 0)
    if not _is_num(start_in) or not _is_num(end_out):
        out.errors.append(f"{label} needs numeric in_sec and out_sec.")
        return
    in_f = float(start_in)  # type: ignore[arg-type]
    out_f = float(end_out)  # type: ignore[arg-type]
    if in_f < 0 or out_f < 0 or in_f > 5 or out_f > 5:
        out.errors.append(f"{label} fades must each be within 0..5 seconds.")
        return
    if in_f + out_f >= media.duration:
        out.errors.append(f"{label} fades exceed the clip duration.")


def _check_interval(
    label: str, raw: dict[str, object], media: MediaInfo, out: PlanValidation,
    what: str,
) -> tuple[float, float] | None:
    start, end = raw.get("start"), raw.get("end")
    if not _is_num(start) or not _is_num(end):
        out.errors.append(f"{label} needs numeric start and end.")
        return None
    start_f, end_f = float(start), float(end)  # type: ignore[arg-type]
    if start_f < 0 or end_f <= start_f or end_f > media.duration + 1e-6:
        out.errors.append(
            f"{label} must satisfy 0 <= start < end <= "
            f"{media.duration:.2f} ({what}, source seconds)."
        )
        return None
    return start_f, end_f


def _check_keep(
    label: str, raw: dict[str, object], media: MediaInfo, out: PlanValidation
) -> None:
    _check_interval(label, raw, media, out, "kept segment")


def _check_remove(
    label: str, raw: dict[str, object], media: MediaInfo, out: PlanValidation
) -> None:
    _check_interval(label, raw, media, out, "removed interval")


def _check_text(
    label: str, raw: dict[str, object], media: MediaInfo, out: PlanValidation
) -> None:
    text = raw.get("text")
    if not isinstance(text, str) or not text.strip():
        out.errors.append(f"{label}.text must be 1-140 characters.")
    elif len(text) > 140:
        out.errors.append(f"{label}.text must be 1-140 characters.")
    elif any(ord(c) < 32 and c not in "\n\t" for c in text):
        out.errors.append(f"{label}.text must not hold control characters.")
    _check_interval(label, raw, media, out, "overlay timing")
    if raw.get("position") not in TEXT_POSITIONS:
        out.errors.append(
            f"{label}.position must be one of {', '.join(TEXT_POSITIONS)}."
        )
    if raw.get("animation") not in TEXT_ANIMATIONS:
        out.errors.append(
            f"{label}.animation must be one of "
            f"{', '.join(TEXT_ANIMATIONS)}."
        )
    if raw.get("size") not in TEXT_SIZES:
        out.errors.append(
            f"{label}.size must be one of {', '.join(TEXT_SIZES)}."
        )


def _check_zoom(
    label: str, raw: dict[str, object], media: MediaInfo, out: PlanValidation
) -> None:
    span = _check_interval(label, raw, media, out, "zoom timing")
    if span is not None and span[1] - span[0] < 0.25:
        out.errors.append(f"{label} must last at least 0.25 seconds.")
    to = raw.get("to")
    if not _is_num(to):
        out.errors.append(f"{label}.to must be a number.")
        return
    to_f = float(to)  # type: ignore[arg-type]
    if to_f < 1.0 or to_f > 2.5:
        out.errors.append(f"{label}.to must be within 1.0..2.5.")
    from_f = 1.0
    if raw.get("from") is not None:
        if not _is_num(raw.get("from")):
            out.errors.append(f"{label}.from must be a number.")
            return
        from_f = float(raw["from"])  # type: ignore[arg-type]
        if from_f < 1.0 or from_f > 2.5:
            out.errors.append(f"{label}.from must be within 1.0..2.5.")
    else:
        raw["from"] = 1.0
    if abs(to_f - from_f) < 1e-6:
        out.errors.append(f"{label} needs different from and to values.")
    for key in ("x", "y"):
        if raw.get(key) is None:
            raw[key] = 0.5
            continue
        if not _is_num(raw.get(key)):
            out.errors.append(f"{label}.{key} must be a number.")
            return
        point = float(raw[key])  # type: ignore[arg-type]
        if point < 0.0 or point > 1.0:
            out.errors.append(f"{label}.{key} must be within 0..1.")


def _check_grade(
    label: str, raw: dict[str, object], media: MediaInfo, out: PlanValidation
) -> None:
    if raw.get("preset") not in GRADE_PRESETS:
        out.errors.append(
            f"{label}.preset must be one of {', '.join(GRADE_PRESETS)}."
        )
    intensity = raw.get("intensity")
    if not _is_num(intensity):
        out.errors.append(f"{label}.intensity must be a number.")
        return
    level = float(intensity)  # type: ignore[arg-type]
    if level < 0.0 or level > 1.0:
        out.errors.append(f"{label}.intensity must be within 0..1.")


def _check_transition(
    label: str, raw: dict[str, object], media: MediaInfo, out: PlanValidation
) -> None:
    if raw.get("type") not in TRANSITION_TYPES:
        out.errors.append(
            f"{label}.type must be one of {', '.join(TRANSITION_TYPES)}."
        )
    duration = raw.get("duration")
    if not _is_num(duration):
        out.errors.append(f"{label}.duration must be a number.")
        return
    seconds = float(duration)  # type: ignore[arg-type]
    if seconds < 0.25 or seconds > 1.0:
        out.errors.append(f"{label}.duration must be within 0.25..1.0s.")


def _check_captions(
    label: str, raw: dict[str, object], media: MediaInfo, out: PlanValidation
) -> None:
    source, segments = raw.get("source"), raw.get("segments")
    if (source is None) == (segments is None):
        out.errors.append(
            f"{label} needs exactly one of source or segments."
        )
        return
    if source is not None:
        if source != "auto":
            out.errors.append(f"{label}.source must be \"auto\".")
        elif not media.has_audio:
            out.errors.append(
                f"{label} needs an audio track to transcribe, found none."
            )
        return
    if not isinstance(segments, list) or not segments:
        out.errors.append(f"{label}.segments must be a non-empty list.")
        return
    if len(segments) > 120:
        out.errors.append(f"{label}.segments holds too many (max 120).")
        return
    for index, entry in enumerate(segments):
        tag = f"{label}.segments[{index}]"
        if not isinstance(entry, dict):
            out.errors.append(f"{tag} must be an object.")
            continue
        start, end = entry.get("start"), entry.get("end")
        if not _is_num(start) or not _is_num(end):
            out.errors.append(f"{tag} needs numeric start and end.")
            continue
        start_f, end_f = float(start), float(end)  # type: ignore[arg-type]
        if start_f < 0 or end_f <= start_f or end_f > media.duration + 1e-6:
            out.errors.append(
                f"{tag} must satisfy 0 <= start < end <= "
                f"{media.duration:.2f} (source seconds)."
            )
        text = entry.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 200:
            out.errors.append(f"{tag}.text must be 1-200 characters.")


_FILE_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _check_music(
    label: str, raw: dict[str, object], media: MediaInfo, out: PlanValidation
) -> None:
    file_id = raw.get("file_id")
    if not isinstance(file_id, str) or not _FILE_ID_RE.match(file_id):
        out.errors.append(f"{label}.file_id must be an uploaded audio id.")
    gain = raw.get("volume_db")
    if not _is_num(gain):
        out.errors.append(f"{label}.volume_db must be a number.")
    elif float(gain) < -30 or float(gain) > 0:  # type: ignore[arg-type]
        out.errors.append(f"{label}.volume_db must be within -30..0 dB.")
    if not isinstance(raw.get("duck"), bool):
        out.errors.append(f"{label}.duck must be true or false.")
    elif raw.get("duck") and not media.has_audio:
        out.warnings.append(
            f"{label}.duck has no source voice to duck under; the music "
            "plays at the set level."
        )


def _check_sfx(
    label: str, raw: dict[str, object], media: MediaInfo, out: PlanValidation
) -> None:
    asset, file_id = raw.get("asset"), raw.get("file_id")
    if (asset is None) == (file_id is None):
        out.errors.append(
            f"{label} needs exactly one of asset or file_id."
        )
    elif asset is not None and asset not in SFX_ASSETS:
        out.errors.append(
            f"{label}.asset must be one of {', '.join(SFX_ASSETS)}."
        )
    elif file_id is not None and (
        not isinstance(file_id, str) or not _FILE_ID_RE.match(file_id)
    ):
        out.errors.append(f"{label}.file_id must be an uploaded audio id.")
    at = raw.get("at")
    if not _is_num(at):
        out.errors.append(f"{label}.at must be a number.")
        return
    # Upper bound needs the whole plan's output span; the cross-op
    # check below enforces it in output seconds.
    if float(at) < 0:  # type: ignore[arg-type]
        out.errors.append(f"{label}.at must be 0 or later.")


def _check_still(
    label: str, raw: dict[str, object], media: MediaInfo, out: PlanValidation,
    what: str, low: float, high: float,
) -> None:
    file_id = raw.get("file_id")
    if not isinstance(file_id, str) or not _FILE_ID_RE.match(file_id):
        out.errors.append(f"{label}.file_id must be an uploaded image id.")
    duration = raw.get("duration")
    if not _is_num(duration):
        out.errors.append(f"{label}.duration must be a number.")
        return
    seconds = float(duration)  # type: ignore[arg-type]
    if seconds < low or seconds > high:
        out.errors.append(
            f"{label}.duration must be within {low:.1f}..{high:.1f}s."
        )


def _check_intro(
    label: str, raw: dict[str, object], media: MediaInfo, out: PlanValidation
) -> None:
    _check_still(label, raw, media, out, "intro",
                 INTRO_MIN_SEC, INTRO_MAX_SEC)


def _check_outro(
    label: str, raw: dict[str, object], media: MediaInfo, out: PlanValidation
) -> None:
    _check_still(label, raw, media, out, "outro",
                 OUTRO_MIN_SEC, OUTRO_MAX_SEC)


def _check_support_common(
    label: str, raw: dict[str, object], out: PlanValidation,
    audio_modes: tuple[str, ...], max_sec: float, dur_label: str,
) -> tuple[float, float] | None:
    """Shared file_id/at/duration/src_start/audio checks. Returns
    (at, duration) on success, None after recording errors."""
    file_id = raw.get("file_id")
    if not isinstance(file_id, str) or not _FILE_ID_RE.match(file_id):
        out.errors.append(
            f"{label}.file_id must be an uploaded video id."
        )
        return None
    at, duration = raw.get("at"), raw.get("duration")
    if not _is_num(at) or not _is_num(duration):
        out.errors.append(
            f"{label} needs numeric at and duration."
        )
        return None
    at_f = float(at)  # type: ignore[arg-type]
    dur_f = float(duration)  # type: ignore[arg-type]
    if at_f < 0:
        out.errors.append(f"{label}.at must be >= 0 ({dur_label}).")
        return None
    if dur_f < 0.5 or dur_f > max_sec:
        out.errors.append(
            f"{label}.duration must be within 0.5..{max_sec:.0f}s."
        )
        return None
    src = raw.get("src_start", 0.0)
    if not _is_num(src) or float(src) < 0:  # type: ignore[arg-type]
        out.errors.append(f"{label}.src_start must be >= 0.")
        return None
    raw["src_start"] = float(src)  # type: ignore[arg-type]
    audio = raw.get("audio", "mute")
    if audio not in audio_modes:
        out.errors.append(
            f"{label}.audio must be one of {', '.join(audio_modes)}."
        )
        return None
    return at_f, dur_f


def _check_overlay_frac(
    label: str, raw: dict[str, object], out: PlanValidation,
    default: float,
) -> float | None:
    frac = raw.get("width_frac", default)
    if not _is_num(frac):
        out.errors.append(f"{label}.width_frac must be a number.")
        return None
    frac_f = float(frac)  # type: ignore[arg-type]
    if frac_f < BANNER_MIN_FRAC or frac_f > BANNER_MAX_FRAC:
        out.errors.append(
            f"{label}.width_frac must be within "
            f"{BANNER_MIN_FRAC:.1f}..{BANNER_MAX_FRAC:.1f}."
        )
        return None
    raw["width_frac"] = frac_f
    return frac_f


def _check_cutaway(
    label: str, raw: dict[str, object], media: MediaInfo, out: PlanValidation
) -> None:
    _check_support_common(
        label, raw, out, CUTAWAY_AUDIO, OVERLAY_MAX_SEC, "output seconds"
    )


def _check_pip(
    label: str, raw: dict[str, object], media: MediaInfo, out: PlanValidation
) -> None:
    if _check_support_common(
        label, raw, out, PIP_AUDIO, OVERLAY_MAX_SEC, "output seconds"
    ) is None:
        return
    if raw.get("position") not in PIP_POSITIONS:
        out.errors.append(
            f"{label}.position must be one of "
            f"{', '.join(PIP_POSITIONS)}."
        )
        return
    _check_overlay_frac(label, raw, out, PIP_DEFAULT_FRAC)


def _check_insert(
    label: str, raw: dict[str, object], media: MediaInfo, out: PlanValidation
) -> None:
    _check_support_common(
        label, raw, out, INSERT_AUDIO, INSERT_MAX_SEC, "content seconds"
    )


def _check_banner(
    label: str, raw: dict[str, object], media: MediaInfo, out: PlanValidation
) -> None:
    file_id = raw.get("file_id")
    if not isinstance(file_id, str) or not _FILE_ID_RE.match(file_id):
        out.errors.append(f"{label}.file_id must be an uploaded image id.")
    start, end = raw.get("start"), raw.get("end")
    if not _is_num(start) or not _is_num(end):
        out.errors.append(
            f"{label} needs numeric start and end (output seconds)."
        )
    else:
        start_f = float(start)  # type: ignore[arg-type]
        end_f = float(end)  # type: ignore[arg-type]
        # Upper bound needs the whole plan's final span (content plus
        # intro/outro); the cross-op check below enforces it.
        if start_f < 0 or end_f <= start_f:
            out.errors.append(
                f"{label} must satisfy 0 <= start < end (output seconds)."
            )
    if raw.get("position") not in BANNER_POSITIONS:
        out.errors.append(
            f"{label}.position must be one of "
            f"{', '.join(BANNER_POSITIONS)}."
        )
    frac = raw.get("width_frac", BANNER_DEFAULT_FRAC)
    if not _is_num(frac):
        out.errors.append(f"{label}.width_frac must be a number.")
        return
    frac_f = float(frac)  # type: ignore[arg-type]
    if frac_f < BANNER_MIN_FRAC or frac_f > BANNER_MAX_FRAC:
        out.errors.append(
            f"{label}.width_frac must be within "
            f"{BANNER_MIN_FRAC:.1f}..{BANNER_MAX_FRAC:.1f}."
        )
    else:
        raw["width_frac"] = frac_f


_CHECKS = {
    "trim": _check_trim,
    "crop": _check_crop,
    "frame_subject": _check_frame_subject,
    "resize_pad": _check_resize_pad,
    "volume": _check_volume,
    "fade": _check_fade,
    "keep": _check_keep,
    "remove": _check_remove,
    "text": _check_text,
    "zoom": _check_zoom,
    "grade": _check_grade,
    "transition": _check_transition,
    "captions": _check_captions,
    "music": _check_music,
    "sfx": _check_sfx,
    "intro": _check_intro,
    "outro": _check_outro,
    "banner": _check_banner,
    "cutaway": _check_cutaway,
    "pip": _check_pip,
    "insert": _check_insert,
}


def build_plan_prompt(
    instruction: str,
    media: MediaInfo,
    style: str = "",
    aspect_ratio: str = "original",
    footage_notes: str = "",
) -> list[dict[str, object]]:
    """System + user messages asking Muse Spark for an editing plan."""
    system = (
        "You are a video editing planner for the 23Editor app. "
        "Convert the user's instruction into editing operations. "
        "Output JSON only, matching the provided schema. "
        "Every operation MUST include ALL of its required fields with "
        "the exact JSON types shown; never omit a required field and "
        "never use strings for numbers: "
        "trim needs numeric start/end seconds; "
        "crop needs integer x/y/w/h pixels; "
        "frame_subject needs a subject phrase plus numeric start/end seconds; "
        "resize_pad needs aspect, exactly one of original, 9:16, 16:9, 1:1; "
        "volume needs gain_db; "
        "fade takes optional in_sec/out_sec. "
        "Example: keep only the first 5 seconds is "
        '{"operations": [{"op": "trim", "start": 0.0, "end": 5.0}]}. '
        "Rules: keep every time within the media duration; keep every "
        "crop inside the frame; change audio only when asked, otherwise "
        "leave it untouched so the original voice is preserved; never "
        "invent content; prefer fewer operations; add only operations the "
        "instruction asks for. Never add captions, music, sfx, zoom, "
        "grade, fade, or volume unless the instruction explicitly asks "
        "for that effect. Never add resize_pad: the requested aspect "
        "ratio is applied automatically after your plan. "
        "A style preset's effects are applied automatically after your "
        "plan; do not duplicate them. "
        "If the instruction asks for effects outside the catalog below, "
        "still return the supported operations and name each unsupported "
        "effect briefly in \"unsupported\" — never invent an operation. "
        "Music and sound effects come only from the user's uploads or "
        "the named bundled assets; never claim to generate custom audio. "
        "For 'clean up', 'clean take', or 'remove mistakes' requests: cut "
        "only spans where the evidence agrees — transcript wording and "
        "timing plus audio or visual signals (a pause, a repeated take, "
        "a false start, an interruption). Never delete a moment merely "
        "because someone smiles, moves, or raises their voice; preserve "
        "intentional laughter, meaningful pauses, and every spoken word "
        "that carries meaning. When a cut is uncertain, keep the "
        "material and name the span with its reason in \"unsupported\" "
        "so the creator can review it instead."
        "\n" + capability_catalog_text()
    )
    user = (
        f"Media: duration={media.duration:.2f}s, frame={media.width}x{media.height}, "
        f"fps={media.fps:.2f}, audio={'yes' if media.has_audio else 'no'}. "
        f"Requested style: {style or 'none'}. "
        f"Requested aspect: {aspect_ratio}. "
        f"Instruction: {instruction.strip()}"
    )
    if footage_notes.strip():
        user += f" Footage notes (sampled frames, do not invent more): {footage_notes.strip()}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
