"""
Phase 11 item 4 -- the final demo credential set (Implementation-Guide.md
Phase 11 item 4 / Build-Log.md #19): 4 fixed, distinct accounts per role
(Local Authority, District Authority, Nodal State Authority, MP,
Implementing Agency -- 20 total), each mapped to a real, distinct
jurisdiction slice already present in the dataset, so logging into
account 2 of a role visibly shows different data from account 1.

`Demo-Credentials.md` is the single source of truth for these values
(its own header says so) -- this file must match it exactly, not the
other way around. Every jurisdiction value below was checked directly
against `mplads_synthetic_v4.csv` before being written here (Rules.md:
"do not invent ... jurisdiction boundaries that aren't already
specified" -- these are pulled from the real data, not invented):

  - local1/district1 -- Kottayam, Kerala; local1's loc is "Ward 44", the
    one village_or_locality in Kottayam carrying the seeded
    progress_expenditure_mismatch flag (work_id MPLADS/KER/2019/000347).
  - local2/district2 -- Mirzapur, Uttar Pradesh; loc "Block 2" carries the
    seeded abnormal_delay flag (MPLADS/UTT/2022/002463).
  - local3/district3 -- Diamond Harbour, West Bengal; loc
    "Gram Panchayat 40" carries the seeded payment_before_sanction flag
    (MPLADS/WES/2022/003159).
  - local4 -- Guntur, Andhra Pradesh; loc "Gram Panchayat 6" is a clean
    contrast pair (both its works are anomaly-free) -- deliberately no
    flagged work here, per Demo-Credentials.md.
  - district4 -- Bhongir, Telangana (216 works) -- a fourth state, for
    variety, with no local-authority account nested under it (Local
    Authority only has 4 slots and district1-3 already cover Kottayam/
    Mirzapur/Diamond Harbour). Replaces "Zahirabad", which has no rows in
    the v5 dataset.
  - state1-4 -- Uttar Pradesh (886 works, largest in the dataset),
    Maharashtra (520), West Bengal (458), Bihar (439).
  - mp1-4 -- constituency values are UPPERCASE in the dataset
    (`df["constituency"]`): DIAMOND HARBOUR (West Bengal, 231 works),
    BHANDARA-GONDIYA (Maharashtra, 220), MIRZAPUR (Uttar Pradesh, 212),
    KOTTAYAM (Kerala, 204). The earlier MALDAHA UTTAR / SIVAGANGA /
    ROBERTSGANJ(SC) values do not exist in the dataset (0 works), so those
    three accounts were re-pointed. These three also line up with the
    district/local accounts (Diamond Harbour, Mirzapur, Kottayam), so one
    story can be followed from Local -> District -> MP.
  - agency1-4 -- Work IDs, executing_agency + implementing_district
    verified against the real work_id each maps to (see table below);
    these are Phase 11 item 3's issued credentials, not internal-scheme
    accounts (auth/agency_auth.py's docstring explains why they still
    share the same `users` table under the hood).

Run standalone from backend/:  python -m app.auth.seed_users
Also called automatically on API startup (main.py) -- idempotent, skips
any x_user_id that already exists.

Superseded set: the original Phase 3/8 one-account-per-role set
(demo-local/demo-district/demo-state/demo-mp/demo-mp-2/demo-agency) is
retired here, not kept alongside the 20 -- Demo-Credentials.md is
explicit that "only these 20 accounts exist." Anyone with the old
seed already applied to their local Postgres should drop and recreate
the `users` table (or just the six old rows) before re-running this.
"""
from app.database import SessionLocal, engine, Base
from app.models import User
from app.auth.security import hash_password, verify_password
from app.auth.permissions import (
    ROLE_IMPLEMENTING_AGENCY,
    ROLE_LOCAL_AUTHORITY,
    ROLE_DISTRICT_AUTHORITY,
    ROLE_NODAL_STATE_AUTHORITY,
    ROLE_MP,
)

