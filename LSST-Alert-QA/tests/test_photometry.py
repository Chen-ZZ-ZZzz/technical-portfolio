"""photometry.py — the broker-neutral fetch layer. Mock brokers only."""

import math
from types import SimpleNamespace

import pytest
from requests.exceptions import ConnectionError

from rubin_qa import photometry as ph

NOW = 61310.0


def alert(mjd, **props):
    return SimpleNamespace(mjd=mjd, properties=props)


def ztf_det(mjd, oid="ZTF26aaa", mag=19.0, magerr=0.1, band="R", isdiffpos="t", ssnamenr="null"):
    return alert(mjd, ant_survey=1, ztf_object_id=oid, ant_mag=mag, ant_magerr=magerr, ant_passband=band,
                 ztf_isdiffpos=isdiffpos, ztf_ssnamenr=ssnamenr)


def ztf_lim(mjd, oid="ZTF26aaa", maglim=20.5, band="g"):
    return alert(mjd, ant_survey=2, ztf_object_id=oid, ant_mag=None, ant_maglim=maglim, ant_passband=band)


def lsst_det(mjd, oid=1706, flux=2000.0, err=100.0, band="r", ss=0):
    return alert(mjd, ant_survey=4, lsst_diaSource_diaObjectId=oid, lsst_diaSource_psfFlux=flux,
                 lsst_diaSource_psfFluxErr=err, lsst_diaSource_band=band, lsst_diaSource_ssObjectId=ss)


def locus(alerts, locus_id="ANT_x", ra=150.0, dec=2.0, tags=("extragalactic",), catalogs=("vsx",), props=None):
    return SimpleNamespace(locus_id=locus_id, ra=ra, dec=dec, tags=list(tags), catalogs=list(catalogs),
                           alerts=alerts, properties=props or {})


class Exploding:
    def __get__(self, obj, objtype=None):
        raise AssertionError("fetched when it should not have been")


# --- units ---------------------------------------------------------------------


def test_ab_zero_point_round_trip():
    assert ph.mag_to_njy(31.4) == pytest.approx(1.0)
    assert ph.mag_to_njy(23.9) == pytest.approx(1000.0)  # AB 23.9 = 1 uJy
    assert ph.njy_to_mag(ph.mag_to_njy(21.3)) == pytest.approx(21.3)


def test_magerr_to_flux_error():
    assert ph.magerr_to_njy(23.9, 0.1) == pytest.approx(1000 * math.log(10) / 2.5 * 0.1)
    assert math.isnan(ph.magerr_to_njy(23.9, None))


def test_truncate_for_replay():
    pts = [ph.PhotPoint("ztf", "z", m, "r", 1.0, 0.1, True, "antares") for m in (1.0, 2.0, 3.0)]
    assert [p.mjd for p in ph.truncate(pts, 2.0)] == [1.0, 2.0]
    assert ph.truncate(pts, None) is pts


@pytest.mark.parametrize("value", ["null", None, ""])
def test_ssnamenr_placeholders_are_not_asteroids(value):
    assert not ph.is_solar_system({"ztf_ssnamenr": value})


# --- ANTARES -------------------------------------------------------------------


def test_cone_query_carries_antares_sky_distance_and_ranges():
    q = ph.antares_cone_query((150.1, 2.5, 2.82), NOW - 14, None, 3)
    f = q["query"]["bool"]["filter"]
    assert f[0] == {"sky_distance": {"distance": "2.82 degree", "htm16": {"center": "150.1 2.5"}}}
    assert {"range": {"properties.newest_alert_observation_time": {"gte": NOW - 14}}} in f
    assert {"range": {"properties.num_mag_values": {"gte": 3}}} in f
    assert not any("properties.oldest_alert_observation_time" in c.get("range", {}) for c in f)


def test_cone_query_in_replay_requires_the_locus_to_exist_by_then():
    f = ph.antares_cone_query((150.1, 2.5, 2.82), NOW - 14, NOW, 3)["query"]["bool"]["filter"]
    assert {"range": {"properties.oldest_alert_observation_time": {"lte": NOW}}} in f


def test_locus_splits_into_survey_objects():
    """A locus is a place: an old ZTF object, a new one and an LSST object share this one."""
    loc = locus([ztf_det(NOW - 900, oid="ZTF18old"), ztf_det(NOW - 2, oid="ZTF26new"),
                 ztf_lim(NOW - 3, oid="ZTF26new"), lsst_det(NOW - 1)])
    objs = ph.objects_from_antares_locus(loc, loc.alerts)
    assert sorted(objs) == ["lsst:1706", "ztf:ZTF18old", "ztf:ZTF26new"]
    new = objs["ztf:ZTF26new"]
    assert (len(new.points), new.broker_refs, new.tags, new.broker_matches) == (2, {"antares": "ANT_x"}, ["extragalactic"], ["vsx"])


