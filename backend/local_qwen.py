"""Local Qwen3 GGUF inference and optional model download (no Ollama required)."""
import os
import re
import threading
from pathlib import Path
from urllib.parse import quote

import requests

from settings_store import APP_DATA_DIR, load_settings

MODEL_ID = "qwen3-1.7b-q4_k_m"
MODEL_REPOS = (
    "bartowski/Qwen_Qwen3-1.7B-GGUF",
    "lmstudio-community/Qwen3-1.7B-GGUF",
    "unsloth/Qwen3-1.7B-GGUF",
    "Qwen/Qwen3-1.7B-GGUF",
)
MODEL_FILENAME = "Qwen3-1.7B-Q4_K_M.gguf"

DEFAULT_MODEL_PATH = APP_DATA_DIR / "models" / MODEL_FILENAME

_download_lock = threading.RLock()
_download_state = {"status": "not_downloaded", "downloaded_bytes": 0, "total_bytes": 0, "error": ""}
_inference_lock = threading.RLock()
_llm = None
_llm_path = None


def model_path() -> Path:
    configured = str(load_settings().get("LOCAL_QWEN_MODEL_PATH") or "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_MODEL_PATH


def _has_gguf_header(path: Path) -> bool:
    try:
        with path.open("rb") as model_file:
            return model_file.read(4) == b"GGUF"
    except OSError:
        return False


def _resolve_model_url() -> tuple[str, str]:
    """Find Q4_K_M in known Qwen GGUF repositories instead of assuming a file exists upstream."""
    headers = {"Accept": "application/json", "User-Agent": "CreatorFlow local-model downloader"}
    catalog_notes = []
    for repo in MODEL_REPOS:
        api_url = f"https://huggingface.co/api/models/{repo}?blobs=true"
        try:
            response = requests.get(api_url, headers=headers, timeout=(20, 60))
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            catalog_notes.append(f"{repo}: {exc}")
            continue
        siblings = payload.get("siblings") if isinstance(payload, dict) else None
        files = [str(item.get("rfilename") or "") for item in siblings or [] if isinstance(item, dict)]
        matches = [name for name in files if name.lower().endswith("q4_k_m.gguf")]
        if matches:
            selected = next((name for name in matches if name.rsplit("/", 1)[-1].casefold() == MODEL_FILENAME.casefold()), matches[0])
            return f"https://huggingface.co/{repo}/resolve/main/{quote(selected, safe='/')}", repo
        available = [name for name in files if name.lower().endswith(".gguf")]
        if available:
            catalog_notes.append(f"{repo}: {', '.join(available[:5])}")
    notes = "; ".join(catalog_notes) or "no GGUF files were listed"
    raise RuntimeError(f"Could not find a Q4_K_M GGUF in the known Qwen3 1.7B repositories. Catalog results: {notes}")


def runtime_status() -> dict:
    runtime_error = ""
    try:
        from llama_cpp import Llama  # noqa: F401 - import tests the native runtime too
        runtime_available = True
    except Exception as exc:
        runtime_available = False
        runtime_error = f"{type(exc).__name__}: {exc}"
    path = model_path()
    with _download_lock:
        result = dict(_download_state)
    result.update({
        "model_id": MODEL_ID,
        "model_path": str(path),
        "model_available": _has_gguf_header(path),
        "runtime_available": runtime_available,
        "runtime_error": runtime_error,
    })
    if result["model_available"] and result["status"] != "downloading":
        result["status"] = "ready"
        result["error"] = ""
    return result


def is_ready() -> bool:
    status = runtime_status()
    return bool(status["model_available"] and status["runtime_available"])


def readiness_message(status=None) -> str:
    status = status or runtime_status()
    problems = []
    if not status.get("model_available"):
        problems.append("Qwen3 is not downloaded; download it in Settings → Local Qwen")
    if not status.get("runtime_available"):
        detail = status.get("runtime_error")
        runtime = "llama.cpp runtime is unavailable" + (f" ({detail})" if detail else "")
        runtime += ". Web/source deployment: install backend/requirements-local.txt on the Python server and redeploy; packaged app: install a build with Local Qwen support"
        problems.append(runtime)
    return "; ".join(problems) or "Local Qwen is ready."


def start_model_download() -> dict:
    path = model_path()
    if _has_gguf_header(path):
        with _download_lock:
            _download_state.update(status="ready", downloaded_bytes=path.stat().st_size, total_bytes=path.stat().st_size, error="")
            return dict(_download_state)
    with _download_lock:
        if _download_state.get("status") == "downloading":
            return dict(_download_state)
        _download_state.update(status="downloading", downloaded_bytes=0, total_bytes=0, error="")
    threading.Thread(target=_download_model, args=(path,), daemon=True, name="qwen-model-download").start()
    return runtime_status()


def _download_model(path: Path) -> None:
    partial = path.with_suffix(path.suffix + ".part")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = partial.stat().st_size if partial.exists() else 0
        headers = {"User-Agent": "CreatorFlow local-model downloader"}
        if existing:
            headers["Range"] = f"bytes={existing}-"
        model_url, model_repo = _resolve_model_url()
        with _download_lock:
            _download_state["source_repo"] = model_repo
        with requests.get(model_url, headers=headers, stream=True, timeout=(20, 120), allow_redirects=True) as response:
            if existing and response.status_code == 206:
                mode = "ab"
                downloaded = existing
                content_range = response.headers.get("Content-Range", "")
                match = re.search(r"/([0-9]+)$", content_range)
                total = int(match.group(1)) if match else existing + int(response.headers.get("Content-Length") or 0)
            else:
                response.raise_for_status()
                mode = "wb"
                downloaded = 0
                total = int(response.headers.get("Content-Length") or 0)
            if response.status_code not in (200, 206):
                response.raise_for_status()
            with _download_lock:
                _download_state.update(downloaded_bytes=downloaded, total_bytes=total)
            with partial.open(mode) as output:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    output.write(chunk)
                    downloaded += len(chunk)
                    with _download_lock:
                        _download_state.update(downloaded_bytes=downloaded, total_bytes=total)
        if not _has_gguf_header(partial):
            raise RuntimeError("Downloaded file is not a valid GGUF model. The download may have been interrupted or the model source changed.")
        os.replace(partial, path)
        size = path.stat().st_size
        with _download_lock:
            _download_state.update(status="ready", downloaded_bytes=size, total_bytes=size, error="")
    except Exception as exc:
        with _download_lock:
            _download_state.update(status="failed", error=f"{type(exc).__name__}: {exc}")


def chat_completion(system_prompt: str, user_prompt: str, *, temperature: float, max_tokens: int) -> str:
    """Run one JSON-oriented chat completion locally on CPU through llama-cpp-python."""
    path = model_path()
    if not _has_gguf_header(path):
        raise RuntimeError(f"Local Qwen model is not installed. Download {MODEL_FILENAME} from Settings first.")
    global _llm, _llm_path
    with _inference_lock:
        try:
            from llama_cpp import Llama
        except (ImportError, OSError) as exc:
            raise RuntimeError("The local llama.cpp runtime is unavailable. Install backend/requirements-local.txt for a source run, or use a desktop build with local Qwen support.") from exc
        if _llm is None or _llm_path != str(path.resolve()):
            if _llm is not None and hasattr(_llm, "close"):
                _llm.close()
            cpu_count = os.cpu_count() or 2
            _llm = Llama(
                model_path=str(path),
                n_ctx=8192,
                n_threads=max(1, min(8, cpu_count - 1)),
                n_gpu_layers=0,
                verbose=False,
            )
            _llm_path = str(path.resolve())
        response = _llm.create_chat_completion(
            messages=[
                {"role": "system", "content": f"{system_prompt} Return only the requested JSON object. Do not include analysis or <think> text."},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            chat_template_kwargs={"enable_thinking": False},
        )
    try:
        choice = response["choices"][0]
        if str(choice.get("finish_reason") or "").lower() == "length":
            raise RuntimeError(f"Local Qwen reached its {max_tokens}-token output limit; the JSON response may be incomplete.")
        content = choice["message"]["content"]
        if isinstance(content, list):
            content = "".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
        if not str(content or "").strip():
            raise RuntimeError("Local Qwen returned an empty response.")
        return str(content)
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("Local Qwen did not return a usable chat response.") from exc
