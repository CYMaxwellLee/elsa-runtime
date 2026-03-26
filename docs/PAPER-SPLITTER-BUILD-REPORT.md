# PaperSplitter Build Report — v3.42

> **Date**: 2026-03-26
> **Task**: T1.11.1 (PaperSplitter) + T1.11.2 (SectionIndex)
> **Commit**: `267397b` on `main`
> **Prior state**: 153 tests passing (Schema Registry v3.41.1 done)
> **Current state**: 224 tests passing, 2 skipped, 0 failures

---

## What Was Built

### New Package: `src/elsa_runtime/paper/`

| File | Lines | Purpose |
|------|------:|---------|
| `splitter.py` | 242 | Data structures (Section, SectionIndex, SplitResult) + PaperSplitter orchestrator |
| `latex_splitter.py` | 219 | Method 1: Download arXiv e-print tarball, find main .tex, parse \section commands |
| `latex_cleaner.py` | 98 | Strip LaTeX formatting while preserving readable text for LLM consumption |
| `pdf_splitter.py` | 197 | Method 2: PyMuPDF font size + bold analysis to detect section headings |
| `llm_splitter.py` | 63 | Method 3: Stub (raises NotImplementedError, needs LLM Client module) |
| `eval_splitter.py` | 172 | Self-validation framework: compare Method 2/3 accuracy vs Method 1 ground truth |
| `__init__.py` | 37 | Public API re-exports |
| **Total** | **1028** | |

### New Tests

| File | Tests | Coverage |
|------|------:|----------|
| `test_latex_cleaner.py` | 13 | Comments, cite/ref, math, itemize, tables, figures, malformed input |
| `test_latex_splitter.py` | 14 | Section parsing, subsections, unnumbered \section*, no-section fallback, abstract extraction, metadata detection |
| `test_pdf_splitter.py` | 13 | Font size detection, heading detection (size + bold + pattern), stop-at-references, segmentation logic |
| `test_paper_splitter.py` | 17 | ArXiv ID regex, PDF path resolution, fallback chain, sanity warnings, SectionIndex generation, prompt string format |
| `test_arxiv_splitter.py` (integration) | 6 | Real arXiv paper download + split (requires network, marked `@pytest.mark.network`) |
| **Total new** | **63** | |

### Dependencies Added

- `pymupdf>=1.24.0` — PDF parsing (PyMuPDF)
- `requests>=2.31` — ArXiv source download

---

## Design Decisions

1. **Fallback chain**: LaTeX (best) -> PDF (good) -> LLM (stub). PaperSplitter orchestrator auto-selects.
2. **LaTeX cleaner**: Preserves inline math (`$x \in R^d$`) and equation content — LLMs can read these. Strips formatting, cross-refs, figures.
3. **PDF splitter**: Uses font size ratio to body text (>1.25 = section, 1.15-1.25 = subsection) + bold + heading pattern regex. Stops at "References".
4. **SectionIndex**: ~500 token lightweight summary for QDIP Phase 0 triage — contains section IDs + first ~100 words of each section.
5. **eval_splitter**: Precision/recall/F1 framework for comparing splitting methods. Uses fuzzy title matching + trigram content overlap.

---

## What's NOT Done Yet

| Item | Blocker | Next Step |
|------|---------|-----------|
| Method 3 (LLM splitter) | Needs `elsa_runtime.llm.client` | Implement when LLM Client module lands |
| Integration test: LaTeX vs PDF F1 comparison | Needs a PDF fixture (can't render .tex -> .pdf in CI) | Create or download a sample PDF |
| Network integration test not auto-run | Marked `@pytest.mark.network` | Add `-m network` to CI when ready |

---

## Test Summary

```
$ pytest tests/ -q --ignore=tests/integration/test_arxiv_splitter.py
224 passed, 2 skipped in 26.16s
```

The 2 skipped are LLM-dependent tests from the existing integration suite (Step 4/5), not related to this PR.

---

## For Claude Project Memory Update

One-line status for memory #8:

```
v3.42 | 224 tests (224 pass, 2 skip) | Schema Registry: DONE | PaperSplitter: DONE (Method 1+2 live, Method 3 stub) | P0 blockers: 0 | Next: LLM Client (T1) to unblock Method 3 + QDIP Phase 1
```
