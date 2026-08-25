"""
transcript_merger.py
=====================
Because audio chunks overlap slightly (see audio_chunker.py), the text
transcribed from the end of chunk N and the start of chunk N+1 both
contain the same spoken words. This module merges a chronological list of
chunk transcripts into one continuous transcript, trimming the duplicated
words at each boundary instead of naively concatenating them.

Example
-------
Chunk 1 ends:   "...therefore the velocity of the particle"
Chunk 2 starts: "the velocity of the particle is given by..."
Merged:         "...therefore the velocity of the particle is given by..."
"""

import difflib
import re
from typing import Optional

# Minimum number of words that must match, word-for-word, at a chunk
# boundary before we trust it enough to trim. This is intentionally a word
# count (not a character count): matching on words is far less prone to
# coincidental false positives than matching on raw substrings, especially
# when nearby chunks happen to share boilerplate phrasing that isn't
# actually the audio overlap.
MIN_OVERLAP_WORDS = 3

# How many words at the tail of `a` / head of `b` we search for an overlap.
# This should comfortably cover the overlap_seconds configured in
# audio_chunker.py at normal lecture speaking speed.
TAIL_WORDS = 60

_PUNCT_RE = re.compile(r"[.,!?;:\"']+$")


def _norm(word: str) -> str:
    """Lowercase and strip trailing punctuation for comparison purposes,
    without altering the original word we'll actually emit."""
    return _PUNCT_RE.sub("", word.lower())


def merge_transcripts(chunk_texts: list[str], chunk_ok: Optional[list[bool]] = None) -> str:
    """
    Merge a chronological list of chunk transcripts into one transcript,
    trimming duplicated overlap text at each chunk boundary.

    `chunk_ok`, if given, marks which chunks actually transcribed
    successfully (vs. a failure placeholder — see lecture2tex.py). This
    matters: audio_chunker.py's overlap assumption only holds between two
    ADJACENT, successfully-transcribed chunks. If a chunk in between failed,
    there's a real gap of un-transcribed audio, and attempting an
    overlap-trim merge across that gap can spuriously "match" and delete
    real content from a neighboring chunk purely by coincidence. When either
    side of a boundary is a failed chunk, we skip trimming and just
    concatenate.
    """
    if chunk_ok is None:
        chunk_ok = [True] * len(chunk_texts)

    items = [
        (t.strip(), ok) for t, ok in zip(chunk_texts, chunk_ok) if t and t.strip()
    ]
    if not items:
        return ""

    merged, prev_ok = items[0]
    for text, ok in items[1:]:
        if prev_ok and ok:
            merged = _merge_pair(merged, text)
        else:
            # A chunk on one side of this boundary failed — there's a real
            # gap in the audio here, not a genuine overlap. Don't attempt
            # to trim; just join the pieces as-is.
            merged = (merged + "\n\n" + text).strip()
        prev_ok = ok

    return merged


def _merge_pair(a: str, b: str) -> str:
    a_words = a.split()
    b_words = b.split()

    tail_words = a_words[-TAIL_WORDS:] if len(a_words) > TAIL_WORDS else a_words
    head_words = b_words[:TAIL_WORDS] if len(b_words) > TAIL_WORDS else b_words

    if not tail_words or not head_words:
        return (a + " " + b).strip()

    tail_norm = [_norm(w) for w in tail_words]
    head_norm = [_norm(w) for w in head_words]

    # ── Primary strategy: exact word-for-word suffix/prefix match ──────────
    # This is the common, clean case (see the module docstring's example):
    # search from the longest plausible overlap down to a minimum, looking
    # for the tail of `a` to equal the head of `b` word-for-word. Anchoring
    # the match to the literal boundary (rather than searching anywhere in
    # a window, as a generic diff would) avoids spuriously matching
    # unrelated repeated phrasing elsewhere in the chunk.
    max_k = min(len(tail_norm), len(head_norm))
    best_k = 0
    for k in range(max_k, MIN_OVERLAP_WORDS - 1, -1):
        if tail_norm[-k:] == head_norm[:k]:
            best_k = k
            break

    if best_k:
        remaining_b_words = b_words[best_k:]
        new_b = " ".join(remaining_b_words).strip()
        return (a + " " + new_b).strip() if new_b else a

    # ── Fallback: fuzzy match, but still anchored at the boundary ──────────
    # Whisper may transcribe the same overlapping audio slightly differently
    # across the two chunk boundaries (minor wording/punctuation drift), so
    # an exact match can legitimately miss a real overlap. Allow a fuzzy
    # match here, but only accept it if it starts within a couple of words
    # of the true boundary on both sides — never a match floating in the
    # middle of the 60-word search window.
    tail_text = " ".join(tail_words)
    head_text = " ".join(head_words)
    matcher = difflib.SequenceMatcher(None, tail_text.lower(), head_text.lower(), autojunk=False)
    match = matcher.find_longest_match(0, len(tail_text), 0, len(head_text))

    anchored_at_tail_end = match.a + match.size >= len(tail_text) - 3
    anchored_at_head_start = match.b <= 3
    long_enough = match.size >= 15  # ~ MIN_OVERLAP_WORDS worth of characters

    if long_enough and anchored_at_tail_end and anchored_at_head_start:
        cut_at = match.b + match.size
        remainder_of_head = head_text[cut_at:].lstrip(" .,!?;:")
        b_after_head_words = " ".join(b_words[len(head_words):])
        new_b = (remainder_of_head + " " + b_after_head_words).strip()
        return (a + " " + new_b).strip() if new_b else a

    # No meaningful, boundary-anchored overlap detected — just concatenate.
    return (a + " " + b).strip()
