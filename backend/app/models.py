# SQLAlchemy models -- Phase 3 added User. Phase 4 adds Flag (current
# escalation state, one row per raised flag) and Review (the append-only
# audit log of every action taken on a flag). Postgres holds only mutable
# data (users, reviews, flags) -- works themselves stay in Pandas,
# read-only (Architecture.md S1).
from datetime import datetime

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Float, Boolean
from app.database import Base


class User(Base):
    """
    Phase 3 -- one row per demo account (auth/seed_users.py seeds five, one
    per role). Identity is resolved via the X-User-Id header matching
    x_user_id here (auth/identity.py) -- hackathon-simple, documented as a
    pre-production placeholder (Rules.md).

    Jurisdiction columns are nullable because which ones apply depends on
    role -- auth/permissions.py's _work_matches_jurisdiction() is the single
    place that knows which column(s) each role checks:
      - Implementing Agency  -> executing_agency + district (same
                                 (agency, district) unit key agency_network.py
                                 uses -- see Build-Log.md)
      - Local Authority      -> district + loc (COMPOUND key -- loc values
                                 like "Block 7" repeat across 36+ districts,
                                 Rules.md)
      - District Authority   -> district
      - Nodal State Authority-> state
      - Member of Parliament -> constituency
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    x_user_id = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    role = Column(String, nullable=False, index=True)

    # Phase 8 bug fix (Implementation-Guide.md Phase 8 item 6 / Build-Log.md
    # #19 finding 2): dashboards were reachable with no login at all because
    # X-User-Id was trusted as a bare, unauthenticated claim. A real login
    # now has to check *something* server-side, so every seeded account
    # gets a password. Hashing is auth/security.py's hash_password() --
    # documented pre-production placeholder (salted SHA-256, not
    # bcrypt/argon2 -- no new dependency added without asking, Rules.md),
    # same "honest placeholder" pattern already used for the X-User-Id
    # scheme and the Phase 11 mock OTP.
    password_hash = Column(String, nullable=True)

    state = Column(String, nullable=True)
    district = Column(String, nullable=True)
    loc = Column(String, nullable=True)
    executing_agency = Column(String, nullable=True)
    constituency = Column(String, nullable=True)

    def __repr__(self):
        return f"<User x_user_id={self.x_user_id!r} role={self.role!r}>"

# class Review(Base): ...   # Phase 4
# class Flag(Base): ...     # Phase 4


class Flag(Base):
    """
    Phase 4 -- one row per raised flag, holding its CURRENT escalation
    stage as a denormalized convenience column for fast queries (e.g.
    "give me every flag currently sitting with Local Authority"). The
    actual source of truth for what happened to a flag is the append-only
    Review log below -- `status` only ever changes as a side effect of
    inserting a new Review row (escalation/state_machine.py), never edited
    on its own. This is not the same thing as "overwriting history"
    (Rules.md forbids that): the history itself -- every Review row -- is
    never touched once written; `status` is just a cache of "what does the
    latest Review say," not a competing copy of the history.

    work_id references the Pandas dataset's work_id column directly --
    there's no SQL foreign key to it since works live outside Postgres
    entirely (Architecture.md S1).
    """
    __tablename__ = "flags"

    id = Column(Integer, primary_key=True, index=True)
    work_id = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False, index=True)  # see escalation/state_machine.py STAGE_* constants
    tier = Column(String, nullable=False)                # risk_aggregator.py tier at creation time
    shape = Column(String, nullable=True)
    driving_signal = Column(String, nullable=True)
    stage_deadline = Column(DateTime, nullable=False)     # when the CURRENT stage auto-escalates if untouched
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    resolved_at = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<Flag work_id={self.work_id!r} status={self.status!r}>"


class Review(Base):
    """
    Phase 4 -- the append-only audit trail. Every action on a flag --
    the AI raising it, a role commenting/clearing/escalating, or the
    scheduler auto-escalating it -- is a new row here, never an update or
    delete (Rules.md: "clearing a flag is an insert ... never a delete or
    overwrite of history"). actor_user_id is null for system-generated
    rows (the initial "flagged"/"routed" pair, and "auto_escalate").
    """
    __tablename__ = "reviews"

    id = Column(Integer, primary_key=True, index=True)
    flag_id = Column(Integer, ForeignKey("flags.id"), nullable=False, index=True)
    work_id = Column(String, nullable=False, index=True)  # denormalized, avoids a join for by-work queries
    actor_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    actor_role = Column(String, nullable=False)            # role at the time of action, or "system"
    action = Column(String, nullable=False)                # "flagged" | "routed" | "comment" | "clear" | "escalate" | "auto_escalate"
    from_status = Column(String, nullable=True)
    to_status = Column(String, nullable=True)
    reason = Column(String, nullable=True)                 # justification text -- feeds escalation/reviewer_diligence.py
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Phase 9 (Implementation-Guide.md Phase 9 item 4 / PRD.md S5.4's
    # alert-routing note) -- who this particular system row is addressed
    # to. Only ever set on system-authored "routed" rows; null everywhere
    # else (a role's own comment/clear/escalate isn't "addressed" to
    # anyone, it's authored by them). Added because create_flag() now
    # writes TWO routing rows the instant a flag is raised (Local
    # Authority -- accountable for first review -- AND District Authority,
    # copied in for early visibility) instead of the single sequential
    # relay that existed through Phase 8, where District only learned of a
    # flag once/if it escalated to them. This is purely an additional
    # notification fact recorded in the audit trail -- it does NOT change
    # Flag.status or who is accountable to act (escalation/state_machine.py's
    # ROLE_FOR_STAGE mapping is unchanged; District still can't clear/
    # escalate until the flag genuinely reaches escalated_district).
    recipient_role = Column(String, nullable=True)

    def __repr__(self):
        return f"<Review work_id={self.work_id!r} action={self.action!r} by={self.actor_role!r}>"


class Session(Base):
    """
    Phase 8 bug fix (Implementation-Guide.md Phase 8 item 6) -- a real,
    server-side session, created ONLY by a successful POST /auth/login
    (routers/auth.py) and checked on every request by
    auth/identity.py:get_current_user(). This is what closes the actual
    bypass Build-Log.md #19 finding 2 described: previously any client
    could set X-User-Id to any seeded account with no credential check at
    all. A bare X-User-Id claim is no longer trusted by itself -- see
    auth/identity.py.

    Same append-only-ish, single-purpose-table pattern as the rest of this
    schema; unlike Flag/Review this one IS deleted on logout/expiry, since
    a session is not an audit record (Rules.md's "never delete" rule is
    about flags/clearances/collusion alerts, not ephemeral login state).
    """
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, index=True)
    token = Column(String, unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)

    def __repr__(self):
        return f"<Session user_id={self.user_id!r} expires_at={self.expires_at!r}>"


class AnalysisResult(Base):
    """
    Phase 8 bug fix (PRD.md S7 item 2 / Rules.md's "any 'no flag yet' UI
    state must be distinguishable from 'confirmed clean'"). A cache of the
    LATEST detection pass on a work -- one row per work_id, written every
    time routers/flags.py actually scores a work, regardless of whether
    the resulting tier crosses the Medium+ flagging bar.

    This gives the frontend three real, honest states instead of two:
      - no row here at all      -> "pending analysis" (never scored)
      - row here, tier Normal/Low, no open Flag -> "clear" (scored,
        genuinely below the flagging bar)
      - an open Flag exists      -> "flagged" (rendered from Flag, richer)

    Same "single mutable cache row" pattern Flag.status already uses for
    escalation stage (see Flag's docstring above) -- this is not a
    competing audit log. Flag + Review stay the untouched audit trail.
    """
    __tablename__ = "analysis_results"

    id = Column(Integer, primary_key=True, index=True)
    work_id = Column(String, unique=True, index=True, nullable=False)
    tier = Column(String, nullable=False)          # risk_aggregator.py tier at last scoring pass
    shape = Column(String, nullable=True)
    driving_signal = Column(String, nullable=True)
    # Phase 14 (Evidence Layer) -- JSON-encoded dict of per-module evidence
    # (money/delay/duplicate/photo_forensics' underlying numbers/dates),
    # written by scoring/pipeline.py's persist_analysis_result(). Same
    # "portable string column" pattern CollusionAlert.related_work_ids
    # already uses, not a new Postgres JSON/ARRAY type. Nullable so rows
    # written before this phase (if any survive a migration) don't break.
    evidence = Column(String, nullable=True)
    scored_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<AnalysisResult work_id={self.work_id!r} tier={self.tier!r}>"


class CollusionAlert(Base):
    """
    Phase 9 -- Authority-Agency Collusion Detection (PRD.md S4.9,
    Implementation-Guide.md Phase 9 item 2). One row per detected instance
    of a specific authority account repeatedly clearing the same recurring
    anomaly on the same agency category without it ever genuinely
    resolving (modules/collusion.py). Append-only, like Flag/Review
    (Rules.md: never delete a flag/clearance/collusion alert) -- a
    clearance that keeps repeating past the point an alert already exists
    for this (authority, agency) pair produces a NEW row with an updated
    override_count, never an edit of the earlier one.

    agency_id holds the executing_agency CATEGORY string, not a distinct
    company identity -- the dataset only has 7 generic agency-type
    categories (PRD.md S4.2's caveat, already documented the same way in
    modules/agency_network.py). related_work_ids is a JSON-encoded list of
    work_id strings (kept as a single portable String column rather than a
    Postgres ARRAY type, so this stays testable against the SQLite
    stand-in the team has used elsewhere -- see Build-Log.md #15/#17).
    """
    __tablename__ = "collusion_alerts"

    id = Column(Integer, primary_key=True, index=True)
    authority_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    agency_id = Column(String, nullable=False, index=True)
    reason = Column(String, nullable=False)
    related_work_ids = Column(String, nullable=False)  # JSON-encoded list of work_id strings
    # Additive beyond the guide's literal 4-field list -- lets a repeat
    # detection on the same (authority, agency) pair skip inserting a
    # duplicate alert with no new evidence, without ever updating/deleting
    # the earlier row (see modules/collusion.py's check_and_record()).
    override_count = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return (
            f"<CollusionAlert authority_id={self.authority_id!r} "
            f"agency_id={self.agency_id!r} override_count={self.override_count!r}>"
        )


class Project(Base):
    """
    Phase 12 item 1 -- a mutable, Postgres-backed counterpart to the
    read-only synthetic CSV's rows, for a REAL project that gets created
    and progressed through the app rather than pre-seeded ground truth.
    Column names deliberately mirror the CSV's own columns 1:1 (only the
    subset the 8 scoring modules and jurisdiction filters need) so
    data_loader.py's get_all_works() can pd.concat() this table's rows
    straight into the same DataFrame shape every router/scoring module
    already reads -- see data_loader.py's docstring.

    `data_type="USER_SUBMITTED"` distinguishes these from the CSV's
    `REAL_BASED_SYNTHETIC` rows if that's ever needed (e.g. a "Live"
    badge in a dashboard) -- purely additive, no existing code reads it
    yet.

    `latest_photo_*` is a denormalized cache of the most recently
    submitted stage's primary photo -- same "single mutable cache row"
    pattern Flag.status/AnalysisResult already use elsewhere in this file
    -- so get_all_works() can populate the CSV's photo_* columns for this
    row without an extra join on every call. Updated by
    routers/projects.py's stage-submission handler whenever a new photo
    is accepted.
    """
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    work_id = Column(String, unique=True, index=True, nullable=False)

    mp_name = Column(String, nullable=True)
    state = Column(String, nullable=True)
    constituency = Column(String, nullable=True)
    nodal_district = Column(String, nullable=True)
    implementing_district = Column(String, nullable=True)
    work_category = Column(String, nullable=True)
    work_description = Column(String, nullable=True)
    executing_agency = Column(String, nullable=True)
    village_or_locality = Column(String, nullable=True)

    estimated_cost = Column(Float, nullable=True)
    sanctioned_amount = Column(Float, nullable=True)
    expenditure = Column(Float, nullable=False, default=0.0)

    recommendation_date = Column(DateTime, nullable=True)
    sanction_date = Column(DateTime, nullable=True)
    expected_completion_date = Column(DateTime, nullable=True)
    actual_completion_date = Column(DateTime, nullable=True)

    physical_progress_pct = Column(Float, nullable=False, default=0.0)
    # Recommended -> Sanctioned -> In Progress -> Completed -- same values
    # the CSV's own `status` column already uses (Implementation-Guide.md
    # Phase 12 item 1).
    status = Column(String, nullable=False, default="Recommended")

    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    data_type = Column(String, nullable=False, default="USER_SUBMITTED")

    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    latest_photo_available = Column(Boolean, nullable=False, default=False)
    latest_photo_gps_lat = Column(Float, nullable=True)
    latest_photo_gps_lon = Column(Float, nullable=True)
    latest_photo_captured_at = Column(DateTime, nullable=True)
    latest_photo_phash = Column(String, nullable=True)

    def __repr__(self):
        return f"<Project work_id={self.work_id!r} status={self.status!r}>"


class StageSubmission(Base):
    """
    Phase 12 item 1 -- one row per stage report an Implementing Agency
    submits against a `Project`. `task_deadline` is manually typed by the
    agency (item 4 -- same trust level as every other self-reported field
    the AI pipeline checks, not ground truth); it is also exactly what
    escalation/deadline_scheduler.py checks for silent-delay auto-flagging
    (item 6): the most recent stage's `task_deadline` passing with no
    newer `StageSubmission` means "next report due" has passed.

    `accepted`: null while pending Local Authority review; flipped to
    True/False as a side effect of a "clear"/"send_back" review action on
    the Flag this stage's scoring pass raised (routers/reviews.py) -- see
    escalation/state_machine.py's STAGE_SENT_BACK for the "send back for
    correction" path (item 7).
    """
    __tablename__ = "stage_submissions"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)

    stage_label = Column(String, nullable=False)
    task_deadline = Column(DateTime, nullable=False)
    submitted_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    physical_progress_pct = Column(Float, nullable=False)
    expenditure_this_stage = Column(Float, nullable=False)

    ai_tier = Column(String, nullable=True)          # risk_aggregator.py tier at submission time
    ai_driving_signal = Column(String, nullable=True)
    accepted = Column(Boolean, nullable=True)  # null=pending, True=cleared, False=sent back

    submitted_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    def __repr__(self):
        return f"<StageSubmission project_id={self.project_id!r} stage_label={self.stage_label!r}>"


class ProjectPhoto(Base):
    """
    Phase 12 item 1/5 -- one row per photo saved against a stage
    submission (only photos that passed the pre-save reject checks in
    modules/live_photo_check.py ever reach this table -- a rejected
    upload is never persisted at all). gps_lat/gps_lon/captured_at/phash
    are extracted once at upload time and never recomputed, so the full
    8-signal pipeline (photo_forensics.py, unchanged) and this table's
    own future reject checks (against "this project's OWN prior photos")
    always see the same values.
    """
    __tablename__ = "project_photos"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    stage_submission_id = Column(Integer, ForeignKey("stage_submissions.id"), nullable=False, index=True)

    file_path = Column(String, nullable=False)
    original_filename = Column(String, nullable=True)
    gps_lat = Column(Float, nullable=True)
    gps_lon = Column(Float, nullable=True)
    captured_at = Column(DateTime, nullable=True)
    phash = Column(String, nullable=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<ProjectPhoto project_id={self.project_id!r} file_path={self.file_path!r}>"


class ProjectDocument(Base):
    """
    Phase 12 item 1 -- non-photo attachments (bills, measurement sheets,
    etc.) against a stage submission. Per the guide's item 8 (explicitly
    deferred), these stay plain attachments for human reviewers -- only
    ProjectPhoto rows ever run through photo_forensics.py/
    live_photo_check.py.
    """
    __tablename__ = "project_documents"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    stage_submission_id = Column(Integer, ForeignKey("stage_submissions.id"), nullable=False, index=True)

    file_path = Column(String, nullable=False)
    original_filename = Column(String, nullable=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<ProjectDocument project_id={self.project_id!r} file_path={self.file_path!r}>"
