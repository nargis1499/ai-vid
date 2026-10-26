"""Generate real narration with Edge TTS and an offline OS-voice fallback."""
import asyncio
import os
import wave
from pathlib import Path
from common import DIRS

class AudioGenerator:
    def __init__(self): self.voice = os.getenv("EDGE_TTS_VOICE", "en-US-AriaNeural")

    def generate(self, text: str, index: int) -> str:
        if not str(text or "").strip():
            raise RuntimeError("The scene has no narration text to speak.")
        mp3_path = DIRS["audio"] / f"narration_{index}.mp3"
        wav_path = DIRS["audio"] / f"narration_{index}.wav"
        for old_path in (mp3_path, wav_path):
            try: old_path.unlink(missing_ok=True)
            except OSError: pass
        edge_error = None
        try:
            import edge_tts
            async def save():
                await edge_tts.Communicate(text, self.voice).save(str(mp3_path))
            try:
                asyncio.run(save())
            except RuntimeError:
                loop = asyncio.new_event_loop()
                try: loop.run_until_complete(save())
                finally: loop.close()
            if not mp3_path.is_file() or mp3_path.stat().st_size < 512:
                raise RuntimeError("Edge TTS returned an empty or incomplete audio file.")
            return str(mp3_path)
        except Exception as exc:
            edge_error = exc
            try: mp3_path.unlink(missing_ok=True)
            except OSError: pass
            print(f"[audio] Edge TTS failed; trying the computer's offline voice: {exc}")
        try:
            return self._generate_system_voice(text, wav_path)
        except Exception as exc:
            raise RuntimeError(
                f"Could not create narration. Edge TTS failed ({type(edge_error).__name__}: {edge_error}); "
                f"offline system TTS failed ({type(exc).__name__}: {exc}). Check internet access or install/enable a Windows speech voice."
            ) from exc

    def _generate_system_voice(self, text: str, path: Path) -> str:
        import pyttsx3
        engine = pyttsx3.init()
        try:
            engine.setProperty("rate", 175)
            voices = engine.getProperty("voices") or []
            wanted = self.voice.lower()
            preference = "zira" if "aria" in wanted else "david" if "guy" in wanted else "hazel" if "gb" in wanted else "natasha" if "au" in wanted else ""
            if preference:
                for voice in voices:
                    if preference in str(getattr(voice, "name", "")).lower():
                        engine.setProperty("voice", voice.id)
                        break
            engine.save_to_file(text, str(path))
            engine.runAndWait()
        finally:
            try: engine.stop()
            except Exception: pass
        if not path.is_file() or path.stat().st_size < 2048:
            raise RuntimeError("The system speech engine did not create a usable WAV file.")
        with wave.open(str(path), "rb") as audio:
            if audio.getnframes() == 0 or audio.getframerate() == 0:
                raise RuntimeError("The system speech engine created an empty WAV file.")
        return str(path)

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
