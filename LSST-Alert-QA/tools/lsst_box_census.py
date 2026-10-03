"""
Census of LSST objects in candidate test boxes, from ALeRCE.

Question: where, between 2026-02-15 and the 2026-07-14 stop, did Rubin alert densely
enough to replay the transient monitor on? Candidates are box D (COSMOS) and the region
RA 240-255, Dec -25..-15, tiled into six 5x5 deg tiles (the densest patch of May-June).

Method, and why each step:
  - ALeRCE only searches cones, so each region is fetched as the cone around it and
    trimmed to the region (half-open, so neighbours never share an object).
  - Regions are fetched in 2.5x2.5 deg cells, one cell at a time, and each cell prints
    its summary as soon as it is complete: results arrive incrementally, and a stopped
    run keeps every finished cell.
  - firstmjd chunks of one week (plus four pre-window chunks): a single tile held 10,116
    new objects in 3 days of late May, and box D 57,900 on one February night, so a
    whole-window query would page for hours. Finished chunks are checkpointed; a rerun
    resumes. (Box D was fetched first as one box in one-day chunks; those chunks still
    count, so its cells are not refetched.)
  - classifier=stamp_classifier_rubin_beta: the listing returns one row per object
    *per classifier* (two stamp classifiers run on LSST), so unfiltered paging reads
    every object twice. The filter gives one row each; checked 2026-10-01 on four
    windows (Feb, Apr, Jun in D and the region, Jul 14): 0 objects missing.
  - Paging is deterministic (two walks and an oid-ordered walk of the same 3-day
    query returned identical sets), so a paged walk is a complete enumeration.
  - Objects first seen before the window but still detected in it come from the
    pre-window chunks (firstmjd before the window, lastmjd inside it), for "active".
  - lastmjd is ALeRCE's current value; since the stream stopped on 07-14 it is final.

Every object is kept (oid, position, first/last MJD, n_det, n_forced, stamp class) in
cache/lsst_census/<box>.jsonl, so a different tiling is a --report away, not a refetch.

Usage:
    uv run tools/lsst_box_census.py                 # fetch (resumes), cell by cell
    uv run tools/lsst_box_census.py --report        # counts per box, month and cell
    uv run tools/lsst_box_census.py --report --map  # plus 1x1 deg maps
    uv run tools/lsst_box_census.py --csv reports/lsst_box_census_20261001.csv  # per box and month
    uv run tools/lsst_box_census.py --boxes D T240-20 --workers 2  # only these boxes, 2 requests at a time

  --boxes NAME [NAME ...]  which boxes (default: all): D (COSMOS) and the six region tiles
                           T240-25 T245-25 T250-25 T240-20 T245-20 T250-20 (named by their lower
                           RA, Dec corner). Applies to fetching, --report and --csv alike.
  --workers N              week chunks fetched in parallel, i.e. ALeRCE requests in flight
                           (default 3). Fetching only; --report and --csv use no network.
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))  # rubin_qa without an install

from rubin_qa.config import PROJECT_ROOT, from_root  # noqa: E402

URL = "https://api-lsst.alerce.online/object_api/list_objects"
CLASSIFIER = "stamp_classifier_rubin_beta"
PAGE_SIZE = 1000
REQUEST_TIMEOUT = 120
RETRY_WAITS = (15, 30, 60)
# cache, not logs: the rows can be fetched again (the stream stopped 07-14, so ALeRCE's answers are
# final), only at a cost of hours. done.jsonl (the resume checkpoint) stays with the <box>.jsonl rows
# it vouches for: a rerun with only the rows left would append every chunk again
OUT_DIR = PROJECT_ROOT / "cache" / "lsst_census"
WARN_PREFIX = "WARN: "

WINDOW_MJD = (61086, 61236)  # 2026-02-15 .. 2026-07-14 inclusive: the stream's last night
# 2025-08-24 (before ALeRCE's first LSST objects, 2025-10) to the window; edges sit on the
# 7-day grid of the first box-D run, so its pre-window chunks cover these
PRE_EDGES = (60900, 60949, 60998, 61047, 61086)
LEGACY_PRE_DAYS = 7
WEEK_DAYS = 7
CELL_DEG = 2.5
BINS = [("Feb15-28", 61086, 61100), ("Mar", 61100, 61131), ("Apr", 61131, 61161),
        ("May", 61161, 61192), ("Jun", 61192, 61222), ("Jul1-14", 61222, 61236)]
# fetch order: the RA 240-255 region first, box D (already fetched) last
BOXES = {
    **{f"T{ra}{dec:+d}": (float(ra), ra + 5.0, float(dec), dec + 5.0) for dec in (-25, -20) for ra in (240, 245, 250)},
    "D": (147.6, 152.6, 0.0, 5.0),
}
# the monitor's "new" needs >= 3 detections over > 1 night; >= 0.5 d apart stands in
ELIGIBLE_NDET = 3
ELIGIBLE_SPAN_DAYS = 0.5
MIN_ABS_GAL_LAT = 20.0  # transient_monitor rejects below this
NORM_AREA_DEG2 = 25.0

Box = tuple[float, float, float, float]
Chunk = tuple[str, int, int]  # kind (pre | week), firstmjd lo, hi

_lock = threading.Lock()


def sep_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    a = math.radians
    c = math.sin(a(dec1)) * math.sin(a(dec2)) + math.cos(a(dec1)) * math.cos(a(dec2)) * math.cos(a(ra2 - ra1))
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def cone(box: Box) -> tuple[float, float, float]:
    ra_min, ra_max, dec_min, dec_max = box
    ra0, dec0 = (ra_min + ra_max) / 2, (dec_min + dec_max) / 2
    r = max(sep_deg(ra0, dec0, ra, dec) for ra in (ra_min, ra_max) for dec in (dec_min, dec_max))
    return ra0, dec0, (r + 0.02) * 3600


def cells(parent: str) -> list[tuple[str, Box]]:
    ra_min, ra_max, dec_min, dec_max = BOXES[parent]
    out = []
    for j in range(round((dec_max - dec_min) / CELL_DEG)):
        for i in range(round((ra_max - ra_min) / CELL_DEG)):
            ra, dec = round(ra_min + i * CELL_DEG, 4), round(dec_min + j * CELL_DEG, 4)
            out.append((f"{parent}@{ra:g},{dec:+g}", (ra, ra + CELL_DEG, dec, dec + CELL_DEG)))
    return out


def in_box(r: dict, box: Box) -> bool:
    return box[0] <= r["ra"] < box[1] and box[2] <= r["dec"] < box[3]


def chunks() -> list[Chunk]:
    out = [("pre", lo, hi) for lo, hi in zip(PRE_EDGES, PRE_EDGES[1:])]
    out += [("week", d, min(d + WEEK_DAYS, WINDOW_MJD[1])) for d in range(WINDOW_MJD[0], WINDOW_MJD[1], WEEK_DAYS)]
    return out


def chunk_key(cell: str, chunk: Chunk) -> str:
    return f"{cell}|{chunk[0]}|{chunk[1]}"


def covered(parent: str, cell: str, chunk: Chunk, done: set[str]) -> bool:
    """Fetched for this cell, or by the first run for the whole parent box (pre: 7-day, then 1-day chunks)."""
    kind, lo, hi = chunk
    if chunk_key(cell, chunk) in done:
        return True
    if kind == "pre":
        return all(f"{parent}|pre|{d}" in done for d in range(lo, hi, LEGACY_PRE_DAYS))
    return all(f"{parent}|day|{d}" in done for d in range(lo, hi))


def get(params: dict) -> dict:
    err = ""
    for wait in (*RETRY_WAITS, None):
        try:
            r = requests.get(URL, params=params, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                return r.json()
            err = f"HTTP {r.status_code}"
        except (requests.RequestException, ValueError) as e:
            err = type(e).__name__
        if wait is not None:
            time.sleep(wait)
    raise RuntimeError(err)


def fetch_chunk(box: Box, chunk: Chunk) -> tuple[list[dict], int]:
    kind, lo, hi = chunk
    ra0, dec0, rad = cone(box)
    params = {"survey": "lsst", "ra": ra0, "dec": dec0, "radius": rad, "classifier": CLASSIFIER,
              "firstmjd": [lo, hi], "page_size": PAGE_SIZE}
    if kind == "pre":
        params["lastmjd"] = [WINDOW_MJD[0], 70000]
    rows, page = {}, 1
    while True:
        j = get({**params, "page": page})
        for i in j["items"]:
            r = {"oid": str(i["oid"]), "ra": i["meanra"], "dec": i["meandec"], "first": i["firstmjd"],
                 "last": i["lastmjd"], "ndet": i["n_det"], "nforced": i.get("n_forced"), "cls": i.get("class_name")}
            if in_box(r, box):
                rows[r["oid"]] = r
        if not j["has_next"]:
            return list(rows.values()), page
        page += 1


def done_keys() -> set[str]:
    path = OUT_DIR / "done.jsonl"
    keys = set()
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                keys.add(json.loads(line)["key"])
            except ValueError:  # a line cut short by a kill
                pass
    return keys


def load(parent: str, box: Box | None = None) -> dict[str, dict]:
    path = OUT_DIR / f"{parent}.jsonl"
    rows = {}
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if box is None or in_box(r, box):
                rows[r["oid"]] = r
    return rows


def eligible(r: dict) -> bool:
    return r["ndet"] >= ELIGIBLE_NDET and r["last"] - r["first"] >= ELIGIBLE_SPAN_DAYS


def bin_counts(rows: dict[str, dict]) -> list[tuple[int, int]]:
    out = []
    for _, lo, hi in BINS:
        new = [r for r in rows.values() if lo <= r["first"] < hi]
        out.append((len(new), sum(eligible(r) for r in new)))
    return out


def cell_summary(cell: str, parent: str, box: Box) -> str:
    counts = bin_counts(load(parent, box))
    per_bin = "  ".join(f"{label} {n}/{e}" for (label, _, _), (n, e) in zip(BINS, counts))
    return (f"CELL {cell:18s} new {sum(n for n, _ in counts):7d}  eligible {sum(e for _, e in counts):6d}   "
            f"(new/eligible) {per_bin}")


def run_fetch(names: list[str], workers: int) -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    done = done_keys()
    todo, remaining = [], {}
    for parent in names:
        for cell, box in cells(parent):
            mine = [(parent, cell, box, c) for c in chunks() if not covered(parent, cell, c, done)]
            todo += mine
            remaining[cell] = len(mine)
    n_cells = sum(len(cells(p)) for p in names)
    print(f"{len(todo)} chunks to fetch in {sum(1 for v in remaining.values() if v)} of {n_cells} cells, "
          f"{workers} workers", flush=True)
    for parent in names:  # cells already complete report straight away
        for cell, box in cells(parent):
            if remaining[cell] == 0:
                print(cell_summary(cell, parent, box), flush=True)
    failed = []

    def work(item):
        parent, cell, box, c = item
        t = time.time()
        try:
            rows, pages = fetch_chunk(box, c)
        except RuntimeError as e:
            print(f"{WARN_PREFIX}{cell} {c[0]} {c[1]}: failed after retries ({e})", file=sys.stderr, flush=True)
            failed.append(chunk_key(cell, c))
            return
        with _lock:
            with open(OUT_DIR / f"{parent}.jsonl", "a") as f:
                f.writelines(json.dumps(r) + "\n" for r in rows)
            with open(OUT_DIR / "done.jsonl", "a") as f:
                f.write(json.dumps({"key": chunk_key(cell, c), "rows": len(rows), "pages": pages,
                                    "s": round(time.time() - t, 1)}) + "\n")
            remaining[cell] -= 1
            complete = remaining[cell] == 0 and not any(k.startswith(cell + "|") for k in failed)
        if pages > 3:
            print(f"  {cell} {c[0]} {c[1]}: {len(rows)} in cell, {pages} pages [{time.time() - t:.0f}s]", flush=True)
        if complete:
            print(cell_summary(cell, parent, box), flush=True)

    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(work, todo))
    print(f"done; {len(failed)} chunks failed (rerun to retry them)", flush=True)
    return 1 if failed else 0


def box_geometry(box: Box) -> tuple[float, float, float, float]:
    """Area (deg2), |b| range, and the fraction of the box at |b| >= MIN_ABS_GAL_LAT."""
    import astropy.units as u
    import numpy as np
    from astropy.coordinates import SkyCoord

    ra_min, ra_max, dec_min, dec_max = box
    area = (ra_max - ra_min) * math.degrees(math.sin(math.radians(dec_max)) - math.sin(math.radians(dec_min)))
    ra, dec = np.meshgrid(np.linspace(ra_min, ra_max, 41), np.linspace(dec_min, dec_max, 41))
    b = np.abs(SkyCoord(ra.ravel() * u.deg, dec.ravel() * u.deg).galactic.b.deg)
    return area, float(b.min()), float(b.max()), float((b >= MIN_ABS_GAL_LAT).mean())


def run_report(names: list[str], show_map: bool) -> int:
    done = done_keys()
    for name in names:
        box = BOXES[name]
        rows = load(name)
        complete = [cell for cell, _ in cells(name) if all(covered(name, cell, c, done) for c in chunks())]
        area, b_lo, b_hi, usable = box_geometry(box)
        scale = NORM_AREA_DEG2 / area
        n_cells = len(cells(name))
        print(f"\n{name}  RA {box[0]:g}..{box[1]:g}  Dec {box[2]:g}..{box[3]:g}  area {area:.1f} deg2  "
              f"|b| {b_lo:.1f}..{b_hi:.1f} ({usable:.0%} at |b| >= {MIN_ABS_GAL_LAT:g})  "
              f"cells complete {len(complete)}/{n_cells}{'' if len(complete) == n_cells else '  PARTIAL'}")
        print(f"  {'bin':9s} {'new':>7s} {'ndet>=2':>8s} {'eligible':>8s} {'active':>7s} {'elig/25deg2':>11s}  stamp class of new")
        for label, lo, hi in BINS:
            new = [r for r in rows.values() if lo <= r["first"] < hi]
            active = sum(r["first"] < hi and r["last"] >= lo for r in rows.values())
            elig = sum(eligible(r) for r in new)
            cls = Counter(r["cls"] for r in new)
            top = ", ".join(f"{c} {n / len(new):.0%}" for c, n in cls.most_common(3)) if new else ""
            print(f"  {label:9s} {len(new):7d} {sum(r['ndet'] >= 2 for r in new):8d} {elig:8d} {active:7d} "
                  f"{elig * scale:11.0f}  {top}")
        for cell, cbox in cells(name):
            line = cell_summary(cell, name, cbox)
            print("  " + line + ("" if cell in complete else "  PARTIAL"))
        if show_map:
            print_map(box, [r for r in rows.values() if eligible(r) and WINDOW_MJD[0] <= r["first"] < WINDOW_MJD[1]])
    return 0


def write_csv(names: list[str], path: str) -> int:
    """One row per box and month: new objects, multi-night (eligible) ones, active, stamp-class shares, |b| coverage."""
    import csv

    done = done_keys()
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["box", "ra_min", "ra_max", "dec_min", "dec_max", "area_deg2", "frac_abs_b_ge_20", "cells_complete",
                    "month", "new", "new_ndet_ge2", "eligible", "active", "eligible_per_25deg2",
                    "frac_bogus", "frac_asteroid", "frac_vs", "frac_agn", "frac_sn"])
        for name in names:
            box = BOXES[name]
            rows = load(name)
            area, _, _, usable = box_geometry(box)
            complete = sum(all(covered(name, cell, c, done) for c in chunks()) for cell, _ in cells(name))
            for label, lo, hi in BINS:
                new = [r for r in rows.values() if lo <= r["first"] < hi]
                cls = Counter(r["cls"] for r in new)
                frac = {k: round(cls[k] / len(new), 3) if new else 0.0 for k in ("bogus", "asteroid", "VS", "AGN", "SN")}
                elig = sum(eligible(r) for r in new)
                w.writerow([name, *box, round(area, 1), round(usable, 2), f"{complete}/{len(cells(name))}", label,
                            len(new), sum(r["ndet"] >= 2 for r in new), elig,
                            sum(r["first"] < hi and r["last"] >= lo for r in rows.values()),
                            round(elig * NORM_AREA_DEG2 / area), *frac.values()])
    print(f"wrote {path}")
    return 0


def print_map(box: Box, rows: list[dict]) -> None:
    """Eligible new objects per 1x1 deg cell, north up, east left."""
    ra_min, ra_max, dec_min, dec_max = box
    grid = Counter((int(r["ra"] - ra_min), int(r["dec"] - dec_min)) for r in rows)
    n_ra, n_dec = int(ra_max - ra_min), int(dec_max - dec_min)
    print("  eligible new, 1x1 deg cells (north up, east left; columns RA "
          + " ".join(f"{ra_max - i - 1:g}" for i in range(n_ra)) + ")")
    for j in reversed(range(n_dec)):
        print(f"  Dec {dec_min + j:+5g} " + " ".join(f"{grid.get((n_ra - 1 - i, j), 0):6d}" for i in range(n_ra)))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--boxes", nargs="+", choices=list(BOXES), default=list(BOXES), metavar="NAME",
                   help=f"boxes to fetch or report (default all: {' '.join(BOXES)}); "
                        "T<ra><dec> tiles are named by their lower RA, Dec corner")
    p.add_argument("--workers", type=int, default=3,
                   help="week chunks fetched in parallel, i.e. ALeRCE requests in flight (default 3; fetching only)")
    p.add_argument("--report", action="store_true", help="read back the fetched objects, no network")
    p.add_argument("--map", action="store_true", help="with --report: 1x1 deg maps of eligible new objects")
    p.add_argument("--csv", metavar="PATH", type=from_root,
                   help="write the per-box, per-month summary as CSV (no network; relative to the repo root)")
    args = p.parse_args()
    if args.csv:
        return write_csv(args.boxes, args.csv)
    if args.report:
        return run_report(args.boxes, args.map)
    return run_fetch(args.boxes, args.workers)


if __name__ == "__main__":
    sys.exit(main())
