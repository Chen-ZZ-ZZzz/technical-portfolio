"""photometry.py — the broker-neutral fetch layer. Mock brokers only."""

import math

import pytest

from rubin_qa import photometry as ph

NOW = 61310.0


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


# --- ANTARES -------------------------------------------------------------------


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


def test_sep_deg():
    assert ph.sep_deg(10.0, 0.0, 11.0, 0.0) == pytest.approx(1.0)
    assert ph.sep_deg(359.5, 0.0, 0.5, 0.0) == pytest.approx(1.0)


# --- the ALeRCE listing, both surveys (ANTARES left the live path 2026-10-03) ----------------


def item(oid, survey="lsst", ra=150.0, dec=2.0, cls="SN", p=0.9):
    """A listing row in the survey's own field names: LSST n_det/class_name/classifier_name, ZTF ndet/class/classifier."""
    n, c, k = ph.ALERCE_LISTING_FIELDS[survey]
    return {"oid": oid, "meanra": ra, "meandec": dec, "firstmjd": NOW - 5, "lastmjd": NOW - 1, n: 4,
            c: cls, k: "stamp", "probability": p}


@pytest.mark.parametrize("survey", ["lsst", "ztf"])
def test_discover_alerce_pages_until_a_short_page_and_merges_repeated_rows(survey):
    pages = {1: [item(i, survey) for i in range(1000)], 2: [item(5, survey), item(2000, survey)]}
    seen = []

    def query(**kw):
        seen.append(kw)
        return pages[kw["page"]], None

    objs, err = ph.discover_alerce(survey, (150.0, 2.0, 2.0), NOW - 14, NOW, query=query)
    assert err is None and len(objs) == 1001 and {o.survey for o in objs} == {survey}
    assert seen[0]["radius"] == 7200.0  # ALeRCE takes arcsec
    assert seen[0]["lastmjd"] == [NOW - 14, 99999.0] and seen[0]["firstmjd"] == [0.0, NOW]
    o = next(o for o in objs if o.survey_object_id == "5")
    assert o.classifications == ["alerce stamp: SN 0.90"]  # duplicate rows add nothing
    assert o.summary == {"firstmjd": NOW - 5, "lastmjd": NOW - 1, "n_det": 4}  # one name, whatever the survey's field


@pytest.mark.parametrize("survey", ["lsst", "ztf"])
def test_discover_alerce_returns_partial_on_error_and_caps(survey):
    def query(**kw):
        return ([item(i, survey) for i in range(1000)], None) if kw["page"] == 1 else (None, "timeout")

    objs, err = ph.discover_alerce(survey, (150.0, 2.0, 2.0), NOW - 14, None, query=query)
    assert len(objs) == 1000 and err == f"alerce {survey} listing page 2: timeout"
    objs, err = ph.discover_alerce(survey, (150.0, 2.0, 2.0), NOW - 14, None, max_pages=1,
                                   query=lambda **kw: ([item(i, survey) for i in range(1000)], None))
    assert err == f"alerce {survey} listing capped at 1 pages"


def test_the_listing_query_reads_both_answer_shapes(monkeypatch):
    """ZTF (legacy client) answers a page dict with items, LSST a bare list (2026-10-03)."""
    from rubin_qa import client

    calls = []

    def fake(fn, *a, **kw):
        calls.append(kw)
        rows = [item("x", "lsst" if kw.get("survey") == "lsst" else "ztf")]
        return ({"items": rows, "has_next": False} if "survey" not in kw else rows), None

    monkeypatch.setattr(client, "_api_call", fake)
    for survey in ("ztf", "lsst"):
        objs, err = ph.discover_alerce(survey, (150.0, 2.0, 2.0), NOW - 14, None)
        assert err is None and [o.key for o in objs] == [f"{survey}:x"]
    assert "survey" not in calls[0] and calls[1]["survey"] == "lsst"  # ZTF never asks the multisurvey client


@pytest.mark.parametrize("survey", ["lsst", "ztf"])
def test_newest_in_cone_asks_one_row_ordered_on_lastmjd(survey):
    seen = []

    def query(**kw):
        seen.append(kw)
        return [item(7, survey)], None

    got, err = ph.alerce_newest_in_cone(survey, (150.0, 2.0, 2.0), NOW - 30, query=query)
    assert err is None and got["oid"] == 7
    assert (seen[0]["order_by"], seen[0]["order_mode"], seen[0]["page_size"], seen[0]["lastmjd"]) == \
        ("lastmjd", "DESC", 1, [NOW - 30, 99999.0])
    ph.alerce_newest_in_cone(survey, (150.0, 2.0, 2.0), None, query=query)
    assert "lastmjd" not in seen[1]  # no floor: the whole cone
    assert ph.alerce_newest_in_cone(survey, (150.0, 2.0, 2.0), None, query=lambda **kw: (None, "504")) == \
        (None, f"alerce {survey} newest-in-cone check: 504")

