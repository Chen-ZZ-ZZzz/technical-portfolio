"""
The retired SSO monitor is frozen (user, 2026-10-03).

It is a self-built portfolio artifact, kept as it was, and it keeps its own ANTARES code
on purpose: a frozen copy cannot drift from the shared module, because it never changes.
This test makes "never changes" true by construction: it pins the SHA-256 of the
monitor's files, and of the one function it imports from the package, so a change by any
route fails the suite. Updating a recorded hash below is the deliberate unlock - the same
idea as the rule fingerprint in the point-source watch.
"""

import hashlib
import inspect
import pathlib

import pytest

from rubin_qa import config

ROOT = pathlib.Path(__file__).resolve().parents[1]
FROZEN_FILES = {
    "antares_sso_monitor.py": "91611a4922c83b547e444b37aa44ba5f84392119ab9f996ef30520716c562dee",
    "systemd/lsst-sso-monitor.service.example": "a611aefcd8cbfe47c6d7bdaf7f030c798bf8fed5b57292a99c1cd9fb00f4b56e",
    "systemd/lsst-sso-monitor.timer.example": "90aedcaff954ae5a0af607acb8fed143637525947537e334ef56178d547f33f6",
}
# antares_sso_monitor.py does `from rubin_qa.config import now_mjd`: a change there would
# change the frozen monitor while its own file stayed byte for byte the same
FROZEN_IMPORTS = {"rubin_qa.config.now_mjd": (config.now_mjd,
                                              "20b34a91150f9a411fe1382fb7f1464ec3d3e3d7e77884c685a9a67307be68bc")}
UNLOCK = "if the change is deliberate, record the new hash in tests/test_sso_frozen.py"


@pytest.mark.parametrize("rel", sorted(FROZEN_FILES))
def test_the_retired_sso_monitors_files_are_unchanged(rel):
    digest = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
    assert digest == FROZEN_FILES[rel], f"{rel} changed (sha256 {digest}): the SSO monitor is frozen; {UNLOCK}"


@pytest.mark.parametrize("name", sorted(FROZEN_IMPORTS))
def test_what_the_frozen_monitor_imports_is_unchanged(name):
    fn, recorded = FROZEN_IMPORTS[name]
    digest = hashlib.sha256(inspect.getsource(fn).encode()).hexdigest()
    assert digest == recorded, f"{name} changed (sha256 {digest}): the frozen SSO monitor imports it; {UNLOCK}"


def test_the_monitor_imports_nothing_else_from_the_package():
    """A new import would be a route the pins above do not cover."""
    lines = [l.strip() for l in (ROOT / "antares_sso_monitor.py").read_text().splitlines()]
    assert [l for l in lines if "rubin_qa" in l and ("import" in l)] == ["from rubin_qa.config import now_mjd"]
