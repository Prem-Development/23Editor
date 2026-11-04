"""Bundled sound-effect assets, synthesized locally with FFmpeg.

Generated on first use into storage/assets with documented lavfi
recipes — no third-party samples, so no licensing issues. The app
lists these names; plans reference them by registry name only, never
by path. User-supplied audio arrives separately via /api/upload.
"""
from __future__ import annotations

import os
import subprocess

SFX_FILES: tuple[str, ...] = ("pop", "chime", "whoosh", "click")


def _run(ffmpeg_bin: str, args: list[str]) -> None:
    done = subprocess.run(
        [ffmpeg_bin] + args,
        capture_output=True, text=True, timeout=120, check=False,
    )
    if done.returncode != 0:
        raise RuntimeError(f"SFX synthesis failed: {done.stderr[-300:]}")


def ensure_sfx_assets(ffmpeg_bin: str, assets_dir: str) -> dict[str, str]:
    """Generate any missing bundled effects; return name -> wav path."""
    os.makedirs(assets_dir, exist_ok=True)
    paths = {name: os.path.join(assets_dir, f"sfx-{name}.wav")
             for name in SFX_FILES}
    if all(os.path.exists(p) and os.path.getsize(p) > 0 for p in paths.values()):
        return paths
    common = ["-v", "error", "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le"]
    if not os.path.exists(paths["pop"]) or os.path.getsize(paths["pop"]) == 0:
        _run(ffmpeg_bin, [
            "-y", "-f", "lavfi", "-i", "sine=frequency=520:duration=0.18",
            "-af", "afade=t=out:st=0.02:d=0.16,volume=0.9",
        ] + common + [paths["pop"]])
    if not os.path.exists(paths["chime"]) or os.path.getsize(paths["chime"]) == 0:
        _run(ffmpeg_bin, [
            "-y", "-f", "lavfi", "-i", "sine=frequency=880:duration=0.7",
            "-f", "lavfi", "-i", "sine=frequency=1318:duration=0.7",
            "-filter_complex",
            "[0:a][1:a]amix=inputs=2:duration=first:dropout_transition=0,"
            "afade=t=out:st=0.35:d=0.35,volume=0.7[a]",
            "-map", "[a]",
        ] + common + [paths["chime"]])
    if not os.path.exists(paths["whoosh"]) or os.path.getsize(paths["whoosh"]) == 0:
        _run(ffmpeg_bin, [
            "-y", "-f", "lavfi",
            "-i", "anoisesrc=duration=0.9:color=white:seed=7",
            "-af",
            "lowpass=f=3000,afade=t=in:st=0:d=0.35,"
            "afade=t=out:st=0.55:d=0.35,volume=0.5",
        ] + common + [paths["whoosh"]])
    if not os.path.exists(paths["click"]) or os.path.getsize(paths["click"]) == 0:
        _run(ffmpeg_bin, [
            "-y", "-f", "lavfi", "-i", "sine=frequency=2000:duration=0.06",
            "-af", "afade=t=out:st=0.01:d=0.05,volume=0.6",
        ] + common + [paths["click"]])
    return paths
