"""Backend configuration.

Reads backend/.env (key names only are ever reported; values are never
logged, printed, or returned by any endpoint).
"""
from __future__ import annotations

import os
from dataclasses import dataclass

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DOTENV_PATH = os.path.join(BACKEND_DIR, ".env")


def load_dotenv(path: str = DOTENV_PATH) -> dict[str, str]:
    """Parse a KEY=VALUE file. Existing process env wins over file values."""
    values: dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except FileNotFoundError:
        return values
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        value = value.strip().strip('"').strip("'").strip()
        if name and name not in os.environ:
            values[name] = value
    return values


@dataclass(frozen=True)
class Settings:
    api_key: str = ""
    api_base: str = "https://api.meta.ai/v1"
    port: int = 8080
    storage_dir: str = ""
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key)

    def redacted(self) -> dict[str, object]:
        """Status-safe view: reports key presence, never the key."""
        return {
            "api_key_present": self.has_api_key,
            "api_key_prefix_ok": self.api_key.startswith("LLM_"),
            "api_base": self.api_base,
            "port": self.port,
            "ffmpeg_bin": self.ffmpeg_bin,
            "ffprobe_bin": self.ffprobe_bin,
        }


def load_settings() -> Settings:
    file_values = load_dotenv()

    def get(name: str, default: str = "") -> str:
        return os.environ.get(name, file_values.get(name, default))

    try:
        port = int(get("PORT", get("BACKEND_PORT", "8080")))
    except ValueError:
        port = 8080
    storage = get("BACKEND_STORAGE", "") or os.path.join(BACKEND_DIR, "storage")
    return Settings(
        api_key=get("META_API_KEY", "") or get("MODEL_API_KEY", ""),
        api_base=get("META_API_BASE", "https://api.meta.ai/v1").rstrip("/"),
        port=port,
        storage_dir=os.path.abspath(storage),
        ffmpeg_bin=get("FFMPEG_BIN", "ffmpeg"),
        ffprobe_bin=get("FFPROBE_BIN", "ffprobe"),
    )
