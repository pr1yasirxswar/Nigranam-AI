"""
Phase 1 -- Duplicate/inflated-work detection.
Sentence-BERT (primary) + TF-IDF (fallback) + Haversine distance corroboration.
See Implementation-Guide.md Phase 1, Architecture.md Section 5.

Interface: score_duplicate(work: dict, all_works: pd.DataFrame) -> float in [0, 1].

Phase-13-scale rewrite (not in the original Implementation Guide -- added
because the live dataset is ~1.2M rows, not the ~19,557 the guide's numbers
assumed): the original brute-force `matrix @ matrix[pos]` comparison, run
once per row for every row, is O(n^2) -- fine at n=19,557 (~380M ops),
impossible at n=1,208,908 (~1.46 TRILLION ops; the first request that
triggers _get_embeddings() would hang indefinitely).

This version replaces the brute-force scan with a FAISS approximate
nearest-neighbor index, and persists BOTH the embeddings and the index to
disk (backend/data/duplicate_index/) so they're built once and loaded on
every subsequent process start -- same "load from disk unless the file is
missing" pattern isolation_forest.py already uses for its own model, for
the same reason (retraining/rebuilding should be a deliberate action, not
an implicit cost on every boot).

score_duplicate()'s and evidence_duplicate()'s SIGNATURES and RETURN
SHAPES are unchanged -- every existing caller (pipeline.py, this module's
own callers in isolation_forest.py) is unaffected. Only the internal
candidate-search mechanism changed.
"""
import os
from math import radians, sin, cos, sqrt, atan2

import numpy as np
import pandas as pd

_model = None
_sbert_unavailable = False
_index_cache = {}  # id(all_works) -> (faiss_index, matrix, method, work_id_list)

TOP_N_CANDIDATES = 5
TEXT_SIM_FLOOR = 0.55  # below this, don't treat it as a real textual match at all

_HERE = os.path.dirname(__file__)
_INDEX_DIR = os.path.normpath(os.path.join(_HERE, "..", "..", "data", "duplicate_index"))
_EMBEDDINGS_PATH = os.path.join(_INDEX_DIR, "embeddings.npy")
_FAISS_INDEX_PATH = os.path.join(_INDEX_DIR, "faiss.index")
_METHOD_PATH = os.path.join(_INDEX_DIR, "method.txt")
_WORK_IDS_PATH = os.path.join(_INDEX_DIR, "work_ids.npy")

# IVF needs at least this many training points per cluster to be meaningful;
# below this, fall back to a flat (exact) index -- brute force is fine at
# small n, the whole point of IVF is only needed once n gets large.
IVF_MIN_ROWS = 50_000
IVF_NLIST = 1024  # number of clusters; sqrt(n) is the usual rule of thumb,
# 1024 covers up to a few million rows without huge per-cluster imbalance


def _haversine_km(lat1, lon1, lat2, lon2):
    if any(pd.isna(v) for v in (lat1, lon1, lat2, lon2)):
        return None
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * 6371.0 * atan2(sqrt(a), sqrt(1 - a))


def _geo_weight(dist_km):
    if dist_km is None:
        return 0.5  # missing coordinates: don't fully discount, don't fully trust
    if dist_km <= 5:
        return 1.0
    if dist_km <= 50:
        return 0.7
    if dist_km <= 200:
        return 0.3
    return 0.05


def _build_embeddings(all_works: pd.DataFrame):
    """Encodes every work_description once. Returns (matrix, method).
    matrix rows are L2-normalized so inner product == cosine similarity --
    required for FAISS's IndexFlatIP/IndexIVFFlat with METRIC_INNER_PRODUCT."""
    descriptions = all_works["work_description"].fillna("").tolist()
    global _model, _sbert_unavailable
    matrix, method = None, None

    if not _sbert_unavailable:
        try:
            if _model is None:
                from sentence_transformers import SentenceTransformer
                _model = SentenceTransformer("all-MiniLM-L6-v2")
            # batch_size tuned for CPU throughput at ~1.2M rows -- encoding
            # this many descriptions takes real wall-clock time regardless
            # (expect tens of minutes to a few hours on CPU); this is a
            # one-time cost, persisted to disk afterward.
            matrix = _model.encode(
                descriptions, show_progress_bar=True, normalize_embeddings=True,
                batch_size=256,
            ).astype("float32")
            method = "sbert"
        except Exception:
            _sbert_unavailable = True

    if matrix is None:
        # TF-IDF fallback: reduced to dense via TruncatedSVD, since FAISS
        # needs dense float32 vectors (a raw TF-IDF sparse matrix has tens
        # of thousands of dims -- SVD to ~256 keeps it FAISS-friendly and
        # still captures most of the similarity signal).
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.decomposition import TruncatedSVD
        from sklearn.preprocessing import normalize as sk_normalize

        vectorizer = TfidfVectorizer(stop_words="english", max_features=50_000)
        sparse_matrix = vectorizer.fit_transform(descriptions)
        n_components = min(256, sparse_matrix.shape[1] - 1, sparse_matrix.shape[0] - 1)
        svd = TruncatedSVD(n_components=max(n_components, 2), random_state=42)
        dense = svd.fit_transform(sparse_matrix)
        matrix = sk_normalize(dense).astype("float32")
        method = "tfidf"

    return np.ascontiguousarray(matrix), method


