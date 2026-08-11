"""
Tests for the run ceilings: per-request timeout, retry sleep budget, derived run
deadline, and the CLI confirmation for long scans.

All offline. The deadline tests drive a fake clock rather than sleeping, so they
assert the break condition itself instead of racing real time.
"""

import sys
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import requests
from alerce.exceptions import APIError

from rubin_qa import retry_budget
from rubin_qa.client import _api_call, _force_session_timeout
from rubin_qa.config import (
    DEADLINE_FLOOR,
    DEADLINE_SLACK,
    DEFAULT_SECONDS_PER_OBJECT,
    RETRY_ATTEMPTS,
    RETRY_BUDGET_SECONDS,
    SECONDS_PER_OBJECT,
)
from rubin_qa.reporting import deadline_for, estimate_runtime


class FakeClock:
    """Monotonic stand-in whose time only moves when the test says so."""

    def __init__(self, step: float = 10.0):
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        return self.now

    def advance(self, dt: float | None = None) -> None:
        self.now += self.step if dt is None else dt


def _empty_alerce_data(*_args, **_kwargs) -> dict:
    empty = pd.DataFrame()
    return {"dets": empty, "ms": empty, "probs": empty, "fetch_errors": []}


def _empty_antares_data(*_args, **_kwargs) -> dict:
    empty = pd.DataFrame()
    return {"dets": empty, "ms": empty, "tags": [], "fetch_errors": []}


class TestEstimateRuntime:
    def test_scales_linearly_with_count(self):
        assert estimate_runtime(100, "ztf") == 100 * SECONDS_PER_OBJECT["ztf"]

    def test_per_survey_costs_differ(self):
        # LSST skips magstats client-side, so it must not be priced like ZTF.
        assert estimate_runtime(50, "lsst") < estimate_runtime(50, "ztf")

    def test_unknown_survey_assumes_slowest(self):
        assert estimate_runtime(10, "nonesuch") == 10 * DEFAULT_SECONDS_PER_OBJECT

    def test_zero_objects(self):
        assert estimate_runtime(0, "ztf") == 0


class TestDeadlineFor:
    def test_scales_with_the_job(self):
        # The point of deriving it: a big scan is entitled to a big deadline.
        assert deadline_for(1000, "ztf") == pytest.approx(
            DEADLINE_SLACK * estimate_runtime(1000, "ztf")
        )

    def test_floor_protects_short_runs(self):
        assert deadline_for(1, "antares") == DEADLINE_FLOOR

    def test_always_exceeds_its_own_estimate(self):
        # Otherwise a healthy run would trip its own deadline.
        for n in (1, 10, 100, 1000, 5000):
            for survey in ("ztf", "lsst", "antares"):
                assert deadline_for(n, survey) > estimate_runtime(n, survey)

    def test_monotonic_in_count(self):
        deadlines = [deadline_for(n, "ztf") for n in (1, 50, 500, 5000)]
        assert deadlines == sorted(deadlines)

    def test_a_five_hour_job_is_allowed_to_run(self):
        n = int(5 * 3600 / SECONDS_PER_OBJECT["ztf"])
        assert deadline_for(n, "ztf") > 5 * 3600


class TestRetryBudget:
    def test_reset_restores_default(self):
        retry_budget.consume(100.0)
        retry_budget.reset()
        assert retry_budget.remaining() == RETRY_BUDGET_SECONDS

    def test_consume_grants_and_decrements(self):
        retry_budget.reset(100.0)
        assert retry_budget.consume(30.0) == 30.0
        assert retry_budget.remaining() == 70.0

    def test_consume_clamps_to_what_is_left(self):
        retry_budget.reset(10.0)
        assert retry_budget.consume(45.0) == 10.0
        assert retry_budget.remaining() == 0.0

    def test_returns_none_once_spent(self):
        retry_budget.reset(5.0)
        retry_budget.consume(5.0)
        assert retry_budget.consume(1.0) is None

    def test_total_never_exceeds_budget(self):
        retry_budget.reset(50.0)
        granted = [retry_budget.consume(9.0 * 2 ** i) for i in range(20)]
        assert sum(g for g in granted if g is not None) == pytest.approx(50.0)

    def test_sleep_sleeps_only_the_granted_amount(self):
        retry_budget.reset(4.0)
        with patch("rubin_qa.retry_budget.time.sleep") as mock_sleep:
            granted = retry_budget.sleep(60.0)
        assert granted == 4.0
        mock_sleep.assert_called_once_with(4.0)

    def test_sleep_does_not_sleep_when_spent(self):
        retry_budget.reset(0.0)
        with patch("rubin_qa.retry_budget.time.sleep") as mock_sleep:
            assert retry_budget.sleep(30.0) is None
        mock_sleep.assert_not_called()


