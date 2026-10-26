# CreatorFlow — YouTube Video Automation

CreatorFlow turns a list of titles into narrated, edited videos with thumbnails and SEO metadata. It can publish to YouTube after you configure OAuth. The Windows **desktop app** opens in its own window, starts its local backend automatically, and offers app configuration in **Settings**—no browser tab, `python app.py`, or manual virtual-environment activation for normal use.

![CreatorFlow studio UI preview](screenshots/creator-studio-preview.png)

## Windows desktop app (recommended)

### One-time build

The packaged app is built on Windows. Install **Python 3.11** (the pinned PyTorch/MoviePy stack does not currently support Python 3.13/3.15 reliably), **FFmpeg**, and the **Microsoft Edge WebView2 Runtime** (usually already present on Windows 11). Add Python and FFmpeg to PATH. You do not need to activate a virtual environment yourself.

1. Download/clone this repository and open its folder in File Explorer.
2. Double-click `build-desktop-windows.bat` for the standard build, or `build-desktop-windows-cuda.bat` if you want to package CUDA-enabled PyTorch for local GPU image/video generation.
3. The build script creates and manages its own private build environment, installs the dependencies, and packages the application. This first build can take a while and requires internet access; the CUDA build downloads several GB.
4. When it finishes, launch `backend\dist\CreatorFlow\CreatorFlow.exe`. Keep the whole `CreatorFlow` folder together when moving/copying the packaged app.

After packaging, open the EXE like a normal desktop app. CreatorFlow starts its local server and opens a native app window automatically. You do not type `python app.py` or activate `.venv` to use it. Generated files default to `%APPDATA%\CreatorFlow\output`.

### Configure everything from the app

Open **Settings** in the header. Configure:

- **Scripts and Ollama:** choose local Ollama drafting or an OpenRouter free text model. In OpenRouter mode, the hosted model creates a draft and Ollama is required to analyze, edit, and finalize it locally; drafts are never used if local review fails. Add the OpenRouter key in Settings, select a model from the live free-model list, and keep Ollama enabled. The key is stored in your per-user settings file and is not returned to the browser. Ollama also prepares metadata and reviews custom scripts. At launch CreatorFlow looks for Ollama; if installed, it starts the local service when needed and loads the selected model if it is already downloaded. If the model is missing, use **Download model** in Settings. Model downloads are explicit because they can be several GB.
- **Visuals:** CreatorFlow searches Pexels for the best-matching stock clip first. If no clip is sufficiently relevant, it uses a local SDXL still when that option is enabled; otherwise it falls back to a Pexels still or a generated gradient. Stable Video Diffusion can animate fallback stills when enabled and supported by your GPU.
- **Narration and captions:** Chatterbox is the default natural-sounding local voice engine. Edge TTS remains selectable, with the OS voice as a final fallback. Burned-in, word-highlight captions use bundled Montserrat SemiBold with no background box; downloaded SRT captions are plain text. On Windows, run `.\\backend\\setup_chatterbox_windows.ps1` in PowerShell to install Chatterbox into a separate Python environment; this keeps the app's PyTorch 2.2 GPU stack intact. The first Chatterbox voiceover downloads its model weights. You can set a custom Chatterbox Python path in Settings if needed.
- **YouTube and batches:** select default privacy/category, daily upload cap, delay between titles, and the output folder. Use the file picker in Settings to add the Google Desktop OAuth JSON.

Settings and API keys are stored per-user under `%APPDATA%\CreatorFlow\settings.json` on Windows (OAuth credentials and token are also stored in that folder). They are not committed to this repository. The settings file is local plaintext; protect your Windows account and do not share the file.

The Ollama badge shows **Running** when the selected model is loaded, **Idle · ready** when installed but not currently loaded, and a clear state when missing or unreachable. If **Keep the model loaded** is enabled, CreatorFlow asks Ollama to keep it in memory while the app is open and unloads it when the desktop app closes. With the local Ollama drafting provider selected, generation can fall back to built-in templates if Ollama is unavailable; OpenRouter drafts and custom scripts instead fail rather than bypassing the required local Ollama review.

### YouTube OAuth setup

