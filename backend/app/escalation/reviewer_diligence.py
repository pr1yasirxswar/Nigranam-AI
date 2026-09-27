"""
Phase 4 -- scores each authority's clearance rate + justification quality,
surfacing suspiciously easy clearances to the next tier up (PRD.md S4.7 --
"we watch the watchers," the project's core differentiator, not just the
escalation timer alone).

Scoped per role (optionally narrowed to the works of one executing_agency,
via a caller-supplied work_id list) rather than per individual reviewer --
the seeded accounts are one demo login per role, not per person, so
per-role is the most specific pairing this build's identity layer actually
supports.

DESIGN NOTE (not specified in the docs, flagged here): "justification
quality" isn't a figure PRD.md defines anywhere, so this uses a generic,
defensible text-effort heuristic -- word count of the reason text -- rather
than inventing an MPLADS-specific rubric (Rules.md: don't invent domain
figures not already specified). LOW_EFFORT_WORD_THRESHOLD is a starting
point for the team to tune against real reviewer behavior once there's
real usage data, not a validated number.
"""
from app.models import Review

LOW_EFFORT_WORD_THRESHOLD = 8


def _word_count(text) -> int:
    return len((text or "").split())


def score_reviewer_diligence(db, role: str, work_ids: list[str] | None = None) -> dict:
    """
    Diligence report for one role, optionally restricted to a list of
    work_ids (routers/flags.py builds this list from the Pandas dataset
    when narrowing to one executing_agency -- Review rows don't carry
    executing_agency directly, since works live in Pandas, not Postgres).

    Looks at "clear" and "escalate" actions by that role -- the two things
    a reviewer actively chooses to do, as opposed to the system's
    "auto_escalate" (which reflects inaction, not a judgment call, and
    would only dilute the justification-quality signal).
    """
    query = db.query(Review).filter(Review.actor_role == role, Review.action.in_(("clear", "escalate")))
    if work_ids is not None:
        query = query.filter(Review.work_id.in_(work_ids))
    reviews = query.all()

    n_total = len(reviews)
    clear_reviews = [r for r in reviews if r.action == "clear"]
    n_cleared = len(clear_reviews)
    clearance_rate = n_cleared / n_total if n_total else 0.0

    word_counts = [_word_count(r.reason) for r in clear_reviews]
    avg_justification_words = sum(word_counts) / len(word_counts) if word_counts else 0.0
    low_effort_count = sum(1 for wc in word_counts if wc < LOW_EFFORT_WORD_THRESHOLD)
    low_effort_clearance_rate = low_effort_count / len(clear_reviews) if clear_reviews else 0.0

    # Composite "worth a senior tier's attention" score: clears a lot of
    # flags AND does so with thin justifications. Either alone is normal
    # behavior (a genuinely clean agency should clear a lot; a careful
    # reviewer might still write short notes) -- it's the combination that's
    # the pattern worth surfacing.
    diligence_score = clearance_rate * low_effort_clearance_rate

    return {
        "role": role,
        "n_total_actions": n_total,
        "n_cleared": n_cleared,
        "clearance_rate": round(clearance_rate, 4),
        "avg_justification_words": round(avg_justification_words, 2),
        "low_effort_clearance_rate": round(low_effort_clearance_rate, 4),
        "diligence_score": round(diligence_score, 4),
    }