class TestBudgetBoundsClientRetries:
    """The budget is what stops per-object retry multiplying across a page."""

    def test_alerce_backoff_capped_by_budget(self):
        retry_budget.reset(10.0)
        fn = MagicMock(side_effect=APIError("504"))
        fn.__name__ = "query_detections"
        with patch("rubin_qa.client.time.sleep") as mock_sleep:
            for _ in range(5):
                _api_call(fn)
        total = sum(c.args[0] for c in mock_sleep.call_args_list)
        assert total == pytest.approx(10.0)

    def test_alerce_still_attempts_after_budget_spent(self):
        retry_budget.reset(0.0)
        fn = MagicMock(side_effect=APIError("504"))
        fn.__name__ = "query_detections"
        with patch("rubin_qa.client.time.sleep") as mock_sleep:
            result, err = _api_call(fn)
        mock_sleep.assert_not_called()
        assert result is None and "504" in err
        assert fn.call_count == 1  # attempted, just not retried

    def test_alerce_warns_when_budget_exhausted(self, capsys):
        retry_budget.reset(0.0)
        fn = MagicMock(side_effect=APIError("504"))
        fn.__name__ = "query_detections"
        with patch("rubin_qa.client.time.sleep"):
            _api_call(fn)
        assert "retry budget exhausted" in capsys.readouterr().err

    def test_alerce_retries_normally_with_budget(self):
        retry_budget.reset(RETRY_BUDGET_SECONDS)
        fn = MagicMock(side_effect=APIError("504"))
        fn.__name__ = "query_detections"
        with patch("rubin_qa.client.time.sleep") as mock_sleep:
            _api_call(fn)
        assert len(mock_sleep.call_args_list) == RETRY_ATTEMPTS - 1

    def test_malformed_request_is_not_retried(self):
        # alerce maps HTTP 400 to ParseError. Resending a malformed request can
        # never succeed, so it must not consume attempts or budget.
        from alerce.exceptions import ParseError

        retry_budget.reset(RETRY_BUDGET_SECONDS)
        fn = MagicMock(side_effect=ParseError("bad query", code=400))
        fn.__name__ = "query_objects"
        with patch("rubin_qa.client.time.sleep") as mock_sleep:
            result, err = _api_call(fn)
        mock_sleep.assert_not_called()
        assert fn.call_count == 1
        assert result is None and "bad_request" in err
        assert retry_budget.remaining() == RETRY_BUDGET_SECONDS

    def test_server_error_is_still_retried(self):
        # Guard the distinction: 5xx maps to APIError and must keep retrying.
        retry_budget.reset(RETRY_BUDGET_SECONDS)
        fn = MagicMock(side_effect=APIError("gateway timeout", code=504))
        fn.__name__ = "query_objects"
        with patch("rubin_qa.client.time.sleep") as mock_sleep:
            _api_call(fn)
        assert fn.call_count == RETRY_ATTEMPTS
        assert len(mock_sleep.call_args_list) == RETRY_ATTEMPTS - 1

    def test_antares_backoff_capped_by_budget(self):
        from rubin_qa.antares_client import _api_call as antares_api_call

        retry_budget.reset(12.0)
        fn = MagicMock(side_effect=requests.exceptions.ReadTimeout("timed out"))
        fn.__name__ = "get_by_id"
        with patch("rubin_qa.antares_client.time.sleep") as mock_sleep:
            for _ in range(5):
                antares_api_call(fn, "ANT1")
        total = sum(c.args[0] for c in mock_sleep.call_args_list)
        assert total == pytest.approx(12.0)

    def test_antares_does_not_retry_programming_errors(self):
        from rubin_qa.antares_client import _api_call as antares_api_call

        fn = MagicMock(side_effect=ValueError("bug, not a fault"))
        fn.__name__ = "get_by_id"
        with patch("rubin_qa.antares_client.time.sleep") as mock_sleep:
            result, err = antares_api_call(fn, "ANT1")
        mock_sleep.assert_not_called()
        assert fn.call_count == 1
        assert result is None and "bug" in err


