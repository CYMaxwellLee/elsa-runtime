"""Unit tests for paper section chunker."""

from elsa_runtime.paper.chunker import (
    Chunk,
    TARGET_CHARS,
    chunk_section,
    chunk_sections,
    chunk_text,
    filter_garbage_chunks,
    is_garbage_chunk,
)
from elsa_runtime.paper.splitter import Section


def _make_section(content: str, **overrides) -> Section:
    return Section(
        id=overrides.get("id", "section:Method"),
        title=overrides.get("title", "Method"),
        content=content,
        level=overrides.get("level", 1),
        order=overrides.get("order", 0),
        estimated_tokens=len(content) // 4,
        metadata=overrides.get("metadata", {}),
    )


# ── chunk_text (pure text) ─────────────────────────────────────────────


class TestChunkTextShortInput:
    def test_short_text_passes_through(self):
        text = "This is a short paragraph."
        assert chunk_text(text) == [text]

    def test_at_target_chars_passes_through(self):
        text = "x" * TARGET_CHARS
        assert chunk_text(text) == [text]

    def test_empty_returns_empty(self):
        assert chunk_text("") == [""]


class TestChunkTextParagraphPacking:
    def test_two_short_paragraphs_pack(self):
        a = "Para A. " * 50  # ~400 chars
        b = "Para B. " * 50
        text = a + "\n\n" + b
        chunks = chunk_text(text, target_chars=2000)
        assert len(chunks) == 1
        assert "Para A" in chunks[0]
        assert "Para B" in chunks[0]

    def test_oversized_pack_splits(self):
        # Three ~3000-char paragraphs, target 5000 → first two fit, third spills
        a = "A " * 1500
        b = "B " * 1500
        c = "C " * 1500
        text = "\n\n".join([a, b, c])
        chunks = chunk_text(text, target_chars=5000)
        assert len(chunks) >= 2
        # No chunk exceeds target
        assert all(len(c) <= 5000 for c in chunks)
        # Content preserved (allow off-by-one for trailing whitespace strip)
        joined = "\n\n".join(chunks)
        # Each letter appears 1500x in source; after strip+rejoin allow ±2 tolerance
        for letter in ("A", "B", "C"):
            occurrences = joined.count(letter)
            assert 1498 <= occurrences <= 1500, (
                f"Lost letters {letter}: expected ~1500 got {occurrences}"
            )


class TestChunkTextLongParagraph:
    def test_oversized_single_paragraph_split_on_sentences(self):
        # One big paragraph (no \n\n) with sentences
        sentences = [f"This is sentence number {i}." for i in range(200)]
        text = " ".join(sentences)
        # text ≈ 5800 chars
        chunks = chunk_text(text, target_chars=2000)
        assert len(chunks) >= 3
        assert all(len(c) <= 2000 for c in chunks)
        # All sentences preserved (loose check)
        joined = " ".join(chunks)
        assert "sentence number 0" in joined
        assert "sentence number 199" in joined

    def test_pathological_no_sentence_no_paragraph(self):
        """Hard-cut fallback when text is one giant blob."""
        text = "x" * 20000
        chunks = chunk_text(text, target_chars=5000)
        assert len(chunks) == 4
        assert all(len(c) == 5000 for c in chunks)


class TestChunkTextCJKSentences:
    def test_chinese_sentence_split(self):
        text = "這是一句話。" * 500  # 3000 chars Chinese sentences
        chunks = chunk_text(text, target_chars=1000)
        assert len(chunks) >= 3
        assert all(len(c) <= 1000 for c in chunks)


# ── chunk_section ────────────────────────────────────────────────────


class TestChunkSection:
    def test_short_section_one_chunk(self):
        s = _make_section("short content")
        chunks = chunk_section(s)
        assert len(chunks) == 1
        c = chunks[0]
        assert c.chunk_idx == 0
        assert c.chunk_total == 1
        assert c.content == "short content"
        assert c.section_id == s.id
        assert c.section_title == s.title

    def test_long_section_multi_chunk(self):
        # 20K chars, target 5K → 4 chunks
        s = _make_section("x" * 20000)
        chunks = chunk_section(s, target_chars=5000)
        assert len(chunks) == 4
        # chunk_idx incrementing 0..3
        assert [c.chunk_idx for c in chunks] == [0, 1, 2, 3]
        # chunk_total consistent across all
        assert all(c.chunk_total == 4 for c in chunks)
        # Section metadata propagated
        assert all(c.section_id == s.id for c in chunks)
        assert all(c.section_title == s.title for c in chunks)
        assert all(c.section_level == s.level for c in chunks)

    def test_metadata_preserved(self):
        s = _make_section(
            "y" * 100,
            metadata={"has_equations": True, "has_figures": ["fig1"]},
        )
        chunks = chunk_section(s)
        assert chunks[0].metadata == {
            "has_equations": True,
            "has_figures": ["fig1"],
        }
        # Mutating chunk metadata should NOT affect original section
        chunks[0].metadata["mutated"] = True
        assert "mutated" not in s.metadata


