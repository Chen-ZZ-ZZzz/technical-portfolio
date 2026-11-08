"""transient_monitor.py — survey objects built by hand; no live broker or catalogue calls."""

import dataclasses
import datetime
import json
import math
import os
import time

import pandas as pd
import pytest
from requests.exceptions import ConnectionError

from rubin_qa import transient_monitor as mon
from rubin_qa.config import PROJECT_ROOT
from rubin_qa.photometry import PhotPoint, SurveyObject, mag_to_njy
from rubin_qa.sky_catalog import Crossmatch
from tests import fake_tns
from tests.test_sky_catalog import SNR_COLUMNS, psf_at, snr_answer

NOW = 61310.0  # 2026-09-27
SINCE = NOW - 14.0
HIGH_B = (150.0, 12.0)  # b ~ +47
IN_PLANE = (280.0, 0.0)  # b ~ +2


def det(mjd, mag=19.0, band="r", survey="ztf", oid="ZTF26aaa", err_mag=0.05):
    f = mag_to_njy(mag)
    return PhotPoint(survey, oid, mjd, band, f, f * math.log(10) / 2.5 * err_mag, True, "test")


def lim(mjd, maglim=20.5, band="r", oid="ZTF26aaa"):
    return PhotPoint("ztf", oid, mjd, band, math.nan, mag_to_njy(maglim) / 5, False, "test")


def forced(mjd, flux, err, band="r", oid="1706"):
    return PhotPoint("lsst", oid, mjd, band, flux, err, False, "test")


def obj(points, survey="ztf", oid="ZTF26aaa", ra=HIGH_B[0], dec=HIGH_B[1], **kw):
    return SurveyObject(survey, oid, ra, dec, points=list(points), **kw)


FIRST = NOW - 5
NEW_ZTF = [lim(FIRST - 1, 20.5), det(FIRST), det(NOW - 3), det(NOW - 1, band="g")]
OLD_ZTF = [det(NOW - 900, 18.0), det(NOW - 400, 18.1), det(NOW - 2, 18.0)]
RAPID = [det(NOW - 2.0, 20.0), det(NOW - 0.3, 18.7)]  # 1.3 mag in 1.7 d
# an old object's baseline, then a spike (1.3 mag) that is still up the next night: persistent
SPIKE = [det(NOW - 900, 20.0), det(NOW - 2.5, 20.0), det(NOW - 1.3, 18.7)]
REBRIGHT = SPIKE + [det(NOW - 0.3, 18.8)]


def xm(verdict, *evidence):
    return lambda ra, dec, **kw: Crossmatch(verdict, list(evidence))


def boom(ra, dec):
    raise AssertionError("catalogue consulted for an object with nothing to report")


# --- helpers -------------------------------------------------------------------------


def test_date_to_mjd_is_end_of_day():
    assert mon.date_to_mjd("2026-04-15") == 61146.0


@pytest.mark.parametrize("oid,old", [("ZTF20abc", True), ("ZTF26abc", False), ("junk", False)])
def test_ztf_id_is_old(oid, old):
    assert mon.ztf_id_is_old(oid, SINCE) is old


def test_ztf_id_year_boundary():
    """Window starting in late December 2025: a ZTF25 ID is still new."""
    assert not mon.ztf_id_is_old("ZTF25xyz", 61030.0)


def test_quiet_points():
    pts = [lim(1.0, 23.9), forced(2.0, 100.0, 50.0), forced(3.0, 900.0, 100.0), det(4.0)]
    assert mon.quiet_points(pts) == [(1.0, pytest.approx(1000.0)), (2.0, 250.0)]  # 9-sigma forced is no quiet point


# --- judge_new ------------------------------------------------------------------------


def test_bracketed_onset_is_new():
    v = mon.judge_new("ztf", NEW_ZTF, NOW)
    assert (v.status, v.code, v.last_quiet_mjd, v.first_det_mjd, v.n_det) == (mon.NEW, "bracketed", FIRST - 1, FIRST, 3)


def test_shallow_quiet_point_does_not_bracket():
    v = mon.judge_new("ztf", [lim(FIRST - 1, 18.5)] + NEW_ZTF[1:], NOW)
    assert (v.status, v.code) == (mon.UNBRACKETED, "quiet_too_shallow")


def test_deeper_earlier_quiet_point_still_brackets():
    v = mon.judge_new("ztf", [lim(FIRST - 8, 20.5), lim(FIRST - 1, 18.5)] + NEW_ZTF[1:], NOW)
    assert (v.status, v.last_quiet_mjd) == (mon.NEW, FIRST - 8)


def test_quiet_point_outside_bracket_or_after_onset_does_not_count():
    v = mon.judge_new("ztf", [lim(FIRST - 10.5), lim(FIRST), lim(FIRST + 1)] + NEW_ZTF[1:], NOW)
    assert v.code == "no_quiet_point"


def test_bracket_boundary_is_inclusive_and_x_is_a_parameter():
    pts = [lim(FIRST - 4)] + NEW_ZTF[1:]
    assert mon.judge_new("ztf", [lim(FIRST - 10)] + NEW_ZTF[1:], NOW).status == mon.NEW
    assert mon.judge_new("ztf", pts, NOW, bracket_days=3).status == mon.UNBRACKETED
    assert mon.judge_new("ztf", pts, NOW, bracket_days=5).status == mon.NEW


@pytest.mark.parametrize("points,kw,code", [
    ([det(NOW - 20), det(NOW - 3), det(NOW - 1)], {}, "old"),
    ([det(FIRST), det(NOW - 1)], {}, "too_few_detections"),
    ([det(NOW - 1.0), det(NOW - 0.996), det(NOW - 0.992)], {}, "single_night"),  # the unknown-asteroid shape
    (NEW_ZTF, {"old_ids": ["ZTF20abc"]}, "preexisting_ztf_object"),
    ([lim(NOW - 1)], {}, "no_detections"),
])
def test_not_new(points, kw, code):
    v = mon.judge_new("ztf", points, NOW, **kw)
    assert (v.status, v.code) == (mon.NOT_NEW, code)


LSST_ONSET = [det(FIRST, 22.0, survey="lsst", oid="1706"), det(NOW - 3, 21.8, survey="lsst", oid="1706"),
              det(NOW - 1, 21.6, survey="lsst", oid="1706")]


def test_lsst_onset_bracketed_by_a_ztf_limit_only_as_a_fallback():
    deep = [(FIRST - 2, mag_to_njy(22.5))]
    v = mon.judge_new("lsst", LSST_ONSET, NOW, extra_quiet=deep)
    assert (v.status, v.code, v.last_quiet_mjd) == (mon.NEW, "bracketed_by_ztf", FIRST - 2)


def test_typical_ztf_limit_is_too_shallow_for_an_lsst_onset():
    v = mon.judge_new("lsst", LSST_ONSET, NOW, extra_quiet=[(FIRST - 2, mag_to_njy(20.5))])
    assert (v.status, v.code) == (mon.UNBRACKETED, "quiet_too_shallow")


# The depth test compares against the FIRST detection, not the brightest so far (decided 2026-10-02).
# Bracketing claims the object was not there at the brightness of its first detection, so the
# quiet point must have been deep enough to see it at that brightness. Against the brightest
# point, a shallow limit would pass and prove only that the object rose, not when it started;
# rising covers that. If this test fails after a change to _bracket, the change is wrong.
LSST_FAINT_ONSET_THEN_BRIGHT = [det(FIRST, 23.0, survey="lsst", oid="1706"), det(FIRST + 1, 22.0, survey="lsst", oid="1706"),
                                det(NOW - 1, 19.8, survey="lsst", oid="1706")]


def test_a_limit_shallower_than_the_first_detection_never_brackets_however_bright_it_gets():
    """The user's example: a ZTF limit of 20.5 before an LSST object first seen at 23 that later passes 20."""
    v = mon.judge_new("lsst", LSST_FAINT_ONSET_THEN_BRIGHT, NOW, extra_quiet=[(FIRST - 2, mag_to_njy(20.5))])
    assert (v.status, v.code) == (mon.UNBRACKETED, "quiet_too_shallow")


def test_that_object_is_still_reported_by_rising_not_by_a_bracket():
    ev = mon.evaluate(obj(LSST_FAINT_ONSET_THEN_BRIGHT, survey="lsst", oid="1706"), NOW, SINCE,
                      associates=[obj([lim(FIRST - 2, 20.5)], oid="ZTF26z")], classify=xm("host", "EXP"))
    assert ev.candidate.conditions[0] == "rising" and "bracketed" not in ev.candidate.conditions
    assert ev.candidate.onset.code == "quiet_too_shallow"


def test_lsst_forced_photometry_after_onset_never_brackets():
    """Rubin's forced photometry starts at or after the first detection (16/16 objects, 2026-09-27)."""
    v = mon.judge_new("lsst", LSST_ONSET + [forced(FIRST + 1, 10.0, 50.0)], NOW)
    assert v.code == "no_quiet_point"


# --- rising / rapid rise ------------------------------------------------------------------


def test_rising_on_forced_photometry():
    pts = [det(FIRST, 24.5, survey="lsst", oid="1706"), forced(FIRST + 2, 800.0, 60.0), forced(NOW - 1, 1500.0, 70.0)]
    r = mon.rising(pts, FIRST)
    early = mag_to_njy(24.5)  # 575 nJy
    assert r.band == "r" and r.mag == pytest.approx(2.5 * math.log10(1500 / early))
    assert r.days == pytest.approx(4.0)


@pytest.mark.parametrize("late_flux,late_err,days", [
    (1050.0, 60.0, 4.0),   # +0.05 mag: too little
    (1400.0, 300.0, 4.0),  # 0.37 mag but 1.3 sigma
    (2000.0, 50.0, 0.1),   # plenty, but one night
    (-100.0, 50.0, 4.0),   # faded to nothing
])
def test_not_rising(late_flux, late_err, days):
    pts = [forced(FIRST, 1000.0, 50.0), forced(FIRST + days, late_flux, late_err)]
    assert mon.rising(pts, FIRST) is None


def test_rising_from_nothing():
    r = mon.rising([forced(FIRST, -20.0, 50.0), forced(NOW - 1, 900.0, 60.0)], FIRST)
    assert math.isinf(r.mag) and r.sigma > 3


def test_rising_ignores_points_before_onset_and_other_bands():
    pts = [forced(FIRST - 3, 5000.0, 50.0), forced(FIRST, 1000.0, 50.0, band="g"), forced(NOW - 1, 3000.0, 50.0, band="i")]
    assert mon.rising(pts, FIRST) is None


def test_rapid_rise_same_band_within_window():
    assert mon.rapid_rise([det(NOW - 2.5, 19.5), det(NOW - 1.0, 18.9), det(NOW - 0.2, 18.3)], NOW) == pytest.approx(1.2)


@pytest.mark.parametrize("points", [
    [det(NOW - 1.0, 19.6, band="g"), det(NOW - 0.5, 18.4)],  # colour, not rise
    [det(NOW - 5.0, 20.0), det(NOW - 0.5, 18.5)],            # over more than 3 d
    [det(NOW - 8.0, 20.0), det(NOW - 7.0, 18.5)],            # stale
    [],
])
def test_no_rapid_rise(points):
    assert mon.rapid_rise(points, NOW) is None


def test_rapid_rise_ignores_upper_limits_and_negative_flux():
    pts = [lim(NOW - 2.0, 21.0), PhotPoint("ztf", "z", NOW - 1.5, "r", -500.0, 50.0, True, "t"),
           det(NOW - 1.0, 19.0), det(NOW - 0.5, 18.8)]
    assert mon.rapid_rise(pts, NOW) == pytest.approx(0.2)


# --- association and context ---------------------------------------------------------------


def test_associate_within_radius_only():
    a = obj([], oid="ZTF26a")
    b = obj([], survey="lsst", oid="17", ra=HIGH_B[0] + 1.0 / 3600)
    c = obj([], oid="ZTF26c", ra=HIGH_B[0] + 10.0 / 3600)
    links = mon.associate([a, b, c])
    assert [o.key for o in links[a.key]] == ["lsst:17"]
    assert links[c.key] == []


def test_history_context():
    old_id = obj([det(NOW - 2)], oid="ZTF19old")
    old_det = obj([det(NOW - 300), det(NOW - 1)], oid="ZTF26mid")
    fresh = obj([det(NOW - 2)], oid="ZTF26new")
    assert mon.history_context(obj([]), [old_id, old_det, fresh], SINCE) == [
        "recurrent: ztf:ZTF19old, a ZTF object from 2019",
        "recurrent: ztf:ZTF26mid detected here since 2025-12-01",
        "also seen as ztf:ZTF26new, first detection 2026-09-25",
    ]


# --- evaluate ------------------------------------------------------------------------------


def test_cheap_rejections_do_not_consult_the_catalogue():
    assert mon.evaluate(obj(NEW_ZTF, ra=IN_PLANE[0], dec=IN_PLANE[1]), NOW, SINCE, classify=boom).reason == "galactic_plane"
    assert mon.evaluate(obj(NEW_ZTF, sso_id="41025"), NOW, SINCE, classify=boom).reason == "solar_system"
    assert mon.evaluate(obj([]), NOW, SINCE, classify=boom).reason == "no_detections"
    assert mon.evaluate(obj([], fetch_errors=["alerce: 504"]), NOW, SINCE, classify=boom).reason == "no_photometry"
    assert mon.evaluate(obj(OLD_ZTF), NOW, SINCE, classify=boom).reason == "old_no_rise"
    assert mon.evaluate(obj(NEW_ZTF, oid="ZTF19x"), NOW, SINCE, classify=boom).reason == "preexisting_ztf_no_rise"


@pytest.mark.parametrize("summary,reason", [
    ({"firstmjd": NOW - 90, "lastmjd": NOW - 20, "n_det": 1}, "single_detection"),
    ({"firstmjd": NOW - 90, "lastmjd": NOW - 10, "n_det": 9}, "old_no_recent_detection"),
    ({"lastmjd": NOW - 1, "n_det": 9}, "no_first_detection_listed"),
    ({"firstmjd": NOW - 5, "lastmjd": NOW - 1, "n_det": 4}, "empty_light_curve"),  # worth the call, came back empty
])
def test_an_unfetched_object_is_rejected_on_the_criterion_that_skipped_it(summary, reason):
    """Never a generic "not fetched": a skip for a bad reason must not hide under a harmless label."""
    assert mon.evaluate(obj([], summary=summary), NOW, SINCE, classify=boom).reason == reason


# --- the decision table: sky context -> category, path -> confidence label ---------------

LSST_RISER = [det(FIRST, 24.5, survey="lsst", oid="1706"), det(FIRST + 1.0, 24.4, survey="lsst", oid="1706"),
              forced(FIRST + 2, 800.0, 60.0), forced(NOW - 1, 1500.0, 70.0)]
