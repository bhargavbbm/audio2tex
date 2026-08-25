---
title: Audio2Tex
emoji: 🎤
colorFrom: blue
colorTo: purple
sdk: docker
pinned: false
---
# Audio2TeX

Convert physics lecture audio → LaTeX notes using **Whisper large-v3**.

Handles short clips and **2+ hour lectures alike** — long audio is automatically
split into overlapping chunks, transcribed sequentially, and merged back into
one continuous transcript. Every chunk (and every LaTeX section) is cached to
disk as it completes, so an interrupted job can be resumed without redoing
work that already finished.

---

## Repository structure

```
audio2tex/
├── main.py                     ← HF Spaces entrypoint (uvicorn)
├── requirements.txt             ← Python deps
├── packages.txt                 ← System deps (ffmpeg, pdflatex, texlive)
├── .env.example                 ← Template for required environment variables
├── backend/
│   ├── __init__.py
│   ├── app.py                   ← FastAPI: /convert, /status/{id}, /resume/{id}, /pdf/{id}, /tex/{id}
│   ├── lecture2tex.py           ← Orchestrates chunking → Whisper → merge → LaTeX → PDF
│   ├── audio_chunker.py         ← Detects duration, splits long audio into overlapping chunks
│   ├── transcript_merger.py     ← Merges overlapping chunk transcripts without duplicating text
│   └── physics_parser.py        ← Regex + Claude API LaTeX conversion, done in sections
└── frontend/
    ├── index.html
    ├── app.js                    ← Polls /status indefinitely; survives page refresh
    └── style.css
```

### Per-job output layout

Every conversion gets its own directory under `output/<job_id>/`:

```
output/<job_id>/
    chunks/               ← temporary chunk audio (deleted once transcribed)
    transcripts/
        001.json          ← saved as soon as chunk 1 finishes transcribing
        002.json
        002.error.json    ← written instead, if a chunk fails after retries
        ...
    sections/
        001.tex           ← saved as soon as LaTeX section 1 finishes
        002.tex
        ...
    transcript.txt         ← final merged transcript
    lecture.tex             ← final compiled LaTeX source
    lecture.pdf              ← final compiled PDF
    job_state.json            ← {"status": ..., "error": ...} — survives a server restart
    progress.json              ← latest human-readable progress message
```

This is what makes resuming work: if the process crashes or is restarted
mid-job, `output/<job_id>/transcripts/*.json` and `output/<job_id>/sections/*.tex`
already on disk are reused instead of being redone.

---

## How long-audio processing works

1. **Duration detection** (`ffprobe`). Audio under 8 minutes is transcribed
   directly, exactly like before — no chunking overhead.
