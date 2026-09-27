"""
transient_monitor.py - daily scan of a sky cone for new transients.
Primary targets: SNe, TDEs, orphan transients. Broker-neutral: brokers are
data sources (photometry.py); this module decides what is interesting, on
photometry, per survey object (a ZTF objectId, an LSST diaObjectId).

  discover   every survey object in the cone active within N days
             (ANTARES loci split into their survey objects; ALeRCE LSST cone)
  reject     galactic plane, the survey's own asteroid association,
             Legacy Surveys DR10 star (Gaia astrometry) unless moving
  new        judged per survey, on that survey's own history:
               first detection within N days            (--lookback-days, 14)
               >= MIN_DETECTIONS detections, over more than one night
               a quiet point within X days before it     (--bracket-days, 10)
               deep enough: QUIET_SIGMA * flux_err(quiet) < flux(first detection)
             An LSST onset may also be bracketed by a ZTF upper limit on a ZTF
             object at the same position ("bracketed_by_ztf"): Rubin's forced
             photometry never precedes the first detection at any broker.
  rising     recent onset, flux climbing across the post-onset photometry
             (forced photometry included): the main path for LSST
  rapid_rise >= 1 mag within 3 days, any age; an old object doing it is
             re_brightening. Ordinary SNe are slower: that is what rising is for.
  unbracketed  recent onset, nothing to bracket it: parked, re-judged each run
  classify   DR10 locally (sky_catalog.py): host -> new_candidate,
             AGN -> agn_flare, nothing -> orphan, point-like -> point_source
  report     broker tags, classifications, catalogue matches and TNS are
             printed, never filtered on

Each survey object is reported once per condition it reaches. State is keyed
on survey IDs, in logs/transient_monitor_<footprint>.json.

    python -m rubin_qa.transient_monitor --footprint D --as-of 2026-04-15 --dry-run
    python -m rubin_qa.transient_monitor --cone RA DEC RADIUS

Run it with -m, not by file path: it uses the package's relative imports.
"""

import argparse
import bisect
import datetime
import json
import math
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import astropy.units as u
from astropy.coordinates import SkyCoord

from . import retry_budget, sky_catalog
from .config import ERROR_PREFIX, MJD_J2000, PROJECT_ROOT, WARN_PREFIX, now_mjd
from .photometry import (  # noqa: F401  (is_solar_system, NETWORK_ERRORS re-exported for tools/)
    NETWORK_ERRORS,
    Cone,
    PhotPoint,
    SurveyObject,
    antares_tns,
    discover_alerce_lsst,
    discover_antares,
    fetch_alerce_lsst,
    fetch_alerce_ztf,
    is_solar_system,
    locus_survey_ids,
    njy_to_mag,
    sep_deg,
    truncate,
)

LOOKBACK_DAYS = 14.0  # N: a survey's first detection must be this recent to be new
BRACKET_DAYS = 10.0  # X: a quiet point this close before the first detection brackets the onset
QUIET_SIGMA = 5.0  # quiet point deep enough if QUIET_SIGMA * flux_err < first-detection flux
MIN_DETECTIONS = 3  # per survey object; filters noise, NOT asteroids (MIN_SPAN_DAYS)
# Detections must span more than one night. Asteroids reach MIN_DETECTIONS in a
# single night: of 159 new high-|b| loci with >= 3 detections (2026-09-27), 62
# were known asteroids; 61 held nothing but asteroid detections, and 60 of those
# fell on one night, 1.4 min to 3.9 h apart. The survey's own asteroid ID names
# the known ones; this span is what catches the unknown ones. It does not
# replace the ID check: the 61st locus was two different asteroids crossing
# 8 days apart, which only the ID catches.
# Evidence: reports/asteroid_probe_20260927.csv, via tools/asteroid_probe.py.
MIN_SPAN_DAYS = 0.5
MIN_ABS_GAL_LAT = 20.0  # |b| below this: dust, crowding, stellar variables
RAPID_RISE_MAG = 1.0  # kilonova-like: >= 1 mag brighter within RAPID_RISE_DAYS
RAPID_RISE_DAYS = 3.0
RISING_SIGMA = 3.0  # rising: latest minus earliest post-onset flux, in combined sigma
RISING_MIN_MAG = 0.3  # ... and at least this much brighter
RISING_MIN_SPAN_DAYS = 0.5  # ... measured on at least two nights
ASSOC_RADIUS_ARCSEC = 1.5  # survey objects this close are the same place
STATE_RETENTION_DAYS = 60.0
STATE_DIR = PROJECT_ROOT / "logs"
MAX_RETRIES = 3
RETRY_WAIT = 300  # 5 minutes
WARN_LIMIT = 5  # per-object fetch warnings printed per run; the rest are counted
PROGRESS_EVERY = 100  # LSST photometry fetches between progress lines