PATH_CASES = {  # one light curve per path combination evaluate can produce, with the labels it must carry
    "bracketed": (obj(NEW_ZTF), ["bracketed"]),
    "bracketed, rising, rapid_rise": (obj([lim(NOW - 4, 21.5), det(NOW - 3, 20.5)] + RAPID), ["bracketed", "rising", "rapid_rise"]),
    "rising": (obj(LSST_RISER, survey="lsst", oid="1706"), ["rising"]),
    "rising, rapid_rise": (obj([det(NOW - 3.5, 20.6), det(NOW - 2.0, 20.0), det(NOW - 0.3, 18.7)]), ["rising", "rapid_rise"]),
    "re_brightening": (obj(REBRIGHT, oid="ZTF18old"), ["re_brightening"]),
}
SKY_CASES = {  # one crossmatch per decision-table key
    "host": Crossmatch("host", ['EXP r=21.0 sep=2.0" d_DLR=0.8']),
    "host+agn_host": Crossmatch("host", ['EXP r=19.0 sep=6.0" d_DLR=1.2'], flags=["agn_host"]),
    "host+agn_nuclear": Crossmatch("host", ['EXP r=19.0 sep=0.5" d_DLR=0.1'], flags=["nuclear", "agn_nuclear"]),
    "point_source": Crossmatch("point_source", ['PSF r=22.0 at 0.3"']),
    "point_source+agn_nuclear": Crossmatch("point_source", ['PSF r=21.0 at 0.3"'], flags=["agn_nuclear"]),
    "none": Crossmatch("none"),
    "stellar": Crossmatch("stellar", ["Gaia parallax 8 sigma"]),
    "unavailable": Crossmatch("unavailable", ["tile 1501,920: ReadTimeout"]),
}


def test_the_cases_cover_the_whole_table():
    assert set(SKY_CASES) == set(mon.SKY_CATEGORY)
    assert set(mon.SKY_CATEGORY.values()) == set(mon.CATEGORY_ORDER)  # every category reachable, and printed
    assert {x for _, labels in PATH_CASES.values() for x in labels} == set(mon.PATHS)


@pytest.mark.parametrize("sky", SKY_CASES)
@pytest.mark.parametrize("path", PATH_CASES)
def test_every_path_and_sky_context_leads_to_one_visible_category(path, sky):
    """Category from the sky alone, whatever the path; the path is the label; nothing is dropped."""
    o, labels = PATH_CASES[path]
    ev = mon.evaluate(o, NOW, SINCE, classify=lambda ra, dec, **kw: SKY_CASES[sky])
    assert ev.candidate is not None, f"{path} on {sky}: dropped ({ev.reason})"
    assert (ev.candidate.category, ev.candidate.conditions) == (mon.SKY_CATEGORY[sky], labels)


@pytest.mark.parametrize("match", [
    Crossmatch("point_source", flags=["agn_host"]),  # a point source within 1" cannot be a host farther out
    Crossmatch("stellar", flags=["agn_nuclear"]),  # the star verdict carries no colour flag today
    Crossmatch("galaxy"),  # a verdict sky_catalog does not have
])
def test_a_sky_context_nobody_mapped_raises_instead_of_falling_through(match):
    with pytest.raises(ValueError, match="missing from the decision table"):
        mon.sky_context(match)


def test_a_star_is_reported_as_stellar_on_every_path():
    """No drop: a real transient wrongly matched to a foreground star must show up, not vanish (2026-10-01)."""
    c = mon.evaluate(obj(NEW_ZTF), NOW, SINCE, classify=xm("stellar", "Gaia")).candidate
    assert (c.category, c.conditions) == ("stellar", ["bracketed"])


def test_an_old_ztf_object_rising_fast_is_re_brightening_never_preexisting():
    """
    The age check must not pre-empt the rise paths: ZTF18 is old by its ID, and its
    spike is still up the next night. Rejecting old IDs before rapid_rise runs would
    make re_brightening impossible for ZTF.
    """
    c = mon.evaluate(obj(REBRIGHT, oid="ZTF18old"), NOW, SINCE, classify=xm("host", "EXP")).candidate
    assert (c.category, c.conditions, c.rapid) == ("host", ["re_brightening"], pytest.approx(1.2))


@pytest.mark.parametrize("oid,label", [("ZTF18old", "preexisting_ztf_no_rise"), ("ZTF26old", "old_no_rise")])
@pytest.mark.parametrize("spike", [
    [],                                    # one spike detection
    [det(NOW - 1.25, 18.8)],               # two on the same night: still one night
], ids=["one detection", "two the same night"])
def test_a_spike_on_one_night_only_is_no_alert(oid, label, spike):
    """
    An old object's rapid rise must persist (user, 2026-10-03): an asteroid passes a
    position once and a bad detection does not repeat. Old by ID or by date alike.
    """
    ev = mon.evaluate(obj(SPIKE + spike, oid=oid), NOW, SINCE, classify=boom)
    assert ev.candidate is None and ev.reason == label


def test_the_same_spike_still_up_the_next_night_is_re_brightening():
    c = mon.evaluate(obj(REBRIGHT, oid="ZTF26old"), NOW, SINCE, classify=xm("host", "EXP")).candidate
    assert c.conditions == ["re_brightening"]


def test_persistence_is_asked_of_old_objects_only():
    """A new object's rapid rise is unchanged: RAPID is up on one night only."""
    c = mon.evaluate(obj([lim(NOW - 4, 21.5), det(NOW - 3, 20.5)] + RAPID), NOW, SINCE, classify=xm("host", "EXP")).candidate
    assert "rapid_rise" in c.conditions


def test_unbracketed_and_flat_is_parked():
    ev = mon.evaluate(obj(NEW_ZTF[1:]), NOW, SINCE, classify=xm("none"))
    assert (ev.candidate, ev.reason, ev.parked.code) == (None, "unbracketed", "no_quiet_point")


def test_lsst_rising_tier_without_a_bracket():
    """The main LSST path: no pre-onset photometry exists, the rise shows in forced photometry."""
    ev = mon.evaluate(obj(LSST_RISER, survey="lsst", oid="1706"), NOW, SINCE, classify=xm("host", "EXP"))
    c = ev.candidate
    assert (c.category, c.conditions) == ("host", ["rising"])
    assert ev.parked is None  # too few detections to be judged new at all: nothing to park
    assert c.onset.code == "too_few_detections"


@pytest.mark.parametrize("points,survey,oid", [
    # rising on one night of detections plus brighter forced photometry on later nights
    ([det(FIRST, 24.5, survey="lsst", oid="1706"), det(FIRST + 0.02, 24.5, survey="lsst", oid="1706"),
      forced(FIRST + 2, 800.0, 60.0), forced(NOW - 1, 1500.0, 70.0)], "lsst", "1706"),
    # a rapid rise between two detections the same night
    ([det(NOW - 0.40, 20.0), det(NOW - 0.30, 18.7)], "ztf", "ZTF26aaa"),
    # three detections in one night with a deep quiet point before them: bracketed, yet one night
    ([lim(NOW - 3, 22.0), det(NOW - 1.0), det(NOW - 0.98), det(NOW - 0.96)], "ztf", "ZTF26aaa"),
])
def test_every_path_needs_detections_on_more_than_one_night(points, survey, oid):
    """The precondition of every report (2026-10-01); it is what makes the replay's span cut lossless."""
    ev = mon.evaluate(obj(points, survey=survey, oid=oid), NOW, SINCE, classify=boom)
    assert (ev.candidate, ev.reason) == (None, "single_night")


def test_spans_nights_is_the_shared_definition():
    assert mon.spans_nights(100.0, 100.0 + mon.MIN_SPAN_DAYS) and not mon.spans_nights(100.0, 100.49)


def test_lsst_rising_and_unbracketed_is_reported_and_parked():
    pts = LSST_ONSET + [forced(NOW - 0.5, mag_to_njy(20.5), 60.0)]
    ev = mon.evaluate(obj(pts, survey="lsst", oid="1706"), NOW, SINCE, classify=xm("none"))
    assert (ev.candidate.category, ev.candidate.conditions) == ("orphan", ["rising"])
    assert ev.parked.code == "no_quiet_point"
    assert "onset not bracketed (no_quiet_point), parked" in ev.candidate.context


def test_lsst_bracketed_by_associated_ztf_limit():
    ztf = obj([lim(FIRST - 2, 22.5), det(NOW - 300, 21.0)], oid="ZTF26z")
    ev = mon.evaluate(obj(LSST_ONSET, survey="lsst", oid="1706"), NOW, SINCE, associates=[ztf], classify=xm("host", "EXP"))
    c = ev.candidate
    assert (c.category, c.conditions, c.onset.code) == ("host", ["bracketed", "rising"], "bracketed_by_ztf")
    assert c.context == ["recurrent: ztf:ZTF26z detected here since 2025-12-01"]


def test_ztf_limits_do_not_bracket_ztf_objects_through_association():
    other = obj([lim(FIRST - 2, 22.5)], oid="ZTF26other")
    ev = mon.evaluate(obj(NEW_ZTF[1:]), NOW, SINCE, associates=[other], classify=xm("none"))
    assert ev.reason == "unbracketed"


def test_preexisting_ztf_object_cannot_be_rising_only_re_brightening():
    pts = [det(FIRST, 21.0), det(FIRST + 1, 20.5), det(NOW - 0.3, 19.0)]
    ev = mon.evaluate(obj(pts, oid="ZTF20old"), NOW, SINCE, classify=xm("none"))
    assert ev.reason == "preexisting_ztf_no_rise"


# --- gathering -----------------------------------------------------------------------------


@pytest.mark.parametrize("summary,points,skip", [
    ({"firstmjd": NOW - 5, "lastmjd": NOW - 1, "n_det": 4}, [], None),                         # recent onset
    ({"firstmjd": NOW - 90, "lastmjd": NOW - 1, "n_det": 9}, [], None),                        # old but active: could rise fast
    ({"firstmjd": NOW - 90, "lastmjd": NOW - 10, "n_det": 9}, [], "old_no_recent_detection"),  # old and quiet
    ({"firstmjd": NOW - 5, "lastmjd": NOW - 5, "n_det": 1}, [], "single_detection"),
    ({}, LSST_ONSET, None),                                                                     # from its own detections
    ({}, [], "no_first_detection_listed"),
])
def test_photometry_is_skipped_only_on_a_named_criterion(summary, points, skip):
    o = obj(points, survey="lsst", oid="1706", summary=summary)
    assert mon.photometry_skip_reason(o, NOW, SINCE) == skip
    assert mon.needs_photometry(o, NOW, SINCE) is (skip is None)


def test_merge_dedups_points_and_unions_context():
    a = obj([det(1.0)], classifications=["c1"], broker_refs={"alerce": "x"})
    b = obj([det(1.0), det(2.0)], classifications=["c1", "c2"], broker_refs={"alerce": "x"}, summary={"n_det": 2},
            sso_id="9")
    mon._merge(a, b)
    assert [p.mjd for p in a.points] == [1.0, 2.0]
    assert (a.classifications, a.summary, a.sso_id) == (["c1", "c2"], {"n_det": 2}, "9")


@pytest.fixture
def sources(monkeypatch):
    """
    ALeRCE, the monitor's one broker (2026-10-03), faked: "objects" are SurveyObjects with
    their full light curves; the listing returns them without points and with the summary
    the real listing gives (first/last detection, count), active since the window start;
    the light-curve calls return their points. "newest": the newest-in-cone answer per survey.
    """
    s = {"objects": [], "fetched": [], "probes": [], "listing_error": {}, "fail": set(),
         "newest": {"ztf": ({"oid": "ZTF19old", "lastmjd": SINCE - 30}, None),
                    "lsst": ({"oid": 1700, "lastmjd": SINCE - 30}, None)}}

    def listing(survey, cone, since, until):
        out = []
        for o in s["objects"]:
            dets = mon.detections(o.points)
            summary = o.summary or ({"firstmjd": dets[0].mjd, "lastmjd": dets[-1].mjd, "n_det": len(dets)} if dets else {})
            if o.survey == survey and summary and summary["lastmjd"] >= since:
                out.append(dataclasses.replace(o, points=[], summary=summary, classifications=list(o.classifications),
                                               fetch_errors=[], tns=[], broker_refs={"alerce": o.survey_object_id}))
        return out, s["listing_error"].get(survey)

    def light_curve(survey, oid):
        key = f"{survey}:{oid}"
        s["fetched"].append(key)
        found = [o for o in s["objects"] if o.key == key]
        return (None, "504") if key in s["fail"] or not found else (found[0], None)

    def ztf(oid):
        o, err = light_curve("ztf", oid)
        return ([], err) if err else (o.points, None)

    def lsst(oid):
        o, err = light_curve("lsst", oid)
        return ([], None, err) if err else (o.points, o.sso_id, None)

    monkeypatch.setattr(mon, "discover_alerce", listing)
    monkeypatch.setattr(mon, "fetch_alerce_ztf", ztf)
    monkeypatch.setattr(mon, "fetch_alerce_lsst", lsst)
    monkeypatch.setattr(mon, "alerce_newest_in_cone",
                        lambda survey, cone, floor: s["probes"].append((survey, floor)) or s["newest"][survey])
    return s


def test_gather_lists_both_surveys_and_fetches_only_what_is_worth_a_call(sources, capsys):
    sources["objects"] = [obj(NEW_ZTF), obj(LSST_ONSET, survey="lsst", oid="1706"),
                          obj([], survey="lsst", oid="2000", summary={"firstmjd": NOW - 90, "lastmjd": NOW - 20, "n_det": 5})]
    stats = {}
    objs = {o.key: o for o in mon.gather((150, 12, 1), NOW, NOW - 30, None, stats)}
    assert sorted(objs) == ["lsst:1706", "lsst:2000", "ztf:ZTF26aaa"]
    assert sources["fetched"] == ["ztf:ZTF26aaa", "lsst:1706"]  # old and quiet: not fetched
    assert [p.mjd for p in objs["lsst:1706"].points] == [p.mjd for p in LSST_ONSET]
    assert objs["ztf:ZTF26aaa"].broker_refs == {"alerce": "ZTF26aaa"}
    assert (stats["fetched"], stats["not_fetched"]) == (2, 1)
    assert stats["ztf_listing"].status == stats["lsst_listing"].status == "objects"


def test_gather_truncates_for_a_replay_date(sources):
    sources["objects"] = [obj(LSST_ONSET + [det(NOW + 3, 20.0, survey="lsst", oid="1706")], survey="lsst", oid="1706")]
    (o,) = mon.gather((150, 12, 1), NOW, SINCE, NOW, {})
    assert [p.mjd for p in o.points] == [p.mjd for p in LSST_ONSET]


def test_gather_warns_on_failed_fetches_and_listing_errors(sources, capsys):
    sources["objects"] = [obj(NEW_ZTF, oid=f"ZTF26{i:03d}") for i in range(mon.WARN_LIMIT + 2)]
    sources["fail"] = {o.key for o in sources["objects"]}
    sources["listing_error"]["lsst"] = "alerce lsst listing page 3: timeout"
    stats = {}
    objs = mon.gather((150, 12, 1), NOW, SINCE, None, stats)
    err = capsys.readouterr().err
    assert err.count("ALeRCE photometry unavailable") == mon.WARN_LIMIT
    assert stats["lsst_listing"].status == "failed" and "alerce lsst listing page 3: timeout" in stats["lsst_listing"].detail
    assert stats["fetch_errors"] == mon.WARN_LIMIT + 2
    assert all(o.fetch_errors == ["alerce ztf: 504"] for o in objs)


# --- the LSST listing guard: "no new data upstream" vs "the listing failed" (2026-10-02) ----


def listed(*lastmjds):
    return [obj([], survey="lsst", oid=str(i), summary={"firstmjd": FIRST, "lastmjd": m, "n_det": 3})
            for i, m in enumerate(lastmjds)]


def probe(item, calls=None):
    """A newest-in-cone check that answers `item` (no error), recording the lastmjd floor it was asked with."""
    def ask(cone, floor):
        if calls is not None:
            calls.append(floor)
        return item, None
    return ask


