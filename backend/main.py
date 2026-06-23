"""
HuggingFace Spaces entrypoint.
HF looks for app.py or main.py at the repo root.
"""
import uvicorn
from backend.app import app  # noqa: F401 — imported so HF can also use `app` directly

if __name__ == "__main__":
    uvicorn.run(
        "backend.app:app",
        host="0.0.0.0",
        port=7860,          # HF Spaces default port
        timeout_keep_alive=600,   # 10-min keep-alive for long transcriptions
        workers=1,          # 1 worker — Whisper model is not thread-safe across workers
    )
