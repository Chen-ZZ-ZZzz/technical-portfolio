"""
sso_monitor.py — daily scan for brightening SSO candidates in ANTARES.
Detects loci that have crossed below mag 15, or brightened significantly
since the last scan.

State is stored in sso_state.json between runs.
"""

import json
import pathlib
import datetime

from elasticsearch_dsl import Search
from antares_client.search import search

MAG_THRESHOLD   = 15.0
DELTA_MAG_ALERT = 1.0   # flag if brightened by this much since last scan
STATE_FILE      = pathlib.Path("bright_sso_state.json")


def _now_mjd() -> float:
    epoch = datetime.datetime(2000, 1, 1, 12, tzinfo=datetime.timezone.utc)
    now   = datetime.datetime.now(datetime.timezone.utc)
    return 51544.5 + (now - epoch).total_seconds() / 86400


def _load_state() -> dict:
    """
    Returns dict with keys:
      last_mjd:   MJD of last scan (default: 7 days ago)
      magnitudes: {locus_id: newest_alert_magnitude from last scan}
    """
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"last_mjd": _now_mjd() - 7.0, "magnitudes": {}}


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def scan():
    state    = _load_state()
    since    = state["last_mjd"]
    prev_mag = state["magnitudes"]
    now_mjd  = _now_mjd()

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

    for locus in search(query):
        p      = locus.properties
        lid    = locus.locus_id
        newest = p.get("newest_alert_magnitude")
        last   = prev_mag.get(lid)

        if newest is None:
            continue

        new_magnitudes[lid] = newest
        oldest = p.get("oldest_alert_observation_time") or 0

        # the locus is "new" and below 15
        new_bright = last is None and newest <= MAG_THRESHOLD and oldest >= since

        # threshold crossing: wasn't below 15 last scan, is now
        crossed = last is not None and last > MAG_THRESHOLD and newest <= MAG_THRESHOLD

        # rapid brightening: dropped by DELTA_MAG_ALERT since last scan
        brightened = last is not None and (last - newest) >= DELTA_MAG_ALERT

        if new_bright or crossed or brightened:
            reason = []
            if new_bright:
                reason.append(f"new locus already below mag {MAG_THRESHOLD}. VERIFY!")
            if crossed:
                reason.append(f"crossed mag {MAG_THRESHOLD}")
            if brightened:
                reason.append(f"brightened {last - newest:.2f} mag since last scan")
            alerts.append((locus, newest, last, ", ".join(reason)))

    if alerts:
        # report to stdout of the results of current scan
        print(f"=== {len(alerts)} BRIGHTENING EVENTS ===\n")
        for locus, newest, last, reason in alerts:
            p = locus.properties
            print(
                f"{locus.locus_id}"
                f"  mag_now={newest:.2f}"
                f"  mag_prev={last if last is not None else 'new'}"
                f"  ndet={p.get('num_mag_values')}"
                f"  ra={locus.ra:.4f}  dec={locus.dec:.4f}"
                f"  → {reason}"
            )
    else:
        print("No brightening events detected.")

    _save_state({"last_mjd": now_mjd, "magnitudes": new_magnitudes})


if __name__ == "__main__":
    scan()