def test_ztf_detection_flux_sign_and_band():
    (p,) = ph.objects_from_antares_locus(loc := locus([ztf_det(NOW, mag=23.9, isdiffpos="f")]), loc.alerts)["ztf:ZTF26aaa"].points
    assert (p.flux, p.band, p.detected, p.broker) == (pytest.approx(-1000.0), "r", True, "antares")


def test_ztf_upper_limit_is_nan_flux_with_limit_over_five_sigma():
    loc = locus([ztf_lim(NOW, maglim=23.9)])
    (p,) = ph.objects_from_antares_locus(loc, loc.alerts)["ztf:ZTF26aaa"].points
    assert math.isnan(p.flux) and p.flux_err == pytest.approx(200.0) and not p.detected


def test_lsst_detection_uses_native_flux():
    loc = locus([lsst_det(NOW, flux=1234.5, err=67.8, band="z")])
    (p,) = ph.objects_from_antares_locus(loc, loc.alerts)["lsst:1706"].points
    assert (p.flux, p.flux_err, p.band, p.survey_object_id) == (1234.5, 67.8, "z", "1706")


def test_each_surveys_own_asteroid_id():
    loc = locus([ztf_det(NOW, ssnamenr="41025"), lsst_det(NOW, ss=987)])
    objs = ph.objects_from_antares_locus(loc, loc.alerts)
    assert (objs["ztf:ZTF26aaa"].sso_id, objs["lsst:1706"].sso_id) == ("41025", "987")


def test_unusable_alerts_are_skipped():
    loc = locus([alert(NOW, ant_survey=1, ztf_object_id=None, ant_mag=19.0), alert(NOW, ant_survey=9),
                 lsst_det(NOW, flux=None)])
    assert ph.objects_from_antares_locus(loc, loc.alerts) == {}


def test_locus_survey_ids():
    props = {"survey": {"ztf": {"id": ["ZTF26a"]}, "lsst": {"dia_object_id": [17]}}}
    assert ph.locus_survey_ids(props) == {"ztf": ["ZTF26a"], "lsst": ["17"]}
    assert ph.locus_survey_ids({"ztf_object_id": "ZTF19b"})["ztf"] == ["ZTF19b"]


def test_discover_antares_dedups_vetoes_and_truncates():
    vetoed = type("L", (SimpleNamespace,), {"alerts": Exploding()})(
        locus_id="ANT_veto", ra=280.0, dec=0.0, tags=[], catalogs=[], properties={})
    listing = [locus([ztf_det(NOW - 2), ztf_det(NOW + 5)], "ANT_a"), locus([ztf_det(NOW - 1)], "ANT_a"), vetoed]
    stats = {}
    objs = list(ph.discover_antares((150, 2, 1), NOW - 14, NOW, 3, keep_locus=lambda l: l.locus_id != "ANT_veto",
                                    search=lambda q: iter(listing), stats=stats))
    assert [o.key for o in objs] == ["ztf:ZTF26aaa"]
    assert [p.mjd for p in objs[0].points] == [NOW - 2]  # nothing after the replay date
    assert stats == {"loci": 2, "duplicates": 1, "loci_skipped": 1}


def test_discover_antares_falls_back_when_alerts_fetch_fails():
    failing = type("L", (SimpleNamespace,), {"alerts": property(lambda self: (_ for _ in ()).throw(ConnectionError("down")))})
    loc = failing(locus_id="ANT_f", ra=150.0, dec=2.0, tags=[], catalogs=[],
                  properties={"survey": {"ztf": {"id": ["ZTF26z"]}, "lsst": {"dia_object_id": [99]}}})
    fallback_pts = [ph.PhotPoint("ztf", "ZTF26z", NOW - 1, "r", 1.0, 0.1, True, "alerce")]
    objs = {o.key: o for o in ph.discover_antares((150, 2, 1), NOW - 14, None, 3, search=lambda q: iter([loc]),
                                                  ztf_fallback=lambda oid: (fallback_pts, None))}
    assert objs["ztf:ZTF26z"].points == fallback_pts
    assert objs["lsst:99"].points == []
    assert objs["lsst:99"].fetch_errors == ["antares alerts: down"]


def test_listing_failure_propagates_to_the_caller():
    def down(q):
        raise ConnectionError("listing down")
        yield  # pragma: no cover

    with pytest.raises(ConnectionError):
        list(ph.discover_antares((150, 2, 1), NOW - 14, None, 3, search=down))


def test_antares_tns(monkeypatch):
    import antares_client.search

    loc = SimpleNamespace(catalog_objects={"tns_public_objects": [{"name": "2026abc", "type": "SN Ia"}]})
    monkeypatch.setattr(antares_client.search, "get_by_id", lambda lid: loc)
    assert ph.antares_tns("ANT_x") == ["2026abc SN Ia"]


