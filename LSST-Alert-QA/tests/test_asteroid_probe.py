"""
tools/asteroid_probe.py — the evidence behind the transient monitor's asteroid
rules (is_solar_system, MIN_SPAN_DAYS). Mock loci and the committed CSV only;
nothing here touches a broker.
"""

import pathlib
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))

import asteroid_probe as ap  # noqa: E402
from rubin_qa.config import PROJECT_ROOT  # noqa: E402

EVIDENCE = PROJECT_ROOT / "reports" / "asteroid_probe_20260927.csv"
NOW = 61310.0
SINCE = NOW - 14.0
HIGH_B = (150.0, 12.0)
IN_PLANE = (280.0, 0.0)


def alert(mjd, mag=19.0, ssnamenr="null"):
    return SimpleNamespace(mjd=mjd, properties={"ant_mag": mag, "ztf_ssnamenr": ssnamenr})


def locus(alerts, locus_id="ANT_x", ra=HIGH_B[0], dec=HIGH_B[1], locus_ss="null",
          tags=(), catalogs=(), ztf_ids=("ZTF26abc",)):
    return SimpleNamespace(
        locus_id=locus_id, ra=ra, dec=dec, tags=list(tags), catalogs=list(catalogs),
        alerts=alerts,
        properties={"ztf_ssnamenr": locus_ss, "survey": {"ztf": {"id": list(ztf_ids)}}},
    )


def row(locus_id="ANT_x", named="", n_det=3, n_ss=0, span=2.0, old=False, tag=False, names=""):
    return {
        "locus_id": locus_id, "gal_b": 40.0, "locus_ssnamenr": named, "sso_tag": tag,
        "preexisting_ztf": old, "n_det": n_det, "n_ss_det": n_ss, "ss_names": names,
        "span_d": span, "catalogs": "", "collected_mjd": NOW,
    }


# --- the committed evidence ------------------------------------------------------


def test_evidence_reproduces_the_numbers_the_monitor_cites():
    """
    The comments on MIN_SPAN_DAYS and is_solar_system() quote these figures.
    If this fails, the evidence and the argument built on it have drifted apart.
    """
    s = ap.summarize(ap.load_csv(EVIDENCE))
    assert s["n_loci"] == 159
    assert s["n_duplicates"] == 0
    assert s["n_named"] == 62
    assert s["n_sso_tag"] == 0
    assert s["n_pure"] == 61
    assert s["n_pure_one_night"] == 60
    lo, hi = s["pure_one_night_span_d"]
    assert round(lo * 1440, 1) == 1.4 and round(hi * 24, 1) == 3.9
    assert [r["locus_id"] for r in s["pure_multi_night"]] == ["ANT2026atwn1nfdu869"]
    assert [(r["locus_id"], r["n_det"] - r["n_ss_det"]) for r in s["mixed"]] == [
        ("ANT2026p6kwpk0rmhvs", 1)
    ]
    assert s["n_unnamed_new"] == 89
    assert [r["locus_id"] for r in s["unnamed_one_night"]] == ["ANT2026b4w8bxhj4j4c"]


def test_evidence_threshold_is_the_monitors():
    from rubin_qa.transient_monitor import MIN_SPAN_DAYS

    assert ap.MIN_SPAN_DAYS == MIN_SPAN_DAYS == 0.5


# --- probe_locus -------------------------------------------------------------------


def test_pure_asteroid_locus():
    r = ap.probe_locus(
        locus([alert(NOW - 1.0, ssnamenr="41025"), alert(NOW - 0.99, ssnamenr="41025"),
               alert(NOW - 0.98, ssnamenr="41025")], locus_ss="41025"),
        SINCE, NOW,
    )
    assert (r["locus_ssnamenr"], r["n_det"], r["n_ss_det"], r["ss_names"]) == ("41025", 3, 3, "41025")
    assert r["span_d"] == pytest.approx(0.02)


def test_attribution_is_per_alert_not_from_the_locus_name():
    """The locus-level name is what is under test; each detection is judged on its own."""
    r = ap.probe_locus(
        locus([alert(NOW - 12.0), alert(NOW - 0.1, ssnamenr="103389"),
               alert(NOW - 0.09, ssnamenr="103389")], locus_ss="103389"),
        SINCE, NOW,
    )
    assert (r["n_det"], r["n_ss_det"]) == (3, 2)


def test_upper_limits_are_not_detections():
    r = ap.probe_locus(locus([alert(NOW - 5.0, mag=None), alert(NOW - 1.0), alert(NOW - 0.5)]), SINCE, NOW)
    assert r["n_det"] == 2
    assert r["span_d"] == pytest.approx(0.5)


def test_no_detections_gives_empty_span():
    assert ap.probe_locus(locus([alert(NOW - 1.0, mag=None)]), SINCE, NOW)["span_d"] is None