DEFAULT_RADIUS_DEG = 2.82  # pi r^2 = 25 deg2, the area of the 5x5 deg boxes it replaced
FOOTPRINTS = {
    "C": ((35.7, -9.0, DEFAULT_RADIUS_DEG), "real run v1: just south of the XMM-LSS deep field"),
    "D": ((150.1, 2.5, DEFAULT_RADIUS_DEG), "test replay: COSMOS, Rubin data 2026-02-15..05-15"),
    "ecdfs": ((53.1, -27.8, DEFAULT_RADIUS_DEG), "real run v2 candidate: ECDFS deep field"),
}

# NewVerdict.status
NEW, UNBRACKETED, NOT_NEW = "new", "unbracketed", "not_new"
NOT_NEW_REASON = {
    "no_detections": "no_detections",
    "preexisting_ztf_object": "preexisting_ztf_object",
    "old": "old_no_rise",
    "too_few_detections": "too_few_detections",
    "single_night": "single_night",
}
NEW_CATEGORY = {
    "host": "new_candidate", "agn": "agn_flare", "none": "orphan",
    "point_source": "point_source", "stellar": "stellar_flare", "unavailable": "unchecked",
}
CATEGORY_ORDER = (
    "new_candidate", "agn_flare", "orphan", "point_source", "stellar_flare", "unchecked",
    "rising", "re_brightening",
)


@dataclass
class NewVerdict:
    survey: str
    status: str  # NEW | UNBRACKETED | NOT_NEW
    code: str  # why: "bracketed", "bracketed_by_ztf", "no_quiet_point", "old", ...
    first_det_mjd: float | None = None
    last_quiet_mjd: float | None = None
    n_det: int = 0


@dataclass
class Rise:
    band: str
    mag: float  # brightening, mag (inf when it rose from nothing)
    sigma: float
    days: float


@dataclass
class Candidate:
    obj: SurveyObject
    gal_b: float
    category: str  # one of CATEGORY_ORDER
    conditions: list[str]
    onset: NewVerdict
    rise: Rise | None
    rapid: float | None
    match: sky_catalog.Crossmatch
    context: list[str] = field(default_factory=list)


@dataclass
class Evaluation:
    candidate: Candidate | None = None
    reason: str | None = None  # why nothing is reported (None when candidate is set)
    parked: NewVerdict | None = None


# ------------------------------------------------------------------ helpers


def mjd_to_datetime(mjd: float) -> datetime.datetime:
    epoch = datetime.datetime(2000, 1, 1, 12, tzinfo=datetime.timezone.utc)
    return epoch + datetime.timedelta(days=mjd - MJD_J2000)


def date_to_mjd(day: str) -> float:
    """MJD at the *end* of a UTC date: an --as-of date includes that whole day."""
    d = datetime.datetime.fromisoformat(day).replace(tzinfo=datetime.timezone.utc)
    epoch = datetime.datetime(2000, 1, 1, 12, tzinfo=datetime.timezone.utc)
    return MJD_J2000 + (d - epoch).total_seconds() / 86400 + 1.0


def _date(mjd: float | None) -> str:
    return str(mjd_to_datetime(mjd).date()) if mjd is not None else "?"


def galactic_latitude(ra: float, dec: float) -> float:
    return float(SkyCoord(ra=ra * u.deg, dec=dec * u.deg, frame="icrs").galactic.b.deg)


def ztf_id_is_old(oid: str, since_mjd: float) -> bool:
    """
    A ZTF objectId's two digits after "ZTF" are the year of its first ZTF
    detection. A new *locus* is not a new *object*: of 300 loci whose oldest
    alert was under 14 days old (2026-09-27), 84 carried a ZTF ID from an
    earlier year. Older than the window's start year = already known to ZTF.
    """
    try:
        return int(oid[3:5]) < mjd_to_datetime(since_mjd).year % 100
    except (TypeError, ValueError):
        return False


