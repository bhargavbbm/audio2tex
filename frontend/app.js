/* ─────────────────────────────────────────────────────────────────────────────
   Audio2TeX — frontend
   Architecture:
     1. POST /convert → get job_id immediately (no timeout)
     2. Poll GET /status/{job_id} every 3 s
     3. When done, render results + offer downloads
   ───────────────────────────────────────────────────────────────────────────── */

const API_URL = "https://bhargavbbm-audio2tex.hf.space";

/* State */
let currentJobId   = null;
let currentLatex   = "";
let currentPdfB64  = null;
let pollTimer      = null;
let pollAttempts   = 0;
const MAX_POLLS    = 200;   // 200 × 3 s = 10 minutes max wait

/* ── Progress bar steps ──────────────────────────────────────────────────────
   We fake smooth progress because the real work is opaque to the browser.
   Each stage maps to a % range; the bar fills smoothly within each range.   */
const STAGES = {
    uploading:      { pct: 5,  icon: "📤", label: "Uploading audio…" },
    waking:         { pct: 10, icon: "⏳", label: "Waking server (first use can take ~30 s)…" },
    transcribing:   { pct: 15, icon: "🎤", label: "Transcribing with Whisper large-v3…" },
    converting:     { pct: 80, icon: "📝", label: "Converting transcript to LaTeX…" },
    compiling:      { pct: 90, icon: "📄", label: "Compiling PDF…" },
    done:           { pct: 100, icon: "✅", label: "Done!" },
    error:          { pct: 0,  icon: "❌", label: "Error" },
};

/* ── Drag-and-drop ─────────────────────────────────────────────────────────── */
const dropZone = document.getElementById("drop-zone");

dropZone.addEventListener("dragover", e => {
    e.preventDefault();
    dropZone.classList.add("dragover");
});
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragover"));
dropZone.addEventListener("drop", e => {
    e.preventDefault();
    dropZone.classList.remove("dragover");
    const files = e.dataTransfer.files;
    if (files.length > 0) assignFile(files[0]);
});
document.getElementById("audioFile").addEventListener("change", e => {
    if (e.target.files.length > 0) assignFile(e.target.files[0]);
});

function assignFile(file) {
    // Put the file into the hidden input via a DataTransfer trick
    const dt = new DataTransfer();
    dt.items.add(file);
    document.getElementById("audioFile").files = dt.files;
    document.getElementById("file-label").textContent = `Selected: ${file.name}`;
}

/* ── Main: start conversion ──────────────────────────────────────────────────  */
async function startConversion() {
    const fileInput = document.getElementById("audioFile");
    const file = fileInput.files[0];
    if (!file) {
        alert("Please select an audio file first.");
        return;
    }

    /* Reset UI */
    currentJobId   = null;
    currentLatex   = "";
    currentPdfB64  = null;
    pollAttempts   = 0;
    if (pollTimer) clearInterval(pollTimer);

    document.getElementById("results-section").hidden = true;
    document.getElementById("progress-card").hidden   = false;
    document.getElementById("convertBtn").disabled    = true;

    setProgress("waking", 8);

    /* 1. Wake the server (fire-and-forget GET to "/") */
    try {
        await fetch(`${API_URL}/`, { method: "GET", mode: "cors" });
    } catch (_) { /* ignore — Space may still be booting */ }

    /* 2. Upload the file */
    setProgress("uploading", 5);

    const formData = new FormData();
    formData.append("file", file);

    let jobId;
    try {
        const res = await fetch(`${API_URL}/convert`, {
            method: "POST",
            body: formData,
        });

        if (!res.ok) {
            const txt = await res.text();
            throw new Error(`Server error ${res.status}: ${txt}`);
        }

        const data = await res.json();
        jobId = data.job_id;

        if (!jobId) throw new Error("Server did not return a job_id.");

    } catch (err) {
        showError(err.message.includes("Failed to fetch")
            ? "Cannot reach the server. Wait 30 s and try again."
            : err.message);
        return;
    }

    currentJobId = jobId;
    setProgress("transcribing", 15);

    /* 3. Poll /status/{job_id} */
    pollTimer = setInterval(() => pollStatus(jobId), 3000);
}

