"""
transient_monitor.py - daily scan of a sky cone for new transients.
Primary targets: SNe, TDEs, orphan transients. Broker-neutral: brokers are
data sources (photometry.py); this module decides what is interesting, on
photometry, per survey object (a ZTF objectId, an LSST diaObjectId).

  discover   every survey object in the cone active within N days
             (ALeRCE cone listings of ZTF and LSST; light curves from ALeRCE)
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
  classify   DR10 locally (sky_catalog.py). The sky context alone decides the
             category, whatever the path: host -> host, nothing -> orphan,
             point-like within 1" -> point_source, Gaia star -> stellar, catalogue
             gap or no catalogue -> unchecked; WISE AGN colours on the matched
             source within 1" -> agn, on a host farther out -> a context note.
             The path (bracketed, rising, rapid_rise, re_brightening) is the
             confidence label: a report reads "host · rising"
  report     broker classifications and TNS (ALeRCE's TNS service, asked
             again for 30 days) are printed, never filtered on

Each survey object is reported again whenever its (category, path) pair is
new: a new path, or a new category (unchecked becoming a real verdict). State is keyed
on survey IDs, in state/transient_monitor_<footprint>.json.

    uv run python -m rubin_qa.transient_monitor --footprint D --dry-run
    uv run python -m rubin_qa.transient_monitor --cone RA DEC RADIUS
    uv run python -m rubin_qa.transient_monitor --footprint D --replay 2026-03-12 2026-05-15 [--estimate | --compare-cuts]

Run it with uv run (the project's environment) and -m, not by file path: it uses the
package's relative imports.
"""

import argparse
import hashlib
import inspect
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

from . import retry_budget, sky_catalog, tns
from .config import CONFIRM_THRESHOLD_SECONDS, ERROR_PREFIX, MJD_J2000, PROJECT_ROOT, WARN_PREFIX, now_mjd
from .photometry import (  # noqa: F401  (NETWORK_ERRORS re-exported for tools/)
    NETWORK_ERRORS,
    Cone,
    PhotPoint,
    SurveyObject,
    alerce_newest_in_cone,
    discover_alerce,
    fetch_alerce_lsst,
    fetch_alerce_ztf,
    njy_to_mag,
    sep_deg,
    truncate,
)

LOOKBACK_DAYS = 14.0  # N: a survey's first detection must be this recent to be new
# An empty LSST listing is checked against ALeRCE's newest LSST object in the cone, asked
# with lastmjd >= min(window start, last known newest - this): the known object must come
# back, so silence there means the check failed, not that the sky is quiet.
LISTING_CHECK_MARGIN_DAYS = 1.0
LIVE_SURVEYS = ("ztf", "lsst")  # both listed and fetched from ALeRCE (2026-10-03)
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
# Since 2026-10-01 this is a precondition of every path to a report (new, rising,
# rapid rise), not only of "new": rising could pass on one night of detections plus
# brighter forced photometry, and rapid_rise on two detections the same night. The
# cost is a genuine transient reported on its second night instead of its first. It
# also makes the replay's candidate cut (detection span) lossless by definition: the
# span only grows, so an object below it today was below it at every replayed date.
MIN_SPAN_DAYS = 0.5
MIN_ABS_GAL_LAT = 20.0  # |b| below this: dust, crowding, stellar variables
RAPID_RISE_MAG = 1.0  # kilonova-like: >= 1 mag brighter within RAPID_RISE_DAYS
RAPID_RISE_DAYS = 3.0
RISING_SIGMA = 3.0  # rising: latest minus earliest post-onset flux, in combined sigma
RISING_MIN_MAG = 0.3  # ... and at least this much brighter
RISING_MIN_SPAN_DAYS = 0.5  # ... measured on at least two nights
ASSOC_RADIUS_ARCSEC = 1.5  # survey objects this close are the same place
STATE_RETENTION_DAYS = 60.0
# State, not logs: the per-footprint state and the point-source watch hold what must
# outlive a run (reported pairs, parked onsets, the watch's evidence, kept past the 60-day
# retention). logs/ is the directory people clean up and rotate, so a tidy-up there would
# silently reset them (2026-10-02). Written like the state always was: temp file, rename.
STATE_DIR = PROJECT_ROOT / "state"
MAX_RETRIES = 3
RETRY_WAIT = 300  # 5 minutes
WARN_LIMIT = 5  # per-object fetch warnings printed per run; the rest are counted
PROGRESS_EVERY = 100  # LSST photometry fetches between progress lines

DEFAULT_RADIUS_DEG = 2.82  # pi r^2 = 25 deg2; membership is angular separation <= radius
FOOTPRINTS = {
    "C": ((35.7, -9.0, DEFAULT_RADIUS_DEG), "former live candidate v1: just south of the XMM-LSS deep field"),
    "D": ((150.1, 2.5, DEFAULT_RADIUS_DEG), "test replay: COSMOS, Rubin data 2026-02-15..05-15"),
    "ecdfs": ((53.1, -27.8, DEFAULT_RADIUS_DEG), "the live footprint: ECDFS deep field"),
}
LIVE_FOOTPRINT = "ecdfs"  # decided 2026-10-02; scanned when neither --footprint nor --cone is given