def test_a_listing_with_objects_needs_no_check_and_raises_the_known_newest():
    check = mon.check_listing("lsst", listed(NOW - 2, NOW - 1), None, (53.1, -27.8, 2.82), SINCE, SINCE - 100,
                                   probe=lambda c, f: pytest.fail("checked a non-empty listing"))
    assert (check.status, check.newest_mjd) == ("objects", NOW - 1) and "2 objects active since" in check.detail
    assert check.answered_mjd == NOW - 1


def test_the_answer_is_what_this_run_listed_the_known_newest_may_be_later():
    """2026-11-04: newest_mjd is the newest known, max(state, listing); answered_mjd only what this listing returned."""
    check = mon.check_listing("lsst", listed(NOW - 3, NOW - 2), None, (53.1, -27.8, 2.82), SINCE, NOW - 1,
                              probe=lambda c, f: pytest.fail("checked a non-empty listing"))
    assert (check.newest_mjd, check.answered_mjd) == (NOW - 1, NOW - 2)


ANSWERED = mon.ListingCheck("objects", "37 objects active since 2026-10-21", 61338.4, 61338.3995)


@pytest.mark.parametrize("listing,phrase", [
    (ANSWERED, "; newest ZTF: 2026-10-25 09:35 UTC (9.9 d)"),
    (mon.ListingCheck("quiet", "none active since 2026-10-21; upstream quiet (checked on ztf:ZTF21x)", 61338.4, 61338.3995),
     "; newest ZTF: 2026-10-25 09:35 UTC (9.9 d)"),
    (mon.ListingCheck("failed", "alerce ztf listing page 3: 504 (objects gathered before it are kept)", 61338.3995),
     "; last known ZTF: 2026-10-25 09:35 UTC (9.9 d), from an earlier run"),
    (mon.ListingCheck("failed", "empty listing, unverified: never observed ...", None), ""),
])
def test_the_listing_line_states_this_runs_answer_or_the_last_known_value_from_an_earlier_run(listing, phrase):
    """Aged from the run's start (2026-11-04 07:00:49 UTC, the 11-04 ecdfs run); a failure never calls a value newest."""
    assert mon.newest_phrase("ztf", listing, 61348.292234) == phrase


def test_an_empty_listing_is_quiet_when_the_known_newest_comes_back_older_than_the_window():
    calls = []
    check = mon.check_listing("lsst", [], None, (53.1, -27.8, 2.82), SINCE, 61108.03,
                                   probe({"oid": 314007346664701955, "lastmjd": 61108.03}, calls))
    assert calls == [61108.03 - mon.LISTING_CHECK_MARGIN_DAYS]  # light: only the last night is sorted
    assert check.status == "quiet" and "upstream quiet (checked on lsst:314007346664701955)" in check.detail
    assert (check.answered_mjd, check.newest_mjd) == (61108.03, 61108.03)
    assert "2026-03-09" not in check.detail  # the line adds the date, once (newest_phrase)


def test_an_empty_listing_with_an_object_inside_the_window_is_a_silent_failure():
    check = mon.check_listing("lsst", [], None, (53.1, -27.8, 2.82), SINCE, 61108.03, probe({"oid": 9, "lastmjd": NOW - 1}))
    assert check.status == "failed" and "the listing failed silently" in check.detail
    assert check.newest_mjd == check.answered_mjd == NOW - 1  # the check's object is this run's answer


@pytest.mark.parametrize("known,answer,says", [
    (61108.03, (None, None), "the known newest object did not come back"),
    (61108.03, (None, "alerce lsst newest-in-cone check: 504"), "the check could not run: alerce lsst newest-in-cone check: 504"),
    (None, (None, None), "never observed, or the listing service failed - cannot tell which"),
])
def test_an_empty_listing_that_cannot_be_verified_is_a_failure_never_quiet(known, answer, says):
    calls = []
    check = mon.check_listing("lsst", [], None, (53.1, -27.8, 2.82), SINCE, known,
                                   lambda c, f: calls.append(f) or answer)
    assert check.status == "failed" and says in check.detail
    assert (check.newest_mjd, check.answered_mjd) == (known, None)  # nothing answered: the known value stays
    assert calls == [None if known is None else known - mon.LISTING_CHECK_MARGIN_DAYS]  # no known newest: whole cone


def test_the_ztf_listing_is_guarded_the_same_way():
    check = mon.check_listing("ztf", [], None, (53.1, -27.8, 2.82), SINCE, SINCE - 40,
                              probe({"oid": "ZTF21aabbvto", "lastmjd": SINCE - 40}))
    assert check.status == "quiet" and "upstream quiet (checked on ztf:ZTF21aabbvto)" in check.detail
    check = mon.check_listing("ztf", [], None, (53.1, -27.8, 2.82), SINCE, SINCE - 40,
                              probe({"oid": "ZTF26x", "lastmjd": NOW - 1}))
    assert check.status == "failed" and "ALeRCE holds ztf:ZTF26x" in check.detail


def test_a_listing_error_is_a_failure_without_any_check():
    check = mon.check_listing("lsst", listed(NOW - 1), "alerce lsst listing page 3: 504", (53.1, -27.8, 2.82), SINCE,
                                   None, probe=lambda c, f: pytest.fail("checked a failed listing"))
    assert check.status == "failed"


def test_quiet_weeks_and_a_real_outage_do_not_look_alike(env, capsys):
    """Rubin off sky: an empty listing every run. Then the listing fails silently while data exists upstream."""
    env["newest"]["lsst"] = ({"oid": 314007346664701955, "lastmjd": 61108.03}, None)
    assert mon.scan("ecdfs", (53.1, -27.8, 2.82)) == 0
    out = capsys.readouterr()
    assert "LSST listing (ALeRCE): none active since" in out.out and "upstream quiet" in out.out
    (line,) = [ln for ln in out.out.splitlines() if ln.startswith("LSST listing (ALeRCE):")]
    assert line.endswith("; newest LSST: 2026-03-09 00:43 UTC (202.0 d)") and line.count("2026-03-09") == 1
    assert "ERROR" not in out.err and state("ecdfs")["newest_mjd"]["lsst"] == 61108.03
    assert report("transient_ecdfs_*.json")["listings"]["lsst"]["answered_mjd"] == 61108.03
    for path in mon.REPORTS_DIR.glob("*.json"):
        path.unlink()

    env["newest"]["lsst"] = ({"oid": 314999, "lastmjd": NOW - 1}, None)  # Rubin is back; the listing still answers empty
    assert mon.scan("ecdfs", (53.1, -27.8, 2.82)) == 1
    out = capsys.readouterr()
    assert ("LSST listing (ALeRCE): FAILED - empty listing, yet ALeRCE holds lsst:314999 inside the window: the "
            "listing failed silently; newest LSST: 2026-09-26 00:00 UTC (24.0 h)") in out.out
    assert "ERROR: LSST listing failed" in out.err
    # first run: nothing stored, so the whole cone (the one heavy query); then the stored newest bounds it
    assert [f for survey, f in env["probes"] if survey == "lsst"] == [None, 61108.03 - mon.LISTING_CHECK_MARGIN_DAYS]
    assert state("ecdfs")["newest_mjd"]["lsst"] == NOW - 1  # state still saved: the ZTF side and the parked objects are valid


def test_a_silent_empty_ztf_listing_fails_the_run_like_an_lsst_one(env, capsys):
    """ZTF comes from ALeRCE too since 2026-10-03: same server, same silent-empty failure, same guard."""
    env["newest"]["ztf"] = ({"oid": "ZTF26x", "lastmjd": NOW - 1}, None)  # ZTF has data in the window; the listing says none
    assert mon.scan("ecdfs", (53.1, -27.8, 2.82)) == 1
    out = capsys.readouterr()
    assert "ZTF listing (ALeRCE): FAILED - empty listing, yet ALeRCE holds ztf:ZTF26x" in out.out
    assert "ERROR: ZTF listing failed" in out.err
    assert "LSST listing (ALeRCE): none active since" in out.out  # the other survey is judged on its own


def test_a_state_from_before_the_switch_is_read_and_rewritten(env, capsys):
    """2026-10-03: lsst_newest_mjd became newest_mjd per survey; ANTARES-locus TNS entries cannot be asked by position."""
    mon._save_state(mon.state_file("ecdfs"), {"last_mjd": NOW - 1, "reported": {}, "parked": {},
                                              "lsst_newest_mjd": 61108.03,
                                              "tns_pending": {"ztf:Z": {"locus": "ANT_1", "alerted_mjd": NOW - 1}}})
    env["newest"]["lsst"] = ({"oid": 3140, "lastmjd": 61108.03}, None)
    assert mon.scan("ecdfs", (53.1, -27.8, 2.82)) == 0
    assert ("lsst", 61108.03 - mon.LISTING_CHECK_MARGIN_DAYS) in env["probes"]  # the stored newest was used
    s = state("ecdfs")
    assert s["newest_mjd"]["lsst"] == 61108.03 and "lsst_newest_mjd" not in s
    assert "tns_pending" not in s and s["tns_watch"] == {}


# --- footprint atlas for the DR10 host search ----------------------------------------------


def test_footprint_classifier_loads_the_atlas_once_on_first_use(monkeypatch):
    loads, seen = [], []
    the_atlas = pd.DataFrame({"ref_id": [7]})
    monkeypatch.setattr(mon.sky_catalog, "footprint_atlas", lambda cone: loads.append(cone) or the_atlas)
    monkeypatch.setattr(mon.sky_catalog, "footprint_bricks", lambda cone: loads.append("bricks") or pd.DataFrame())
    monkeypatch.setattr(mon.sky_catalog, "classify", lambda ra, dec, atlas, coverage: seen.append(atlas) or Crossmatch("none"))
    classify = mon.footprint_classifier((150.1, 2.5, 2.82))
    assert loads == []  # nothing fetched until a DR10 lookup happens
    classify(150.0, 2.0)
    classify(150.2, 2.1)
    assert loads == [(150.1, 2.5, 2.82), "bricks"]
    assert all(a is the_atlas for a in seen) and len(seen) == 2


def test_footprint_classifier_atlas_outage_warns_once_and_runs_step_1(monkeypatch, capsys):
    loads, seen = [], []

    def down(cone):
        loads.append(cone)
        raise mon.sky_catalog.CatalogError("atlas for cone 150.1 2.5 2.82: ReadTimeout")

    monkeypatch.setattr(mon.sky_catalog, "footprint_atlas", down)
    monkeypatch.setattr(mon.sky_catalog, "footprint_bricks", lambda cone: pd.DataFrame())
    monkeypatch.setattr(mon.sky_catalog, "classify", lambda ra, dec, atlas, coverage: seen.append(atlas) or Crossmatch("none"))
    classify = mon.footprint_classifier((150.1, 2.5, 2.82))
    classify(150.0, 2.0)
    classify(150.2, 2.1)
    assert len(loads) == 1 and seen == [None, None]
    # a "none" made without the atlas is flagged, never a plain orphan
    assert classify(150.0, 2.0).evidence == ["atlas unavailable: large hosts not searched"]
    err = capsys.readouterr().err
    assert err.count("WARN: Siena Galaxy Atlas unavailable") == 1 and 'within 30" only this run' in err


# --- scan: state, reporting, retry ------------------------------------------------------------


