"""ASS subtitle events for text overlays and captions.

Rendered by FFmpeg's libass filter, which shapes Tamil correctly through
HarfBuzz (drawtext cannot shape Indic scripts). Coordinates are output
pixels: the ass filter runs after resize/zoom, so PlayRes matches the
final frame size. Callers remap event times to the output timeline first.
"""
from __future__ import annotations

import os


def _ass_time(seconds: float) -> str:
    total = max(0, int(round(seconds * 100)))
    hours, rem = divmod(total, 360000)
    minutes, rem = divmod(rem, 6000)
    secs, centis = divmod(rem, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def find_tamil_font() -> tuple[str | None, str | None]:
    """(font_name, fonts_dir) for Tamil-capable rendering, if present."""
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    fonts_dir = os.path.join(system_root, "Fonts")
    for filename, name in (
        ("Nirmala.ttc", "Nirmala UI"),
        ("NirmalaUI.ttf", "Nirmala UI"),
        ("Latha.ttf", "Latha"),
    ):
        if os.path.exists(os.path.join(fonts_dir, filename)):
            return name, fonts_dir
    return None, None


def _clean(text: str) -> str:
    out = text.replace("\r", "").replace("\n", "\\N")
    out = out.replace("{", "(").replace("}", ")")
    return out.strip()


_ALIGN = {"top": 8, "center": 5, "bottom": 2}
_SIZE_FRAC = {"small": 0.035, "medium": 0.05, "large": 0.075}
_SIZE_MIN = {"small": 14, "medium": 18, "large": 24}


def build_ass(
    events: list[dict[str, object]],
    width: int,
    height: int,
    font_name: str = "Nirmala UI",
) -> str:
    """Render an .ass document. Each event needs start/end/text/position/
    size/animation keys with output-timeline seconds."""
    margin_v = max(12, int(round(height * 0.04)))
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Overlay,{font_name},20,&H00FFFFFF,&H000019FF,&H80000000,"
        f"&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,20,20,{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text",
    ]
    center_x = width // 2
    for event in events:
        start = float(event["start"])  # type: ignore[arg-type]
        end = float(event["end"])  # type: ignore[arg-type]
        if end <= start:
            continue
        position = str(event.get("position", "bottom"))
        size = str(event.get("size", "medium"))
        animation = str(event.get("animation", "none"))
        duration_ms = int(round((end - start) * 1000))
        font_size = max(
            _SIZE_MIN.get(size, 18),
            int(round(height * _SIZE_FRAC.get(size, 0.05))),
        )
        tags = [f"\\an{_ALIGN.get(position, 2)}", f"\\fs{font_size}"]
        if animation == "fade" and duration_ms >= 300:
            edge = min(400, duration_ms // 3)
            tags.append(f"\\fad({edge},{edge})")
        elif animation == "slide" and duration_ms >= 700:
            if position == "top":
                target_y = margin_v + font_size
                tags.append(
                    f"\\move({center_x},-60,{center_x},{target_y},0,600)"
                )
            else:
                target_y = (
                    height // 2 if position == "center"
                    else height - margin_v
                )
                tags.append(
                    f"\\move({center_x},{height + 60},{center_x},"
                    f"{target_y},0,600)"
                )
        override = "{" + "".join(tags) + "}"
        lines.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Overlay,,0,0,0,,"
            f"{override}{_clean(str(event.get('text', '')))}"
        )
    return "\n".join(lines) + "\n"
