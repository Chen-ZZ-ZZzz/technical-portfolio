"""transient_monitor.py — survey objects built by hand; no live broker or catalogue calls."""

import json
import math

import pytest
from requests.exceptions import ConnectionError

from rubin_qa import transient_monitor as mon
from rubin_qa.config import PROJECT_ROOT
from rubin_qa.photometry import PhotPoint, SurveyObject, mag_to_njy
from rubin_qa.sky_catalog import Crossmatch

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


def xm(verdict, *evidence):
    return lambda ra, dec: Crossmatch(verdict, list(evidence))


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


def test_preexisting_ztf_ids_from_locus_props():
    props = {"survey": {"ztf": {"id": ["ZTF26aaa", "ZTF18bbb"]}}}
    assert mon.preexisting_ztf_ids(props, SINCE) == ["ZTF18bbb"]


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
    assert mon.evaluate(obj(NEW_ZTF, oid="ZTF19x"), NOW, SINCE, classify=boom).reason == "preexisting_ztf_object"


@pytest.mark.parametrize("verdict,category", [
    ("host", "new_candidate"), ("agn", "agn_flare"), ("none", "orphan"),
    ("point_source", "point_source"), ("unavailable", "unchecked"),
])
def test_new_object_category_from_dr10(verdict, category):
    ev = mon.evaluate(obj(NEW_ZTF), NOW, SINCE, classify=xm(verdict, "e"))
    assert (ev.candidate.category, ev.candidate.conditions, ev.parked) == (category, [category], None)


def test_star_rejected_unless_moving():
    assert mon.evaluate(obj(NEW_ZTF), NOW, SINCE, classify=xm("stellar", "Gaia")).reason == "stellar"
    moving = [lim(NOW - 4, 21.5), det(NOW - 3, 20.5)] + RAPID
    c = mon.evaluate(obj(moving), NOW, SINCE, classify=xm("stellar", "Gaia")).candidate
    assert c.conditions == ["stellar_flare", "rising", "rapid_rise"]  # fast risers pass the slower test too


def test_old_object_rapidly_rising_is_re_brightening():
    c = mon.evaluate(obj([det(NOW - 900, 20.0)] + RAPID, oid="ZTF18old"), NOW, SINCE, classify=xm("host", "EXP")).candidate
    assert (c.category, c.conditions, c.rapid) == ("re_brightening", ["re_brightening"], pytest.approx(1.3))


def test_unbracketed_and_flat_is_parked():
    ev = mon.evaluate(obj(NEW_ZTF[1:]), NOW, SINCE, classify=xm("none"))
    assert (ev.candidate, ev.reason, ev.parked.code) == (None, "unbracketed", "no_quiet_point")


def test_lsst_rising_tier_without_a_bracket():
    """The main LSST path: no pre-onset photometry exists, the rise shows in forced photometry."""
    pts = [det(FIRST, 24.5, survey="lsst", oid="1706"), det(FIRST + 0.02, 24.5, survey="lsst", oid="1706"),
           forced(FIRST + 2, 800.0, 60.0), forced(NOW - 1, 1500.0, 70.0)]
    ev = mon.evaluate(obj(pts, survey="lsst", oid="1706"), NOW, SINCE, classify=xm("host", "EXP"))
    c = ev.candidate
    assert (c.category, c.conditions) == ("rising", ["rising"])
    assert ev.parked is None  # too few detections to be judged new at all: nothing to park
    assert c.onset.code == "too_few_detections"


def test_lsst_rising_and_unbracketed_is_reported_and_parked():
    pts = LSST_ONSET + [forced(NOW - 0.5, mag_to_njy(20.5), 60.0)]
    ev = mon.evaluate(obj(pts, survey="lsst", oid="1706"), NOW, SINCE, classify=xm("none"))
    assert (ev.candidate.category, ev.candidate.conditions) == ("rising", ["rising"])
    assert ev.parked.code == "no_quiet_point"
    assert "onset not bracketed (no_quiet_point), parked" in ev.candidate.context