@pytest.fixture
def env(tmp_path, monkeypatch, sources):
    monkeypatch.setattr(mon, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(mon, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(mon, "now_mjd", lambda: NOW)
    monkeypatch.setattr(mon.time, "sleep", lambda s: None)
    monkeypatch.setattr(mon.sky_catalog, "classify", xm("host", 'EXP r=21.0 sep=1.0" d_DLR=0.8'))
    monkeypatch.setattr(mon.sky_catalog, "footprint_atlas", lambda cone: pd.DataFrame())
    monkeypatch.setattr(mon.sky_catalog, "footprint_bricks", lambda cone: pd.DataFrame(columns=["brickname"]))
    monkeypatch.setattr(mon.tns, "tns_lookup", lambda ra, dec: {})  # ALeRCE's TNS: "none" unless a test says otherwise
    monkeypatch.setattr(mon.tns, "tns_once", lambda ra, dec: {})
    monkeypatch.setattr(mon.sky_catalog, "counterpart_snr", lambda ra, dec: pytest.fail("S/N asked in a test that set none"))
    installed("D", "ecdfs")  # an existing installation; the first-run (--init) tests remove it
    return sources


def installed(*names):
    """Empty state for each footprint, and the watch file: what --init leaves behind."""
    for name in names:
        mon._save_state(mon.state_file(name), {"last_mjd": None, "reported": {}, "parked": {}})
    (mon.STATE_DIR / mon.PS_WATCH_FILE).write_text(json.dumps({"objects": {}}))


def state(name="D"):
    return json.loads(mon.state_file(name).read_text())


def tns_record(name, ra, dec, prefix="SN", kind="SN Ia", found="2026-09-24 03:00:00"):
    """A record from ALeRCE's TNS service, in the shape seen 2026-10-02."""
    return {"objname": name, "name_prefix": prefix, "object_type": {"id": 3 if kind else None, "name": kind},
            "radeg": ra, "decdeg": dec, "discoverydate": found}


def test_first_scan_reports_keyed_on_survey_ids(env, capsys, monkeypatch):
    env["objects"] = [obj(NEW_ZTF)]
    monkeypatch.setattr(mon.tns, "tns_lookup", lambda ra, dec: tns_record("2026abc", ra, dec))
    assert mon.scan("D", (150, 12, 1)) == 0
    out = capsys.readouterr().out
    assert "1 TRANSIENT ALERTS" in out and "--- host (1) ---" in out
    assert "ztf:ZTF26aaa  host · bracketed  new: bracketed" in out
    assert "  onset: first detection 2026-09-22 (MJD 61305.00), 3 detections, bracketed by a quiet point 1.0 d before" in out
    assert '  DR10: host - EXP r=21.0 sep=1.0" d_DLR=0.8' in out
    assert "  brokers: alerce ZTF26aaa" in out and "  TNS: SN 2026abc, SN Ia, discovered 2026-09-24" in out
    s = state()
    assert s["reported"] == {"ztf:ZTF26aaa": {"first_reported_mjd": NOW, "category": "host",
                                              "pairs": [["host", "bracketed"]], "ra": HIGH_B[0], "dec": HIGH_B[1]}}
    assert s["parked"] == {}


@pytest.mark.parametrize("answer,line", [
    (lambda ra, dec: tns_record("2026abc", ra, dec), "  TNS: SN 2026abc, SN Ia, discovered 2026-09-24"),
    (lambda ra, dec: {}, f"  TNS: {mon.TNS_NONE_YET}"),
    (lambda ra, dec: None, f"  TNS: {mon.TNS_UNAVAILABLE}"),
    (lambda ra, dec: tns_record("2026far", ra + 5 / 3600, dec), f"  TNS: {mon.TNS_NONE_YET}"),  # 5": another object
])
def test_every_alert_says_what_tns_knows_never_a_silent_absence(env, capsys, monkeypatch, answer, line):
    """Every object asked by position at ALeRCE's TNS service: no 'not checked' any more (2026-10-03)."""
    env["objects"] = [obj(NEW_ZTF), obj(LSST_ONSET, survey="lsst", oid="1706", ra=HIGH_B[0] + 0.01)]
    monkeypatch.setattr(mon.tns, "tns_lookup", answer)
    mon.scan("D", (150, 12, 1))
    assert capsys.readouterr().out.count(line) == 2  # the ZTF and the LSST alert alike


@pytest.mark.parametrize("first", [lambda ra, dec: {}, lambda ra, dec: None], ids=["none", "unavailable"])
def test_none_and_unavailable_are_asked_again_until_a_record_appears(env, capsys, monkeypatch, first):
    """'none' only means nobody had reported it yet: SN 2026kfw's report came 5 days after our alert."""
    env["objects"] = [obj(NEW_ZTF)]
    monkeypatch.setattr(mon.tns, "tns_lookup", first)
    assert mon.scan("D", (150, 12, 1)) == 0  # display context: never fails the run
    assert set(state()["tns_watch"]) == {"ztf:ZTF26aaa"}

    calls = []
    monkeypatch.setattr(mon.tns, "tns_once", lambda ra, dec: calls.append(ra) or {})
    mon.scan("D", (150, 12, 1))  # still nothing: kept, asked with one call
    assert calls == [HIGH_B[0]] and "ztf:ZTF26aaa" in state()["tns_watch"]
    capsys.readouterr()
    monkeypatch.setattr(mon.tns, "tns_once", lambda ra, dec: tns_record("2026abc", ra, dec, found="2026-10-02 01:00:00"))
    monkeypatch.setattr(mon, "now_mjd", lambda: NOW + 6)
    mon.scan("D", (150, 12, 1))
    out = capsys.readouterr().out
    assert "No new transient alerts." in out
    assert "  ztf:ZTF26aaa (alert 2026-09-27): now in TNS: SN 2026abc, SN Ia, discovered 2026-10-02 (5 d after our alert)" in out
    assert state()["tns_watch"] == {}


def test_the_tns_watch_gives_up_after_30_days(env, capsys, monkeypatch):
    env["objects"] = [obj(NEW_ZTF)]
    mon.scan("D", (150, 12, 1))
    env["objects"] = []
    monkeypatch.setattr(mon, "now_mjd", lambda: NOW + mon.TNS_RECHECK_DAYS + 1)
    monkeypatch.setattr(mon.tns, "tns_once", lambda ra, dec: pytest.fail("asked past the 30 days"))
    capsys.readouterr()
    mon.scan("D", (150, 12, 1))
    assert "ztf:ZTF26aaa (alert 2026-09-27): no TNS record 30 d after the alert; no longer asked" in capsys.readouterr().out
    assert state()["tns_watch"] == {}


def test_a_tns_lookup_that_raises_never_fails_the_run(env, capsys, monkeypatch):
    env["objects"] = [obj(NEW_ZTF)]

    def broken(ra, dec):
        raise RuntimeError("TNS gone")

    monkeypatch.setattr(mon.tns, "tns_lookup", broken)
    assert mon.scan("D", (150, 12, 1)) == 0
    assert f"  TNS: {mon.TNS_UNAVAILABLE}" in capsys.readouterr().out
    monkeypatch.setattr(mon.tns, "tns_once", broken)
    assert mon.scan("D", (150, 12, 1)) == 0 and "ztf:ZTF26aaa" in state()["tns_watch"]


# --- one event, one alert: another survey's detection is a note (2026-10-02) -----------------

LSST_RISER_HERE = [det(FIRST - 2, 24.5, survey="lsst", oid="1706"), det(FIRST - 1, 24.4, survey="lsst", oid="1706"),
                   forced(FIRST, 800.0, 60.0), forced(NOW - 1, 1500.0, 70.0)]


def judge(objs, state, now=NOW):
    return mon.judge_day(objs, now, SINCE, state, xm("host", "EXP"))


def test_the_same_event_in_two_surveys_on_one_day_is_one_alert_and_a_note():
    lsst = obj(LSST_RISER_HERE, survey="lsst", oid="1706")
    ztf = obj(NEW_ZTF, ra=HIGH_B[0] + 0.3 / 3600)  # 0.3" away: the replay's three pairs sat 0.26-0.34" apart
    day = judge([ztf, lsst], {"reported": {}, "parked": {}})
    assert [(c.obj.key, n) for c, n in day.alerts] == [("lsst:1706", ["rising"])]  # first detected first
    assert [(c.obj.key, n, a) for c, n, a in day.notes] == [("ztf:ZTF26aaa", ["bracketed"], "lsst:1706")]


def test_the_survey_that_saw_it_first_holds_the_alert_not_the_one_that_sorts_first():
    late_lsst = [det(FIRST + 1, 24.5, survey="lsst", oid="1706"), det(FIRST + 2, 24.4, survey="lsst", oid="1706"),
                 forced(FIRST + 2.5, 800.0, 60.0), forced(NOW - 0.5, 1500.0, 70.0)]
    day = judge([obj(late_lsst, survey="lsst", oid="1706"), obj(NEW_ZTF, ra=HIGH_B[0] + 0.3 / 3600)],
                {"reported": {}, "parked": {}})
    assert [c.obj.key for c, _ in day.alerts] == ["ztf:ZTF26aaa"]  # ZTF first detected at FIRST, LSST a day later
    assert [(c.obj.key, a) for c, _, a in day.notes] == [("lsst:1706", "ztf:ZTF26aaa")]


def test_a_later_detection_by_another_survey_is_a_note_on_the_earlier_alert():
    state = {"reported": {}, "parked": {}}
    assert [c.obj.key for c, _ in judge([obj(LSST_RISER_HERE, survey="lsst", oid="1706")], state).alerts] == ["lsst:1706"]
    # the LSST object is not in today's objects: its listing failed, say - the note must not depend on it
    day = judge([obj(NEW_ZTF, ra=HIGH_B[0] + 0.3 / 3600)], state, now=NOW + 1)
    assert day.alerts == [] and [(c.obj.key, a) for c, _, a in day.notes] == [("ztf:ZTF26aaa", "lsst:1706")]
    assert state["reported"]["ztf:ZTF26aaa"]["note_on"] == "lsst:1706"


def test_beyond_the_association_radius_or_within_one_survey_both_alert():
    far = obj(NEW_ZTF, ra=HIGH_B[0] + 2.0 / 3600)  # 2" > ASSOC_RADIUS_ARCSEC
    day = judge([obj(LSST_RISER_HERE, survey="lsst", oid="1706"), far], {"reported": {}, "parked": {}})
    assert len(day.alerts) == 2 and day.notes == []
    twin = obj(NEW_ZTF, oid="ZTF26bbb", ra=HIGH_B[0] + 0.3 / 3600)  # same survey, same place: a recurrence, not grouped
    day = judge([obj(NEW_ZTF), twin], {"reported": {}, "parked": {}})
    assert len(day.alerts) == 2 and day.notes == []


def test_the_scan_report_shows_the_note_under_its_own_heading(env, capsys):
    env["objects"] = [obj(LSST_RISER_HERE, survey="lsst", oid="1706"), obj(NEW_ZTF, ra=HIGH_B[0] + 0.3 / 3600)]
    mon.scan("D", (150, 12, 1))
    out = capsys.readouterr().out
    assert "1 TRANSIENT ALERTS" in out and "--- the same events, seen by another survey: notes on earlier alerts (1) ---" in out
    assert "ztf:ZTF26aaa  host · bracketed  new: bracketed  note on lsst:1706" in out


# --- the point-source watch (2026-10-02) ---------------------------------------------------


def tns_answers(answers):
    """A tns_lookup by object position: answers maps an RA to the record (None: unavailable; {}: not in TNS)."""
    calls = []

    def lookup(ra, dec):
        calls.append(ra)
        a = answers[ra]
        return dict(a, radeg=ra, decdeg=dec) if a else a
    lookup.calls = calls
    return lookup


def classified(name, prefix="SN", kind="SN Ia"):
    """A TNS record in the shapes seen 2026-10-02: unclassified AT has object_type {"id": None, "name": None}."""
    return {"objname": name, "name_prefix": prefix, "object_type": {"id": 3 if kind else None, "name": kind}}


def ps_alerts(*ras, mjd=NOW, source="live D"):
    return [(f"lsst:{ra}", float(ra), 2.0, mjd, source) for ra in ras]


@pytest.fixture
def watch(tmp_path, monkeypatch):
    monkeypatch.setattr(mon, "STATE_DIR", tmp_path)
    snrs = {}  # the found point source's S/N by RA
    monkeypatch.setattr(mon.sky_catalog, "counterpart_snr", lambda ra, dec: (True, snrs[ra]))
    return snrs


def test_only_spectroscopic_supernovae_below_the_snr_count(watch, monkeypatch):
    watch.update({1: 6.3, 2: 5.0, 3: 6.0, 4: 12.0})
    monkeypatch.setattr(mon.tns, "tns_lookup", tns_answers({
        1: classified("2026kfw"), 2: classified("2026aaa", prefix="AT", kind=None),  # unclassified: still watched
        3: classified("2026bbb", prefix="AT", kind="AGN"),  # classified, not a supernova
        4: classified("2026ccc"), 5: {}}))  # a supernova on a well-detected point source; not in TNS
    lines = mon.point_source_watch(ps_alerts(1, 2, 3, 4, 5), NOW)
    assert lines == ["Point-source watch: 2 point_source alerts awaiting a TNS classification; spectroscopic supernovae "
                     "on a point source below S/N 7: 1 of 3 (SN 2026kfw S/N 6.3); classified as other than supernovae: 1 AGN",
                     "  lsst:3, a point_source alert, is classified in TNS: AT 2026bbb, AGN - "
                     "the nuclear case the point_source label exists for"]


def test_a_supernova_on_a_point_source_with_no_measurement_is_shown_and_never_counted(tmp_path, monkeypatch):
    """
    2026-11-04: every band of the point source's S/N row empty is no measurement. A 0 would count it below
    S/N 7 and make this a pattern of three; it is shown on the line instead. Through the real counterpart_snr.
    """
    monkeypatch.setattr(mon, "STATE_DIR", tmp_path)
    real = mon.sky_catalog.counterpart_snr
    answers = {1: snr_answer(*SNR_COLUMNS), 2: snr_answer(flux=3.0), 3: snr_answer(flux=3.0)}  # 2 and 3: S/N 6.0
    monkeypatch.setattr(mon.sky_catalog, "counterpart_snr",
                        lambda ra, dec: real(ra, dec, fetch=psf_at(ra, dec), get=answers[int(ra)]))
    monkeypatch.setattr(mon.tns, "tns_lookup", tns_answers({1: classified("2026a"), 2: classified("2026b"),
                                                            3: classified("2026c")}))
    assert mon.point_source_watch(ps_alerts(1, 2, 3), NOW) == [
        "Point-source watch: 0 point_source alerts awaiting a TNS classification; spectroscopic supernovae on a point "
        "source below S/N 7: 2 of 3 (SN 2026b S/N 6.0, SN 2026c S/N 6.0); 1 on a point source with no S/N measurement, "
        "not counted (SN 2026a)"]
    watched = json.loads((tmp_path / mon.PS_WATCH_FILE).read_text())["objects"]
    assert (watched["lsst:1"]["status"], watched["lsst:1"]["snr"]) == ("sn", None)


def test_three_make_a_pattern_and_the_notice_repeats_every_run(watch, monkeypatch):
    watch.update({1: 6.3, 2: 5.9, 3: 6.8})
    monkeypatch.setattr(mon.tns, "tns_lookup", tns_answers({1: classified("2026kfw"), 2: classified("2026x"),
                                                            3: classified("2026y")}))
    assert len(mon.point_source_watch(ps_alerts(1, 2), NOW)) == 1  # two can be coincidence
    lines = mon.point_source_watch(ps_alerts(3), NOW + 1)
    assert lines[1].startswith("NOTICE: 3 spectroscopic supernovae labelled point_source")
    monkeypatch.setattr(mon.tns, "tns_lookup", lambda ra, dec: pytest.fail("settled objects are not asked again"))
    assert mon.point_source_watch([], NOW + 30)[1].startswith("NOTICE: 3")  # nothing new: still there


def test_changing_the_threshold_or_the_rule_is_what_stops_the_notice(watch, monkeypatch):
    watch.update({1: 6.3, 2: 5.9, 3: 6.8})
    monkeypatch.setattr(mon.tns, "tns_lookup", tns_answers({1: classified("2026kfw"), 2: classified("2026x"),
                                                            3: classified("2026y")}))
    assert len(mon.point_source_watch(ps_alerts(1, 2, 3), NOW)) == 2
    monkeypatch.setattr(mon, "PS_PATTERN", 4)
    assert len(mon.point_source_watch([], NOW)) == 1
    monkeypatch.setattr(mon, "PS_PATTERN", 3)
    monkeypatch.setattr(mon, "point_source_rule", lambda: "a-new-rule")  # e.g. a floor adopted
    (line,) = mon.point_source_watch([], NOW)
    assert ": 0 of 3; 3 more under an earlier version of the rule, not counted" in line


def test_one_supernova_counts_once_whether_live_replayed_or_seen_twice(watch, monkeypatch):
    watch.update({1: 6.3, 2: 6.3})
    monkeypatch.setattr(mon.tns, "tns_lookup", tns_answers({1: classified("2026kfw"), 2: classified("2026kfw")}))
    mon.point_source_watch(ps_alerts(1, source="replay D"), NOW)
    (line,) = mon.point_source_watch(ps_alerts(1) + ps_alerts(2), NOW)  # the same key again, and a second key
    assert ": 1 of 3 (SN 2026kfw S/N 6.3)" in line


def test_unclassified_alerts_are_asked_weekly_until_classified_or_too_old(watch, monkeypatch):
    lookup = tns_answers({1: classified("2026aaa", prefix="AT", kind=None)})
    monkeypatch.setattr(mon.tns, "tns_lookup", lookup)
    mon.point_source_watch(ps_alerts(1), NOW)
    mon.point_source_watch([], NOW + 3)  # not due
    mon.point_source_watch([], NOW + mon.PS_CHECK_EVERY_DAYS)  # due
    assert lookup.calls == [1.0, 1.0]
    mon.point_source_watch([], NOW + mon.PS_WATCH_DAYS + 7)
    watched = json.loads((mon.STATE_DIR / mon.PS_WATCH_FILE).read_text())["objects"]
    assert watched["lsst:1"]["status"] == "expired"


def test_an_unanswered_lookup_is_asked_again_never_counted(watch, monkeypatch):
    lookup = tns_answers({1: None})
    monkeypatch.setattr(mon.tns, "tns_lookup", lookup)
    (line,) = mon.point_source_watch(ps_alerts(1), NOW)
    assert "1 lookups unavailable, asked again next run" in line
    mon.point_source_watch([], NOW + 1)  # not marked as checked: asked again at once
    assert lookup.calls == [1.0, 1.0]


def test_classes_are_read_from_the_type_never_the_prefix(watch, monkeypatch):
    """AT 2018hyz, a TDE, comes back with prefix TDE; a CV may keep AT; a SLSN is a supernova."""
    watch.update({3: 6.0})
    lookup = tns_answers({1: classified("2018hyz", prefix="TDE", kind="TDE"),
                          2: classified("2026cv", prefix="AT", kind="CV"),
                          3: classified("2026sl", prefix="SN", kind="SLSN-I"),
                          4: classified("2026aaa", prefix="AT", kind=None)})
    monkeypatch.setattr(mon.tns, "tns_lookup", lookup)
    lines = mon.point_source_watch(ps_alerts(1, 2, 3, 4), NOW)
    assert lines[0] == ("Point-source watch: 1 point_source alerts awaiting a TNS classification; spectroscopic supernovae "
                        "on a point source below S/N 7: 1 of 3 (SN 2026sl S/N 6.0); "
                        "classified as other than supernovae: 1 CV, 1 TDE")
    # a classification is said, never dropped silently; a nuclear one names why the label exists
    assert lines[1:] == ["  lsst:1, a point_source alert, is classified in TNS: TDE 2018hyz, TDE - "
                         "the nuclear case the point_source label exists for",
                         "  lsst:2, a point_source alert, is classified in TNS: AT 2026cv, CV"]
    mon.point_source_watch([], NOW + 30)
    assert lookup.calls.count(1.0) == lookup.calls.count(2.0) == 1  # settled: never asked again
    assert lookup.calls.count(4.0) == 2  # unclassified: asked again, weekly


def test_an_interrupted_watch_write_keeps_the_evidence(watch, monkeypatch):
    """The watch file is written like the state: a crash mid-write must not reset the count."""
    watch.update({1: 6.3})
    monkeypatch.setattr(mon.tns, "tns_lookup", tns_answers({1: classified("2026kfw"), 2: classified("2026x", kind=None)}))
    mon.point_source_watch(ps_alerts(1), NOW)
    before = (mon.STATE_DIR / mon.PS_WATCH_FILE).read_text()
    real = mon.Path.write_text

    def half(self, data, *a, **k):
        real(self, data[: len(data) // 2], *a, **k)
        raise KeyboardInterrupt

    monkeypatch.setattr(mon.Path, "write_text", half)
    with pytest.raises(KeyboardInterrupt):
        mon._point_source_watch(ps_alerts(2), NOW + 1, True, [])  # the inner function: an interrupt is not an error to report
    monkeypatch.setattr(mon.Path, "write_text", real)
    assert (mon.STATE_DIR / mon.PS_WATCH_FILE).read_text() == before
    assert ": 1 of 3 (SN 2026kfw S/N 6.3)" in mon.point_source_watch([], NOW + 2)[0]


def test_the_watch_never_fails_the_run(env, capsys, monkeypatch):
    env["objects"] = [obj(NEW_ZTF)]
    monkeypatch.setattr(mon.sky_catalog, "classify", xm("point_source", 'PSF r=26.1 at 0.2"'))

    def broken(ra, dec):
        raise RuntimeError("TNS gone")

    monkeypatch.setattr(mon.tns, "tns_lookup", broken)
    assert mon.scan("D", (150, 12, 1)) == 0
    out, err = capsys.readouterr()
    assert "; 1 lookups unavailable, asked again next run" in out  # an entry's failure, not the watch's
    assert "ztf:ZTF26aaa: point-source watch lookup failed (RuntimeError: TNS gone)" in err
    assert any("point-source watch lookup failed" in w for w in report()["warnings"])  # the journal rotates, the report stays


def test_a_watch_file_that_cannot_be_read_costs_the_watch_not_the_run(env, capsys):
    env["objects"] = [obj(NEW_ZTF)]
    (mon.STATE_DIR / mon.PS_WATCH_FILE).write_text("{ not json")
    assert mon.scan("D", (150, 12, 1)) == 0
    assert "Point-source watch: could not run (JSONDecodeError" in capsys.readouterr().out


BAD_RECORDS = {
    "no_position": lambda ra, dec: {k: v for k, v in dict(classified("2026bad"), radeg=ra, decdeg=dec).items() if k != "radeg"},
    "nan_position": lambda ra, dec: dict(classified("2026bad"), radeg=float("nan"), decdeg=dec),
    "no_name": lambda ra, dec: {"name_prefix": "SN", "object_type": {"name": "SN Ia"}, "radeg": ra, "decdeg": dec},
    "type_not_text": lambda ra, dec: dict(classified("2026bad"), radeg=ra, decdeg=dec, object_type={"name": 3}),
    "type_not_a_record": lambda ra, dec: dict(classified("2026bad"), radeg=ra, decdeg=dec, object_type="SN Ia"),
}


@pytest.mark.parametrize("bad", BAD_RECORDS.values(), ids=BAD_RECORDS.keys())
def test_one_bad_record_costs_one_entry_the_others_are_judged_and_saved(watch, capsys, monkeypatch, bad):
    """Before, one bad record ended the watch for the run, and saved nothing of what it had found."""
    watch.update({2: 6.3})
    good = tns_answers({2: classified("2026kfw")})
    monkeypatch.setattr(mon.tns, "tns_lookup", lambda ra, dec: bad(ra, dec) if ra == 1 else good(ra, dec))
    warnings = []
    lines = mon.point_source_watch(ps_alerts(1, 2), NOW, warnings=warnings)
    objects = json.loads((mon.STATE_DIR / mon.PS_WATCH_FILE).read_text())["objects"]
    assert objects["lsst:2"]["status"] == "sn" and "1 of 3 (SN 2026kfw S/N 6.3)" in lines[0]
    assert (objects["lsst:1"]["status"], objects["lsst:1"]["checked_mjd"]) == ("watching", None)  # still due
    assert "tns" not in objects["lsst:1"] and "1 lookups unavailable, asked again next run" in lines[0]
    assert len(warnings) == 1 and warnings[0].startswith("lsst:1: ") and f"WARN: {warnings[0]}" in capsys.readouterr().err


def test_a_record_the_watch_cannot_read_is_never_settled_as_not_there(watch, monkeypatch):
    """A NaN position read as 0" here too: it would have matched and been judged on another object's record."""
    monkeypatch.setattr(mon.tns, "tns_lookup", BAD_RECORDS["nan_position"])
    mon.point_source_watch(ps_alerts(1), NOW)
    entry = json.loads((mon.STATE_DIR / mon.PS_WATCH_FILE).read_text())["objects"]["lsst:1"]
    assert entry["status"] == "watching" and entry["checked_mjd"] is None and "tns" not in entry


def test_a_watch_match_is_the_same_2_arcsec_as_the_alerts(watch, monkeypatch):
    """One radius (PS_MATCH_ARCSEC was a second copy until 2026-11-07): 1.9" matches, 2.1" is another object."""
    monkeypatch.setattr(mon.tns, "tns_lookup", lambda ra, dec: dict(classified("2026near", kind=None), radeg=ra, decdeg=dec + 1.9 / 3600))
    mon.point_source_watch(ps_alerts(1), NOW)
    assert json.loads((mon.STATE_DIR / mon.PS_WATCH_FILE).read_text())["objects"]["lsst:1"]["tns"] == "SN 2026near"
    monkeypatch.setattr(mon.tns, "tns_lookup", lambda ra, dec: dict(classified("2026far", kind=None), radeg=ra, decdeg=dec + 2.1 / 3600))
    mon.point_source_watch(ps_alerts(5), NOW)
    assert "tns" not in json.loads((mon.STATE_DIR / mon.PS_WATCH_FILE).read_text())["objects"]["lsst:5"]


def test_the_daily_report_carries_the_watch_and_a_dry_run_saves_nothing(env, capsys, monkeypatch):
    env["objects"] = [obj(NEW_ZTF)]
    monkeypatch.setattr(mon.sky_catalog, "classify", xm("point_source", 'PSF r=26.1 at 0.2"'))
    monkeypatch.setattr(mon.tns, "tns_lookup", lambda ra, dec: {**classified("2026kfw"), "radeg": ra, "decdeg": dec})
    monkeypatch.setattr(mon.sky_catalog, "counterpart_snr", lambda ra, dec: (True, 6.3))
    watch_before = (mon.STATE_DIR / mon.PS_WATCH_FILE).read_text()
    mon.scan("D", (150, 12, 1), dry_run=True)
    assert "below S/N 7: 1 of 3 (SN 2026kfw S/N 6.3)" in capsys.readouterr().out
    assert (mon.STATE_DIR / mon.PS_WATCH_FILE).read_text() == watch_before
    mon.scan("D", (150, 12, 1))
    watched = json.loads((mon.STATE_DIR / mon.PS_WATCH_FILE).read_text())["objects"]
    assert watched["ztf:ZTF26aaa"]["source"] == "live D" and watched["ztf:ZTF26aaa"]["status"] == "sn"


def test_second_scan_does_not_repeat_but_new_condition_does(env, capsys):
    flat = [lim(NOW - 4, 21.5), det(NOW - 3, 20.5), det(NOW - 2.5, 20.5), det(NOW - 2.2, 20.5)]
    env["objects"] = [obj(flat)]
    mon.scan("D", (150, 12, 1))
    mon.scan("D", (150, 12, 1))
    runs = capsys.readouterr().out.split("Footprint D")
    assert "new: bracketed" in runs[1] and "No new transient alerts." in runs[2]
    env["objects"] = [obj(flat + [det(NOW - 0.3, 19.0)])]
    mon.scan("D", (150, 12, 1))
    assert "new: rising, rapid_rise" in capsys.readouterr().out


def test_a_new_category_is_reported_again_unchecked_becoming_a_verdict(env, capsys, monkeypatch):
    """Re-reporting keys on the (category, path) pair: either axis changing is news."""
    env["objects"] = [obj(NEW_ZTF)]
    monkeypatch.setattr(mon.sky_catalog, "classify", xm("unavailable", "tile 1501,920: ReadTimeout"))
    mon.scan("D", (150, 12, 1))
    assert "ztf:ZTF26aaa  unchecked · bracketed  new: bracketed" in capsys.readouterr().out
    monkeypatch.setattr(mon.sky_catalog, "classify", xm("host", 'EXP r=21.0 sep=1.0" d_DLR=0.8'))
    mon.scan("D", (150, 12, 1))
    assert "ztf:ZTF26aaa  host · bracketed  new: bracketed" in capsys.readouterr().out
    assert state()["reported"]["ztf:ZTF26aaa"]["pairs"] == [["host", "bracketed"], ["unchecked", "bracketed"]]
    mon.scan("D", (150, 12, 1))
    assert "No new transient alerts." in capsys.readouterr().out


def test_parked_object_resolves_when_bracketed(env, capsys):
    env["objects"] = [obj(NEW_ZTF[1:])]
    mon.scan("D", (150, 12, 1))
    out = capsys.readouterr().out
    assert "Parked, onset not bracketed: 1  (new 1, resolved 0, expired unbracketed 0)" in out
    assert "  ztf:ZTF26aaa  first detection 2026-09-22  no_quiet_point" in out
    env["objects"] = [obj(NEW_ZTF)]
    mon.scan("D", (150, 12, 1))
    out = capsys.readouterr().out
    assert "(new 0, resolved 1, expired unbracketed 0)" in out
    assert "  context: was parked since 2026-09-27" in out
    assert state()["parked"] == {}


def test_parked_object_expires(env, capsys):
    path = mon.state_file("D")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"last_mjd": NOW - 1, "reported": {}, "parked": {
        "lsst:1": {"survey": "lsst", "first_det_mjd": NOW - 15, "code": "no_quiet_point", "parked_mjd": NOW - 13}}}))
    mon.scan("D", (150, 12, 1))
    assert "expired unbracketed 1" in capsys.readouterr().out
    assert state()["parked"] == {}


def test_rejections_and_sources_are_counted(env, capsys):
    env["objects"] = [obj(OLD_ZTF, oid="ZTF26old"), obj(NEW_ZTF, oid="ZTF26p", ra=IN_PLANE[0], dec=IN_PLANE[1])]
    mon.scan("D", (150, 12, 1))
    out = capsys.readouterr().out
    assert "Survey objects: 2 (ztf 2)  kept: 0" in out
    assert "  rejected galactic_plane: 1" in out and "  rejected old_no_rise: 1" in out


def test_dry_run_saves_nothing(env):
    before = mon.state_file("D").read_text()
    env["objects"] = [obj(NEW_ZTF)]
    mon.scan("D", (150, 12, 1), dry_run=True)
    assert mon.state_file("D").read_text() == before


# --- no state is an error; --init is the first run (2026-10-02) ------------------------------


def test_no_state_is_an_error_before_anything_is_fetched(env, capsys, monkeypatch):
    mon.state_file("D").unlink()
    monkeypatch.setattr(mon, "discover_alerce", lambda *a, **k: pytest.fail("fetched without state"))
    assert mon.scan("D", (150, 12, 1)) == 1
    err = capsys.readouterr().err
    assert f"ERROR: no state found at {mon.state_file('D')}" in err and "pass --init" in err
    assert not mon.state_file("D").exists()


def test_init_records_what_already_qualifies_without_reporting_it(env, capsys, monkeypatch):
    mon.state_file("D").unlink()
    (mon.STATE_DIR / mon.PS_WATCH_FILE).unlink()
    env["objects"] = [obj(NEW_ZTF), obj(NEW_ZTF, oid="ZTF26bbb", ra=HIGH_B[0] + 0.01)]
    monkeypatch.setattr(mon.tns, "tns_lookup", lambda ra, dec: pytest.fail("TNS looked up for an unreported object"))
    assert mon.scan("D", (150, 12, 1), init=True) == 0
    out = capsys.readouterr().out
    assert "INIT: recorded 2 survey objects that already qualify, not reported (host 2); parked 0." in out
    assert "TRANSIENT ALERTS" not in out and "ztf:ZTF26aaa" not in out
    assert set(state()["reported"]) == {"ztf:ZTF26aaa", "ztf:ZTF26bbb"}
    assert (mon.STATE_DIR / mon.PS_WATCH_FILE).exists()  # the watch starts with the footprint

    monkeypatch.setattr(mon.tns, "tns_lookup", lambda ra, dec: {})  # the ban was for the init run
    mon.scan("D", (150, 12, 1))  # the next run: nothing has changed, nothing to report
    assert "No new transient alerts." in capsys.readouterr().out
    env["objects"] = [obj(NEW_ZTF + [det(NOW - 0.3, 18.0)])]  # then something changes: that is reported
    mon.scan("D", (150, 12, 1))
    assert "new: rising" in capsys.readouterr().out


def test_init_never_overwrites_existing_state(env, capsys):
    before = mon.state_file("D").read_text()
    env["objects"] = [obj(NEW_ZTF)]
    assert mon.scan("D", (150, 12, 1), init=True) == 1
    assert "ERROR: --init: state already exists" in capsys.readouterr().err
    assert mon.state_file("D").read_text() == before


def test_init_dry_run_saves_nothing(env):
    mon.state_file("D").unlink()
    env["objects"] = [obj(NEW_ZTF)]
    assert mon.scan("D", (150, 12, 1), init=True, dry_run=True) == 0
    assert not mon.state_file("D").exists()


def test_a_missing_watch_file_is_said_not_silently_restarted(env, capsys):
    (mon.STATE_DIR / mon.PS_WATCH_FILE).unlink()
    assert mon.scan("D", (150, 12, 1)) == 0  # the watch never fails the run
    out = capsys.readouterr()
    assert "no point-source watch file at" in out.err and "evidence count restarts from zero" in out.out


def test_init_goes_with_a_live_scan_only():
    assert mon._parse_args(["--footprint", "ecdfs", "--init"]).init
    with pytest.raises(SystemExit):
        mon._parse_args(["--footprint", "D", "--init", "--replay", "2026-04-01", "2026-04-02"])


def test_old_reported_entries_are_pruned(env):
    path = mon.state_file("D")
    path.parent.mkdir(parents=True, exist_ok=True)
    old = {"first_reported_mjd": NOW - 100, "category": "orphan", "pairs": [["orphan", "bracketed"]]}
    path.write_text(json.dumps({"last_mjd": NOW - 1, "reported": {"ztf:old": old}}))
    mon.scan("D", (150, 12, 1))
    assert state()["reported"] == {}


def test_state_from_before_parking_loads(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"last_mjd": 1.0, "reported": {}}))
    assert mon._load_state(p)["parked"] == {}