def test_low_latitude_locus_is_skipped():
    assert ap.probe_locus(locus([alert(NOW)], ra=IN_PLANE[0], dec=IN_PLANE[1]), SINCE, NOW) is None


def test_flags_and_lists():
    r = ap.probe_locus(
        locus([alert(NOW - 1.0), alert(NOW - 3.0, ssnamenr="801"), alert(NOW - 2.0, ssnamenr="171360")],
              tags=["sso_candidates"], catalogs=["vsx", "2mass_psc"], ztf_ids=["ZTF19old"]),
        SINCE, NOW,
    )
    assert r["sso_tag"] is True
    assert r["preexisting_ztf"] is True
    assert r["locus_ssnamenr"] == ""  # locus-level "null" is no name
    assert r["ss_names"] == "171360;801"
    assert r["catalogs"] == "2mass_psc;vsx"


# --- collect -------------------------------------------------------------------------


def test_collect_dedups_limits_and_queries_new_loci_all_sky(monkeypatch):
    import antares_client.search

    seen_query = {}
    listing = [locus([alert(NOW - 1), alert(NOW - 2)], locus_id=i) for i in ("A", "A", "B", "C")]

    def fake_search(q):
        seen_query["q"] = q
        return iter(listing)

    monkeypatch.setattr(antares_client.search, "search", fake_search)
    monkeypatch.setattr(ap, "now_mjd", lambda: NOW)
    rows = ap.collect(limit=3, lookback_days=14.0, min_detections=3)
    assert [r["locus_id"] for r in rows] == ["A", "B"]  # limit counts listing entries
    f = seen_query["q"]["query"]["bool"]["filter"]
    assert f == [
        {"range": {"properties.oldest_alert_observation_time": {"gte": NOW - 14.0}}},
        {"range": {"properties.num_mag_values": {"gte": 3}}},
    ]  # the evidence's population: new loci, no sky cut (|b| is applied per locus)


# --- CSV and summary -------------------------------------------------------------


def test_csv_round_trip_restores_types(tmp_path):
    rows = [row("A", named="41025", n_ss=3, span=0.01, tag=True), row("B", span=None, old=True)]
    path = tmp_path / "sub" / "p.csv"
    ap.write_csv(rows, path)
    assert ap.load_csv(path) == rows


def test_summary_drops_duplicates_and_counts_them():
    s = ap.summarize([row("A"), row("A"), row("B")])
    assert (s["n_loci"], s["n_duplicates"]) == (2, 1)


def test_summary_of_nothing():
    s = ap.summarize([])
    assert s["n_loci"] == 0 and s["pure_one_night_span_d"] is None
    assert "high-|b| new loci: 0" in ap.format_summary(s)


def test_preexisting_unnamed_loci_are_not_counted_as_new():
    s = ap.summarize([row("A", span=0.01, old=True), row("B", span=0.01)])
    assert s["n_unnamed_new"] == 1
    assert [r["locus_id"] for r in s["unnamed_one_night"]] == ["B"]


# --- CLI -----------------------------------------------------------------------------


def test_report_prints_summary(capsys):
    assert ap.main(["--report", str(EVIDENCE)]) == 0
    out = capsys.readouterr().out
    assert "named by ZTF (locus ztf_ssnamenr):        62 (39%)" in out
    assert "ANT2026b4w8bxhj4j4c: 3 detections in 11.5 min, no catalogue" in out


def test_report_missing_file_is_an_error(tmp_path, capsys):
    assert ap.main(["--report", str(tmp_path / "nope.csv")]) == 1
    assert capsys.readouterr().err.startswith("ERROR: no such file")


def test_collect_writes_csv(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ap, "collect", lambda limit, lb, md: [row("A")])
    out = tmp_path / "probe.csv"
    assert ap.main(["--out", str(out), "--limit", "5"]) == 0
    assert ap.load_csv(out) == [row("A")]
    assert f"wrote 1 rows to {out}" in capsys.readouterr().out


def test_collect_network_failure_exits_1(monkeypatch, capsys):
    from requests.exceptions import ConnectionError

    def down(*a):
        raise ConnectionError("down")

    monkeypatch.setattr(ap, "collect", down)
    assert ap.main([]) == 1
    assert capsys.readouterr().err.startswith("ERROR: ANTARES query failed")


def test_preexisting_ztf_ids_from_locus_props():
    """Moved here from the monitor 2026-10-03: the probe is its one user (the monitor reads ALeRCE only)."""
    props = {"survey": {"ztf": {"id": ["ZTF26aaa", "ZTF18bbb"]}}}
    assert ap.preexisting_ztf_ids(props, 61310.0 - 14) == ["ZTF18bbb"]
