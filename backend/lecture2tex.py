"""
lecture2tex.py
==============
Whisper large-v3 transcription → physics LaTeX conversion → PDF compilation.

Uses per-job output filenames so concurrent requests never collide.
"""

import subprocess
from pathlib import Path
from typing import Optional

import whisper

from backend.physics_parser import physics_to_latex

# ── Model ─────────────────────────────────────────────────────────────────────
# large-v3: best accuracy, ~3 GB RAM.  HF free tier has ~16 GB — fine.
# fp16=False is set at transcribe() time (CPU doesn't support fp16).
MODEL_NAME = "large-v3"

print(f"[Audio2TeX] Loading Whisper {MODEL_NAME}…")
MODEL = whisper.load_model(MODEL_NAME)
print(f"[Audio2TeX] Whisper {MODEL_NAME} ready.")

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

CLEANUP_EXTS = {".aux", ".log", ".out", ".toc", ".fls", ".fdb_latexmk", ".synctex.gz"}

# ── LaTeX document template ───────────────────────────────────────────────────
LATEX_TEMPLATE = r"""\documentclass[12pt]{{article}}

\usepackage[a4paper,left=1in,right=1in,bottom=1in,top=1.2in]{{geometry}}

\usepackage{{amsmath}}
\usepackage{{amssymb}}
\usepackage[hidelinks]{{hyperref}}
\usepackage{{parskip}}
\usepackage{{enumitem}}
\usepackage{{fancyhdr}}

\pagestyle{{fancy}}
\fancyhf{{}}
\fancyhead[L]{{Transcribed}}
\fancyhead[R]{{\thepage}}

\title{{Transcribed}}
\author{{Audio2TeX}}
\date{{\today}}

\begin{{document}}
\newpage

{body}

\end{{document}}
"""


def audio_to_latex(
    audio_file: str,
    job_id: str = "local",
    jobs: Optional[dict] = None,
) -> dict:
    """
    Full pipeline: audio → transcript → LaTeX body → full .tex → PDF.

    Parameters
    ----------
    audio_file : path to the uploaded audio file
    job_id     : used to create unique per-job output filenames
    jobs       : shared JOBS dict for progress updates (None when running standalone)
    """

    def update(msg: str):
        print(f"[Job {job_id}] {msg}")
        if jobs and job_id in jobs:
            jobs[job_id]["progress"] = msg

    # ── 1. Transcription ──────────────────────────────────────────────────────
    update("Transcribing audio with Whisper large-v3 (this is the slow step)…")

    transcription = MODEL.transcribe(
        audio_file,
        fp16=False,        # CPU doesn't support fp16
        verbose=False,
        language=None,     # auto-detect language
        task="transcribe",
        # These settings improve accuracy on lecture audio:
        condition_on_previous_text=True,
        compression_ratio_threshold=2.4,
        no_speech_threshold=0.6,
        word_timestamps=False,
    )

    transcript: str = transcription["text"].strip()
    detected_lang   = transcription.get("language", "unknown")
    update(f"Transcription done ({len(transcript)} chars, language: {detected_lang}).")

    # Save raw transcript
    transcript_file = OUTPUT_DIR / f"{job_id}_transcript.txt"
    transcript_file.write_text(transcript, encoding="utf-8")

    # ── 2. LaTeX conversion ───────────────────────────────────────────────────
    update("Converting transcript to LaTeX…")
    latex_body = physics_to_latex(transcript)

    full_tex = LATEX_TEMPLATE.format(body=latex_body)

    tex_file = OUTPUT_DIR / f"{job_id}_lecture.tex"
    tex_file.write_text(full_tex, encoding="utf-8")

    # ── 3. PDF compilation ────────────────────────────────────────────────────
    pdf_file      = OUTPUT_DIR / f"{job_id}_lecture.pdf"
    pdf_available = False

    update("Compiling PDF with pdflatex…")

    try:
        # Two passes: first builds .aux/.toc, second resolves \tableofcontents
        for pass_num in (1, 2):
            update(f"pdflatex pass {pass_num}/2…")
            proc = subprocess.run(
                [
                    "pdflatex",
                    "-interaction=nonstopmode",
                    f"-output-directory={OUTPUT_DIR}",
                    # Rename the output .pdf to our job-id filename
                    f"-jobname={job_id}_lecture",
                    str(tex_file),
                ],
                capture_output=True,
                text=True,
                timeout=180,
            )
            if proc.returncode != 0:
                print(f"[Job {job_id}] pdflatex stderr:\n{proc.stderr[-2000:]}")
                raise subprocess.CalledProcessError(proc.returncode, "pdflatex", proc.stdout, proc.stderr)

        pdf_available = pdf_file.exists()
        update("PDF compiled successfully." if pdf_available else "PDF missing after pdflatex.")

    except FileNotFoundError:
        update("WARNING: pdflatex not found on this server. Install texlive via packages.txt.")

    except subprocess.TimeoutExpired:
        update("ERROR: pdflatex timed out after 3 minutes.")

    except subprocess.CalledProcessError as e:
        update(f"ERROR: pdflatex failed (exit {e.returncode}). LaTeX .tex file is still available.")

    finally:
        # Clean up auxiliary files for this job
        for f in OUTPUT_DIR.iterdir():
            stem = f"{job_id}_lecture"
            if f.stem == stem and f.suffix in CLEANUP_EXTS:
                try:
                    f.unlink()
                except Exception:
                    pass

    return {
        "transcript":    transcript,
        "latex_body":    latex_body,
        "full_tex":      full_tex,
        "pdf_available": pdf_available,
        "tex_path":      str(tex_file),
        "pdf_path":      str(pdf_file),
    }


# ── CLI entrypoint ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m backend.lecture2tex <audiofile>")
        sys.exit(1)

    out = audio_to_latex(sys.argv[1])
    print(f"\nTranscript:\n{out['transcript'][:500]}\n…")
    print(f"\nPDF available: {out['pdf_available']}")
    print(f"TeX file: {out['tex_path']}")
