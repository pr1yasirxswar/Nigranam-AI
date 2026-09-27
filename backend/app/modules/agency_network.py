"""
Phase 2 -- Agency network analysis (Module C).
NetworkX graph over (executing_agency, implementing_district) operating
units. Degree centrality surfaces regional risk concentration.

Scoped deliberately as "regional risk concentration," not a collusion claim
-- only 7 generic agency-type categories exist in the dataset, not distinct
company identities (Architecture.md S3, PRD.md 4.2).

Interface: score_agency_network(work: dict, all_works: pd.DataFrame) -> float in [0, 1]
"""
import pandas as pd
import networkx as nx

from app.modules.money import score_money
from app.modules.progress import score_progress
from app.modules.delay import score_delay
from app.scoring.risk_aggregator import tier_for_score

_graph_cache = {}  # id(all_works) -> (graph, {unit: degree_centrality})
_unit_reason_cache = {}  # id(all_works) -> {unit: reason_dict} -- Phase 8 bug fix, see below

# A unit is only genuinely "flag-worthy" if at least one of its OWN works
# individually cleared High -- reuses risk_aggregator's existing tier
# floor (not a new number, Rules.md).
HIGH_PLUS_TIERS = {"Critical", "High"}
SIGNAL_LABELS = {
    "money": "cost anomaly",
    "progress": "progress-vs-expenditure mismatch",
    "delay": "delay",
}

# A unit only becomes eligible to be a node in the risk-concentration graph
# once its mean risk clears this percentile across all units -- this keeps
# the graph to "elevated" units only, not every unit in the dataset.
ELEVATED_PERCENTILE = 0.75


def _unit_key(work: dict):
    return (work.get("executing_agency"), work.get("implementing_district"))


def _build_graph(all_works: pd.DataFrame):
    """
    Builds the (agency, district) risk-concentration graph once per
    DataFrame instance (cached -- this is a whole-dataset computation, not
    a per-work one):
      1. Compute each unit's mean risk, using the three cheap per-row/peer
         modules (money, progress, delay). Duplicate detection is deliberately
         excluded here: it's a pairwise/global signal already, not a natural
         per-unit aggregate, and would just re-derive signal already used
         elsewhere in the aggregator.
      2. Keep only units at or above the elevated-risk percentile.
      3. Connect two elevated units with an edge if they're in the same
         state -- this is the "regional" in regional risk concentration.
      4. Degree centrality over that graph is the per-unit network score.
    """
    key = id(all_works)
    if key in _graph_cache:
        return _graph_cache[key]

    unit_scores = {}  # unit -> list of per-work risk scores
    unit_state = {}   # unit -> state (first one seen; a unit doesn't cross states)

    for _, row in all_works.iterrows():
        work = row.to_dict()
        unit = _unit_key(work)
        if unit[0] is None or unit[1] is None or pd.isna(unit[0]) or pd.isna(unit[1]):
            continue
        risk = max(
            score_money(work, all_works),
            score_progress(work),
            score_delay(work, all_works),
        )
        unit_scores.setdefault(unit, []).append(risk)
        unit_state.setdefault(unit, work.get("state"))

    if not unit_scores:
        _graph_cache[key] = (nx.Graph(), {})
        return _graph_cache[key]

    unit_mean = {u: sum(v) / len(v) for u, v in unit_scores.items()}
    sorted_means = sorted(unit_mean.values())
    cutoff_idx = int(ELEVATED_PERCENTILE * (len(sorted_means) - 1))
    threshold = sorted_means[cutoff_idx]

    elevated = [u for u, m in unit_mean.items() if m >= threshold and m > 0]

    graph = nx.Graph()
    graph.add_nodes_from(elevated)
    for i in range(len(elevated)):
        for j in range(i + 1, len(elevated)):
            u1, u2 = elevated[i], elevated[j]
            if unit_state.get(u1) == unit_state.get(u2):
                graph.add_edge(u1, u2)

    centrality = nx.degree_centrality(graph) if graph.number_of_nodes() > 0 else {}
    _graph_cache[key] = (graph, centrality)
    return _graph_cache[key]


