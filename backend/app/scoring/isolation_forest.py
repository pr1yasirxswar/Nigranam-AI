"""
Phase 2 -- Isolation Forest, trained on engineered features from the
modules above (duplicate score, cost deviation, delay ratio, network
centrality). The one model in the pipeline genuinely trained by the team.
See Tech-Stack-Reference.md's "Why these choices" section for the judge Q&A
framing.

Core interface (kept stable -- Rules.md):
    train_isolation_forest(feature_matrix) -> fitted sklearn IsolationForest
    score_isolation_forest(model, feature_row) -> float in [0, 1]

Per Rules.md:
  - Trained on ENGINEERED features (the outputs of the other modules),
    never on raw project rows directly.
  - Its score is one input into risk_aggregator.py's max-tier logic, never
    a replacement for the rule-based signals.
  - Retrain only when the feature-engineering modules upstream of it
    change -- not silently on every pipeline run. In this implementation
    that means: once a model is saved to disk, it's loaded from disk on
    every subsequent run until someone deletes the file.
"""
import os

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
import joblib

from app.modules.duplicate import score_duplicate
from app.modules.money import score_money
from app.modules.progress import score_progress
from app.modules.delay import score_delay
from app.modules.agency_network import score_agency_network

FEATURE_NAMES = ["duplicate", "money", "progress", "delay", "network_centrality"]

_HERE = os.path.dirname(__file__)
MODEL_PATH = os.path.normpath(os.path.join(_HERE, "..", "..", "data", "isolation_forest.joblib"))

_model_cache = {}    # id(all_works) -> fitted IsolationForest
_feature_cache = {}  # id(all_works) -> np.ndarray, row order matches all_works.iterrows()


def build_feature_matrix(all_works: pd.DataFrame) -> np.ndarray:
    """
    Runs the four Phase-1 modules + agency_network on every row of the
    dataset and stacks their scores into the engineered feature matrix
    Isolation Forest trains on. Cached per DataFrame instance -- this is a
    whole-dataset pass, not something to redo per work.
    """
    key = id(all_works)
    if key in _feature_cache:
        return _feature_cache[key]

    rows = []
    for _, row in all_works.iterrows():
        work = row.to_dict()
        rows.append([
            score_duplicate(work, all_works),
            score_money(work, all_works),
            score_progress(work),
            score_delay(work, all_works),
            score_agency_network(work, all_works),
        ])
    matrix = np.array(rows, dtype=float)
    _feature_cache[key] = matrix
    return matrix


def train_isolation_forest(feature_matrix: np.ndarray) -> IsolationForest:
    """
    Fits sklearn's IsolationForest on the engineered feature matrix.
    Unsupervised -- no labels are used here. `seeded_anomaly_type` /
    `is_seeded_anomaly` are reserved strictly for post-hoc validation
    (Implementation-Guide.md Phase 2 step 4 / scripts/validate_phase2.py),
    never fed into training -- doing so would make the recall/FPR numbers
    meaningless (the model would just be memorizing labels).

    Also records the training distribution's own raw score_samples range
    (as robust 1st/99th-percentile bounds, stored as extra attributes on
    the fitted model) -- score_samples has no fixed, documented range, it
    depends on the data and the fitted trees. score_isolation_forest below
    normalizes against these bounds rather than an assumed fixed range, so
    the [0, 1] output actually spreads across the training distribution
    instead of saturating at one end for most rows.
    """
    model = IsolationForest(
        n_estimators=200,
        contamination="auto",
        random_state=42,
    )
    model.fit(feature_matrix)

    raw_scores = model.score_samples(feature_matrix)  # higher = more normal
    model._raw_score_low = float(np.percentile(raw_scores, 1))    # near-most-anomalous end
    model._raw_score_high = float(np.percentile(raw_scores, 99))  # near-most-normal end
    return model


def score_isolation_forest(model: IsolationForest, feature_row) -> float:
    """
    Converts sklearn's raw anomaly score (score_samples: higher = more
    normal, range depends on the fitted data -- not fixed) into a [0, 1]
    score where higher = more anomalous, min-max normalized against the
    training distribution's own 1st/99th-percentile bounds (set on the
    model by train_isolation_forest). A model without those bounds attached
    (e.g. constructed some other way) falls back to a fixed-offset scaling
    rather than raising.
    """
    feature_row = np.asarray(feature_row, dtype=float).reshape(1, -1)
    raw = model.score_samples(feature_row)[0]  # higher = more normal

    low = getattr(model, "_raw_score_low", None)
    high = getattr(model, "_raw_score_high", None)
    if low is None or high is None or high <= low:
        return float(np.clip(-raw + 0.5, 0.0, 1.0))  # fallback: fixed-offset, still bounded

    normalized = (high - raw) / (high - low)
    return float(np.clip(normalized, 0.0, 1.0))


def get_model(all_works: pd.DataFrame) -> IsolationForest:
    """
    Returns a fitted model, cached in-process per DataFrame instance.
    If a model is already saved to disk (MODEL_PATH), loads that instead of
    retraining -- per Rules.md, retraining should be a deliberate action
    (delete the file, or call train_isolation_forest directly), not an
    implicit side effect of every server start.
    """
    key = id(all_works)
    if key in _model_cache:
        return _model_cache[key]

    if os.path.exists(MODEL_PATH):
        model = joblib.load(MODEL_PATH)
    else:
        feature_matrix = build_feature_matrix(all_works)
        model = train_isolation_forest(feature_matrix)
        os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
        joblib.dump(model, MODEL_PATH)

    _model_cache[key] = model
    return model


def score_work_isolation_forest(work: dict, all_works: pd.DataFrame) -> float:
    """
    Convenience wrapper matching the other modules' (work, all_works)
    interface, for risk_aggregator.py / main.py to call uniformly alongside
    score_duplicate / score_money / score_progress / score_delay /
    score_agency_network.
    """
    model = get_model(all_works)
    feature_row = [
        score_duplicate(work, all_works),
        score_money(work, all_works),
        score_progress(work),
        score_delay(work, all_works),
        score_agency_network(work, all_works),
    ]
    return score_isolation_forest(model, feature_row)
