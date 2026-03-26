"""
llm_splitter.py — LLM-assisted section extraction (Method 3).

STUB: Requires LLM Client module (not yet implemented).
When LLM Client is ready, implement _call_llm() and this works.

Self-validation: eval_splitter.py compares Method 3 output against
Methods 1/2 on papers where those methods succeed, to measure
Method 3's accuracy before trusting it on papers where it's the only option.
"""

from .splitter import BaseSplitter, Section, SplitMethod, SourceUnavailable


class LlmSplitter(BaseSplitter):
    """Use LLM to extract section structure from raw text."""

    EXTRACTION_PROMPT = """You are analyzing the structure of an academic paper.
Given the first 3 pages of text, identify ALL section and subsection headings
with their approximate position (page number if visible, or sequential order).

Return ONLY a JSON array, no other text:
[
  {{"level": 1, "title": "Introduction", "starts_at": "page 1"}},
  {{"level": 2, "title": "Problem Setup", "starts_at": "page 2"}},
  ...
]

Rules:
- level 1 = section (e.g., "1 Introduction", "2 Method")
- level 2 = subsection (e.g., "2.1 Architecture", "3.2 Training")
- Include Abstract as level 1 if present
- Stop at References (do not include References or Appendix)
- Strip numbering from titles: "2.1 Architecture" -> "Architecture"
"""

    def split(self, source: str) -> list[Section]:
        """Extract sections using LLM.

        Phase 1: Use LLM to identify section headings from first 3 pages.
        Phase 2: Use the headings as split points to extract content.

        This is a two-pass approach: LLM identifies structure, then
        deterministic code does the actual splitting.
        """
        raise NotImplementedError(
            "LLM Client module not yet implemented. "
            "LlmSplitter will be activated when elsa_runtime.llm.client is ready."
        )

    # TODO: implement when LLM Client is available
    # async def _identify_sections(self, first_pages_text: str) -> list[dict]:
    #     """Ask LLM to identify section structure."""
    #     from elsa_runtime.llm.client import ask
    #     response = await ask(
    #         prompt=self.EXTRACTION_PROMPT + "\n\nPaper text:\n" + first_pages_text,
    #         model="haiku",  # Cheap model — just structure extraction
    #     )
    #     return json.loads(response)
    #
    # def _split_by_identified_headings(self, full_text: str, headings: list[dict]) -> list[Section]:
    #     """Use identified headings as regex anchors to split the full text."""
    #     ...