def preexisting_ztf_ids(props: dict, since_mjd: float) -> list[str]:
    """ZTF IDs on an ANTARES locus first seen before the window (tools/asteroid_probe.py)."""
    return [oid for oid in locus_survey_ids(props)["ztf"] if ztf_id_is_old(oid, since_mjd)]


def _finite(x) -> bool:
    return x is not None and math.isfinite(x)


def detections(points: list[PhotPoint]) -> list[PhotPoint]:
    return sorted((p for p in points if p.detected and _finite(p.flux)), key=lambda p: p.mjd)


def quiet_points(points: list[PhotPoint]) -> list[tuple[float, float]]:
    """(mjd, depth) of epochs where the source was not detected; depth = QUIET_SIGMA * flux_err."""
    out = []
    for p in points:
        if p.detected or not _finite(p.flux_err) or p.flux_err <= 0:
            continue
        depth = QUIET_SIGMA * p.flux_err
        if not _finite(p.flux) or p.flux < depth:
            out.append((p.mjd, depth))
    return out


def _bracket(first: PhotPoint, quiet: list[tuple[float, float]], bracket_days: float):
    window = [(m, d) for m, d in quiet if first.mjd - bracket_days <= m < first.mjd]
    deep = [m for m, d in window if d < first.flux]
    return (max(deep) if deep else None), bool(window)


# ------------------------------------------------------------------ the tests


def judge_new(
    survey: str,
    points: list[PhotPoint],
    now: float,
    lookback_days: float = LOOKBACK_DAYS,
    bracket_days: float = BRACKET_DAYS,
    min_detections: int = MIN_DETECTIONS,
    old_ids: list[str] | None = None,
    extra_quiet: list[tuple[float, float]] | None = None,
) -> NewVerdict:
    """
    Is this survey object new, on its own survey's history? extra_quiet (ZTF
    upper limits at the same position) is tried only when the object's own
    history cannot bracket the onset.
    """
    dets = detections(points)
    if not dets:
        return NewVerdict(survey, NOT_NEW, "no_detections")
    first = dets[0]
    v = NewVerdict(survey, NOT_NEW, "", first_det_mjd=first.mjd, n_det=len(dets))
    if old_ids:
        v.code = "preexisting_ztf_object"
    elif first.mjd < now - lookback_days:
        v.code = "old"
    elif len(dets) < min_detections:
        v.code = "too_few_detections"
    elif dets[-1].mjd - first.mjd < MIN_SPAN_DAYS:
        v.code = "single_night"
    if v.code:
        return v

    v.status = UNBRACKETED
    own, own_window = _bracket(first, quiet_points(points), bracket_days)
    if own is not None:
        v.status, v.code, v.last_quiet_mjd = NEW, "bracketed", own
        return v
    other, other_window = _bracket(first, extra_quiet or [], bracket_days)
    if other is not None:
        v.status, v.code, v.last_quiet_mjd = NEW, "bracketed_by_ztf", other
        return v
    v.code = "quiet_too_shallow" if (own_window or other_window) else "no_quiet_point"
    return v


def rising(points: list[PhotPoint], first_det_mjd: float) -> Rise | None:
    """
    Same band, earliest vs latest measured flux from the first detection on,
    forced photometry included: >= RISING_SIGMA and >= RISING_MIN_MAG over at
    least RISING_MIN_SPAN_DAYS. Forced photometry sits at a fixed position, so
    an asteroid leaves only zero flux behind and can never look like rising.
    """
    measured = [p for p in points if _finite(p.flux) and _finite(p.flux_err) and p.flux_err > 0
                and p.mjd >= first_det_mjd - 1e-6]
    best = None
    for band in {p.band for p in measured}:
        pts = sorted((p for p in measured if p.band == band), key=lambda p: p.mjd)
        early, late = pts[0], pts[-1]
        if late.mjd - early.mjd < RISING_MIN_SPAN_DAYS or late.flux <= 0:
            continue
        sigma = (late.flux - early.flux) / math.hypot(early.flux_err, late.flux_err)
        mag = 2.5 * math.log10(late.flux / early.flux) if early.flux > 0 else math.inf
        if sigma >= RISING_SIGMA and mag >= RISING_MIN_MAG and (best is None or sigma > best.sigma):
            best = Rise(band, mag, sigma, late.mjd - early.mjd)
    return best


