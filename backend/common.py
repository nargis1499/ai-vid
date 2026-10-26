"""Shared configuration and output paths for YouTube Auto Creator."""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
ROOT_DIR = BASE_DIR.parent
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(ROOT_DIR / "output"))).expanduser()
if not OUTPUT_DIR.is_absolute():
    OUTPUT_DIR = (BASE_DIR / OUTPUT_DIR).resolve()
DIRS = {name: OUTPUT_DIR / name for name in ("images", "audio", "videos", "clips", "thumbnails", "local_ai", "uploads", "batch")}
for _path in DIRS.values():
    _path.mkdir(parents=True, exist_ok=True)

def env_bool(key, default=False):
    return os.getenv(key, str(default)).strip().lower() in {"1", "true", "yes", "on"}