def _build_faiss_index(matrix: np.ndarray):
    """IVF for large n (approximate, sub-linear query time); flat (exact)
    for small n where brute force is already fast and IVF's training
    overhead isn't worth it."""
    import faiss

    dim = matrix.shape[1]
    n = matrix.shape[0]

    if n < IVF_MIN_ROWS:
        index = faiss.IndexFlatIP(dim)
        index.add(matrix)
        return index

    nlist = min(IVF_NLIST, max(1, n // 40))  # keep >= ~40 points/cluster on average
    quantizer = faiss.IndexFlatIP(dim)
    index = faiss.IndexIVFFlat(quantizer, dim, nlist, faiss.METRIC_INNER_PRODUCT)
    index.train(matrix)
    index.add(matrix)
    index.nprobe = min(32, nlist)  # search 32 clusters per query -- accuracy/speed tradeoff
    return index


def _get_index(all_works: pd.DataFrame):
    """Returns (faiss_index, matrix, method, work_ids), cached in-process
    per DataFrame instance, and persisted to disk (backend/data/
    duplicate_index/) so it survives process restarts -- built once, not
    on every boot, same reasoning as isolation_forest.py's MODEL_PATH."""
    key = id(all_works)
    if key in _index_cache:
        return _index_cache[key]

    import faiss

    work_ids = all_works["work_id"].to_numpy()

    if (os.path.exists(_FAISS_INDEX_PATH) and os.path.exists(_EMBEDDINGS_PATH)
            and os.path.exists(_METHOD_PATH) and os.path.exists(_WORK_IDS_PATH)):
        cached_work_ids = np.load(_WORK_IDS_PATH, allow_pickle=True)
        if len(cached_work_ids) == len(work_ids) and (cached_work_ids == work_ids).all():
            index = faiss.read_index(_FAISS_INDEX_PATH)
            matrix = np.load(_EMBEDDINGS_PATH)
            method = open(_METHOD_PATH).read().strip()
            _index_cache[key] = (index, matrix, method, work_ids)
            return _index_cache[key]
        # else: dataset changed since the cached index was built -- rebuild below.

    matrix, method = _build_embeddings(all_works)
    index = _build_faiss_index(matrix)

    os.makedirs(_INDEX_DIR, exist_ok=True)
    faiss.write_index(index, _FAISS_INDEX_PATH)
    np.save(_EMBEDDINGS_PATH, matrix)
    np.save(_WORK_IDS_PATH, work_ids, allow_pickle=True)
    with open(_METHOD_PATH, "w") as f:
        f.write(method)

    _index_cache[key] = (index, matrix, method, work_ids)
    return _index_cache[key]


def _search_candidates(work: dict, all_works: pd.DataFrame):
    """Returns a list of (work_id, text_sim, row_position) for this work's
    approximate top-N nearest neighbors, excluding itself. Shared by
    score_duplicate() and evidence_duplicate() so they can never drift out
    of sync with each other (same principle risk_aggregator.py's docstring
    already applies elsewhere in this codebase)."""
    index, matrix, method, work_ids = _get_index(all_works)

    idx_matches = np.where(work_ids == work.get("work_id"))[0]
    if len(idx_matches) == 0:
        return []
    pos = int(idx_matches[0])

    query = matrix[pos:pos + 1]
    # +1 candidate since the work always matches itself with sim=1.0 --
    # filtered out below.
    sims, positions = index.search(query, TOP_N_CANDIDATES + 1)
    sims, positions = sims[0], positions[0]

    candidates = []
    for sim, cand_pos in zip(sims, positions):
        if cand_pos < 0 or cand_pos == pos:
            continue
        candidates.append((work_ids[cand_pos], float(sim), int(cand_pos)))
    return candidates


def score_duplicate(work: dict, all_works) -> float:
    """
    Flags near-duplicate work entries: a work_description that closely matches
    another work's, corroborated by geographic proximity (a copy-pasted entry
    a few hundred meters away is far more suspicious than similar wording on
    the other side of the country, which is often just a common phrase like
    "Construction of internal road").
    """
    candidates = _search_candidates(work, all_works)
    if not candidates:
        return 0.0

    best_score = 0.0
    for cand_work_id, text_sim, cand_pos in candidates:
        if text_sim <= 0:
            continue
        cand_row = all_works.iloc[cand_pos]
        dist_km = _haversine_km(
            work.get("latitude"), work.get("longitude"),
            cand_row.get("latitude"), cand_row.get("longitude"),
        )
        combined = text_sim * _geo_weight(dist_km)
        if text_sim < TEXT_SIM_FLOOR:
            combined *= 0.3
        best_score = max(best_score, combined)

    return float(np.clip(best_score, 0.0, 1.0))


def evidence_duplicate(work: dict, all_works) -> dict:
    """
    Phase 14 (Evidence Layer) -- additive only. Same top-N approximate
    search score_duplicate() runs, but returns WHICH candidate work_id it
    matched against and the raw similarity/distance numbers, instead of
    collapsing straight to a single float. No change to score_duplicate()
    itself.
    """
    candidates = _search_candidates(work, all_works)
    if not candidates:
        return {"matched_work_id": None}

    best = {"matched_work_id": None, "text_similarity": 0.0, "distance_km": None, "combined_score": 0.0}
    for cand_work_id, text_sim, cand_pos in candidates:
        if text_sim <= 0:
            continue
        cand_row = all_works.iloc[cand_pos]
        dist_km = _haversine_km(
            work.get("latitude"), work.get("longitude"),
            cand_row.get("latitude"), cand_row.get("longitude"),
        )
        combined = text_sim * _geo_weight(dist_km)
        if text_sim < TEXT_SIM_FLOOR:
            combined *= 0.3
        if combined > best["combined_score"]:
            best = {
                "matched_work_id": cand_work_id,
                "text_similarity": text_sim,
                "distance_km": dist_km,
                "combined_score": combined,
            }
    return best
