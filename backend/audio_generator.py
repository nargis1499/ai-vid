"""Generate narration using Microsoft's hosted Edge TTS service."""
import asyncio
import os
import subprocess
import threading
import wave
from pathlib import Path

from common import DIRS


class NarrationCancelled(RuntimeError):
    """Raised when a user stops narration generation."""


class AudioGenerator:
    def __init__(self):
        self.voice = os.getenv("EDGE_TTS_VOICE", "en-US-AriaNeural")
        self.last_engine = ""
        self.last_warning = ""
        self.last_speed = "medium"
        self._cancel_event = threading.Event()

    def generate(self, text: str, index: int, speed: str = "medium", cancel_check=None) -> str:
        if not str(text or "").strip():
            raise RuntimeError("The scene has no narration text to speak.")
        speed = str(speed or "medium").strip().lower()
        if speed not in {"slower", "slow", "medium", "faster"}:
            raise ValueError("Voiceover pace must be Slower, Slow, Medium, or Faster.")
        self._cancel_event.clear()
        self.last_speed = speed
        self.last_warning = ""
        path = DIRS["audio"] / f"narration_{index}.mp3"
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        if cancel_check:
            cancel_check()
        try:
            import edge_tts

            async def save_audio():
                communicator = edge_tts.Communicate(str(text), self.voice)
                with path.open("wb") as output:
                    async for chunk in communicator.stream():
                        if self._cancel_event.is_set():
                            raise NarrationCancelled("Voiceover stopped by user.")
                        if cancel_check:
                            cancel_check()
                        if chunk.get("type") == "audio":
                            output.write(chunk.get("data", b""))

            try:
                asyncio.run(save_audio())
            except RuntimeError as exc:
                if "asyncio.run() cannot be called" not in str(exc):
                    raise
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(save_audio())
                finally:
                    loop.close()
        except NarrationCancelled:
            path.unlink(missing_ok=True)
            raise
        except Exception as exc:
            path.unlink(missing_ok=True)
            raise RuntimeError(
                f"Could not create narration with hosted Edge TTS ({type(exc).__name__}: {exc}). "
                "Check the internet connection and selected voice, then retry. No local speech model or offline voice was used."
            ) from exc
        if self._cancel_event.is_set():
            path.unlink(missing_ok=True)
            raise NarrationCancelled("Voiceover stopped by user.")
        if not path.is_file() or path.stat().st_size < 512:
            raise RuntimeError("Edge TTS returned an empty or incomplete audio file.")
        self.last_engine = f"Edge TTS ({self.voice})"
        return self._apply_voice_speed(str(path), speed)

    def cancel_current(self):
        """Stop Edge TTS streaming at its next received audio chunk."""
        self._cancel_event.set()

    @staticmethod
    def _apply_voice_speed(path: str, speed: str) -> str:
        rates = {"slower": 0.72, "slow": 0.86, "medium": 1.0, "faster": 1.12}
        rate = rates[speed]
        if rate == 1.0:
            return path
        source = Path(path)
        temporary = source.with_name(f"{source.stem}.paced{source.suffix}")
        try:
            from moviepy.config import FFMPEG_BINARY
            command = [
                FFMPEG_BINARY, "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(source), "-filter:a", f"atempo={rate}", "-vn",
                "-c:a", "libmp3lame", "-q:a", "2", str(temporary),
            ]
            completed = subprocess.run(command, capture_output=True, text=True, timeout=300, check=False)
            if completed.returncode or not temporary.is_file() or temporary.stat().st_size == 0:
                detail = (completed.stderr or "FFmpeg did not create a paced audio file.").strip()[-500:]
                raise RuntimeError(detail)
            os.replace(temporary, source)
            return str(source)
        except Exception as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise RuntimeError(f"Could not apply the selected voiceover pace: {exc}") from exc

    @staticmethod
    def duration(path: str) -> float:
        try:
            from pydub import AudioSegment
            return len(AudioSegment.from_file(path)) / 1000
        except Exception:
            try:
                with wave.open(path, "rb") as audio:
                    return audio.getnframes() / float(audio.getframerate())
            except Exception:
                return 5.0
