"""Footage analysis: ffmpeg-measured signals plus transcript context.

There is deliberately no Meta vision call here: the verified Meta
endpoints in this project are text planning, speech transcription,
subject tracking, and image generation. Everything visual below is
measured locally (streams, scene cuts, audio levels, sampled frames)
and every interpretation is labeled with its uncertainty, so the app
can show observations without inventing products, dialogue, or events.
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
import uuid

MAX_FRAMES = 8
MIN_FRAME_GAP = 2.0
MAX_CUTS_REPORTED = 60
MAX_PAUSES_REPORTED = 20
PAUSE_MIN_SEC = 1.0
SCENE_THRESHOLD = 0.4
SILENCE_MEAN_DB = -60.0


class Cancelled(Exception):
    """Raised when an analysis job is cancelled between stages."""


class Job:
    """One cancellable analysis run."""

    def __init__(self, job_id: str, file_id: str) -> None:
        self.id = job_id
        self.file_id = file_id
        self.state = "queued"  # queued|running|done|error|cancelled
        self.stage = "Waiting to start."
        self.result: dict[str, object] | None = None
        self.error = ""
        self._cancel = threading.Event()

    def cancel(self) -> bool:
        """Request cancellation; True when the job was still active."""
        if self.state in ("done", "error", "cancelled"):
            return False
        self._cancel.set()
        return True

    def check(self) -> None:
        if self._cancel.is_set():
            raise Cancelled()

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "job_id": self.id, "state": self.state, "stage": self.stage,
        }
        if self.result is not None:
            payload["result"] = self.result
        if self.error:
            payload["error"] = self.error
        return payload


class JobManager:
    """Thread-safe registry of analysis jobs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}

    def start(self, file_id: str, runner) -> Job:
        job = Job(uuid.uuid4().hex, file_id)

        def _run() -> None:
            try:
                job.state = "running"
                job.result = runner(job)
                if job.state == "running":
                    job.state = "done"
                    job.stage = "Analysis complete."
            except Cancelled:
                job.state = "cancelled"
                job.stage = "Cancelled."
            except Exception as exc:  # surfaced via the job, not raised
                job.state = "error"
                job.error = f"{type(exc).__name__}: {exc}"
                job.stage = "Analysis failed."

        with self._lock:
            self._jobs[job.id] = job
        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        return job.cancel() if job is not None else False


def frame_times(duration: float) -> list[float]:
    """Adaptive sample timestamps: spread out, capped, honestly sparse."""
    if duration <= 0:
        return []
    count = min(MAX_FRAMES, max(1, int(duration / 3)))
    if count == 1:
        return [round(duration / 2, 2)]
    return [
        round(duration * (i + 1) / (count + 1), 2) for i in range(count)
    ]


