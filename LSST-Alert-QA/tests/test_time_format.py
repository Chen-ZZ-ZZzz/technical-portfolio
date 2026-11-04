"""The one formatter for a newest detection's time (config.utc_and_age), and the MJD conversion beside now_mjd."""

import datetime

import pytest

from rubin_qa.config import AGE_IN_DAYS_FROM_HOURS, mjd_to_datetime, utc_and_age

MJD = 61347.5572  # 2026-11-03 13:22:22 UTC
HOUR = 1 / 24


def test_mjd_to_datetime_is_utc_and_exact_at_the_j2000_epoch():
    assert mjd_to_datetime(51544.5) == datetime.datetime(2000, 1, 1, 12, tzinfo=datetime.timezone.utc)
    assert mjd_to_datetime(MJD).isoformat(timespec="seconds") == "2026-11-03T13:22:22+00:00"


def test_the_time_is_utc_to_the_minute_and_the_age_in_hours():
    assert utc_and_age(MJD, MJD + 17.46 * HOUR) == "2026-11-03 13:22 UTC (17.5 h)"


@pytest.mark.parametrize("hours,age", [
    (47.96, "48.0 h"),  # under the switch: hours, even where it rounds to 48.0
    (48.0, "2.0 d"),    # at the switch: days
    (48.01, "2.0 d"),
    (237.4, "9.9 d"),
])
def test_the_switch_to_days_is_decided_on_the_unrounded_age(hours, age):
    assert AGE_IN_DAYS_FROM_HOURS == 48.0
    assert utc_and_age(MJD, MJD + hours * HOUR).endswith(f"({age})")


def test_a_detection_after_the_reference_shows_a_negative_age_never_clamped():
    """An alert that arrived after the run started: the age says so."""
    assert utc_and_age(MJD, MJD - 0.3 * HOUR).endswith("(-0.3 h)")
