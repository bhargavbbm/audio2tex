"""
lecture2tex.py
==============
Whisper large-v3 transcription → physics LaTeX conversion → PDF compilation.

Long-audio pipeline (2+ hours)
-------------------------------
1. Detect duration (ffprobe). Short audio (<8 min) is transcribed directly,
   exactly as before — no chunking overhead.
2. Long audio is split into ~5-10 minute overlapping chunks (audio_chunker.py).
3. Each chunk is transcribed with Whisper large-v3 SEQUENTIALLY (safe on a
   16 GB machine — the model is loaded once and reused) and its result is
   saved to output/<job_id>/transcripts/NNN.json as soon as it's done. If a
   chunk was already transcribed in a previous (crashed/interrupted) run,
   it is loaded from disk instead of being re-transcribed. This is what
   makes the job resumable.
4. Chunk transcripts are merged with overlap-aware de-duplication
   (transcript_merger.py) into one continuous transcript.
5. The transcript is converted to LaTeX in logical sections (not as one
   giant LLM request) — see physics_parser.py.
6. The final .tex is compiled to PDF with pdflatex.

Uses per-job output directories (output/<job_id>/) so concurrent requests
never collide and every job's chunk/transcript/tex/pdf history is kept
together, per Audio2TeX's job structure.
"""

import json
import subprocess
from pathlib import Path
from typing import Optional

import whisper

from backend import audio_chunker, transcript_merger
from backend.physics_parser import physics_to_latex

# ── Model ─────────────────────────────────────────────────────────────────────
# large-v3: best accuracy, ~3 GB RAM. Kept as the default — accuracy is the
# priority for lecture transcription, per project requirements.
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

# ── Chunking configuration ──────────────────────────────────────────────────
CHUNK_SECONDS   = 420   # 7 minutes — within the requested 5-10 minute range
OVERLAP_SECONDS = 20    # small overlap so sentences at boundaries aren't cut
MAX_CHUNK_RETRIES = 2   # total attempts per chunk = 1 + MAX_CHUNK_RETRIES

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


