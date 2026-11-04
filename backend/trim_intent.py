"""Explicit first-N-seconds trim intent parsed from the instruction.

A plan with no trim expects the full source duration, so without this
module an unambiguous request like "keep only the first 5 seconds" can
silently render uncut when the model omits the trim. These helpers close
that gap: they recognize ONLY unambiguous first-N-seconds requests (in
English and Tamil) and reconcile the model plan to the requested
interval. Anything ambiguous returns None and is never guessed.
"""
from __future__ import annotations

import re

_EN_WORDS: dict[str, float] = {
    "one": 1.0, "two": 2.0, "three": 3.0, "four": 4.0, "five": 5.0,
    "six": 6.0, "seven": 7.0, "eight": 8.0, "nine": 9.0, "ten": 10.0,
    "eleven": 11.0, "twelve": 12.0,
}

_TA_WORDS: dict[str, float] = {
    "ஒரு": 1.0, "ஒன்று": 1.0, "இரண்டு": 2.0, "மூன்று": 3.0,
    "நான்கு": 4.0, "ஐந்து": 5.0, "ஆறு": 6.0, "ஏழு": 7.0,
    "எட்டு": 8.0, "ஒன்பது": 9.0, "பத்து": 10.0,
}

_TA_DIGITS = str.maketrans("௦௧௨௩௪௫௬௭௮௯", "0123456789")

_EN_RE = re.compile(
    r"\bfirst\s+(?P<num>[0-9]+(?:\.[0-9]+)?|one|two|three|four|five|six|"
    r"seven|eight|nine|ten|eleven|twelve)\s*(?P<unit>seconds?|secs?)\b",
    re.IGNORECASE,
)

_TA_RE = re.compile(
    r"முதல்\s*(?P<num>[0-9]+(?:\.[0-9]+)?|ஒரு|ஒன்று|இரண்டு|மூன்று|"
    r"நான்கு|ஐந்து|ஆறு|ஏழு|எட்டு|ஒன்பது|பத்து)\s*வி[னந]ாடி"
)

_MINUTES_EN_RE = re.compile(
    r"\bfirst\s+(?P<num>[0-9]+(?:\.[0-9]+)?|one|two|three|four|five|six|"
    r"seven|eight|nine|ten|eleven|twelve)\s*minutes?\b",
    re.IGNORECASE,
)

_MINUTES_TA_RE = re.compile(
    r"முதல்\s*(?P<num>[0-9]+(?:\.[0-9]+)?|ஒரு|ஒன்று|இரண்டு|மூன்று|"
    r"நான்கு|ஐந்து|ஆறு|ஏழு|எட்டு|ஒன்பது|பத்து)\s*நிமிட(?:ம்|ங்கள்)"
)

_VISUAL_RE = re.compile(
    r"\b(?:appear|appears|appearing|show|shows|showing|shown|scene|"
    r"product|person|people|enter|enters|entering|leave|leaves|"
    r"leaving|arrive|arrives|visible|object)\b",
    re.IGNORECASE,
)

_VISUAL_TA_RE = re.compile(r"(?:தோன்ற|காட்சி|பொருள்|நபர்)")

_TIME_HINT_RE = re.compile(
    r"[0-9]+(?:\.[0-9]+)?\s*(?:seconds?|secs?|minutes?|mins?|"
    r"வி[னந]ாடி|நிமிட(?:ம்|ங்கள்))"
)

_CUT_RANGE_RE = re.compile(
    r"\b(?P<verb>keep|remove|cut|delete)\s+"
    r"(?:only\s+|the\s+|from\s+|section\s+|part\s+|portion\s+|"
    r"segment\s+|scene\s+|clip\s+)*"
    r"(?:(?P<pre>seconds?|secs?|வி[னந]ாடி)\s+)?"
    r"(?P<a>[0-9]+(?:\.[0-9]+)?)"
    r"(?:\s*(?:seconds?|secs?|வி[னந]ாடி))?"
    r"\s*(?:-|–|—|\bto\b)\s*"
    r"(?P<b>[0-9]+(?:\.[0-9]+)?)"
    r"(?:\s*(?P<post>seconds?|secs?|வி[னந]ாடி))?",
    re.IGNORECASE,
)


