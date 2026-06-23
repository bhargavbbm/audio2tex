"""
physics_parser.py
=================
Converts a plain-English physics transcript into valid LaTeX body content.

Two-stage pipeline
------------------
Stage 1 — Rule-based regex pass:
    Fast substitutions for Greek letters, common operators, and spoken math.
    Handles cases like "alpha", "squared", "integral of", etc.

Stage 2 — Claude API pass (optional but recommended):
    Sends the regex-processed text to Claude claude-sonnet-4-6 with a system prompt
    that instructs it to output ONLY valid LaTeX body content (no preamble).
    This handles complex spoken math that regex can't reliably catch.
    Falls back to regex-only output if the API call fails.

Environment variable:
    ANTHROPIC_API_KEY — set this in your HF Space secrets for Stage 2 to work.
"""

import os
import re
import json
from urllib import request as urllib_request
from urllib.error import URLError

# ── Stage 1: Regex replacement table ─────────────────────────────────────────
# Order is critical: longer/more-specific patterns first.
# All patterns are case-insensitive.

_REPLACEMENTS: list[tuple[str, str]] = [
    # ── Full spoken equations ─────────────────────────────────────────────────
    (r"\be\s+equals\s+m\s+c\s+squared\b",   r"$E = mc^2$"),
    (r"\bf\s+equals\s+m\s+a\b",             r"$F = ma$"),
    (r"\bp\s+equals\s+m\s+v\b",             r"$p = mv$"),
    (r"\be\s+equals\s+h\s+nu\b",            r"$E = h\\nu$"),
    (r"\be\s+equals\s+h\s+f\b",             r"$E = hf$"),
    (r"\bv\s+equals\s+i\s+r\b",             r"$V = IR$"),

    # ── Powers ────────────────────────────────────────────────────────────────
    (r"\bto\s+the\s+power\s+of\s+(\w+)\b",  r"$^{\1}$"),
    (r"\bto\s+the\s+(\w+)\s+power\b",       r"$^{\1}$"),
    (r"\bsquared\b",                         r"$^2$"),
    (r"\bcubed\b",                           r"$^3$"),
    (r"\bsquare\s+root\s+of\b",             r"$\\sqrt{\\cdot}$"),
    (r"\bsquare\s+root\b",                  r"$\\sqrt{\\cdot}$"),

    # ── Calculus ──────────────────────────────────────────────────────────────
    (r"\bpartial\s+derivative\s+of\b",      r"$\\frac{\\partial}{\\partial}$"),
    (r"\bpartial\s+derivative\b",           r"$\\partial$"),
    (r"\bderivative\s+of\b",               r"$\\frac{d}{d}$"),
    (r"\bintegral\s+from\b",               r"$\\int$"),
    (r"\bintegral\s+of\b",                 r"$\\int$"),
    (r"\bdouble\s+integral\b",             r"$\\iint$"),
    (r"\btriple\s+integral\b",             r"$\\iiint$"),
    (r"\bcontour\s+integral\b",            r"$\\oint$"),
    (r"\bsurface\s+integral\b",            r"$\\iint$"),
    (r"\bgradient\s+of\b",                 r"$\\nabla$"),
    (r"\bdivergence\s+of\b",              r"$\\nabla \\cdot$"),
    (r"\bcurl\s+of\b",                    r"$\\nabla \\times$"),
    (r"\blaplacian\s+of\b",               r"$\\nabla^2$"),
    (r"\blaplacian\b",                     r"$\\nabla^2$"),
    (r"\bgrad\b",                          r"$\\nabla$"),

    # ── Sums & products ───────────────────────────────────────────────────────
    (r"\bsum\s+from\b",                    r"$\\sum$"),
    (r"\bsummation\b",                     r"$\\sum$"),
    (r"\bproduct\s+from\b",               r"$\\prod$"),
    (r"\binfinity\b",                      r"$\\infty$"),
    (r"\binfinite\b",                      r"$\\infty$"),

    # ── Relations & symbols ───────────────────────────────────────────────────
    (r"\bplus\s+or\s+minus\b",             r"$\\pm$"),
    (r"\bminus\s+or\s+plus\b",             r"$\\mp$"),
    (r"\bapproximately\s+equal\s+to\b",   r"$\\approx$"),
    (r"\bapproximately\b",                 r"$\\approx$"),
    (r"\bnot\s+equal\s+to\b",             r"$\\neq$"),
    (r"\bgreater\s+than\s+or\s+equal\s+to\b", r"$\\geq$"),
    (r"\bless\s+than\s+or\s+equal\s+to\b",   r"$\\leq$"),
    (r"\bproportional\s+to\b",            r"$\\propto$"),
    (r"\bdot\s+product\b",               r"$\\cdot$"),
    (r"\bcross\s+product\b",             r"$\\times$"),
    (r"\btensor\s+product\b",            r"$\\otimes$"),
    (r"\bdirect\s+sum\b",               r"$\\oplus$"),
    (r"\bimplies\b",                     r"$\\Rightarrow$"),
    (r"\bif\s+and\s+only\s+if\b",       r"$\\Leftrightarrow$"),
    (r"\bfor\s+all\b",                   r"$\\forall$"),
    (r"\bthere\s+exists\b",             r"$\\exists$"),
    (r"\belement\s+of\b",               r"$\\in$"),
    (r"\bsubset\s+of\b",                r"$\\subset$"),
    (r"\bunion\b",                       r"$\\cup$"),
    (r"\bintersection\b",               r"$\\cap$"),

    # ── Greek letters (word-boundary to avoid replacing inside other words) ───
    (r"\bAlpha\b",       r"$A$"),          # uppercase Greek = Roman in LaTeX
    (r"\bBeta\b",        r"$B$"),
    (r"\bGamma\b",       r"$\\Gamma$"),
    (r"\bDelta\b",       r"$\\Delta$"),
    (r"\bEpsilon\b",     r"$E$"),
    (r"\bZeta\b",        r"$Z$"),
    (r"\bEta\b",         r"$H$"),
    (r"\bTheta\b",       r"$\\Theta$"),
    (r"\bIota\b",        r"$I$"),
    (r"\bKappa\b",       r"$K$"),
    (r"\bLambda\b",      r"$\\Lambda$"),
    (r"\bMu\b",          r"$M$"),
    (r"\bNu\b",          r"$N$"),
    (r"\bXi\b",          r"$\\Xi$"),
    (r"\bOmicron\b",     r"$O$"),
    (r"\bPi\b",          r"$\\Pi$"),
    (r"\bRho\b",         r"$P$"),
    (r"\bSigma\b",       r"$\\Sigma$"),
    (r"\bTau\b",         r"$T$"),
    (r"\bUpsilon\b",     r"$\\Upsilon$"),
    (r"\bPhi\b",         r"$\\Phi$"),
    (r"\bChi\b",         r"$X$"),
    (r"\bPsi\b",         r"$\\Psi$"),
    (r"\bOmega\b",       r"$\\Omega$"),

    (r"\balpha\b",       r"$\\alpha$"),
    (r"\bbeta\b",        r"$\\beta$"),
    (r"\bgamma\b",       r"$\\gamma$"),
    (r"\bdelta\b",       r"$\\delta$"),
    (r"\bepsilon\b",     r"$\\epsilon$"),
    (r"\bvarepsilon\b",  r"$\\varepsilon$"),
    (r"\bzeta\b",        r"$\\zeta$"),
    (r"\beta\b",         r"$\\eta$"),
    (r"\btheta\b",       r"$\\theta$"),
    (r"\bvartheta\b",    r"$\\vartheta$"),
    (r"\biota\b",        r"$\\iota$"),
    (r"\bkappa\b",       r"$\\kappa$"),
    (r"\blambda\b",      r"$\\lambda$"),
    (r"\bmu\b",          r"$\\mu$"),
    (r"\bnu\b",          r"$\\nu$"),
    (r"\bxi\b",          r"$\\xi$"),
    (r"\bpi\b",          r"$\\pi$"),
    (r"\bvarpi\b",       r"$\\varpi$"),
    (r"\brho\b",         r"$\\rho$"),
    (r"\bvarrho\b",      r"$\\varrho$"),
    (r"\bsigma\b",       r"$\\sigma$"),
    (r"\bvarsigma\b",    r"$\\varsigma$"),
    (r"\btau\b",         r"$\\tau$"),
    (r"\bupsilon\b",     r"$\\upsilon$"),
    (r"\bphi\b",         r"$\\phi$"),
    (r"\bvarphi\b",      r"$\\varphi$"),
    (r"\bchi\b",         r"$\\chi$"),
    (r"\bpsi\b",         r"$\\psi$"),
    (r"\bomega\b",       r"$\\omega$"),

    # ── Physics-specific terms ────────────────────────────────────────────────
    (r"\bhamiltonian\b",        r"Hamiltonian $\\hat{H}$"),
    (r"\blagrangian\b",         r"Lagrangian $\\mathcal{L}$"),
    (r"\bschrodinger\b",        r"Schr\\\"odinger"),
    (r"\bhbar\b",               r"$\\hbar$"),
    (r"\bh-bar\b",              r"$\\hbar$"),
    (r"\bplanck'?s\s+constant\b", r"Planck's constant $h$"),
    (r"\breduced\s+planck\b",   r"reduced Planck constant $\\hbar$"),
    (r"\bboltzmann\s+constant\b", r"Boltzmann constant $k_B$"),
    (r"\bavogadro\b",           r"Avogadro"),
    (r"\bhbar\b",               r"$\\hbar$"),
    (r"\bnabla\b",              r"$\\nabla$"),
    (r"\bdel\b",                r"$\\nabla$"),
    (r"\bket\s+(\w+)\b",        r"$|\\mathinner{\\langle \\1 |}$"),
    (r"\bbra\s+(\w+)\b",        r"$\\langle \\1 |$"),

    # ── Functions ─────────────────────────────────────────────────────────────
    (r"\bsine\b",       r"$\\sin$"),
    (r"\bcosine\b",     r"$\\cos$"),
    (r"\btangent\b",    r"$\\tan$"),
    (r"\barcsin\b",     r"$\\arcsin$"),
    (r"\barccos\b",     r"$\\arccos$"),
    (r"\barctan\b",     r"$\\arctan$"),
    (r"\bnatural\s+log\b",  r"$\\ln$"),
    (r"\bnatural\s+logarithm\b", r"$\\ln$"),
    (r"\bexponential\b",    r"$\\exp$"),

    # ── Structure keywords → LaTeX section commands ───────────────────────────
    (r"^\s*section[:\s]+(.+)$",      r"\\section{\1}"),
    (r"^\s*subsection[:\s]+(.+)$",   r"\\subsection{\1}"),
    (r"^\s*definition[:\s]+(.+)$",   r"\\begin{definition}\1\\end{definition}"),
    (r"^\s*theorem[:\s]+(.+)$",      r"\\begin{theorem}\1\\end{theorem}"),
    (r"^\s*proof[:\s]+(.+)$",        r"\\begin{proof}\1\\end{proof}"),
    (r"^\s*example[:\s]+(.+)$",      r"\\begin{example}\1\\end{example}"),
    (r"^\s*remark[:\s]+(.+)$",       r"\\begin{remark}\1\\end{remark}"),
    (r"^\s*lemma[:\s]+(.+)$",        r"\\begin{lemma}\1\\end{lemma}"),
    (r"^\s*corollary[:\s]+(.+)$",    r"\\begin{corollary}\1\\end{corollary}"),
]