def test_lsst_bracketed_by_associated_ztf_limit():
    ztf = obj([lim(FIRST - 2, 22.5), det(NOW - 300, 21.0)], oid="ZTF26z")
    ev = mon.evaluate(obj(LSST_ONSET, survey="lsst", oid="1706"), NOW, SINCE, associates=[ztf], classify=xm("host", "EXP"))
    c = ev.candidate
    assert (c.category, c.onset.code) == ("new_candidate", "bracketed_by_ztf")
    assert c.context == ["recurrent: ztf:ZTF26z detected here since 2025-12-01"]


def test_ztf_limits_do_not_bracket_ztf_objects_through_association():
    other = obj([lim(FIRST - 2, 22.5)], oid="ZTF26other")
    ev = mon.evaluate(obj(NEW_ZTF[1:]), NOW, SINCE, associates=[other], classify=xm("none"))
    assert ev.reason == "unbracketed"


def test_preexisting_ztf_object_cannot_be_rising_only_re_brightening():
    pts = [det(FIRST, 21.0), det(FIRST + 1, 20.5), det(NOW - 0.3, 19.0)]
    ev = mon.evaluate(obj(pts, oid="ZTF20old"), NOW, SINCE, classify=xm("none"))
    assert ev.reason == "preexisting_ztf_object"


# --- gathering -----------------------------------------------------------------------------


@pytest.mark.parametrize("summary,points,needed", [
    ({"firstmjd": NOW - 5, "lastmjd": NOW - 1, "n_det": 4}, [], True),     # recent onset
    ({"firstmjd": NOW - 90, "lastmjd": NOW - 1, "n_det": 9}, [], True),    # old but active: could rise fast
    ({"firstmjd": NOW - 90, "lastmjd": NOW - 10, "n_det": 9}, [], False),  # old and quiet
    ({"firstmjd": NOW - 5, "lastmjd": NOW - 5, "n_det": 1}, [], False),    # single detection
    ({}, LSST_ONSET, True),                                                 # from ANTARES detections
    ({}, [], False),
])
def test_needs_lsst_photometry(summary, points, needed):
    assert mon.needs_lsst_photometry(obj(points, survey="lsst", oid="1706", summary=summary), NOW, SINCE) is needed


def test_merge_dedups_points_and_unions_context():
    a = obj([det(1.0)], tags=["t1"], broker_refs={"antares": "ANT_a"})
    b = obj([det(1.0), det(2.0)], tags=["t1", "t2"], broker_refs={"alerce": "x"}, summary={"n_det": 2}, sso_id="9")
    mon._merge(a, b)
    assert [p.mjd for p in a.points] == [1.0, 2.0]
    assert (a.tags, a.broker_refs, a.summary, a.sso_id) == (["t1", "t2"], {"antares": "ANT_a", "alerce": "x"}, {"n_det": 2}, "9")


@pytest.fixture
def sources(monkeypatch):
    """Patchable stand-ins for every broker the monitor gathers from."""
    s = {"antares": [], "alerce": ([], None), "lsst_points": {}, "fetched": []}

    def antares(cone, since, until, min_det, keep_locus=None, ztf_fallback=None, stats=None):
        stats.update({"loci": len(s["antares"])})
        yield from s["antares"]

    def lsst(oid):
        s["fetched"].append(oid)
        got = s["lsst_points"].get(oid)
        return (got, None, None) if got is not None else ([], None, "504")

    monkeypatch.setattr(mon, "discover_antares", antares)
    monkeypatch.setattr(mon, "discover_alerce_lsst", lambda cone, since, until: s["alerce"])
    monkeypatch.setattr(mon, "fetch_alerce_lsst", lsst)
    return s


def test_gather_merges_sources_and_fetches_only_needed_lsst(sources, capsys):
    sources["antares"] = [obj(NEW_ZTF), obj([det(NOW - 1, 22, survey="lsst", oid="1706")], survey="lsst", oid="1706")]
    sources["alerce"] = ([obj([], survey="lsst", oid="1706", summary={"firstmjd": FIRST, "lastmjd": NOW - 1, "n_det": 3}),
                          obj([], survey="lsst", oid="2000", summary={"firstmjd": NOW - 90, "lastmjd": NOW - 20, "n_det": 5})], None)
    sources["lsst_points"]["1706"] = LSST_ONSET + [det(NOW + 3, 20.0, survey="lsst", oid="1706")]
    stats = {}
    objs = {o.key: o for o in mon.gather((150, 12, 1), NOW, SINCE, NOW, 3, True, stats)}
    assert sorted(objs) == ["lsst:1706", "lsst:2000", "ztf:ZTF26aaa"]
    assert sources["fetched"] == ["1706"]
    assert [p.mjd for p in objs["lsst:1706"].points] == [p.mjd for p in LSST_ONSET]  # ALeRCE's, replay-truncated
    assert objs["lsst:1706"].broker_refs == {"alerce": "1706"}
    assert (stats["lsst_fetched"], stats["lsst_not_fetched"]) == (1, 1)


