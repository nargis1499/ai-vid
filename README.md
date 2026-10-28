# CreatorFlow — YouTube Video Automation

CreatorFlow is a web app that turns video-title lists into narrated, edited videos with thumbnails and YouTube metadata. Scripts, scene plans, SEO metadata, and thumbnail copy use the **same selected hosted OpenRouter text model**. The app does not load or run local AI models.

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

- **OpenRouter:** choose a text-output model from the live model list and use your existing OpenRouter API key in Settings. The selected model handles regular and custom scripts, scene plans, SEO metadata, and thumbnail copy. Paid models may incur OpenRouter usage charges. Prompts and custom drafts are sent to OpenRouter; the key is stored in per-user settings and is not returned to the browser. If OpenRouter is unavailable or returns incomplete output, generation fails rather than silently switching to a fallback.
- **Stock visuals:** CreatorFlow prefers the closest Pexels or Pixabay clip, then a stock still. If neither is available, it creates a designed gradient fallback; it does not download or run an image-generation model. Add stock API keys in Settings. Search results are cached locally and selected media is downloaded rather than hotlinked.
- **Narration:** voiceovers use Microsoft's hosted Edge TTS service and require an internet connection. Choose a voice and pace in the video brief. If hosted narration fails, generation stops with an error—there is no Chatterbox or offline system-voice fallback.
- **Music and rendering:** background music is created procedurally (not with an AI model). Video assembly, captions, and encoding run on the app host; optional NVIDIA NVENC is hardware video encoding, not AI inference.
- **Thumbnails:** template-matched collage layouts use up to five scene visuals, bold Montserrat typography, warm dividers, and concise labels. When a headline names multiple subjects, the generator matches existing scene visuals and searches stock stills/clips for missing subjects instead of repeating an unrelated image. Downloadable JPEGs use the standard 1280 × 720 YouTube size. Enable **Thumbnail generation only** to skip narration, MP4 rendering, upload, and batch delays.
- **YouTube and batches:** select default privacy/category, daily upload cap, delay between titles, and output folder. Add the Google Desktop OAuth JSON in Settings. Stop an active batch from its progress panel; when automatic publishing is off, publish a finished video individually from its result row.

Settings and API keys are stored per-user under `%APPDATA%\CreatorFlow\settings.json` on Windows, or the platform's application-settings folder. OAuth credentials and tokens are also stored there. The settings file is local plaintext; protect your account and do not share the file.

## No local AI models

CreatorFlow no longer depends on Ollama, SDXL/Diffusers, Chatterbox, or an offline TTS model. You do not need to install or download model weights. OpenRouter handles text-generation tasks; visuals come from licensed stock or a designed fallback, and narration uses hosted Edge TTS. Ordinary video editing and encoding still run locally on the app host.

## YouTube OAuth setup

1. In [Google Cloud Console](https://console.cloud.google.com/), enable **YouTube Data API v3** and configure the OAuth consent screen.
2. Create an OAuth client of type **Desktop app** and download its JSON file.
3. In CreatorFlow **Settings**, choose that JSON file. Then select **Connect channel** in the sidebar and complete Google's consent flow.
4. Uploads are opt-in for each batch and default to **Private**. Review each generated video before switching to Unlisted or Public.

## Requirements

- Python 3.11 and FFmpeg for running from source.
- An OpenRouter API key and a selected text model.
- Internet access for OpenRouter, hosted Edge TTS, and stock-media providers.
- Pexels and/or Pixabay API keys for stock photos and clips (optional).
- Google OAuth JSON for YouTube uploads (optional).

## Troubleshooting

- **OpenRouter model list does not load:** check the app host's internet connection. The catalog requires access to OpenRouter.
- **Script generation fails:** check that an OpenRouter API key and text model are selected in Settings, and that the model/account has sufficient quota.
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
