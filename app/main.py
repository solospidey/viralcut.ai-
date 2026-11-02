from __future__ import annotations
import os, threading, uuid
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime
from .core import ROOT, UPLOADS, OUTPUTS, load_jobs, save_jobs, update_job, youtube_id, process_job
from .youtube import upload_short

load_dotenv(ROOT / ".env")
app = FastAPI(title="ViralCut AI", version="1.0.0")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
app.mount("/outputs", StaticFiles(directory=OUTPUTS), name="outputs")
scheduler = BackgroundScheduler(timezone="UTC")
scheduler.start()

@app.get("/", response_class=HTMLResponse)
def home(): return (ROOT / "static" / "index.html").read_text()

@app.get("/api/health")
def health(): return {"ok": True, "ffmpeg": True, "note": "Requires FFmpeg/ffprobe installed on the server."}

@app.post("/api/validate-youtube")
def validate_youtube(payload: dict):
    url = str(payload.get("url", ""))
    vid = youtube_id(url)
    if not vid: raise HTTPException(400, "Please enter a valid YouTube video URL.")
    return {"valid": True, "video_id": vid, "message": "URL format is valid. Upload the original/authorized video file to process it; YouTube does not provide an official video-download endpoint."}

@app.post("/api/process")
async def process(background: BackgroundTasks, video: UploadFile = File(...), youtube_url: str = Form(""), count: int = Form(12), clip_len: int = Form(30)):
    if count < 1 or count > 15: raise HTTPException(400, "Clip count must be between 1 and 15.")
    if clip_len < 15 or clip_len > 60: raise HTTPException(400, "Clip length must be between 15 and 60 seconds.")
    ext = Path(video.filename or "video.mp4").suffix.lower()
    if ext not in {".mp4", ".mov", ".m4v", ".webm", ".mkv"}: raise HTTPException(400, "Supported formats: MP4, MOV, M4V, WebM, MKV.")
    job_id = uuid.uuid4().hex[:12]
    dest = UPLOADS / f"{job_id}{ext}"
    max_bytes = int(os.getenv("MAX_UPLOAD_MB", "2048")) * 1024 * 1024
    size = 0
    with dest.open("wb") as f:
        while chunk := await video.read(1024 * 1024):
            size += len(chunk)
            if size > max_bytes:
                f.close(); dest.unlink(missing_ok=True); raise HTTPException(413, "File exceeds upload size limit.")
            f.write(chunk)
    if size == 0: raise HTTPException(400, "Uploaded file is empty.")
    jobs = load_jobs(); jobs[job_id] = {"id": job_id, "status": "queued", "progress": 0, "message": "Queued", "youtube_url": youtube_url, "source_file": dest.name, "created_at": datetime.utcnow().isoformat()}; save_jobs(jobs)
    background.add_task(process_job, job_id, dest, count, clip_len)
    return {"job_id": job_id, "status": "queued"}

@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = load_jobs().get(job_id)
    if not job: raise HTTPException(404, "Job not found")
    return job

@app.post("/api/publish/{job_id}/{clip_id}")
def publish(job_id: str, clip_id: int, payload: dict):
    job = load_jobs().get(job_id)
    if not job or job.get("status") != "complete": raise HTTPException(404, "Completed job not found")
    clip = next((c for c in job.get("clips", []) if c["id"] == clip_id), None)
    if not clip: raise HTTPException(404, "Clip not found")
    path = OUTPUTS / job_id / clip["file"]
    title = str(payload.get("title") or clip["title"] or "New YouTube Short")
    description = str(payload.get("description") or "#Shorts")
    privacy = str(payload.get("privacy") or "private")
    if privacy not in {"private", "unlisted", "public"}: raise HTTPException(400, "Invalid visibility")
    try: result = upload_short(path, title, description, privacy)
    except Exception as e: raise HTTPException(500, str(e))
    return result

@app.post("/api/publish-all/{job_id}")
def publish_all(job_id: str, payload: dict):
    job = load_jobs().get(job_id)
    if not job or job.get("status") != "complete": raise HTTPException(404, "Completed job not found")
    privacy = str(payload.get("privacy") or "private")
    if privacy not in {"private", "unlisted", "public"}: raise HTTPException(400, "Invalid visibility")
    results = []
    for clip in job.get("clips", []):
        try:
            title = str(clip.get("title") or f"Short {clip['id']}")[:90] + " #Shorts"
            results.append({"clip_id": clip["id"], **upload_short(OUTPUTS / job_id / clip["file"], title, "#Shorts", privacy)})
        except Exception as e: results.append({"clip_id": clip["id"], "error": str(e)})
    return {"results": results}

@app.post("/api/schedule/{job_id}")
def schedule_job(job_id: str, payload: dict):
    job = load_jobs().get(job_id)
    if not job or job.get("status") != "complete": raise HTTPException(404, "Completed job not found")
    # Schedule one clip upload at a time. The app process must remain running.
    when = datetime.fromisoformat(str(payload.get("publish_at")))
    clip_id = int(payload.get("clip_id", 1)); privacy = str(payload.get("privacy", "private"))
    if privacy not in {"private", "unlisted", "public"}: raise HTTPException(400, "Invalid visibility")
    clip = next((c for c in job.get("clips", []) if c["id"] == clip_id), None)
    if not clip: raise HTTPException(404, "Clip not found")
    title = str(payload.get("title") or clip["title"] + " #Shorts")
    description = str(payload.get("description") or "#Shorts")
    def task():
        try: upload_short(OUTPUTS / job_id / clip["file"], title, description, privacy)
        except Exception as e: update_job(job_id, last_schedule_error=str(e))
    scheduler.add_job(task, "date", run_date=when, id=f"{job_id}-{clip_id}-{uuid.uuid4().hex[:5]}")
    return {"scheduled": True, "publish_at": when.isoformat(), "clip_id": clip_id, "note": "The server must remain online at the scheduled time."}

@app.get("/api/config")
def config():
    return {"google_oauth_configured": (ROOT / os.getenv("GOOGLE_CLIENT_SECRETS_FILE", "client_secret.json")).exists(), "max_upload_mb": int(os.getenv("MAX_UPLOAD_MB", "2048"))}