def test_orphaned_tmp_is_cleaned(tmp_path):
    p = tmp_path / "s.json"
    p.with_suffix(".tmp").write_text("{partial")
    mon._load_state(p)
    assert not p.with_suffix(".tmp").exists()


def test_network_failure_retries_then_exits_1_without_state(env, monkeypatch, capsys):
    calls = {"n": 0}

    def flaky(*a, **kw):
        calls["n"] += 1
        raise ConnectionError("down")

    monkeypatch.setattr(mon, "discover_alerce", flaky)
    before = mon.state_file("D").read_text()
    assert mon.scan("D", (150, 12, 1)) == 1
    err = capsys.readouterr().err
    assert calls["n"] == mon.MAX_RETRIES
    assert err.startswith("WARN: network error (attempt 1/3)") and "ERROR: network error after 3 attempts" in err
    assert mon.state_file("D").read_text() == before  # not updated


def test_update_parked_lifecycle():
    prev = {
        "stays": {"survey": "ztf", "first_det_mjd": NOW - 5, "code": "no_quiet_point", "parked_mjd": NOW - 3},
        "unseen": {"survey": "lsst", "first_det_mjd": NOW - 5, "code": "no_quiet_point", "parked_mjd": NOW - 2},
        "resolved": {"survey": "ztf", "first_det_mjd": NOW - 5, "code": "no_quiet_point", "parked_mjd": NOW - 2},
        "expired": {"survey": "ztf", "first_det_mjd": NOW - 20, "code": "no_quiet_point", "parked_mjd": NOW - 10},
    }
    now_parked = {"stays": {"survey": "ztf", "first_det_mjd": NOW - 5, "code": "no_quiet_point"},
                  "fresh": {"survey": "ztf", "first_det_mjd": NOW - 1, "code": "quiet_too_shallow"}}
    parked, resolved, expired = mon.update_parked(prev, now_parked, {"stays", "resolved", "fresh"}, SINCE, NOW)
    assert sorted(parked) == ["fresh", "stays", "unseen"]
    assert (parked["stays"]["parked_mjd"], parked["fresh"]["parked_mjd"]) == (NOW - 3, NOW)
    assert (resolved, expired) == (["resolved"], ["expired"])