# --- ALeRCE --------------------------------------------------------------------


def test_ztf_points_from_alerce():
    lc = {"detections": [{"mjd": NOW, "fid": 2, "magpsf": 23.9, "sigmapsf": 0.1, "isdiffpos": -1},
                         {"mjd": NOW, "fid": 1, "magpsf": None}],
          "non_detections": [{"mjd": NOW - 1, "fid": 1, "diffmaglim": 23.9}]}
    det, lim = ph.ztf_points_from_alerce("ZTF26a", lc)
    assert (det.band, det.flux, det.broker) == ("r", pytest.approx(-1000.0), "alerce")
    assert (lim.band, math.isnan(lim.flux), lim.flux_err) == ("g", True, pytest.approx(200.0))


def test_lsst_points_from_alerce_include_forced_photometry():
    lc = {"detections": [{"mjd": NOW, "band_name": "g", "psfFlux": 500.0, "psfFluxErr": 50.0, "ssObjectId": 0}],
          "forced_photometry": [{"mjd": NOW + 2, "band_name": "g", "psfFlux": 800.0, "psfFluxErr": 60.0},
                                {"mjd": NOW + 3, "band_name": "g", "psfFlux": None}],
          "non_detections": []}
    points, sso = ph.lsst_points_from_alerce("1706", lc)
    assert [(p.detected, p.flux) for p in points] == [(True, 500.0), (False, 800.0)]
    assert sso is None
    lc["detections"][0]["ssObjectId"] = 42
    assert ph.lsst_points_from_alerce("1706", lc)[1] == "42"


def test_fetchers_use_alerce_lightcurve(monkeypatch):
    from rubin_qa import client

    calls = []

    def fake(fn, *a, **kw):
        calls.append((fn.__name__, a, kw))
        return {"detections": [], "non_detections": [], "forced_photometry": []}, None

    monkeypatch.setattr(client, "_api_call", fake)
    assert ph.fetch_alerce_ztf("ZTF26a") == ([], None)
    assert ph.fetch_alerce_lsst("1706") == ([], None, None)
    assert calls == [("query_lightcurve", ("ZTF26a",), {"format": "json"}),
                     ("query_lightcurve", ("1706",), {"survey": "lsst", "format": "json"})]
    monkeypatch.setattr(client, "_api_call", lambda fn, *a, **kw: (None, "504"))
    assert ph.fetch_alerce_ztf("ZTF26a") == ([], "504")
    assert ph.fetch_alerce_lsst("1706") == ([], None, "504")


def item(oid, ra=150.0, dec=2.0, cls="SN", p=0.9):
    return {"oid": oid, "meanra": ra, "meandec": dec, "firstmjd": NOW - 5, "lastmjd": NOW - 1, "n_det": 4,
            "class_name": cls, "classifier_name": "stamp", "probability": p}


def test_discover_alerce_lsst_pages_until_short_page():
    pages = {1: [item(i) for i in range(1000)], 2: [item(5), item(2000)]}
    seen = []

    def query(**kw):
        seen.append(kw)
        return pages[kw["page"]], None

    objs, err = ph.discover_alerce_lsst((150.0, 2.0, 2.0), NOW - 14, NOW, query=query)
    assert err is None and len(objs) == 1001
    assert seen[0]["radius"] == 7200.0  # ALeRCE takes arcsec
    assert seen[0]["lastmjd"] == [NOW - 14, 99999.0] and seen[0]["firstmjd"] == [0.0, NOW]
    o = next(o for o in objs if o.survey_object_id == "5")
    assert o.classifications == ["alerce stamp: SN 0.90"]  # duplicate rows add nothing
    assert o.summary == {"firstmjd": NOW - 5, "lastmjd": NOW - 1, "n_det": 4}


def test_discover_alerce_lsst_returns_partial_on_error_and_caps():
    def query(**kw):
        return ([item(i) for i in range(1000)], None) if kw["page"] == 1 else (None, "timeout")

    objs, err = ph.discover_alerce_lsst((150.0, 2.0, 2.0), NOW - 14, None, query=query)
    assert len(objs) == 1000 and err == "alerce lsst listing page 2: timeout"
    objs, err = ph.discover_alerce_lsst((150.0, 2.0, 2.0), NOW - 14, None, max_pages=1,
                                        query=lambda **kw: ([item(i) for i in range(1000)], None))
    assert err == "alerce lsst listing capped at 1 pages"


def test_sep_deg():
    assert ph.sep_deg(10.0, 0.0, 11.0, 0.0) == pytest.approx(1.0)
    assert ph.sep_deg(359.5, 0.0, 0.5, 0.0) == pytest.approx(1.0)