_COMPILED = [
    (re.compile(pat, re.IGNORECASE | re.MULTILINE), repl)
    for pat, repl in _REPLACEMENTS
]

_LATEX_ESCAPE = [
    # Escape bare special chars ONLY when not already preceded by backslash
    # and not inside a $…$ block that we've already generated.
    # We handle just the most common ones that break pdflatex.
    (re.compile(r'(?<!\\)%'),  r'\\%'),
    (re.compile(r'(?<!\\)&'),  r'\\&'),
    (re.compile(r'(?<!\\)#'),  r'\\#'),
    (re.compile(r'(?<!\\)_'),  r'\\_'),
    (re.compile(r'(?<!\\)\^'), r'\\^{}'),
    (re.compile(r'(?<!\\)~'),  r'\\textasciitilde{}'),
]


# ── Stage 1: regex-based conversion ──────────────────────────────────────────
def _regex_pass(text: str) -> str:
    """Apply all regex substitutions to produce rough LaTeX body text."""
    for pattern, replacement in _COMPILED:
        text = pattern.sub(replacement, text)

    # Escape dangerous LaTeX characters that aren't inside our generated $…$
    # Strategy: split on $…$, escape only the non-math parts.
    parts = re.split(r'(\$[^$]*\$)', text)
    escaped_parts = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            # This is a $…$ math block — leave untouched
            escaped_parts.append(part)
        else:
            # Plain text — escape special chars
            for esc_re, esc_repl in _LATEX_ESCAPE:
                part = esc_re.sub(esc_repl, part)
            escaped_parts.append(part)

    return "".join(escaped_parts)


