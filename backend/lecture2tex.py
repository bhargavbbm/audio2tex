import whisper
import subprocess
from pathlib import Path

from backend.physics_parser import physics_to_latex

MODEL_NAME = "base"

print(f"Loading Whisper model: {MODEL_NAME}")
MODEL = whisper.load_model(MODEL_NAME)
print("Whisper model loaded.")

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR.parent / "output"

OUTPUT_DIR.mkdir(exist_ok=True)

TRANSCRIPT_FILE = OUTPUT_DIR / "transcript.txt"
LATEX_FILE = OUTPUT_DIR / "lecture.tex"
PDF_FILE = OUTPUT_DIR / "lecture.pdf"


def audio_to_latex(audio_file):

    print(f"Transcribing: {audio_file}")

    result = MODEL.transcribe(audio_file)

    transcript = result["text"]

    with open(TRANSCRIPT_FILE, "w", encoding="utf-8") as f:
        f.write(transcript)

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

    try:

        subprocess.run(
            [
                "pdflatex",
                "-interaction=nonstopmode",
                "-output-directory",
                str(OUTPUT_DIR),
                str(LATEX_FILE)
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        print("PDF generated successfully.")

    except FileNotFoundError:

        print("WARNING: pdflatex not found. Skipping PDF generation.")

    except subprocess.CalledProcessError as e:

        print("WARNING: PDF generation failed.")
        print(e.stderr)

    cleanup_extensions = [
        ".aux",
        ".log",
        ".out",
        ".toc",
        ".fls",
        ".fdb_latexmk",
        ".synctex.gz"
    ]

    for file in OUTPUT_DIR.iterdir():

        for ext in cleanup_extensions:

            if file.name.endswith(ext):

                try:
                    file.unlink()

                except Exception:
                    pass

    return transcript, latex_text


if __name__ == "__main__":

    import sys

    if len(sys.argv) < 2:
        print("Usage: python lecture2tex.py <audiofile>")
        sys.exit(1)

    transcript, latex = audio_to_latex(sys.argv[1])

    print("\nDone.")