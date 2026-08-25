"""
Audio2TeX — FastAPI backend
===========================
Architecture
------------
POST /convert         → starts a background job, returns job_id immediately (no timeout risk)
GET  /status/{job_id} → poll for progress / completion
POST /resume/{job_id} → resume an interrupted job (crashed process, server restart, etc.)
GET  /pdf/{job_id}    → download the compiled PDF
GET  /tex/{job_id}    → download the .tex source
GET  /           → health check

Job state is kept in memory (JOBS) for speed, but every progress update is
also mirrored to disk at output/<job_id>/job_state.json and
output/<job_id>/progress.json. This means /status can still answer
correctly even if the JOBS dict was lost (e.g. the server process
restarted), and /resume can relaunch a job that never finished — the
underlying pipeline (backend/lecture2tex.py) skips any audio chunks and
LaTeX sections that were already completed, so resuming is cheap.
"""

import base64
import json
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from backend.lecture2tex import audio_to_latex

# ── App setup ───────────────────────────────────────────────────────────[...]
app = FastAPI(title="Audio2TeX", version="4.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,   # must be False when allow_origins=["*"]
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# ── Paths ────────────────────────────────────────────────────────────–[...]
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


# ── Disk persistence helpers ────────────────────────────────────────────────
def _job_dir(job_id: str) -> Path:
    d = OUTPUT_DIR / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _persist_status(job_id: str, status: str, error: Optional[str] = None) -> None:
    """Mirror the top-level job status to disk so /status can recover it
    even if this process restarts mid-job."""
    try:
        meta = {"status": status, "error": error}
        (_job_dir(job_id) / "job_state.json").write_text(json.dumps(meta), encoding="utf-8")
    except Exception:
        pass  # persistence is best-effort, never fatal to the job itself


def _read_progress(job_id: str) -> str:
    progress_file = OUTPUT_DIR / job_id / "progress.json"
    if progress_file.exists():
        try:
            return json.loads(progress_file.read_text(encoding="utf-8")).get("progress", "")
        except Exception:
            return ""
    return ""


def _load_result_from_disk(job_id: str) -> Optional[dict]:
    """Reconstruct a completed job's result from files on disk. Used when a
    job finished in a previous process but JOBS no longer has it in memory."""
    job_dir = OUTPUT_DIR / job_id
    transcript_file = job_dir / "transcript.txt"
    tex_file        = job_dir / "lecture.tex"
    pdf_file        = job_dir / "lecture.pdf"

    if not tex_file.exists():
        return None

    pdf_b64: Optional[str] = None
    pdf_available = pdf_file.exists()
    if pdf_available:
        try:
            pdf_b64 = base64.b64encode(pdf_file.read_bytes()).decode("utf-8")
        except Exception:
            pdf_available = False

    full_tex = tex_file.read_text(encoding="utf-8")

    return {
        "transcript":    transcript_file.read_text(encoding="utf-8") if transcript_file.exists() else "",
        "latex_body":    full_tex,
        "full_tex":      full_tex,
        "pdf_available": pdf_available,
        "pdf_base64":    pdf_b64,
    }


# ── Background worker ────────────────────────────────────────────────────────–[...]
def run_job(job_id: str, audio_path: str):
    JOBS[job_id]["status"] = "processing"
    JOBS[job_id]["progress"] = "Preparing audio…"
    _persist_status(job_id, "processing")

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
        _persist_status(job_id, "done")

    except Exception as exc:
        JOBS[job_id]["status"] = "error"
        JOBS[job_id]["error"]  = str(exc)
        _persist_status(job_id, "error", error=str(exc))
        print(f"[Job {job_id}] ERROR: {exc}")


# ── Endpoints ───────────────────────────────────────────────────────────[...]
@app.get("/")
def root():
    return {"project": "Audio2TeX", "status": "running", "version": "4.0"}


@app.post("/convert")
async def convert(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...)
):
    """
    Accepts an audio upload, saves it, queues a background job, and immediately
    returns a job_id.  The client polls /status/{job_id} until done — for as
    long as that takes (2+ hour lectures are expected).
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
    _persist_status(job_id, "pending")

    background_tasks.add_task(run_job, job_id, str(filepath))

    return JSONResponse({"job_id": job_id})


@app.post("/resume/{job_id}")
def resume(job_id: str, background_tasks: BackgroundTasks):
    """
    Resume a job that didn't finish — e.g. the server process crashed or
    restarted mid-transcription. Finds the originally-uploaded audio file
    and relaunches the pipeline; already-completed audio chunks
    (output/<job_id>/transcripts/*.json) and LaTeX sections
    (output/<job_id>/sections/*.tex) are reused rather than redone.
    """
    matches = sorted(UPLOAD_DIR.glob(f"{job_id}_*"))
    if not matches:
        return JSONResponse(
            {"error": "Original uploaded audio file not found — cannot resume. Please re-upload."},
            status_code=404,
        )

    audio_path = str(matches[0])

    JOBS[job_id] = {
        "status":   "pending",
        "progress": "Resuming job — reusing already-completed chunks…",
        "result":   None,
        "error":    None,
    }
    _persist_status(job_id, "pending")

    background_tasks.add_task(run_job, job_id, audio_path)

    return JSONResponse({"job_id": job_id, "resumed": True})


@app.get("/status/{job_id}")
def job_status(job_id: str):
    """Poll this endpoint until status == 'done' or 'error'. No time limit —
    a 2-hour lecture may legitimately take a long time to process."""
    job = JOBS.get(job_id)

    if not job:
        # Not in memory (e.g. this process restarted) — try to recover
        # state from disk.
        state_file = OUTPUT_DIR / job_id / "job_state.json"
        if not state_file.exists():
            return JSONResponse({"error": "Unknown job ID"}, status_code=404)

        try:
            meta = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            return JSONResponse({"error": "Unknown job ID"}, status_code=404)

        response: dict = {
            "job_id":   job_id,
            "status":   meta.get("status", "error"),
            "progress": _read_progress(job_id),
        }
        if response["status"] == "done":
            result = _load_result_from_disk(job_id)
            if result is not None:
                response["result"] = result
            else:
                response["status"] = "error"
                response["error"] = "Job reported done but output files are missing."
        elif response["status"] == "error":
            response["error"] = meta.get("error") or "Job failed. You can retry it via /resume/{job_id}."
        return JSONResponse(response)

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
    pdf_file = OUTPUT_DIR / job_id / "lecture.pdf"
    if not pdf_file.exists():
        return JSONResponse({"error": "PDF not found (job may not be complete)"}, status_code=404)

    return FileResponse(
        path=pdf_file,
        media_type="application/pdf",
        filename="lecture.pdf"
    )


@app.get("/tex/{job_id}")
def download_tex(job_id: str):
    """Direct .tex source download endpoint."""
    tex_file = OUTPUT_DIR / job_id / "lecture.tex"
    if not tex_file.exists():
        return JSONResponse({"error": "TeX file not found (job may not be complete)"}, status_code=404)

    return FileResponse(
        path=tex_file,
        media_type="text/plain",
        filename="lecture.tex"
    )
