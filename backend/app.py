"""
Audio2TeX — FastAPI backend
===========================
Architecture
------------
POST /convert   → starts a background job, returns job_id immediately (no timeout risk)
GET  /status/{job_id} → poll for progress / completion
GET  /pdf/{job_id}    → download the compiled PDF
GET  /tex/{job_id}    → download the .tex source
GET  /           → health check
"""

import base64
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from backend.lecture2tex import audio_to_latex

# ── App setup ─────────────────────────────────────────────────────────────────
app = FastAPI(title="Audio2TeX", version="3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,   # must be False when allow_origins=["*"]
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).resolve().parent.parent   # project root
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "output"
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# ── In-memory job store ───────────────────────────────────────────────────────
# { job_id: { "status": "pending"|"processing"|"done"|"error",
#             "progress": str,
#             "result": dict | None,
#             "error": str | None } }
JOBS: dict[str, dict] = {}


# ── Background worker ─────────────────────────────────────────────────────────
def run_job(job_id: str, audio_path: str):
    JOBS[job_id]["status"] = "processing"
    JOBS[job_id]["progress"] = "Transcribing audio with Whisper large-v3…"

    try:
        result = audio_to_latex(audio_path, job_id=job_id, jobs=JOBS)

        # Embed PDF as base64 in the result so the client can download it
        pdf_b64: Optional[str] = None
        if result["pdf_available"]:
            pdf_path = Path(result["pdf_path"])
            if pdf_path.exists():
                pdf_b64 = base64.b64encode(pdf_path.read_bytes()).decode("utf-8")

        JOBS[job_id]["status"] = "done"
        JOBS[job_id]["progress"] = "Complete"
        JOBS[job_id]["result"] = {
            "transcript":    result["transcript"],
            "latex_body":    result["latex_body"],
            "full_tex":      result["full_tex"],
            "pdf_available": result["pdf_available"],
            "pdf_base64":    pdf_b64,
        }

    except Exception as exc:
        JOBS[job_id]["status"] = "error"
        JOBS[job_id]["error"]  = str(exc)
        print(f"[Job {job_id}] ERROR: {exc}")


# ── Endpoints ─────────────────────────────────────────────────────────────────
@app.get("/")
def root():
    return {"project": "Audio2TeX", "status": "running", "version": "3.0"}


@app.post("/convert")
async def convert(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...)
):
    """
    Accepts an audio upload, saves it, queues a background job, and immediately
    returns a job_id.  The client polls /status/{job_id} until done.
    """
    job_id    = str(uuid.uuid4())
    safe_name = Path(file.filename).name   # strip any path components
    filepath  = UPLOAD_DIR / f"{job_id}_{safe_name}"

    # Stream-write to disk (avoids loading entire file into RAM)
    with open(filepath, "wb") as buf:
        while chunk := await file.read(1024 * 1024):
            buf.write(chunk)

    JOBS[job_id] = {
        "status":   "pending",
        "progress": "Upload received, queued for processing…",
        "result":   None,
        "error":    None,
    }

    background_tasks.add_task(run_job, job_id, str(filepath))

    return JSONResponse({"job_id": job_id})


@app.get("/status/{job_id}")
def job_status(job_id: str):
    """Poll this endpoint every 3 s until status == 'done' or 'error'."""
    job = JOBS.get(job_id)
    if not job:
        return JSONResponse({"error": "Unknown job ID"}, status_code=404)

    response: dict = {
        "job_id":   job_id,
        "status":   job["status"],
        "progress": job["progress"],
    }

    if job["status"] == "done":
        response["result"] = job["result"]

    if job["status"] == "error":
        response["error"] = job["error"]

    return JSONResponse(response)


@app.get("/pdf/{job_id}")
def download_pdf(job_id: str):
    """Direct PDF download endpoint (fallback if base64 is too large)."""
    job = JOBS.get(job_id)
    if not job or job["status"] != "done":
        return JSONResponse({"error": "Job not complete"}, status_code=404)

    pdf_file = OUTPUT_DIR / f"{job_id}_lecture.pdf"
    if not pdf_file.exists():
        return JSONResponse({"error": "PDF not found"}, status_code=404)

    return FileResponse(
        path=pdf_file,
        media_type="application/pdf",
        filename="lecture.pdf"
    )


@app.get("/tex/{job_id}")
def download_tex(job_id: str):
    """Direct .tex source download endpoint."""
    job = JOBS.get(job_id)
    if not job or job["status"] != "done":
        return JSONResponse({"error": "Job not complete"}, status_code=404)

    tex_file = OUTPUT_DIR / f"{job_id}_lecture.tex"
    if not tex_file.exists():
        return JSONResponse({"error": "TeX file not found"}, status_code=404)

    return FileResponse(
        path=tex_file,
        media_type="text/plain",
        filename="lecture.tex"
    )