def rapid_rise(points: list[PhotPoint], now: float, window: float = RAPID_RISE_DAYS) -> float | None:
    """
    Largest same-band brightening (mag) ending on a band's latest detection,
    against detections up to `window` days before it; that latest detection
    must fall within `window` days of now. Detections only. None if no pair.
    """
    best = None
    dets = [p for p in detections(points) if p.flux > 0]
    for band in {p.band for p in dets}:
        pts = [p for p in dets if p.band == band]
        latest = pts[-1]
        if latest.mjd < now - window:
            continue
        prior = [p for p in pts if latest.mjd - window <= p.mjd < latest.mjd]
        if not prior:
            continue
        rise = max(njy_to_mag(p.flux) for p in prior) - njy_to_mag(latest.flux)
        best = rise if best is None else max(best, rise)
    return best


# ------------------------------------------------------------------ one object


def associate(objs: list[SurveyObject], radius_arcsec: float = ASSOC_RADIUS_ARCSEC) -> dict[str, list[SurveyObject]]:
    """
    Other survey objects at the same place (any survey), by key. Sorted on Dec,
    each object is compared only with those inside a Dec window of the radius
    (astropy's search_around_sky would need scipy, which is not a dependency).
    """
    out = {o.key: [] for o in objs}
    if len(objs) < 2:
        return out
    order = sorted(range(len(objs)), key=lambda i: objs[i].dec)
    decs = [objs[i].dec for i in order]
    r_deg = radius_arcsec / 3600
    for pos, i in enumerate(order):
        a = objs[i]
        for j in order[bisect.bisect_left(decs, a.dec - r_deg):bisect.bisect_right(decs, a.dec + r_deg)]:
            if j != i and sep_deg(a.ra, a.dec, objs[j].ra, objs[j].dec) <= r_deg:
                out[a.key].append(objs[j])
    return out


def history_context(obj: SurveyObject, associates: list[SurveyObject], since_mjd: float) -> list[str]:
    """What else has been seen here. Context only, never a gate."""
    flags = []
    for a in associates:
        dets = detections(a.points)
        if a.survey == "ztf" and ztf_id_is_old(a.survey_object_id, since_mjd):
            flags.append(f"recurrent: {a.key}, a ZTF object from 20{a.survey_object_id[3:5]}")
        elif dets and dets[0].mjd < since_mjd:
            flags.append(f"recurrent: {a.key} detected here since {_date(dets[0].mjd)}")
        elif dets:
            flags.append(f"also seen as {a.key}, first detection {_date(dets[0].mjd)}")
    return flags


def evaluate(
    obj: SurveyObject,
    now: float,
    since_mjd: float,
    associates: list[SurveyObject] = (),
    bracket_days: float = BRACKET_DAYS,
    min_detections: int = MIN_DETECTIONS,
    classify=None,
) -> Evaluation:
    """Judge one survey object. The DR10 lookup runs only for objects worth reporting."""
    classify = classify or sky_catalog.classify
    gal_b = galactic_latitude(obj.ra, obj.dec)
    if abs(gal_b) < MIN_ABS_GAL_LAT:
        return Evaluation(reason="galactic_plane")
    if obj.sso_id:
        return Evaluation(reason="solar_system")
    if not detections(obj.points):
        return Evaluation(reason="no_photometry" if obj.fetch_errors else "no_detections")

    old_ids = [obj.survey_object_id] if obj.survey == "ztf" and ztf_id_is_old(obj.survey_object_id, since_mjd) else []
    extra = [q for a in associates if a.survey == "ztf" for q in quiet_points(a.points)] if obj.survey == "lsst" else None
    v = judge_new(obj.survey, obj.points, now, now - since_mjd, bracket_days, min_detections, old_ids, extra)
    recent = v.first_det_mjd is not None and v.first_det_mjd >= since_mjd and not old_ids
    rise = rising(obj.points, v.first_det_mjd) if recent else None
    rapid = rapid_rise(obj.points, now)
    rapid_hit = rapid is not None and rapid >= RAPID_RISE_MAG
    moving = rise is not None or rapid_hit

    if v.status == NOT_NEW and not moving:
        return Evaluation(reason=NOT_NEW_REASON.get(v.code, v.code))

    match = classify(obj.ra, obj.dec)
    if match.verdict == "stellar" and not moving:
        return Evaluation(reason="stellar")
    parked = v if v.status == UNBRACKETED else None
    if v.status == UNBRACKETED and not moving:
        return Evaluation(reason="unbracketed", parked=parked)

    context = history_context(obj, associates, since_mjd)
    if v.status == NEW:
        category = NEW_CATEGORY[match.verdict]
        conditions = [category] + (["rising"] if rise else []) + (["rapid_rise"] if rapid_hit else [])
    else:
        category = "rising" if recent else "re_brightening"
        conditions = ((["rising"] if rise else []) + (["rapid_rise"] if rapid_hit else [])) if recent else ["re_brightening"]
        if parked:
            context.append(f"onset not bracketed ({parked.code}), parked")
    return Evaluation(
        candidate=Candidate(obj, gal_b, category, conditions, v, rise, rapid if rapid_hit else None, match, context),
        parked=parked,
    )


