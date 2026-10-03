"""
replay.py - run the transient monitor over past days, from data that cannot change.

The live scan filters on summary fields that keep changing (ANTARES's newest alert,
ALeRCE's last detection), and those are read as of today, not as of the replayed date:
a replay of footprint D as of 2026-04-15 would have fetched 8,105 LSST light curves, 6,453 of
them only because of detections after that date. So a replay filters only on what
cannot change afterwards (design: the user, 2026-10-01):

  candidates   survey objects whose first detection falls in
               [first day - LOOKBACK_DAYS, last day], listed from ALeRCE (LSST and ZTF)
               in LISTING_CHUNK_DAYS chunks, with detections spanning more than one
               night (transient_monitor.spans_nights on today's first and last
               detection). The span cut is lossless by construction: every path to a
               report requires it, and a span only grows, so an object below it today
               was below it on every replayed day.
  neighbours   ZTF objects of any age within the association radius of each LSST
               candidate (a position never changes), for "bracketed by ZTF".
  light curves one fetch per object, cached on disk; a failed fetch is never cached.
  catalogue    every DR10 tile and maskbits brick the candidates' crossmatch needs is
               fetched before judging, in parallel with retries. Judging from a cold
               cache once took 2.5 h on a slow Data Lab (2026-10-01), and a lookup
               that timed out became an "unavailable" crossmatch for the whole run.
  days         each day is judged by transient_monitor.judge_day on light curves
               sliced to that day: no API call per simulated day. Crossmatches that
               could not be made are counted in the summary, never passed as results.

Out of scope: objects first detected before the window that re-brighten (rapid_rise on
old objects). A rise is a pure function of the light curve; unit tests on synthetic
light curves cover it.

ALeRCE's LSST listing can answer a heavy query with an empty result instead of an
error (2026-10-01: a 30-day first-detection window over footprint D returned 0 objects,
although the census holds thousands for it). An empty chunk is therefore asked again
day by day; data in any day marks the chunk's answer as a failure.
"""

from __future__ import annotations

import json
import math
import sys
import time
from dataclasses import asdict
from pathlib import Path

from concurrent.futures import ThreadPoolExecutor

from . import sky_catalog as sc
from . import transient_monitor as mon
from .config import CONFIRM_THRESHOLD_SECONDS, PROJECT_ROOT, WARN_PREFIX
from .photometry import PhotPoint, SurveyObject, fetch_alerce_lsst, fetch_alerce_ztf, truncate

CACHE_DIR = PROJECT_ROOT / "cache" / "replay"
REPORT_DIR = PROJECT_ROOT / "logs"
LISTING_CHUNK_DAYS = 7
PAGE_SIZE = 1000
MAX_PAGES = 200
LSST_CLASSIFIER = "stamp_classifier_rubin_beta"  # one row per object (the listing repeats rows per classifier)
# measured 2026-10-01: ALeRCE query_lightcurve, 10 footprint-D LSST objects, median 1.13 s (max 2.47 s);
# a neighbour cone query costs about the same
SECONDS_PER_CALL = 1.13
# a DR10 tile from Data Lab when it is healthy (~3 s each, 2026-10-01); it has also taken
# over 2 minutes per tile the same afternoon, so the estimate says which it assumes
SECONDS_PER_TILE = 3.0
CATALOGUE_WORKERS = 4
CATALOGUE_RETRY_WAITS = (30, 60)
PROGRESS_EVERY = 50


class ListingError(RuntimeError):
    pass


def _clocks() -> tuple[float, float]:
    """(awake, wall): CLOCK_MONOTONIC stops while the machine is suspended, CLOCK_BOOTTIME does not."""
    return time.monotonic(), time.clock_gettime(time.CLOCK_BOOTTIME)


