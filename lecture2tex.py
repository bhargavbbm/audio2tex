import whisper
import subprocess
import sys
from pathlib import Path
from physics_parser import physics_to_latex

MODEL_NAME = "large"

if len(sys.argv) < 2:
    print("Usage: python lecture2tex.py <audiofile>")
    sys.exit(1)

AUDIO_FILE = sys.argv[1]

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)

TRANSCRIPT_FILE = OUTPUT_DIR / "transcript.txt"
LATEX_FILE = OUTPUT_DIR / "lecture.tex"

print(f"Loading model: {MODEL_NAME}")
model = whisper.load_model(MODEL_NAME)

print(f"Transcribing: {AUDIO_FILE}")
result = model.transcribe(AUDIO_FILE)
transcript = result["text"]

with open(TRANSCRIPT_FILE, "w", encoding="utf-8") as f:
    f.write(transcript)

print(f"Saved: {TRANSCRIPT_FILE}")
latex_text = physics_to_latex(transcript)

with open(LATEX_FILE, "w", encoding="utf-8") as f:

    f.write(r"""
\documentclass[12pt]{article}

\usepackage{amsmath}
\usepackage{amssymb}
\usepackage[hidelinks]{hyperref}
\title{Lecture Notes}
\author{Lecture2TeX}
\date{\today}
\begin{document}
\maketitle
""")
    f.write(latex_text)
    f.write(r"""
\end{document}
""")
print(f"Saved: {LATEX_FILE}")
print("Compiling PDF...")

subprocess.run(
    [
        "pdflatex",
        "-interaction=nonstopmode",
        "-output-directory=output",
        str(LATEX_FILE)
    ],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL
)

print("PDF generated.")
extensions_to_remove = [
    ".aux",
    ".log",
    ".out",
    ".toc",
    ".fls",
    ".fdb_latexmk",
    ".synctex.gz"
]

for file in OUTPUT_DIR.iterdir():
    for ext in extensions_to_remove:
        if file.name.endswith(ext):
            try:
                file.unlink()
            except Exception:
                pass
print("Temporary files removed.")
print("Done.")