def _job_dir(job_id: str) -> Path:
    d = OUTPUT_DIR / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _transcribe_chunk(
    chunk_path: str,
    chunk_index: int,
    total_chunks: int,
    transcripts_dir: Path,
    update,
) -> tuple[str, bool]:
    """
    Transcribe a single chunk, or reuse a previously-saved result.
    Retries on failure; if all attempts fail, records the error and returns
    a placeholder so the rest of the pipeline can continue (per the
    "don't destroy completed work" requirement).

    Returns (text, ok) — `ok` is False if this chunk ultimately failed, so
    the merge step knows not to attempt overlap-trimming across the gap.
    """
    json_path = transcripts_dir / f"{chunk_index:03d}.json"

    if json_path.exists():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            update(f"Chunk {chunk_index}/{total_chunks} already transcribed — reusing saved result.")
            return data["text"], True
        except Exception:
            pass  # corrupted cache file — fall through and re-transcribe

    last_err: Optional[Exception] = None
    for attempt in range(1, MAX_CHUNK_RETRIES + 2):
        try:
            suffix = f" (retry {attempt - 1})" if attempt > 1 else ""
            update(f"Transcribing chunk {chunk_index}/{total_chunks}{suffix}…")

            result = MODEL.transcribe(
                str(chunk_path),
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

            text = result["text"].strip()
            data = {
                "text": text,
                "language": result.get("language", "unknown"),
                "segments": [
                    {"start": s.get("start"), "end": s.get("end"), "text": s.get("text")}
                    for s in result.get("segments", [])
                ],
            }
            json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            return text, True

        except Exception as e:
            last_err = e
            update(f"WARNING: chunk {chunk_index}/{total_chunks} failed on attempt {attempt}: {e}")

    # Exhausted retries — preserve what we know and keep the pipeline going.
    error_path = transcripts_dir / f"{chunk_index:03d}.error.json"
    try:
        error_path.write_text(json.dumps({"error": str(last_err)}), encoding="utf-8")
    except Exception:
        pass
    update(f"ERROR: chunk {chunk_index}/{total_chunks} failed after {MAX_CHUNK_RETRIES + 1} attempts — skipping it.")
    return f"[Transcription unavailable for this segment — chunk {chunk_index} failed: {last_err}]", False


def audio_to_latex(
    audio_file: str,
    job_id: str = "local",
    jobs: Optional[dict] = None,
) -> dict:
    """
    Full pipeline: audio → chunked transcript → LaTeX body → full .tex → PDF.

    Parameters
    ----------
    audio_file : path to the uploaded audio file
    job_id     : used to create a unique per-job output directory
                 (output/<job_id>/) and to enable resuming
    jobs       : shared JOBS dict for progress updates (None when running standalone)
    """
    job_dir          = _job_dir(job_id)
    chunks_dir        = job_dir / "chunks"
    transcripts_dir   = job_dir / "transcripts"
    chunks_dir.mkdir(exist_ok=True)
    transcripts_dir.mkdir(exist_ok=True)

    def update(msg: str):
        print(f"[Job {job_id}] {msg}")
        if jobs and job_id in jobs:
            jobs[job_id]["progress"] = msg
        try:
            (job_dir / "progress.json").write_text(
                json.dumps({"progress": msg}), encoding="utf-8"
            )
        except Exception:
            pass  # progress persistence is best-effort, never fatal

    # ── 1. Detect duration & split into chunks (if needed) ─────────────────────
    update("Preparing audio…")
    try:
        duration = audio_chunker.get_duration_seconds(audio_file)
        h, rem = divmod(int(duration), 3600)
        m, s = divmod(rem, 60)
        update(f"Audio duration: {h}h {m}m {s}s.")
    except Exception as e:
        duration = None
        update(f"WARNING: could not detect audio duration ({e}).")

    try:
        chunk_infos = audio_chunker.split_into_chunks(
            audio_file, chunks_dir, chunk_seconds=CHUNK_SECONDS, overlap_seconds=OVERLAP_SECONDS
        )
    except Exception as e:
        update(f"WARNING: automatic chunking failed ({e}); falling back to single-pass transcription.")
        chunk_infos = [{"index": 1, "path": audio_file, "start": 0.0, "end": duration or 0.0}]

    total_chunks = len(chunk_infos)
    if total_chunks == 1:
        update("Audio is short enough to transcribe in a single pass — no chunking needed.")
    else:
        update(f"Created {total_chunks} chunks (~{CHUNK_SECONDS // 60}-minute segments, {OVERLAP_SECONDS}s overlap).")

    # ── 2. Transcribe chunks sequentially (resumable, memory-safe) ─────────────
    chunk_texts: list[str] = []
    chunk_ok: list[bool] = []
    for info in chunk_infos:
        text, ok = _transcribe_chunk(info["path"], info["index"], total_chunks, transcripts_dir, update)
        chunk_texts.append(text)
        chunk_ok.append(ok)
        pct = 5 + round(65 * info["index"] / total_chunks)
        update(f"Progress: {pct}% (chunk {info['index']}/{total_chunks} done)")

    # ── 3. Merge overlapping chunk text into one transcript ────────────────────
    if total_chunks > 1:
        update("All chunks transcribed. Merging overlapping text…")
        transcript = transcript_merger.merge_transcripts(chunk_texts, chunk_ok).strip()
    else:
        transcript = (chunk_texts[0] if chunk_texts else "").strip()

    update(f"Transcription complete ({len(transcript)} chars).")

    transcript_file = job_dir / "transcript.txt"
    transcript_file.write_text(transcript, encoding="utf-8")

    # Clean up chunk audio files now that they're merged — keep the small
    # JSON transcripts (needed for resumability / auditing), just drop the
    # (potentially large) intermediate WAVs.
    for info in chunk_infos:
        p = Path(info["path"])
        if p.parent == chunks_dir and p.exists():
            try:
                p.unlink()
            except Exception:
                pass

    # ── 4. LaTeX conversion, done in logical sections (not one huge request) ───
    update("Converting transcript to LaTeX…")
    try:
        latex_body = physics_to_latex(transcript, job_id=job_id, jobs=jobs)
    except Exception as e:
        update(f"WARNING: section-based LaTeX conversion failed ({e}); using raw transcript as a fallback.")
        latex_body = transcript

    full_tex = LATEX_TEMPLATE.format(body=latex_body)

    tex_file = job_dir / "lecture.tex"
    tex_file.write_text(full_tex, encoding="utf-8")

    # ── 5. PDF compilation ────────────────────────────────────────────────────
    pdf_file      = job_dir / "lecture.pdf"
    pdf_available = False

    update("Compiling PDF with pdflatex…")

    try:
        # Two passes: first builds .aux/.toc, second resolves cross-references
        for pass_num in (1, 2):
            update(f"pdflatex pass {pass_num}/2…")
            proc = subprocess.run(
                [
                    "pdflatex",
                    "-interaction=nonstopmode",
                    f"-output-directory={job_dir}",
                    "-jobname=lecture",
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
        update("ERROR: pdflatex timed out after 3 minutes. Transcript and .tex are still available.")

    except subprocess.CalledProcessError as e:
        update(f"ERROR: pdflatex failed (exit {e.returncode}). Transcript and .tex file are still available.")

    finally:
        # Clean up auxiliary files for this job
        for f in job_dir.iterdir():
            if f.stem == "lecture" and f.suffix in CLEANUP_EXTS:
                try:
                    f.unlink()
                except Exception:
                    pass

    update("Complete.")

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
    import uuid

    if len(sys.argv) < 2:
        print("Usage: python -m backend.lecture2tex <audiofile>")
        sys.exit(1)

    out = audio_to_latex(sys.argv[1], job_id=str(uuid.uuid4()))
    print(f"\nTranscript:\n{out['transcript'][:500]}\n…")
    print(f"\nPDF available: {out['pdf_available']}")
    print(f"TeX file: {out['tex_path']}")
