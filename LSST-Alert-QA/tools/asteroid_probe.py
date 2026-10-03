"""
Asteroid contamination probe for the transient monitor.

Question: do the monitor's first cuts (new locus, >= MIN_DETECTIONS detections,
|b| >= MIN_ABS_GAL_LAT) already keep asteroids out, and when they do not, what
does an asteroid locus look like?

Collect walks new loci from ANTARES, all sky: oldest alert within the look-back,
>= MIN_DETECTIONS detections. That is the population the 2026-09-27 evidence
describes, and the monitor's own query at the time; the monitor has since moved
to active loci (LSST alerts join old ZTF loci), so the probe keeps its own query.
For each high-|b| locus it fetches the alerts and attributes every detection
(ant_mag present; upper limits ignored) to a known asteroid or not, using that
alert's own ztf_ssnamenr, never the locus-level one being tested. One CSV row
per locus.

Report reduces a saved CSV, offline, to the numbers behind MIN_SPAN_DAYS and
is_solar_system() (here; the monitor's asteroid rule).

Usage:
    uv run tools/asteroid_probe.py                   # ~250 loci -> reports/asteroid_probe_YYYYMMDD.csv
    uv run tools/asteroid_probe.py --limit 100 --out /tmp/probe.csv
    uv run tools/asteroid_probe.py --report reports/asteroid_probe_20260927.csv

Evidence: reports/asteroid_probe_20260927.csv is the run the monitor's asteroid
rules cite. It was collected by this probe's first draft, before it was a tool,
and converted to this format: same attribution rule, but gal_b rounded to 0.1 deg
and span_d to 0.001 d, and one locus the listing returned twice dropped.

Cost: one alerts request per high-|b| locus, ~1.5 min for 250 loci.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from elasticsearch.dsl import Search  # noqa: E402

from rubin_qa.transient_monitor import (  # noqa: E402
    LOOKBACK_DAYS,
    MIN_ABS_GAL_LAT,
    MIN_DETECTIONS,
    MIN_SPAN_DAYS,
    galactic_latitude,
    ztf_id_is_old,
)
from rubin_qa.config import ERROR_PREFIX, REPORTS_DIR, from_root, now_mjd  # noqa: E402


from rubin_qa.antares_api import antares_errors, is_solar_system, locus_survey_ids  # noqa: E402,F401


def preexisting_ztf_ids(props: dict, since_mjd: float) -> list[str]:
    """ZTF IDs on an ANTARES locus first seen before the window."""
    return [oid for oid in locus_survey_ids(props)["ztf"] if ztf_id_is_old(oid, since_mjd)]


DEFAULT_LIMIT = 250  # loci pulled from the listing, before the |b| cut
SSO_TAGS = ("sso_candidates", "sso_confirmed")  # recorded to test whether they help
LIST_SEP = ";"
COLUMNS = [
    "locus_id", "gal_b", "locus_ssnamenr", "sso_tag", "preexisting_ztf",
    "n_det", "n_ss_det", "ss_names", "span_d", "catalogs", "collected_mjd",
]


def probe_locus(locus, since_mjd: float, collected_mjd: float) -> dict | None:
    """One CSV row for a locus, or None below the |b| cut. Fetches locus.alerts."""
    gal_b = galactic_latitude(locus.ra, locus.dec)
    if abs(gal_b) < MIN_ABS_GAL_LAT:
        return None
    props = locus.properties
    dets = [a for a in locus.alerts if a.properties.get("ant_mag") is not None]
    ss_dets = [a for a in dets if is_solar_system(a.properties)]
    mjds = [a.mjd for a in dets]
    return {
        "locus_id": locus.locus_id,
        "gal_b": round(gal_b, 2),
        "locus_ssnamenr": props.get("ztf_ssnamenr") if is_solar_system(props) else "",
        "sso_tag": any(t in SSO_TAGS for t in (locus.tags or [])),
        "preexisting_ztf": bool(preexisting_ztf_ids(props, since_mjd)),
        "n_det": len(dets),
        "n_ss_det": len(ss_dets),
        "ss_names": LIST_SEP.join(sorted({str(a.properties["ztf_ssnamenr"]) for a in ss_dets})),
        "span_d": round(max(mjds) - min(mjds), 4) if mjds else None,
        "catalogs": LIST_SEP.join(sorted(locus.catalogs or [])),
        "collected_mjd": round(collected_mjd, 4),
    }


def new_loci_query(since_mjd: float, min_detections: int) -> dict:
    """All sky, loci whose oldest alert is since since_mjd: the probed population."""
    return (
        Search()
        .filter("range", **{"properties.oldest_alert_observation_time": {"gte": since_mjd}})
        .filter("range", **{"properties.num_mag_values": {"gte": min_detections}})
        .to_dict()
    )


def collect(limit: int, lookback_days: float, min_detections: int) -> list[dict]:
    """Walk new loci all sky; one row per unique high-|b| locus."""
    from antares_client.search import search

    now = now_mjd()
    since = now - lookback_days
    rows, seen = [], set()
    for locus in itertools.islice(search(new_loci_query(since, min_detections)), limit):
        # the listing can return a locus twice in one walk
        if locus.locus_id in seen:
            continue
        seen.add(locus.locus_id)
        row = probe_locus(locus, since, now)
        if row is not None:
            rows.append(row)
    return rows


def write_csv(rows: list[dict], path: pathlib.Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def load_csv(path: pathlib.Path) -> list[dict]:
    """Read a probe CSV back with its types restored."""
    rows = []
    with path.open(newline="") as f:
        for r in csv.DictReader(f):
            rows.append({
                **r,
                "gal_b": float(r["gal_b"]),
                "sso_tag": r["sso_tag"] == "True",
                "preexisting_ztf": r["preexisting_ztf"] == "True",
                "n_det": int(r["n_det"]),
                "n_ss_det": int(r["n_ss_det"]),
                "span_d": float(r["span_d"]) if r["span_d"] else None,
                "collected_mjd": float(r["collected_mjd"]),
            })
    return rows


def summarize(rows: list[dict], min_span_days: float = MIN_SPAN_DAYS) -> dict:
    """The numbers the monitor's asteroid rules are argued from."""
    unique = list({r["locus_id"]: r for r in rows}.values())
    named = [r for r in unique if r["locus_ssnamenr"]]
    pure = [r for r in named if r["n_ss_det"] == r["n_det"]]
    mixed = [r for r in named if r["n_ss_det"] < r["n_det"]]
    pure_one_night = [r for r in pure if r["span_d"] is not None and r["span_d"] < min_span_days]
    unnamed_new = [r for r in unique if not r["locus_ssnamenr"] and not r["preexisting_ztf"]]
    return {
        "n_loci": len(unique),
        "n_duplicates": len(rows) - len(unique),
        "n_named": len(named),
        "n_sso_tag": sum(r["sso_tag"] for r in unique),
        "n_pure": len(pure),
        "n_pure_one_night": len(pure_one_night),
        "pure_one_night_span_d": (
            (min(r["span_d"] for r in pure_one_night), max(r["span_d"] for r in pure_one_night))
            if pure_one_night else None
        ),
        "pure_multi_night": [r for r in pure if r not in pure_one_night],
        "mixed": mixed,
        "n_unnamed_new": len(unnamed_new),
        "unnamed_one_night": [
            r for r in unnamed_new if r["span_d"] is not None and r["span_d"] < min_span_days
        ],
    }