def _split_paragraphs(text: str) -> list[str]:
    """
    Whisper returns a flat string.  Heuristically break into paragraphs:
    - Keep existing blank lines if any.
    - Otherwise group every 5 sentences into a paragraph.
    """
    if "\n\n" in text:
        return [p.strip() for p in text.split("\n\n") if p.strip()]

    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    paragraphs, chunk = [], []
    for i, sent in enumerate(sentences, 1):
        chunk.append(sent)
        if i % 5 == 0:
            paragraphs.append(" ".join(chunk))
            chunk = []
    if chunk:
        paragraphs.append(" ".join(chunk))
    return paragraphs


# ── Stage 2: Claude API pass ──────────────────────────────────────────────────
_CLAUDE_SYSTEM = """You are an expert LaTeX typesetter specializing in physics and mathematics lecture notes.

You will receive text that has already been partially processed by a regex pass:
- Greek letter names like alpha, beta, etc. may have been replaced with LaTeX math like $\\alpha$
- Some spoken math may already be converted

Your job is to:
1. Fix any remaining spoken mathematics into proper LaTeX. For example:
   - "the energy is equal to m c squared" → "the energy is equal to $E = mc^2$"
   - "integrate from zero to infinity" → "integrate from $0$ to $\\infty$"
   - "x sub n plus 1" → "$x_{n+1}$"
   - "the partial of f with respect to x" → "$\\frac{\\partial f}{\\partial x}$"
2. Add appropriate \\section{} and \\subsection{} commands where the speaker signals topic changes (phrases like "now let's talk about", "moving on to", "the next topic is").
3. Format displayed (block) equations with \\begin{equation}...\\end{equation} or \\[ ... \\] when the spoken math is a standalone equation being stated.
4. Use \\begin{enumerate} / \\begin{itemize} for lists the speaker enumerates.
5. Keep the LaTeX body VALID — it must compile in pdflatex with standard amsmath/amssymb packages.
6. Do NOT include \\documentclass, \\begin{document}, \\end{document}, or any preamble.
7. Do NOT wrap your response in ```latex``` code fences.
8. Output ONLY the LaTeX body content, nothing else.
9. Preserve ALL the spoken content — do not summarize or omit anything.
10. Fix obvious transcription errors where context makes the correct word clear (e.g. "hammy Tonian" → "Hamiltonian")."""


