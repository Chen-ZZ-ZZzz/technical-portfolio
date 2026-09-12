"""
Tests for the latency sampling tooling in tools/.

These are the only code path in the repo whose output is *evidence* rather than
a report: the 2026-08/09 campaign set SECONDS_PER_OBJECT from what --report
printed, so a silent bug here corrupts a design decision rather than one run.
Everything covered below is pure data reduction — nothing here touches a broker.
"""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))

import sample_latency as sl  # noqa: E402


def _rec(ts="2026-08-11T16:00:00+0200", hour=16, ztf_raw=None, **extra):
    """A minimal sample record shaped like the ones the timer writes."""
    record = {
        "ts": ts, "hour": hour, "weekday": "Tue", "n_objects": len(ztf_raw or []),
        "source": "timer", "uptime_s": 90000.0,
        "per_object": {}, "constants": {"ztf": 3.7, "lsst": 2.4, "antares": 1.0},
    }
    if ztf_raw is not None:
        record["per_object"]["ztf"] = {
            "median": sorted(ztf_raw)[len(ztf_raw) // 2], "n_err": 0,
            "raw": ztf_raw, "with_delay": round(sorted(ztf_raw)[len(ztf_raw) // 2] + 0.5, 2),
        }
    record.update(extra)
    return record


def _write(tmp_path, records):
    path = tmp_path / "samples.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return path


class TestLoadSamples:
    def test_missing_file_is_not_an_error(self, tmp_path):
        assert sl.load_samples(tmp_path / "nope.jsonl") == []

    def test_blank_lines_are_skipped(self, tmp_path):
        path = tmp_path / "s.jsonl"
        path.write_text(json.dumps(_rec()) + "\n\n   \n")
        assert len(sl.load_samples(path)) == 1

    def test_malformed_line_is_skipped_not_fatal(self, tmp_path, capsys):
        """
        A run killed mid-write leaves a truncated line. Losing that one sample is
        fine; losing the campaign because --report raises is not.
        """
        path = tmp_path / "s.jsonl"
        path.write_text(json.dumps(_rec()) + '\n{"ts": "trunc\n' + json.dumps(_rec()) + "\n")
        records = sl.load_samples(path)
        assert len(records) == 2
        assert "not valid JSON" in capsys.readouterr().err


class TestSpread:
    def test_no_values(self):
        assert sl._spread([]) == "no data"

    def test_reports_n_median_and_range(self):
        out = sl._spread([1.0, 2.0, 3.0])
        assert "n= 3" in out and "median  2.00s" in out and "1.00-3.00s" in out

    def test_ratio_included(self):
        assert "4.0x spread" in sl._spread([1.0, 4.0])

    def test_zero_minimum_does_not_divide(self):
        out = sl._spread([0.0, 2.0])
        assert "spread" not in out and "0.00-2.00s" in out


class TestBootAdjacent:
    def test_missing_uptime_is_not_boot_adjacent(self):
        assert sl._is_boot_adjacent({"uptime_s": None}) is False
        assert sl._is_boot_adjacent({}) is False

    def test_below_window(self):
        assert sl._is_boot_adjacent({"uptime_s": sl.BOOT_WINDOW_SECONDS - 1}) is True

    def test_at_or_above_window(self):
        assert sl._is_boot_adjacent({"uptime_s": sl.BOOT_WINDOW_SECONDS}) is False


class TestSummaryLine:
    def test_renders_each_survey(self):
        line = sl.summary_line(_rec(ztf_raw=[3.0, 3.0, 3.0]))
        assert "ztf=3.50s" in line

    def test_failed_survey_reads_as_err(self):
        record = _rec()
        record["per_object"]["ztf"] = {"error": "ReadTimeout: boom"}
        assert "ztf=ERR" in sl.summary_line(record)

    def test_candidates_appended_and_nones_dropped(self):
        record = _rec(ztf_raw=[1.0], candidates={"ztf": 19.3, "lsst": None})
        line = sl.summary_line(record)
        assert "candidates[ztf=19.3]" in line and "lsst" not in line


class TestAppendSample:
    def test_creates_parent_and_returns_total(self, tmp_path):
        path = tmp_path / "deep" / "logs" / "s.jsonl"
        assert sl.append_sample(_rec(), path) == 1
        assert sl.append_sample(_rec(), path) == 2
        assert len(sl.load_samples(path)) == 2


class TestReportPooling:
    """
    --report must pool *every object timing* across runs, not one figure per run.
    Sample size otherwise tracks how often someone remembered to run the sampler,
    which is what the raw[] arrays exist to avoid.
    """

    def test_pools_objects_not_runs(self, tmp_path, capsys):
        path = _write(tmp_path, [_rec(ztf_raw=[1.0, 2.0, 3.0]), _rec(ztf_raw=[1.0, 2.0, 3.0])])
        sl.report(path, by_hour=False, exclude_boot=False)
        out = capsys.readouterr().out
        assert "2 samples" in out
        assert "n= 6" in out          # six objects, not two runs
        assert "median  2.50s" in out  # raw + INTER_OBJECT_DELAY

    def test_legacy_record_without_raw_falls_back_to_its_median(self, tmp_path, capsys):
        """
        The first record ever written predates the raw[] field. It still carries a
        per-run median, and dropping it would silently shorten the history.
        """
        legacy = _rec()
        legacy["per_object"]["ztf"] = {"median": 3.34, "n_err": 0, "with_delay": 3.84}
        path = _write(tmp_path, [legacy, _rec(ztf_raw=[1.0, 2.0, 3.0])])
        sl.report(path, by_hour=False, exclude_boot=False)
        out = capsys.readouterr().out
        assert "n= 4" in out           # 3 pooled objects + 1 salvaged median
        assert "3.84" in out           # the legacy figure is used as-is

    def test_exclude_boot_drops_catch_up_runs(self, tmp_path, capsys):
        records = [_rec(ztf_raw=[1.0]), _rec(ztf_raw=[9.0], uptime_s=12.0)]
        path = _write(tmp_path, records)

        sl.report(path, by_hour=False, exclude_boot=False)
        assert "n= 2" in capsys.readouterr().out

        sl.report(path, by_hour=False, exclude_boot=True)
        out = capsys.readouterr().out
        assert "1 sample" in out and "n= 1" in out
        assert "excluded" in out

    def test_all_boot_adjacent_reports_rather_than_crashing(self, tmp_path, capsys):
        path = _write(tmp_path, [_rec(ztf_raw=[1.0], uptime_s=5.0)])
        sl.report(path, by_hour=False, exclude_boot=True)
        assert "Every sample was boot-adjacent" in capsys.readouterr().out

    def test_failures_are_listed_and_excluded_from_timings(self, tmp_path, capsys):
        """A failure carries no timing — it must not silently vanish either."""
        failed = _rec(ts="2026-08-15T13:11:02+0200")
        failed["per_object"]["ztf"] = {"error": "ReadTimeout: read timeout=60.0"}
        path = _write(tmp_path, [_rec(ztf_raw=[1.0, 2.0]), failed])
        sl.report(path, by_hour=False, exclude_boot=False)
        out = capsys.readouterr().out
        assert "Failed fetches (1 across 2 runs)" in out
        assert "ReadTimeout" in out
        assert "n= 2" in out           # the failed run contributes no timings

    def test_no_failures_says_so(self, tmp_path, capsys):
        path = _write(tmp_path, [_rec(ztf_raw=[1.0])])
        sl.report(path, by_hour=False, exclude_boot=False)
        assert "No failed fetches recorded." in capsys.readouterr().out

    def test_empty_log_gives_guidance(self, tmp_path, capsys):
        sl.report(tmp_path / "none.jsonl", by_hour=False, exclude_boot=False)
        assert "No samples yet" in capsys.readouterr().out

    def test_hours_covered_lists_only_sampled_hours(self, tmp_path, capsys):
        path = _write(tmp_path, [_rec(hour=8, ztf_raw=[1.0]), _rec(hour=21, ztf_raw=[1.0])])
        sl.report(path, by_hour=False, exclude_boot=False)
        assert "hours covered: 08, 21" in capsys.readouterr().out

    def test_by_hour_buckets_each_hour_separately(self, tmp_path, capsys):
        path = _write(tmp_path, [
            _rec(hour=8, ztf_raw=[1.0, 1.0]), _rec(hour=21, ztf_raw=[3.0]),
        ])
        sl.report(path, by_hour=True, exclude_boot=False)
        out = capsys.readouterr().out
        assert "08:00" in out and "21:00" in out
        assert "(2 runs)" not in out   # one run per hour here

    def test_candidate_fetch_section_skips_missing_values(self, tmp_path, capsys):
        path = _write(tmp_path, [
            _rec(ztf_raw=[1.0], candidates={"ztf": 19.0, "lsst": None}, candidate_page_size=100),
            _rec(ztf_raw=[1.0], candidates={"ztf": 21.0, "lsst": 10.0}, candidate_page_size=100),
        ])
        sl.report(path, by_hour=False, exclude_boot=False)
        out = capsys.readouterr().out
        assert "Candidate fetch (page_size=100)" in out
        assert "median 20.00s" in out   # ztf: two values pooled
        assert "median 10.00s" in out   # lsst: the None dropped, not counted as 0


class TestSurveyListHasOneDefinition:
    """
    tools/ used to restate ("ztf", "lsst", "antares") as bench_latency.ALL_TARGETS,
    so a fourth broker had to be added in two files and a sampler that silently
    skipped it would still look healthy. Both tools now read config.SURVEYS.
    """

    def test_both_tools_use_the_config_constant(self):
        import bench_latency

        from rubin_qa.config import SURVEYS

        assert bench_latency.SURVEYS is SURVEYS
        assert sl.SURVEYS is SURVEYS

    def test_alerce_surveys_is_a_subset(self):
        """ALERCE_SURVEYS answers a different question and stays a literal — but
        it cannot name a survey the project does not support."""
        import bench_latency

        from rubin_qa.config import SURVEYS

        assert set(bench_latency.ALERCE_SURVEYS) < set(SURVEYS)

    def test_report_covers_every_survey(self, tmp_path, capsys):
        """A survey added to config must appear in --report without a second edit."""
        from rubin_qa.config import SURVEYS

        path = _write(tmp_path, [_rec(ztf_raw=[1.0])])
        sl.report(path, by_hour=False, exclude_boot=False)
        out = capsys.readouterr().out
        for survey in SURVEYS:
            assert survey in out