2. **Chunking** (`audio_chunker.py`): longer audio is split into ~7-minute
   chunks with a 20-second overlap, written as 16kHz mono WAV (the format
   Whisper decodes to internally anyway, so this isn't extra work).
3. **Sequential transcription**: chunks are transcribed one at a time with
   Whisper large-v3 (loaded once, reused for every chunk — safe on a 16 GB
   machine). Each chunk's result is saved to `transcripts/NNN.json`
   immediately. A chunk that fails is retried up to twice; if it still
   fails, its failure is recorded and the pipeline continues with a
   placeholder rather than aborting the whole job.
4. **Merging** (`transcript_merger.py`): the overlapping text at each chunk
   boundary is de-duplicated (word-for-word boundary matching, with a
   fuzzy fallback) so the final transcript doesn't repeat the same sentence
   twice. Merging is skipped across a boundary where a chunk failed, since
   there's a real audio gap there rather than a genuine overlap.
5. **Section-chunked LaTeX conversion** (`physics_parser.py`): the merged
   transcript is grouped into ~3500-character sections and each is sent to
   Claude separately (never the whole 2-hour transcript in one request). A
   short tail of the previous section's finalized LaTeX is carried forward
   purely so notation (e.g. a symbol the lecturer defined earlier) stays
   consistent across sections. Each section is cached to `sections/NNN.tex`.
6. **PDF compilation**: two `pdflatex` passes (for cross-references),
   auxiliary files cleaned up afterward.

---

## Installation (Ubuntu)

### 1. System dependencies

```bash
sudo apt update
sudo apt install ffmpeg
sudo apt install texlive-latex-base texlive-latex-extra texlive-fonts-recommended texlive-science
```

(`packages.txt` lists the same packages for HF Spaces' automatic `apt-get install`.)

### 2. Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Environment variables

```bash
cp .env.example .env
nano .env
```

The only variable the code actually reads is:

| Variable | Required? | Used by | Where to get it |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Optional but recommended | `backend/physics_parser.py` (Claude LaTeX cleanup pass) | https://console.anthropic.com/ |

Without it, Audio2TeX still works end-to-end — it just falls back to the
regex-only LaTeX conversion, which is lower quality on complex spoken math.

**Never commit your real `.env` file.** It's already excluded in `.gitignore`.

### 4. Run the backend

```bash
uvicorn backend.app:app --reload
```

or, matching the HF Spaces entrypoint:

```bash
python main.py
```

A successful startup looks like:

```
[Audio2TeX] Loading Whisper large-v3…
[Audio2TeX] Whisper large-v3 ready.
INFO:     Uvicorn running on http://127.0.0.1:8000
```

(The first line can take a minute or two — Whisper large-v3 is a ~3GB model
being loaded into memory.)

### 5. Run the frontend

The frontend is static HTML/JS/CSS — no build step. Serve it with anything:

```bash
cd frontend
python3 -m http.server 5500
```

Then open `http://localhost:5500`. If your backend isn't running on
`https://bhargavbbm-audio2tex.hf.space`, update `API_URL` at the top of
`frontend/app.js` to point at your backend (e.g. `http://localhost:8000`).

---

## Testing

Start small and work up:

1. **Short clip (~1 min)** — upload, click Convert, confirm you get a
   transcript, `.tex`, and PDF within a few seconds/minutes.
2. **10–20 minutes** — confirm chunking kicks in (`Created N chunks…` in the
   progress messages) and everything still merges correctly.
3. **30 minutes → 1 hour → 2 hours**, progressively. Watch the progress text
   for real chunk counts (`Transcribing chunk 3/14`) and check
   `output/<job_id>/transcripts/` fills in one file per chunk as you go.
4. **Resumability**: while a long job is running, kill the backend process
   (`Ctrl+C`) partway through. Restart it, then:
   ```bash
   curl -X POST http://localhost:8000/resume/<job_id>
   ```
   Poll `/status/<job_id>` again — chunks that already had a saved
   `transcripts/NNN.json` are reused, not re-transcribed.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| PDF never appears, transcript/`.tex` do | `pdflatex` not installed | `sudo apt install texlive-latex-extra texlive-science` |
| LaTeX looks like plain regex output, no real math cleanup | `ANTHROPIC_API_KEY` not set | Add it to `.env` (or HF Space secrets) |
| Job stuck at "Preparing audio…" | `ffprobe`/`ffmpeg` not installed or not on PATH | `sudo apt install ffmpeg` |
| `/status/{job_id}` returns 404 after a restart | Job never actually started (upload failed) or `output/<job_id>/` was deleted | Re-upload the file |
| Long lecture job seems to "vanish" after a while | Old 10-minute frontend polling cap — this has been removed; if you still see it, you're on an old `app.js` | Replace with the updated `frontend/app.js` |
| Whisper transcription is very slow | CPU-only inference of `large-v3` is inherently slow (real-time-ish, sometimes slower) | Expected; do not silently downgrade the model — accuracy is the priority. Consider running the container on a GPU-enabled host if you need faster turnaround |
| Two chunks' text overlaps or drops words at a boundary | Overlap merge false-positive/negative on unusual phrasing | Check `output/<job_id>/transcripts/*.json` — the raw per-chunk text is always preserved even if the merge isn't perfect |

---

## LaTeX in Overleaf

The `.tex` file produced is 100% standard LaTeX. Packages used:
`amsmath`, `amssymb`, `hyperref`, `parskip`, `enumitem`, `fancyhdr`.

Just paste `lecture.tex`'s content into a new Overleaf project and compile.