def format_summary(s: dict, min_span_days: float = MIN_SPAN_DAYS) -> str:
    pct = f" ({100 * s['n_named'] / s['n_loci']:.0f}%)" if s["n_loci"] else ""
    def row(label: str, value) -> str:
        return f"{label:<44}{value}"

    lines = [
        f"high-|b| new loci: {s['n_loci']}"
        + (f"  (duplicates dropped: {s['n_duplicates']})" if s["n_duplicates"] else ""),
        row("  named by ZTF (locus ztf_ssnamenr):", f"{s['n_named']}{pct}"),
        row("  carrying an ANTARES SSO tag:", s["n_sso_tag"]),
        row("  named, every detection the asteroid:", s["n_pure"]),
        row(f"    ... all within {min_span_days:g} d:", s["n_pure_one_night"]),
    ]
    if s["pure_one_night_span_d"]:
        lo, hi = s["pure_one_night_span_d"]
        lines.append(f"    ... span {lo * 1440:.1f} min to {hi * 24:.1f} h")
    for r in s["pure_multi_night"]:
        lines.append(f"    multi-night: {r['locus_id']} asteroids {r['ss_names']} span {r['span_d']} d")
    lines.append(row("  named, with non-asteroid detections:", len(s["mixed"])))
    for r in s["mixed"]:
        lines.append(
            f"    {r['locus_id']}: {r['n_det'] - r['n_ss_det']} of {r['n_det']} detections not the asteroid"
        )
    lines.append(row("  unnamed, not a pre-existing ZTF object:", s["n_unnamed_new"]))
    lines.append(row(f"    ... all detections within {min_span_days:g} d:", len(s["unnamed_one_night"])))
    for r in s["unnamed_one_night"]:
        cats = r["catalogs"] or "no catalogue"
        lines.append(f"    {r['locus_id']}: {r['n_det']} detections in {r['span_d'] * 1440:.1f} min, {cats}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip())
    parser.add_argument("--report", type=from_root, metavar="CSV",
                        help="summarize a saved probe CSV instead of collecting")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help=f"loci to pull from the listing (default {DEFAULT_LIMIT})")
    parser.add_argument("--lookback-days", type=float, default=LOOKBACK_DAYS)
    parser.add_argument("--min-detections", type=int, default=MIN_DETECTIONS)
    parser.add_argument("--out", type=from_root,
                        help="CSV path, relative to the repo root (default reports/asteroid_probe_YYYYMMDD.csv)")
    args = parser.parse_args(argv)

    if args.report:
        if not args.report.exists():
            print(f"{ERROR_PREFIX}no such file: {args.report}", file=sys.stderr)
            return 1
        print(format_summary(summarize(load_csv(args.report))))
        return 0

    try:
        rows = collect(args.limit, args.lookback_days, args.min_detections)
    except antares_errors() as e:
        print(f"{ERROR_PREFIX}ANTARES query failed: {e}", file=sys.stderr)
        return 1
    today = datetime.date.today().strftime("%Y%m%d")
    out = args.out or REPORTS_DIR / f"asteroid_probe_{today}.csv"
    write_csv(rows, out)
    print(f"wrote {len(rows)} rows to {out}\n")
    print(format_summary(summarize(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
