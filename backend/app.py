from pathlib import Path

from fastapi import FastAPI
from fastapi import UploadFile
from fastapi import File

from fastapi.responses import FileResponse

from fastapi.middleware.cors import CORSMiddleware

from lecture2tex import audio_to_latex

app = FastAPI(
    title="Audio2TeX",
    version="1.0"
)

# CORS FIX
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent

UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

OUTPUT_DIR = BASE_DIR.parent / "output"


@app.get("/")
def root():

    return {
        "project": "Audio2TeX",
        "status": "running"
    }


@app.post("/convert")
async def convert(
    file: UploadFile = File(...)
):

    filepath = UPLOAD_DIR / file.filename

    with open(filepath, "wb") as buffer:
        buffer.write(
            await file.read()
        )

    transcript, latex = audio_to_latex(
        str(filepath)
    )

    return {
        "filename": file.filename,
        "transcript": transcript,
        "latex": latex,
        "pdf_available": True
    }


@app.get("/pdf")
def download_pdf():

    pdf_file = OUTPUT_DIR / "lecture.pdf"

    if not pdf_file.exists():
        return {
            "error": "PDF not found. Convert a lecture first."
        }

    return FileResponse(
        path=pdf_file,
        media_type="application/pdf",
        filename="lecture.pdf"
    )