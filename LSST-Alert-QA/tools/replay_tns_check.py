"""
TNS truth check of a replay's alerts, per survey.

The two surveys are read against different expectations. Bright ZTF transients are
reported to TNS routinely, so ZTF objects bracketed on a host should match at a high
rate; a low rate there means a problem. Most faint Rubin transients are never reported,
so LSST risers should match far less. (Footprint D, spring 2026, defied that: brokers reported
COSMOS Rubin transients systematically, so the reporting group is kept with every match.)

Lookups go through ALeRCE's TNS service (host_match_check.tns_lookup). The service
answers empty once its quota of 10 a minute is spent, so an empty answer means "not in
TNS" only when a known TNS object, asked right after it in the same window, came back
full. What stays unresolved is "unavailable", and the run exits 1.
An answer counts as a match only within MATCH_ARCSEC of the alert's position; a
farther record is listed with its separation, not matched.

    uv run tools/replay_tns_check.py logs/replay_D_2026-03-12_2026-05-15.json
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import host_match_check as h  # noqa: E402

from rubin_qa.config import ERROR_PREFIX, PROJECT_ROOT, from_root  # noqa: E402

CANDIDATE_CACHE = PROJECT_ROOT / "cache" / "replay" / "candidates"
MATCH_ARCSEC = 2.0
DEFAULT_REPLAY = PROJECT_ROOT / "logs" / "replay_D_2026-03-12_2026-05-15.json"


def positions(footprint: str) -> dict[str, tuple[float, float]]:
    """Survey-object key -> (ra, dec), from the replay's cached candidate lists."""
    out = {}
    for path in sorted(CANDIDATE_CACHE.glob(f"{footprint}_*.json")):
        for r in json.loads(path.read_text()):
            out[f"{r['survey']}:{r['oid']}"] = (r["ra"], r["dec"])
    return out


def judge(alert: dict, pos: tuple[float, float], rec: dict | None) -> dict:
    """One alert's TNS outcome: matched / nearby_only / not_in_tns / unavailable, with the record's details."""
    out = {"key": alert["key"], "survey": alert["key"].split(":")[0], "category": alert["category"],
           "path": " ".join(alert["conditions"]), "crossmatch": alert["crossmatch"], "day": alert["day"]}
    if rec is None:
        return {**out, "tns": "unavailable"}
    if not rec:
        return {**out, "tns": "not_in_tns"}
    sep = h.sep_arcsec(pos[0], pos[1], rec["radeg"], rec["decdeg"])
    oid = alert["key"].split(":", 1)[1]
    names = [n.strip() for n in (rec.get("internal_names") or "").split(",")]
    out |= {"tns_name": f"{rec.get('name_prefix', '')} {rec['objname']}".strip(), "sep": round(sep, 2),
            "tns_type": (rec.get("object_type") or {}).get("name"),
            "discovered": (rec.get("discoverydate") or "")[:10],
            # ZTF lists the bare ID; Rubin's reports carry it as LSST-AP-DO-<diaObjectId>
            "own_id_listed": any(n == oid or n.endswith(f"-{oid}") for n in names),
            "reported_by": (rec.get("reporting_group") or {}).get("group_name"),
            "discovery_source": (rec.get("discovery_data_source") or {}).get("group_name"),
            "internal_names": rec.get("internal_names")}
    return {**out, "tns": "matched" if sep <= MATCH_ARCSEC else "nearby_only"}


def run(replay: pathlib.Path) -> int:
    data = json.loads(replay.read_text())
    where = positions(data["footprint"])
    alerts, seen = [], set()
    for a in data["alerts"]:  # one lookup per object; its first alert is the one reported
        if a["key"] not in seen:
            seen.add(a["key"])
            alerts.append(a)
    missing = [a["key"] for a in alerts if a["key"] not in where]
    if missing:
        print(f"{ERROR_PREFIX}no cached position for {', '.join(missing)}", file=sys.stderr)
        return 1
    print(f"{replay.name}: {len(alerts)} objects", flush=True)
    rows = [judge(a, where[a["key"]], r) for a, r in zip(alerts, h.tns_batch([where[a["key"]] for a in alerts]))]

    print(f"\nmatch: a TNS record within {MATCH_ARCSEC:g}\" of the alert position")
    groups = collections.defaultdict(list)
    for r in rows:
        groups[(r["survey"], f"{r['category']} · {r['path']}")].append(r)
    for (survey, category), g in sorted(groups.items()):
        n = collections.Counter(r["tns"] for r in g)
        asked = len(g) - n["unavailable"]
        rate = f"{n['matched'] / asked:.0%}" if asked else "-"
        print(f"  {survey:4s} {category:30s} {len(g):3d} objects: matched {n['matched']:2d} ({rate} of {asked} answered)"
              + (f", nearby only {n['nearby_only']}" if n["nearby_only"] else "")
              + (f", UNAVAILABLE {n['unavailable']}" if n["unavailable"] else ""))
    print()
    for r in rows:
        if r["tns"] in ("matched", "nearby_only"):
            own = "  own ID listed" if r["own_id_listed"] else ""
            print(f"  {r['key']:28s} {r['category']:12s} {r['tns']:11s} {r['tns_name']:12s} {r['tns_type'] or '-':18.18s} "
                  f"sep={r['sep']:.1f}\" discovered {r['discovered']} from {r['discovery_source'] or '-'} data, "
                  f"reported by {r['reported_by'] or '-'} (alert {r['day']}){own}")

    out = PROJECT_ROOT / "logs" / f"replay_tns_{replay.stem.removeprefix('replay_')}.json"
    out.write_text(json.dumps({"replay": replay.name, "match_arcsec": MATCH_ARCSEC,
                               "rows": rows}, indent=1))
    print(f"\nwritten: {out.relative_to(PROJECT_ROOT)}")
    return 0 if all(r["tns"] != "unavailable" for r in rows) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("replay", nargs="?", type=from_root, default=DEFAULT_REPLAY,
                        help="a replay result written by transient_monitor --replay")
    return run(parser.parse_args().replay)


if __name__ == "__main__":
    sys.exit(main())