# ------------------------------------------------------------------ gathering


def needs_lsst_photometry(obj: SurveyObject, now: float, since_mjd: float) -> bool:
    """
    One ALeRCE call per LSST object is the run's main cost, so decide from the
    listing (or ANTARES detections) first: a recent onset, or recent enough
    activity for a rapid rise, with at least two detections.
    """
    dets = detections(obj.points)
    first = obj.summary.get("firstmjd") or (dets[0].mjd if dets else None)
    last = obj.summary.get("lastmjd") or (dets[-1].mjd if dets else None)
    n = obj.summary.get("n_det") or len(dets)
    if first is None or n < 2:
        return False
    return first >= since_mjd or (last is not None and last >= now - RAPID_RISE_DAYS)


def _merge(into: SurveyObject, new: SurveyObject) -> None:
    seen = {(p.mjd, p.band, p.detected) for p in into.points}
    into.points += [p for p in new.points if (p.mjd, p.band, p.detected) not in seen]
    into.broker_refs.update(new.broker_refs)
    for attr in ("tags", "classifications", "broker_matches", "tns", "fetch_errors"):
        have = getattr(into, attr)
        have += [x for x in getattr(new, attr) if x not in have]
    into.summary = into.summary or new.summary
    into.sso_id = into.sso_id or new.sso_id


def gather(cone: Cone, now: float, since_mjd: float, until_mjd: float | None, min_detections: int,
           use_alerce: bool, stats: dict) -> list[SurveyObject]:
    """Every survey object in the cone, with photometry, from all sources."""
    def keep(locus) -> bool:  # the cheapest cut, before the per-locus alerts call
        return abs(galactic_latitude(locus.ra, locus.dec)) >= MIN_ABS_GAL_LAT

    objs: dict[str, SurveyObject] = {}
    for o in discover_antares(cone, since_mjd, until_mjd, min_detections, keep_locus=keep,
                              ztf_fallback=fetch_alerce_ztf if use_alerce else None, stats=stats):
        if o.key in objs:
            _merge(objs[o.key], o)
        else:
            objs[o.key] = o
    if not use_alerce:
        return list(objs.values())

    listed, err = discover_alerce_lsst(cone, since_mjd, until_mjd)
    if err:
        print(f"{WARN_PREFIX}{err}", file=sys.stderr)
        stats["alerce_listing_error"] = err
    for o in listed:
        if o.key in objs:
            _merge(objs[o.key], o)
        else:
            objs[o.key] = o

    lsst = [o for o in objs.values() if o.survey == "lsst"]
    todo = [o for o in lsst if needs_lsst_photometry(o, now, since_mjd)]
    stats["lsst_not_fetched"] = len(lsst) - len(todo)
    if todo:
        print(f"Fetching LSST photometry from ALeRCE for {len(todo)} of {len(lsst)} LSST objects", flush=True)
    warned = 0
    for n, o in enumerate(todo, 1):
        if n % PROGRESS_EVERY == 0:
            print(f"  ... {n}/{len(todo)}", flush=True)
        points, sso, ferr = fetch_alerce_lsst(o.survey_object_id)
        stats["lsst_fetched"] = stats.get("lsst_fetched", 0) + 1
        if ferr is None:
            o.points = truncate(points, until_mjd)  # complete: detections + forced photometry
            o.sso_id = o.sso_id or sso
            o.broker_refs.setdefault("alerce", o.survey_object_id)
        else:
            o.fetch_errors.append(f"alerce lsst: {ferr}")
            stats["lsst_fetch_errors"] = stats.get("lsst_fetch_errors", 0) + 1
            if warned < WARN_LIMIT:
                print(f"{WARN_PREFIX}{o.key}: ALeRCE photometry unavailable ({ferr})", file=sys.stderr)
                warned += 1
    return list(objs.values())


