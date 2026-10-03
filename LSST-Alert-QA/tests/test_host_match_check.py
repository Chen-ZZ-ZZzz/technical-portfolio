"""
Failure handling in tools/host_match_check.py, by injection.

The tool's output sets the host-matching threshold, so a failure that quietly turns
into "no host" or an orphan would bias that choice. Each handler is fed its failure
and must come back as a fetch error (not saved, or scored as "error"), never as data.
No network: every broker, resolver and catalogue call is replaced.
"""

import json
import pathlib
import sys

import pandas as pd
import pytest
import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))

import host_match_check as h  # noqa: E402

from rubin_qa import tns as t  # noqa: E402  (the lookup lives there; patch it there)

SN = {"footprint": "D", "name": "2026xyz", "type": "SN Ia", "z": 0.05, "ra": 150.0, "dec": 2.0, "locus": "ANT1"}


@pytest.fixture
def sample_env(tmp_path, monkeypatch):
    monkeypatch.setattr(h, "OUT_DIR", tmp_path)
    monkeypatch.setattr(h, "SAMPLE_DIR", tmp_path)
    monkeypatch.setattr(h.time, "sleep", lambda s: None)
    monkeypatch.setattr(h, "antares_tns_sne", lambda fp: [dict(SN)])
    return tmp_path


CANARY_REC = {"objname": h.TNS_CANARY["name"], "hostname": "NGC1398"}


def tns(answer, canary=CANARY_REC, calls=None):
    """A tns_once: the canary position gets `canary`, every other position `answer`."""
    def once(ra, dec):
        if calls is not None:
            calls.append((ra, dec))
        return canary if (ra, dec) == (h.TNS_CANARY["ra"], h.TNS_CANARY["dec"]) else answer
    return once


def pending(tmp_path):
    path = tmp_path / h.PENDING_FILE
    return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []


def saved(tmp_path):
    path = tmp_path / "sne.jsonl"
    return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []


def test_a_decoded_j_name_with_a_plus_resolves_from_its_coordinates(monkeypatch):
    def offline(name):
        raise AssertionError("Sesame consulted for a name with embedded coordinates")

    monkeypatch.setattr(h, "sesame", offline)
    ra, dec, how = h.resolve_host("2MASXJ09565234+0328119")
    assert (round(ra, 5), round(dec, 5), how) == (149.21808, 3.46997, "name:2MASXJ09565234+0328119")


def test_resolve_host_raises_when_every_lookup_failed(monkeypatch):
    monkeypatch.setattr(h.time, "sleep", lambda s: None)

    def down(name):
        raise requests.ConnectionError("Sesame down")

    monkeypatch.setattr(h, "sesame", down)
    with pytest.raises(requests.RequestException):
        h.resolve_host("NGC 1234, UGC 5678")


def test_resolve_host_none_only_when_sesame_answered_and_knows_nothing(monkeypatch):
    monkeypatch.setattr(h.time, "sleep", lambda s: None)
    monkeypatch.setattr(h, "sesame", lambda name: None)
    assert h.resolve_host("anonymous galaxy") is None


def test_sample_an_empty_tns_record_is_not_saved_as_no_host(sample_env, monkeypatch, capsys):
    """The pre-fix bug: 200 with an empty record was saved as 'no hostname' (14 of 44, 2026-10-01)."""
    monkeypatch.setattr(t, "tns_once", tns({}, canary={}))  # throttled: empty to everything
    assert h.run_sample(["D"]) == 1  # something is pending
    assert saved(sample_env) == []  # not recorded as "no host"
    assert "2026xyz: TNS unavailable (request failed, or still throttled after 180 s); not saved" in capsys.readouterr().err


def test_sample_reports_a_vouched_empty_answer_as_not_found(sample_env, monkeypatch, capsys):
    monkeypatch.setattr(t, "tns_once", tns({}))
    assert h.run_sample(["D"]) == 1 and saved(sample_env) == []
    assert "not found at ALeRCE's TNS service (vouched for by the canary in the same window)" in capsys.readouterr().err


