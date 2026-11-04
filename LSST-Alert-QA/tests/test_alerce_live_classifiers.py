"""
tools/alerce_live_classifiers.py: its judgement on fake rows, with no network.

A version on at least one of the newest objects is live; one seen only on older objects is
retired. The fetchers are patched where run() reads them (the tool's own module).
"""

import pathlib
import sys

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))

import alerce_live_classifiers as alc  # noqa: E402
from rubin_qa import classifier  # noqa: E402
from rubin_qa.reporting import build_qa_row  # noqa: E402
from tests.conftest import BHRF, class_rows, probs_frame  # noqa: E402

LIVE = ("model_a", "2.0")
OLD = ("model_a", "1.0")
LEVEL = ("model_a_top", "2.0")
LABEL = ("position_label", "1")
REGISTRY = dict(voters={"A": (LIVE,)}, retired=frozenset({OLD}), live_context=frozenset({LEVEL, LABEL}))


def rows(*pairs):
    return [{"classifier_name": n, "classifier_version": v, "class_name": "SN", "probability": 0.9, "ranking": 1}
            for n, v in pairs]


def test_a_version_on_one_newest_object_is_live():
    v = alc.judge({"1": rows(LIVE), "2": rows(LABEL), "3": rows(LABEL)}, **REGISTRY)
    assert (v.live[LIVE], v.changes) == (1, [])


def test_a_voter_on_none_of_the_newest_objects_is_reported_retired():
    v = alc.judge({"1": rows(LABEL)}, **REGISTRY)
    assert v.changes == ["move model_a@2.0 (A) from VOTERS to RETIRED: on none of the 1 newest objects "
                         "(or the sample missed it: the blind spot)"]


def test_an_unknown_pair_on_a_newest_object_is_to_be_placed():
    v = alc.judge({"1": rows(LIVE, ("model_b", "1.0"))}, **REGISTRY)
    assert len(v.changes) == 1 and v.changes[0].startswith("place model_b@1.0: live (on 1 of the 1 newest objects)")


def test_an_unknown_pair_seen_only_on_older_objects_is_retired():
    v = alc.judge({"1": rows(LIVE)}, elsewhere={("model_c", "0.9")}, **REGISTRY)
    assert v.changes == ["add model_c@0.9 to RETIRED: seen on older objects, on none of the 1 newest"]


def test_a_retired_version_back_on_a_newest_object_is_live_again():
    v = alc.judge({"1": rows(LIVE, OLD)}, **REGISTRY)
    assert v.changes == ["move model_a@1.0 from RETIRED to VOTERS: live again (on 1 of the 1 newest objects)"]


def test_live_levels_and_position_labels_are_no_finding():
    assert alc.judge({"1": rows(LIVE, LEVEL, LABEL)}, **REGISTRY).changes == []


def test_with_a_survey_unsampled_missing_voters_are_not_judged():
    v = alc.judge({"1": rows(LABEL)}, every_survey=False, **REGISTRY)
    assert v.changes == [] and v.notes


def test_todays_live_versions_need_no_change_in_the_real_registry():
    """The newest objects of 2026-11-03 carried every voter, the live levels and the label (README)."""
    live = [p for pairs in classifier.VOTERS.values() for p in pairs]
    live += sorted(classifier.HIERARCHY_LEVELS | classifier.POSITION_LABELS)
    assert alc.judge({"1": rows(*live)}).changes == []


def test_integer_versions_match_the_registry():
    """ALeRCE LSST sends versions as integers."""
    v = alc.judge({"1": rows(("stamp_classifier_rubin_beta_20260421", 202))}, every_survey=False)
    assert (dict(v.live), v.changes) == ({("stamp_classifier_rubin_beta_20260421", "202"): 1}, [])


def test_unknown_pairs_are_read_from_a_reports_flags():
    flags = ["completeness: ndet_lt_2; unknown_classifier: model_b@1.0; insufficient_classifiers",
             "unknown_classifier: LC_classifier_ATAT_forced_phot(beta)@1.1.0", None, "genuine split across 2 classes"]
    assert alc.unknown_pairs(flags) == {("model_b", "1.0"), ("LC_classifier_ATAT_forced_phot(beta)", "1.1.0")}


def test_a_saved_report_with_unflagged_rows_reads_back(tmp_path):
    """
    The tool reads a report with pd.read_csv, where a row without a flag is NaN, not None
    (2026-11-04: every report with an unflagged row raised TypeError).
    """
    path = tmp_path / "qa_alerce_ztf_20261104_074952_n2.csv"
    pd.DataFrame({"oid": ["ZTF1", "ZTF2"],
                  "flag": ["unknown_classifier: model_b@1.0; insufficient_classifiers", None]}).to_csv(path, index=False)
    flags = pd.read_csv(path)["flag"]
    assert flags.isna().sum() == 1  # the empty cell is NaN on read-back
    assert alc.unknown_pairs(flags) == {("model_b", "1.0")}


def test_the_token_build_qa_row_writes_reads_back(data_complete):
    new_atat = ("LC_classifier_ATAT_forced_phot(beta)", "1.1.0")
    probs = probs_frame(class_rows(BHRF, "SN", 0.9), class_rows(new_atat, "SN", 0.9))
    row = build_qa_row("1", {**data_complete, "probs": probs}, [], classifier.classify_object(probs, 10))
    assert alc.unknown_pairs([row["flag"]]) == {new_atat}


def test_run_exits_1_and_names_the_change_when_the_registry_needs_one(monkeypatch, capsys):
    monkeypatch.setattr(alc, "newest_objects", lambda survey, n, min_ndet, days: (["1"], None))
    monkeypatch.setattr(alc, "probabilities", lambda oid, survey: (rows(("brand_new", "1")), None))
    assert alc.run(["ztf"], 20, 8, 30.0, None) == 1
    assert "place brand_new@1" in capsys.readouterr().out


def test_run_exits_0_when_the_registry_matches(monkeypatch, capsys):
    live = [p for pairs in classifier.VOTERS.values() for p in pairs] + sorted(classifier.POSITION_LABELS)
    monkeypatch.setattr(alc, "newest_objects", lambda survey, n, min_ndet, days: ([survey], None))
    monkeypatch.setattr(alc, "probabilities", lambda oid, survey: (rows(*live), None))
    assert alc.run(list(alc.SURVEYS), 20, 8, 30.0, None) == 0
    assert "The registry matches: no change." in capsys.readouterr().out


def test_a_failed_listing_is_an_error_and_exit_1(monkeypatch, capsys):
    monkeypatch.setattr(alc, "newest_objects", lambda survey, n, min_ndet, days: ([], "timeout"))
    assert alc.run(["ztf"], 20, 8, 30.0, None) == 1
    assert "ERROR: ztf newest objects: timeout" in capsys.readouterr().err
