let currentLatex = "";

const dropZone =
    document.getElementById("drop-zone");

dropZone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropZone.classList.add("dragover");
});

dropZone.addEventListener("dragleave", () => {
    dropZone.classList.remove("dragover");
});

dropZone.addEventListener("drop", (e) => {

    e.preventDefault();

    dropZone.classList.remove("dragover");

    const files = e.dataTransfer.files;

    if (files.length > 0) {
        document.getElementById("audioFile").files = files;
    }
});

async function uploadAudio() {

    const file =
        document.getElementById("audioFile").files[0];

    if (!file) {
        alert("Select an audio file.");
        return;
    }

    const status =
        document.getElementById("status");

    status.innerHTML =
        "⏳ Uploading...";

    const formData = new FormData();

    formData.append("file", file);

    status.innerHTML =
        "🎤 Transcribing...";

    const response =
        await fetch(
            "https://bhargavbbm-audio2tex.hf.space/convert",
            {
                method: "POST",
                body: formData
            }
        );

    status.innerHTML =
        "🧠 Generating LaTeX...";

    const data =
        await response.json();

    document.getElementById("transcript")
        .textContent =
        data.transcript;

    document.getElementById("latex")
        .textContent =
        data.latex;

    currentLatex =
        data.latex;

    status.innerHTML =
        "✅ Complete";
}

function copyLatex() {

    navigator.clipboard.writeText(
        currentLatex
    );

    alert("LaTeX copied.");
}

function downloadTex() {

    const blob =
        new Blob(
            [currentLatex],
            {type: "text/plain"}
        );

    const a =
        document.createElement("a");

    a.href =
        URL.createObjectURL(blob);

    a.download =
        "lecture.tex";

    a.click();
}

function downloadPdf() {

    alert(
        "PDF download endpoint not added yet."
    );
}