def test_a_pending_sn_is_asked_again_even_when_antares_no_longer_lists_it(sample_env, monkeypatch):
    monkeypatch.setattr(t, "tns_once", tns({}, canary={}))
    h.run_sample(["D"])
    (p,) = pending(sample_env)
    assert p["pending_reason"].startswith("TNS unavailable") and p["pending_runs"] == 1
    h.run_sample(["D"])
    assert pending(sample_env)[0]["pending_runs"] == 2  # counted, not reset

    monkeypatch.setattr(h, "antares_tns_sne", lambda fp: [])  # gone from the listing
    monkeypatch.setattr(t, "tns_once", tns({"objname": "2026xyz", "hostname": ""}))  # the service is back
    assert h.run_sample(["D"]) == 0
    assert [sn["name"] for sn in saved(sample_env)] == ["2026xyz"] and pending(sample_env) == []


def test_sample_failed_host_lookup_is_not_saved_without_a_host(sample_env, monkeypatch, capsys):
    monkeypatch.setattr(t, "tns_once", tns({"objname": "2026xyz", "hostname": "NGC 1234"}))

    def down(name):
        raise requests.ConnectionError("Sesame down")

    monkeypatch.setattr(h, "sesame", down)
    h.run_sample(["D"])
    assert saved(sample_env) == []
    assert "2026xyz: host lookup failed: ConnectionError Sesame down; not saved" in capsys.readouterr().err


def test_sample_saves_a_real_answer(sample_env, monkeypatch):
    monkeypatch.setattr(t, "tns_once", tns({"objname": "2026xyz", "hostname": "SDSS J100000.00+020010.0"}))
    h.run_sample(["D"])
    (sn,) = saved(sample_env)
    assert sn["hostname"].startswith("SDSS") and sn["host_how"].startswith("name:") and "tns_mismatch" not in sn


def test_scoring_an_atlas_failure_is_a_fetch_error_not_an_orphan(monkeypatch):
    def down(footprint):
        raise h.sc.CatalogError("atlas for cone 150.1 2.5 2.82: ReadTimeout")

    monkeypatch.setattr(h, "_atlas", down)
    monkeypatch.setattr(h, "_tile", lambda i, j: pd.DataFrame(columns=[*h.sc.COLUMNS, *h.sc.ATLAS_COLUMNS]))
    out = h.score_or_error("D", 150.0, 2.0)
    assert out == {"error": "atlas for cone 150.1 2.5 2.82: ReadTimeout", "tile_rows": 0}


def test_scoring_a_tile_failure_is_a_fetch_error_not_an_orphan(monkeypatch):
    def down(i, j):
        raise h.sc.CatalogError(f"tile {i},{j}: ReadTimeout")

    monkeypatch.setattr(h, "_tile", down)
    out = h.score_or_error("D", 150.0, 2.0, (150.0, 2.001), 1.0)
    assert set(out) == {"error", "tile_rows"} and "ReadTimeout" in out["error"]


