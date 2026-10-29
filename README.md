# CreatorFlow — YouTube Video Automation

CreatorFlow is a web app that turns video-title lists into narrated, edited videos with thumbnails and YouTube metadata. Scripts, scene plans, SEO metadata, and thumbnail copy use the **same selected text model**: OpenRouter, Groq (GroqCloud), or local Qwen3 1.7B Q4_K_M. When a hosted text request fails or returns invalid JSON, the app can automatically use the local Qwen model for that unfinished text step.

![CreatorFlow studio UI preview](screenshots/creator-studio-preview.png)

## Run the web app

Install Python 3.11 and FFmpeg, then install the backend dependencies and start Flask:

```bash
python -m pip install -r backend/requirements.txt
python backend/app.py
```

Open the URL printed by Flask (normally `http://localhost:5000`). Generated videos and thumbnails are saved to the configured output folder.

## Configure CreatorFlow

Open **Settings** in the web app:

- **Text AI provider:** choose **OpenRouter**, **Groq (GroqCloud)**, or **Local Qwen** in Settings. OpenRouter's visible catalog contains free text-generation models only; Groq's catalog loads from GroqCloud after its API key is saved. The selected provider/model handles scripts, scene plans, SEO metadata, and thumbnail copy. When OpenRouter or Groq fails (including rate limits, connection errors, or invalid JSON), the app automatically retries only that text task with Local Qwen if the GGUF model and llama.cpp runtime are installed. API keys remain in per-user settings and are not returned to the browser. If Local Qwen is selected directly, these text tasks run locally without a cloud text API.
- **Stock visuals:** The selected text model creates a few concrete stock-search phrases per scene. CreatorFlow searches Pexels and Pixabay, then ranks candidates using available metadata (alt text, tags, and URL terms), duration, landscape orientation, and resolution, with a slight Pexels preference. This is metadata matching, not visual inspection by a vision model. It prefers the closest available clip, then a stock still. If neither is available, it creates a designed gradient fallback; it does not download or run an image-generation model. Add stock API keys in Settings. Pixabay search results are cached locally and selected media is downloaded rather than hotlinked.
- **Narration:** voiceovers use Microsoft's hosted Edge TTS service and require an internet connection. Choose a voice and pace in the video brief. If hosted narration fails, generation stops with an error—there is no Chatterbox or offline system-voice fallback.
- **Music, transitions, and rendering:** background music is created procedurally (not with an AI model). Scene cuts randomly use alternating panel-slice, shattered-glass, directional swish-pan, warm light-leak flash, split-screen wipe, or luma-dissolve transitions. Video assembly and captions run on the app host; MP4 encoding always uses CPU-based H.264 libx264, with no GPU encoder path.
- **Thumbnails:** template-matched collage layouts use up to five scene visuals, bold Montserrat typography, warm dividers, and concise labels. When a headline names multiple subjects, the generator matches existing scene visuals and searches stock stills/clips for missing subjects instead of repeating an unrelated image. Downloadable JPEGs use the standard 1280 × 720 YouTube size. Enable **Thumbnail generation only** to skip narration, MP4 rendering, upload, and batch delays.
- **YouTube and batches:** select default privacy/category, daily upload cap, delay between titles, and output folder. Add the Google Desktop OAuth JSON in Settings. Stop an active batch from its progress panel; retry a failed pipeline step from the per-video progress list without repeating completed earlier work. When automatic publishing is off, publish a finished video individually from its result row.

Settings and API keys are stored per-user under `%APPDATA%\CreatorFlow\settings.json` on Windows, or the platform's application-settings folder. OAuth credentials and tokens are also stored there. The settings file is local plaintext; protect your account and do not share the file.

## Local Qwen model (no Ollama)

Settings includes a **Download Qwen3 model** option for `Qwen3-1.7B-Q4_K_M.gguf`. The model is downloaded to the per-user CreatorFlow data folder, never into Git or the application bundle. The local GGUF runs on CPU through `llama-cpp-python`/llama.cpp; Ollama is not used. New source installs from `backend/requirements.txt` and desktop builds include the CPU runtime. To add it to an existing source environment, run `python -m pip install -r backend/requirements-local.txt` and restart CreatorFlow.

Qwen is a text model: it can generate scripts, scenes, SEO metadata, and thumbnail copy, but it does not create images or speech. Visuals continue to use Pexels/Pixabay and the designed fallback; narration still uses hosted Edge TTS and needs internet. Stock search, YouTube publishing, and other non-text services are not replaced by Qwen.

## YouTube OAuth setup

1. In [Google Cloud Console](https://console.cloud.google.com/), enable **YouTube Data API v3** and configure the OAuth consent screen.
2. Create an OAuth client of type **Desktop app** and download its JSON file.
3. In CreatorFlow **Settings**, choose that JSON file. Then select **Connect channel** in the sidebar and complete Google's consent flow.
4. Uploads are opt-in for each batch and default to **Private**. Review each generated video before switching to Unlisted or Public.

## Requirements

- Python 3.11 and FFmpeg for running from source.
- An OpenRouter or GroqCloud API key for hosted text generation, or the downloaded Qwen GGUF plus llama.cpp runtime for local text generation/fallback.
- Internet access for hosted text/Edge TTS/stock services and to download the optional local model.
- Pexels and/or Pixabay API keys for stock photos and clips (optional).
- Google OAuth JSON for YouTube uploads (optional).

## Troubleshooting

- **Text model list does not load:** check the app host's internet connection and the selected provider's model-catalog access. OpenRouter lists free text models; Groq discovery requires a valid GroqCloud API key.
- **Script generation fails:** check that the selected provider/model is set in Settings and that its API key/account quota is valid. For automatic fallback, download Qwen3 in Settings and use a desktop build with the llama.cpp CPU runtime installed.
- **Local Qwen unavailable:** check the model and runtime statuses in Settings. If the GGUF is present but the runtime is unavailable, source installs should run `python -m pip install -r backend/requirements-local.txt` and restart; desktop users need a build that includes local Qwen support.
- **No voiceover:** Edge TTS is hosted and requires internet access. Check the selected voice and retry; the app will not use a local voice model.
- **No stock visuals:** add a valid Pexels or Pixabay key in Settings; a designed gradient fallback is used when stock is unavailable.
- **MP4 export says MoviePy/FFmpeg is required:** set FFmpeg executable to `ffmpeg-imageio` (default) or its full path. For source installs, reinstall `moviepy==1.0.3` and `imageio-ffmpeg` in the backend environment.
- **YouTube OAuth issue:** verify that YouTube Data API v3 is enabled and that the Desktop OAuth client is selected in Settings. Test users may need to be added to the Google consent screen.

## Repository layout

```text
backend/        Flask API and video pipeline
frontend/       CreatorFlow web UI
output/         Generated media (git-ignored)
```

Do not put API keys, OAuth credentials, refresh tokens, or generated media in a public Git repository.
