"""
Phase 9 item 1 (Implementation-Guide.md): "Validate the override-frequency
threshold against the dataset/demo-walkthrough data before hardcoding it."

This sandbox has no outbound network access and no FastAPI/SQLAlchemy/
Postgres available to actually boot the API (same constraint noted in
Build-Log.md #17 for the SBERT re-validation) -- so this can't be a real
end-to-end run against a live server the way scripts/validate_phase2.py
was. What it CAN do, and does: exercise the exact streak-counting logic
modules/collusion.py's check_and_record() uses (_current_streak(), copied
here rather than imported since importing app.modules.collusion pulls in
app.models -> SQLAlchemy, unavailable in this sandbox) against synthetic
clearance histories, to confirm OVERRIDE_THRESHOLD=3 behaves as intended
before anyone treats it as locked. Re-run the real thing (this exact
scenario, via the actual API) once a machine with Postgres/FastAPI
installed is available -- flagging that gap rather than silently treating
this standalone check as sufficient (Rules.md).
"""


class FakeFlag:
    def __init__(self, work_id, driving_signal):
        self.work_id = work_id
        self.driving_signal = driving_signal


def _current_streak(history):
    """Mirrors modules/collusion.py's _current_streak() exactly."""
    if not history:
        return 0
    streak = 1
    current_signal = history[-1][1].driving_signal
    for _review, flag in reversed(history[:-1]):
        if flag.driving_signal == current_signal:
            streak += 1
        else:
            break
    return streak


OVERRIDE_THRESHOLD = 3


def _history(*signals):
    """Builds a fake (review, flag) history from a list of driving_signal strings."""
    return [(None, FakeFlag(f"WORK-{i}", s)) for i, s in enumerate(signals)]


CASES = [
    # (description, signals in order, expected streak, expected to alert)
    ("single clearance, no pattern yet", ["delay"], 1, False),
    ("two in a row -- below threshold", ["delay", "delay"], 2, False),
    ("exactly 3 in a row -- exit check's own scenario", ["delay", "delay", "delay"], 3, True),
    ("4 in a row -- still fires, streak keeps growing", ["delay", "delay", "delay", "delay"], 4, True),
    ("different signal breaks the streak", ["delay", "delay", "money"], 1, False),
    ("streak restarts after a break, then re-crosses threshold",
     ["delay", "delay", "money", "delay", "delay", "delay"], 3, True),
    ("3 in a row but on 'money' instead of 'delay' -- signal-agnostic, still fires",
     ["money", "money", "money"], 3, True),
]


def main():
    failures = 0
    for description, signals, expected_streak, expected_alert in CASES:
        history = _history(*signals)
        streak = _current_streak(history)
        would_alert = streak >= OVERRIDE_THRESHOLD
        ok = streak == expected_streak and would_alert == expected_alert
        status = "PASS" if ok else "FAIL"
        if not ok:
            failures += 1
        print(f"[{status}] {description}: streak={streak} (expected {expected_streak}), "
              f"alert={would_alert} (expected {expected_alert})")

    print()
    if failures:
        print(f"{failures}/{len(CASES)} case(s) FAILED.")
        raise SystemExit(1)
    print(f"All {len(CASES)} cases passed. OVERRIDE_THRESHOLD={OVERRIDE_THRESHOLD} "
          f"behaves as intended for these synthetic scenarios -- still needs a real, "
          f"live-dataset walkthrough (see module docstring) before being treated as final.")


if __name__ == "__main__":
    main()
