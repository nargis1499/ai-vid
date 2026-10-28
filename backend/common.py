"""Shared configuration and output paths for YouTube Auto Creator."""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

if getattr(sys, "frozen", False):
    # PyInstaller extracts bundled frontend assets into its runtime root.
    BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent)).resolve()
    ROOT_DIR = BASE_DIR
else:
    BASE_DIR = Path(__file__).resolve().parent
    ROOT_DIR = BASE_DIR.parent
    load_dotenv(BASE_DIR / ".env")
if os.getenv("OUTPUT_DIR"):
    OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR")).expanduser()
elif getattr(sys, "frozen", False):
    user_data = Path(os.getenv("APPDATA", Path.home() / "AppData" / "Roaming")) / "CreatorFlow"
    OUTPUT_DIR = user_data / "output"
else:
    OUTPUT_DIR = ROOT_DIR / "output"
if not OUTPUT_DIR.is_absolute():
    OUTPUT_DIR = (BASE_DIR / OUTPUT_DIR).resolve()
DIRS = {name: OUTPUT_DIR / name for name in ("images", "audio", "videos", "clips", "thumbnails", "uploads", "batch")}
for _path in DIRS.values():
    _path.mkdir(parents=True, exist_ok=True)

def env_bool(key, default=False):
    return os.getenv(key, str(default)).strip().lower() in {"1", "true", "yes", "on"}