class TestForceSessionTimeout:
    """The alerce package passes no timeout, so requests would block forever."""

    @staticmethod
    def _holder_with_session():
        session = requests.Session()
        session.request = MagicMock(return_value="resp")
        holder = MagicMock()
        holder.session = session
        # MagicMock's vars() has no nested holders to walk
        holder.__dict__ = {"session": session}
        return holder, session

    def test_injects_default_timeout(self):
        holder, session = self._holder_with_session()
        original = session.request
        assert _force_session_timeout(holder, timeout=42.0) == 1
        session.request("GET", "http://example.invalid")
        assert original.call_args.kwargs["timeout"] == 42.0

    def test_explicit_timeout_still_wins(self):
        holder, session = self._holder_with_session()
        original = session.request
        _force_session_timeout(holder, timeout=42.0)
        session.request("GET", "http://example.invalid", timeout=1.5)
        assert original.call_args.kwargs["timeout"] == 1.5

    def test_idempotent(self):
        holder, _ = self._holder_with_session()
        assert _force_session_timeout(holder, timeout=42.0) == 1
        assert _force_session_timeout(holder, timeout=42.0) == 0

    def test_live_client_sessions_are_all_patched(self):
        # Regression guard: the Alerce object holds one session per sub-client,
        # and patching only the top-level one leaves the query paths unprotected.
        import rubin_qa.client as client_module

        holders = [client_module._client] + [
            v for v in vars(client_module._client).values() if hasattr(v, "__dict__")
        ]
        sessions = [
            getattr(h, "session", None)
            for h in holders
            if isinstance(getattr(h, "session", None), requests.Session)
        ]
        assert len(sessions) >= 2
        assert all(getattr(s, "_rubin_qa_timeout", False) for s in sessions)


class TestRunDeadline:
    """A stalled upstream must cut the run short but still report what it got."""

    def test_alerce_run_stops_at_deadline(self):
        from rubin_qa import reporting

        clock = FakeClock(step=10.0)

        def slow_fetch(oid, survey="ztf"):
            clock.advance()
            return _empty_alerce_data()

        with patch.object(reporting.time, "monotonic", clock), \
             patch.object(reporting, "fetch_object_data", slow_fetch):
            df = reporting.run_pipeline(
                oids=[f"ZTF{i}" for i in range(50)],
                inter_object_delay=0,
                quiet=True,
                max_run_seconds=35.0,
            )
        assert 0 < len(df) < 50

    def test_partial_report_is_still_usable(self):
        from rubin_qa import reporting
        from rubin_qa.config import OUTPUT_COLUMNS

        clock = FakeClock(step=10.0)

        def slow_fetch(oid, survey="ztf"):
            clock.advance()
            return _empty_alerce_data()

        with patch.object(reporting.time, "monotonic", clock), \
             patch.object(reporting, "fetch_object_data", slow_fetch):
            df = reporting.run_pipeline(
                oids=[f"ZTF{i}" for i in range(50)],
                inter_object_delay=0,
                quiet=True,
                max_run_seconds=35.0,
            )
        # Non-empty means __main__ still writes a CSV rather than exiting 1.
        assert not df.empty
        assert list(df.columns) == OUTPUT_COLUMNS

    def test_alerce_warns_about_upstream(self, capsys):
        from rubin_qa import reporting

        clock = FakeClock(step=10.0)

        def slow_fetch(oid, survey="ztf"):
            clock.advance()
            return _empty_alerce_data()

        with patch.object(reporting.time, "monotonic", clock), \
             patch.object(reporting, "fetch_object_data", slow_fetch):
            reporting.run_pipeline(
                oids=[f"ZTF{i}" for i in range(50)],
                inter_object_delay=0,
                quiet=True,
                max_run_seconds=35.0,
            )
        err = capsys.readouterr().err
        assert "stalled" in err and "partial report" in err

    def test_zero_disables_the_deadline(self):
        from rubin_qa import reporting

        clock = FakeClock(step=10_000.0)

        def slow_fetch(oid, survey="ztf"):
            clock.advance()
            return _empty_alerce_data()

        with patch.object(reporting.time, "monotonic", clock), \
             patch.object(reporting, "fetch_object_data", slow_fetch):
            df = reporting.run_pipeline(
                oids=[f"ZTF{i}" for i in range(20)],
                inter_object_delay=0,
                quiet=True,
                max_run_seconds=0,
            )
        assert len(df) == 20

    def test_healthy_run_never_trips_its_derived_deadline(self):
        from rubin_qa import reporting

        # Each object costs exactly the measured per-object estimate.
        clock = FakeClock(step=SECONDS_PER_OBJECT["ztf"])

        def paced_fetch(oid, survey="ztf"):
            clock.advance()
            return _empty_alerce_data()

        with patch.object(reporting.time, "monotonic", clock), \
             patch.object(reporting, "fetch_object_data", paced_fetch):
            df = reporting.run_pipeline(
                oids=[f"ZTF{i}" for i in range(40)],
                inter_object_delay=0,
                quiet=True,   # max_run_seconds=None → derived
            )
        assert len(df) == 40

    def test_antares_run_stops_at_deadline(self):
        from rubin_qa import antares_client, reporting

        clock = FakeClock(step=10.0)

        def slow_locus(locus_id):
            clock.advance()
            return _empty_antares_data()

        with patch.object(reporting.time, "monotonic", clock), \
             patch.object(antares_client, "fetch_antares_locus", slow_locus):
            df = reporting.run_antares_pipeline(
                locus_ids=[f"ANT{i}" for i in range(50)],
                inter_object_delay=0,
                quiet=True,
                max_run_seconds=35.0,
            )
        assert 0 < len(df) < 50

    def test_antares_derives_deadline_from_deduplicated_count(self):
        from rubin_qa import antares_client, reporting

        # 4 loci → below the floor, so the derived deadline is DEADLINE_FLOOR.
        clock = FakeClock(step=DEADLINE_FLOOR / 2)

        def slow_locus(locus_id):
            clock.advance()
            return _empty_antares_data()

        with patch.object(reporting.time, "monotonic", clock), \
             patch.object(antares_client, "fetch_antares_locus", slow_locus):
            df = reporting.run_antares_pipeline(
                locus_ids=[f"ANT{i}" for i in range(4)],
                inter_object_delay=0,
                quiet=True,
            )
        assert 0 < len(df) < 4


