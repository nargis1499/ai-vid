"""Narration via Edge TTS with a silent-audio fallback."""
import asyncio
import os
from pathlib import Path
from common import DIRS

class AudioGenerator:
    def __init__(self): self.voice = os.getenv("EDGE_TTS_VOICE", "en-US-AriaNeural")
    def generate(self, text: str, index: int) -> str:
        path = DIRS["audio"] / f"narration_{index}.mp3"
        try:
            import edge_tts
            async def save():
                await edge_tts.Communicate(text, self.voice).save(str(path))
            try: asyncio.run(save())
            except RuntimeError:
                loop = asyncio.new_event_loop()
                try: loop.run_until_complete(save())
                finally: loop.close()
        except Exception as exc:
            print(f"[audio] Edge TTS failed; using silence: {exc}")
            try:
                from pydub import AudioSegment
                AudioSegment.silent(duration=5000).export(str(path), format="mp3")
            except Exception:
                path.write_bytes(b"")
        return str(path)
    @staticmethod
    def duration(path: str) -> float:
        try:
            from pydub import AudioSegment
            return len(AudioSegment.from_file(path)) / 1000
        except Exception: return 5.0