def test_state_file_names():
    assert mon.state_file("ecdfs") == PROJECT_ROOT / "state" / "transient_monitor_ecdfs.json"  # state, not logs/


# --- the run report: one JSON file per run in reports/ (user, 2026-10-23) ----------------------


def strict(path):
    """A report parsed as strict JSON: NaN or Infinity fails it."""
    def refuse(constant):
        raise ValueError(f"non-standard JSON: {constant}")
    return json.loads(path.read_text(), parse_constant=refuse)


def report(pattern="transient_D_*.json"):
    """The one report a test's run wrote."""
    (path,) = mon.REPORTS_DIR.glob(pattern)
    return strict(path)


def test_a_live_report_says_what_the_printed_report_said(env, capsys, monkeypatch):
    env["objects"] = [obj(NEW_ZTF), obj(OLD_ZTF, oid="ZTF26old")]
    monkeypatch.setattr(mon.tns, "tns_lookup", lambda ra, dec: tns_record("2026abc", ra, dec))
    assert mon.scan("D", (150, 12, 1)) == 0
    out = capsys.readouterr().out
    r = report()
    assert (r["mode"], r["footprint"], r["cone"], r["run_utc"], r["exit_code"]) == (
        "live", "D", [150, 12, 1], "2026-09-27T00:00:00+00:00", 0)
    assert r["settings"] == {"lookback_days": 14.0, "bracket_days": 10.0, "min_detections": 3}
    for survey, listing in r["listings"].items():
        assert f"{survey.upper()} listing (ALeRCE): {listing['detail']}" in out
        assert set(listing) == {"status", "detail", "newest_mjd", "answered_mjd"}
    assert r["objects"] == {"total": 2, "by_survey": {"ztf": 2}, "kept": 1} and r["rejected"] == {"old_no_rise": 1}
    (alert,) = r["alerts"]
    assert (alert["key"], alert["category"], alert["conditions"], alert["onset"], alert["onset_status"]) == (
        "ztf:ZTF26aaa", "host", ["bracketed"], "bracketed", mon.NEW)
    assert (alert["first_det_mjd"], alert["last_quiet_mjd"], alert["n_det"]) == (FIRST, FIRST - 1, 3)
    assert alert["tns"] == {"status": "record", "line": "SN 2026abc, SN Ia, discovered 2026-09-24",
                            "discovered": "2026-09-24 03:00:00", "sep_arcsec": 0.0, "own_id_listed": False, "notice": None}
    assert f"  TNS: {alert['tns']['line']}" in out and f"Report: {mon.REPORTS_DIR}" in out
    assert r["point_source_watch"] and r["point_source_watch"][0] in out and r["warnings"] == []


def test_every_count_and_line_the_report_shares_with_the_printed_one_agrees(env, capsys, monkeypatch):
    """Rebuild each printed line from the report: a field that drifts from what stdout said fails here."""
    mon._save_state(mon.state_file("D"), {
        "last_mjd": NOW - 1, "parked": {},
        "reported": {"ztf:ZTF25old": {"first_reported_mjd": NOW - 3, "category": "host",
                                      "pairs": [["host", "bracketed"]], "ra": 10.0, "dec": -30.0}},
        "tns_watch": {"ztf:ZTF25old": {"ra": 10.0, "dec": -30.0, "alerted_mjd": NOW - 3}}})
    (mon.STATE_DIR / mon.PS_WATCH_FILE).unlink()  # a warning of the run's own

    def no_atlas(cone):
        raise mon.sky_catalog.CatalogError("atlas: ReadTimeout")  # and one of the classifier's

    monkeypatch.setattr(mon.sky_catalog, "footprint_atlas", no_atlas)
    monkeypatch.setattr(mon.tns, "tns_once", lambda ra, dec: tns_record("2026old", ra, dec, found="2026-09-25 01:00:00"))
    env["objects"] = [obj(NEW_ZTF), obj(OLD_ZTF, oid="ZTF26old"),
                      obj(NEW_ZTF[1:], oid="ZTF26prk", ra=HIGH_B[0] + 0.02),   # parked: no quiet point
                      obj(NEW_ZTF, oid="ZTF26bad", ra=HIGH_B[0] + 0.04)]       # its light curve fails
    env["fail"] = {"ztf:ZTF26bad"}
    mon.scan("D", (150, 12, 1))
    out, err = capsys.readouterr()
    r = report()

    s, p, o, k = r["state_before"], r["photometry"], r["objects"], r["parked"]
    assert (s, p["failed"], k["count"]) == ({"reported": 1, "parked": 0}, 1, 1)  # each part has something to say
    assert f"Previously reported: {s['reported']}  parked: {s['parked']}" in out
    assert f"Photometry from ALeRCE: fetched {p['fetched']}, not needed {p['not_fetched']}, failed {p['failed']}" in out
    assert (f"Survey objects: {o['total']} ({', '.join(f'{v} {n}' for v, n in o['by_survey'].items())})  "
            f"kept: {o['kept']}") in out
    assert all(f"  rejected {why}: {n}" in out for why, n in r["rejected"].items()) and "no_photometry" in r["rejected"]
    assert (f"Parked, onset not bracketed: {k['count']}  (new {len(k['new'])}, resolved {len(k['resolved'])}, "
            f"expired unbracketed {len(k['expired'])})") in out
    assert all(f"  {e['key']}  first detection {mon._date(e['first_det_mjd'])}  {e['code']}" in out for e in k["new"])
    assert len(r["fetch_errors"]) == p["failed"]
    assert all(f"{e.split(': ')[0]}: ALeRCE photometry unavailable" in err for e in r["fetch_errors"])
    assert r["tns_earlier"] and all(line in out for line in r["tns_earlier"])
    assert len(r["warnings"]) == 2 and all(f"WARN: {w}" in err for w in r["warnings"])


@pytest.fixture
def nine_hours_ahead():
    """Local time 9 h ahead of UTC (a POSIX zone: no tz database needed), so a local and a UTC
    stamp can never agree, whatever zone the machine running the suite is in."""
    old, zone = os.environ.get("TZ"), (time.timezone, time.tzname)
    os.environ["TZ"] = "JST-9"
    time.tzset()
    yield
    if old is None:
        os.environ.pop("TZ")
    else:
        os.environ["TZ"] = old
    time.tzset()  # after TZ is restored: before it, or without it, every later test would run at +9 h
    assert (time.timezone, time.tzname) == zone, "the machine's zone was not restored"


def test_a_run_names_its_report_by_its_start_in_local_time(env, nine_hours_ahead, monkeypatch):
    """The name and run_utc are one moment, the run's start: the clock is not read again, however long the run."""
    def slow(*a, **kw):
        monkeypatch.setattr(mon, "now_mjd", lambda: NOW + 25 / 1440)  # 25 min of photometry later
        return listing(*a, **kw)

    listing = mon.discover_alerce
    monkeypatch.setattr(mon, "discover_alerce", slow)
    mon.scan("D", (150, 12, 1))
    (path,) = mon.REPORTS_DIR.glob("transient_D_*.json")
    assert path.name == "transient_D_20260927_090000.json"  # NOW, 00:00 UTC, at +9 h
    assert strict(path)["run_utc"] == "2026-09-27T00:00:00+00:00"


def test_at_20_utc_9_hours_ahead_the_file_name_is_tomorrow_but_run_utc_and_day_are_today(env, nine_hours_ahead,
                                                                                       monkeypatch):
    """The file name is local time; run_utc and a live alert's day are the run's UTC date, like the replay's day."""
    monkeypatch.setattr(mon, "now_mjd", lambda: NOW + 20 / 24)  # 2026-09-27 20:00 UTC
    env["objects"] = [obj(NEW_ZTF)]
    assert mon.scan("D", (150, 12, 1)) == 0
    (path,) = mon.REPORTS_DIR.glob("transient_D_*.json")
    r = strict(path)
    assert path.name == "transient_D_20260928_050000.json"  # 05:00 local, the next day
    assert r["run_utc"] == "2026-09-27T20:00:00+00:00" and r["alerts"][0]["day"] == "2026-09-27"


@pytest.mark.parametrize("answer,status,line,record", [
    (lambda ra, dec: tns_record("2026abc", ra, dec), "record", "SN 2026abc, SN Ia, discovered 2026-09-24",
     {"discovered": "2026-09-24 03:00:00", "sep_arcsec": 0.0, "own_id_listed": False}),
    (lambda ra, dec: {}, "none_yet", mon.TNS_NONE_YET, {}),
    (lambda ra, dec: None, "unavailable", mon.TNS_UNAVAILABLE, {}),
])
def test_every_alert_in_the_report_carries_its_tns_status_and_line(env, monkeypatch, answer, status, line, record):
    env["objects"] = [obj(NEW_ZTF)]
    monkeypatch.setattr(mon.tns, "tns_lookup", answer)
    mon.scan("D", (150, 12, 1))
    assert report()["alerts"][0]["tns"] == {"status": status, "line": line, "notice": None, **record}


@pytest.mark.parametrize("init", [False, True], ids=["live", "init"])
def test_a_dry_run_leaves_reports_untouched(env, init):
    if init:
        mon.state_file("D").unlink()
    env["objects"] = [obj(NEW_ZTF)]
    mon.REPORTS_DIR.mkdir(parents=True)
    for name in ("qa_alerce_lsst_20261001_071300_n256.csv", "transient_D_20261001_160000.json.tmp"):
        (mon.REPORTS_DIR / name).write_text("x")  # not even a killed run's leftover is cleaned
    before = sorted(p.name for p in mon.REPORTS_DIR.iterdir())
    assert mon.scan("D", (150, 12, 1), dry_run=True, init=init) == 0
    assert sorted(p.name for p in mon.REPORTS_DIR.iterdir()) == before


def test_a_failed_listing_writes_its_report_after_the_state_and_exits_1(env, monkeypatch):
    env["newest"]["ztf"] = ({"oid": "ZTF26x", "lastmjd": NOW - 1}, None)  # ZTF has data; its listing says none
    seen, write = [], mon._write_report

    def after_the_state(name, path, rep):
        seen.append(state()["last_mjd"])
        return write(name, path, rep)

    monkeypatch.setattr(mon, "_write_report", after_the_state)
    assert mon.scan("D", (150, 12, 1)) == 1
    assert seen == [NOW]  # one report, written once the state was saved
    r = report()
    assert r["exit_code"] == 1 and r["listings"]["ztf"]["status"] == "failed" and r["listings"]["lsst"]["status"] == "quiet"


def test_an_init_report_has_no_alerts_but_what_it_recorded_and_absorbed(env, capsys, monkeypatch):
    mon.state_file("D").unlink()
    env["objects"] = [obj(NEW_ZTF), obj(NEW_ZTF, oid="ZTF26bbb", ra=HIGH_B[0] + 0.01)]
    monkeypatch.setattr(mon.tns, "tns_lookup", lambda ra, dec: pytest.fail("TNS looked up for an unreported object"))
    assert mon.scan("D", (150, 12, 1), init=True) == 0
    assert "INIT: recorded 2 survey objects that already qualify, not reported (host 2); parked 0." in capsys.readouterr().out
    r = report("transient_D_*_init.json")
    assert r["mode"] == "init" and "alerts" not in r
    assert r["recorded"] == {"by_category": {"host": 2}, "parked": 0}
    assert [a["key"] for a in r["absorbed"]] == ["ztf:ZTF26aaa", "ztf:ZTF26bbb"]
    assert not any("tns" in a for a in r["absorbed"])
    assert r["point_source_watch"][0].startswith("Point-source watch:")  # the watch runs during --init too


def test_a_network_failure_writes_a_short_report(env, monkeypatch):
    def down(*a, **kw):
        raise ConnectionError("down")

    monkeypatch.setattr(mon, "discover_alerce", down)
    assert mon.scan("D", (150, 12, 1)) == 1
    r = report()
    assert r["exit_code"] == 1 and r["error"].startswith("network error after 3 attempts") and "listings" not in r
    assert len(r["warnings"]) == mon.MAX_RETRIES - 1 and r["warnings"][0].startswith("network error (attempt 1/3)")


def test_a_report_is_written_whole_or_not_at_all(env, capsys, monkeypatch):
    env["objects"] = [obj(NEW_ZTF)]
    rename = mon.Path.rename

    def read_only(self, target):
        if self.parent == mon.REPORTS_DIR:
            raise OSError(30, "Read-only file system")
        return rename(self, target)

    monkeypatch.setattr(mon.Path, "rename", read_only)
    assert mon.scan("D", (150, 12, 1)) == 1
    assert "ERROR: run report not written" in capsys.readouterr().err
    assert list(mon.REPORTS_DIR.iterdir()) == []  # its temp file removed, nothing half-written
    assert state()["last_mjd"] == NOW  # the state is saved all the same


def test_a_rise_from_nothing_is_null_and_the_report_strict_json(env, monkeypatch):
    env["objects"] = [obj(NEW_ZTF)]
    monkeypatch.setattr(mon, "rising", lambda points, first: mon.Rise("r", math.inf, 6.0, 4.0))
    assert mon.scan("D", (150, 12, 1)) == 0
    assert report()["alerts"][0]["rise"] == {"band": "r", "mag": None, "sigma": 6.0, "days": 4.0}


def test_a_non_finite_float_past_the_builder_fails_the_write_not_the_json(env, capsys, monkeypatch):
    env["objects"] = [obj(NEW_ZTF)]
    build = mon.alert_record
    monkeypatch.setattr(mon, "alert_record", lambda *a: {**build(*a), "gal_b": math.nan})
    assert mon.scan("D", (150, 12, 1)) == 1
    assert "ERROR: run report not written" in capsys.readouterr().err and list(mon.REPORTS_DIR.iterdir()) == []