DEMO_USERS = [
    # -- Local Authority (4) --------------------------------------------
    dict(
        x_user_id="local1",
        password="Local@Kottayam1",
        name="Local Authority -- Ward 44, Kottayam",
        role=ROLE_LOCAL_AUTHORITY,
        district="Kottayam",
        loc="Ward 44",
    ),
    dict(
        x_user_id="local2",
        password="Local@Mirzapur1",
        name="Local Authority -- Block 2, Mirzapur",
        role=ROLE_LOCAL_AUTHORITY,
        district="Mirzapur",
        loc="Block 2",
    ),
    dict(
        x_user_id="local3",
        password="Local@DiamondHarbour1",
        name="Local Authority -- Gram Panchayat 40, Diamond Harbour",
        role=ROLE_LOCAL_AUTHORITY,
        district="Diamond Harbour",
        loc="Gram Panchayat 40",
    ),
    dict(
        x_user_id="local4",
        password="Local@Guntur1",
        name="Local Authority -- Gram Panchayat 6, Guntur",
        role=ROLE_LOCAL_AUTHORITY,
        district="Guntur",
        loc="Gram Panchayat 6",
    ),

    # -- District Authority (4) ------------------------------------------
    dict(
        x_user_id="district1",
        password="District@Kottayam1",
        name="District Authority -- Kottayam",
        role=ROLE_DISTRICT_AUTHORITY,
        district="Kottayam",
    ),
    dict(
        x_user_id="district2",
        password="District@Mirzapur1",
        name="District Authority -- Mirzapur",
        role=ROLE_DISTRICT_AUTHORITY,
        district="Mirzapur",
    ),
    dict(
        x_user_id="district3",
        password="District@DiamondHarbour1",
        name="District Authority -- Diamond Harbour",
        role=ROLE_DISTRICT_AUTHORITY,
        district="Diamond Harbour",
    ),
    dict(
        x_user_id="district4",
        password="District@Bhongir1",
        name="District Authority -- Bhongir",
        role=ROLE_DISTRICT_AUTHORITY,
        district="Bhongir",
    ),

    # -- Nodal State Authority (4) ----------------------------------------
    dict(
        x_user_id="state1",
        password="State@UttarPradesh1",
        name="Nodal State Authority -- Uttar Pradesh",
        role=ROLE_NODAL_STATE_AUTHORITY,
        state="Uttar Pradesh",
    ),
    dict(
        x_user_id="state2",
        password="State@Maharashtra1",
        name="Nodal State Authority -- Maharashtra",
        role=ROLE_NODAL_STATE_AUTHORITY,
        state="Maharashtra",
    ),
    dict(
        x_user_id="state3",
        password="State@WestBengal1",
        name="Nodal State Authority -- West Bengal",
        role=ROLE_NODAL_STATE_AUTHORITY,
        state="West Bengal",
    ),
    dict(
        x_user_id="state4",
        password="State@Bihar1",
        name="Nodal State Authority -- Bihar",
        role=ROLE_NODAL_STATE_AUTHORITY,
        state="Bihar",
    ),

    # -- Member of Parliament (4) -----------------------------------------
    dict(
        x_user_id="mp1",
        password="MP@DiamondHarbour1",
        name="MP -- Diamond Harbour Constituency",
        role=ROLE_MP,
        constituency="DIAMOND HARBOUR",
    ),
    dict(
        x_user_id="mp2",
        password="MP@BhandaraGondiya1",
        name="MP -- Bhandara-Gondiya Constituency",
        role=ROLE_MP,
        constituency="BHANDARA-GONDIYA",
    ),
    dict(
        x_user_id="mp3",
        password="MP@Mirzapur1",
        name="MP -- Mirzapur Constituency",
        role=ROLE_MP,
        constituency="MIRZAPUR",
    ),
    dict(
        x_user_id="mp4",
        password="MP@Kottayam1",
        name="MP -- Kottayam Constituency",
        role=ROLE_MP,
        constituency="KOTTAYAM",
    ),

    # -- Implementing Agency (4) -- Phase 11 item 3's issued-credential
    # shape (auth/agency_auth.py), pre-seeded here with fixed Work IDs so
    # the demo doesn't depend on a District Authority actually running the
    # issuance flow live. ------------------------------------------------
    dict(
        x_user_id="WORK-KER-000347",
        password="Agency@Kottayam1",
        name="Rural Engineering Services (Kottayam)",
        role=ROLE_IMPLEMENTING_AGENCY,
        executing_agency="Rural Engineering Services",
        district="Kottayam",
    ),
    dict(
        x_user_id="WORK-UTT-002463",
        password="Agency@Mirzapur1",
        name="PWD (Mirzapur)",
        role=ROLE_IMPLEMENTING_AGENCY,
        executing_agency="PWD",
        district="Mirzapur",
    ),
    dict(
        x_user_id="WORK-UTT-002699",
        password="Agency@Unnao1",
        name="Panchayati Raj Department (Unnao)",
        role=ROLE_IMPLEMENTING_AGENCY,
        executing_agency="Panchayati Raj Department",
        district="Unnao",
    ),
    dict(
        x_user_id="WORK-MAH-001034",
        password="Agency@BhandaraGondiya1",
        name="District Rural Development Agency (Bhandara-Gondiya)",
        role=ROLE_IMPLEMENTING_AGENCY,
        executing_agency="District Rural Development Agency",
        district="Bhandara-Gondiya",
    ),
]


