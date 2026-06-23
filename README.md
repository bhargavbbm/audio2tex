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

---

## Repository structure

```
audio2tex/
├── main.py                  ← HF Spaces entrypoint (uvicorn)
├── requirements.txt         ← Python deps
├── packages.txt             ← System deps (ffmpeg, pdflatex, texlive)
├── backend/
│   ├── __init__.py
│   ├── app.py               ← FastAPI: /convert, /status/{id}, /pdf/{id}
│   ├── lecture2tex.py       ← Whisper transcription + PDF compilation
│   └── physics_parser.py   ← Regex + Claude API LaTeX conversion
└── frontend/
    ├── index.html
    ├── app.js
    └── style.css
```

---

## HuggingFace Space setup

### 1. Create the Space

- Go to https://huggingface.co/new-space
- SDK: **Docker** → pick "Blank"  (or FastAPI template)
- Name: `audio2tex`

### 2. Upload all backend files

Push the repo root (all files above) to the Space.

### 3. Set secrets (optional but recommended for Claude LaTeX pass)

In your Space → Settings → Repository Secrets:

| Name | Value |
|------|-------|
| `ANTHROPIC_API_KEY` | your Anthropic key |

Without this key, the app still works — it just uses regex-only LaTeX conversion.

### 4. packages.txt installs pdflatex automatically

HF Spaces reads `packages.txt` and runs `apt-get install` on startup.
This installs `texlive-latex-extra`, `texlive-science`, `ffmpeg`, etc.
**PDF download will work once this is installed.**

### 5. Frontend (Netlify / Vercel)

Upload the `frontend/` folder.  The `API_URL` in `app.js` already points to your HF Space.
Change it if your Space URL is different.

---

## How it works

1. **POST /convert** — receives audio, saves it, returns `job_id` immediately
2. Frontend polls **GET /status/{job_id}** every 3 seconds
3. Background job runs:
   - Whisper large-v3 transcription (slowest step, ~1–2 min for 30-min lecture)
   - Regex pass (Greek letters, operators, equations)
   - Claude claude-sonnet-4-6 pass (if `ANTHROPIC_API_KEY` set) — intelligent math formatting
   - pdflatex compilation (2 passes for table of contents)
4. PDF returned as **base64** inside JSON — no second request, no CORS issues
5. Frontend offers "Download .tex" and "Download PDF" buttons

---

## LaTeX in Overleaf

The `.tex` file produced is 100% standard LaTeX.  Packages used:
- `amsmath`, `amssymb`, `amsthm`, `mathtools`
- `geometry`, `parskip`, `microtype`, `lmodern`
- `hyperref`

Just paste the `.tex` content into a new Overleaf project and compile.
