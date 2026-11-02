# ViralCut AI — self-hosted MVP

A working, self-hosted starter that accepts an authorized source video file, transcribes speech with Faster-Whisper, ranks candidate 30-second windows, renders vertical 9:16 MP4 clips with burned-in English captions, and can upload clips through the official YouTube Data API after Google OAuth consent.

## Important YouTube limitation

The official YouTube Data API does **not** provide an endpoint to download the media file behind an arbitrary YouTube URL. This app validates and stores the URL as a reference, but requires the original/authorized video file to be uploaded. Do not use third-party downloaders to bypass access controls, DRM, or YouTube restrictions. Only process material you own or have permission to edit and publish.

## Requirements

- Python 3.10–3.12
- FFmpeg and ffprobe installed and available on PATH
- FFmpeg build with `libass` support (for burned-in captions)
- 4–8 GB RAM recommended for Whisper `small`; CPU processing can be slow
- Internet access for initial Whisper model download and Google OAuth/API calls

## Run locally

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000 . On Windows, use the corresponding venv activation command. The first processing run downloads the selected Whisper model. Set `WHISPER_MODEL=base` for faster/lower-memory processing or `small` for a stronger general-purpose model. For a compatible NVIDIA GPU, set `WHISPER_DEVICE=cuda` and `WHISPER_COMPUTE_TYPE=float16`.

## Google / YouTube upload setup

1. Open Google Cloud Console and create/select a project.
2. Enable **YouTube Data API v3**.
3. Configure the OAuth consent screen and add your Google account as a test user if the app is in testing.
4. Create an OAuth 2.0 Client ID with application type **Desktop app**.
5. Download the client JSON and place it in this project folder as `client_secret.json`.
6. Start the app and use **Publish** or **Publish all**. The first upload starts Google's local OAuth consent flow. Approve the requested `youtube.upload` scope.
7. A `token.json` is saved locally. Keep both OAuth files private; never commit them to Git or expose them in a public deployment.

YouTube API quota, verification, account eligibility, and upload restrictions apply. Newly created API projects can have upload limitations. Check the current YouTube API console/quota status if uploads fail.

## What works in this MVP

- Responsive dashboard for mobile and desktop
- YouTube URL format validation (reference only)
- Local video upload with configurable 10–15 clip count and 15–60 second duration
- Speech transcription using Faster-Whisper
- Heuristic highlight selection based on speech density, punctuation, and hook words
- 1080×1920 vertical render with centered crop
- Burned-in English captions from recognized speech
- Individual and batch YouTube upload using OAuth and YouTube Data API
- Per-clip scheduled upload endpoint; the server must remain running at the scheduled time

## Known limitations / next engineering steps

- Highlight ranking is a transparent heuristic, not a trained virality predictor. Review clips before publishing.
- Whisper runs in translation mode and translates supported spoken languages into English captions. Translation quality depends on audio clarity and the selected Whisper model; review captions before publishing.
- Crop is center-based, not face-aware. Add a person/face detector and tracking model for reliable subject reframing.
- Titles are based on transcript fragments; no external generative LLM is included.
- Scheduling is in-memory. A production deployment needs a persistent queue (e.g. Redis/Celery) and durable scheduler.
- Large files need production object storage, resumable uploads, authentication, rate limiting, malware scanning, and retention controls.
- OAuth local-server flow is intended for local/self-hosted desktop use. A public multi-user deployment needs a secure web OAuth callback flow and per-user token storage.
- The app currently has no login system. Do not expose it to the public internet as-is.

## API endpoints

- `POST /api/validate-youtube` — validate a YouTube URL format.
- `POST /api/process` — multipart upload; fields: `video`, `youtube_url`, `count`, `clip_len`.
- `GET /api/jobs/{job_id}` — processing status and generated clip list.
- `POST /api/publish/{job_id}/{clip_id}` — upload one clip; JSON: `title`, `description`, `privacy`.
- `POST /api/publish-all/{job_id}` — upload all clips; JSON: `privacy`.
- `POST /api/schedule/{job_id}` — schedule one clip; JSON: `clip_id`, ISO-8601 `publish_at`, optional `privacy`, `title`, `description`.

## Privacy and rights

Uploaded source files and rendered clips are stored in local `uploads/` and `outputs/` folders. Add an explicit retention/deletion policy before using this with other people's content. The user is responsible for rights, permissions, music licensing, and YouTube Community Guidelines compliance.


## Use on iPhone (PWA)

This project now includes an iPhone-friendly installable web-app shell. It is **not an iOS App Store app**. First deploy the FastAPI server to an HTTPS host that supports FFmpeg, persistent storage, and background processing. Then open the deployed HTTPS URL in Safari on iPhone, tap Share, and choose **Add to Home Screen**. Launch ViralCut AI from the new Home Screen icon. The web app shell can cache for quicker reopening, but video processing, uploads, and YouTube publishing still require the server to be online. Do not deploy this starter publicly until authentication, per-user OAuth token storage, upload limits, and access controls have been added.