/* ── Polling ─────────────────────────────────────────────────────────────────  */
async function pollStatus(jobId) {
    pollAttempts++;
    if (pollAttempts > MAX_POLLS) {
        clearInterval(pollTimer);
        showError("Timed out after 10 minutes. Try a shorter audio clip.");
        return;
    }

    let data;
    try {
        const res = await fetch(`${API_URL}/status/${jobId}`);
        if (!res.ok) return;   // transient error — keep polling
        data = await res.json();
    } catch (_) {
        return;   // network hiccup — keep polling
    }

    /* Update progress label from server message */
    const progress = data.progress || "";
    if (progress.toLowerCase().includes("transcri")) {
        setProgress("transcribing", Math.min(15 + pollAttempts * 0.8, 75));
    } else if (progress.toLowerCase().includes("convert")) {
        setProgress("converting", 80);
    } else if (progress.toLowerCase().includes("pdflatex") || progress.toLowerCase().includes("compil")) {
        setProgress("compiling", 90);
    }

    if (data.status === "done") {
        clearInterval(pollTimer);
        onComplete(data.result);

    } else if (data.status === "error") {
        clearInterval(pollTimer);
        showError(data.error || "An unknown error occurred on the server.");
    }
}

/* ── Job complete ────────────────────────────────────────────────────────────  */
function onComplete(result) {
    setProgress("done", 100);

    /* Transcript */
    document.getElementById("transcript").textContent =
        result.transcript || "(empty transcript)";

    /* Full LaTeX — this is what goes to Overleaf */
    const tex = result.full_tex || result.latex_body || "(no LaTeX returned)";
    document.getElementById("latex-output").textContent = tex;
    currentLatex  = tex;
    currentPdfB64 = result.pdf_base64 || null;

    /* PDF button label */
    const pdfNote = document.getElementById("pdf-note");
    if (result.pdf_available && currentPdfB64) {
        document.getElementById("btn-pdf").disabled = false;
        pdfNote.hidden = true;
    } else {
        document.getElementById("btn-pdf").disabled = true;
        pdfNote.hidden   = false;
        pdfNote.textContent =
            "PDF compilation unavailable on this server. "
            + "Copy the LaTeX and paste into Overleaf to get a PDF.";
    }

    document.getElementById("results-section").hidden = false;
    document.getElementById("convertBtn").disabled    = false;
    document.getElementById("progress-card").hidden   = true;

    showToast("Conversion complete!");
}

/* ── Download helpers ────────────────────────────────────────────────────────  */
function copyText(elementId) {
    const text = document.getElementById(elementId).textContent;
    navigator.clipboard.writeText(text).then(() => showToast("Copied to clipboard."));
}

function downloadTex() {
    if (!currentLatex) return;
    triggerDownload(new Blob([currentLatex], { type: "text/plain" }), "lecture.tex");
    showToast("Downloading lecture.tex…");
}

function downloadPdf() {
    if (currentPdfB64) {
        /* PDF was returned as base64 — pure client-side download, no CORS */
        try {
            const bytes = Uint8Array.from(atob(currentPdfB64), c => c.charCodeAt(0));
            triggerDownload(new Blob([bytes], { type: "application/pdf" }), "lecture.pdf");
            showToast("Downloading lecture.pdf…");
        } catch (e) {
            console.error("PDF decode failed:", e);
            fallbackPdfTab();
        }
    } else if (currentJobId) {
        /* Fallback: open /pdf/{job_id} in new tab (no CORS on navigation) */
        fallbackPdfTab();
    } else {
        showToast("No PDF available. Use the .tex file with Overleaf.");
    }
}

function fallbackPdfTab() {
    if (currentJobId) {
        window.open(`${API_URL}/pdf/${currentJobId}`, "_blank");
    }
}

function triggerDownload(blob, filename) {
    const a  = document.createElement("a");
    a.href   = URL.createObjectURL(blob);
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(a.href), 10_000);
}

/* ── UI helpers ──────────────────────────────────────────────────────────────  */
function setProgress(stage, pct) {
    const s = STAGES[stage] || STAGES.waking;
    document.getElementById("progress-icon").textContent = s.icon;
    document.getElementById("progress-text").textContent = s.label;
    document.getElementById("progress-bar").style.width  = `${pct}%`;
}

function showError(msg) {
    setProgress("error", 0);
    document.getElementById("progress-icon").textContent = "❌";
    document.getElementById("progress-text").textContent = msg;
    document.getElementById("convertBtn").disabled       = false;
}

function showToast(msg) {
    const t = document.getElementById("toast");
    t.textContent = msg;
    t.classList.add("show");
    setTimeout(() => t.classList.remove("show"), 3000);
}
