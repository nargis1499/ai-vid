"""Persistent per-user app settings (secrets never live in the repository)."""
import json
import os
import sys
from pathlib import Path

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
    "TEXT_PROVIDER": "openrouter",
    "TEXT_MODEL": "",
    "LOCAL_QWEN_MODEL_PATH": str(APP_DATA_DIR / "models" / "Qwen3-1.7B-Q4_K_M.gguf"),
    "OPENROUTER_API_KEY": "",
    "GROQ_API_KEY": "",
    "EDGE_TTS_VOICE": "en-US-AriaNeural",
    "PEXELS_API_KEY": "",
    "PIXABAY_API_KEY": "",
    "YOUTUBE_PRIVACY_STATUS": "private",
    "YOUTUBE_CATEGORY_ID": "22",
    "MAX_UPLOADS_PER_DAY": 5,
    "BATCH_DELAY_SECONDS": 10,
    "FFMPEG_BINARY": "ffmpeg-imageio",
    "OUTPUT_DIR": "",
}
SECRET_KEYS = {"PEXELS_API_KEY", "PIXABAY_API_KEY", "OPENROUTER_API_KEY", "GROQ_API_KEY"}
PUBLIC_KEYS = set(DEFAULTS) - SECRET_KEYS


def load_settings():
    values = dict(DEFAULTS)
    for key in values:
        if key in os.environ:
            values[key] = _coerce_env(key, os.environ[key])
    if "GROQ_API_KEY" not in os.environ and os.getenv("GROK_API_KEY"):
        values["GROQ_API_KEY"] = os.getenv("GROK_API_KEY", "").strip()
    try:
        saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if isinstance(saved, dict):
            values.update({key: val for key, val in saved.items() if key in DEFAULTS})
            # Migrate the earlier Grok-labelled provider and key to GroqCloud settings without exposing the saved secret.
            if not saved.get("GROQ_API_KEY") and saved.get("GROK_API_KEY"):
                values["GROQ_API_KEY"] = str(saved["GROK_API_KEY"]).strip()
            if values["TEXT_PROVIDER"] == "grok":
                values["TEXT_PROVIDER"] = "groq"
            if not values["TEXT_MODEL"]:
                values["TEXT_MODEL"] = str(saved.get("OPENROUTER_MODEL") or os.getenv("OPENROUTER_MODEL", "")).strip()
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        if not values["TEXT_MODEL"]:
            values["TEXT_MODEL"] = os.getenv("OPENROUTER_MODEL", "").strip()
    if values["TEXT_PROVIDER"] == "grok":
        values["TEXT_PROVIDER"] = "groq"
    if values["TEXT_PROVIDER"] not in {"openrouter", "groq", "local"}:
        values["TEXT_PROVIDER"] = "openrouter"
    if values["TEXT_PROVIDER"] == "local" and not values["TEXT_MODEL"]:
        values["TEXT_MODEL"] = "qwen3-1.7b-q4_k_m"
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
        if key == "TEXT_PROVIDER":
            provider = str(value or "openrouter").strip().lower()
            current[key] = "groq" if provider == "grok" else provider
        elif key in {"MAX_UPLOADS_PER_DAY", "BATCH_DELAY_SECONDS"}:
            current[key] = int(value)
        elif key in SECRET_KEYS:
            current[key] = str(value or "").strip()
        else:
            current[key] = str(value).strip()
    if current["TEXT_PROVIDER"] not in {"openrouter", "groq", "local"}:
        raise ValueError("Text provider must be OpenRouter, Groq, or Local Qwen.")
    if current["YOUTUBE_PRIVACY_STATUS"] not in {"private", "unlisted", "public"}:
        raise ValueError("YouTube privacy must be private, unlisted, or public.")
    if not 1 <= current["MAX_UPLOADS_PER_DAY"] <= 50:
        raise ValueError("Daily upload limit must be from 1 to 50.")
    if not 0 <= current["BATCH_DELAY_SECONDS"] <= 3600:
        raise ValueError("Delay between videos must be from 0 to 3600 seconds.")
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
    result["PIXABAY_API_KEY_CONFIGURED"] = bool(settings.get("PIXABAY_API_KEY"))
    result["OPENROUTER_API_KEY_CONFIGURED"] = bool(settings.get("OPENROUTER_API_KEY"))
    result["GROQ_API_KEY_CONFIGURED"] = bool(settings.get("GROQ_API_KEY"))
    result["YOUTUBE_CREDENTIALS_CONFIGURED"] = (APP_DATA_DIR / "client_secret.json").is_file() or (Path(__file__).resolve().parent / "client_secret.json").is_file()
    return result


def _coerce_env(key, value):
    if key in {"MAX_UPLOADS_PER_DAY", "BATCH_DELAY_SECONDS"}:
        try:
            return int(value)
        except ValueError:
            return DEFAULTS[key]
    return value