# ------------------------------------------------------------------ state + report


def state_file(name: str, replay: bool) -> Path:
    return STATE_DIR / f"transient_monitor_{name}{'_replay' if replay else ''}.json"


def _load_state(path) -> dict:
    """{"last_mjd", "reported": {key: {...}}, "parked": {key: {...}}}, keyed on survey IDs."""
    tmp = path.with_suffix(".tmp")
    if tmp.exists():
        tmp.unlink()  # clean up orphaned temp from previous crash
    if path.exists():
        state = json.loads(path.read_text())
        state.setdefault("parked", {})
        return state
    return {"last_mjd": None, "reported": {}, "parked": {}}


def _save_state(path, state: dict) -> None:
    # logs/ is gitignored, so a fresh checkout has no such directory.
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.rename(path)


def update_parked(prev: dict, parked_now: dict, seen: set[str], since_mjd: float, now: float):
    """
    Returns (parked, resolved, expired). An object stays parked while its first
    detection is inside the window, even if it misses one run's listing; it is
    resolved when a later run sees it and no longer parks it, and expires
    unbracketed when the window passes it.
    """
    parked = {k: {**info, "parked_mjd": prev.get(k, {}).get("parked_mjd", now)} for k, info in parked_now.items()}
    resolved, expired = [], []
    for k, info in prev.items():
        if k in parked:
            continue
        if info["first_det_mjd"] < since_mjd:
            expired.append(k)
        elif k in seen:
            resolved.append(k)
        else:
            parked[k] = info
    return parked, resolved, expired


def _print_candidate(c: Candidate, new_conditions: list[str]) -> None:
    o, v = c.obj, c.onset
    print(f"{o.key}  [{', '.join(c.conditions)}]  new: {', '.join(new_conditions)}")
    print(f"  ra={o.ra:.5f}  dec={o.dec:.5f}  b={c.gal_b:.1f}")
    onset = f"first detection {_date(v.first_det_mjd)} (MJD {v.first_det_mjd:.2f}), {v.n_det} detections"
    if v.status == NEW:
        by = "a ZTF upper limit" if v.code == "bracketed_by_ztf" else "a quiet point"
        onset += f", bracketed by {by} {v.first_det_mjd - v.last_quiet_mjd:.1f} d before"
    elif v.status == UNBRACKETED:
        onset += f", not bracketed ({v.code})"
    print(f"  onset: {onset}")
    if c.rise:
        mag = "from nothing" if math.isinf(c.rise.mag) else f"+{c.rise.mag:.2f} mag"
        print(f"  rising: {c.rise.band} {mag}, {c.rise.sigma:.1f} sigma over {c.rise.days:.1f} d")
    if c.rapid is not None:
        print(f"  rapid rise: {c.rapid:.2f} mag within {RAPID_RISE_DAYS:g} d")
    print(f"  DR10: {c.match.verdict}" + (f" - {'; '.join(c.match.evidence)}" if c.match.evidence else ""))
    for line in c.context:
        print(f"  context: {line}")
    if o.broker_refs:
        print(f"  brokers: {', '.join(f'{b} {i}' for b, i in sorted(o.broker_refs.items()))}")
    for label, items in (("tags", sorted(o.tags)), ("classifications", o.classifications),
                         ("broker catalogues", sorted(o.broker_matches)), ("TNS", o.tns)):
        if items:
            print(f"  {label}: {', '.join(items)}")


