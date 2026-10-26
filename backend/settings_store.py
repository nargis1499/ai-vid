"""Persistent, per-user desktop settings (secrets never live in the repo)."""
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

APP_NAME = "CreatorFlow"
if sys.platform == "win32":
    APP_DATA_DIR = Path(os.getenv("APPDATA", Path.home() / "AppData" / "Roaming")) / APP_NAME
elif sys.platform == "darwin":
    APP_DATA_DIR = Path.home() / "Library" / "Application Support" / APP_NAME
else:
    APP_DATA_DIR = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config")) / "creatorflow"
APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
SETTINGS_FILE = APP_DATA_DIR / "settings.json"

DEFAULTS = {
    "USE_OLLAMA": True,
    "OLLAMA_MODEL": "qwen2.5:7b",
    "OLLAMA_URL": "http://localhost:11434",
    "OLLAMA_EXECUTABLE": "",
    "OLLAMA_AUTO_START": True,
    "OLLAMA_KEEP_ALIVE": True,
    "EDGE_TTS_VOICE": "en-US-AriaNeural",
    "USE_LOCAL_IMAGES": False,
    "USE_LOCAL_VIDEO": False,
    "USE_NVENC": True,
    "PEXELS_API_KEY": "",
    "YOUTUBE_PRIVACY_STATUS": "private",
    "YOUTUBE_CATEGORY_ID": "22",
    "MAX_UPLOADS_PER_DAY": 5,
    "BATCH_DELAY_SECONDS": 10,
    "GPU_MEMORY_LIMIT_GB": 7,
    "FFMPEG_BINARY": "ffmpeg-imageio",
    "OUTPUT_DIR": "",
}
BOOL_KEYS = {"USE_OLLAMA", "OLLAMA_AUTO_START", "OLLAMA_KEEP_ALIVE", "USE_LOCAL_IMAGES", "USE_LOCAL_VIDEO", "USE_NVENC"}
PUBLIC_KEYS = set(DEFAULTS) - {"PEXELS_API_KEY"}


def load_settings():
    values = dict(DEFAULTS)
    for key in values:
        if key in os.environ:
            values[key] = _coerce_env(key, os.environ[key])
    try:
        saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if isinstance(saved, dict):
            values.update({key: val for key, val in saved.items() if key in DEFAULTS})
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    if not values["OUTPUT_DIR"]:
        if getattr(sys, "frozen", False):
            values["OUTPUT_DIR"] = str(APP_DATA_DIR / "output")
        else:
            values["OUTPUT_DIR"] = str(Path(__file__).resolve().parent.parent / "output")
    return values


def save_settings(updates):
    current = load_settings()
    for key, value in updates.items():
        if key not in DEFAULTS:
            continue
        if key in BOOL_KEYS:
            current[key] = value is True or str(value).strip().lower() in {"true", "1", "yes", "on"}
        elif key in {"MAX_UPLOADS_PER_DAY", "BATCH_DELAY_SECONDS", "GPU_MEMORY_LIMIT_GB"}:
            current[key] = int(value)
        elif key == "PEXELS_API_KEY":
            current[key] = str(value or "").strip()
        else:
            current[key] = str(value).strip()
    if current["YOUTUBE_PRIVACY_STATUS"] not in {"private", "unlisted", "public"}:
        raise ValueError("YouTube privacy must be private, unlisted, or public.")
    if not current["OLLAMA_MODEL"].strip():
        raise ValueError("Enter an Ollama model name, for example qwen2.5:7b.")
    ollama_url=urlparse(current["OLLAMA_URL"])
    if ollama_url.scheme not in {"http", "https"} or not ollama_url.netloc:
        raise ValueError("Enter a valid Ollama URL, for example http://localhost:11434.")
    if not 1 <= current["MAX_UPLOADS_PER_DAY"] <= 50:
        raise ValueError("Daily upload limit must be from 1 to 50.")
    if not 0 <= current["BATCH_DELAY_SECONDS"] <= 3600:
        raise ValueError("Batch delay must be from 0 to 3600 seconds.")
    if not 1 <= current["GPU_MEMORY_LIMIT_GB"] <= 64:
        raise ValueError("GPU memory limit must be from 1 to 64 GB.")
    if current["USE_LOCAL_VIDEO"] and not current["USE_LOCAL_IMAGES"]:
        # SVD can animate stock or uploaded stills too, so this is allowed.
        pass
    settings_path = Path(current["OUTPUT_DIR"]).expanduser()
    settings_path.mkdir(parents=True, exist_ok=True)
    try:
        SETTINGS_FILE.write_text(json.dumps(current, indent=2), encoding="utf-8")
        if os.name != "nt":
            SETTINGS_FILE.chmod(0o600)
    except OSError as exc:
        raise ValueError(f"Could not save settings to {SETTINGS_FILE}: {exc}") from exc
    return current


def apply_environment(settings):
    for key, value in settings.items():
        if key == "OUTPUT_DIR":
            continue
        os.environ[key] = str(value).lower() if isinstance(value, bool) else str(value)


def public_settings(settings=None):
    settings = settings or load_settings()
    result = {key: settings[key] for key in PUBLIC_KEYS}
    result["PEXELS_API_KEY_CONFIGURED"] = bool(settings.get("PEXELS_API_KEY"))
    result["YOUTUBE_CREDENTIALS_CONFIGURED"] = (APP_DATA_DIR / "client_secret.json").is_file() or (Path(__file__).resolve().parent / "client_secret.json").is_file()
    return result


def _coerce_env(key, value):
    if key in BOOL_KEYS:
        return str(value).strip().lower() in {"true", "1", "yes", "on"}
    if key in {"MAX_UPLOADS_PER_DAY", "BATCH_DELAY_SECONDS", "GPU_MEMORY_LIMIT_GB"}:
        try:
            return int(value)
        except ValueError:
            return DEFAULTS[key]
    return value