1. In [Google Cloud Console](https://console.cloud.google.com/), enable **YouTube Data API v3** and configure the OAuth consent screen.
2. Create an OAuth client of type **Desktop app** and download its JSON file.
3. In CreatorFlow **Settings**, choose that JSON file. Then select **Connect channel** in the sidebar and complete Google's consent flow. Google may open a system browser briefly for OAuth; the CreatorFlow interface itself remains a desktop app.
4. Uploads are opt-in for each batch and default to **Private**. Review each generated video before switching to Unlisted or Public.

## Requirements and optional services

- Windows 10/11; Python 3.11 is only required for building from source, not for running the packaged app.
- FFmpeg on PATH for MP4 export.
- Ollama installed locally for local LLM scripts and metadata. Install Ollama from [ollama.com](https://ollama.com/download). You can download the default model in the app or run `ollama pull qwen2.5:7b` separately.
- Pexels API key for stock photos and clips (optional; gradient/still fallbacks are built in).
- NVIDIA GPU and CUDA-enabled packaged build for local diffusion models (optional; initial model weights are large).
- Google OAuth JSON for YouTube uploads (optional).

Ollama itself is a separate local program; CreatorFlow can start its service and run an installed model, but it does not install Ollama silently. Without a Pexels key, images fall back to locally generated gradients. Local SDXL/SVD are off until enabled in Settings. Chatterbox runs locally when its optional isolated runtime is installed; Edge TTS uses Microsoft's online service, and the app tries the installed operating-system voice if Edge TTS fails.

## Build from source / developer mode

For development only, install Python 3.11 and FFmpeg, then run the Windows build script above. It creates `backend/.venv` automatically. To run the Flask backend in a terminal during development, install `backend/requirements.txt` and launch `backend/app.py`; standard users should use the packaged desktop EXE instead.

The desktop-specific dependencies are in `backend/requirements-desktop.txt`. The Windows build scripts use PyInstaller in **one-folder** mode for reliable WebView2 packaging. For a CUDA build, PyTorch is installed from the official CUDA 12.1 wheel index before packaging; the machine still needs a current NVIDIA driver.

## Troubleshooting

- **Build says Python 3.11 not found:** install Python 3.11, then double-click the build script again. Python 3.13/3.15 is not suitable for this pinned stack.
- **The EXE cannot open a window:** install/update Microsoft Edge WebView2 Runtime and relaunch.
- **Ollama is offline:** install the Ollama Windows app. CreatorFlow can start its service; use Settings → **Start / load model** to retry. Ensure the model selected in Settings is installed, or choose **Download model**.
- **Local AI reports no GPU:** the standard build may not contain the CUDA wheel. Rebuild with `build-desktop-windows-cuda.bat`, update your NVIDIA driver, and check that the GPU is available.
- **MoviePy reports `cannot load module more than once per process`:** rebuild the CUDA desktop app with the latest `build-desktop-windows-cuda.bat`. The CUDA PyTorch installer can otherwise upgrade NumPy to 2.x; the current build script restores and verifies NumPy 1.26.4 before packaging.
- **MP4 export says MoviePy/FFmpeg is required:** in Settings, set FFmpeg executable to `ffmpeg-imageio` (default) or the full path from `where.exe ffmpeg`. The packaging script bundles MoviePy, NumPy, ImageIO and the FFmpeg binary. The assembler reports the underlying import/FFmpeg error instead of hiding it. For source installs, reinstall `moviepy==1.0.3` and `imageio-ffmpeg` in the app environment.
- **Other MP4 export failures:** install FFmpeg and add the directory containing `ffmpeg.exe` to PATH, then relaunch CreatorFlow.
- **No voiceover:** Edge TTS requires internet access. The app now tries the local Windows speech voice if Edge TTS fails; if neither produces valid audio, it marks the video failed instead of exporting a silent MP4. Rebuild the desktop EXE to include the offline TTS fallback.
- **No stock visuals:** add a valid Pexels key in Settings; generated gradients/stills are used otherwise.
- **YouTube OAuth issue:** verify that the Data API is enabled and the Desktop OAuth client is selected in Settings. Test users may need to be added to the Google consent screen.
- **Pillow/MoviePy compatibility:** the video assembler includes a compatibility alias for the legacy MoviePy 1.x `Image.ANTIALIAS` reference.

## Repository layout

```text
backend/                    Flask API, pipeline and desktop launcher
frontend/                   CreatorFlow studio UI
build-desktop-windows*.bat  One-click Windows packaging entry points
output/                     Generated media in developer mode (git-ignored)
```

Do not put API keys, OAuth credentials, refresh tokens, or generated media in a public Git repository.
