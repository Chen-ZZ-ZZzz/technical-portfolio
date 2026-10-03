"""replay.py — candidate listing, cached light curves, day-by-day judging. No live broker calls."""

import json
import math
from types import SimpleNamespace

import pytest

from rubin_qa import replay
from rubin_qa import transient_monitor as mon
from rubin_qa.photometry import PhotPoint, mag_to_njy
from rubin_qa.sky_catalog import Crossmatch

CONE = (150.1, 2.5, 2.82)
DAY0 = "2026-04-08"
NOW0 = mon.date_to_mjd(DAY0)  # end of 04-08


@pytest.fixture(autouse=True)
def tmp_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(replay, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(replay, "REPORT_DIR", tmp_path / "logs")
    monkeypatch.setattr(mon, "STATE_DIR", tmp_path / "state")  # the point-source watch file
    monkeypatch.setattr(mon.tns, "tns_lookup", lambda ra, dec: pytest.fail("TNS asked in a test that set no answer"))
    return tmp_path


def det(mjd, mag=19.0, oid="ZTF26aaa", survey="ztf", band="r"):
    f = mag_to_njy(mag)
    return PhotPoint(survey, oid, mjd, band, f, f * math.log(10) / 2.5 * 0.05, True, "test")


def lim(mjd, maglim=21.5, oid="ZTF26aaa"):
    return PhotPoint("ztf", oid, mjd, "r", math.nan, mag_to_njy(maglim) / 5, False, "test")


def item(oid, first, last, ndet, survey="ztf"):
    return {"oid": oid, "meanra": 150.0, "meandec": 2.0, "firstmjd": first, "lastmjd": last,
            ("n_det" if survey == "lsst" else "ndet"): ndet}


# --- listing ----------------------------------------------------------------------------


def fake_query(items_by_window):
    """query(survey, cone, lo, hi, min_ndet, page) answering from {(lo, hi): [items]}, with a call log."""
    calls = []

    def query(survey, cone, lo, hi, min_ndet, page):
        calls.append((round(lo, 1), round(hi, 1), min_ndet))
        items = [i for i in items_by_window.get((round(lo, 1), round(hi, 1)), [])
                 if (i.get("ndet") or i.get("n_det")) >= min_ndet]
        return items, False
    query.calls = calls
    return query


def test_listing_is_chunked_and_the_span_cut_is_applied():
    lo, hi = 100.0, 114.0
    q = fake_query({
        (100.0, 107.0): [item("ZTF26one_night", 101.0, 101.2, 3), item("ZTF26two_nights", 102.0, 103.5, 2)],
        (107.0, 114.0): [item("ZTF26late", 110.0, 140.0, 5)],
    })
    got = replay.list_candidates("ztf", CONE, lo, hi, cut=True, query=q)
    assert [r["oid"] for r in got] == ["ZTF26two_nights", "ZTF26late"]  # one night is cut, a long span kept
    assert [(c[0], c[1]) for c in q.calls] == [(100.0, 107.0), (107.0, 114.0)]
    assert all(c[2] == 2 for c in q.calls)  # n_det >= 2 asked server side


def test_without_the_cut_every_first_detection_is_listed():
    q = fake_query({(100.0, 107.0): [item("ZTF26one_night", 101.0, 101.2, 1), item("ZTF26two", 102.0, 103.5, 2)]})
    got = replay.list_candidates("ztf", CONE, 100.0, 107.0, cut=False, query=q)
    assert {r["oid"] for r in got} == {"ZTF26one_night", "ZTF26two"} and q.calls[0][2] == 1


def test_an_empty_chunk_is_asked_again_day_by_day():
    """ALeRCE's LSST listing can answer a heavy query with an empty result instead of an error."""
    q = fake_query({(100.0, 107.0): [],  # the silent-empty answer
                    (103.0, 104.0): [item("1706", 103.2, 105.0, 4, "lsst")]})
    notes = []
    got = replay.list_candidates("lsst", CONE, 100.0, 107.0, cut=True, query=q, notes=notes)
    assert [r["oid"] for r in got] == ["1706"]
    assert notes and "empty chunk answer contradicted by daily queries" in notes[0]


def test_a_genuinely_empty_chunk_stays_empty_after_the_daily_check():
    q = fake_query({})
    notes = []
    assert replay.list_candidates("lsst", CONE, 100.0, 107.0, query=q, notes=notes) == [] and notes == []
    assert len(q.calls) == 1 + 7


def test_a_listing_error_propagates():
    def down(*a):
        raise replay.ListingError("alerce lsst listing: 504")

    with pytest.raises(replay.ListingError):
        replay.list_candidates("lsst", CONE, 100.0, 107.0, query=down)


# --- light curves -----------------------------------------------------------------------


def test_a_light_curve_is_fetched_once_and_cached():
    calls = []

    def fetch(survey, oid):
        calls.append(oid)
        return [det(100.0, oid=oid)], None, None

    first = replay.light_curve("ztf", "ZTF26aaa", fetch)
    again = replay.light_curve("ztf", "ZTF26aaa", lambda *a: pytest.fail("cache miss"))
    assert calls == ["ZTF26aaa"] and first[0] == again[0] and again[2] is None


def test_a_failed_fetch_is_never_cached():
    assert replay.light_curve("lsst", "1706", lambda *a: ([], None, "504"))[2] == "504"
    assert not replay.is_cached("lsst", "1706")


# --- the replay -------------------------------------------------------------------------


@pytest.fixture
def world(monkeypatch):
    """One footprint's worth of candidates and light curves, all in memory."""
    data = {"rows": [], "curves": {}}
    monkeypatch.setattr(replay, "cached_candidates",
                        lambda name, survey, cone, lo, hi, cut, notes: [r for r in data["rows"] if r["survey"] == survey
                                                                         and (not cut or mon.spans_nights(r["first"], r["last"]))])
    monkeypatch.setattr(replay, "ztf_neighbours", lambda row: data.get("neighbours", {}).get(row["oid"], []))
    monkeypatch.setattr(replay, "light_curve", lambda survey, oid: (data["curves"][oid], None, None))
    monkeypatch.setattr(replay, "is_cached", lambda survey, oid: True)
    monkeypatch.setattr(replay.sc, "footprint_atlas", lambda cone: None)
    monkeypatch.setattr(replay, "catalogue_needs", lambda rows, cone: data.get("needs", ([], [])))
    monkeypatch.setattr(replay, "prefetch_catalogue", lambda tiles, bricks, say=print: data.setdefault("prefetched", (tiles, bricks)) and [])
    return data


def add(world, oid, points, survey="ztf"):
    dets = [p for p in points if p.detected]
    world["rows"].append({"survey": survey, "oid": oid, "ra": 150.0, "dec": 12.0,
                          "first": dets[0].mjd, "last": dets[-1].mjd, "ndet": len(dets)})
    world["curves"][oid] = points


HOST = lambda ra, dec: Crossmatch("host", ['EXP r=20.0 sep=1.0" d_DLR=0.5'])


def test_each_day_sees_only_what_existed_then_and_alerts_once(world, capsys):
    # first detected 04-06, second night 04-08 night, a quiet point before: new on 04-08, not before
    add(world, "ZTF26sn", [lim(NOW0 - 4.0), det(NOW0 - 2.4), det(NOW0 - 2.38), det(NOW0 - 0.4), det(NOW0 + 1.6)])
    records = replay.run("D", CONE, "2026-04-06", "2026-04-10", classify=HOST)
    assert [(r["day"], r["key"], r["category"], r["conditions"]) for r in records] == [
        ("2026-04-08", "ztf:ZTF26sn", "host", ["bracketed"])]
    out = capsys.readouterr().out
    assert "2026-04-07:     1 objects, 0 alerts" in out  # seen, one night only: not yet
    assert "~0 min at 1.13 s per call" in out  # the estimate comes first, before any fetch


def test_the_future_never_leaks_into_a_day(world):
    """An object detected only after a day does not exist on that day."""
    add(world, "ZTF26later", [lim(NOW0 + 1.0), det(NOW0 + 2.0), det(NOW0 + 3.0), det(NOW0 + 4.0)])
    seen = []
    real = mon.judge_day

    def spy(objs, now, *a, **k):
        seen.append((now, [p.mjd for o in objs for p in o.points]))
        return real(objs, now, *a, **k)
    replay.mon.judge_day = spy
    try:
        replay.run("D", CONE, "2026-04-08", "2026-04-12", classify=HOST, quiet=True)
    finally:
        replay.mon.judge_day = real
    for now, mjds in seen:
        assert all(m <= now for m in mjds)
    assert seen[0][1] == []  # on 04-08 it has not been detected yet


def test_the_span_cut_changes_nothing_reported(world):
    """Equivalence on synthetic data: what the cut removes can never be reported."""
    add(world, "ZTF26sn", [lim(NOW0 - 4.0), det(NOW0 - 2.4), det(NOW0 - 1.4), det(NOW0 - 0.4)])
    # one night only, yet rising and a rapid rise within it: the shapes the cut removes
    add(world, "ZTF26flash", [lim(NOW0 - 3.0), det(NOW0 - 1.40, 20.0), det(NOW0 - 1.35, 18.7), det(NOW0 - 1.30, 18.5)])
    cut = replay.alert_set(replay.run("D", CONE, "2026-04-06", "2026-04-10", cut=True, classify=HOST, quiet=True))
    uncut = replay.alert_set(replay.run("D", CONE, "2026-04-06", "2026-04-10", cut=False, classify=HOST, quiet=True))
    assert cut == uncut and {k for _, k, _, _, _ in cut} == {"ztf:ZTF26sn"}


def test_the_replay_writes_its_alerts_with_the_window_and_the_cut(world, tmp_dirs):
    add(world, "ZTF26sn", [lim(NOW0 - 4.0), det(NOW0 - 2.4), det(NOW0 - 1.4), det(NOW0 - 0.4)])
    replay.run("D", CONE, "2026-04-06", "2026-04-10", cut=False, classify=HOST, quiet=True)
    report = json.loads((tmp_dirs / "logs" / "replay_D_2026-04-06_2026-04-10_nocut.json").read_text())
    assert report["cut"] is False and report["alerts"][0]["key"] == "ztf:ZTF26sn"


def test_the_window_reaches_back_one_lookback():
    lo, hi, days = replay.window("2026-04-08", "2026-04-10", 14.0)
    assert (lo, hi) == (mon.date_to_mjd("2026-04-08") - 15.0, mon.date_to_mjd("2026-04-10"))
    assert days == ["2026-04-08", "2026-04-09", "2026-04-10"]


def test_ztf_neighbours_bracket_lsst_onsets_but_are_never_judged(world):
    """An old ZTF object at an LSST onset brackets it; its own re-brightening is out of the replay's scope."""
    lsst = [det(NOW0 - 2.4, 22.0, oid="1706", survey="lsst"), det(NOW0 - 1.4, 21.9, oid="1706", survey="lsst"),
            det(NOW0 - 0.4, 21.8, oid="1706", survey="lsst")]
    add(world, "1706", lsst, survey="lsst")
    old_ztf = [det(NOW0 - 300, 20.0, oid="ZTF19old"), lim(NOW0 - 3.0, 23.0, oid="ZTF19old"),
               det(NOW0 - 1.0, 21.0, oid="ZTF19old"), det(NOW0 - 0.3, 19.6, oid="ZTF19old")]  # a rapid rise of its own
    world["curves"]["ZTF19old"] = old_ztf
    world["neighbours"] = {"1706": [{"survey": "ztf", "oid": "ZTF19old", "ra": 150.0, "dec": 12.0,
                                      "first": NOW0 - 300, "last": NOW0 - 0.3, "ndet": 3}]}
    records = replay.run("D", CONE, "2026-04-08", "2026-04-08", classify=HOST, quiet=True)
    assert [(r["key"], r["onset"]) for r in records] == [("lsst:1706", "bracketed_by_ztf")]


def test_estimate_only_fetches_nothing(world, monkeypatch, capsys):
    add(world, "ZTF26sn", [lim(NOW0 - 4.0), det(NOW0 - 2.4), det(NOW0 - 1.4), det(NOW0 - 0.4)])
    monkeypatch.setattr(replay, "is_cached", lambda survey, oid: False)
    monkeypatch.setattr(replay, "light_curve", lambda *a: pytest.fail("an estimate must not fetch"))
    assert replay.run("D", CONE, "2026-04-06", "2026-04-10", classify=HOST, estimate_only=True) == []
    assert "to fetch: 1 light curves" in capsys.readouterr().out


def test_the_catalogue_is_fetched_before_any_day_is_judged(world, monkeypatch):
    add(world, "ZTF26sn", [lim(NOW0 - 4.0), det(NOW0 - 2.4), det(NOW0 - 1.4), det(NOW0 - 0.4)])
    world["needs"] = ([(1501, 920)], ["1501p020"])
    order = []
    monkeypatch.setattr(replay, "prefetch_catalogue", lambda tiles, bricks, say=print: order.append(("prefetch", tiles, bricks)) or [])
    real = mon.judge_day
    monkeypatch.setattr(replay.mon, "judge_day", lambda *a, **k: order.append("judge") or real(*a, **k))
    replay.run("D", CONE, "2026-04-08", "2026-04-09", classify=HOST, quiet=True)
    assert order[0] == ("prefetch", [(1501, 920)], ["1501p020"]) and order[1:] == ["judge", "judge"]


def test_unavailable_crossmatches_are_counted_not_passed_as_results(world, capsys):
    add(world, "ZTF26sn", [lim(NOW0 - 4.0), det(NOW0 - 2.4), det(NOW0 - 1.4), det(NOW0 - 0.4)])
    down = replay._memo(lambda ra, dec: Crossmatch("unavailable", ["tile 1501,920: ReadTimeout"]))
    replay.run("D", CONE, "2026-04-06", "2026-04-10", classify=down)
    assert "crossmatch unavailable for 1 positions: 0 catalogue gaps, 1 fetch failures" in capsys.readouterr().out


def test_catalogue_needs_skip_objects_that_can_never_reach_the_crossmatch(monkeypatch):
    import pandas as pd
    monkeypatch.setattr(replay.sc, "footprint_bricks", lambda cone: pd.DataFrame(
        {"brickname": ["b"], "ra1": [149.0], "ra2": [151.0], "dec1": [1.0], "dec2": [3.0]}))
    monkeypatch.setattr(replay.sc, "tile_is_cached", lambda i, j: False)
    one_night = {"ra": 150.0, "dec": 2.0, "first": 100.0, "last": 100.1}
    assert replay.catalogue_needs([one_night], CONE) == ([], [])
    two_nights = {**one_night, "last": 101.0}
    tiles, bricks = replay.catalogue_needs([two_nights], CONE)
    assert tiles and bricks == ["b"]


def test_stage_timer_separates_suspended_time_from_work():
    ticks = iter([(0.0, 0.0), (10.0, 10.0), (12.0, 3612.0)])  # 2 s of work, then a 1 h suspend
    t = replay.StageTimer(clocks=lambda: next(ticks))
    assert t.mark("listing") == {"stage": "listing", "awake_s": 10.0, "suspended_s": 0.0}
    assert t.mark("judging") == {"stage": "judging", "awake_s": 2.0, "suspended_s": 3600.0}
    assert t.summary() == "listing 10s, judging 2s; suspended 3600s (excluded above)"


# --- pricing before fetching, and the cost gate (2026-10-02, after an aborted --compare-cuts) ---


def test_compare_cuts_prices_both_sides_before_either_fetches(world, monkeypatch, capsys):
    """The uncut side is the expensive one: its price must be known, and confirmed, before anything is fetched."""
    add(world, "ZTF26sn", [lim(NOW0 - 4.0), det(NOW0 - 2.4), det(NOW0 - 1.4), det(NOW0 - 0.4)])
    for k in range(1000):  # one-night objects: only the uncut side lists them
        add(world, f"ZTF26n{k}", [det(NOW0 - 1.0), det(NOW0 - 0.98)])
    monkeypatch.setattr(replay, "is_cached", lambda survey, oid: False)
    monkeypatch.setattr(replay, "light_curve", lambda *a: pytest.fail("fetched before the cost was confirmed"))
    monkeypatch.setattr(replay.mon, "footprint_classifier", lambda cone: HOST)
    monkeypatch.setattr(replay.sys, "stdin", None)  # no terminal: no one to ask
    with pytest.raises(replay.Declined, match="no terminal to confirm on"):
        replay.compare_cuts("D", CONE, "2026-04-06", "2026-04-10")
    out = capsys.readouterr().out
    assert "Equivalence test: ~0 min with the cut + ~19 min without it" in out


@pytest.mark.parametrize("answer,goes", [("y", True), ("n", False), ("", False)])
def test_on_a_terminal_a_long_replay_asks_first(monkeypatch, answer, goes):
    monkeypatch.setattr(replay.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("builtins.input", lambda prompt: answer)
    if goes:
        replay.confirm_cost(replay.CONFIRM_THRESHOLD_SECONDS + 1, False, "replay D")
    else:
        with pytest.raises(replay.Declined, match="declined"):
            replay.confirm_cost(replay.CONFIRM_THRESHOLD_SECONDS + 1, False, "replay D")


def test_a_short_replay_or_yes_needs_no_confirmation(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt: pytest.fail("asked"))
    monkeypatch.setattr(replay.sys, "stdin", None)
    replay.confirm_cost(replay.CONFIRM_THRESHOLD_SECONDS, False, "replay D")  # at the threshold: short enough
    replay.confirm_cost(10 * replay.CONFIRM_THRESHOLD_SECONDS, True, "replay D")


# --- cache files: atomic writes, checked reads ---------------------------------------------


@pytest.mark.parametrize("text", ['{"points": [{"survey": "ztf", "mjd": 1', "", '{"points": [], "x": 1}', "[1, 2]"])
def test_a_damaged_light_curve_is_fetched_again_not_read(capsys, text):
    path = replay._lc_path("ztf", "ZTF26aaa")
    path.parent.mkdir(parents=True)
    path.write_text(text)  # what an aborted, non-atomic write could leave
    calls = []
    points, sso, err = replay.light_curve("ztf", "ZTF26aaa", fetch=lambda s, o: calls.append(o) or ([det(NOW0)], None, None))
    assert calls == ["ZTF26aaa"] and err is None and len(points) == 1
    assert "damaged cache file" in capsys.readouterr().err
    assert replay.light_curve("ztf", "ZTF26aaa", fetch=lambda s, o: pytest.fail("refetched a good file"))[0] == points


def test_damaged_candidate_and_neighbour_files_are_listed_again(capsys, monkeypatch):
    lo, hi = 61121.0, 61137.0
    path = replay.CACHE_DIR / "candidates" / f"D_ztf_{lo:.1f}_{hi:.1f}_cut.json"
    path.parent.mkdir(parents=True)
    path.write_text('[{"survey": "ztf", "oid": "ZTF26a"')  # truncated mid-row
    good = [{"survey": "ztf", "oid": "ZTF26a", "ra": 1.0, "dec": 2.0, "first": lo, "last": lo + 1, "ndet": 3}]
    monkeypatch.setattr(replay, "list_candidates", lambda *a, **k: good)
    assert replay.cached_candidates("D", "ztf", CONE, lo, hi, True, []) == good
    nb = replay._neighbours_path("1706")
    nb.parent.mkdir(parents=True)
    nb.write_text('{"oid"')
    out = replay.ztf_neighbours({"oid": "1706", "ra": 1.0, "dec": 2.0}, query=lambda ra, dec, r: [])
    assert out == [] and capsys.readouterr().err.count("damaged cache file") == 2


def test_cache_writes_go_through_a_temp_file(monkeypatch):
    """The file only ever appears complete: written to a temp name, then renamed."""
    renames = []
    real = replay.Path.rename
    monkeypatch.setattr(replay.Path, "rename", lambda self, target: renames.append((self.name, target.name)) or real(self, target))
    replay.light_curve("ztf", "ZTF26aaa", fetch=lambda s, o: ([det(NOW0)], None, None))
    assert renames == [("ZTF26aaa.json.tmp", "ZTF26aaa.json")]
    assert not list(replay.CACHE_DIR.rglob("*.tmp"))


def test_the_replay_feeds_its_point_source_alerts_to_the_watch(world, monkeypatch, capsys):
    add(world, "ZTF26ps", [lim(NOW0 - 4.0), det(NOW0 - 2.4), det(NOW0 - 1.4), det(NOW0 - 0.4)])
    psf = lambda ra, dec: Crossmatch("point_source", ['PSF r=26.1 at 0.2"'])
    monkeypatch.setattr(mon.tns, "tns_lookup",
                        lambda ra, dec: {"objname": "2026kfw", "name_prefix": "SN", "object_type": {"id": 3, "name": "SN Ia"},
                                         "radeg": ra, "decdeg": dec})
    monkeypatch.setattr(mon.sky_catalog, "counterpart_snr", lambda ra, dec: 6.3)
    replay.run("D", CONE, "2026-04-06", "2026-04-10", classify=psf)
    out = capsys.readouterr().out
    assert "spectroscopic supernovae on a point source below S/N 7: 1 of 3 (SN 2026kfw S/N 6.3)" in out
    watched = json.loads((mon.STATE_DIR / mon.PS_WATCH_FILE).read_text())["objects"]
    assert watched["ztf:ZTF26ps"]["source"] == "replay D 2026-04-06..2026-04-10"