def test_gather_warns_on_failed_lsst_fetch_and_listing_error(sources, capsys):
    sources["alerce"] = ([obj([], survey="lsst", oid=str(i), summary={"firstmjd": FIRST, "lastmjd": NOW, "n_det": 3})
                          for i in range(mon.WARN_LIMIT + 2)], "alerce lsst listing page 3: timeout")
    stats = {}
    objs = mon.gather((150, 12, 1), NOW, SINCE, None, 3, True, stats)
    err = capsys.readouterr().err
    assert err.count("ALeRCE photometry unavailable") == mon.WARN_LIMIT
    assert "WARN: alerce lsst listing page 3: timeout" in err
    assert stats["lsst_fetch_errors"] == mon.WARN_LIMIT + 2
    assert all(o.fetch_errors == ["alerce lsst: 504"] for o in objs)


def test_gather_without_alerce(sources):
    sources["antares"] = [obj(NEW_ZTF)]
    sources["alerce"] = None  # would blow up if touched
    assert [o.key for o in mon.gather((150, 12, 1), NOW, SINCE, None, 3, False, {})] == ["ztf:ZTF26aaa"]


# --- scan: state, reporting, retry ------------------------------------------------------------


@pytest.fixture
def env(tmp_path, monkeypatch, sources):
    monkeypatch.setattr(mon, "STATE_DIR", tmp_path / "logs")
    monkeypatch.setattr(mon, "now_mjd", lambda: NOW)
    monkeypatch.setattr(mon.time, "sleep", lambda s: None)
    monkeypatch.setattr(mon.sky_catalog, "classify", xm("host", 'EXP r=21.0 sep=1.0" d_DLR=0.8'))
    monkeypatch.setattr(mon, "antares_tns", lambda locus: ["2026abc SN Ia"])
    return sources


def state(name="D", replay=False):
    return json.loads(mon.state_file(name, replay).read_text())


def test_first_scan_reports_keyed_on_survey_ids(env, capsys):
    env["antares"] = [obj(NEW_ZTF, broker_refs={"antares": "ANT_1"}, broker_matches=["tns_public_objects"])]
    assert mon.scan("D", (150, 12, 1)) == 0
    out = capsys.readouterr().out
    assert "1 TRANSIENT ALERTS" in out and "--- new_candidate (1) ---" in out
    assert "ztf:ZTF26aaa  [new_candidate]  new: new_candidate" in out
    assert "  onset: first detection 2026-09-22 (MJD 61305.00), 3 detections, bracketed by a quiet point 1.0 d before" in out
    assert '  DR10: host - EXP r=21.0 sep=1.0" d_DLR=0.8' in out
    assert "  brokers: antares ANT_1" in out and "  TNS: 2026abc SN Ia" in out
    s = state()
    assert s["reported"] == {"ztf:ZTF26aaa": {"first_reported_mjd": NOW, "category": "new_candidate",
                                              "conditions": ["new_candidate"]}}
    assert s["parked"] == {}


def test_second_scan_does_not_repeat_but_new_condition_does(env, capsys):
    flat = [lim(NOW - 4, 21.5), det(NOW - 3, 20.5), det(NOW - 2.5, 20.5), det(NOW - 2.2, 20.5)]
    env["antares"] = [obj(flat)]
    mon.scan("D", (150, 12, 1))
    mon.scan("D", (150, 12, 1))
    runs = capsys.readouterr().out.split("Footprint D")
    assert "new: new_candidate" in runs[1] and "No new transient alerts." in runs[2]
    env["antares"] = [obj(flat + [det(NOW - 0.3, 19.0)])]
    mon.scan("D", (150, 12, 1))
    assert "new: rising, rapid_rise" in capsys.readouterr().out


