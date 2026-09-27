"""
Phase 1 -- Delay detection.
IQR-relative for completed works; fixed-scale rule for stalled in-progress works.
See Implementation-Guide.md Phase 1, Architecture.md Section 5.

Interface: score_delay(work: dict, all_works: pd.DataFrame) -> float in [0, 1].
"""
import numpy as np
import pandas as pd

_delay_stats_cache = {}     # id(all_works) -> (q1, q3, iqr) over completed-work delays
_reference_date_cache = {}  # id(all_works) -> pd.Timestamp used as "today" for stall checks

MILD_OUTLIER_IQR = 1.5
SEVERE_OUTLIER_IQR = 3.0

STALL_PROGRESS_PCT = 5       # "near-zero progress" per DATASET-CHANGELOG.md's stalled_project seed
STALL_SPEND_FRACTION = 0.05  # "expenditure under 5% of sanctioned"


def _completed_delay_days(row) -> float | None:
    expected = row.get("expected_completion_date")
    actual = row.get("actual_completion_date")
    if pd.isna(expected) or pd.isna(actual):
        return None
    return (pd.to_datetime(actual) - pd.to_datetime(expected)).days


def _get_delay_distribution(all_works: pd.DataFrame):
    key = id(all_works)
    if key in _delay_stats_cache:
        return _delay_stats_cache[key]
    completed = all_works[all_works["status"] == "Completed"]
    delays = completed.apply(_completed_delay_days, axis=1).dropna()
    q1, q3 = delays.quantile(0.25), delays.quantile(0.75)
    stats = (q1, q3, q3 - q1)
    _delay_stats_cache[key] = stats
    return stats


def _get_reference_date(all_works: pd.DataFrame):
    """The dataset is historical (recorded dates, not live ones), so there is
    no real "today" to measure an in-progress work's staleness against. Using
    the latest date seen anywhere in the dataset as a stand-in "as of" date
    keeps the stall check meaningful without hardcoding a MPLADS-specific
    figure (Rules.md)."""
    key = id(all_works)
    if key in _reference_date_cache:
        return _reference_date_cache[key]
    date_cols = ["expected_completion_date", "actual_completion_date", "last_payment_date"]
    all_dates = pd.concat([pd.to_datetime(all_works[c], errors="coerce") for c in date_cols])
    ref = all_dates.max()
    _reference_date_cache[key] = ref
    return ref


def score_delay(work: dict, all_works) -> float:
    status = work.get("status")

    if status == "Completed":
        delay_days = _completed_delay_days(work)
        if delay_days is None:
            return 0.0
        q1, q3, iqr = _get_delay_distribution(all_works)
        if iqr is None or pd.isna(iqr) or iqr <= 0 or delay_days <= q3:
            return 0.0
        excess_iqrs = (delay_days - q3) / iqr
        score = (excess_iqrs - MILD_OUTLIER_IQR) / (SEVERE_OUTLIER_IQR - MILD_OUTLIER_IQR)
        return float(np.clip(score, 0.0, 1.0))

    if status == "In Progress":
        expected = work.get("expected_completion_date")
        progress_pct = work.get("physical_progress_pct")
        sanctioned = work.get("sanctioned_amount")
        expenditure = work.get("expenditure")
        if expected is None or pd.isna(expected) or progress_pct is None or pd.isna(progress_pct):
            return 0.0

        reference_date = _get_reference_date(all_works)
        days_past_expected = (reference_date - pd.to_datetime(expected)).days
        if days_past_expected <= 0:
            return 0.0

        spend_fraction = None
        if sanctioned and not pd.isna(sanctioned) and sanctioned > 0 and expenditure is not None:
            spend_fraction = expenditure / sanctioned

        low_progress = progress_pct <= STALL_PROGRESS_PCT
        low_spend = spend_fraction is not None and spend_fraction < STALL_SPEND_FRACTION

        if low_progress and low_spend:
            # Fixed-scale: genuinely stalled works are flagged with high confidence
            # regardless of exactly how far past deadline -- confidence just climbs
            # with how long it's been stalled.
            scale = min(days_past_expected / 180.0, 1.0)
            return float(max(scale, 0.6))
        if low_progress:
            # Low progress alone (spend tracking is unclear/missing) -- weaker signal
            return float(np.clip(days_past_expected / 365.0, 0.0, 0.5))
        return 0.0

    return 0.0


def evidence_delay(work: dict, all_works) -> dict:
    """
    Phase 14 (Evidence Layer) -- additive only. Returns the underlying
    dates/numbers behind score_delay()'s float. Recomputes using the exact
    same cached helpers score_delay() already uses -- no change to
    score_delay() itself, no impact on its existing callers.
    """
    status = work.get("status")

    if status == "Completed":
        return {
            "status": status,
            "expected_completion_date": work.get("expected_completion_date"),
            "actual_completion_date": work.get("actual_completion_date"),
            "days_late": _completed_delay_days(work),
        }

    if status == "In Progress":
        expected = work.get("expected_completion_date")
        progress_pct = work.get("physical_progress_pct")
        sanctioned = work.get("sanctioned_amount")
        expenditure = work.get("expenditure")

        days_past_expected = None
        if expected is not None and not pd.isna(expected):
            reference_date = _get_reference_date(all_works)
            days_past_expected = (reference_date - pd.to_datetime(expected)).days

        spend_fraction = None
        if sanctioned and not pd.isna(sanctioned) and sanctioned > 0 and expenditure is not None:
            spend_fraction = float(expenditure / sanctioned)

        return {
            "status": status,
            "expected_completion_date": expected,
            "physical_progress_pct": progress_pct,
            "spend_fraction": spend_fraction,
            "days_past_expected_deadline": days_past_expected,
        }

    return {"status": status}
