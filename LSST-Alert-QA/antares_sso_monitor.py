"""
sso_monitor.py — daily scan for brightening SSO candidates in ANTARES.
Detects loci that have crossed below mag 15, or brightened significantly
since the last scan.

State is stored in sso_state.json between runs.
"""

import json
import pathlib
import datetime
from requests.exceptions import ConnectionError, Timeout
import time

from elasticsearch_dsl import Search
from antares_client.search import search

MAG_THRESHOLD = 15.0  # flag if brighter than this. adjust based on your interest
DELTA_MAG_ALERT = (
    1.0  # flag if brightened by this much since last scan. 1 mag = ~ 2.5x flux increase
)
MJD_J2000 = 51544.5  # MJD of J2000.0 epoch reference (2000-01-01 12:00 UTC)
STATE_FILE = pathlib.Path("bright_sso_state.json")
MAX_RETRIES = 3
RETRY_WAIT = 300  # 5 minutes


def _now_mjd() -> float:
    """Return the MJD of current time"""
    epoch = datetime.datetime(2000, 1, 1, 12, tzinfo=datetime.timezone.utc)
    now = datetime.datetime.now(datetime.timezone.utc)
    return MJD_J2000 + (now - epoch).total_seconds() / 86400  # 86400s = 1 day


def _load_state() -> dict:
    """
    Returns dict with keys:
      last_mjd:   MJD of last scan (default: 7 days ago)
      magnitudes: {locus_id: newest_alert_magnitude from last scan}
    """
    tmp = STATE_FILE.with_suffix(".tmp")
    if tmp.exists():
        tmp.unlink()  # clean up orphaned temp from previous crash

    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"last_mjd": _now_mjd() - 7.0, "magnitudes": {}}


def _save_state(state: dict) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.rename(STATE_FILE)


def scan():
    state = _load_state()
    since = state["last_mjd"]
    prev_mag = state["magnitudes"]
    now_mjd = _now_mjd()

    print(f"Scanning MJD {since:.1f} to {now_mjd:.1f}")
    print(f"Known loci from last scan: {len(prev_mag)}\n")

    # gets all solar system objects detected by rubin since last scan.
    # returns a lazy iterator
    query = (
        Search()
        .filter("term", tags="sso_candidates")
        .filter("range", **{"properties.newest_alert_observation_time": {"gte": since}})
        .to_dict()
    )

    new_magnitudes = {}
    alerts = []

    # retry guard against mid scan network failures.
    for attempt in range(MAX_RETRIES):
        try:
            for locus in search(query):
                p = locus.properties
                lid = locus.locus_id
                newest = p.get("newest_alert_magnitude")
                last = prev_mag.get(lid)

                if newest is None:
                    continue

                new_magnitudes[lid] = newest
                oldest = p.get("oldest_alert_observation_time") or 0

                # the locus is "new" and below 15
                new_bright = (
                    last is None and newest <= MAG_THRESHOLD and oldest >= since
                )

                # threshold crossing: wasn't below 15 last scan, is now
                crossed = (
                    last is not None
                    and last > MAG_THRESHOLD
                    and newest <= MAG_THRESHOLD
                )

                # rapid brightening: dropped by DELTA_MAG_ALERT since last scan
                brightened = last is not None and (last - newest) >= DELTA_MAG_ALERT

                if new_bright or crossed or brightened:
                    reason = []

                    if new_bright:
                        reason.append(
                            f"new locus already below mag {MAG_THRESHOLD}. VERIFY!"
                        )
                    if crossed:
                        reason.append(f"crossed mag {MAG_THRESHOLD}")
                    if brightened:
                        reason.append(
                            f"brightened {last - newest:.2f} mag since last scan"
                        )

                    alerts.append((locus, newest, last, ", ".join(reason)))

            break  # success, exit retry loop

        except (ConnectionError, Timeout) as e:
            if attempt < MAX_RETRIES - 1:
                print(
                    f"Network error (attempt {attempt + 1}/{MAX_RETRIES}): retrying in {RETRY_WAIT}s"
                )
                time.sleep(RETRY_WAIT)
            else:
                print(f"Network error after {MAX_RETRIES} attempts: {e}")
                print("State not updated – will retry next run.")
                return

    if alerts:
        # report to stdout of the results of current scan
        print(f"=== {len(alerts)} BRIGHTENING EVENTS ===\n")
        for locus, newest, last, reason in alerts:
            p = locus.properties
            print(
                f"{locus.locus_id}:\n"
                f"  mag_now={newest:.2f}"
                f"  mag_prev={last if last is not None else 'new'}\n"
                f"  ndet={p.get('num_mag_values')}\n"
                f"  ra={locus.ra:.4f}  dec={locus.dec:.4f}\n"
                f"  {reason}"
            )
    else:
        print("No brightening events detected.")

    # blank line between runs for readability
    print("\n")

    # maintain a cumulating magnitudes dict of all loci ever seen from ANTARES
    # for cross check
    merged = {**prev_mag, **new_magnitudes}
    _save_state({"last_mjd": now_mjd, "magnitudes": merged})


if __name__ == "__main__":
    scan()
