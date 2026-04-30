"""
chunker.py — Split long sections into BGE-M3-friendly chunks.

BGE-M3 max input is 8192 tokens, but attention is O(N²) — sending sections
near 8K tokens causes MPS / GPU memory blowup (verified 2026-04-29 ingest:
multiple "Invalid buffer size: 10-31 GiB" failures on M1, max MPS allowed
20 GiB).

Strategy:
  1. If section is short (≤ TARGET_CHARS), keep as-is — single chunk.
  2. Otherwise split on paragraph boundaries (`\\n\\n`), pack paragraphs into
     chunks up to TARGET_CHARS.
  3. If a single paragraph itself exceeds TARGET_CHARS, recursively split on
     sentence boundaries (`. `, `。`, `？`, `！`).
  4. If a single sentence still exceeds, hard-cut at TARGET_CHARS.

Each output chunk preserves the parent Section's metadata + a chunk_idx /
chunk_total marker so the consumer (paper_harvest.py) can build deterministic
LanceDB IDs (e.g. `arxiv_id::section:Method::chunk:0`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .splitter import Section


# Token-to-char ratio for BGE-M3 (XLM-RoBERTa) on English text: ~3.5-4 chars
# per token. We target 1500 tokens = ~6000 chars, leaving headroom under the
# 8192 hard ceiling so attention buffers stay reasonable.
TARGET_CHARS = 6000

# A single paragraph rarely needs splitting; if one is bigger than this we
# fall back to sentence splits. Set higher than TARGET_CHARS so we don't
# split paragraphs that pack-and-go cleanly.
PARAGRAPH_SPLIT_THRESHOLD = TARGET_CHARS + 1000

_PARAGRAPH_RE = re.compile(r"\n\n+")
# Sentence-ish split: . ! ? in either ASCII or CJK form, followed by space/newline.
_SENTENCE_RE = re.compile(r"(?<=[.!?。！？])\s+")


@dataclass
class Chunk:
    """One BGE-M3-bounded chunk of a section."""
    section_id: str           # parent section id (e.g. "section:Method")
    section_title: str        # parent section title
    section_level: int        # parent section level (1=section, 2=subsection)
    section_order: int        # parent section order in paper
    chunk_idx: int            # 0-based chunk index within this section
    chunk_total: int          # total chunks for this section
    content: str              # the chunk text
    estimated_tokens: int     # rough token estimate (chars // 4)
    metadata: dict            # parent section metadata (passed through)


def chunk_text(text: str, target_chars: int = TARGET_CHARS) -> list[str]:
    """Pure text chunking — split text into pieces ≤ target_chars.

    Tries paragraph-aware packing first; falls back to sentence and
    hard-cut for pathological cases.
    """
    if len(text) <= target_chars:
        return [text]

    # Step 1: split into paragraphs
    paragraphs = _PARAGRAPH_RE.split(text)

    # Step 2: pack paragraphs into chunks
    chunks: list[str] = []
    current = ""
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue

        # If single paragraph too big, recursively split it on sentences
        if len(p) > PARAGRAPH_SPLIT_THRESHOLD:
            # Flush current pack first
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_split_long_paragraph(p, target_chars))
            continue

        # Try to add this paragraph to current pack
        candidate = (current + "\n\n" + p) if current else p
        if len(candidate) <= target_chars:
            current = candidate
        else:
            # Adding would overflow; emit current, start new
            if current:
                chunks.append(current)
            current = p

    if current:
        chunks.append(current)

    # Defensive: any chunk still too big (shouldn't happen given recursion above)
    final: list[str] = []
    for c in chunks:
        if len(c) <= target_chars:
            final.append(c)
        else:
            final.extend(_hard_cut(c, target_chars))
    return final


def _split_long_paragraph(text: str, target_chars: int) -> list[str]:
    """Split a single oversized paragraph at sentence boundaries."""
    sentences = _SENTENCE_RE.split(text)
    chunks: list[str] = []
    current = ""
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        candidate = (current + " " + s) if current else s
        if len(candidate) <= target_chars:
            current = candidate
            continue
        # Adding would overflow
        if current:
            chunks.append(current)
        # Single sentence might still be > target_chars; if so, hard cut
        if len(s) > target_chars:
            chunks.extend(_hard_cut(s, target_chars))
            current = ""
        else:
            current = s

    if current:
        chunks.append(current)
    return chunks


def _hard_cut(text: str, target_chars: int) -> list[str]:
    """Last-resort: split at exact char boundaries. Used for pathological cases."""
    return [text[i:i + target_chars] for i in range(0, len(text), target_chars)]


def chunk_section(section: Section, target_chars: int = TARGET_CHARS) -> list[Chunk]:
    """Split a Section into 1+ Chunks.

    A short section returns one Chunk identical to the input. A long section
    returns multiple chunks with chunk_idx 0..N-1 and chunk_total N.
    """
    pieces = chunk_text(section.content, target_chars=target_chars)
    total = len(pieces)
    return [
        Chunk(
            section_id=section.id,
            section_title=section.title,
            section_level=section.level,
            section_order=section.order,
            chunk_idx=i,
            chunk_total=total,
            content=piece,
            estimated_tokens=len(piece) // 4,
            metadata=dict(section.metadata),
        )
        for i, piece in enumerate(pieces)
    ]


def chunk_sections(sections: list[Section], target_chars: int = TARGET_CHARS) -> list[Chunk]:
    """Chunk every section in a paper. Preserves order across all chunks."""
    out: list[Chunk] = []
    for s in sections:
        out.extend(chunk_section(s, target_chars=target_chars))
    return out


# Patterns indicating a chunk that has no real content — most commonly
# an end-of-paper figure dump where the splitter / latex_cleaner stripped
# captions but left structural [FIGURE] placeholders. Embedding these
# pollutes retrieval (they contribute nothing semantic).
_GARBAGE_TOKEN_RE = re.compile(r"\[(?:FIGURE|TABLE|EQUATION|ALGORITHM)\]")
_MIN_SIGNAL_CHARS = 30  # below this we treat the chunk as noise


def is_garbage_chunk(text: str) -> bool:
    """True if a chunk has no embedding-worthy content.

    Empty / whitespace-only chunks are obvious. The harder case is chunks
    consisting only of [FIGURE] / [TABLE] / [EQUATION] / [ALGORITHM]
    placeholders left behind by the LaTeX cleaner. Strip those tokens
    (and surrounding whitespace) and check whether anything substantive
    remains.

    Threshold: >= 30 non-placeholder characters survive. Below that the
    chunk is almost certainly figure-caption residue or stray markup.
    """
    if not text:
        return True
    stripped = _GARBAGE_TOKEN_RE.sub("", text).strip()
    return len(stripped) < _MIN_SIGNAL_CHARS


def filter_garbage_chunks(chunks: list[Chunk]) -> tuple[list[Chunk], int]:
    """Drop chunks whose content is empty / placeholder-only.

    Returns (kept, n_dropped). Logging is the caller's job.
    """
    kept: list[Chunk] = []
    dropped = 0
    for ch in chunks:
        if is_garbage_chunk(ch.content):
            dropped += 1
            continue
        kept.append(ch)
    return kept, dropped
