"""
Router: public -- PRD.md S4.10's public area-lookup flow.

  GET  /public/summary        -- same figures as GET /stats/public-summary
                                  (Phase 8's fix for the S7 item 1 scope
                                  leak); re-exported under /public/* so the
                                  whole no-login flow lives under one
                                  prefix. Not a second implementation --
                                  calls stats.public_summary() directly.
  GET  /public/districts       -- state -> sorted list of districts,
                                  derived straight from the works data
                                  (get_all_works()) so the frontend's
                                  state/district dropdowns can never list
                                  a combination with no matching data.
  GET  /public/area-projects  -- state + district -> that district's
                                  project list, PRD.md S4.10's allowed
                                  fields only (status, implementing
                                  authority, % complete -- no risk/tier or
                                  financial data). No phone/OTP
                                  verification required -- a visitor picks
                                  their state and district from a list and
                                  sees that area's projects directly.
"""
from collections import defaultdict

from fastapi import APIRouter, HTTPException, Query, status

from app.data_loader import get_all_works
from app.routers.stats import public_summary as _stats_public_summary

router = APIRouter(prefix="/public", tags=["public"])


@router.get("/summary")
def public_summary():
    return _stats_public_summary()


@router.get("/districts")
def public_districts():
    all_works = get_all_works()
    by_state = defaultdict(set)
    for state, district in zip(
        all_works["state"].astype(str), all_works["implementing_district"].astype(str)
    ):
        state = state.strip()
        district = district.strip()
        if not state or state.lower() == "nan" or not district or district.lower() == "nan":
            continue
        by_state[state].add(district)
    return {state: sorted(districts) for state, districts in sorted(by_state.items())}


# PRD.md S4.10 / dashboard.js's existing copy: "status, implementing
# authority, % complete -- no risk data". Deliberately excludes
# sanctioned_amount/expenditure (financial detail beyond what an
# unauthenticated area lookup should show) and any tier/flag data.
_AREA_FIELDS = [
    "work_id",
    "work_category",
    "work_description",
    "executing_agency",
    "status",
    "physical_progress_pct",
]


@router.get("/area-projects")
def public_area_projects(
    state: str = Query(..., description="State name, as returned by GET /public/districts"),
    district: str = Query(..., description="District name, as returned by GET /public/districts"),
):
    state_needle = state.strip().lower()
    district_needle = district.strip().lower()
    if not state_needle or not district_needle:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="state and district are both required.")

    all_works = get_all_works()
    mask = (
        all_works["state"].astype(str).str.lower().eq(state_needle)
        & all_works["implementing_district"].astype(str).str.lower().eq(district_needle)
    )
    matches = all_works[mask]
    return [{k: row.get(k) for k in _AREA_FIELDS} for row in matches.to_dict(orient="records")]