def _add_tns(c: Candidate) -> None:
    locus = c.obj.broker_refs.get("antares")
    if locus and "tns_public_objects" in c.obj.broker_matches and not c.obj.tns:
        try:
            c.obj.tns = antares_tns(locus)
        except NETWORK_ERRORS as e:
            c.obj.fetch_errors.append(f"tns: {e}")


def scan(
    name: str,
    cone: Cone,
    lookback_days: float = LOOKBACK_DAYS,
    min_detections: int = MIN_DETECTIONS,
    bracket_days: float = BRACKET_DAYS,
    as_of_mjd: float | None = None,
    dry_run: bool = False,
    use_alerce: bool = True,
) -> int:
    """
    Scan the cone once, report survey objects reaching a condition not reported
    before, park those whose onset cannot be bracketed yet. --as-of replays a
    past date: nothing observed after it is used. Returns a process exit code.
    """
    path = state_file(name, as_of_mjd is not None)
    state = _load_state(path)
    reported = state["reported"]
    now = as_of_mjd if as_of_mjd is not None else now_mjd()
    since = now - lookback_days
    retry_budget.reset()  # ALeRCE calls draw on it

    ra, dec, radius = cone
    replay = f"  REPLAY as of {_date(now - 1e-6)}" if as_of_mjd is not None else ""
    print(f"\n\nFootprint {name}: cone RA {ra:g} Dec {dec:g} radius {radius:g} deg{replay}")
    print(f"Active since MJD {since:.1f} ({lookback_days:g} d); new = first detection within "
          f"{lookback_days:g} d, quiet point within {bracket_days:g} d before it")
    print(f"Previously reported: {len(reported)}  parked: {len(state['parked'])}\n")

    for attempt in range(MAX_RETRIES):
        stats: dict = {}
        try:
            objs = gather(cone, now, since, as_of_mjd, min_detections, use_alerce, stats)
            break
        except NETWORK_ERRORS as e:
            if attempt < MAX_RETRIES - 1:
                print(f"{WARN_PREFIX}network error (attempt {attempt + 1}/{MAX_RETRIES}): {e}; "
                      f"retrying in {RETRY_WAIT}s", file=sys.stderr)
                time.sleep(RETRY_WAIT)
            else:
                print(f"{ERROR_PREFIX}network error after {MAX_RETRIES} attempts: {e}. "
                      "State not updated - will retry next run.", file=sys.stderr)
                return 1

    links = associate(objs)
    candidates: list[Candidate] = []
    parked_now: dict = {}
    rejected: Counter = Counter()
    for o in objs:
        ev = evaluate(o, now, since, links[o.key], bracket_days, min_detections)
        if ev.parked is not None:
            parked_now[o.key] = {"survey": o.survey, "first_det_mjd": ev.parked.first_det_mjd,
                                 "code": ev.parked.code}
        if ev.candidate is None:
            rejected[ev.reason] += 1
        else:
            candidates.append(ev.candidate)

    prev_parked = state["parked"]
    parked, resolved, expired = update_parked(prev_parked, parked_now, {o.key for o in objs}, since, now)

    alerts = []
    for c in candidates:
        k = c.obj.key
        if k in resolved:
            c.context.append(f"was parked since {_date(prev_parked[k]['parked_mjd'])}")
        prev = reported.get(k, {})
        seen = set(prev.get("conditions", []))
        new_conditions = [x for x in c.conditions if x not in seen]
        if new_conditions:
            alerts.append((c, new_conditions))
        reported[k] = {"first_reported_mjd": prev.get("first_reported_mjd", now), "category": c.category,
                       "conditions": sorted(seen | set(c.conditions))}

    by_survey = Counter(o.survey for o in objs)
    print(f"ANTARES loci: {stats.get('loci', 0)}  (skipped at |b| < {MIN_ABS_GAL_LAT:g}: "
          f"{stats.get('loci_skipped', 0)}, duplicate listing entries: {stats.get('duplicates', 0)})")
    if use_alerce:
        print(f"LSST photometry from ALeRCE: fetched {stats.get('lsst_fetched', 0)}, "
              f"not needed {stats.get('lsst_not_fetched', 0)}, failed {stats.get('lsst_fetch_errors', 0)}")
    print(f"Survey objects: {len(objs)} ({', '.join(f'{s} {n}' for s, n in sorted(by_survey.items()))})  "
          f"kept: {len(candidates)}")
    for reason, n in sorted(rejected.items()):
        print(f"  rejected {reason}: {n}")
    newly_parked = sorted(k for k in parked_now if k not in prev_parked)
    print(f"Parked, onset not bracketed: {len(parked)}  "
          f"(new {len(newly_parked)}, resolved {len(resolved)}, expired unbracketed {len(expired)})")
    for k in newly_parked:
        info = parked_now[k]
        print(f"  {k}  first detection {_date(info['first_det_mjd'])}  {info['code']}")
    print()

    if alerts:
        print(f"=== {len(alerts)} TRANSIENT ALERTS ===\n")
        for category in CATEGORY_ORDER:
            group = [a for a in alerts if a[0].category == category]
            if not group:
                continue
            print(f"--- {category} ({len(group)}) ---")
            for c, new_conditions in group:
                _add_tns(c)
                _print_candidate(c, new_conditions)
            print()
    else:
        print("No new transient alerts.")

    cutoff = now - STATE_RETENTION_DAYS
    reported = {k: v for k, v in reported.items() if v["first_reported_mjd"] >= cutoff}
    if dry_run:
        print("\n(dry run: state not saved)")
    else:
        _save_state(path, {"last_mjd": now, "reported": reported, "parked": parked})
    return 0


