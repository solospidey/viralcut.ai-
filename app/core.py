from __future__ import annotations
import json, os, re, shutil, subprocess, threading, uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
UPLOADS = ROOT / "uploads"
OUTPUTS = ROOT / "outputs"
DATA = ROOT / "data"
for p in (UPLOADS, OUTPUTS, DATA): p.mkdir(exist_ok=True)
JOBS_FILE = DATA / "jobs.json"
LOCK = threading.Lock()


def load_jobs() -> dict:
    if JOBS_FILE.exists():
        try: return json.loads(JOBS_FILE.read_text())
        except Exception: pass
    return {}

def save_jobs(jobs: dict):
    with LOCK: JOBS_FILE.write_text(json.dumps(jobs, indent=2, ensure_ascii=False))

def update_job(job_id: str, **kwargs):
    jobs = load_jobs(); jobs.setdefault(job_id, {}).update(kwargs); save_jobs(jobs)
    return jobs[job_id]

def run(cmd: list[str]):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode:
        raise RuntimeError((p.stderr or p.stdout)[-4000:])
    return p.stdout

def ffprobe_duration(path: Path) -> float:
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)])
    return float(out.strip())

def safe_name(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "-", s).strip("-")
    return s[:60] or "clip"

def youtube_id(url: str) -> str | None:
    m = re.search(r"(?:youtu\.be/|youtube\.com/(?:watch\?v=|shorts/|embed/))([\w-]{11})", url)
    return m.group(1) if m else None

def get_transcriber():
    from faster_whisper import WhisperModel
    model_name = os.getenv("WHISPER_MODEL", "small")
    device = os.getenv("WHISPER_DEVICE", "cpu")
    compute = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
    return WhisperModel(model_name, device=device, compute_type=compute)

def transcribe(path: Path):
    model = get_transcriber()
    segments, info = model.transcribe(str(path), task="translate", beam_size=5, vad_filter=True, word_timestamps=True)
    result = []
    for seg in segments:
        words = []
        if seg.words:
            words = [{"start": float(w.start), "end": float(w.end), "word": w.word.strip()} for w in seg.words]
        result.append({"start": float(seg.start), "end": float(seg.end), "text": seg.text.strip(), "words": words})
    return result, info.language

def choose_highlights(segments: list[dict], duration: float, count: int = 12, clip_len: int = 30):
    """Heuristic highlight ranking. It is intentionally transparent, not a claim of human-level virality."""
    if duration <= clip_len: return [{"start": 0.0, "end": duration, "score": 1.0, "title": "Full clip"}]
    candidates = []
    step = 5.0
    for start in [x * step for x in range(max(1, int((duration-clip_len)//step)+1))]:
        end = min(duration, start + clip_len)
        contained = [s for s in segments if s["end"] > start and s["start"] < end]
        text = " ".join(s["text"] for s in contained)
        words = re.findall(r"\b\w+\b", text)
        speech = sum(max(0, min(s["end"], end)-max(s["start"], start)) for s in contained)
        density = min(1.0, speech / clip_len)
        punctuation = sum(text.count(c) for c in "!?…")
        hooks = len(re.findall(r"\b(why|how|secret|never|best|worst|truth|mistake|imagine|because|actually|important|surprising|लेकिन|क्यों|कैसे)\b", text, re.I))
        score = density * 3 + min(punctuation, 5) * .12 + min(hooks, 5) * .18 + min(len(words), 75) / 250
        candidates.append({"start": start, "end": end, "score": score, "text": text})
    candidates.sort(key=lambda x: x["score"], reverse=True)
    chosen = []
    for c in candidates:
        if all(c["end"] <= x["start"] or c["start"] >= x["end"] for x in chosen):
            chosen.append(c)
            if len(chosen) >= count: break
    chosen.sort(key=lambda x: x["start"])
    for i, c in enumerate(chosen, 1):
        words = re.findall(r"\w+", c.get("text", ""))
        c["title"] = " ".join(words[:7]) or f"Highlight {i}"
    return chosen

def ass_time(seconds: float) -> str:
    cs = int(round(seconds * 100))
    h, rem = divmod(cs, 360000); m, rem = divmod(rem, 6000); s, cc = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cc:02d}"

def make_ass(segments: list[dict], clip_start: float, clip_end: float, path: Path):
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Viral,Arial,68,&H00FFFFFF,&H0000FFFF,&H00101010,&H90000000,1,0,0,0,100,100,0,0,1,5,2,2,80,80,280,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for seg in segments:
        a, b = max(seg["start"], clip_start), min(seg["end"], clip_end)
        if b <= a: continue
        text = seg["text"].replace("{", "(").replace("}", ")").replace("\\", "")
        text = re.sub(r"\s+", " ", text).strip()
        if not text: continue
        lines.append(f"Dialogue: 0,{ass_time(a-clip_start)},{ass_time(b-clip_start)},Viral,,0,0,0,,{text}\n")
    path.write_text("".join(lines), encoding="utf-8")

def render_clip(source: Path, clip: dict, index: int, job_dir: Path, transcript: list[dict]):
    start, end = clip["start"], clip["end"]
    ass = job_dir / f"captions_{index:02d}.ass"
    make_ass(transcript, start, end, ass)
    out = job_dir / f"short_{index:02d}.mp4"
    # Scale to fill 1080x1920 and center-crop. Face-aware tracking needs a separate vision model.
    ass_filter = "subtitles='" + str(ass).replace("\\", "/").replace(":", "\\:").replace("'", "\\'") + "'"
    vf = f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,{ass_filter},setsar=1"
    run(["ffmpeg", "-y", "-ss", str(start), "-i", str(source), "-t", str(end-start), "-vf", vf,
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)])
    return out

def process_job(job_id: str, source: Path, count: int, clip_len: int):
    job_dir = OUTPUTS / job_id; job_dir.mkdir(exist_ok=True)
    try:
        update_job(job_id, status="transcribing", progress=10, message="Transcribing audio…")
        duration = ffprobe_duration(source)
        segments, language = transcribe(source)
        if not segments: raise RuntimeError("No speech detected. Try a video with clear spoken audio.")
        update_job(job_id, status="selecting", progress=35, language=language, message="Selecting highlights…")
        clips = choose_highlights(segments, duration, count, clip_len)
        if not clips: raise RuntimeError("Could not find suitable clips.")
        rendered = []
        for i, clip in enumerate(clips, 1):
            update_job(job_id, status="rendering", progress=35 + int(60*i/len(clips)), message=f"Rendering clip {i}/{len(clips)}…")
            p = render_clip(source, clip, i, job_dir, segments)
            rendered.append({"id": i, "file": p.name, "url": f"/outputs/{job_id}/{p.name}", "title": clip["title"], "start": round(clip["start"],1), "end": round(clip["end"],1), "score": round(clip["score"],2)})
        update_job(job_id, status="complete", progress=100, message=f"Created {len(rendered)} Shorts.", clips=rendered, duration=duration)
    except Exception as e:
        update_job(job_id, status="failed", progress=0, error=str(e), message="Processing failed")
