"""
Phase 2 -- Combines all module scores (four Phase-1 modules + agency_network
+ isolation_forest) via MAX-TIER logic -- worst signal wins, never averaged
(Rules.md, Tech-Stack-Reference.md's "why max-tier" Q&A answer).

Phase 6 update (per Rules.md: "New scoring modules must follow the same
interface pattern ... so risk_aggregator.py doesn't need to change its
aggregation logic, only its list of inputs") -- photo_forensics and weather
were added to ALL_SIGNAL_KEYS and to the shape classification below.
Nothing about _tier_for_score's thresholds or the max-tier aggregation
itself changed.

Output: severity tier (Critical/High/Medium/Low/Normal) + shape
(circle = fraud-type, triangle = inefficiency-type).

Isolation Forest's score is one input among the signals, not a replacement
for the explainable rule-based signals -- but see the DESIGN NOTE below for
how "shape" specifically handles it.

Interface:
    aggregate_risk(module_scores: dict) -> dict

    module_scores keys (all floats in [0, 1]; a missing key is treated the
    same as a score of 0.0 for that signal):
        "duplicate", "money", "progress", "delay", "network",
        "isolation_forest", "photo_forensics", "weather"

    Returns:
        {
            "tier": "Critical" | "High" | "Medium" | "Low" | "Normal",
            "shape": "circle" | "triangle" | None,
            "driving_signal": <module name with the max score>,
            "scores": <module_scores, echoed back for transparency>,
        }
"""

# Tier thresholds applied to the single winning (max) raw signal score.
# Ordered highest floor first; first match wins.
TIER_THRESHOLDS = [
    ("Critical", 0.85),
    ("High", 0.65),
    ("Medium", 0.40),
    ("Low", 0.20),
]

# DESIGN NOTE (not specified in the docs -- flagging this as an assumption):
# Architecture.md/PRD.md define the shape code (circle = fraud-type,
# triangle = inefficiency-type) but don't say which module maps to which.
# This implementation treats it as: duplicate/money -> looks deliberate
# (circle); progress/delay -> looks like execution trouble (triangle).
# agency_network and isolation_forest aren't independently explainable at
# the per-feature level the way the four rule-based modules are, so neither
# assigns a shape directly when it's the top score -- shape instead falls
# back to whichever rule-based module is the runner-up, so a flag still
# traces to an explainable reason a judge can ask about. If every
# rule-based module is genuinely at 0, shape is left unset (None) rather
# than guessed.
#
# Phase 6 additions, same reasoning: photo_forensics (GPS mismatch, reused
# hash, backdated capture) is fabricated/deliberate evidence -> FRAUD
# (circle), same bucket as duplicate/money. weather is about a delay excuse
# that historical weather doesn't corroborate -- also a misrepresentation
# of cause rather than a genuine execution problem, so it joins FRAUD too,
# not INEFFICIENCY (see weather_check.py's own DESIGN NOTE on why this
# signal is indirect and capped below 1.0).
FRAUD_MODULES = {"duplicate", "money", "photo_forensics", "weather"}
INEFFICIENCY_MODULES = {"progress", "delay"}
RULE_BASED_MODULES = FRAUD_MODULES | INEFFICIENCY_MODULES

ALL_SIGNAL_KEYS = [
    "duplicate", "money", "progress", "delay", "network", "isolation_forest",
    "photo_forensics", "weather",
]


def _tier_for_score(score: float) -> str:
    for tier, floor in TIER_THRESHOLDS:
        if score >= floor:
            return tier
    return "Normal"


# Phase 8 bug fix (PRD.md S7 item 3) -- public alias, purely additive.
# agency_network.py's per-work High+ counting (the Network-view reasoning
# fix) needs the exact same tier floor this module already uses, rather
# than a second, separately-guessed threshold (Rules.md: don't invent
# domain thresholds that already exist elsewhere). No change to
# aggregation logic itself.
tier_for_score = _tier_for_score


def _shape_for(driving_signal: str, module_scores: dict) -> str | None:
    if driving_signal in FRAUD_MODULES:
        return "circle"
    if driving_signal in INEFFICIENCY_MODULES:
        return "triangle"

    # driving_signal is "network" or "isolation_forest" (or unrecognized) --
    # fall back to the strongest rule-based signal, if any is nonzero.
    rule_based_scores = {k: v for k, v in module_scores.items() if k in RULE_BASED_MODULES}
    if rule_based_scores and max(rule_based_scores.values()) > 0:
        runner_up = max(rule_based_scores, key=lambda k: rule_based_scores[k])
        return "circle" if runner_up in FRAUD_MODULES else "triangle"
    return None


def aggregate_risk(module_scores: dict, module_evidence: dict | None = None) -> dict:
    scores = {k: float(module_scores.get(k, 0.0)) for k in ALL_SIGNAL_KEYS}

    driving_signal = max(scores, key=lambda k: scores[k])
    max_score = scores[driving_signal]
    tier = _tier_for_score(max_score)

    shape = _shape_for(driving_signal, scores) if tier != "Normal" else None

    return {
        "tier": tier,
        "shape": shape,
        "driving_signal": driving_signal,
        "scores": scores,
        # Phase 14 (Evidence Layer) -- additive. module_evidence is whatever
        # score_work() (scoring/pipeline.py) passed in -- the raw
        # numbers/dates behind each module's float, for the detail page.
        # Defaults to {} so a caller that doesn't pass evidence (e.g. any
        # test that still calls aggregate_risk(scores) the old way) is
        # unaffected.
        "evidence": module_evidence or {},
    }
