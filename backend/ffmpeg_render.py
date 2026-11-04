"""FFmpeg rendering: probe, convert, and render validated plans.

The original upload is never modified: renders always go to a fresh file
under storage/renders. Audio is preserved by default (stream copy unless a
trim or another audio operation requires an exact re-encode).
"""
from __future__ import annotations

import json
import os
import subprocess

from plan_schema import content_ranges, kept_ranges, output_segments

ASPECT_TARGETS = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
}


class FFmpegError(Exception):
    pass


NO_VIDEO_MESSAGE = "No video stream found."
NO_AUDIO_MESSAGE = "No audio stream found."
NO_AUDIO_DURATION_MESSAGE = "Could not read audio duration."
NO_IMAGE_MESSAGE = "No image found."


def _run(bin_path: str, args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [bin_path] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        raise FFmpegError(
            f"{bin_path} was not found. Install FFmpeg and make sure it is "
            "on PATH (see backend/README.md)."
        ) from None
    except subprocess.TimeoutExpired:
        raise FFmpegError(f"{bin_path} timed out.") from None


def _parse_rate(text: str) -> float:
    if "/" in text:
        num, _, den = text.partition("/")
        try:
            return float(num) / float(den) if float(den) else 0.0
        except ValueError:
            return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _streams_info(ffprobe_bin: str, path: str) -> object:
    """Run ffprobe and return the parsed JSON (tool errors only)."""
    done = _run(
        ffprobe_bin,
        [
            "-v", "error",
            "-print_format", "json",
            "-show_format", "-show_streams",
            path,
        ],
        timeout=60.0,
    )
    if done.returncode != 0:
        raise FFmpegError(f"ffprobe failed: {done.stderr.strip()[-300:]}")
    try:
        return json.loads(done.stdout or "{}")
    except ValueError:
        raise FFmpegError("ffprobe returned invalid JSON.") from None


def probe_media(ffprobe_bin: str, path: str) -> dict[str, object]:
    """Return duration, dims, fps, and audio presence via ffprobe JSON."""
    info = _streams_info(ffprobe_bin, path)
    streams = info.get("streams", []) if isinstance(info, dict) else []
    video = next(
        (s for s in streams if s.get("codec_type") == "video"), None
    )
    audio = next(
        (s for s in streams if s.get("codec_type") == "audio"), None
    )
    if video is None:
        raise FFmpegError(NO_VIDEO_MESSAGE)
    duration = float(
        video.get("duration")
        or (info.get("format") or {}).get("duration")
        or 0.0
    )
    if duration <= 0:
        raise FFmpegError("Could not read media duration.")
    return {
        "duration": duration,
        "width": int(video.get("width", 0)),
        "height": int(video.get("height", 0)),
        "fps": _parse_rate(str(video.get("r_frame_rate") or "30")) or 30.0,
        "has_audio": audio is not None,
        "sar": str(video.get("sample_aspect_ratio") or "1:1"),
    }


def probe_audio(ffprobe_bin: str, path: str) -> dict[str, object]:
    """Return audio duration/codec. Requires an audio stream, not video."""
    info = _streams_info(ffprobe_bin, path)
    streams = info.get("streams", []) if isinstance(info, dict) else []
    audio = next(
        (s for s in streams if s.get("codec_type") == "audio"), None
    )
    if audio is None:
        raise FFmpegError(NO_AUDIO_MESSAGE)
    duration = float(
        audio.get("duration")
        or (info.get("format") or {}).get("duration")
        or 0.0
    )
    if duration <= 0:
        raise FFmpegError(NO_AUDIO_DURATION_MESSAGE)
    return {
        "duration": duration,
        "audio_codec": str(audio.get("codec_name", "?")),
        "sample_rate": int(audio.get("sample_rate") or 0),
        "channels": int(audio.get("channels") or 0),
        "has_audio": True,
    }


def probe_image(ffprobe_bin: str, path: str) -> dict[str, object]:
    """Return still-image dimensions. Requires a video stream with a
    positive size; stills carry no duration, so duration is not read."""
    info = _streams_info(ffprobe_bin, path)
    streams = info.get("streams", []) if isinstance(info, dict) else []
    video = next(
        (s for s in streams if s.get("codec_type") == "video"), None
    )
    width = int(video.get("width", 0)) if video else 0
    height = int(video.get("height", 0)) if video else 0
    if video is None or width <= 0 or height <= 0:
        raise FFmpegError(NO_IMAGE_MESSAGE)
    return {"width": width, "height": height}


def convert_to_wav(ffmpeg_bin: str, src: str, dst: str) -> dict[str, object]:
    """Convert any audio/video to mono 16-bit PCM WAV @16kHz for STT."""
    done = _run(
        ffmpeg_bin,
        [
            "-v", "error",
            "-i", src,
            "-map", "0:a:0",
            "-ac", "1",
            "-ar", "16000",
            "-c:a", "pcm_s16le",
            "-map_metadata", "-1",
            "-y", dst,
        ],
        timeout=300.0,
    )
    if done.returncode != 0:
        raise FFmpegError(f"WAV conversion failed: {done.stderr.strip()[-300:]}")
    size = os.path.getsize(dst)
    return {"path": dst, "bytes": size}


def _even_xy(value: int) -> int:
    return max(0, (int(value) // 2) * 2)


def _even_wh(value: int) -> int:
    return max(2, (int(value) // 2) * 2)


GRADE_FILTERS = {
    "warm": "colorbalance=rs={v:.3f}:gs={v:.3f}:bs={n:.3f}:"
            "rm={v:.3f}:gm={v:.3f}:bm={n:.3f}:"
            "rh={v:.3f}:gh={v:.3f}:bh={n:.3f}",
    "cool": "colorbalance=rs={n:.3f}:gs={n:.3f}:bs={v:.3f}:"
            "rm={n:.3f}:gm={n:.3f}:bm={v:.3f}:"
            "rh={n:.3f}:gh={n:.3f}:bh={v:.3f}",
    "vivid": "eq=saturation={sat:.3f}:contrast={con:.3f}",
    "mono": "eq=saturation={sat:.3f}",
}


def _banner_xy(position: str) -> tuple[str, str]:
    """Overlay x/y expressions for a banner position.

    W/H are the main frame, w/h the banner; the margin keeps the image
    inside safe area. Caller guarantees a validated position value.
    """
    margin = 40
    return {
        "top": ("(W-w)/2", f"{margin}"),
        "bottom": ("(W-w)/2", f"H-h-{margin}"),
        "top_left": (f"{margin}", f"{margin}"),
        "top_right": (f"W-w-{margin}", f"{margin}"),
        "bottom_left": (f"{margin}", f"H-h-{margin}"),
        "bottom_right": (f"W-w-{margin}", f"H-h-{margin}"),
        "center": ("(W-w)/2", "(H-h)/2"),
    }[position]


def _pip_xy(position: str) -> tuple[str, str]:
    """Overlay x/y expressions for a picture-in-picture corner.

    The window never covers the frame center, so captions stay
    readable; the margin keeps it inside safe area. Caller guarantees
    a validated position value.
    """
    margin = 24
    return {
        "top_left": (f"{margin}", f"{margin}"),
        "top_right": (f"W-w-{margin}", f"{margin}"),
        "bottom_left": (f"{margin}", f"H-h-{margin}"),
        "bottom_right": (f"W-w-{margin}", f"H-h-{margin}"),
    }[position]


def escape_ass_path(path: str) -> str:
    """Escape a file path for FFmpeg filtergraph options (Windows-safe).

    Single shared implementation used for ass= and fontsdir= values.
    Drive-letter colons need BOTH single quotes and a backslash to
    survive filtergraph parsing when argv is passed without a shell.
    Spaces survive inside the quotes; Unicode passes through because
    no shell ever re-parses the argument vector.
    """
    return (
        "'" + path.replace("\\", "/")
        .replace(":", "\\:")
        .replace("'", "\\'")
        + "'"
    )


def build_render_args(
    plan: dict[str, object],
    media: dict[str, object],
    input_path: str,
    output_path: str,
    tracked: dict[int, tuple[int, int, int, int]] | None = None,
    music_path: str | None = None,
    sfx_paths: list[str] | None = None,
    ass_path: str | None = None,
    fonts_dir: str | None = None,
    intro_path: str | None = None,
    outro_path: str | None = None,
    banner_paths: list[str] | None = None,
    support_paths: dict[str, str] | None = None,
) -> tuple[list[str], list[str]]:
    """Build the ffmpeg argv for a validated plan. Pure: runs nothing.

    All cuts use output-side trim/atrim filters (frame/sample accurate);
    every effect timestamp in the plan is source-referenced and is
    remapped to the edited timeline here, except sfx.at, banner
    start/end, and cutaway/pip at/duration, which already speak
    edited-output seconds (intro included). Inserts extend the content
    timeline before the stills join; a still-image intro/outro extends
    it further. Source effects render on the footage first, then
    inserts and stills join, and overlays/fades/subtitles/mix run on
    the final timeline. Returns (argv, adjustments) where adjustments
    describes rounding or dropped events.
    """
    if os.path.abspath(input_path) == os.path.abspath(output_path):
        raise FFmpegError("Refusing to overwrite the original file.")
    ops = plan.get("operations", [])
    assert isinstance(ops, list)
    duration = float(media["duration"])
    has_audio = bool(media.get("has_audio"))
    fps = float(media.get("fps", 30.0) or 30.0)

    trim = next((o for o in ops if o.get("op") == "trim"), None)
    segments = [o for o in ops if o.get("op") == "frame_subject"]
    crop = next((o for o in ops if o.get("op") == "crop"), None)
    resize = next((o for o in ops if o.get("op") == "resize_pad"), None)
    volume = next((o for o in ops if o.get("op") == "volume"), None)
    fades = [o for o in ops if o.get("op") == "fade"]
    grade = next((o for o in ops if o.get("op") == "grade"), None)
    transition = next((o for o in ops if o.get("op") == "transition"), None)
    texts = [o for o in ops if o.get("op") == "text"]
    captions = next((o for o in ops if o.get("op") == "captions"), None)
    music = next((o for o in ops if o.get("op") == "music"), None)
    sfx_ops = [o for o in ops if o.get("op") == "sfx"]
    intro_op = next((o for o in ops if o.get("op") == "intro"), None)
    outro_op = next((o for o in ops if o.get("op") == "outro"), None)
    banner_ops = [o for o in ops if o.get("op") == "banner"]
    cutaway_ops = [o for o in ops if o.get("op") == "cutaway"]
    pip_ops = [o for o in ops if o.get("op") == "pip"]
    insert_ops = sorted(
        [o for o in ops if o.get("op") == "insert"],
        key=lambda o: float(o["at"]),  # type: ignore[arg-type]
    )
    intro_dur = float(intro_op["duration"]) if intro_op else 0.0
    outro_dur = float(outro_op["duration"]) if outro_op else 0.0
    has_stills = intro_op is not None or outro_op is not None
    has_overlays = bool(cutaway_ops or pip_ops)
    if intro_op is not None and not intro_path:
        raise FFmpegError("The intro image is missing.")
    if outro_op is not None and not outro_path:
        raise FFmpegError("The outro image is missing.")
    if banner_ops and len(banner_paths or []) != len(banner_ops):
        raise FFmpegError("Banner image files are missing.")
    support_ids: list[str] = []
    for op in cutaway_ops + pip_ops + insert_ops:
        file_id = str(op["file_id"])
        if not (support_paths or {}).get(file_id):
            raise FFmpegError(
                f"{op['op']} at {float(op['at']):.1f}s is missing its "
                "supporting video file."
            )
        if file_id not in support_ids:
            support_ids.append(file_id)
    # Stills, banners, and supporting footage scale to the final frame;
    # tracked plans have no single frame size, so the validator keeps
    # them apart.
    final_dims: tuple[int, int] | None = None
    if has_stills or banner_ops or has_overlays or insert_ops:
        final_dims = compute_expected_dims(plan, media)
        if final_dims is None:
            raise FFmpegError(
                "Intro, outro, banner, and supporting video need a "
                "fixed frame size."
            )
    # Image inputs take the first extra input slots (input 0 is the
    # video), then supporting videos; music and effect inputs follow.
    # Indexes are fixed here so the filters below can reference them;
    # argv order matches.
    intro_index: int | None = None
    outro_index: int | None = None
    banner_indexes: list[int] = []
    next_input = 1
    if intro_op is not None:
        intro_index = next_input
        next_input += 1
    if outro_op is not None:
        outro_index = next_input
        next_input += 1
    for _ in banner_ops:
        banner_indexes.append(next_input)
        next_input += 1
    support_indexes: dict[str, int] = {}
    for file_id in support_ids:
        support_indexes[file_id] = next_input
        next_input += 1

    fade_in = max([float(f.get("in_sec", 0) or 0) for f in fades] or [0.0])
    fade_out = max([float(f.get("out_sec", 0) or 0) for f in fades] or [0.0])
    trans_dur = float(transition["duration"]) if transition else 0.0

    content = content_ranges(ops, duration)
    pieces = output_segments(ops, duration, trans_dur)
    out_len = sum(e - s for s, e in content)
    if transition and len(content) > 1:
        out_len -= trans_dur * (len(content) - 1)

    filters_v: list[str] = []
    filters_a: list[str] = []
    adjustments: list[str] = []
    if trim is not None:
        start, end = float(trim["start"]), float(trim["end"])
        if start <= 0.0 and end >= duration - 1e-6:
            adjustments.append(
                "Trim covers the full source duration; the render keeps "
                "the original length."
            )
    out_label = "[vout]"
    # Music, effects, and supporting audio can carry the whole audio
    # track: silent footage plus music must still render WITH an
    # audio stream.
    overlay_audio = any(
        o.get("audio", "mute") != "mute" for o in cutaway_ops + pip_ops
    )
    insert_audio = any(
        o.get("audio", "mute") != "mute" for o in insert_ops
    )
    audio_label: str | None = (
        "[aout]"
        if (
            has_audio or music is not None or sfx_ops
            or overlay_audio or insert_audio
        )
        else None
    )

    # -- cut the kept content into labeled pieces ----------------------
    v_pieces: list[str] = []
    a_pieces: list[str] = []
    if segments:
        for index, seg in enumerate(segments):
            rs, re = float(seg["start"]), float(seg["end"])
            window = (tracked or {}).get(index)
            if window is None:
                raise FFmpegError(
                    f"Segment {index} has no tracked window; "
                    "run subject tracking first."
                )
            x, y = _even_xy(window[0]), _even_xy(window[1])
            w, h = _even_wh(window[2]), _even_wh(window[3])
            if (x, y, w, h) != window:
                adjustments.append(
                    f"Segment {index} crop rounded to {w}x{h}."
                )
            chain = (f"[0:v]trim=start={rs:.3f}:end={re:.3f},"
                     f"setpts=PTS-STARTPTS,crop={w}:{h}:{x}:{y}")
            if transition:
                chain += f",format=yuv420p,fps={fps:.3f}"
            v_pieces.append(f"{chain}[sv{index}]")
            if has_audio:
                a_pieces.append(
                    f"[0:a]atrim=start={rs:.3f}:end={re:.3f},"
                    f"asetpts=PTS-STARTPTS[sa{index}]"
                )
    else:
        full = len(content) == 1 and content[0] == (0.0, duration)
        for index, (rs, re) in enumerate(content):
            if full:
                # No audio filter here: when nothing processes the
                # sound, the mix maps [0:a] directly, and an unused
                # [sa0] would leave the graph unconnected.
                v_pieces.append("[0:v]null[sv0]")
                break
            chain = (f"[0:v]trim=start={rs:.3f}:end={re:.3f},"
                     f"setpts=PTS-STARTPTS")
            if transition:
                chain += f",format=yuv420p,fps={fps:.3f}"
            v_pieces.append(f"{chain}[sv{index}]")
            if has_audio:
                a_pieces.append(
                    f"[0:a]atrim=start={rs:.3f}:end={re:.3f},"
                    f"asetpts=PTS-STARTPTS[sa{index}]"
                )
    filters_v.extend(v_pieces)
    filters_a.extend(a_pieces)
    count = len(v_pieces)

    # -- join pieces ----------------------------------------------------
    if count > 1 and transition:
        cur_v, cur_dur = "[sv0]", content[0][1] - content[0][0]
        for index in range(1, count):
            offset = cur_dur - trans_dur
            filters_v.append(
                f"{cur_v}[sv{index}]xfade=transition=fade:"
                f"duration={trans_dur:.3f}:offset={offset:.3f}[vx{index}]"
            )
            cur_v = f"[vx{index}]"
            cur_dur += content[index][1] - content[index][0] - trans_dur
        base_v = cur_v
        if has_audio:
            cur_a = "[sa0]"
            for index in range(1, count):
                filters_a.append(
                    f"{cur_a}[sa{index}]acrossfade=d={trans_dur:.3f}[ax{index}]"
                )
                cur_a = f"[ax{index}]"
            base_a = cur_a
        else:
            base_a = ""
    elif count > 1:
        filters_v.append(
            "".join(f"[sv{i}]" for i in range(count))
            + f"concat=n={count}:v=1:a=0[vcat]"
        )
        base_v = "[vcat]"
        if has_audio:
            filters_a.append(
                "".join(f"[sa{i}]" for i in range(count))
                + f"concat=n={count}:v=0:a=1[acat]"
            )
            base_a = "[acat]"
        else:
            base_a = ""
    else:
        # Full-length passthrough emits no [sa0] (see above): the voice
        # chain starts at the source stream itself.
        passthrough = (
            not segments and len(content) == 1
            and content[0] == (0.0, duration)
        )
        if has_audio:
            base_a = "[0:a]" if passthrough else "[sa0]"
        else:
            base_a = ""
        base_v = "[sv0]"

    # -- inserts extend the content timeline --------------------------
    # Each insert splits the joined content at `at` (content seconds,
    # sequential) and interleaves the supporting segment, scaled to
    # the source frame; resize/grade below apply to everything.
    if insert_ops:
        assert final_dims is not None
        mw = _even_wh(int(media["width"]))
        mh = _even_wh(int(media["height"]))
        shift = 0.0
        for pos, ins in enumerate(insert_ops):
            at = float(ins["at"]) + shift  # type: ignore[arg-type]
            dur = float(ins["duration"])  # type: ignore[arg-type]
            src = float(ins.get("src_start", 0.0))  # type: ignore[arg-type]
            sidx = support_indexes[str(ins["file_id"])]
            filters_v.append(
                f"[{sidx}:v]trim=start={src:.3f}:end={src + dur:.3f},"
                "setpts=PTS-STARTPTS,"
                f"scale={mw}:{mh}:force_original_aspect_ratio=decrease,"
                f"pad={mw}:{mh}:(ow-iw)/2:(oh-ih)/2:color=black,"
                f"setsar=1,fps={fps:.3f},format=yuv420p[insv{pos}]"
            )
            use_ins_audio = str(ins.get("audio", "mute")) == "use"
            if base_a:
                if use_ins_audio:
                    filters_a.append(
                        f"[{sidx}:a]atrim=start={src:.3f}:end={src + dur:.3f},"
                        "asetpts=PTS-STARTPTS,aformat=sample_fmts=fltp:"
                        "channel_layouts=stereo,aresample=44100"
                        f"[insa{pos}]"
                    )
                else:
                    filters_a.append(
                        f"anullsrc=r=44100:cl=stereo:d={dur:.3f}[insa{pos}]"
                    )
            had_audio_chain = bool(base_a)
            if not had_audio_chain and use_ins_audio:
                # Silent main footage: the insert's audio still plays,
                # placed over generated silence spanning the content.
                # The chain below then covers content plus this insert,
                # so the generic interleave must skip it this pass.
                cur_len = out_len + shift
                segs: list[str] = []
                if at > 1e-6:
                    filters_a.append(
                        f"anullsrc=r=44100:cl=stereo:d={at:.3f}[insSL{pos}]"
                    )
                    segs.append(f"[insSL{pos}]")
                filters_a.append(
                    f"[{sidx}:a]atrim=start={src:.3f}:end={src + dur:.3f},"
                    "asetpts=PTS-STARTPTS,aformat=sample_fmts=fltp:"
                    "channel_layouts=stereo,aresample=44100"
                    f"[insa{pos}]"
                )
                segs.append(f"[insa{pos}]")
                if at < cur_len - 1e-6:
                    filters_a.append(
                        "anullsrc=r=44100:cl=stereo:"
                        f"d={cur_len - at:.3f}[insSR{pos}]"
                    )
                    segs.append(f"[insSR{pos}]")
                filters_a.append(
                    "".join(segs)
                    + f"concat=n={len(segs)}:v=0:a=1[ains{pos}]"
                )
                base_a = f"[ains{pos}]"
            cur_len = out_len + shift
            join_v = [f"[insv{pos}]"]
            join_a = [f"[insa{pos}]"] if had_audio_chain else []
            if at > 1e-6:
                filters_v.append(
                    f"{base_v}trim=start=0:end={at:.3f},"
                    f"setpts=PTS-STARTPTS[insL{pos}]"
                )
                join_v.insert(0, f"[insL{pos}]")
                if had_audio_chain:
                    filters_a.append(
                        f"{base_a}atrim=start=0:end={at:.3f},"
                        f"asetpts=PTS-STARTPTS[insAL{pos}]"
                    )
                    join_a.insert(0, f"[insAL{pos}]")
            if at < cur_len - 1e-6:
                filters_v.append(
                    f"{base_v}trim=start={at:.3f},"
                    f"setpts=PTS-STARTPTS[insR{pos}]"
                )
                join_v.append(f"[insR{pos}]")
                if had_audio_chain:
                    filters_a.append(
                        f"{base_a}atrim=start={at:.3f},"
                        f"asetpts=PTS-STARTPTS[insAR{pos}]"
                    )
                    join_a.append(f"[insAR{pos}]")
            filters_v.append(
                "".join(join_v)
                + f"concat=n={len(join_v)}:v=1:a=0[vins{pos}]"
            )
            base_v = f"[vins{pos}]"
            if had_audio_chain:
                filters_a.append(
                    "".join(join_a)
                    + f"concat=n={len(join_a)}:v=0:a=1[ains{pos}]"
                )
                base_a = f"[ains{pos}]"
            shift += dur
        out_len += shift

    # -- smooth zooms on the edited timeline --------------------------
    # Every zoom maps to one or more output spans (a zoom may cover a
    # plain cut; the validator rejects dissolve crossings and overlaps).
    # Continuity rule: the zoom factor advances linearly over KEPT time,
    # so cut-out seconds do not stall or jump the motion.
    zooms = [o for o in ops if o.get("op") == "zoom"]
    if zooms:
        from plan_schema import remap_interval_output
        zw = _even_wh(int(media.get("width", 0)))
        zh = _even_wh(int(media.get("height", 0)))
        if (zw, zh) != (int(media.get("width", 0)),
                        int(media.get("height", 0))):
            adjustments.append(
                f"Zoom frame rounded to even {zw}x{zh}."
            )
        runs: list[tuple[float, float, dict[str, object], float, float]] = []
        for zoom in zooms:
            spans = remap_interval_output(
                float(zoom["start"]), float(zoom["end"]), pieces  # type: ignore[arg-type]
            )
            if not spans:
                raise FFmpegError(
                    "The zoom interval was cut from the render."
                )
            total = sum(high - low for low, high in spans)
            elapsed = 0.0
            for zs, ze in spans:
                runs.append((zs, ze, zoom, elapsed, total))
                elapsed += ze - zs
        runs.sort(key=lambda run: run[0])
        bounds = sorted(
            {0.0, out_len} | {b for r in runs for b in (r[0], r[1])}
        )
        zparts: list[str] = []
        for piece_index in range(len(bounds) - 1):
            ps, pe = bounds[piece_index], bounds[piece_index + 1]
            if pe - ps < 1e-6:
                continue
            owner = next(
                (r for r in runs
                 if r[0] - 1e-6 <= ps and pe <= r[1] + 1e-6),
                None,
            )
            chain = (f"{base_v}trim=start={ps:.3f}:end={pe:.3f},"
                     "setpts=PTS-STARTPTS")
            if owner is None:
                filters_v.append(f"{chain}[zb{piece_index}]")
            else:
                _, _, zoom, elapsed_before, total = owner
                from_f = float(zoom.get("from", 1.0))  # type: ignore[arg-type]
                to_f = float(zoom["to"])  # type: ignore[arg-type]
                fx = float(zoom.get("x", 0.5))  # type: ignore[arg-type]
                fy = float(zoom.get("y", 0.5))  # type: ignore[arg-type]
                progress = (
                    "min(max((({eb:.6f}+t)/{tot:.6f})\\,0)\\,1)".format(
                        eb=elapsed_before, tot=total
                    )
                )
                factor = (
                    f"({from_f:.4f}+({to_f:.4f}-{from_f:.4f})*{progress})"
                )
                filters_v.append(
                    f"{chain},"
                    f"scale=iw*{factor}:ih*{factor}:eval=frame,"
                    f"crop={zw}:{zh}:x='(iw-ow)*{fx:.3f}':"
                    f"y='(ih-oh)*{fy:.3f}'[zb{piece_index}]"
                )
            zparts.append(f"[zb{piece_index}]")
        if len(zparts) > 1:
            filters_v.append(
                "".join(zparts)
                + f"concat=n={len(zparts)}:v=1:a=0[vzoom]"
            )
            base_v = "[vzoom]"
        else:
            base_v = zparts[0]

    if crop is not None:
        x = _even_xy(int(crop["x"]))
        y = _even_xy(int(crop["y"]))
        w = _even_wh(int(crop["w"]))
        h = _even_wh(int(crop["h"]))
        filters_v.append(f"{base_v}crop={w}:{h}:{x}:{y}[v1]")
        base_v = "[v1]"
    if resize is not None and resize.get("aspect") != "original":
        target = ASPECT_TARGETS[str(resize.get("aspect"))]
        tw, th = target
        if str(resize.get("fit", "pad")) == "crop":
            filters_v.append(
                f"{base_v}scale={tw}:{th}:force_original_aspect_ratio="
                f"increase,crop={tw}:{th}[v2]"
            )
        else:
            filters_v.append(
                f"{base_v}scale={tw}:{th}:force_original_aspect_ratio="
                f"decrease,pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2[v2]"
            )
        base_v = "[v2]"
    if grade is not None:
        level = float(grade["intensity"])
        preset = str(grade["preset"])
        if preset in ("warm", "cool"):
            strength = 0.3 * level
            filters_v.append(
                f"{base_v}" + GRADE_FILTERS[preset].format(
                    v=strength, n=-strength) + "[vg]"
            )
        elif preset == "vivid":
            filters_v.append(
                f"{base_v}" + GRADE_FILTERS[preset].format(
                    sat=1.0 + 0.9 * level, con=1.0 + 0.12 * level) + "[vg]"
            )
        else:  # mono
            filters_v.append(
                f"{base_v}" + GRADE_FILTERS[preset].format(
                    sat=1.0 - level) + "[vg]"
            )
        base_v = "[vg]"
    # -- still-image intro/outro join ----------------------------------
    # Source effects above render on the footage; the stills join here,
    # so fades, subtitles, and the mix below all run on the final
    # timeline. Without stills this block changes nothing.
    main_len = out_len
    if has_stills:
        assert final_dims is not None
        fw, fh = final_dims
        joiners: list[str] = []
        if intro_op is not None:
            assert intro_index is not None
            filters_v.append(
                f"[{intro_index}:v]trim=0:{intro_dur:.3f},"
                "setpts=PTS-STARTPTS,"
                f"scale={fw}:{fh}:force_original_aspect_ratio=decrease,"
                f"pad={fw}:{fh}:(ow-iw)/2:(oh-ih)/2:color=black,"
                f"setsar=1,fps={fps:.3f},format=yuv420p[vhead]"
            )
            joiners.append("[vhead]")
        joiners.append(base_v)
        if outro_op is not None:
            assert outro_index is not None
            filters_v.append(
                f"[{outro_index}:v]trim=0:{outro_dur:.3f},"
                "setpts=PTS-STARTPTS,"
                f"scale={fw}:{fh}:force_original_aspect_ratio=decrease,"
                f"pad={fw}:{fh}:(ow-iw)/2:(oh-ih)/2:color=black,"
                f"setsar=1,fps={fps:.3f},format=yuv420p[vtail]"
            )
            joiners.append("[vtail]")
        filters_v.append(
            "".join(joiners)
            + f"concat=n={len(joiners)}:v=1:a=0[vjoin]"
        )
        base_v = "[vjoin]"
        out_len = intro_dur + main_len + outro_dur
    # -- banner overlays on the final timeline ------------------------
    # Banner start/end are edited-output seconds (intro included), so
    # they apply after the stills join, under fades and subtitles.
    if banner_ops:
        assert final_dims is not None
        fw, fh = final_dims
        for pos, (bop, bindex) in enumerate(
            zip(banner_ops, banner_indexes)
        ):
            frac = float(bop.get("width_frac", 0.4))  # type: ignore[arg-type]
            bw = max(2, int(fw * frac) // 2 * 2)
            bx, by = _banner_xy(str(bop.get("position")))
            filters_v.append(
                f"[{bindex}:v]scale={bw}:-1,format=yuva420p[ban{pos}]"
            )
            filters_v.append(
                f"{base_v}[ban{pos}]overlay={bx}:{by}:enable='between(t,"
                f"{float(bop['start']):.3f},{float(bop['end']):.3f})'"
                f"[vban{pos}]"
            )
            base_v = f"[vban{pos}]"
    # -- cutaway / picture-in-picture on the final timeline ----------
    # Cutaway/pip at/duration are edited-output seconds (intro
    # included), like sfx.at and banner start/end: no remap, applied
    # straight after the stills join. The overlay sits above banners
    # and below fades/subtitles.
    for pos, op in enumerate(cutaway_ops + pip_ops):
        assert final_dims is not None
        fw, fh = final_dims
        file_id = str(op["file_id"])
        sidx = support_indexes[file_id]
        at = float(op["at"])  # type: ignore[arg-type]
        dur = float(op["duration"])  # type: ignore[arg-type]
        src = float(op.get("src_start", 0.0))  # type: ignore[arg-type]
        start = at
        end_expr = f"{at + dur:.3f}"
        if op.get("op") == "cutaway":
            filters_v.append(
                f"[{sidx}:v]trim=start={src:.3f}:end={src + dur:.3f},"
                "setpts=PTS-STARTPTS,"
                f"scale={fw}:{fh}:force_original_aspect_ratio=increase,"
                f"crop={fw}:{fh},setsar=1,fps={fps:.3f},"
                "format=yuv420p,"
                f"setpts=PTS-STARTPTS+{start:.3f}/TB[cov{pos}]"
            )
            filters_v.append(
                f"{base_v}[cov{pos}]overlay=0:0:"
                f"eof_action=pass:enable='between(t,{start:.3f},{end_expr})'"
                f"[vcov{pos}]"
            )
        else:
            frac = float(op.get("width_frac", 0.3))
            pw = max(2, int(fw * frac) // 2 * 2)
            px, py = _pip_xy(str(op.get("position")))
            filters_v.append(
                f"[{sidx}:v]trim=start={src:.3f}:end={src + dur:.3f},"
                "setpts=PTS-STARTPTS,"
                f"scale={pw}:-1,setsar=1,fps={fps:.3f},format=yuv420p,"
                f"setpts=PTS-STARTPTS+{start:.3f}/TB[cov{pos}]"
            )
            filters_v.append(
                f"{base_v}[cov{pos}]overlay={px}:{py}:"
                f"eof_action=pass:enable='between(t,{start:.3f},{end_expr})'"
                f"[vcov{pos}]"
            )
        base_v = f"[vcov{pos}]"
    if fade_in > 0:
        filters_v.append(f"{base_v}fade=t=in:st=0:d={fade_in:.3f}[v3]")
        base_v = "[v3]"
    if fade_out > 0:
        at = max(0.0, out_len - fade_out)
        filters_v.append(
            f"{base_v}fade=t=out:st={at:.3f}:d={fade_out:.3f}[v4]"
        )
        base_v = "[v4]"
    if texts or captions is not None:
        if not ass_path or not fonts_dir:
            raise FFmpegError(
                "Text overlays need a generated subtitles file."
            )
        filters_v.append(
            f"{base_v}ass={escape_ass_path(ass_path)}:"
            f"fontsdir={escape_ass_path(fonts_dir)}[vsub]"
        )
        base_v = "[vsub]"
    filters_v.append(f"{base_v}format=yuv420p{out_label}")

    # -- audio: voice, music, effects -----------------------------------
    argv = ["-v", "error", "-i", input_path]
    if intro_op is not None:
        assert intro_path is not None
        argv += ["-framerate", f"{fps:.3f}", "-loop", "1",
                "-t", f"{intro_dur:.3f}", "-i", intro_path]
    if outro_op is not None:
        assert outro_path is not None
        argv += ["-framerate", f"{fps:.3f}", "-loop", "1",
                "-t", f"{outro_dur:.3f}", "-i", outro_path]
    for bpath in banner_paths or []:
        argv += ["-i", bpath]
    for file_id in support_ids:
        assert support_paths is not None
        argv += ["-i", support_paths[file_id]]
    audio_inputs = next_input  # input 0 is the video, then images/support
    music_index: int | None = None
    if music is not None:
        if not music_path:
            raise FFmpegError("The music track has no audio file.")
        argv += ["-i", music_path]
        music_index = audio_inputs
        audio_inputs += 1
    sfx_kept: list[tuple[int, float]] = []
    if sfx_ops:
        paths = list(sfx_paths or [])
        if len(paths) != len(sfx_ops):
            raise FFmpegError("Sound-effect audio files are missing.")
        # sfx.at is edited-output seconds: the span covers cuts, inserts,
        # and stills, matching -t/out_len below.
        span = out_len
        for op, path in zip(sfx_ops, paths):
            # sfx.at is edited-output seconds: mixed directly, no remap.
            moment = float(op["at"])
            if moment >= span - 1e-3:
                adjustments.append(
                    f"A sound effect at {moment:.1f}s lands past the "
                    f"{span:.1f}s output end (output seconds); dropped."
                )
                continue
            argv += ["-i", path]
            sfx_kept.append((audio_inputs, moment))
            audio_inputs += 1

    cut_audio = not (
        len(content) == 1 and content[0] == (0.0, duration)
        and not segments
    )
    # An insert with audio="use" over silent footage synthesizes its
    # own voice chain above, so presence follows the chain, not the
    # source stream.
    has_voice = has_audio or bool(base_a)
    audio_filtered = (
        has_voice and (cut_audio or volume is not None or fades
                       or transition is not None or music is not None
                       or bool(sfx_kept) or has_stills
                       or bool(insert_ops) or overlay_audio)
    )
    mix_sources: list[str] = []
    if audio_filtered:
        if volume is not None:
            filters_a.append(
                f"{base_a}volume={float(volume['gain_db']):.2f}dB[a1]"
            )
            base_a = "[a1]"
        if has_stills:
            # Voice stays with its footage: trim to the footage length,
            # then delay past the silent intro so fades and the mix
            # below run on the final timeline.
            chain = (f"{base_a}atrim=0:{main_len:.3f},"
                     "asetpts=PTS-STARTPTS")
            if intro_dur > 0:
                delay_ms = int(round(intro_dur * 1000))
                chain += f",adelay={delay_ms}:all=1"
            filters_a.append(f"{chain}[avox]")
            base_a = "[avox]"
        if fade_in > 0:
            filters_a.append(f"{base_a}afade=t=in:st=0:d={fade_in:.3f}[a2]")
            base_a = "[a2]"
        if fade_out > 0:
            at = max(0.0, out_len - fade_out)
            filters_a.append(
                f"{base_a}afade=t=out:st={at:.3f}:d={fade_out:.3f}[a3]"
            )
            base_a = "[a3]"
        # audio="use" overlays replace the voice inside their window;
        # "mix" and "mute" leave it untouched.
        for pos, op in enumerate(cutaway_ops + pip_ops):
            if str(op.get("audio", "mute")) != "use":
                continue
            start = float(op["at"])  # type: ignore[arg-type]
            end = start + float(op["duration"])  # type: ignore[arg-type]
            filters_a.append(
                f"{base_a}volume=0:enable='between(t,"
                f"{start:.3f},{end:.3f})'[vux{pos}]"
            )
            base_a = f"[vux{pos}]"
        filters_a.append(
            f"{base_a}atrim=0:{out_len:.3f},asetpts=PTS-STARTPTS[vox]"
        )
        mix_sources.append("[vox]")
    elif has_audio:
        mix_sources.append("[0:a]")
    if music is not None and music_index is not None:
        gain = float(music["volume_db"])
        filters_a.append(
            f"[{music_index}:a]atrim=0:{out_len:.3f},asetpts=PTS-STARTPTS,"
            f"volume={gain:.2f}dB[mus]"
        )
        music_label = "[mus]"
        if music.get("duck") and "[vox]" in mix_sources:
            filters_a.append(
                "[mus][vox]sidechaincompress=threshold=0.02:ratio=6:"
                "attack=20:release=400:makeup=1[musd]"
            )
            music_label = "[musd]"
        elif music.get("duck"):
            adjustments.append(
                "Ducking needs a voice track to duck under; the music "
                "plays at the set level."
            )
        mix_sources.append(music_label)
    # Supporting audio for cutaway/pip: the trimmed segment is delayed
    # onto the final timeline (edited-output seconds, no remap) and
    # mixed under the voice; "use" already muted the voice window
    # above, so no extra duck is needed.
    for pos, op in enumerate(cutaway_ops + pip_ops):
        if str(op.get("audio", "mute")) == "mute":
            continue
        sidx = support_indexes[str(op["file_id"])]
        start = float(op["at"])  # type: ignore[arg-type]
        dur = float(op["duration"])  # type: ignore[arg-type]
        src = float(op.get("src_start", 0.0))  # type: ignore[arg-type]
        delay_ms = int(round(start * 1000))
        filters_a.append(
            f"[{sidx}:a]atrim=start={src:.3f}:end={src + dur:.3f},"
            "asetpts=PTS-STARTPTS,aformat=sample_fmts=fltp:"
            f"channel_layouts=stereo,aresample=44100,"
            f"adelay={delay_ms}:all=1[oax{pos}]"
        )
        mix_sources.append(f"[oax{pos}]")
    for index, (sfx_index, moment) in enumerate(sfx_kept):
        delay_ms = int(round(moment * 1000))
        filters_a.append(
            f"[{sfx_index}:a]adelay={delay_ms}:all=1[fx{index}]"
        )
        mix_sources.append(f"[fx{index}]")
    needs_mix = len(mix_sources) > 1
    audio_built = bool(mix_sources) and (
        needs_mix or audio_filtered or music is not None or sfx_kept
        or overlay_audio or insert_audio
    )
    if audio_built:
        if needs_mix:
            filters_a.append(
                "".join(mix_sources)
                + f"amix=inputs={len(mix_sources)}:duration=longest:"
                "dropout_transition=0,"
                f"atrim=0:{out_len:.3f},asetpts=PTS-STARTPTS{audio_label}"
            )
        else:
            filters_a.append(
                f"{mix_sources[0]}atrim=0:{out_len:.3f},"
                f"asetpts=PTS-STARTPTS{audio_label}"
            )

    if filters_v or filters_a:
        argv += ["-filter_complex", ";".join(filters_v + filters_a)]
    argv += ["-map", out_label]
    if audio_label and audio_built:
        argv += ["-map", audio_label]
        audio_codec = ["-c:a", "aac", "-b:a", "128k"]
    elif has_audio:
        argv += ["-map", "0:a"]
        audio_codec = ["-c:a", "copy"]
    else:
        audio_codec = ["-an"]
    argv += ["-c:v", "libx264", "-crf", "18", "-preset", "veryfast"]
    argv += audio_codec
    argv += ["-movflags", "+faststart", "-y", output_path]
    return argv, adjustments


def build_join_args(
    clips: list[dict[str, object]],
    width: int,
    height: int,
    fps: float,
    output_path: str,
) -> list[str]:
    """Build the ffmpeg argv joining clips into one normalized timeline.

    Each clip is {path, duration, has_audio}. Video is scaled down
    into width x height with black padding (never cropped up), audio
    is normalized to stereo 44.1kHz with silence filling short or
    missing tracks, and the segments concatenate in order. Pure:
    runs nothing.
    """
    if len(clips) < 2:
        raise FFmpegError("Joining needs at least two clips.")
    argv: list[str] = []
    for clip in clips:
        argv += ["-i", str(clip["path"])]
    filters: list[str] = []
    concat_in: list[str] = []
    for index, clip in enumerate(clips):
        dur = float(clip["duration"])
        filters.append(
            f"[{index}:v]scale={width}:{height}:"
            "force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"setsar=1,fps={fps:g}[v{index}]"
        )
        if bool(clip.get("has_audio")):
            filters.append(
                f"[{index}:a]aformat=sample_fmts=fltp:"
                "channel_layouts=stereo,aresample=44100,"
                f"apad=whole_dur={dur:.3f},atrim=0:{dur:.3f},"
                f"asetpts=PTS-STARTPTS[a{index}]"
            )
        else:
            filters.append(
                f"anullsrc=r=44100:cl=stereo:d={dur:.3f}[a{index}]"
            )
        concat_in += [f"[v{index}]", f"[a{index}]"]
    filters.append(
        "".join(concat_in) + f"concat=n={len(clips)}:v=1:a=1[vjoin][ajoin]"
    )
    argv += ["-filter_complex", ";".join(filters)]
    argv += ["-map", "[vjoin]", "-map", "[ajoin]"]
    argv += ["-c:v", "libx264", "-crf", "18", "-preset", "veryfast"]
    argv += ["-c:a", "aac", "-b:a", "128k"]
    argv += ["-movflags", "+faststart", "-y", output_path]
    return argv


def expected_output_duration(
    plan: dict[str, object], media_duration: float
) -> float:
    """Expected render length in seconds for a validated plan."""
    ops = plan.get("operations", [])
    assert isinstance(ops, list)
    content = content_ranges(ops, media_duration)
    total = sum(end - start for start, end in content)
    transition = next((o for o in ops if o.get("op") == "transition"), None)
    if transition is not None and len(content) > 1:
        total -= float(transition["duration"]) * (len(content) - 1)
    for op in ops:
        if op.get("op") in ("intro", "outro", "insert"):
            total += float(op["duration"])
    return total


def compute_expected_dims(
    plan: dict[str, object], media: dict[str, object]
) -> tuple[int, int] | None:
    """Expected output width/height, or None when frame_subject is used
    (tracked windows vary per segment, so no single size applies)."""
    ops = plan.get("operations", [])
    assert isinstance(ops, list)
    if any(o.get("op") == "frame_subject" for o in ops):
        return None
    width, height = int(media["width"]), int(media["height"])
    crop = next((o for o in ops if o.get("op") == "crop"), None)
    if crop is not None:
        width, height = _even_wh(int(crop["w"])), _even_wh(int(crop["h"]))
    resize = next((o for o in ops if o.get("op") == "resize_pad"), None)
    if resize is not None and resize.get("aspect") != "original":
        width, height = ASPECT_TARGETS[str(resize.get("aspect"))]
    return width, height


def verify_render_output(
    ffprobe_bin: str,
    path: str,
    *,
    expected_duration: float,
    expect_audio: bool,
    expected_dims: tuple[int, int] | None = None,
) -> dict[str, object]:
    """ffprobe a fresh render; fail clearly if the cut was not applied.

    Returns the probed media dict. Raises FFmpegError when the output
    duration, dimensions, or audio presence does not match the plan —
    the caller must not report such output as a success.
    """
    media = probe_media(ffprobe_bin, path)
    actual = float(media["duration"])
    tolerance = max(0.6, 0.05 * expected_duration)
    if abs(actual - expected_duration) > tolerance:
        raise FFmpegError(
            "Render verification failed: expected about "
            f"{expected_duration:.1f}s but the output is {actual:.1f}s. "
            "The requested cut was not applied; "
            "the source file is unchanged."
        )
    if expect_audio and not media["has_audio"]:
        raise FFmpegError(
            "Render verification failed: the source has audio but the "
            "output has no audio stream."
        )
    if expected_dims is not None:
        width, height = int(media["width"]), int(media["height"])
        want_w, want_h = expected_dims
        if abs(width - want_w) > 2 or abs(height - want_h) > 2:
            raise FFmpegError(
                "Render verification failed: expected "
                f"{want_w}x{want_h} but the output is {width}x{height}."
            )
    return media


def run_render(ffmpeg_bin: str, args: list[str], timeout: float = 1800.0) -> None:
    done = _run(ffmpeg_bin, args, timeout=timeout)
    if done.returncode != 0:
        raise FFmpegError(f"Render failed: {done.stderr.strip()[-500:]}")
