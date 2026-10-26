"""Desktop lifecycle manager: start local Ollama and optionally warm the chosen model."""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import requests


class OllamaRuntime:
    def __init__(self, settings_provider):
        self.settings_provider = settings_provider
        self.process = None
        self.loaded_model = None
        self.lock = threading.RLock()
        self.worker = None
        self._state = {"state": "checking", "detail": "Checking for Ollama…", "progress": None}

    def status(self):
        with self.lock:
            return dict(self._state)

    def start(self):
        settings = self.settings_provider()
        if not settings.get("USE_OLLAMA", True):
            self._set("disabled", "Local Ollama use is disabled in Settings.")
            return
        if not settings.get("OLLAMA_AUTO_START", True):
            self._set("offline", "Automatic Ollama start is off. Use Start / load model to start it when needed.")
            return
        with self.lock:
            if self.worker and self.worker.is_alive():
                return
            self.worker = threading.Thread(target=self._ensure_and_warm, daemon=True, name="ollama-startup")
            self.worker.start()

    def activate(self):
        with self.lock:
            if self.worker and self.worker.is_alive():
                return {"started": True, "detail": "Ollama startup is already in progress."}
            self.worker = threading.Thread(target=self._ensure_and_warm, daemon=True, name="ollama-activate")
            self.worker.start()
        return {"started": True, "detail": "Starting Ollama and loading the configured model."}

    def deactivate(self):
        settings=self.settings_provider()
        base_url=settings.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
        model=settings.get("OLLAMA_MODEL", "qwen2.5:7b")
        for loaded in {model, self.loaded_model} - {None}:
            self._unload_model(base_url, loaded)
        self.loaded_model=None
        process=self.process
        if process and process.poll() is None:
            try: process.terminate()
            except Exception: pass
        self.process=None
        self._set("disabled", "Ollama auto-start or model use is disabled in Settings.")

    def pull_model(self):
        with self.lock:
            if self.worker and self.worker.is_alive():
                return {"started": False, "error": "An Ollama startup or model operation is already running."}
            self.worker = threading.Thread(target=self._pull_worker, daemon=True, name="ollama-pull")
            self.worker.start()
        return {"started": True, "detail": "Downloading the model from Ollama."}

    def _is_local(self, base_url):
        try:
            return urlparse(base_url).hostname in {"localhost", "127.0.0.1", "::1"}
        except Exception:
            return False

    def _find_executable(self, settings=None):
        configured=(settings or {}).get("OLLAMA_EXECUTABLE") or os.getenv("OLLAMA_EXECUTABLE")
        if configured:
            path=Path(configured).expanduser()
            if path.is_file():return str(path)
        found = shutil.which("ollama")
        if found:
            return found
        if sys.platform == "win32":
            candidates = [
                Path(os.getenv("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
                Path(os.getenv("ProgramFiles", "C:\\Program Files")) / "Ollama" / "ollama.exe",
            ]
            for candidate in candidates:
                if candidate.is_file():
                    return str(candidate)
        return None

    def _ensure_service(self, settings):
        base_url = settings.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
        try:
            response = requests.get(base_url + "/api/tags", timeout=2)
            response.raise_for_status()
            return True
        except requests.RequestException:
            pass
        if not self._is_local(base_url):
            self._set("offline", "The configured Ollama endpoint is remote and did not respond; no local service was started.")
            return False
        executable = self._find_executable(settings)
        if not executable:
            self._set("not-installed", "Ollama was not found. Install Ollama, or turn off local-model use in Settings.")
            return False
        try:
            kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
            if sys.platform == "win32":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            else:
                kwargs["start_new_session"] = True
            self.process = subprocess.Popen([executable, "serve"], **kwargs)
            self._set("starting", "Ollama is installed; starting its local service…")
        except OSError as exc:
            self._set("error", f"Could not start Ollama: {exc}")
            return False
        for _ in range(45):
            time.sleep(1)
            try:
                response = requests.get(base_url + "/api/tags", timeout=2)
                if response.ok:
                    return True
            except requests.RequestException:
                pass
            if self.process.poll() is not None:
                self._set("error", "The Ollama service exited while starting. Open the Ollama app to see its error.")
                return False
        self._set("offline", "Ollama did not become ready in time. Check the Ollama app and try again.")
        return False

    def _ensure_and_warm(self):
        settings = self.settings_provider()
        if not settings.get("USE_OLLAMA", True):
            self._set("disabled", "Local Ollama use is disabled in Settings.")
            return
        model = settings.get("OLLAMA_MODEL", "qwen2.5:7b")
        self._set("checking", f"Checking Ollama and {model}…")
        if not self._ensure_service(settings):
            return
        base_url = settings.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
        try:
            tags = requests.get(base_url + "/api/tags", timeout=3).json().get("models", [])
            if not self._has_model(tags, model):
                self._set("missing", f"{model} is not installed. Use Download model in Settings, or run ollama pull {model}.")
                return
            if self.loaded_model and self.loaded_model != model:
                self._unload_model(base_url, self.loaded_model)
                self.loaded_model=None
            if not settings.get("OLLAMA_KEEP_ALIVE", True):
                self._unload_model(base_url, model)
                self.loaded_model=None
                self._set("ready", f"{model} is installed; it will load for generation and unload afterward.")
                return
            self._set("warming", f"Loading {model} into memory. The first load can take a few minutes…")
            response = requests.post(base_url + "/api/generate", json={"model": model, "prompt": "", "stream": False, "keep_alive": -1, "options": {"num_predict": 0}}, timeout=300)
            response.raise_for_status()
            self.loaded_model=model
            self._set("running", f"{model} is loaded and ready for script and SEO generation.")
        except requests.RequestException as exc:
            self._set("error", f"Ollama responded, but {model} could not be loaded: {exc}")

    def _pull_worker(self):
        settings = self.settings_provider()
        if not self._ensure_service(settings):
            return
        model = settings.get("OLLAMA_MODEL", "qwen2.5:7b")
        base_url = settings.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
        self._set("pulling", f"Downloading {model}…", progress=0)
        try:
            with requests.post(base_url + "/api/pull", json={"name": model, "stream": True}, stream=True, timeout=(5, 300)) as response:
                response.raise_for_status()
                for line in response.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    try:
                        update = json.loads(line)
                    except ValueError:
                        continue
                    total, completed = update.get("total"), update.get("completed")
                    progress = round(100 * completed / total) if total and completed is not None else None
                    self._set("pulling", update.get("status", f"Downloading {model}…"), progress=progress)
            self._set("ready", f"{model} downloaded. Loading it now…", progress=100)
            self._ensure_and_warm()
        except requests.RequestException as exc:
            self._set("error", f"Model download failed: {exc}")

    @staticmethod
    def _has_model(models, target):
        target = str(target).strip().lower()
        if target.endswith(":latest"):
            target = target[:-7]
        for item in models:
            name = str(item.get("name") or item.get("model") or "").strip().lower()
            if name == target or name == target + ":latest":
                return True
        return False

    @staticmethod
    def _unload_model(base_url, model):
        try:
            requests.post(base_url.rstrip("/")+"/api/generate",json={"model":model,"prompt":"","stream":False,"keep_alive":0,"options":{"num_predict":0}},timeout=3)
        except requests.RequestException:
            pass

    def _set(self, state, detail, **extra):
        with self.lock:
            self._state = {"state": state, "detail": detail, "progress": None, **extra}

    def shutdown(self):
        settings=self.settings_provider()
        if settings.get("OLLAMA_KEEP_ALIVE", True):
            base_url=settings.get("OLLAMA_URL", "http://localhost:11434")
            model=settings.get("OLLAMA_MODEL", "qwen2.5:7b")
            for loaded in {model, self.loaded_model} - {None}:
                self._unload_model(base_url, loaded)
        self.loaded_model=None
        process = self.process
        if process and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=5)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
