"""
Router: mp_compare -- Phase 10, "MP dashboard 2" (Implementation-Guide.md
Phase 10 item 6, PRD.md S5.6's cross-MP risk-comparison view).

GET /mp/compare -- one new read-only endpoint, aggregating each
constituency's risk stats side by side. Reuses the same per-constituency
concept Phase 3 already built into auth/permissions.py's ROLE_MP branch
(user_can_act_on_project/filter_works_for_user), just grouped across every
constituency in the dataset instead of scoping the *works themselves* to
one caller. Gated to MP only -- PRD.md S3/S5.6 is explicit that the
cross-MP comparison view belongs to the MP role alone; no other role has a
stated use for seeing every constituency side by side.

Deliberately returns only aggregate counts/rates per constituency (never
individual work_ids, review text, or reviewer identity) -- same
public-aggregate-only posture as routers/stats.py's /stats/public, just
grouped one level finer (per constituency instead of per state). This is
NOT a jurisdiction bypass: an MP already cannot act on (comment/clear/
escalate) any work outside their own constituency (auth/permissions.py is
unchanged), this endpoint only lets them see how their constituency's risk
profile compares to others', which is exactly what PRD.md S5.6 asks
"MP dashboard 2" to do.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.data_loader import get_all_works
from app.auth.permissions import require_roles, ROLE_MP
from app.models import Flag
from app.escalation.state_machine import ESCALATION_CHAIN, FLAGGED_TIERS

router = APIRouter(prefix="/mp", tags=["mp"])


@router.get("/compare")
def compare_constituencies(
    user=Depends(require_roles(ROLE_MP)),
    db: Session = Depends(get_db),
):
    all_works = get_all_works()
    flags = db.query(Flag).all()

    # work_id -> constituency, built once rather than re-scanning per flag.
    work_constituency = dict(zip(all_works["work_id"], all_works["constituency"]))

    by_constituency: dict[str, dict] = {}
    for _, row in all_works.dropna(subset=["constituency"]).iterrows():
        c = row["constituency"]
        entry = by_constituency.get(c)
        if entry is None:
            entry = {
                "constituency": c,
                "state": row.get("state"),
                "mp_name": row.get("mp_name"),
                "total_works": 0,
                "sanctioned_amount_total": 0.0,
                "flagged_count": 0,
                "critical_high_count": 0,
                "resolved_count": 0,
            }
            by_constituency[c] = entry
        entry["total_works"] += 1
        entry["sanctioned_amount_total"] += float(row.get("sanctioned_amount") or 0)

    for f in flags:
        c = work_constituency.get(f.work_id)
        entry = by_constituency.get(c)
        if entry is None:
            continue
        if f.tier in FLAGGED_TIERS:
            entry["flagged_count"] += 1
        if f.tier in ("Critical", "High"):
            entry["critical_high_count"] += 1
        if f.status not in ESCALATION_CHAIN:
            entry["resolved_count"] += 1

    result = []
    for entry in by_constituency.values():
        flagged = entry["flagged_count"]
        resolved = entry.pop("resolved_count")
        entry["resolution_rate"] = round(resolved / flagged, 3) if flagged else None
        entry["risk_rate"] = round(flagged / entry["total_works"], 3) if entry["total_works"] else 0.0
        result.append(entry)

    result.sort(key=lambda r: -r["risk_rate"])
    return result
