"""Muse Voice Transcribe helpers.

Verified against https://dev.meta.ai/docs/speech-to-text (Nov 2026):
- 25 supported languages with code-switching; Tamil is included,
  Sinhala is NOT in the documented list.
- Only turn-level timestamps exist; word-level timestamps and confidence
  scores are explicitly unavailable.
- File endpoint takes mono 16-bit PCM WAV at 16/24 kHz, max 32 MB,
  max 10 minutes per request.
- JSON (non-stream) responses always carry ``transcript``; ``turns`` is
  empty in PUSH_TO_TALK mode.
"""
from __future__ import annotations

import re

from meta_client import MODEL_TRANSCRIBE

SUPPORTED_LANGUAGES = (
    "Arabic",
    "Bengali",
    "Dutch",
    "English",
    "French",
    "German",
    "Hebrew",
    "Hindi",
    "Indonesian",
    "Italian",
    "Japanese",
    "Kannada",
    "Korean",
    "Malay",
    "Mandarin Chinese",
    "Marathi",
    "Polish",
    "Portuguese",
    "Spanish",
    "Tagalog",
    "Tamil",
    "Telugu",
    "Thai",
    "Turkish",
    "Vietnamese",
)

MODES = ("PUSH_TO_TALK", "ENDPOINTING", "DIARIZATION")

MAX_WAV_BYTES = 32 * 1024 * 1024
MAX_AUDIO_SECONDS = 10 * 60


def canonical_languages(names: list[str]) -> list[str]:
    """Match user language names against the documented 25, case-insensitively."""
    canon = {name.lower(): name for name in SUPPORTED_LANGUAGES}
    matched: list[str] = []
    for raw in names:
        hit = canon.get(raw.strip().lower())
        if hit is None:
            raise ValueError(
                f"Language {raw!r} is not in Meta's documented list of 25. "
                "Supported: " + ", ".join(SUPPORTED_LANGUAGES) + "."
            )
        if hit not in matched:
            matched.append(hit)
    return matched


def build_request_part(
    mode: str = "ENDPOINTING",
    languages: list[str] | None = None,
    keywords: list[str] | None = None,
) -> dict[str, object]:
    """Build the JSON ``request`` part for POST /v1/asr/transcribe."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    part: dict[str, object] = {
        "mode": mode,
        "model": MODEL_TRANSCRIBE,
        "audioEncoding": "WAV",
    }
    if languages:
        part["languageBias"] = canonical_languages(languages)
    if keywords:
        clean = [k.strip() for k in keywords if k.strip()][:20]
        if clean:
            part["keywords"] = clean
    return part


_CONTROL_TOKEN_RE = re.compile(r"<\|[^|<>]*\|>")


def strip_control_tokens(text: str) -> str:
    """Remove provider control tokens such as <|speaker_A|> from display text.

    Actual speech (including Tamil/English Unicode) is preserved; only the
    <|...|> markers go, with leftover whitespace collapsed.
    """
    cleaned = _CONTROL_TOKEN_RE.sub("", text)
    return re.sub(r"[ \t]+", " ", cleaned).strip()


def parse_transcript(response: dict[str, object]) -> dict[str, object]:
    """Reduce a transcribe JSON response to text + turns.

    Unknown turn fields are preserved verbatim under ``raw`` so nothing
    documented is lost and nothing undocumented is invented. Provider
    control tokens (<|speaker_A|> and friends) are stripped from the
    surfaced text so they never reach captions.
    """
    text = response.get("transcript")
    if not isinstance(text, str):
        raise ValueError("Transcription response has no transcript.")
    raw_turns = response.get("turns", [])
    if not isinstance(raw_turns, list):
        raise ValueError("Transcription response has invalid turns.")
    turns: list[dict[str, object]] = []
    for entry in raw_turns:
        if not isinstance(entry, dict):
            continue
        turn: dict[str, object] = {"raw": dict(entry)}
        if isinstance(entry.get("turnId"), int):
            turn["turn_id"] = entry["turnId"]
        if isinstance(entry.get("transcript"), str):
            cleaned = strip_control_tokens(entry["transcript"])
            if cleaned:
                turn["text"] = cleaned
        if isinstance(entry.get("speaker"), str):
            turn["speaker"] = entry["speaker"]
        turns.append(turn)
    return {"text": strip_control_tokens(text), "turns": turns}