def _claude_pass(text: str) -> str:
    """
    Send regex-processed text to Claude claude-sonnet-4-6 for intelligent LaTeX conversion.
    Returns improved LaTeX body, or the original text if the API call fails.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("[physics_parser] ANTHROPIC_API_KEY not set — skipping Claude pass.")
        return text

    payload = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 4096,
        "system": _CLAUDE_SYSTEM,
        "messages": [
            {
                "role": "user",
                "content": (
                    "Convert the following partially-processed physics lecture transcript "
                    "into clean LaTeX body content:\n\n"
                    + text
                )
            }
        ]
    }).encode("utf-8")

    req = urllib_request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type":      "application/json",
            "x-api-key":         api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )

    try:
        with urllib_request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content_blocks = data.get("content", [])
            result = "".join(
                block["text"] for block in content_blocks if block.get("type") == "text"
            ).strip()

            if result:
                # Strip any accidental code fences the model may have added
                result = re.sub(r'^```(?:latex)?\s*', '', result)
                result = re.sub(r'\s*```$', '', result)
                print("[physics_parser] Claude pass complete.")
                return result
            else:
                print("[physics_parser] Claude returned empty response — using regex output.")
                return text

    except (URLError, Exception) as e:
        print(f"[physics_parser] Claude API call failed ({e}) — using regex output.")
        return text


# ── Public entry point ────────────────────────────────────────────────────────
def physics_to_latex(transcript: str) -> str:
    """
    Full two-stage conversion:
        1. Regex substitutions (fast, deterministic)
        2. Claude API cleanup (accurate, needs ANTHROPIC_API_KEY in env)

    Returns a valid LaTeX body (no preamble, no \\begin{document}).
    """
    paragraphs = _split_paragraphs(transcript)
    processed_paragraphs = [_regex_pass(p) for p in paragraphs]
    regex_body = "\n\n".join(processed_paragraphs)

    # Stage 2: Claude intelligent pass
    final_body = _claude_pass(regex_body)

    return final_body