# NewVerdict.status
NEW, UNBRACKETED, NOT_NEW = "new", "unbracketed", "not_new"
NOT_NEW_REASON = {
    "no_detections": "no_detections",
    "preexisting_ztf_object": "preexisting_ztf_object",
    "old": "old_no_rise",
    "too_few_detections": "too_few_detections",
    "single_night": "single_night",
}
# The decision table (user's design, 2026-10-01). Two separate axes:
#   sky context - what the object sits on: decides the category, whatever the path
#   path        - why it is believed new (bracketed, rising, rapid_rise) or that an old
#                 object woke up (re_brightening): the confidence label, Candidate.conditions
# Every sky context leads to a visible category: no "dropped" cell, since a silently
# dropped object (a real transient wrongly matched to a foreground star) cannot be audited.
# A verdict/flag combination missing here raises instead of falling through; the tests
# enumerate every (path, sky context) pair. Names claim only the sky: "new_candidate",
# "agn_flare" and "stellar_flare" claimed a path too (the stars it caught rose over up
# to two weeks: stellar variability, not flares).
SKY_CATEGORY = {
    "host": "host",
    "host+agn_host": "host",  # AGN colours of a host farther than 1": a note; an SN in an AGN host's disk is no AGN event
    "host+agn_nuclear": "agn",
    "point_source": "point_source",
    "point_source+agn_nuclear": "agn",
    "none": "orphan",
    "stellar": "stellar",
    "unavailable": "unchecked",
}
CATEGORY_ORDER = ("host", "agn", "orphan", "point_source", "stellar", "unchecked")
PATHS = ("bracketed", "rising", "rapid_rise", "re_brightening")


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
    category: str  # one of CATEGORY_ORDER: the sky context
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


def _finite(x) -> bool:
    return x is not None and math.isfinite(x)


def detections(points: list[PhotPoint]) -> list[PhotPoint]:
    return sorted((p for p in points if p.detected and _finite(p.flux)), key=lambda p: p.mjd)


def spans_nights(first_det_mjd: float, last_det_mjd: float) -> bool:
    """Detections on more than one night: required for every report, and the replay's candidate cut."""
    return last_det_mjd - first_det_mjd >= MIN_SPAN_DAYS


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
    # against the FIRST detection, never the brightest so far (decided 2026-10-02): a shallow limit
    # would then pass and prove only a rise. Pinned by test_a_limit_shallower_than_the_first_detection_...
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
    elif not spans_nights(first.mjd, dets[-1].mjd):
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


def sky_context(match: sky_catalog.Crossmatch) -> str:
    """The decision table's sky key: the verdict plus any AGN flag on the assigned source."""
    key = match.verdict + "".join(f"+{f}" for f in match.flags if f.startswith("agn_"))
    if key not in SKY_CATEGORY:
        raise ValueError(f"sky context missing from the decision table: {match.verdict!r} with flags {match.flags}")
    return key


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
    dets = detections(obj.points)
    if not dets:
        # an object the listing says has detections but was not worth a light-curve call
        # (needs_photometry) is "not_fetched", not "no_detections"
        return Evaluation(reason="no_photometry" if obj.fetch_errors else
                          "not_fetched" if obj.summary.get("n_det") else "no_detections")
    if not spans_nights(dets[0].mjd, dets[-1].mjd):  # every path, rising and rapid rise included
        return Evaluation(reason="single_night")

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

    parked = v if v.status == UNBRACKETED else None
    if v.status == UNBRACKETED and not moving:
        return Evaluation(reason="unbracketed", parked=parked)

    match = classify(obj.ra, obj.dec)
    category = SKY_CATEGORY[sky_context(match)]
    context = history_context(obj, associates, since_mjd)
    if "agn_host" in match.flags:
        context.append("host has WISE AGN colours")
    moves = (["rising"] if rise else []) + (["rapid_rise"] if rapid_hit else [])
    if v.status == NEW:
        conditions = ["bracketed"] + moves
    else:
        conditions = moves if recent else ["re_brightening"]
        if parked:
            context.append(f"onset not bracketed ({parked.code}), parked")
    return Evaluation(
        candidate=Candidate(obj, gal_b, category, conditions, v, rise, rapid if rapid_hit else None, match, context),
        parked=parked,
    )


# ------------------------------------------------------------------ gathering


