"""
Phase 1 -- Progress-vs-expenditure mismatch.
Scored differently for "In Progress" vs "Completed" works.
See Implementation-Guide.md Phase 1, Architecture.md Section 5.

Interface: score_progress(work: dict) -> float in [0, 1].
No all_works needed -- this check is entirely intra-row (unlike the peer/IQR
based modules), so it deliberately does not take the dataset as an argument.
"""
import numpy as np
import pandas as pd

# Matches the >0.30 organic threshold used to seed progress_expenditure_mismatch
# in the dataset (DATASET-CHANGELOG.md) -- treated as the point the score
# starts climbing, not a hard cliff.
GAP_FLOOR = 0.10
GAP_CEILING = 0.60

UNDERSPEND_FLOOR = 0.10
UNDERSPEND_CEILING = 0.50


def _safe_fraction(numerator, denominator):
    if denominator is None or pd.isna(denominator) or denominator == 0:
        return None
    if numerator is None or pd.isna(numerator):
        return None
    return numerator / denominator


def score_progress(work: dict) -> float:
    """
    Compares physical_progress_pct against how much of the sanctioned amount
    has actually been spent:
      - "In Progress": the two fractions should track each other roughly;
        a large gap either way (money spent far ahead of visible progress,
        or vice versa) is the flag.
      - "Completed": progress_pct is ~100% by definition, so it can't carry
        signal on its own -- the flag instead is a completed work that
        never actually consumed most of its sanctioned funds.
    """
    status = work.get("status")
    progress_pct = work.get("physical_progress_pct")
    if progress_pct is None or pd.isna(progress_pct):
        return 0.0

    expenditure_fraction = _safe_fraction(work.get("expenditure"), work.get("sanctioned_amount"))
    if expenditure_fraction is None:
        return 0.0
    expenditure_fraction = min(expenditure_fraction, 1.5)  # cap runaway ratios for this check

    if status == "In Progress":
        progress_fraction = progress_pct / 100.0
        gap = abs(expenditure_fraction - progress_fraction)
        score = (gap - GAP_FLOOR) / (GAP_CEILING - GAP_FLOOR)
        return float(np.clip(score, 0.0, 1.0))

    if status == "Completed":
        underspend = 1.0 - expenditure_fraction
        score = (underspend - UNDERSPEND_FLOOR) / (UNDERSPEND_CEILING - UNDERSPEND_FLOOR)
        return float(np.clip(score, 0.0, 1.0))

    # Recommended / Sanctioned / Rejected -- nothing to compare against yet
    return 0.0