class StageTimer:
    """Seconds per stage, awake and suspended apart: a suspend must never pass as the work being slow."""

    def __init__(self, clocks=None):
        self.clocks = clocks or _clocks
        self.stages: list[dict] = []
        self._last = self.clocks()

    def mark(self, name: str) -> dict:
        now = self.clocks()
        awake, wall = now[0] - self._last[0], now[1] - self._last[1]
        self.stages.append({"stage": name, "awake_s": round(awake, 1), "suspended_s": round(max(0.0, wall - awake), 1)})
        self._last = now
        return self.stages[-1]

    def summary(self) -> str:
        parts = ", ".join(f"{s['stage']} {s['awake_s']:.0f}s" for s in self.stages)
        suspended = sum(s["suspended_s"] for s in self.stages)
        return f"{parts}; suspended {suspended:.0f}s" + (" (excluded above)" if suspended > 5 else "")


# ------------------------------------------------------------------ listing


def _query(survey: str, cone, lo: float, hi: float, min_ndet: int, page: int):
    from .client import _api_call, _client

    ra, dec, radius = cone
    kw = dict(format="json", ra=ra, dec=dec, radius=radius * 3600, firstmjd=[lo, hi], page=page, page_size=PAGE_SIZE)
    if survey == "lsst":
        kw.update(survey="lsst", n_det=[min_ndet, 10 ** 6], classifier=LSST_CLASSIFIER)
    else:
        kw.update(survey="ztf", ndet=[min_ndet, 10 ** 6])
    res, err = _api_call(_client.query_objects, **kw)
    if err is not None:
        raise ListingError(f"alerce {survey} listing {lo:.1f}-{hi:.1f} page {page}: {err}")
    items = res.get("items", []) if isinstance(res, dict) else (res or [])
    has_next = res.get("has_next") if isinstance(res, dict) else len(items) == PAGE_SIZE
    return items, bool(has_next)


def _rows(survey: str, cone, lo: float, hi: float, min_ndet: int, query=None) -> list[dict]:
    query = query or _query
    out = []
    for page in range(1, MAX_PAGES + 1):
        items, has_next = query(survey, cone, lo, hi, min_ndet, page)
        for i in items:
            out.append({"survey": survey, "oid": str(i["oid"]), "ra": float(i["meanra"]), "dec": float(i["meandec"]),
                        "first": float(i["firstmjd"]), "last": float(i["lastmjd"]),
                        "ndet": int(i.get("n_det") if survey == "lsst" else i.get("ndet"))})
        if not has_next:
            return out
    raise ListingError(f"alerce {survey} listing {lo:.1f}-{hi:.1f}: more than {MAX_PAGES} pages")


def list_candidates(survey: str, cone, lo: float, hi: float, cut: bool = True, query=None,
                    notes: list[str] | None = None) -> list[dict]:
    """
    Objects first detected in [lo, hi], in LISTING_CHUNK_DAYS chunks; with cut, at least
    2 detections (server side) spanning more than one night (here). An empty chunk is
    asked again day by day before it is believed.
    """
    notes = notes if notes is not None else []
    min_ndet = 2 if cut else 1
    rows: dict[str, dict] = {}
    start = lo
    while start < hi:
        end = min(start + LISTING_CHUNK_DAYS, hi)
        chunk = _rows(survey, cone, start, end, min_ndet, query)
        if not chunk:
            daily = []
            d = start
            while d < end:
                daily += _rows(survey, cone, d, min(d + 1, end), min_ndet, query)
                d += 1
            if daily:
                notes.append(f"{survey} {start:.0f}-{end:.0f}: empty chunk answer contradicted by daily queries "
                             f"({len(daily)} objects); daily answers used")
            chunk = daily
        for r in chunk:
            rows[r["oid"]] = r
        start = end
    out = [r for r in rows.values() if lo <= r["first"] <= hi]
    if cut:
        out = [r for r in out if r["ndet"] >= 2 and mon.spans_nights(r["first"], r["last"])]
    return sorted(out, key=lambda r: r["first"])