def audio_levels(
    ffmpeg_bin: str, src_path: str, timeout: float = 120.0
) -> dict[str, float] | None:
    """Mean/max volume in dB, or None when there is no audio stream."""
    done = subprocess.run(
        [ffmpeg_bin, "-v", "info", "-i", src_path,
         "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, timeout=timeout, check=False,
    )
    mean = maximum = None
    for line in done.stderr.splitlines():
        if "mean_volume" in line:
            try:
                mean = float(line.split("mean_volume:")[1].split("dB")[0])
            except (ValueError, IndexError):
                pass
        if "max_volume" in line:
            try:
                maximum = float(line.split("max_volume:")[1].split("dB")[0])
            except (ValueError, IndexError):
                pass
    if mean is None and maximum is None:
        return None
    return {
        "mean_db": mean if mean is not None else -91.0,
        "max_db": maximum if maximum is not None else -91.0,
    }


def classify_audio(
    levels: dict[str, float] | None,
) -> tuple[str, str]:
    """Coarse audio class + human reason. Speech needs transcription."""
    if levels is None:
        return "no_stream", "The file has no audio stream."
    if levels["mean_db"] < SILENCE_MEAN_DB:
        return "silence", (
            f"Mean volume {levels['mean_db']:.1f} dB is below the "
            f"{SILENCE_MEAN_DB:.0f} dB silence floor."
        )
    return "non_silent", (
        f"Mean volume {levels['mean_db']:.1f} dB; speech is confirmed "
        "only by transcription, not by loudness."
    )


_SILENCE_START_RE = re.compile(r"silence_start:\s*([0-9.]+)")
_SILENCE_END_RE = re.compile(
    r"silence_end:\s*([0-9.]+)\s*\|\s*silence_duration:\s*([0-9.]+)"
)


def silence_spans(
    ffmpeg_bin: str,
    src_path: str,
    min_dur: float = PAUSE_MIN_SEC,
    timeout: float = 300.0,
) -> list[dict[str, float]]:
    """Silent spans [{start, end}] from silencedetect (bounded output).

    Pauses are review evidence for 'clean up dead air' requests, never
    automatic cuts: a pause may be a meaningful beat.
    """
    done = subprocess.run(
        [ffmpeg_bin, "-v", "info", "-i", src_path,
         "-af", f"silencedetect=noise=-30dB:d={min_dur:g}",
         "-f", "null", "-"],
        capture_output=True, text=True, timeout=timeout, check=False,
    )
    spans: list[dict[str, float]] = []
    pending: float | None = None
    for line in done.stderr.splitlines():
        if "silence_start" in line:
            match = _SILENCE_START_RE.search(line)
            if match:
                pending = float(match.group(1))
        elif "silence_end" in line and pending is not None:
            match = _SILENCE_END_RE.search(line)
            if match:
                spans.append({
                    "start": round(pending, 2),
                    "end": round(float(match.group(1)), 2),
                })
                pending = None
                if len(spans) >= MAX_PAUSES_REPORTED:
                    break
    return spans


_CUT_RE = re.compile(r"pts_time:([0-9.]+)")


def scene_cuts(
    ffmpeg_bin: str, src_path: str, timeout: float = 300.0
) -> list[float]:
    """Cut timestamps from scene-change detection (bounded output)."""
    done = subprocess.run(
        [ffmpeg_bin, "-v", "info", "-i", src_path,
         "-vf", f"select='gt(scene,{SCENE_THRESHOLD})',showinfo",
         "-fps_mode", "passthrough", "-f", "null", "-"],
        capture_output=True, text=True, timeout=timeout, check=False,
    )
    cuts: list[float] = []
    for line in done.stderr.splitlines():
        if "showinfo" not in line:
            continue
        match = _CUT_RE.search(line)
        if match:
            cuts.append(round(float(match.group(1)), 2))
            if len(cuts) >= MAX_CUTS_REPORTED:
                break
    return cuts


def extract_frame(
    ffmpeg_bin: str, src_path: str, at: float, dest: str,
    width: int = 480, timeout: float = 120.0,
) -> None:
    done = subprocess.run(
        [ffmpeg_bin, "-y", "-v", "error", "-ss", f"{at:.2f}",
         "-i", src_path, "-frames:v", "1",
         "-vf", f"scale={width}:-1", dest],
        capture_output=True, text=True, timeout=timeout, check=False,
    )
    if done.returncode != 0 or not os.path.exists(dest):
        raise RuntimeError(f"Frame extraction failed: {done.stderr[-200:]}")


_BLACK_RE = re.compile(r"black_start:([0-9.]+).*?black_end:([0-9.]+)")


def black_fades(
    ffmpeg_bin: str, src_path: str, timeout: float = 300.0
) -> int:
    """Count of fade-to-black intervals (dips, not dissolve types)."""
    done = subprocess.run(
        [ffmpeg_bin, "-v", "info", "-i", src_path,
         "-vf", "blackdetect=d=0.3:pix_th=0.10", "-f", "null", "-"],
        capture_output=True, text=True, timeout=timeout, check=False,
    )
    return sum(1 for line in done.stderr.splitlines()
               if _BLACK_RE.search(line))


_YAVG_RE = re.compile(r"YAVG[=:]([0-9.]+)")
_SATAVG_RE = re.compile(r"SATAVG[=:]([0-9.]+)")


def frame_color(
    ffmpeg_bin: str, frame_path: str, timeout: float = 60.0
) -> dict[str, float] | None:
    """Average brightness/saturation of one frame (signalstats).

    Reads the values through ``metadata=print`` (``lavfi.signalstats.``
    keys) because current ffmpeg no longer logs per-frame signalstats
    lines; the ``YAVG:``/``SATAVG:`` single-line shape is still
    accepted for older binaries.
    """
    done = subprocess.run(
        [ffmpeg_bin, "-v", "info", "-i", frame_path,
         "-vf", "signalstats,metadata=print", "-f", "null", "-"],
        capture_output=True, text=True, timeout=timeout, check=False,
    )
    brightness: float | None = None
    saturation: float | None = None
    for line in done.stderr.splitlines():
        if brightness is None:
            match = _YAVG_RE.search(line)
            if match:
                brightness = round(float(match.group(1)), 1)
        if saturation is None:
            match = _SATAVG_RE.search(line)
            if match:
                saturation = round(float(match.group(1)), 1)
        if brightness is not None and saturation is not None:
            return {"brightness": brightness, "saturation": saturation}
    return None


def orientation_of(width: int, height: int) -> str:
    if width > height:
        return "landscape"
    if height > width:
        return "portrait"
    return "square"


def content_hint(
    duration: float,
    has_audio: bool,
    audio_class: str,
    transcript_words: int,
    cut_count: int,
) -> dict[str, str]:
    """Low-confidence content guess with its reason. Never a fact."""
    if transcript_words >= 5:
        return {
            "guess": "talking",
            "confidence": "low",
            "reason": (
                f"{transcript_words} transcribed words suggest a person "
                "talking; the camera subject is unverified."
            ),
        }
    if audio_class == "silence" or not has_audio:
        return {
            "guess": "silent",
            "confidence": "medium",
            "reason": "No usable audio; captions need written titles.",
        }
    if audio_class == "non_silent" and transcript_words == 0:
        return {
            "guess": "music_or_ambience",
            "confidence": "low",
            "reason": "Audible audio with no detected speech.",
        }
    if cut_count >= 4:
        return {
            "guess": "montage",
            "confidence": "low",
            "reason": f"{cut_count} detected cuts suggest edited footage.",
        }
    return {
        "guess": "general",
        "confidence": "low",
        "reason": "Not enough signal to specialize; ask the user.",
    }


def summarize_for_prompt(obs: dict[str, object]) -> str:
    """Compact observation text for planner/brief prompts."""
    bits = [
        f"{obs.get('duration', 0):.1f}s, {obs.get('orientation', '?')}, "
        f"{obs.get('width', 0)}x{obs.get('height', 0)}",
    ]
    audio = obs.get("audio", {})
    if isinstance(audio, dict):
        bits.append(f"audio: {audio.get('class', '?')}")
    speech = obs.get("speech", {})
    if isinstance(speech, dict) and speech.get("present"):
        bits.append(
            f"speech: yes ({speech.get('words', 0)} words, "
            f"{'approximate' if speech.get('approximate') else 'timed'})"
        )
    else:
        bits.append("speech: none detected")
    cuts = obs.get("cuts", [])
    if isinstance(cuts, list):
        if cuts and all(isinstance(c, (int, float)) for c in cuts):
            shown = ", ".join(f"{c:.1f}s" for c in cuts[:8])
            bits.append(f"cuts: {len(cuts)} (first: {shown})")
        else:
            bits.append(f"cuts: {len(cuts)}")
    pauses = obs.get("pauses", [])
    if isinstance(pauses, list) and pauses:
        shown = ", ".join(
            f"{p.get('start', 0):.1f}-{p.get('end', 0):.1f}s"
            for p in pauses[:6] if isinstance(p, dict)
        )
        bits.append(f"silent pauses: {len(pauses)} ({shown})")
    if isinstance(speech, dict):
        turns = speech.get("turns", [])
        if isinstance(turns, list) and turns:
            timed = sum(
                1 for t in turns
                if isinstance(t, dict)
                and isinstance(t.get("start"), (int, float))
            )
            bits.append(
                f"speech turns: {len(turns)} ({timed} with timestamps)"
            )
    hint = obs.get("content_hint", {})
    if isinstance(hint, dict):
        bits.append(
            f"likely content: {hint.get('guess', '?')} "
            f"({hint.get('confidence', '?')} confidence)"
        )
    return "; ".join(bits) + "."