def test_a_killed_runs_leftover_is_removed_and_nothing_else_in_reports(env):
    mon.REPORTS_DIR.mkdir(parents=True)
    others = {"transient_ecdfs_20261001_160000.json.tmp", "qa_alerce_lsst_20261001_071300_n256.csv",
              "transient_D_20261001_160000.json"}
    for name in others | {"transient_D_20261001_160000.json.tmp"}:
        (mon.REPORTS_DIR / name).write_text("x")
    mon.scan("D", (150, 12, 1))
    left = {p.name for p in mon.REPORTS_DIR.iterdir()}
    assert "transient_D_20261001_160000.json.tmp" not in left and others <= left


def test_report_file_names_are_local_time_and_say_init(nine_hours_ahead):
    started = datetime.datetime(2026, 10, 23, 16, 0, 5)
    assert mon.report_file("ecdfs", "live", started) == mon.REPORTS_DIR / "transient_ecdfs_20261023_160005.json"
    assert mon.report_file("ecdfs", "init", started).name == "transient_ecdfs_20261023_160005_init.json"
    assert mon.REPORTS_DIR == PROJECT_ROOT / "reports"  # reports/, not logs/


# --- CLI ---------------------------------------------------------------------------------------


def test_footprint_and_cone_are_exclusive():
    with pytest.raises(SystemExit):
        mon._parse_args(["--footprint", "D", "--cone", "1", "2", "3"])


def test_the_live_footprint_is_ecdfs_and_the_default():
    a = mon._parse_args([])
    assert (mon.LIVE_FOOTPRINT, a.name, a.cone) == ("ecdfs", "ecdfs", (53.1, -27.8, mon.DEFAULT_RADIUS_DEG))
    assert mon._parse_args(["--cone", "10", "0", "1"]).name == "cone_10_0_1"  # a cone still overrides it


def test_footprint_presets():
    a = mon._parse_args(["--footprint", "ecdfs"])
    assert (a.name, a.cone) == ("ecdfs", (53.1, -27.8, mon.DEFAULT_RADIUS_DEG))
    assert mon._parse_args(["--footprint", "D", "--radius", "1.75"]).cone == (150.1, 2.5, 1.75)


@pytest.mark.parametrize("argv", [
    ["--cone", "360", "0", "1"], ["--cone", "10", "-91", "1"], ["--cone", "10", "0", "0"],
    ["--cone", "10", "0", "11"], ["--cone", "10", "0", "1", "--radius", "2"],
    ["--footprint", "D", "--bracket-days", "0"], ["--footprint", "D", "--min-detections", "0"],
    ["--footprint", "D", "--as-of", "15/04/2026"], ["--footprint", "Z"],
])
def test_bad_arguments(argv):
    with pytest.raises(SystemExit):
        mon._parse_args(argv)


def test_main_passes_arguments(monkeypatch):
    seen = {}
    monkeypatch.setattr(mon, "scan", lambda *a, **kw: seen.setdefault("args", (a, kw)) and 0)
    mon.main(["--cone", "150", "2", "1.5", "--lookback-days", "7",
              "--min-detections", "4", "--bracket-days", "5", "--dry-run", "--init"])
    assert seen["args"] == (("cone_150_2_1.5", (150.0, 2.0, 1.5), 7.0, 4, 5.0, True), {"init": True})


def test_main_routes_a_replay_and_never_scans(monkeypatch):
    from rubin_qa import replay
    seen = {}
    monkeypatch.setattr(mon, "scan", lambda *a: pytest.fail("a replay must not run the live scan"))
    monkeypatch.setattr(replay, "run", lambda *a, **kw: seen.setdefault("run", (a, kw)) and [])
    monkeypatch.setattr(replay, "compare_cuts", lambda *a, **kw: seen.setdefault("compare", a) and 0)
    assert mon.main(["--footprint", "D", "--replay", "2026-04-01", "2026-04-30", "--no-cuts"]) == 0
    args, kw = seen["run"]
    assert args[:4] == ("D", mon.FOOTPRINTS["D"][0], "2026-04-01", "2026-04-30")
    assert kw == {"cut": False, "estimate_only": False, "yes": False}
    assert mon.main(["--footprint", "D", "--replay", "2026-04-08", "2026-04-10", "--compare-cuts"]) == 0
    assert seen["compare"][2:4] == ("2026-04-08", "2026-04-10")


def test_a_declined_replay_exits_1_with_the_reason(monkeypatch, capsys):
    from rubin_qa import replay

    def declined(*a, **kw):
        raise replay.Declined("--compare-cuts D 2026-03-12..2026-05-15: ~1500 min estimated; declined")

    monkeypatch.setattr(replay, "compare_cuts", declined)
    assert mon.main(["--footprint", "D", "--replay", "2026-03-12", "2026-05-15", "--compare-cuts"]) == 1
    assert "ERROR: --compare-cuts D 2026-03-12..2026-05-15: ~1500 min estimated; declined" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [
    ["--footprint", "D", "--replay", "2026-04-30", "2026-04-01"],  # backwards
    ["--footprint", "D", "--replay", "April", "2026-04-30"],       # not a date
    ["--footprint", "D", "--no-cuts"],                              # needs --replay
])
def test_replay_arguments_are_checked(argv):
    with pytest.raises(SystemExit):
        mon._parse_args(argv)


def test_footprint_classifier_brick_list_outage_flags_every_crossmatch(monkeypatch, capsys):
    """Without the brick list the gap check cannot run: say so on the run and on each crossmatch, never a bare orphan."""
    def down(cone):
        raise mon.sky_catalog.CatalogError("bricks for cone 53.1 -27.8 2.82: HTTP 502")

    coverages = []
    monkeypatch.setattr(mon.sky_catalog, "footprint_atlas", lambda cone: pd.DataFrame())
    monkeypatch.setattr(mon.sky_catalog, "footprint_bricks", down)
    monkeypatch.setattr(mon.sky_catalog, "classify",
                        lambda ra, dec, atlas, coverage: coverages.append(coverage) or Crossmatch("none"))
    classify = mon.footprint_classifier((53.1, -27.8, 2.82))
    assert classify(53.0, -27.9).evidence == ["catalogue gaps not checked"]
    classify(53.2, -27.7)
    assert coverages == [None, None]
    assert capsys.readouterr().err.count("WARN: DR10 brick list unavailable") == 1


def test_footprint_classifier_passes_the_gap_check_bound_to_the_bricks(monkeypatch):
    bricks = pd.DataFrame({"brickname": ["0532m280"]})
    calls = []
    monkeypatch.setattr(mon.sky_catalog, "footprint_atlas", lambda cone: pd.DataFrame())
    monkeypatch.setattr(mon.sky_catalog, "footprint_bricks", lambda cone: bricks)
    monkeypatch.setattr(mon.sky_catalog, "catalogue_gap", lambda ra, dec, b: calls.append(b is bricks) or "gap")
    monkeypatch.setattr(mon.sky_catalog, "classify", lambda ra, dec, atlas, coverage: Crossmatch("unavailable", [coverage(ra, dec)]))
    assert mon.footprint_classifier((53.1, -27.8, 2.82))(53.25, -28.05).evidence == ["gap"]
    assert calls == [True]


def test_agn_colours_on_the_matched_source_within_1_arcsec_is_agn():
    xm_agn = lambda ra, dec, **kw: Crossmatch("point_source", ["PSF r=21.0 at 0.3\""], flags=["agn_nuclear"])
    ev = mon.evaluate(obj(NEW_ZTF), NOW, SINCE, classify=xm_agn)
    assert ev.candidate.category == "agn"


def test_agn_colours_of_a_host_farther_out_is_a_note_not_a_category():
    """An SN in the disk of an AGN host is not an AGN flare."""
    xm_host = lambda ra, dec, **kw: Crossmatch("host", ['EXP r=19.0 sep=6.0" d_DLR=1.2'], flags=["agn_host"])
    ev = mon.evaluate(obj(NEW_ZTF), NOW, SINCE, classify=xm_host)
    assert ev.candidate.category == "host" and "host has WISE AGN colours" in ev.candidate.context


# --- the TNS discovery notice (user, 2026-11-07) ----------------------------------------------
#
# Real alerts and the TNS records at their positions, trimmed to what the code reads. AFMA and
# AHLY: the two alerts of the ecdfs run of 2026-11-05, with their records as fetched 2026-11-07
# (the run's report keeps only the day of the discovery). EVY and KHR: alerts of replay D, rerun
# 2026-11-07, records of the same day. Values are inline: tests depend on no run's output.

AFMA_RUN_MJD = 61349.62559313397
AFMA = {"key": "lsst:171141216521945181", "ra": 52.088979913356205, "dec": -27.3811726809613,
        "first_det_mjd": 61343.135857905916}
AFMA_TNS = {"objname": "2026afma", "name_prefix": "SN", "object_type": {"id": 3, "name": "SN Ia"},
            "radeg": 52.088882554506, "decdeg": -27.380949386136, "discoverydate": "2026-09-28 08:14:49.632",
            "internal_names": ",LSST-P-DO-171141216521945181", "discovery_data_source": {"group_name": "YSE", "groupid": 83}}
AHLY = {"key": "lsst:171141216668745796", "ra": 54.48050170370357, "dec": -27.533952646966743,
        "first_det_mjd": 61343.1360799451}
AHLY_TNS = {"objname": "2026ahly", "name_prefix": "AT", "object_type": {"id": None, "name": None},
            "radeg": 54.480477254708006, "decdeg": -27.53393989423294, "discoverydate": "2026-10-30 03:15:57.307",
            "internal_names": "LSST-AP-DO-171141216668745796", "discovery_data_source": {"group_name": "Rubin", "groupid": 165}}
# a real bracketed alert: both 11-05 alerts are no_quiet_point, so this is what the bracketed cases are built from
EVY = {"key": "ztf:ZTF26aajowek", "ra": 151.22224217168346, "dec": -0.058046698868854496,
       "first_det_mjd": 61105.36178239994, "last_quiet_mjd": 61100.22143519996}
EVY_TNS = {"objname": "2026evy", "name_prefix": "SN", "object_type": {"name": "SN IIb"},
           "radeg": 151.222240161, "decdeg": -0.0580446045592, "discoverydate": "2026-03-05 10:39:49.248",
           "internal_names": "GOTO26bpn,ZTF26aajowek,ATLAS26crp,BGEM J100453.35-000329.2,PS26axk",
           "discovery_data_source": {"group_name": "GOTO"}}
KHR = {"key": "lsst:170270399191318556", "ra": 150.46040662051306, "dec": 3.4887700325500295,
       "first_det_mjd": 61145.13363716459}
KHR_TNS = {"objname": "2026khr", "name_prefix": "AT", "object_type": {"id": None, "name": None},
           "radeg": 150.460408, "decdeg": 3.488769, "discoverydate": "2026-04-22 02:00:23.040",
           "internal_names": "LSST-AP-DO-170270399191318556", "discovery_data_source": {"group_name": "Rubin", "groupid": 165}}
# not real: round numbers, so a gap can be exactly one day in floats. 61343.5 is 2026-10-30 12:00 UTC.
ROUND = {"key": "lsst:1706", "ra": 150.0, "dec": 12.0, "first_det_mjd": 61343.5}


def on_the_spot(discovered, **rec):
    """A record exactly at ROUND's position, discovered then."""
    return {"objname": "2026x", "radeg": ROUND["ra"], "decdeg": ROUND["dec"], "discoverydate": discovered, **rec}


def verdict(alert, status=None, code=None):
    quiet = alert.get("last_quiet_mjd")
    return mon.NewVerdict(alert["key"].split(":")[0], status or (mon.NEW if quiet is not None else mon.UNBRACKETED),
                          code or ("bracketed" if quiet is not None else "no_quiet_point"),
                          first_det_mjd=alert["first_det_mjd"], last_quiet_mjd=quiet, n_det=4)


def notice_on(alert, rec, onset=None, conditions=("rising",)):
    """The pure function, on the separation the 2" match reads."""
    return mon._discovery_notice(rec, alert["key"], onset or verdict(alert), list(conditions),
                                 mon._tns_sep_arcsec(rec, alert["ra"], alert["dec"]))


def scan_real_alert(env, monkeypatch, alert, answer):
    """A live scan on the alert's real ID, position and first detection; the photometry after it is built by hand."""
    first, oid = alert["first_det_mjd"], alert["key"].split(":")[1]
    env["objects"] = [obj([det(first + d, mag, band="i", survey="lsst", oid=oid)
                           for d, mag in ((0, 23.5), (1, 23.3), (3, 22.9), (6.1, 22.45))],
                          survey="lsst", oid=oid, ra=alert["ra"], dec=alert["dec"])]
    monkeypatch.setattr(mon, "now_mjd", lambda: AFMA_RUN_MJD)
    monkeypatch.setattr(mon.tns, "tns_lookup", answer)
    code = mon.scan("ecdfs", mon.FOOTPRINTS["ecdfs"][0])
    r = report("transient_ecdfs_*.json")
    (record,) = r["alerts"]
    assert (record["key"], record["conditions"], record["onset"]) == (alert["key"], ["rising"], "no_quiet_point")
    return code, r, record["tns"]


def test_afma_a_discovery_a_month_before_our_first_detection_gets_the_notice(env, capsys, monkeypatch):
    code, r, said = scan_real_alert(env, monkeypatch, AFMA, lambda ra, dec: AFMA_TNS)
    text = ('TNS discovery 31.8 d before our first detection (YSE data; 0.86" away, TNS lists our ID). '
            "Onset not bracketed: it is LSST's first detection, not the transient's start.")
    assert said == {"status": "record", "line": "SN 2026afma, SN Ia, discovered 2026-09-28",
                    "discovered": "2026-09-28 08:14:49.632", "sep_arcsec": 0.86, "own_id_listed": True,
                    "notice": {"kind": "onset_not_bracketed", "gap_days": 31.79, "text": text}}
    out = capsys.readouterr().out
    assert f"  TNS: {said['line']}\n  NOTICE: {text}\n" in out  # in the alert block, right under its TNS line
    assert code == 0 and r["exit_code"] == 0  # informational: nothing about the run changes
    assert set(state("ecdfs")["reported"]) == {AFMA["key"]} and state("ecdfs")["tns_watch"] == {}


def test_ahly_a_discovery_on_our_own_first_detection_gets_none(env, capsys, monkeypatch):
    """The alert came 6.5 d after the first detection, so a gap measured from the run would fire here."""
    code, r, said = scan_real_alert(env, monkeypatch, AHLY, lambda ra, dec: AHLY_TNS)
    assert said == {"status": "record", "line": "AT 2026ahly, discovered 2026-10-30",
                    "discovered": "2026-10-30 03:15:57.307", "sep_arcsec": 0.09, "own_id_listed": True, "notice": None}
    assert "NOTICE" not in capsys.readouterr().out and code == 0


def test_evy_0_92_d_is_silent_though_the_date_cut_to_its_day_reads_1_36():
    assert notice_on(EVY, EVY_TNS) is None


def test_khr_a_discovery_days_after_our_first_detection_is_the_normal_case_and_silent():
    """TNS 6.95 d after us: a gap taken as a distance would fire."""
    assert KHR["first_det_mjd"] < mon._utc_to_mjd(KHR_TNS["discoverydate"]) - 6.9
    assert notice_on(KHR, KHR_TNS) is None