# ------------------------------------------------------------------ cache files
# Every cache file is written to a temp file and renamed, so an aborted run (Ctrl-C, a
# suspend, a crash) leaves either the old file or none, never half of one; and every
# read checks the file, so a damaged one is fetched again instead of crashing the run
# or passing as data (2026-10-02, after an aborted --compare-cuts).

CANDIDATE_KEYS = {"survey", "oid", "ra", "dec", "first", "last", "ndet"}


def _candidates_ok(d) -> bool:
    return isinstance(d, list) and all(isinstance(r, dict) and CANDIDATE_KEYS <= set(r) for r in d)


def _light_curve_ok(d) -> bool:
    if not (isinstance(d, dict) and isinstance(d.get("points"), list) and "sso_id" in d):
        return False
    try:
        [PhotPoint(**pt) for pt in d["points"]]
    except TypeError:
        return False
    return True


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj))
    tmp.rename(path)


def _read_json(path: Path, valid):
    """A cached file's content, or None when it is absent or damaged (then deleted, to be fetched again)."""
    if not path.exists():
        return None
    try:
        d = json.loads(path.read_text())
        if valid(d):
            return d
        why = "unexpected content"
    except (ValueError, UnicodeDecodeError) as e:
        why = f"{type(e).__name__}: {e}"
    print(f"{WARN_PREFIX}damaged cache file {path} ({why}); deleted, fetched again", file=sys.stderr)
    path.unlink()
    return None


def cached_candidates(name: str, survey: str, cone, lo: float, hi: float, cut: bool, notes: list[str]) -> list[dict]:
    """The candidate list, from the cache or listed once: first detections in a past window do not change."""
    path = CACHE_DIR / "candidates" / f"{name}_{survey}_{lo:.1f}_{hi:.1f}_{'cut' if cut else 'all'}.json"
    rows = _read_json(path, _candidates_ok)
    if rows is not None:
        return rows
    rows = list_candidates(survey, cone, lo, hi, cut, notes=notes)
    _write_json(path, rows)
    return rows


def ztf_neighbours(row: dict, query=None) -> list[dict]:
    """ZTF objects of any age at an LSST candidate's position (cached per object: positions do not change)."""
    path = _neighbours_path(row["oid"])
    cached = _read_json(path, lambda d: isinstance(d, list) and all(isinstance(i, dict) and "oid" in i for i in d))
    if cached is not None:
        return cached
    if query is None:
        from .client import _api_call, _client

        def query(ra, dec, radius_arcsec):
            res, err = _api_call(_client.query_objects, survey="ztf", format="json", ra=ra, dec=dec,
                                 radius=radius_arcsec, page=1, page_size=100)
            if err is not None:
                raise ListingError(f"alerce ztf neighbours of {row['oid']}: {err}")
            return res.get("items", []) if isinstance(res, dict) else (res or [])
    items = query(row["ra"], row["dec"], mon.ASSOC_RADIUS_ARCSEC)
    out = [{"survey": "ztf", "oid": str(i["oid"]), "ra": float(i["meanra"]), "dec": float(i["meandec"]),
            "first": float(i["firstmjd"]), "last": float(i["lastmjd"]), "ndet": int(i.get("ndet") or 0)} for i in items]
    _write_json(path, out)
    return out


def _neighbours_path(oid: str) -> Path:
    return CACHE_DIR / "neighbours" / f"{oid}.json"


# ------------------------------------------------------------------ light curves


def _lc_path(survey: str, oid: str) -> Path:
    return CACHE_DIR / "lightcurves" / survey / f"{oid}.json"


def fetch_light_curve(survey: str, oid: str) -> tuple[list[PhotPoint], str | None, str | None]:
    """(points, sso_id, error) from ALeRCE: one query_lightcurve call."""
    if survey == "lsst":
        return fetch_alerce_lsst(oid)
    points, err = fetch_alerce_ztf(oid)
    return points, None, err


