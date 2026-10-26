"""Line-oriented worker for Chatterbox TTS in an optional, isolated Python env."""
import json
import os
import sys
import wave
from pathlib import Path


def send(payload):
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def write_wav(audio, sample_rate, output_path):
    import numpy as np

    if hasattr(audio, "detach"):
        audio = audio.detach().float().cpu().numpy()
    audio = np.asarray(audio, dtype=np.float32).squeeze()
    if audio.ndim == 2:
        # The model is mono, but handle either channels-first or samples-first output.
        audio = audio.mean(axis=0) if audio.shape[0] <= 2 else audio.mean(axis=1)
    if audio.ndim != 1 or audio.size == 0:
        raise RuntimeError("Chatterbox returned an empty or unsupported audio tensor.")
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(int(sample_rate))
        wav_file.writeframes(pcm.tobytes())


def main():
    model = None
    model_device = "cpu"
    load_error = None
    for line in sys.stdin:
        try:
            request = json.loads(line)
            request_id = request.get("id")
            action = request.get("action", "generate")
            if action == "release":
                if "wav" in locals():
                    del wav
                if model is not None:
                    del model
                    model = None
                    try:
                        import gc
                        import torch
                        gc.collect()
                        if torch.cuda.is_available():
                            torch.cuda.empty_cache()
                    except Exception:
                        pass
                send({"id": request_id, "ok": True, "device": model_device})
                continue
            if action != "generate":
                raise ValueError("Unsupported Chatterbox worker action.")
            text = str(request.get("text") or "").strip()
            output_path = str(request.get("output_path") or "")
            if not text or not output_path:
                raise ValueError("A narration text and output path are required.")
            if load_error:
                raise RuntimeError(load_error)
            if model is None:
                try:
                    import torch
                    from chatterbox.tts import ChatterboxTTS
                    preferred_device = "cuda" if torch.cuda.is_available() else "cpu"
                    try:
                        model = ChatterboxTTS.from_pretrained(device=preferred_device)
                        model_device = preferred_device
                    except Exception as cuda_error:
                        if preferred_device != "cuda":
                            raise
                        # Preserve a working narration path on systems where CUDA has a
                        # driver/memory issue. CPU is slower but keeps the feature usable.
                        print(f"Chatterbox CUDA load failed; retrying on CPU: {cuda_error}", file=sys.stderr, flush=True)
                        import gc
                        gc.collect()
                        torch.cuda.empty_cache()
                        model = ChatterboxTTS.from_pretrained(device="cpu")
                        model_device = "cpu"
                except Exception as exc:
                    load_error = f"Could not load Chatterbox: {type(exc).__name__}: {exc}"
                    raise RuntimeError(load_error) from exc
            send({"id": request_id, "ok": True, "status": "generating", "device": model_device})
            wav = model.generate(text)
            write_wav(wav, model.sr, output_path)
            send({"id": request_id, "ok": True, "device": model_device})
        except Exception as exc:
            send({"id": locals().get("request_id"), "ok": False, "error": f"{type(exc).__name__}: {exc}"})


if __name__ == "__main__":
    main()