class TestConfirmLongRun:
    """Must never block a systemd unit, which has no stdin."""

    @staticmethod
    def _tty(is_tty: bool):
        stdin = MagicMock()
        stdin.isatty.return_value = is_tty
        return patch.object(sys, "stdin", stdin)

    def test_short_run_does_not_prompt(self, capsys):
        from rubin_qa.__main__ import _confirm_long_run

        with self._tty(True), patch("builtins.input") as mock_input:
            _confirm_long_run("antares", ["20"], assume_yes=False)
        mock_input.assert_not_called()
        assert capsys.readouterr().err == ""

    def test_non_tty_proceeds_without_prompting(self, capsys):
        from rubin_qa.__main__ import _confirm_long_run

        with self._tty(False), patch("builtins.input") as mock_input:
            _confirm_long_run("ztf", ["1000"], assume_yes=False)
        mock_input.assert_not_called()
        assert "proceeding" in capsys.readouterr().err

    def test_closed_stdin_proceeds_instead_of_crashing(self, capsys):
        # sys.stdin is None when fd 0 is closed; must degrade to "proceed", not
        # AttributeError, or the run dies instead of skipping a prompt.
        from rubin_qa.__main__ import _confirm_long_run

        with patch.object(sys, "stdin", None):
            _confirm_long_run("ztf", ["1000"], assume_yes=False)
        assert "proceeding" in capsys.readouterr().err

    def test_assume_yes_skips_prompt(self):
        from rubin_qa.__main__ import _confirm_long_run

        with self._tty(True), patch("builtins.input") as mock_input:
            _confirm_long_run("ztf", ["1000"], assume_yes=True)
        mock_input.assert_not_called()

    def test_yes_proceeds(self):
        from rubin_qa.__main__ import _confirm_long_run

        with self._tty(True), patch("builtins.input", return_value="y"):
            _confirm_long_run("ztf", ["1000"], assume_yes=False)  # must not raise

    def test_no_aborts_with_exit_1(self):
        from rubin_qa.__main__ import _confirm_long_run

        with self._tty(True), patch("builtins.input", return_value="n"):
            with pytest.raises(SystemExit) as exc:
                _confirm_long_run("ztf", ["1000"], assume_yes=False)
        assert exc.value.code == 1

    def test_bare_enter_defaults_to_no(self):
        from rubin_qa.__main__ import _confirm_long_run

        with self._tty(True), patch("builtins.input", return_value=""):
            with pytest.raises(SystemExit):
                _confirm_long_run("ztf", ["1000"], assume_yes=False)

    def test_eof_aborts(self):
        from rubin_qa.__main__ import _confirm_long_run

        with self._tty(True), patch("builtins.input", side_effect=EOFError):
            with pytest.raises(SystemExit):
                _confirm_long_run("ztf", ["1000"], assume_yes=False)

    def test_explicit_ids_counted_not_treated_as_page_size(self):
        from rubin_qa.__main__ import _planned_count

        assert _planned_count(["ZTF1", "ZTF2", "ZTF3"]) == 3
        assert _planned_count(["250"]) == 250

    def test_prompt_reports_estimate_and_deadline(self):
        from rubin_qa.__main__ import _confirm_long_run

        with self._tty(True), patch("builtins.input", return_value="y") as mock_input:
            _confirm_long_run("ztf", ["1000"], assume_yes=False)
        prompt = mock_input.call_args.args[0]
        assert "min estimated" in prompt and "deadline" in prompt