def light_curve(survey: str, oid: str, fetch=None) -> tuple[list[PhotPoint], str | None, str | None]:
    """(points, sso_id, error) from the cache or one fetch; only a successful fetch is cached."""
    path = _lc_path(survey, oid)
    d = _read_json(path, _light_curve_ok)
    if d is not None:
        return [PhotPoint(**p) for p in d["points"]], d["sso_id"], None
    points, sso, err = (fetch or fetch_light_curve)(survey, oid)
    if err is not None:
        return [], None, err
    _write_json(path, {"points": [asdict(p) for p in points], "sso_id": sso})
    return points, sso, None


def is_cached(survey: str, oid: str) -> bool:
    return _lc_path(survey, oid).exists()


# ------------------------------------------------------------------ the replay


def catalogue_needs(rows: list[dict], cone) -> tuple[list[tuple[int, int]], list[str]]:
    """
    (uncached DR10 tiles, uncached maskbits bricks) the candidates' crossmatches will read.
    Only objects whose detections span more than one night can reach the crossmatch
    (evaluate rejects the rest first, on any day), so an uncut run needs the same ones.
    """
    rows = [r for r in rows if mon.spans_nights(r["first"], r["last"])]
    tiles = sorted({t for r in rows for t in sc.tiles_around(r["ra"], r["dec"], sc.SEARCH_RADIUS_ARCSEC)})
    bricks = sc.footprint_bricks(cone)
    rad = sc.GAP_RADIUS_ARCSEC / 3600
    names = set()
    for r in rows:
        dra = rad / math.cos(math.radians(r["dec"]))
        hit = bricks[(bricks["ra1"] < r["ra"] + dra) & (bricks["ra2"] > r["ra"] - dra)
                     & (bricks["dec1"] < r["dec"] + rad) & (bricks["dec2"] > r["dec"] - rad)]
        names |= set(hit["brickname"])
    return ([t for t in tiles if not sc.tile_is_cached(*t)],
            sorted(b for b in names if not sc.maskbits_is_cached(b)))


def _retrying(fn, *args):
    for wait in (*CATALOGUE_RETRY_WAITS, None):
        try:
            return fn(*args)
        except sc.CatalogError as e:
            if wait is None:
                raise
            print(f"{WARN_PREFIX}{e}; retrying in {wait}s", file=sys.stderr, flush=True)
            time.sleep(wait)


def prefetch_catalogue(tiles: list[tuple[int, int]], bricks: list[str], say=print) -> list[str]:
    """Fetch the tiles and maskbits bricks before judging; returns the failures (never cached)."""
    def one(job):
        kind, arg = job
        try:
            _retrying(sc.fetch_tile, *arg) if kind == "tile" else _retrying(sc.fetch_maskbits, arg)
            return None
        except sc.CatalogError as e:
            return str(e)

    jobs = [("tile", t) for t in tiles] + [("maskbits", b) for b in bricks]
    failed, t0 = [], time.time()
    with ThreadPoolExecutor(CATALOGUE_WORKERS) as ex:
        for k, err in enumerate(ex.map(one, jobs), 1):
            if err:
                failed.append(err)
            if k % 100 == 0:
                say(f"    catalogue {k}/{len(jobs)} [{time.time() - t0:.0f}s]", flush=True)
    return failed