def _build_unit_reasons(all_works: pd.DataFrame):
    """
    Phase 8 bug fix (PRD.md S7 item 3 / S4.2, Implementation-Guide.md
    Phase 8 item 3) -- for every unit already in the elevated set
    (_build_graph above), compute WHY: how many of its own works
    individually cleared the existing High-tier floor
    (risk_aggregator.tier_for_score, reused not reinvented) on any of the
    three per-unit signals, and which signal drove that most often.

    A unit only gets a reason -- and routers/agencies.py only ever shows
    units that have one -- if at least one of its OWN works genuinely
    cleared High. An elevated MEAN score alone (which the graph's
    ELEVATED_PERCENTILE cutoff can produce from several merely-Medium
    works) is not treated as flag-worthy by itself; surfacing that with no
    per-work evidence is exactly the bare-flag bug this fixes.

    Cached the same way _build_graph is (once per DataFrame instance) --
    read-only, does not change _build_graph()'s own logic or cache
    (Rules.md: don't rewrite a stable module beyond its named bug).
    """
    key = id(all_works)
    if key in _unit_reason_cache:
        return _unit_reason_cache[key]

    graph, _ = _build_graph(all_works)
    elevated_units = set(graph.nodes)

    if not elevated_units:
        _unit_reason_cache[key] = {}
        return _unit_reason_cache[key]

    per_unit = {
        u: {"total": 0, "high_plus": 0, "signal_hits": {"money": 0, "progress": 0, "delay": 0}}
        for u in elevated_units
    }

    for _, row in all_works.iterrows():
        work = row.to_dict()
        unit = _unit_key(work)
        if unit not in elevated_units:
            continue
        scores = {
            "money": score_money(work, all_works),
            "progress": score_progress(work),
            "delay": score_delay(work, all_works),
        }
        top_signal = max(scores, key=lambda k: scores[k])
        per_unit[unit]["total"] += 1
        if tier_for_score(scores[top_signal]) in HIGH_PLUS_TIERS:
            per_unit[unit]["high_plus"] += 1
            per_unit[unit]["signal_hits"][top_signal] += 1

    reasons = {}
    for unit, stats in per_unit.items():
        if stats["high_plus"] == 0:
            continue  # no genuinely High+ work in this unit -- no reason, no flag
        dominant_signal = max(stats["signal_hits"], key=lambda k: stats["signal_hits"][k])
        reasons[unit] = {
            "works_count": stats["total"],
            "high_plus_count": stats["high_plus"],
            "dominant_signal": dominant_signal,
            "reason": (
                f"{stats['high_plus']} of {stats['total']} works in this unit scored High+ "
                f"on {SIGNAL_LABELS[dominant_signal]}."
            ),
        }

    _unit_reason_cache[key] = reasons
    return reasons


def get_reasoned_units(all_works: pd.DataFrame):
    """
    Phase 8 bug fix -- read-only accessor for routers/agencies.py's Network
    view, mirroring get_graph_and_centrality()'s existing pattern below.
    Returns only units with a genuine reason; a unit with no reason is
    simply absent, never returned with a bare flag.
    """
    return _build_unit_reasons(all_works)


def get_graph_and_centrality(all_works: pd.DataFrame):
    """
    Phase 5 -- read-only accessor for routers/agencies.py's Network view.
    Thin wrapper over the same cached _build_graph() the scoring function
    below uses, so the view and the score can never see two different
    graphs. Does not change _build_graph()'s logic (Rules.md: once a
    module passes its exit check, don't rewrite it).
    """
    return _build_graph(all_works)


def score_agency_network(work: dict, all_works) -> float:
    """
    Regional risk-concentration score for the (executing_agency,
    implementing_district) unit this work belongs to: how many other
    same-state, similarly-elevated-risk units it's connected to in the
    graph. A unit that never clears the elevated-risk threshold (or has no
    same-state peers that do) scores 0.0.
    """
    _, centrality = _build_graph(all_works)
    unit = _unit_key(work)
    return float(centrality.get(unit, 0.0))