def test_parked_object_resolves_when_bracketed(env, capsys):
    env["antares"] = [obj(NEW_ZTF[1:])]
    mon.scan("D", (150, 12, 1))
    out = capsys.readouterr().out
    assert "Parked, onset not bracketed: 1  (new 1, resolved 0, expired unbracketed 0)" in out
    assert "  ztf:ZTF26aaa  first detection 2026-09-22  no_quiet_point" in out
    env["antares"] = [obj(NEW_ZTF)]
    mon.scan("D", (150, 12, 1))
    out = capsys.readouterr().out
    assert "(new 0, resolved 1, expired unbracketed 0)" in out
    assert "  context: was parked since 2026-09-27" in out
    assert state()["parked"] == {}


def test_parked_object_expires(env, capsys):
    path = mon.state_file("D", False)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"last_mjd": NOW - 1, "reported": {}, "parked": {
        "lsst:1": {"survey": "lsst", "first_det_mjd": NOW - 15, "code": "no_quiet_point", "parked_mjd": NOW - 13}}}))
    mon.scan("D", (150, 12, 1))
    assert "expired unbracketed 1" in capsys.readouterr().out
    assert state()["parked"] == {}


def test_replay_uses_its_own_state_and_the_replay_date(env, capsys, monkeypatch):
    monkeypatch.setattr(mon, "now_mjd", lambda: pytest.fail("replay must not read the clock"))
    env["antares"] = [obj([lim(61140.0, 20.5), det(61141.0), det(61143.0), det(61145.0)])]
    assert mon.scan("D", (150, 12, 1), as_of_mjd=61146.0) == 0
    out = capsys.readouterr().out
    assert "REPLAY as of 2026-04-15" in out and "1 TRANSIENT ALERTS" in out
    assert mon.state_file("D", True).exists() and not mon.state_file("D", False).exists()


def test_rejections_and_sources_are_counted(env, capsys):
    env["antares"] = [obj(OLD_ZTF, oid="ZTF26old"), obj(NEW_ZTF, oid="ZTF26p", ra=IN_PLANE[0], dec=IN_PLANE[1])]
    mon.scan("D", (150, 12, 1))
    out = capsys.readouterr().out
    assert "Survey objects: 2 (ztf 2)  kept: 0" in out
    assert "  rejected galactic_plane: 1" in out and "  rejected old_no_rise: 1" in out


def test_dry_run_saves_nothing(env):
    env["antares"] = [obj(NEW_ZTF)]
    mon.scan("D", (150, 12, 1), dry_run=True)
    assert not mon.state_file("D", False).exists()


def test_old_reported_entries_are_pruned(env):
    path = mon.state_file("D", False)
    path.parent.mkdir(parents=True)
    old = {"first_reported_mjd": NOW - 100, "category": "orphan", "conditions": ["orphan"]}
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
        yield  # pragma: no cover

    monkeypatch.setattr(mon, "discover_antares", flaky)
    assert mon.scan("D", (150, 12, 1)) == 1
    err = capsys.readouterr().err
    assert calls["n"] == mon.MAX_RETRIES
    assert err.startswith("WARN: network error (attempt 1/3)") and "ERROR: network error after 3 attempts" in err
    assert not mon.state_file("D", False).exists()


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
    assert mon.state_file("ecdfs", False) == PROJECT_ROOT / "logs" / "transient_monitor_ecdfs.json"
    assert mon.state_file("D", True).name == "transient_monitor_D_replay.json"


# --- CLI ---------------------------------------------------------------------------------------


def test_footprint_or_cone_is_required_and_exclusive():
    with pytest.raises(SystemExit):
        mon._parse_args([])
    with pytest.raises(SystemExit):
        mon._parse_args(["--footprint", "D", "--cone", "1", "2", "3"])


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
    monkeypatch.setattr(mon, "scan", lambda *a: seen.setdefault("args", a) and 0)
    mon.main(["--cone", "150", "2", "1.5", "--as-of", "2026-04-15", "--lookback-days", "7",
              "--min-detections", "4", "--bracket-days", "5", "--no-alerce", "--dry-run"])
    assert seen["args"] == ("cone_150_2_1.5", (150.0, 2.0, 1.5), 7.0, 4, 5.0, 61146.0, True, False)
