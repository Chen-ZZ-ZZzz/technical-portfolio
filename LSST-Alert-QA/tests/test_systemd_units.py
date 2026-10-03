"""
The transient monitor's systemd unit (user, 2026-10-03): daily, Persistent=true, never --init.

The unit passes the monitor no arguments, so what it runs is whatever the CLI does with
none: these tests pin both sides, the example file and the code's defaults. A unit with
--init would turn a lost or misplaced state file back into a quiet first run, the case
"no state is an error" exists to catch; one with --dry-run would save nothing, so every
alert would come back every day.
"""

import shlex
from pathlib import Path

from rubin_qa import transient_monitor as mon

SYSTEMD = Path(__file__).resolve().parents[1] / "systemd"
SERVICE = SYSTEMD / "lsst-transient-monitor.service.example"
TIMER = SYSTEMD / "lsst-transient-monitor.timer"  # no paths, so no .example


def settings(path: Path, key: str) -> list[str]:
    return [line.split("=", 1)[1] for line in path.read_text().splitlines() if line.startswith(f"{key}=")]


def test_the_unit_runs_the_live_scan_with_no_arguments():
    (exec_start,) = settings(SERVICE, "ExecStart")
    assert shlex.split(exec_start)[1:] == ["run", "python", "-m", "rubin_qa.transient_monitor"]


def test_no_arguments_means_the_live_footprint_saved_never_init():
    a = mon._parse_args([])
    assert (a.name, a.cone) == (mon.LIVE_FOOTPRINT, mon.FOOTPRINTS[mon.LIVE_FOOTPRINT][0])
    assert (a.init, a.dry_run, a.replay) == (False, False, None)


def test_the_timer_makes_up_missed_runs():
    """The hour is the installer's choice; catching up after the machine was off is not."""
    assert settings(TIMER, "Persistent") == ["true"]


def test_the_service_logs_to_the_journal_and_cannot_hang_forever():
    assert settings(SERVICE, "StandardOutput") == settings(SERVICE, "StandardError") == []
    assert settings(SERVICE, "Environment") == ["PYTHONUNBUFFERED=1"]  # line by line, in order, not one block at the end
    assert settings(SERVICE, "TimeoutStartSec") == ["2h"]