# Demo accounts that pointed at jurisdictions with no rows in the dataset.
# Removed on boot so they don't linger next to their replacements.
RETIRED_DEMO_IDS = ["WORK-UTT-003263"]


def seed_users():
    Base.metadata.create_all(bind=engine)  # safe no-op if tables already exist
    db = SessionLocal()
    try:
        for retired in RETIRED_DEMO_IDS:
            # Best effort: if the old account already has sessions/actions
            # attached (foreign keys), leave it in place rather than crash
            # startup -- it is harmless, just an unused login.
            try:
                stale = db.query(User).filter(User.x_user_id == retired).first()
                if stale is not None:
                    db.delete(stale)
                    db.commit()
            except Exception:
                db.rollback()
        existing = {u.x_user_id: u for u in db.query(User).all()}
        added = 0
        repaired = 0
        for entry in DEMO_USERS:
            plain_password = entry["password"]
            current = existing.get(entry["x_user_id"])
            if current is not None:
                # Self-heal: seeded hashes depend on SENTINEL_PASSWORD_PEPPER.
                # If the pepper was added/changed after the first boot, every
                # stored hash silently stops matching and ALL logins fail with
                # "Incorrect account or password" even though the credentials
                # are right. The demo passwords are published in
                # Demo-Credentials.md (source of truth), so re-hash any demo
                # account whose stored hash no longer verifies.
                if not verify_password(plain_password, current.password_hash):
                    current.password_hash = hash_password(plain_password)
                    repaired += 1
                # Keep the jurisdiction/name of an existing demo account in
                # sync with this file. Without this, changing an account's
                # constituency here would never reach a database that was
                # already seeded (e.g. Supabase), and it would keep pointing
                # at the old, empty jurisdiction.
                for field, value in entry.items():
                    if field in ("x_user_id", "password"):
                        continue
                    if getattr(current, field, None) != value:
                        setattr(current, field, value)
                continue
            row = {k: v for k, v in entry.items() if k != "password"}
            row["password_hash"] = hash_password(plain_password)
            db.add(User(**row))
            added += 1
        db.commit()
        if repaired:
            import logging
            logging.getLogger("sentinel.startup").warning(
                "seed_users: re-hashed %d demo account(s) whose stored hash did not "
                "match the current SENTINEL_PASSWORD_PEPPER", repaired
            )
        return added
    finally:
        db.close()


if __name__ == "__main__":
    added = seed_users()
    print(f"Seeded {added} new demo user(s) ({len(DEMO_USERS)} total defined).")