def _to_number(token: str, words: dict[str, float]) -> float | None:
    token = token.strip().lower()
    if token in words:
        return words[token]
    try:
        return float(token)
    except ValueError:
        return None


def parse_first_seconds(instruction: str) -> float | None:
    """Seconds N for an unambiguous "first N seconds" request, else None.

    Requires the word "first" (முதல்) plus an explicit seconds unit, so
    phrases like "cut 5 seconds from the middle" or "first 5 minutes"
    never match. Conflicting values ("first 5 ... first 10") and
    non-positive values also return None.
    """
    if not instruction:
        return None
    text = instruction.translate(_TA_DIGITS)
    values: set[float] = set()
    for match in _EN_RE.finditer(text):
        value = _to_number(match.group("num"), _EN_WORDS)
        if value is not None:
            values.add(value)
    for match in _TA_RE.finditer(text):
        value = _to_number(match.group("num"), _TA_WORDS)
        if value is not None:
            values.add(value)
    if len(values) != 1:
        return None
    seconds = values.pop()
    return seconds if seconds > 0 else None


def _spans_overlap(spans: list[tuple[float, float]]) -> bool:
    """True when any two spans truly overlap (touching is fine)."""
    ordered = sorted(spans)
    for first, second in zip(ordered, ordered[1:]):
        if second[0] < first[1] - 1e-9:
            return True
    return False


def parse_cut_ranges(
    instruction: str,
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]] | None:
    """Explicit (keeps, removes) second-ranges, else None.

    Recognizes ONLY unambiguous ranged requests such as "keep only
    seconds 0-3", "remove 3 to 12 seconds", or "keep only the
    section from 2 seconds to 15 seconds" (digits or Tamil digits,
    seconds unit before, inside, or after the range). A range
    without a seconds unit never matches, so minute values are
    never mistaken for seconds. Overlapping ranges and reversed
    ranges return None and are never guessed; repeats collapse.
    """
    if not instruction:
        return None
    text = instruction.translate(_TA_DIGITS)
    keeps: list[tuple[float, float]] = []
    removes: list[tuple[float, float]] = []
    for match in _CUT_RANGE_RE.finditer(text):
        if match.group("pre") is None and match.group("post") is None:
            continue
        try:
            span = (float(match.group("a")), float(match.group("b")))
        except ValueError:
            return None
        if not 0 <= span[0] < span[1]:
            return None
        bucket = (
            keeps if match.group("verb").lower() == "keep" else removes
        )
        if span not in bucket:
            bucket.append(span)
    if not keeps and not removes:
        return None
    if _spans_overlap(keeps) or _spans_overlap(removes):
        return None
    for keep in keeps:
        for remove in removes:
            if max(keep[0], remove[0]) < min(keep[1], remove[1]) - 1e-9:
                return None
    return sorted(keeps), sorted(removes)


def minutes_clarification(instruction: str) -> str | None:
    """Seconds-vs-minutes question for "first N minutes", else None.

    A bare minutes request is never silently converted; the caller asks
    first so a 5-minute request is not rendered as 5 seconds.
    """
    if not instruction:
        return None
    text = instruction.translate(_TA_DIGITS)
    match = _MINUTES_EN_RE.search(text)
    words: dict[str, float] = _EN_WORDS
    if match is None:
        match = _MINUTES_TA_RE.search(text)
        words = _TA_WORDS
    if match is None:
        return None
    value = _to_number(match.group("num"), words)
    if value is None or value <= 0:
        return None
    return (
        f"Did you mean the first {value:g} seconds, or the first "
        f"{value:g} minutes ({value * 60:g} seconds)? Reply with the "
        "unit spelled out and I will apply it."
    )


def needs_visual_analysis(instruction: str) -> bool:
    """True when the instruction names a visual event without timestamps.

    "Zoom when the product appears" needs sampled frames; "zoom between
    seconds 3 and 5" does not.
    """
    if not instruction:
        return False
    text = instruction.translate(_TA_DIGITS)
    if _TIME_HINT_RE.search(text):
        return False
    return bool(_VISUAL_RE.search(text) or _VISUAL_TA_RE.search(text))


