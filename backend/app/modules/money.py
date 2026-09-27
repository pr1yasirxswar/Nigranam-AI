"""
Phase 1 -- Money irregularity detection.
IQR peer-benchmarking + CPWD DSR-style standard-cost check, combined via max().
See Implementation-Guide.md Phase 1, Architecture.md Section 5.

Interface: score_money(work: dict, all_works: pd.DataFrame) -> float in [0, 1].
"""
import numpy as np
import pandas as pd

_peer_stats_cache = {}  # id(all_works) -> {work_category: (q1, q3, iqr)}

MILD_OUTLIER_IQR = 1.5   # standard Tukey fence
SEVERE_OUTLIER_IQR = 3.0


def _iqr_outlier_score(value, q1, q3, iqr):
    """0.0 up to the mild-outlier fence, ramping to 1.0 at the severe fence."""
    if q1 is None or iqr is None or iqr <= 0 or value is None or pd.isna(value):
        return 0.0
    if value <= q3:
        return 0.0
    excess_iqrs = (value - q3) / iqr
    score = (excess_iqrs - MILD_OUTLIER_IQR) / (SEVERE_OUTLIER_IQR - MILD_OUTLIER_IQR)
    return float(np.clip(score, 0.0, 1.0))


def _get_peer_stats(all_works: pd.DataFrame):
    """Sanctioned-amount IQR fences per work_category, cached per DataFrame
    instance -- these are peer statistics over the whole dataset, not
    per-work, so they only need to be computed once."""
    key = id(all_works)
    if key in _peer_stats_cache:
        return _peer_stats_cache[key]

    stats = {}
    for category, group in all_works.groupby("work_category"):
        amounts = group["sanctioned_amount"].dropna()
        q1, q3 = amounts.quantile(0.25), amounts.quantile(0.75)
        stats[category] = (q1, q3, q3 - q1)
    _peer_stats_cache[key] = stats
    return stats


def score_money(work: dict, all_works) -> float:
    """
    Two independent cost-irregularity checks, combined via max() -- worst
    signal wins, per Rules.md's aggregation principle:
      1. IQR peer comparison -- is this work's sanctioned_amount an outlier
         against other works in the same work_category?
      2. CPWD DSR-style standard-cost check -- does sanctioned_amount blow
         past what the BOQ quantity x standard rate says it should cost?
    """
    sanctioned = work.get("sanctioned_amount")
    category = work.get("work_category")

    peer_stats = _get_peer_stats(all_works)
    q1, q3, iqr = peer_stats.get(category, (None, None, None))
    peer_score = _iqr_outlier_score(sanctioned, q1, q3, iqr)

    dsr_score = 0.0
    expected_from_boq = work.get("expected_cost_from_boq")
    if expected_from_boq and expected_from_boq > 0 and sanctioned and not pd.isna(sanctioned):
        ratio = sanctioned / expected_from_boq
        if ratio > 1.0:
            # 1.0x expected -> 0, 2.0x -> 0.5, 3.0x+ -> 1.0
            dsr_score = float(np.clip((ratio - 1.0) / 2.0, 0.0, 1.0))

    return float(max(peer_score, dsr_score))


def evidence_money(work: dict, all_works) -> dict:
    """
    Phase 14 (Evidence Layer) -- additive only. Returns the underlying
    numbers behind score_money()'s float, for the detail-analysis page
    (STAGE-ANALYSIS-v5.md's "why is this flagged" reasoning). Recomputes
    using the exact same cached helpers score_money() already uses --
    no new logic, no change to score_money() itself, so every existing
    caller (agency_network.py, isolation_forest.py, pipeline.py, main.py)
    is unaffected.
    """
    sanctioned = work.get("sanctioned_amount")
    category = work.get("work_category")

    peer_stats = _get_peer_stats(all_works)
    q1, q3, iqr = peer_stats.get(category, (None, None, None))
    peer_score = _iqr_outlier_score(sanctioned, q1, q3, iqr)

    expected_from_boq = work.get("expected_cost_from_boq")
    boq_ratio = None
    dsr_score = 0.0
    if expected_from_boq and expected_from_boq > 0 and sanctioned and not pd.isna(sanctioned):
        boq_ratio = float(sanctioned / expected_from_boq)
        if boq_ratio > 1.0:
            dsr_score = float(np.clip((boq_ratio - 1.0) / 2.0, 0.0, 1.0))

    return {
        "sanctioned_amount": sanctioned,
        "work_category": category,
        "peer_q3_sanctioned_amount": q3,
        "peer_outlier_score": peer_score,
        "expected_cost_from_boq": expected_from_boq,
        "sanctioned_vs_boq_ratio": boq_ratio,
        "dsr_score": dsr_score,
    }