# ------------------------------------------------------------------ CLI


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan a sky cone for new transients (SNe, TDEs, orphans).")
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--footprint", choices=sorted(FOOTPRINTS),
                       help="; ".join(f"{k}: {v[1]}" for k, v in FOOTPRINTS.items()))
    where.add_argument("--cone", nargs=3, type=float, metavar=("RA", "DEC", "RADIUS"),
                       help="cone in degrees")
    parser.add_argument("--radius", type=float, help=f"override a footprint's radius (default {DEFAULT_RADIUS_DEG:g} deg)")
    parser.add_argument("--as-of", metavar="YYYY-MM-DD", help="replay: judge as of the end of this UTC date")
    parser.add_argument("--lookback-days", type=float, default=LOOKBACK_DAYS,
                        help=f"N: a first detection this recent can be new (default {LOOKBACK_DAYS:g})")
    parser.add_argument("--bracket-days", type=float, default=BRACKET_DAYS,
                        help=f"X: a quiet point this close before it brackets the onset (default {BRACKET_DAYS:g})")
    parser.add_argument("--min-detections", type=int, default=MIN_DETECTIONS,
                        help=f"per survey object (default {MIN_DETECTIONS})")
    parser.add_argument("--no-alerce", action="store_true",
                        help="ANTARES only: no LSST forced photometry, no ZTF fallback")
    parser.add_argument("--dry-run", action="store_true", help="report without saving state")
    args = parser.parse_args(argv)

    if args.footprint:
        args.name = args.footprint
        ra, dec, radius = FOOTPRINTS[args.footprint][0]
        args.cone = (ra, dec, args.radius if args.radius is not None else radius)
    else:
        if args.radius is not None:
            parser.error("--radius goes with --footprint; --cone carries its own")
        args.name = "cone_{:g}_{:g}_{:g}".format(*args.cone)
        args.cone = tuple(args.cone)
    ra, dec, radius = args.cone
    if not 0 <= ra < 360:
        parser.error("RA must be in [0, 360)")
    if not -90 <= dec <= 90:
        parser.error("Dec must be in [-90, 90]")
    if not 0 < radius <= 10:
        parser.error("radius must be in (0, 10] deg")
    if args.lookback_days <= 0 or args.bracket_days <= 0:
        parser.error("--lookback-days and --bracket-days must be positive")
    if args.min_detections < 1:
        parser.error("--min-detections must be at least 1")
    args.as_of_mjd = None
    if args.as_of:
        try:
            args.as_of_mjd = date_to_mjd(args.as_of)
        except ValueError:
            parser.error("--as-of must be YYYY-MM-DD")
    return args


def main(argv: list[str] | None = None) -> int:
    a = _parse_args(argv)
    return scan(a.name, a.cone, a.lookback_days, a.min_detections, a.bracket_days, a.as_of_mjd,
                a.dry_run, not a.no_alerce)


if __name__ == "__main__":
    sys.exit(main())