def test_a_discovery_before_a_real_quiet_point_is_a_conflict():
    """evy with one field changed: discoverydate, 5 d earlier, which lands 0.8 d before its quiet point."""
    n = notice_on(EVY, {**EVY_TNS, "discoverydate": "2026-02-28 10:39:49.248"})
    assert (n["kind"], n["gap_days"]) == ("quiet_point_conflict", 5.92)
    assert n["text"] == ('TNS discovery 5.9 d before our first detection, 0.8 d before our quiet point (GOTO data; '
                         '0.01" away, TNS lists our ID). Conflict: the quiet point was not deep enough, or the TNS '
                         "match is another source.")


def test_a_discovery_inside_the_gap_is_no_conflict():
    """evy again, discoverydate 2 d earlier: between its quiet point and its first detection."""
    n = notice_on(EVY, {**EVY_TNS, "discoverydate": "2026-03-03 10:39:49.248"})
    assert (n["kind"], n["gap_days"]) == ("after_quiet_point", 2.92)
    assert n["text"] == ('TNS discovery 2.9 d before our first detection, after our quiet point (GOTO data; 0.01" away, '
                         "TNS lists our ID). No conflict: TNS caught the rise inside our gap.")


def test_a_discovery_exactly_at_the_quiet_point_is_a_conflict_a_millisecond_later_is_not():
    bracketed = {**ROUND, "first_det_mjd": 61345.5, "last_quiet_mjd": 61340.5}  # the quiet point: 10-27 12:00 UTC
    assert mon._utc_to_mjd("2026-10-27 12:00:00.000") == 61340.5
    assert notice_on(bracketed, on_the_spot("2026-10-27 12:00:00.000"))["kind"] == "quiet_point_conflict"
    assert notice_on(bracketed, on_the_spot("2026-10-27 12:00:00.001"))["kind"] == "after_quiet_point"


def test_a_discovery_exactly_one_day_before_our_first_detection_is_silent():
    """Literal dates, never the constant: moving the bound turns this test or the next one red."""
    assert mon._utc_to_mjd("2026-10-29 12:00:00.000") == ROUND["first_det_mjd"] - 1
    assert notice_on(ROUND, on_the_spot("2026-10-29 12:00:00.000")) is None


def test_a_millisecond_more_than_one_day_fires():
    n = notice_on(ROUND, on_the_spot("2026-10-29 11:59:59.999"))
    assert n["kind"] == "onset_not_bracketed" and n["gap_days"] == 1.0


def test_the_survey_in_the_text_comes_from_the_alerts_id():
    unbracketed = {**EVY, "last_quiet_mjd": None}
    n = notice_on(unbracketed, {**EVY_TNS, "discoverydate": "2026-03-03 10:39:49.248"})
    assert "it is ZTF's first detection" in n["text"] and "LSST" not in n["text"]


def test_a_re_brightening_gets_no_notice_it_claims_no_onset():
    old = verdict({**EVY, "last_quiet_mjd": None}, status=mon.NOT_NEW, code="old")
    assert notice_on(EVY, {**EVY_TNS, "discoverydate": "2019-03-05 10:39:49.248"}, old, ["re_brightening"]) is None


def test_a_recent_alert_with_too_few_detections_never_tried_to_bracket_and_says_not_bracketed():
    few = verdict({**EVY, "last_quiet_mjd": None}, status=mon.NOT_NEW, code="too_few_detections")
    n = notice_on(EVY, {**EVY_TNS, "discoverydate": "2026-03-03 10:39:49.248"}, few)
    assert n["kind"] == "onset_not_bracketed"


def test_a_date_reported_to_the_day_counts_from_its_end_so_the_gap_can_only_understate():
    """The shape of 2026fgl's real date (2026-03-10 00:00:00.000)."""
    assert notice_on(ROUND, on_the_spot("2026-10-29 00:00:00.000")) is None  # 1.5 d as written, 0.5 d from the day's end
    n = notice_on(ROUND, on_the_spot("2026-10-28 00:00:00.000"))
    assert n["gap_days"] == 1.5 and n["text"].startswith(
        'TNS discovery at least 1.5 d before our first detection (reported to the day; 0.00" away, ')


def test_without_our_id_in_tns_every_claim_that_needs_the_same_source_is_conditional():
    """A position-only match cannot support 'not the transient's start'."""
    unlisted = {**AFMA_TNS, "internal_names": "YSE26xyz"}
    assert notice_on(AFMA, unlisted)["text"] == (
        'TNS discovery 31.8 d before our first detection (YSE data; 0.86" away, TNS does not list our ID). '
        "Onset not bracketed: if it is the same source, this is LSST's first detection, not the transient's start.")
    inside = {**EVY_TNS, "discoverydate": "2026-03-03 10:39:49.248", "internal_names": "GOTO26bpn"}
    assert notice_on(EVY, inside)["text"].endswith("No conflict: if it is the same source, TNS caught the rise inside our gap.")


def test_a_record_with_no_data_source_says_only_what_it_has():
    rec = {k: v for k, v in AFMA_TNS.items() if k != "discovery_data_source"}
    assert '(0.86" away, TNS lists our ID)' in notice_on(AFMA, rec)["text"]


def _raises(ra, dec):
    raise RuntimeError("TNS gone")


@pytest.mark.parametrize("answer,status", [
    (lambda ra, dec: {}, "none_yet"),  # no date to fire on: this one alone could not catch a notice that fires
    (lambda ra, dec: {**AFMA_TNS, "decdeg": AFMA_TNS["decdeg"] + 3 / 3600}, "none_yet"),  # 3" off, with an old date
    (lambda ra, dec: None, "unavailable"),
    (_raises, "unavailable"),
], ids=["empty", "record_too_far", "none", "raises"])
def test_none_yet_and_unavailable_carry_no_notice_and_no_complaint(env, capsys, monkeypatch, answer, status):
    code, r, said = scan_real_alert(env, monkeypatch, AFMA, answer)
    assert said == {"status": status, "line": mon.TNS_LINE[status], "notice": None}
    assert "NOTICE" not in capsys.readouterr().out and r["warnings"] == [] and code == 0
    assert AFMA["key"] in state("ecdfs")["tns_watch"]


@pytest.mark.parametrize("broken", [
    {"radeg": None}, {"decdeg": None}, {"radeg": float("nan")}, {"radeg": "52.09"}, {"objname": None},
], ids=["radeg_none", "decdeg_none", "radeg_nan", "radeg_text", "no_name"])
def test_a_record_that_cannot_be_read_is_unavailable_never_none_yet(env, capsys, monkeypatch, broken):
    """The alert is kept, the state saved, the run unharmed. A NaN separation would fail the match and read as none_yet."""
    code, r, said = scan_real_alert(env, monkeypatch, AFMA, lambda ra, dec: {**AFMA_TNS, **broken})
    assert said == {"status": "unavailable", "line": mon.TNS_UNAVAILABLE, "notice": None}
    assert code == 0 and r["exit_code"] == 0 and len(r["warnings"]) == 1
    assert f"WARN: {r['warnings'][0]}" in capsys.readouterr().err
    assert AFMA["key"] in state("ecdfs")["reported"] and AFMA["key"] in state("ecdfs")["tns_watch"]


@pytest.mark.parametrize("missing", ["radeg", "decdeg", "objname"])
def test_a_record_missing_a_field_outright_is_unavailable_too(env, monkeypatch, missing):
    answer = {k: v for k, v in AFMA_TNS.items() if k != missing}
    code, r, said = scan_real_alert(env, monkeypatch, AFMA, lambda ra, dec: answer)
    assert said["status"] == "unavailable" and code == 0 and AFMA["key"] in state("ecdfs")["tns_watch"]


@pytest.mark.parametrize("date", ["soon", None, 20260928], ids=["text", "missing", "number"])
def test_an_unreadable_discovery_date_is_warned_about_never_read_as_not_earlier(env, capsys, monkeypatch, date):
    code, r, said = scan_real_alert(env, monkeypatch, AFMA, lambda ra, dec: {**AFMA_TNS, "discoverydate": date})
    assert (said["status"], said["notice"], said["discovered"]) == ("record", None, date)
    out, err = capsys.readouterr()
    assert code == 0 and "NOTICE" not in out and len(r["warnings"]) == 1 and f"WARN: {r['warnings'][0]}" in err
    assert AFMA["key"] in state("ecdfs")["reported"]


def test_whatever_goes_wrong_in_the_notice_the_alert_and_the_run_go_on(env, capsys, monkeypatch):
    def broken(*args):
        raise ArithmeticError("a bug in the notice")

    monkeypatch.setattr(mon, "_discovery_notice", broken)
    code, r, said = scan_real_alert(env, monkeypatch, AFMA, lambda ra, dec: AFMA_TNS)
    assert code == 0 and said["status"] == "record" and said["notice"] is None
    assert "a bug in the notice" in r["warnings"][0] and AFMA["key"] in state("ecdfs")["reported"]


def asked_again_on_a_later_run(env, monkeypatch, answer):
    """An earlier alert in the state's tns_watch, asked again."""
    env["objects"] = []
    mon._save_state(mon.state_file("ecdfs"), {"last_mjd": None, "reported": {}, "parked": {}, "tns_watch": {
        AFMA["key"]: {"ra": AFMA["ra"], "dec": AFMA["dec"], "alerted_mjd": AFMA_RUN_MJD - 1}}})
    monkeypatch.setattr(mon, "now_mjd", lambda: AFMA_RUN_MJD)
    monkeypatch.setattr(mon.tns, "tns_once", answer)
    code = mon.scan("ecdfs", mon.FOOTPRINTS["ecdfs"][0])
    return code, report("transient_ecdfs_*.json")


def test_a_record_that_cannot_be_read_stays_watched_when_it_turns_up_on_a_later_run(env, capsys, monkeypatch):
    answer = {k: v for k, v in AFMA_TNS.items() if k != "radeg"}
    code, r = asked_again_on_a_later_run(env, monkeypatch, lambda ra, dec: answer)
    assert code == 0 and r["tns_earlier"] == [] and AFMA["key"] in state("ecdfs")["tns_watch"]
    assert len(r["warnings"]) == 1 and f"WARN: {r['warnings'][0]}" in capsys.readouterr().err


def test_an_unreadable_date_on_a_later_run_costs_the_line_its_lag_not_the_run(env, monkeypatch):
    answer = {**AFMA_TNS, "discoverydate": "soon"}
    code, r = asked_again_on_a_later_run(env, monkeypatch, lambda ra, dec: answer)
    assert code == 0 and "now in TNS: SN 2026afma" in r["tns_earlier"][0] and "our alert)" not in r["tns_earlier"][0]
    assert state("ecdfs")["tns_watch"] == {}


def test_a_record_arriving_on_a_later_run_gets_no_notice_for_now(env, capsys, monkeypatch):
    """Left out by decision (2026-11-07): the line still gives the discovery date and the lag behind the alert."""
    code, r = asked_again_on_a_later_run(env, monkeypatch, lambda ra, dec: AFMA_TNS)
    assert code == 0 and "discovered 2026-09-28" in r["tns_earlier"][0]
    assert "NOTICE" not in capsys.readouterr().out


# --- the real lookup, on the service's recorded bodies (2026-11-07) ---------------------------

REAL_TNS_LOOKUP, REAL_TNS_ONCE = mon.tns.tns_lookup, mon.tns.tns_once


@pytest.fixture
def real_tns(env, monkeypatch):
    """env fakes the lookup whole; here only requests.post is fake, and the clock."""
    monkeypatch.setattr(mon.tns, "tns_once", REAL_TNS_ONCE)
    return fake_tns.fake_clock(monkeypatch)


def test_a_dropped_call_on_afma_still_finds_the_record_and_gives_the_notice(real_tns, env, capsys, monkeypatch):
    """A rejected first ask, from the body to the report."""
    asked = fake_tns.serve(monkeypatch, fake_tns.REJECTED, fake_tns.RECORD)
    code, r, said = scan_real_alert(env, monkeypatch, AFMA, REAL_TNS_LOOKUP)
    assert len(asked) == 2 and said["status"] == "record" and said["notice"]["kind"] == "onset_not_bracketed"
    assert said["notice"]["gap_days"] == 31.79 and said["sep_arcsec"] == 0.86  # from the verbatim record body
    assert "NOTICE: TNS discovery 31.8 d before" in capsys.readouterr().out and code == 0


@pytest.mark.parametrize("body,status,asks", [
    (fake_tns.NONE, "none_yet", 1),
    (fake_tns.REJECTED, "unavailable", 9),
    (fake_tns.UPSTREAM_ERROR, "unavailable", 9),
    ('{"surprise":true}', "unavailable", 1),
], ids=["none", "rejected_throughout", "upstream_throughout", "unknown_shape"])
def test_only_the_services_own_none_reads_as_none_yet(real_tns, env, monkeypatch, body, status, asks):
    asked = fake_tns.serve(monkeypatch, body)
    code, r, said = scan_real_alert(env, monkeypatch, AFMA, REAL_TNS_LOOKUP)
    assert said == {"status": status, "line": mon.TNS_LINE[status], "notice": None} and len(asked) == asks
    assert code == 0 and AFMA["key"] in state("ecdfs")["reported"] and AFMA["key"] in state("ecdfs")["tns_watch"]


@pytest.mark.parametrize("body", [fake_tns.REJECTED, fake_tns.UPSTREAM_ERROR, fake_tns.NONE, '{"surprise":true}'],
                         ids=["rejected", "upstream", "none", "unknown_shape"])
def test_on_a_later_run_only_a_record_is_news_everything_else_stays_watched(real_tns, env, monkeypatch, body):
    asked = fake_tns.serve(monkeypatch, body)
    code, r = asked_again_on_a_later_run(env, monkeypatch, REAL_TNS_ONCE)
    assert code == 0 and len(asked) == 1 and r["tns_earlier"] == [] and AFMA["key"] in state("ecdfs")["tns_watch"]


def test_on_a_later_run_a_record_body_is_news(real_tns, env, monkeypatch):
    fake_tns.serve(monkeypatch, fake_tns.RECORD)
    code, r = asked_again_on_a_later_run(env, monkeypatch, REAL_TNS_ONCE)
    assert code == 0 and "now in TNS: SN 2026afma, SN Ia, discovered 2026-09-28" in r["tns_earlier"][0]
    assert state("ecdfs")["tns_watch"] == {}


# --- a NaN position matches nothing (2026-11-07) ------------------------------------------------


def test_a_nan_ra_associates_with_nothing_a_real_pair_still_does():
    """The Dec window lets it through, and NaN read as 0" from sep_deg: a false associate, whose quiet points can bracket an onset."""
    nan_ra = SurveyObject("lsst", "1", math.nan, 12.0)
    ztf, lsst = SurveyObject("ztf", "ZTF26a", 200.0, 12.0), SurveyObject("lsst", "2", 200.0, 12.0)
    found = mon.associate([nan_ra, ztf, lsst])
    assert found[nan_ra.key] == [] and [o.key for o in found[ztf.key]] == [lsst.key]


@pytest.mark.parametrize("ours,theirs", [(math.nan, 200.0), (200.0, math.nan)], ids=["our_ra", "their_ra"])
def test_a_nan_ra_is_never_a_note_on_someone_elses_alert(ours, theirs):
    """A note replaces an alert, so a false match here loses the alert."""
    assert mon._alerted_elsewhere(SurveyObject("lsst", "1", ours, 12.0), {"ztf:ZTF26a": {"ra": theirs, "dec": 12.0}}) is None


def test_a_real_position_is_still_a_note():
    assert mon._alerted_elsewhere(SurveyObject("lsst", "1", 200.0, 12.0), {"ztf:ZTF26a": {"ra": 200.0, "dec": 12.0}}) == "ztf:ZTF26a"