def test_report_excludes_and_counts_fetch_errors(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(h, "OUT_DIR", tmp_path)
    good = {"source": "DES", "footprint": "C", "name": "1", "truth": "ok", "selfcheck_ok": True, "des_ddlr": 1.0,
            "tile_rows": 100, **{v: {"best_d": 0.5, "best_is_true": True, "true_d": 0.5} for v in h.VARIANTS}}
    bad = {"source": "DES", "footprint": "C", "name": "2", "error": "tile 1,1: ReadTimeout", "tile_rows": 0}
    (tmp_path / "eval_known.jsonl").write_text(json.dumps(good) + "\n" + json.dumps(bad) + "\n")
    h.run_report()
    out = capsys.readouterr().out
    assert "Fetch error, excluded from every score: 1 positions (1 known-host, 0 random" in out
    assert "DES: ok 1" in out  # the failed pair is not among the scored ones


def _rows(*points):
    df = pd.DataFrame([{**{c: 0.0 for c in [*h.sc.COLUMNS, *h.sc.ATLAS_COLUMNS]}, "ra": ra, "dec": dec, "type": t,
                        "ref_cat": "", "ref_id": 0} for ra, dec, t in points])
    return lambda i, j: df if (i, j) == h.sc.tile_of(150.0, 2.0) else df.iloc[0:0]


@pytest.fixture
def no_gap_world(monkeypatch):
    monkeypatch.setattr(h, "_atlas", lambda fp: pd.DataFrame(columns=[*h.sc.COLUMNS, *h.sc.ATLAS_COLUMNS]))
    monkeypatch.setattr(h, "_bricks", lambda fp: pd.DataFrame(columns=list(h.sc.BRICK_COLUMNS)))
    gaps = {}
    monkeypatch.setattr(h.sc, "catalogue_gap", lambda ra, dec, bricks, fetch=None: gaps.get((round(ra, 4), round(dec, 4))))
    return gaps


def test_a_position_in_a_catalogue_gap_is_flagged_not_an_orphan(no_gap_world, monkeypatch):
    monkeypatch.setattr(h, "_tile", _rows((150.0 + 25 / 3600, 2.0, "EXP")))
    no_gap_world[(150.0, 2.0)] = "DR10 catalogue gap: BAILOUT"
    out = h.score_position("D", 150.0, 2.0)
    assert out["catalogue_gap"] == "DR10 catalogue gap: BAILOUT" and out["empty_15"] is True


def test_a_gap_check_that_fails_is_a_fetch_error(monkeypatch):
    monkeypatch.setattr(h, "_atlas", lambda fp: pd.DataFrame(columns=[*h.sc.COLUMNS, *h.sc.ATLAS_COLUMNS]))
    monkeypatch.setattr(h, "_bricks", lambda fp: pd.DataFrame({"brickname": ["x"], "ra1": [149.0], "ra2": [151.0],
                                                               "dec1": [1.0], "dec2": [3.0]}))
    monkeypatch.setattr(h, "_tile", _rows((150.0 + 3 / 3600, 2.0, "EXP")))

    def down(brick):
        raise h.sc.CatalogError("maskbits x: ReadTimeout")

    monkeypatch.setattr(h, "_maskbits", down)
    assert h.score_or_error("D", 150.0, 2.0) == {"error": "maskbits x: ReadTimeout", "tile_rows": 0}


def test_compact_host_fields_and_outcome_order(no_gap_world, monkeypatch):
    """A PSF 2" off with no star signature is the compact candidate; a PSF within 1" makes point_source first."""
    monkeypatch.setattr(h, "_tile", _rows((150.0 + 2 / 3600, 2.0, "PSF")))
    out = h.score_position("D", 150.0, 2.0, (150.0 + 2 / 3600, 2.0), 1.0)
    assert (out["truth"], out["compact_sep"], out["point_key"]) == ("host_is_point_source", 2.0, None)
    assert out["true_type"] == "PSF" and out["true_sep"] == 2.0
    assert h.outcome_with_compact(out, h.PRODUCTION_VARIANT, 4.0, 3.0) == "compact_correct"
    assert h.outcome_with_compact(out, h.PRODUCTION_VARIANT, 4.0, 1.5) == "orphan"
    monkeypatch.setattr(h, "_tile", _rows((150.0 + 0.5 / 3600, 2.0, "PSF"), (150.0 + 2 / 3600, 2.0, "PSF")))
    out = h.score_position("D", 150.0, 2.0)
    assert h.outcome_with_compact(out, h.PRODUCTION_VARIANT, 4.0, 3.0) == "point_source"


def test_an_atlas_galaxy_at_the_named_host_position_beats_its_point_like_nucleus():
    """SN 2022xjk / NGC 873: DR10 fits the nucleus as a PSF 0.18" from Sesame's position, the galaxy 1.15"."""
    cols = [*h.sc.COLUMNS, *h.sc.ATLAS_COLUMNS]
    rows = pd.DataFrame([{**{c: 0.0 for c in cols}, "ra": 150.0 + 0.18 / 3600, "dec": 2.0, "type": "PSF", "ref_cat": "", "ref_id": 0},
                         {**{c: 0.0 for c in cols}, "ra": 150.0 + 1.15 / 3600, "dec": 2.0, "type": "SER", "ref_cat": "L3", "ref_id": 1049583}])
    rows = h.sc.with_separation(rows, 150.0, 2.0)
    assert h.true_host(rows, 150.0, 2.0, 2.0) == ("sga1049583", "ok")


def test_report_header_names_the_version_and_flags_mixed_stages(tmp_path, monkeypatch):
    monkeypatch.setattr(h, "OUT_DIR", tmp_path)
    code = tmp_path / "code.py"
    code.write_text("v1")
    monkeypatch.setattr(h, "FINGERPRINTED", {"code.py": code})
    h.write_meta("known")
    h.write_meta("randoms")
    header = "\n".join(h.version_header())
    assert "MIXED VERSIONS" not in header and h.fingerprint()["code.py"] in header
    code.write_text("v2")  # the code changed after the known-host stage...
    h.write_meta("randoms")  # ...and only the random stage ran again
    assert "MIXED VERSIONS: scores 'known'" in "\n".join(h.version_header())