def window(first_day: str, last_day: str, lookback_days: float) -> tuple[float, float, list[str]]:
    """(first-detection lo, hi, the days) for replaying first_day..last_day inclusive."""
    import datetime

    d0 = datetime.date.fromisoformat(first_day)
    d1 = datetime.date.fromisoformat(last_day)
    days = [(d0 + datetime.timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]
    return mon.date_to_mjd(first_day) - 1.0 - lookback_days, mon.date_to_mjd(last_day), days


class Declined(Exception):
    """A replay above the cost threshold that was not confirmed."""


def confirm_cost(seconds: float, yes: bool, what: str) -> None:
    """
    Go ahead under CONFIRM_THRESHOLD_SECONDS or with yes; otherwise ask on a terminal.
    Without a terminal, refuse: a replay is never scheduled, so a non-interactive start
    of a many-hour job is a mistake, not a timer (unlike the QA pipeline's units).
    """
    if seconds <= CONFIRM_THRESHOLD_SECONDS or yes:
        return
    msg = f"{what}: ~{seconds / 60:.0f} min estimated"
    if sys.stdin is None or not sys.stdin.isatty():
        raise Declined(f"{msg}; not started - no terminal to confirm on (pass --yes to run it anyway)")
    if input(f"{msg}. Continue? [y/N] ").strip().lower() not in ("y", "yes"):
        raise Declined(f"{msg}; declined")


def price(name: str, cone, first_day: str, last_day: str, lookback_days: float, cut: bool, say=print,
          notes: list[str] | None = None) -> tuple[list[dict], list, list, dict]:
    """
    List the candidates (cached after the first time) and estimate the fetching, printing
    it. Returns (rows, tiles, bricks, estimate); estimate["seconds"] is the total.
    """
    notes = [] if notes is None else notes
    lo, hi, days = window(first_day, last_day, lookback_days)
    say(f"\nReplay {name}: {first_day} .. {last_day} ({len(days)} days), candidates first detected MJD {lo:.1f}-{hi:.1f}"
        f"{'' if cut else ', NO CUT'}", flush=True)
    rows = []
    for survey in ("lsst", "ztf"):
        got = cached_candidates(name, survey, cone, lo, hi, cut, notes)
        say(f"  {survey}: {len(got)} candidates", flush=True)
        rows += got
    lsst = [r for r in rows if r["survey"] == "lsst"]
    todo_nb = [r for r in lsst if not _neighbours_path(r["oid"]).exists()]
    todo_lc = [r for r in rows if not is_cached(r["survey"], r["oid"])]
    calls = len(todo_nb) + len(todo_lc)
    sc.footprint_atlas(cone)  # one query each, cached; the brick list also sizes the maskbits need
    tiles, bricks = catalogue_needs(rows, cone)
    cat_min = len(tiles) * SECONDS_PER_TILE / CATALOGUE_WORKERS / 60
    estimate = {"calls": calls, "calls_min": round(calls * SECONDS_PER_CALL / 60, 1),
                "tiles": len(tiles), "bricks": len(bricks), "catalogue_min": round(cat_min, 1),
                "seconds": round(calls * SECONDS_PER_CALL + cat_min * 60)}
    say(f"  to fetch: {len(todo_lc)} light curves, {len(todo_nb)} ZTF neighbour lookups "
        f"(~{calls * SECONDS_PER_CALL / 60:.0f} min at {SECONDS_PER_CALL} s per call, plus the neighbours' own light curves); "
        f"{len(tiles)} DR10 tiles and {len(bricks)} maskbits bricks (~{cat_min:.0f} min at {SECONDS_PER_TILE:g} s per tile "
        f"over {CATALOGUE_WORKERS} workers, healthy Data Lab; much longer when it is slow)", flush=True)
    return rows, tiles, bricks, estimate


def run(name: str, cone, first_day: str, last_day: str, lookback_days: float = mon.LOOKBACK_DAYS,
        bracket_days: float = mon.BRACKET_DAYS, min_detections: int = mon.MIN_DETECTIONS, cut: bool = True,
        classify=None, quiet: bool = False, estimate_only: bool = False, yes: bool = True) -> list[dict]:
    """
    Replay first_day..last_day; returns the alerts as records and writes them to logs/.
    estimate_only: list the candidates (cheap, cached), print the time estimate, fetch nothing.
    yes=False: above CONFIRM_THRESHOLD_SECONDS, ask before fetching (raises Declined).
    """
    say = (lambda *a, **k: None) if quiet else print
    timer = StageTimer()
    lo, hi, days = window(first_day, last_day, lookback_days)
    notes: list[str] = []
    rows, tiles, bricks, estimate = price(name, cone, first_day, last_day, lookback_days, cut, say, notes)
    timer.mark("listing")
    lsst = [r for r in rows if r["survey"] == "lsst"]
    if estimate_only:
        return []
    confirm_cost(estimate["seconds"], yes, f"replay {name} {first_day}..{last_day}{'' if cut else ' without the cut'}")
    timer.mark("estimate")
    if tiles or bricks:
        failed = prefetch_catalogue(tiles, bricks, say)

        if failed:
            print(f"{WARN_PREFIX}{len(failed)} catalogue fetches failed; their crossmatches will be counted as "
                  f"unavailable: {', '.join(failed[:3])}{' ...' if len(failed) > 3 else ''}", file=sys.stderr)

    timer.mark("catalogue")
    t0 = time.time()
    have = {(r["survey"], r["oid"]) for r in rows}
    neighbours = []  # association context only: bracket LSST onsets, never judged themselves
    for k, r in enumerate(lsst, 1):
        for nb in ztf_neighbours(r):
            if ("ztf", nb["oid"]) not in have:
                have.add(("ztf", nb["oid"]))
                neighbours.append(nb)
        if k % PROGRESS_EVERY == 0:
            say(f"    neighbours {k}/{len(lsst)} [{time.time() - t0:.0f}s]", flush=True)
    timer.mark("neighbours")

    objs, context, errors = [], [], []
    for k, (r, into) in enumerate([(r, objs) for r in rows] + [(r, context) for r in neighbours], 1):
        points, sso, err = light_curve(r["survey"], r["oid"])
        if err is not None:
            errors.append(f"{r['survey']}:{r['oid']}: {err}")
        into.append(SurveyObject(r["survey"], r["oid"], r["ra"], r["dec"], points=points, sso_id=sso,
                                 broker_refs={"alerce": r["oid"]}, fetch_errors=[err] if err else []))
        if k % PROGRESS_EVERY == 0:
            say(f"    light curves {k}/{len(rows) + len(neighbours)} [{time.time() - t0:.0f}s]", flush=True)
    timer.mark("light curves")
    if errors:
        print(f"{WARN_PREFIX}{len(errors)} light curves could not be fetched (not cached; rerun to retry): "
              f"{', '.join(errors[:5])}{' ...' if len(errors) > 5 else ''}", file=sys.stderr)

    classify = classify or _memo(mon.footprint_classifier(cone))
    verdicts = getattr(classify, "seen", None)
    state = {"reported": {}, "parked": {}}
    records = []
    for day in days:
        now = mon.date_to_mjd(day)
        since = now - lookback_days
        today = _as_of(objs, now, need_detection=True)
        result = mon.judge_day(today, now, since, state, classify, bracket_days, min_detections,
                               context=_as_of(context, now, need_detection=False))
        for c, new_conditions, anchor in [(c, n, None) for c, n in result.alerts] + result.notes:
            records.append({"day": day, "key": c.obj.key, "ra": c.obj.ra, "dec": c.obj.dec,
                            "category": c.category, "conditions": new_conditions,
                            "onset": c.onset.code if c.onset else None,
                            "crossmatch": c.match.verdict, "evidence": c.match.evidence,
                            **({"note_on": anchor} if anchor else {})})
        say(f"  {day}: {len(today):5d} objects, {len(result.alerts)} alerts"
            + (f", {len(result.notes)} notes" if result.notes else "")
            + "".join(f"\n      {c.category:13s} {c.obj.key}  {', '.join(n)}" for c, n in result.alerts)
            + "".join(f"\n      note          {c.obj.key}  {', '.join(n)}  on {a}" for c, n, a in result.notes), flush=True)

    timer.mark("judging")
    unavailable = [x for x in (verdicts or {}).values() if x.verdict == "unavailable"]
    gaps = sum(any("catalogue gap" in e or "no DR10 brick" in e for e in x.evidence) for x in unavailable)
    if unavailable:
        say(f"  crossmatch unavailable for {len(unavailable)} positions: {gaps} catalogue gaps, "
            f"{len(unavailable) - gaps} fetch failures (those alerts are 'unchecked', not results)", flush=True)
    out = REPORT_DIR / f"replay_{name}_{first_day}_{last_day}{'' if cut else '_nocut'}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"footprint": name, "cone": list(cone), "first_day": first_day, "last_day": last_day,
                               "cut": cut, "candidates": len(rows), "fetch_errors": errors, "notes": notes,
                               "crossmatch_unavailable": {"catalogue_gap": gaps, "fetch_failure": len(unavailable) - gaps},
                               "estimate": estimate, "timings": timer.stages,
                               "alerts": records}, indent=1, default=str))
    for n in notes:
        print(f"{WARN_PREFIX}{n}", file=sys.stderr)
    # the point-source watch counts replayed alerts too (TNS as it stands today)
    ps = [(r["key"], r["ra"], r["dec"], mon.date_to_mjd(r["day"]), f"replay {name} {first_day}..{last_day}")
          for r in records if r["category"] == "point_source"]
    for line in mon.point_source_watch(ps, mon.now_mjd()):
        say(line, flush=True)
    n_notes = sum("note_on" in r for r in records)
    say(f"Replay done: {len(records) - n_notes} alerts and {n_notes} notes (the same event seen by another survey) "
        f"over {len(days)} days -> {out}", flush=True)
    say(f"  time per stage (awake): {timer.summary()}", flush=True)
    return records


