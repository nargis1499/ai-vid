"""Generate narration with Chatterbox, Edge TTS, or an offline OS voice."""
import asyncio
import json
import os
import queue
import subprocess
import sys
import threading
import uuid
import wave
from pathlib import Path

from common import DIRS
from settings_store import APP_DATA_DIR


class NarrationCancelled(RuntimeError):
    """Raised when a user stops narration generation."""


class AudioGenerator:
    def __init__(self):
        self.voice = os.getenv("EDGE_TTS_VOICE", "en-US-AriaNeural")
        self.voice_engine = os.getenv("TTS_ENGINE", "chatterbox").strip().lower()
        self.chatterbox_python = os.getenv("CHATTERBOX_PYTHON", "").strip()
        self.last_engine = ""
        self.last_warning = ""
        self.last_device = ""
        self.chatterbox_active = False
        self._worker = None
        self._worker_key = None
        self._responses = None
        self._worker_lock = threading.Lock()
        self._worker_failure = ""
        self._worker_failure_interpreter = ""
        self._cancel_requested = False
        self.last_speed = "medium"

    def configure(self, engine=None, chatterbox_python=None):
        engine_changed = False
        if engine is not None:
            normalized_engine = str(engine).strip().lower()
            engine_changed = normalized_engine != self.voice_engine
            self.voice_engine = normalized_engine
        interpreter_changed = False
        if chatterbox_python is not None:
            value = str(chatterbox_python).strip()
            interpreter_changed = value != self.chatterbox_python
            if interpreter_changed:
                self.chatterbox_python = value
        if engine_changed or interpreter_changed:
            self._worker_failure = ""
            self._worker_failure_interpreter = ""
            self._stop_chatterbox_worker()

    def generate(self, text: str, index: int, speed: str = "medium", cancel_check=None) -> str:
        if not str(text or "").strip():
            raise RuntimeError("The scene has no narration text to speak.")
        speed = str(speed or "medium").strip().lower()
        if speed not in {"slow", "medium", "faster"}:
            raise ValueError("Voiceover pace must be Slow, Medium, or Faster.")
        self._cancel_requested = False
        self.last_speed = speed
        self.last_warning = ""
        self.last_device = ""
        mp3_path = DIRS["audio"] / f"narration_{index}.mp3"
        wav_path = DIRS["audio"] / f"narration_{index}.wav"
        for old_path in (mp3_path, wav_path):
            try:
                old_path.unlink(missing_ok=True)
            except OSError:
                pass

        if cancel_check:
            cancel_check()
        chatterbox_error = ""
        result = None
        if self.voice_engine == "chatterbox":
            try:
                result = self._generate_chatterbox(text, wav_path)
                self.last_engine = "Chatterbox"
            except Exception as exc:
                if self._cancel_requested:
                    raise NarrationCancelled("Voiceover stopped by user.") from exc
                self._worker_failure = f"{type(exc).__name__}: {exc}"
                self._worker_failure_interpreter = self._chatterbox_interpreter()
                chatterbox_error = self._worker_failure
                self._stop_chatterbox_worker()

        if result is None:
            if cancel_check:
                cancel_check()
            result = self._generate_edge_or_system(text, mp3_path, wav_path)
            if chatterbox_error:
                self.last_warning = (
                    f"Chatterbox was unavailable ({chatterbox_error}); this scene used {self.last_engine} instead. "
                    "Install the optional Chatterbox runtime or choose Edge TTS in Settings."
                )
                print(f"[audio] {self.last_warning}")
        if self._cancel_requested:
            raise NarrationCancelled("Voiceover stopped by user.")
        if cancel_check:
            cancel_check()
        return self._apply_voice_speed(result, speed)

    def cancel_current(self):
        """Stop an active Chatterbox subprocess; other engines stop at their next checkpoint."""
        self._cancel_requested = True
        worker = self._worker
        if worker is not None and worker.poll() is None:
            self._stop_chatterbox_worker()

    @staticmethod
    def _apply_voice_speed(path: str, speed: str) -> str:
        rates = {"slow": 0.86, "medium": 1.0, "faster": 1.12}
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
            ]
            if source.suffix.lower() == ".wav":
                command.extend(["-c:a", "pcm_s16le"])
            else:
                command.extend(["-c:a", "libmp3lame", "-q:a", "2"])
            command.append(str(temporary))
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

    def _generate_edge_or_system(self, text: str, mp3_path: Path, wav_path: Path) -> str:
        edge_error = None
        try:
            import edge_tts

            async def save():
                await edge_tts.Communicate(text, self.voice).save(str(mp3_path))

            try:
                asyncio.run(save())
            except RuntimeError:
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(save())
                finally:
                    loop.close()
            if not mp3_path.is_file() or mp3_path.stat().st_size < 512:
                raise RuntimeError("Edge TTS returned an empty or incomplete audio file.")
            self.last_engine = f"Edge TTS ({self.voice})"
            return str(mp3_path)
        except Exception as exc:
            edge_error = exc
            try:
                mp3_path.unlink(missing_ok=True)
            except OSError:
                pass
            print(f"[audio] Edge TTS failed; trying the computer's offline voice: {exc}")
        try:
            result = self._generate_system_voice(text, wav_path)
            self.last_engine = "offline system voice"
            return result
        except Exception as exc:
            if isinstance(exc, ModuleNotFoundError) and exc.name == "pyttsx3":
                fallback_hint = "Install the updated speech packages into the same Python environment running Flask with `python -m pip install --upgrade edge-tts==7.3.1 pyttsx3==2.99`, then restart the web backend."
            else:
                fallback_hint = "Check that the host has a working system speech engine (Windows SAPI, or eSpeak on Linux)."
            raise RuntimeError(
                f"Could not create narration. Edge TTS failed ({type(edge_error).__name__}: {edge_error}); "
                f"offline system TTS failed ({type(exc).__name__}: {exc}). {fallback_hint} "
                "The Edge TTS package was updated; if its WebSocket still returns HTTP 403, the Edge speech service or network is rejecting the connection."
            ) from exc

    def _chatterbox_interpreter(self) -> str:
        if self.chatterbox_python:
            return self.chatterbox_python
        app_data_python = APP_DATA_DIR / "chatterbox-venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if app_data_python.is_file():
            return str(app_data_python)
        return sys.executable

    def _ensure_chatterbox_worker(self):
        interpreter = self._chatterbox_interpreter()
        worker_script = Path(__file__).with_name("chatterbox_worker.py")
        if not worker_script.is_file():
            raise RuntimeError(f"Chatterbox worker script is missing: {worker_script}")
        key = (interpreter, str(worker_script))
        if self._worker is not None and self._worker.poll() is None and key == self._worker_key:
            return
        self._stop_chatterbox_worker()
        responses = queue.Queue()
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            worker = subprocess.Popen(
                [interpreter, str(worker_script)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                bufsize=1,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                **kwargs,
            )
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Could not start Chatterbox using Python at '{interpreter}': {exc}") from exc

        def read_responses():
            try:
                for line in worker.stdout:
                    responses.put(line)
            finally:
                responses.put(None)

        threading.Thread(target=read_responses, daemon=True, name="chatterbox-stdout").start()
        self._worker = worker
        self._worker_key = key
        self._responses = responses

    def _send_worker_request(self, action: str, **payload):
        with self._worker_lock:
            self._ensure_chatterbox_worker()
            worker = self._worker
            responses = self._responses
            request_id = uuid.uuid4().hex
            request = {"id": request_id, "action": action, **payload}
            try:
                worker.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
                worker.stdin.flush()
            except (BrokenPipeError, OSError, AttributeError) as exc:
                self._stop_chatterbox_worker()
                raise RuntimeError(f"Chatterbox worker stopped unexpectedly: {exc}") from exc

            timeout = 900 if action == "generate" else 60
            while True:
                try:
                    line = responses.get(timeout=timeout)
                except queue.Empty as exc:
                    self._stop_chatterbox_worker()
                    raise TimeoutError(f"Chatterbox did not respond within {timeout} seconds.") from exc
                if line is None:
                    self._stop_chatterbox_worker()
                    raise RuntimeError("Chatterbox worker exited without returning a result.")
                try:
                    response = json.loads(line)
                except json.JSONDecodeError:
                    # Ignore incidental library output; stdout is normally reserved for JSON.
                    continue
                if response.get("id") != request_id:
                    continue
                if not response.get("ok"):
                    raise RuntimeError(response.get("error") or "Chatterbox generation failed.")
                if response.get("status") == "generating":
                    self.last_device = str(response.get("device") or "")
                    continue
                return response

    def _generate_chatterbox(self, text: str, path: Path) -> str:
        current_interpreter = self._chatterbox_interpreter()
        if self._worker_failure and current_interpreter == self._worker_failure_interpreter:
            raise RuntimeError(self._worker_failure)
        if current_interpreter != self._worker_failure_interpreter:
            self._worker_failure = ""
            self._worker_failure_interpreter = ""
        self.chatterbox_active = True
        try:
            response = self._send_worker_request("generate", text=text, output_path=str(path))
        finally:
            self.chatterbox_active = False
        if not path.is_file() or path.stat().st_size < 2048:
            raise RuntimeError("Chatterbox returned an empty or incomplete WAV file.")
        with wave.open(str(path), "rb") as audio:
            if audio.getnframes() == 0 or audio.getframerate() == 0:
                raise RuntimeError("Chatterbox created an invalid WAV file.")
        self.last_device = str(response.get("device") or "")
        return str(path)

    def release_chatterbox_model(self):
        """Free Chatterbox GPU memory after a video's narration is complete."""
        if self._worker is None or self._worker.poll() is not None or self._worker_failure:
            return
        try:
            self._send_worker_request("release")
        except Exception as exc:
            print(f"[audio] Could not release Chatterbox model cleanly: {exc}")
            self._stop_chatterbox_worker()

    def _stop_chatterbox_worker(self):
        worker = self._worker
        self._worker = None
        self._worker_key = None
        self._responses = None
        if worker is None:
            return
        try:
            if worker.stdin:
                worker.stdin.close()
        except OSError:
            pass
        try:
            worker.terminate()
            worker.wait(timeout=5)
        except Exception:
            try:
                worker.kill()
            except Exception:
                pass

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
            try:
                engine.stop()
            except Exception:
                pass
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