def needs_photometry(obj: SurveyObject, now: float, since_mjd: float) -> bool:
    """
    One ALeRCE light-curve call per object is the run's main cost, so decide from
    the listing first: a recent onset, or recent enough activity for a rapid rise,
    with at least two detections.
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
    for attr in ("classifications", "tns", "fetch_errors"):
        have = getattr(into, attr)
        have += [x for x in getattr(new, attr) if x not in have]
    into.summary = into.summary or new.summary
    into.sso_id = into.sso_id or new.sso_id


@dataclass
class ListingCheck:
    """What the LSST listing said, and whether its silence can be trusted."""
    status: str  # "objects" | "quiet" | "failed" | "not_asked"
    detail: str
    newest_mjd: float | None = None  # latest lastmjd of the survey known in the cone, for the next run's check


def check_listing(survey: str, listed: list[SurveyObject], err: str | None, cone: Cone, since_mjd: float,
                  known_newest: float | None, probe=None) -> ListingCheck:
    """
    Tell "no new data upstream" from "the listing failed" (LSST 2026-10-02, ZTF too since
    2026-10-03). With Rubin off sky an empty LSST listing is the normal state for weeks,
    and ALeRCE can also answer a listing with 200 and zero items when it fails; the two
    must not look alike. An empty listing is checked against a known answer: ALeRCE's
    newest object of the survey in the cone (lastmjd >= min(window start, known newest -
    margin), so the known one must come back).
    """
    probe = probe or (lambda cone, floor: alerce_newest_in_cone(survey, cone, floor))
    label, since = survey.upper(), _date(since_mjd)
    if err:
        return ListingCheck("failed", f"{err} (objects gathered before it are kept)", known_newest)
    if listed:
        newest = max([known_newest or 0.0] + [float(o.summary.get("lastmjd") or 0.0) for o in listed])
        return ListingCheck("objects", f"{len(listed)} objects active since {since}", newest)
    floor = None if known_newest is None else min(since_mjd, known_newest - LISTING_CHECK_MARGIN_DAYS)
    item, perr = probe(cone, floor)
    if perr:
        return ListingCheck("failed", f"empty listing, and the check could not run: {perr}", known_newest)
    if item is None:
        what = (f"ALeRCE returned no {label} object in this cone at all: never observed, or the listing service "
                "failed - cannot tell which" if floor is None else
                f"the known newest object (lastmjd {known_newest:.2f}, {_date(known_newest)}) did not come back: "
                "the listing service is not answering")
        return ListingCheck("failed", f"empty listing, unverified: {what}", known_newest)
    newest = float(item["lastmjd"])
    if newest >= since_mjd:
        return ListingCheck("failed", f"empty listing, yet ALeRCE holds {survey}:{item['oid']} detected "
                                      f"{_date(newest)}, inside the window: the listing failed silently", newest)
    return ListingCheck("quiet", f"none active since {since}; upstream quiet: newest {label} detection in the cone "
                                 f"{_date(newest)} ({survey}:{item['oid']}, checked)", max(newest, known_newest or 0.0))


def gather(cone: Cone, now: float, since_mjd: float, until_mjd: float | None, stats: dict,
           newest: dict | None = None) -> list[SurveyObject]:
    """
    Every survey object in the cone, with photometry, all from ALeRCE: each survey's
    listing (stats["<survey>_listing"]: a ListingCheck), then one light-curve call per
    object the listing says is worth it. newest: the latest lastmjd known per survey.
    """
    newest = newest or {}
    objs: dict[str, SurveyObject] = {}
    for survey in LIVE_SURVEYS:
        listed, err = discover_alerce(survey, cone, since_mjd, until_mjd)
        stats[f"{survey}_listing"] = check_listing(survey, listed, err, cone, since_mjd, newest.get(survey))
        for o in listed:
            if o.key in objs:
                _merge(objs[o.key], o)
            else:
                objs[o.key] = o

    todo = [o for o in objs.values() if needs_photometry(o, now, since_mjd)]
    stats["not_fetched"] = len(objs) - len(todo)
    if todo:
        print(f"Fetching photometry from ALeRCE for {len(todo)} of {len(objs)} objects", flush=True)
    warned = 0
    for n, o in enumerate(todo, 1):
        if n % PROGRESS_EVERY == 0:
            print(f"  ... {n}/{len(todo)}", flush=True)
        if o.survey == "lsst":
            points, sso, ferr = fetch_alerce_lsst(o.survey_object_id)  # detections + forced photometry
        else:
            (points, ferr), sso = fetch_alerce_ztf(o.survey_object_id), None  # detections + upper limits
        stats["fetched"] = stats.get("fetched", 0) + 1
        if ferr is None:
            o.points = truncate(points, until_mjd)
            o.sso_id = o.sso_id or sso
        else:
            o.fetch_errors.append(f"alerce {o.survey}: {ferr}")
            stats["fetch_errors"] = stats.get("fetch_errors", 0) + 1
            if warned < WARN_LIMIT:
                print(f"{WARN_PREFIX}{o.key}: ALeRCE photometry unavailable ({ferr})", file=sys.stderr)
                warned += 1
    return list(objs.values())


# ------------------------------------------------------------------ state + report


def state_file(name: str) -> Path:
    return STATE_DIR / f"transient_monitor_{name}.json"


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
    # state/ is gitignored, so a fresh checkout has no such directory.
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


def _print_candidate(c: Candidate, new_conditions: list[str], tns: str | None = None) -> None:
    o, v = c.obj, c.onset
    print(f"{o.key}  {c.category} · {', '.join(c.conditions)}  new: {', '.join(new_conditions)}")
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
    if o.classifications:
        print(f"  classifications: {', '.join(o.classifications)}")
    if tns is not None:
        print(f"  TNS: {tns}")


# TNS, via ALeRCE's TNS service (rubin_qa/tns.py; ANTARES's crossmatch until 2026-10-03,
# which left every LSST object without a locus "not checked"). A "none" only means nobody
# had reported the object yet - SN 2026kfw: our replay alert came 5 days before its TNS
# report - so "none" is asked again, like "unavailable", every run for TNS_RECHECK_DAYS.
TNS_RECHECK_DAYS = 30.0
TNS_MATCH_ARCSEC = 2.0
TNS_NONE_YET = f"none yet; asked again each run for {TNS_RECHECK_DAYS:g} d (a report often comes days after an alert)"
TNS_UNAVAILABLE = f"unavailable (lookup failed); asked again each run for {TNS_RECHECK_DAYS:g} d"

# The point-source watch (user, 2026-10-02). A S/N floor for the point-source rule was
# measured and not adopted: its window rested on one supernova, SN 2026kfw, a spectroscopic
# SN Ia on a DR10 point source of S/N 6.3. The watch counts the pattern instead: every
# point_source alert, live or replayed, is looked up in TNS until classified (TNS
# classifies days to weeks after discovery, so it looks back, weekly per alert), and each
# spectroscopic supernova (TNS prefix SN) on a point source below PS_REVISIT_SNR counts.
# One is kfw, two can be coincidence, PS_PATTERN is a pattern: from then on the report
# carries a notice every run. Findings are kept with a fingerprint of the point-source rule,
# so changing the rule (or PS_PATTERN) is what stops the notice. Never fails the run.
PS_WATCH_FILE = "point_source_watch.json"  # under STATE_DIR, shared by every footprint and replay
PS_REVISIT_SNR = 7.0  # the top of the measured window (2026-10-02, tools/point_source_snr.py)
PS_PATTERN = 3
PS_CHECK_EVERY_DAYS = 7.0  # a TNS lookup paces at 6.5 s; a classification takes days to weeks
PS_WATCH_DAYS = 180.0  # unclassified this long after the alert: no longer asked
PS_MATCH_ARCSEC = 2.0
# TNS records are judged on their classification type, never the prefix: unclassified is
# AT with type {"id": None, "name": None}; a supernova has an SN type ("SN Ia", "SLSN-I")
# and the SN prefix; other classes may keep AT or get their own prefix (AT 2018hyz, a TDE,
# comes back as prefix TDE, type TDE; checked 2026-10-02).
SUPERNOVA_TYPES = ("SN", "SLSN")
NUCLEAR_TYPES = ("TDE", "AGN", "QSO")  # the nuclear cases the point_source label exists for


def point_source_rule() -> str:
    """Fingerprint of the point-source rule as it stands: what the counted findings were judged under."""
    text = "".join(inspect.getsource(f) for f in (sky_catalog.point_counterpart, sky_catalog._verdict))
    return hashlib.sha256(f"{text}{sky_catalog.COUNTERPART_RADIUS_ARCSEC}".encode()).hexdigest()[:12]


def point_source_watch(alerts: list[tuple[str, float, float, float, str]], now: float, save: bool = True) -> list[str]:
    """
    Register this run's point_source alerts ((key, ra, dec, alerted_mjd, source)), look up
    the watched ones that are due, and return the report lines. Never raises: a failure is
    a line, asked again next run.
    """
    try:
        return _point_source_watch(alerts, now, save)
    except Exception as e:  # display context: it must never take the run down
        return [f"Point-source watch: could not run ({type(e).__name__}: {e}); asked again next run"]


def _point_source_watch(alerts, now, save) -> list[str]:
    path = STATE_DIR / PS_WATCH_FILE
    objects = json.loads(path.read_text())["objects"] if path.exists() else {}
    for key, ra, dec, mjd, source in alerts:
        objects.setdefault(key, {"ra": ra, "dec": dec, "alerted_mjd": mjd, "source": source,
                                 "status": "watching", "checked_mjd": None})
    rule, unavailable, classified_now = point_source_rule(), 0, []
    for key, e in sorted(objects.items()):
        if e["status"] != "watching" or (e["checked_mjd"] is not None and now - e["checked_mjd"] < PS_CHECK_EVERY_DAYS):
            continue
        rec = tns.tns_lookup(e["ra"], e["dec"])
        if rec is None:
            unavailable += 1
            continue
        if rec and sep_deg(e["ra"], e["dec"], rec["radeg"], rec["decdeg"]) * 3600 <= PS_MATCH_ARCSEC:
            e["tns"] = f"{rec.get('name_prefix', '')} {rec['objname']}".strip()
            e["tns_type"] = (rec.get("object_type") or {}).get("name")
            if e["tns_type"] and e["tns_type"].startswith(SUPERNOVA_TYPES):  # classified a supernova
                try:
                    snr = sky_catalog.counterpart_snr(e["ra"], e["dec"])
                except sky_catalog.CatalogError:
                    unavailable += 1
                    continue
                e.update(status="sn" if snr is not None else "no_counterpart", snr=snr, rule=rule)
            elif e["tns_type"]:  # classified as something else: settled, and said, never a silent drop
                e["status"] = "other_class"
                classified_now.append((key, e))
        e["checked_mjd"] = now
        if e["status"] == "watching" and now - e["alerted_mjd"] > PS_WATCH_DAYS:
            e["status"] = "expired"
    if save:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps({"objects": objects}, indent=1))
        tmp.rename(path)

    hits = {e["tns"]: e for e in objects.values() if e["status"] == "sn" and e["snr"] < PS_REVISIT_SNR}
    counted = sorted(n for n, e in hits.items() if e["rule"] == rule)
    older = len(hits) - len(counted)
    watching = sum(e["status"] == "watching" for e in objects.values())
    other = Counter(e["tns_type"] for e in objects.values() if e["status"] == "other_class")
    found = ", ".join(f"{n} S/N {hits[n]['snr']:.1f}" for n in counted)
    lines = [f"Point-source watch: {watching} point_source alerts awaiting a TNS classification; spectroscopic "
             f"supernovae on a point source below S/N {PS_REVISIT_SNR:g}: {len(counted)} of {PS_PATTERN}"
             + (f" ({found})" if found else "")
             + (f"; {older} more under an earlier version of the rule, not counted" if older else "")
             + (f"; classified as other than supernovae: {', '.join(f'{n} {k}' for k, n in sorted(other.items()))}"
                if other else "")
             + (f"; {unavailable} lookups unavailable, asked again next run" if unavailable else "")]
    for key, e in classified_now:
        nuclear = e["tns_type"].startswith(NUCLEAR_TYPES)
        lines.append(f"  {key}, a point_source alert, is classified in TNS: {e['tns']}, {e['tns_type']}"
                     + (" - the nuclear case the point_source label exists for" if nuclear else ""))
    if len(counted) >= PS_PATTERN:
        lines.append(f"NOTICE: {len(counted)} spectroscopic supernovae labelled point_source on DR10 point sources below "
                     f"S/N {PS_REVISIT_SNR:g} - a pattern, not one object. Measure the point-source S/N floor again "
                     "(tools/point_source_snr.py). Repeated each run until the rule or PS_PATTERN changes.")
    return lines


def _tns_match(rec: dict, ra: float, dec: float) -> bool:
    return bool(rec) and sep_deg(ra, dec, rec["radeg"], rec["decdeg"]) * 3600 <= TNS_MATCH_ARCSEC


def _tns_line(rec: dict) -> str:
    kind = (rec.get("object_type") or {}).get("name")
    found = (rec.get("discoverydate") or "")[:10]
    return (f"{rec.get('name_prefix', '')} {rec['objname']}".strip() + (f", {kind}" if kind else "")
            + (f", discovered {found}" if found else ""))


def _add_tns(c: Candidate, now: float) -> tuple[str, dict | None]:
    """
    The alert's TNS line, looked up after its verdict: TNS is display context, never an
    input. Returns (line, watch entry): the record, or TNS_NONE_YET / TNS_UNAVAILABLE with
    an entry scan keeps in the state and asks again each run for TNS_RECHECK_DAYS.
    """
    entry = {"ra": c.obj.ra, "dec": c.obj.dec, "alerted_mjd": now}
    try:
        rec = tns.tns_lookup(c.obj.ra, c.obj.dec)  # "none" only when the canary vouched for it
    except Exception as e:  # display context: whatever goes wrong, the run goes on
        print(f"{WARN_PREFIX}{c.obj.key}: TNS lookup failed ({type(e).__name__}: {e})", file=sys.stderr)
        rec = None
    if rec is None:
        print(f"{WARN_PREFIX}{c.obj.key}: TNS unavailable; asked again next run", file=sys.stderr)
        return TNS_UNAVAILABLE, entry
    if _tns_match(rec, c.obj.ra, c.obj.dec):
        c.obj.tns = [_tns_line(rec)]
        return c.obj.tns[0], None
    return TNS_NONE_YET, entry


def _retry_tns(watch: dict, now: float) -> tuple[dict, list[str]]:
    """
    Ask TNS again for earlier alerts that had no record yet (or no answer). One paced call
    each, no canary: an empty answer only means "not yet" whatever its cause; only a record
    is news. Returns (still watched, report lines).
    """
    still, lines = {}, []
    for key, e in sorted(watch.items()):
        if "ra" not in e:  # an ANTARES-locus entry from before 2026-10-03: cannot be asked by position
            continue
        when = f"{key} (alert {_date(e['alerted_mjd'])})"
        if now - e["alerted_mjd"] > TNS_RECHECK_DAYS:
            lines.append(f"  {when}: no TNS record {TNS_RECHECK_DAYS:g} d after the alert; no longer asked")
            continue
        time.sleep(tns.TNS_PAUSE)
        try:
            rec = tns.tns_once(e["ra"], e["dec"])
        except Exception:  # display context: kept, asked again next run
            rec = {}
        if _tns_match(rec, e["ra"], e["dec"]):
            lag = ""
            if rec.get("discoverydate"):
                days = date_to_mjd(rec["discoverydate"][:10]) - 1 - e["alerted_mjd"]
                lag = f" ({abs(days):.0f} d {'after' if days > 0 else 'before'} our alert)"
            lines.append(f"  {when}: now in TNS: {_tns_line(rec)}{lag}")
        else:
            still[key] = e
    return still, lines


def footprint_classifier(cone: Cone):
    """
    sky_catalog.classify bound to the footprint's Siena Galaxy Atlas (step 2 of the host
    search), loaded on the first DR10 lookup: one query per footprint and catalogue
    version, then from the cache, with the footprint's DR10 brick list for the
    catalogue-gap check. If either cannot be loaded the run goes on without it: it says
    so once, and every crossmatch made without it says so too, so an orphan from an
    outage is never mistaken for a real one.
    """
    loaded: dict = {}

    def classify(ra: float, dec: float):
        if "atlas" not in loaded:
            try:
                loaded["atlas"] = sky_catalog.footprint_atlas(cone)
            except sky_catalog.CatalogError as e:
                loaded["atlas"] = None
                print(f"{WARN_PREFIX}Siena Galaxy Atlas unavailable ({e}): hosts searched within "
                      f'{sky_catalog.SEARCH_RADIUS_ARCSEC:g}" only this run', file=sys.stderr)
            try:
                loaded["bricks"] = sky_catalog.footprint_bricks(cone)
            except sky_catalog.CatalogError as e:
                loaded["bricks"] = None
                print(f"{WARN_PREFIX}DR10 brick list unavailable ({e}): catalogue gaps not checked this run",
                      file=sys.stderr)
        bricks = loaded["bricks"]
        coverage = (lambda r, d: sky_catalog.catalogue_gap(r, d, bricks)) if bricks is not None else None
        xm = sky_catalog.classify(ra, dec, atlas=loaded["atlas"], coverage=coverage)
        if loaded["atlas"] is None:
            xm.evidence.append("atlas unavailable: large hosts not searched")
        if bricks is None:
            xm.evidence.append("catalogue gaps not checked")
        return xm

    return classify


@dataclass
class DayResult:
    """One judged day: what is new to report, and what the state now holds."""
    candidates: list[Candidate]
    alerts: list[tuple[Candidate, list[str]]]  # (candidate, paths not reported before under its category)
    rejected: Counter
    parked: dict
    newly_parked: list[str]
    resolved: list[str]
    expired: list[str]
    # the same event seen by another survey: (candidate, new paths, key of the alert it is a note on)
    notes: list[tuple[Candidate, list[str], str]] = field(default_factory=list)


def judge_day(objs: list[SurveyObject], now: float, since: float, state: dict, classify,
              bracket_days: float = BRACKET_DAYS, min_detections: int = MIN_DETECTIONS,
              context: list[SurveyObject] = ()) -> DayResult:
    """
    Judge every survey object as of `now`, against and into `state` ({"reported", "parked"}).
    The live scan runs it once per run; the replay once per simulated day, on light curves
    sliced to that day, so both judge with the same code. `context` objects take part in
    association only (the replay's ZTF neighbours, there to bracket LSST onsets) and are
    never judged themselves.
    """
    links = associate(list(objs) + list(context))
    candidates: list[Candidate] = []
    parked_now: dict = {}
    rejected: Counter = Counter()
    for o in objs:
        ev = evaluate(o, now, since, links[o.key], bracket_days, min_detections, classify)
        if ev.parked is not None:
            parked_now[o.key] = {"survey": o.survey, "first_det_mjd": ev.parked.first_det_mjd,
                                 "code": ev.parked.code}
        if ev.candidate is None:
            rejected[ev.reason] += 1
        else:
            candidates.append(ev.candidate)

    prev_parked = state["parked"]
    parked, resolved, expired = update_parked(prev_parked, parked_now, {o.key for o in objs}, since, now)
    reported = state["reported"]
    alerts, notes = [], []
    # earliest first detection first, so the survey that saw the event first holds its alert
    for c in sorted(candidates, key=lambda c: (c.onset.first_det_mjd if c.onset and c.onset.first_det_mjd is not None
                                               else math.inf, c.obj.key)):
        k = c.obj.key
        if k in resolved:
            c.context.append(f"was parked since {_date(prev_parked[k]['parked_mjd'])}")
        prev = reported.get(k, {})
        # reported again when either axis changes: a new path, or a new category
        seen = {tuple(p) for p in prev.get("pairs", [])}
        new_conditions = [x for x in c.conditions if (c.category, x) not in seen]
        anchor = prev.get("note_on") or _alerted_elsewhere(c.obj, reported)
        if new_conditions:
            (notes.append((c, new_conditions, anchor)) if anchor else alerts.append((c, new_conditions)))
        reported[k] = {"first_reported_mjd": prev.get("first_reported_mjd", now), "category": c.category,
                       "pairs": sorted(seen | {(c.category, x) for x in c.conditions}),
                       "ra": c.obj.ra, "dec": c.obj.dec, **({"note_on": anchor} if anchor else {})}
    newly_parked = sorted(k for k in parked_now if k not in prev_parked)
    state["parked"] = parked
    return DayResult(candidates, alerts, rejected, parked, newly_parked, resolved, expired, notes)


def _alerted_elsewhere(obj: SurveyObject, reported: dict) -> str | None:
    """
    The alert this object's event already has: another survey's object reported within
    ASSOC_RADIUS_ARCSEC (this run or before), followed to the alert itself if that one was
    a note. One event, one alert; the second survey's detection becomes a note on it
    (2026-10-02: 3 of 46 replay alerts were such repeats). Matched on the positions kept in
    the state, not on today's listing, so it holds on a day the first survey's listing
    failed. Same-survey neighbours are a recurrence (the context lines), not grouped here.
    """
    r_deg = ASSOC_RADIUS_ARCSEC / 3600
    for key, entry in reported.items():
        if (not key.startswith(f"{obj.survey}:") and "ra" in entry
                and abs(entry["dec"] - obj.dec) <= r_deg and sep_deg(obj.ra, obj.dec, entry["ra"], entry["dec"]) <= r_deg):
            return entry.get("note_on") or key
    return None


def scan(
    name: str,
    cone: Cone,
    lookback_days: float = LOOKBACK_DAYS,
    min_detections: int = MIN_DETECTIONS,
    bracket_days: float = BRACKET_DAYS,
    dry_run: bool = False,
    init: bool = False,
) -> int:
    """
    Scan the cone once, report survey objects reaching a condition not reported
    before, park those whose onset cannot be bracketed yet. Past dates are not
    scanned here but replayed (replay.py). Returns a process exit code.

    No state file is an error unless init (user, 2026-10-02): it catches a wrong path
    at once, instead of a run that silently starts over and reports everything that
    already qualifies. init is the footprint's first run: it records what already
    qualifies without reporting it, so the next run reports only what changes.
    """
    path = state_file(name)
    if init and path.exists():
        print(f"{ERROR_PREFIX}--init: state already exists at {path}. --init is only for a footprint's first run: "
              "it would reset what was reported and hide alerts.", file=sys.stderr)
        return 1
    if not init and not path.exists():
        print(f"{ERROR_PREFIX}no state found at {path}: a wrong path, or this footprint's first run? "
              "For a first run pass --init: it records what already qualifies without reporting it.", file=sys.stderr)
        return 1
    state = _load_state(path)
    now = now_mjd()
    since = now - lookback_days
    retry_budget.reset()  # ALeRCE calls draw on it

    ra, dec, radius = cone
    print(f"\n\nFootprint {name}: cone RA {ra:g} Dec {dec:g} radius {radius:g} deg")
    print(f"Active since MJD {since:.1f} ({lookback_days:g} d); new = first detection within "
          f"{lookback_days:g} d, quiet point within {bracket_days:g} d before it")
    print(f"Previously reported: {len(state['reported'])}  parked: {len(state['parked'])}\n")

    # the newest detection per survey; a state from before 2026-10-03 kept LSST's alone
    newest = state.get("newest_mjd") or ({"lsst": state["lsst_newest_mjd"]} if state.get("lsst_newest_mjd") else {})
    for attempt in range(MAX_RETRIES):
        stats: dict = {}
        try:
            objs = gather(cone, now, since, None, stats, newest)
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

    day = judge_day(objs, now, since, state, footprint_classifier(cone), bracket_days, min_detections)

    by_survey = Counter(o.survey for o in objs)
    listings = {survey: stats[f"{survey}_listing"] for survey in LIVE_SURVEYS}
    for survey, listing in listings.items():
        print(f"{survey.upper()} listing (ALeRCE): {'FAILED - ' if listing.status == 'failed' else ''}{listing.detail}")
        if listing.status == "failed":
            print(f"{ERROR_PREFIX}{survey.upper()} listing failed: {listing.detail}. Its objects are not listed this "
                  "run (parked ones stay parked); exit 1 so the outage is not mistaken for a quiet sky.", file=sys.stderr)
    print(f"Photometry from ALeRCE: fetched {stats.get('fetched', 0)}, not needed {stats.get('not_fetched', 0)}, "
          f"failed {stats.get('fetch_errors', 0)}")
    print(f"Survey objects: {len(objs)} ({', '.join(f'{s} {n}' for s, n in sorted(by_survey.items()))})  "
          f"kept: {len(day.candidates)}")
    for reason, n in sorted(day.rejected.items()):
        print(f"  rejected {reason}: {n}")
    print(f"Parked, onset not bracketed: {len(day.parked)}  "
          f"(new {len(day.newly_parked)}, resolved {len(day.resolved)}, expired unbracketed {len(day.expired)})")
    for k in day.newly_parked:
        info = day.parked[k]
        print(f"  {k}  first detection {_date(info['first_det_mjd'])}  {info['code']}")
    print()

    tns_watch, tns_lines = _retry_tns(state.get("tns_watch", {}), now)
    if init:
        recorded = Counter(c.category for c, _ in day.alerts) + Counter(c.category for c, _, _ in day.notes)
        print(f"INIT: recorded {sum(recorded.values())} survey objects that already qualify, not reported"
              + (f" ({', '.join(f'{k} {n}' for k, n in sorted(recorded.items()))})" if recorded else "")
              + f"; parked {len(day.parked)}. From the next run on, only what changes is reported.")
    elif day.alerts:
        print(f"=== {len(day.alerts)} TRANSIENT ALERTS ===\n")
        for category in CATEGORY_ORDER:
            group = [a for a in day.alerts if a[0].category == category]
            if not group:
                continue
            print(f"--- {category} ({len(group)}) ---")
            for c, new_conditions in group:
                tns_text, watch_entry = _add_tns(c, now)
                if watch_entry:
                    tns_watch[c.obj.key] = watch_entry
                _print_candidate(c, new_conditions, tns_text)
            print()
    else:
        print("No new transient alerts.")
    if day.notes and not init:
        print(f"\n--- the same events, seen by another survey: notes on earlier alerts ({len(day.notes)}) ---")
        for c, new_conditions, anchor in day.notes:
            onset = f", first detection {_date(c.onset.first_det_mjd)}" if c.onset and c.onset.first_det_mjd else ""
            print(f"{c.obj.key}  {c.category} · {', '.join(c.conditions)}  new: {', '.join(new_conditions)}  "
                  f"note on {anchor}{onset}")
    if tns_lines:
        print("\nTNS for earlier alerts:")
        print("\n".join(tns_lines))
    ps = [] if init else [(c.obj.key, c.obj.ra, c.obj.dec, now, f"live {name}")
                          for c in [a[0] for a in day.alerts] + [n[0] for n in day.notes] if c.category == "point_source"]
    watch_file = STATE_DIR / PS_WATCH_FILE
    if not init and not watch_file.exists():  # state, like the monitor's: a missing one loses its evidence count
        print(f"{WARN_PREFIX}no point-source watch file at {watch_file} although this footprint has state: "
              "deleted? Its evidence count restarts from zero.", file=sys.stderr)
        print(f"\nPoint-source watch file missing at {watch_file}: evidence count restarts from zero.")
    print("\n" + "\n".join(point_source_watch(ps, now, save=not dry_run)))

    cutoff = now - STATE_RETENTION_DAYS
    reported = {k: v for k, v in state["reported"].items() if v["first_reported_mjd"] >= cutoff}
    if dry_run:
        print("\n(dry run: state not saved)")
    else:
        _save_state(path, {"last_mjd": now, "reported": reported, "parked": day.parked,
                           "newest_mjd": {s: lc.newest_mjd for s, lc in listings.items()}, "tns_watch": tns_watch})
    return 1 if any(lc.status == "failed" for lc in listings.values()) else 0


# ------------------------------------------------------------------ CLI


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan a sky cone for new transients (SNe, TDEs, orphans).")
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--footprint", choices=sorted(FOOTPRINTS),
                       help=f"default {LIVE_FOOTPRINT}. " + "; ".join(f"{k}: {v[1]}" for k, v in FOOTPRINTS.items()))
    where.add_argument("--cone", nargs=3, type=float, metavar=("RA", "DEC", "RADIUS"),
                       help="cone in degrees")
    parser.add_argument("--radius", type=float, help=f"override a footprint's radius (default {DEFAULT_RADIUS_DEG:g} deg)")
    parser.add_argument("--replay", nargs=2, metavar=("FIRST_DAY", "LAST_DAY"),
                        help="replay these past days (YYYY-MM-DD, inclusive) from cached light curves; see replay.py")
    parser.add_argument("--no-cuts", action="store_true",
                        help="with --replay: list every object first detected in the window (no lossless span cut)")
    parser.add_argument("--estimate", action="store_true",
                        help="with --replay: list the candidates and print the time estimate, fetch nothing")
    parser.add_argument("--compare-cuts", action="store_true",
                        help="with --replay: run with and without the cut and require identical alerts")
    parser.add_argument("--lookback-days", type=float, default=LOOKBACK_DAYS,
                        help=f"N: a first detection this recent can be new (default {LOOKBACK_DAYS:g})")
    parser.add_argument("--bracket-days", type=float, default=BRACKET_DAYS,
                        help=f"X: a quiet point this close before it brackets the onset (default {BRACKET_DAYS:g})")
    parser.add_argument("--min-detections", type=int, default=MIN_DETECTIONS,
                        help=f"per survey object (default {MIN_DETECTIONS})")
    parser.add_argument("--dry-run", action="store_true", help="report without saving state")
    parser.add_argument("--init", action="store_true",
                        help="a footprint's first run: record what already qualifies without reporting it "
                             "(without --init, a missing state file is an error)")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="with --replay: start even when the estimate is above "
                             f"{CONFIRM_THRESHOLD_SECONDS / 60:g} min, without asking")
    args = parser.parse_args(argv)
    if args.footprint is None and args.cone is None:
        args.footprint = LIVE_FOOTPRINT

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
    if args.init and args.replay:
        parser.error("--init is for a live footprint's first run; a replay keeps its state in memory")
    if (args.no_cuts or args.compare_cuts or args.estimate) and not args.replay:
        parser.error("--no-cuts, --compare-cuts and --estimate go with --replay")
    if args.replay:
        try:
            first, last = (date_to_mjd(d) for d in args.replay)
        except ValueError:
            parser.error("--replay days must be YYYY-MM-DD")
        if last < first:
            parser.error("--replay: the last day comes before the first")
    return args


def main(argv: list[str] | None = None) -> int:
    a = _parse_args(argv)
    if a.replay:
        from . import replay
        try:
            if a.compare_cuts:
                return replay.compare_cuts(a.name, a.cone, *a.replay, a.lookback_days, a.bracket_days, a.min_detections,
                                           yes=a.yes)
            replay.run(a.name, a.cone, *a.replay, a.lookback_days, a.bracket_days, a.min_detections, cut=not a.no_cuts,
                       estimate_only=a.estimate, yes=a.yes)
        except replay.Declined as e:
            print(f"{ERROR_PREFIX}{e}", file=sys.stderr)
            return 1
        return 0
    return scan(a.name, a.cone, a.lookback_days, a.min_detections, a.bracket_days, a.dry_run, init=a.init)


if __name__ == "__main__":
    sys.exit(main())