_EXTRA_KEYWORDS: dict[str, tuple[str, ...]] = {
    # Substring cues (lowercased) that an extra was explicitly requested.
    # Only consulted to KEEP a model-suggested extra; absence drops it.
    "captions": ("caption", "subtitle", "சப்டைட்டில்", "வசன வரி"),
    "music": ("music", "song", "background track", "பாடல்", "இசை",
              "மியூசிக்"),
    "sfx": ("sound effect", "sound fx", "sfx"),
    "zoom": ("zoom", "ஜூம்"),
    "fade": ("fade",),
    "grade": ("grade", "colour grade", "color grade", "warm tone",
              "cool tone", "vivid", "black and white",
              "black-and-white", "cinematic look"),
    "volume": ("volume", "louder", "quieter", "gain"),
}


def mentions_extra(instruction: str, kind: str) -> bool:
    """True when the instruction explicitly asks for an extra kind.

    ``kind`` is one of captions/music/sfx/zoom/fade/grade/volume.
    Matching is a case-insensitive substring search over a small
    English + Tamil cue list; unknown kinds never match.
    """
    if not instruction:
        return False
    text = instruction.lower()
    return any(cue in text for cue in _EXTRA_KEYWORDS.get(kind, ()))


def required_trim_end(instruction: str, duration: float) -> float | None:
    """Trim end seconds the instruction explicitly requires, if any.

    Returns None when the instruction has no unambiguous first-N-seconds
    request, or when N covers the whole clip (no trim needed).
    """
    seconds = parse_first_seconds(instruction)
    if seconds is None:
        return None
    if seconds >= duration - 1e-6:
        return None
    return seconds


def ensure_first_seconds_trim(
    plan: dict[str, object], end: float, duration: float
) -> tuple[dict[str, object], str | None]:
    """Reconcile a validated plan with the requested 0-end trim interval.

    Returns (plan, note). The note is None when the plan already holds
    exactly that cut. An explicit first-N request overrides model cuts
    of any mode (trim, keep, remove): the model must not silently keep
    other footage. The caller applies UI explicit cuts first and skips
    this when those are present, then re-validates afterwards, since
    a narrowed trim can conflict with tracked segments.
    """
    ops = plan.get("operations")
    assert isinstance(ops, list)
    label = f"{end:g}s"
    cutting = [
        o for o in ops
        if isinstance(o, dict) and o.get("op") in ("trim", "keep", "remove")
    ]
    if len(cutting) == 1 and cutting[0].get("op") == "keep":
        start = float(cutting[0]["start"])  # type: ignore[arg-type]
        current = float(cutting[0]["end"])  # type: ignore[arg-type]
        if start == 0.0 and abs(current - end) < 1e-6:
            return plan, None
    if any(
        isinstance(o, dict) and o.get("op") in ("keep", "remove")
        for o in ops
    ):
        # Model multi-segment cuts stand aside for the explicit
        # request; UI explicit cuts never reach here (route guard).
        first = next(
            i for i, o in enumerate(ops)
            if isinstance(o, dict)
            and o.get("op") in ("trim", "keep", "remove")
        )
        rest = [
            o for o in ops
            if not (isinstance(o, dict)
                    and o.get("op") in ("trim", "keep", "remove"))
        ]
        rest.insert(first, {
            "op": "trim", "start": 0.0, "end": end,
            "origin": "instruction",
        })
        plan["operations"] = rest
        return plan, (
            f"Set the trim to the requested first-{label} "
            f"(0.0s-{end:.1f}s); the model's keep/remove cut "
            "was replaced."
        )
    trims = [o for o in ops if isinstance(o, dict) and o.get("op") == "trim"]
    if not trims:
        ops.insert(0, {
            "op": "trim", "start": 0.0, "end": end,
            "origin": "instruction",
        })
        return plan, (
            f"Added the requested first-{label} trim (0.0s-{end:.1f}s); "
            "the model plan omitted it."
        )
    trim = trims[0]
    start = float(trim["start"])  # type: ignore[arg-type]
    current = float(trim["end"])  # type: ignore[arg-type]
    if start == 0.0 and abs(current - end) < 1e-6:
        return plan, None
    trim["start"] = 0.0
    trim["end"] = end
    if start <= 0.0 and current >= duration - 1e-6:
        reason = "covered the full video"
    else:
        reason = f"was {start:.1f}s-{current:.1f}s"
    return plan, (
        f"Set the trim to the requested first-{label} (0.0s-{end:.1f}s); "
        f"the model trim {reason}."
    )
