"""
Phase 6 -- Weather-API delay cross-check (Open-Meteo, free tier).
MUST fail gracefully -- return a neutral/unknown score on API failure,
never crash the aggregator (see Rules.md).

Interface: score_weather_check(work: dict) -> float in [0, 1].
Deliberately takes only `work`, not `all_works` -- unlike the peer-comparison
modules (duplicate/money/delay/agency_network), this is a single-row
corroboration check against an external source, not a dataset-relative one.

DESIGN NOTE (not specified in the docs -- flagging this as an assumption,
per Rules.md's "ask instead of guessing" for anything not already in
PRD.md): PRD.md S4.5 describes this as confirming "claimed delay causes
(rain, floods)" against real weather, but the dataset has no free-text or
categorical claimed-reason field to cross-check -- v3/v4 only added
anomaly-type ground truth, not a reason string (DATASET-CHANGELOG.md).
Absent that field, this module treats every *completed* work with an
abnormal delay (the same "Completed" branch delay.py already flags, per
its own IQR-outlier logic) as carrying an implicit weather claim, and asks
Open-Meteo's historical archive whether heavy precipitation actually
occurred at that work's location during the expected-to-actual delay
window. No corroborating rain/flood weather found -> raises suspicion that
"weather" would be an unsubstantiated excuse for the delay. This is a
weaker, indirect signal than the photo-forensics checks (a real claimed
reason field would make it direct), which is why its score is capped
below 1.0 -- flag this design decision to the team before the pitch, since
it's a stand-in for data the pipeline doesn't have.
"""
import pandas as pd
import requests

WEATHER_API_URL = "https://archive-api.open-meteo.com/v1/archive"
REQUEST_TIMEOUT_SECONDS = 3
MAX_CONSECUTIVE_API_FAILURES = 3  # after this, disable the API for the rest of the run (fail gracefully, per Rules.md)
_consecutive_failures = 0
_api_disabled = False  # set True after MAX_CONSECUTIVE_API_FAILURES, short

# A day this wet plausibly disrupts outdoor construction/civil work -- used
# only as a "was there any real corroborating rain event" gate, not a
# calibrated agronomic/engineering threshold (Rules.md: don't invent
# domain-specific figures beyond what's needed for a directional signal).
HEAVY_RAIN_MM_PER_DAY = 20.0

MIN_DELAY_DAYS_TO_CHECK = 30   # below this, "was it the weather" isn't worth asking
MAX_WINDOW_DAYS = 120          # cap the query window so one wildly-delayed row can't blow up the API call
NO_RAIN_FOUND_SCORE = 0.6      # capped below 1.0 -- see DESIGN NOTE: indirect signal, not a real claim to disprove


def _fetch_max_daily_precipitation(lat, lon, start_date: str, end_date: str):
    """Returns the max daily precipitation (mm) seen in the window, or None
    if the call fails/times out or returns no usable data -- callers must
    treat None as "unknown," not "no rain" (Rules.md: fail gracefully, log
    rather than silently drop)."""
    global _consecutive_failures, _api_disabled
    
    if _api_disabled:
        return None  # short-circuit if the API has been disabled after repeated failures
    try:
        resp = requests.get(
            WEATHER_API_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "start_date": start_date,
                "end_date": end_date,
                "daily": "precipitation_sum",
                "timezone": "auto",
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        daily = resp.json().get("daily", {})
        values = [v for v in daily.get("precipitation_sum", []) if v is not None]
        if not values:
            return None
        return max(values)
    except Exception as exc:
        # Log and fail neutral -- never let a flaky external API take down
        # the aggregator (Rules.md, PRD.md S4.5).
            _consecutive_failures += 1
            if _consecutive_failures >= MAX_CONSECUTIVE_API_FAILURES:
                _api_disabled = True
                print(f"[weather_check] API unreachable after {MAX_CONSECUTIVE_API_FAILURES} consecutive failures -- skipping weather checks for the rest of this run.")
            print(f"[weather_check] API call failed for ({lat}, {lon}, {start_date}..{end_date}): {exc}")
            return None

def score_weather_check(work: dict) -> float:
    if work.get("status") != "Completed":
        return 0.0  # no actual_completion_date to bound a delay window against (see DESIGN NOTE)

    lat, lon = work.get("latitude"), work.get("longitude")
    expected, actual = work.get("expected_completion_date"), work.get("actual_completion_date")
    if any(v is None or pd.isna(v) for v in (lat, lon, expected, actual)):
        return 0.0

    expected_dt, actual_dt = pd.to_datetime(expected), pd.to_datetime(actual)
    delay_days = (actual_dt - expected_dt).days
    if delay_days < MIN_DELAY_DAYS_TO_CHECK:
        return 0.0

    window_end = expected_dt + pd.Timedelta(days=min(delay_days, MAX_WINDOW_DAYS))
    max_precip = _fetch_max_daily_precipitation(
        lat, lon,
        expected_dt.strftime("%Y-%m-%d"),
        window_end.strftime("%Y-%m-%d"),
    )

    if max_precip is None:
        return 0.0  # unknown -- neutral, per Rules.md, not a flag against the work

    if max_precip >= HEAVY_RAIN_MM_PER_DAY:
        return 0.0  # real adverse weather did occur -- corroborates a weather-related delay

    return NO_RAIN_FOUND_SCORE
