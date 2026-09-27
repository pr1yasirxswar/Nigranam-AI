"""
Phase 2 exit validation (Implementation-Guide.md Phase 2, step 4):
runs the full aggregator against the dataset's seeded ground-truth labels
(`is_seeded_anomaly` / `seeded_anomaly_type`, added in DATASET-CHANGELOG.md
v2->v3) and reports recall @ Medium+ tier and false-positive rate, to
compare against the previously-measured bar (~91% recall, ~0.18% FPR).

Run from backend/:
    python -m scripts.validate_phase2
"""
from app.data_loader import load_works
from app.scoring.isolation_forest import build_feature_matrix, get_model, score_isolation_forest
from app.scoring.risk_aggregator import aggregate_risk

FLAGGED_TIERS = {"Critical", "High", "Medium"}


def main():
    all_works = load_works()

    # feature_matrix columns match FEATURE_NAMES in isolation_forest.py:
    # [duplicate, money, progress, delay, network_centrality]
    feature_matrix = build_feature_matrix(all_works)
    model = get_model(all_works)

    tiers = []
    for i in range(len(all_works)):
        feats = feature_matrix[i]
        module_scores = {
            "duplicate": float(feats[0]),
            "money": float(feats[1]),
            "progress": float(feats[2]),
            "delay": float(feats[3]),
            "network": float(feats[4]),
            "isolation_forest": score_isolation_forest(model, feats),
        }
        tiers.append(aggregate_risk(module_scores)["tier"])

    df = all_works.copy()
    df["_tier"] = tiers

    is_anomaly = df["is_seeded_anomaly"] == 1
    flagged = df["_tier"].isin(FLAGGED_TIERS)

    true_positives = int((is_anomaly & flagged).sum())
    n_anomalies = int(is_anomaly.sum())
    recall = true_positives / n_anomalies if n_anomalies else 0.0

    false_positives = int((~is_anomaly & flagged).sum())
    n_normal = int((~is_anomaly).sum())
    fpr = false_positives / n_normal if n_normal else 0.0

    print(f"Rows: {len(df)}  |  seeded anomalies: {n_anomalies}  |  normal rows: {n_normal}")
    print(f"Recall @ Medium+:      {recall:.4f}  ({true_positives}/{n_anomalies})   -- bar: ~0.91")
    print(f"False-positive rate:   {fpr:.4%}  ({false_positives}/{n_normal})   -- bar: ~0.18%")
    print()
    print("Per seeded-type recall @ Medium+:")
    for atype, group in df[is_anomaly].groupby("seeded_anomaly_type"):
        type_recall = group["_tier"].isin(FLAGGED_TIERS).mean()
        print(f"  {atype:35s} {type_recall:.2%}  (n={len(group)})")

    print()
    print("Tier distribution (all rows):")
    print(df["_tier"].value_counts())


if __name__ == "__main__":
    main()