class TestChunkSections:
    def test_multi_section_paper(self):
        sections = [
            _make_section("intro text", id="section:Intro", title="Intro", order=0),
            _make_section("y" * 12000, id="section:Method", title="Method", order=1),
            _make_section("conclusion text", id="section:Conclusion", title="Conclusion", order=2),
        ]
        chunks = chunk_sections(sections, target_chars=5000)
        # Intro: 1 chunk; Method (12K chars / 5K target): 3 chunks; Conclusion: 1
        assert len(chunks) == 5
        # Order preserved
        section_titles = [c.section_title for c in chunks]
        assert section_titles == ["Intro", "Method", "Method", "Method", "Conclusion"]


# ── BGE-M3 sanity: target chars conservative for 8K token limit ──────


class TestTargetCharsSafe:
    def test_target_well_below_bge_m3_max(self):
        """Sanity: TARGET_CHARS / 4 should be well below BGE-M3 max 8192 tokens."""
        max_tokens_estimate = TARGET_CHARS // 4
        assert max_tokens_estimate < 8192, (
            f"TARGET_CHARS={TARGET_CHARS} produces ~{max_tokens_estimate} tokens, "
            f"close to BGE-M3 8192 limit"
        )
        # Also: leave headroom for attention (target should be < 25% of max)
        assert max_tokens_estimate < 8192 * 0.25, (
            f"TARGET_CHARS={TARGET_CHARS} too aggressive for attention budget"
        )


# ── Garbage chunk filter ────────────────────────────────────────────────


class TestIsGarbageChunk:
    def test_empty_string_is_garbage(self):
        assert is_garbage_chunk("")

    def test_whitespace_only_is_garbage(self):
        assert is_garbage_chunk("   \n\n  \t ")

    def test_figure_only_is_garbage(self):
        # Real example from 4/30 audit: D3PM last chunk
        assert is_garbage_chunk("[FIGURE]\n\n[FIGURE]\n\n[FIGURE]\n\n[FIGURE]")

    def test_table_only_is_garbage(self):
        assert is_garbage_chunk("[TABLE]\n[TABLE]\n[TABLE]")

    def test_mixed_placeholders_only_is_garbage(self):
        assert is_garbage_chunk(
            "[FIGURE]\n[TABLE]\n[EQUATION]\n[ALGORITHM]\n[FIGURE]"
        )

    def test_real_content_passes(self):
        text = (
            "We propose a new method for diffusion modeling on discrete state "
            "spaces. The approach generalizes prior work in the categorical "
            "domain by introducing a transition matrix that admits multiple "
            "noise schedules."
        )
        assert not is_garbage_chunk(text)

    def test_short_caption_passes_if_substantive(self):
        # Even short body text >= 30 chars after strip should pass
        text = "Following Austin et al we set the parameters as follows."
        assert not is_garbage_chunk(text)

    def test_figure_with_real_caption_passes(self):
        text = (
            "[FIGURE] As shown above, the variational lower bound improves "
            "monotonically with the number of denoising steps."
        )
        assert not is_garbage_chunk(text)


class TestFilterGarbageChunks:
    def _ch(self, content: str, idx: int = 0) -> Chunk:
        return Chunk(
            section_id="section:Method",
            section_title="Method",
            section_level=1,
            section_order=0,
            chunk_idx=idx,
            chunk_total=1,
            content=content,
            estimated_tokens=len(content) // 4,
            metadata={},
        )

    def test_drops_garbage_keeps_real(self):
        chunks = [
            self._ch("Real text with sufficient length to be meaningful content here.", 0),
            self._ch("[FIGURE]\n[FIGURE]", 1),
            self._ch("More substantive method explanation in this chunk overall.", 2),
            self._ch("", 3),
            self._ch("[TABLE]\n[TABLE]\n[TABLE]", 4),
        ]
        kept, dropped = filter_garbage_chunks(chunks)
        assert dropped == 3
        assert len(kept) == 2
        assert all("Real" in k.content or "substantive" in k.content for k in kept)

    def test_empty_input(self):
        kept, dropped = filter_garbage_chunks([])
        assert kept == []
        assert dropped == 0

    def test_all_garbage_returns_empty(self):
        chunks = [self._ch("[FIGURE]", 0), self._ch("", 1)]
        kept, dropped = filter_garbage_chunks(chunks)
        assert kept == []
        assert dropped == 2