def _as_of(objs: list[SurveyObject], now: float, need_detection: bool) -> list[SurveyObject]:
    """The objects as they stood at `now`: points after it dropped; without a detection by then, absent."""
    out = []
    for o in objs:
        pts = truncate(o.points, now)
        if need_detection and not any(p.detected for p in pts):
            continue
        out.append(SurveyObject(o.survey, o.survey_object_id, o.ra, o.dec, points=pts, sso_id=o.sso_id,
                                broker_refs=o.broker_refs, fetch_errors=list(o.fetch_errors)))
    return out


def _memo(classify):
    """DR10 does not change during a replay: one crossmatch per position."""
    seen: dict = {}

    def cached(ra: float, dec: float):
        k = (round(ra, 7), round(dec, 7))
        if k not in seen:
            seen[k] = classify(ra, dec)
        return seen[k]
    cached.seen = seen
    return cached


def alert_set(records: list[dict]) -> set[tuple]:
    return {(r["day"], r["key"], r["category"], tuple(r["conditions"]), r.get("note_on")) for r in records}


def compare_cuts(name: str, cone, first_day: str, last_day: str, lookback_days: float = mon.LOOKBACK_DAYS,
                 bracket_days: float = mon.BRACKET_DAYS, min_detections: int = mon.MIN_DETECTIONS,
                 yes: bool = False) -> int:
    """
    The equivalence test: the same window with and without the span cut must give identical
    alerts. Both sides are priced before either fetches anything, and the total is what
    needs confirming: the uncut side is the expensive one, often by hours (2026-10-02).
    """
    sides = [price(name, cone, first_day, last_day, lookback_days, cut)[3]["seconds"] for cut in (True, False)]
    print(f"\nEquivalence test: ~{sides[0] / 60:.0f} min with the cut + ~{sides[1] / 60:.0f} min without it", flush=True)
    confirm_cost(sum(sides), yes, f"--compare-cuts {name} {first_day}..{last_day}")
    classify = _memo(mon.footprint_classifier(cone))
    with_cut = alert_set(run(name, cone, first_day, last_day, lookback_days, bracket_days, min_detections, True, classify))
    without = alert_set(run(name, cone, first_day, last_day, lookback_days, bracket_days, min_detections, False, classify))
    if with_cut == without:
        print(f"\nEQUIVALENT: {len(with_cut)} alerts with and without the cut")
        return 0
    print(f"\nDIFFERENT: only without the cut {sorted(without - with_cut)}; only with it {sorted(with_cut - without)}")
    return 1
