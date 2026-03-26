"""
eval_splitter.py — Validate splitting quality by comparing methods.

Workflow:
1. Pick N papers where Method 1 (LaTeX) succeeds -> that's the ground truth.
2. Run Method 2 (PDF) on the same papers -> measure accuracy vs ground truth.
3. Run Method 3 (LLM) on the same papers -> measure accuracy vs ground truth.
4. Report precision/recall of section detection for each method.

A "section" is correctly detected if:
  - Title matches (fuzzy, normalized) AND
  - Content overlap > 80% (measured by character-level Jaccard)

This lets us:
  - Know how good Method 2 is (it's our primary fallback)
  - Know how good Method 3 is before trusting it as last resort
  - Detect regressions when we change splitting logic
"""

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from .splitter import Section


@dataclass
class SplitEvalResult:
    """Evaluation of one splitting method against ground truth."""

    paper_id: str
    method_tested: str  # "pdf" or "llm"
    ground_truth_sections: int  # How many sections in LaTeX (ground truth)
    detected_sections: int  # How many sections the method found
    matched_sections: int  # How many correctly matched
    precision: float  # matched / detected
    recall: float  # matched / ground_truth
    f1: float
    section_details: list[dict]  # Per-section match details
    # Each: {"gt_title": ..., "detected_title": ..., "title_sim": ..., "content_overlap": ...}


def normalize_title(title: str) -> str:
    """Normalize section title for comparison.

    "2.1 Loss Function" -> "loss function"
    "LOSS FUNCTION" -> "loss function"
    """
    title = re.sub(r"^[\d.]+\s+", "", title)  # Strip numbering
    title = re.sub(r"[^\w\s]", "", title)  # Strip punctuation
    return title.lower().strip()


def title_similarity(a: str, b: str) -> float:
    """Fuzzy title match score (0-1)."""
    a_norm = normalize_title(a)
    b_norm = normalize_title(b)
    return SequenceMatcher(None, a_norm, b_norm).ratio()


def content_overlap(a: str, b: str) -> float:
    """Character-level Jaccard similarity between two content strings."""
    if not a or not b:
        return 0.0
    # Use trigram Jaccard for efficiency on long texts
    def trigrams(text: str) -> set[str]:
        text = text.lower()
        return set(text[i : i + 3] for i in range(len(text) - 2))

    ta, tb = trigrams(a), trigrams(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def evaluate_splitter(
    ground_truth: list[Section],  # From Method 1 (LaTeX)
    candidate: list[Section],  # From Method 2 or 3
    paper_id: str = "",
    method_name: str = "",
    title_threshold: float = 0.7,
    content_threshold: float = 0.5,
) -> SplitEvalResult:
    """Compare candidate splitting against ground truth.

    A candidate section "matches" a ground truth section if:
    1. Title similarity > title_threshold (default 0.7)
    2. Content overlap > content_threshold (default 0.5)
    """
    details: list[dict] = []
    matched = 0
    gt_matched: set[int] = set()

    for cs in candidate:
        best_match = None
        best_score = 0.0

        for i, gt in enumerate(ground_truth):
            if i in gt_matched:
                continue

            tsim = title_similarity(gt.title, cs.title)
            if tsim < title_threshold:
                continue

            coverlap = content_overlap(gt.content, cs.content)
            combined = tsim * 0.4 + coverlap * 0.6

            if combined > best_score:
                best_score = combined
                best_match = (i, gt, tsim, coverlap)

        if best_match and best_match[3] >= content_threshold:
            i, gt, tsim, coverlap = best_match
            gt_matched.add(i)
            matched += 1
            details.append(
                {
                    "gt_title": gt.title,
                    "detected_title": cs.title,
                    "title_sim": round(tsim, 3),
                    "content_overlap": round(coverlap, 3),
                    "status": "matched",
                }
            )
        else:
            details.append(
                {
                    "gt_title": None,
                    "detected_title": cs.title,
                    "title_sim": 0,
                    "content_overlap": 0,
                    "status": (
                        "extra" if best_match is None else "content_mismatch"
                    ),
                }
            )

    # Note unmatched ground truth sections
    for i, gt in enumerate(ground_truth):
        if i not in gt_matched:
            details.append(
                {
                    "gt_title": gt.title,
                    "detected_title": None,
                    "title_sim": 0,
                    "content_overlap": 0,
                    "status": "missed",
                }
            )

    gt_count = len(ground_truth)
    det_count = len(candidate)
    precision = matched / det_count if det_count > 0 else 0.0
    recall = matched / gt_count if gt_count > 0 else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )

    return SplitEvalResult(
        paper_id=paper_id,
        method_tested=method_name,
        ground_truth_sections=gt_count,
        detected_sections=det_count,
        matched_sections=matched,
        precision=round(precision, 3),
        recall=round(recall, 3),
        f1=round(f1, 3),
        section_details=details,
    )
