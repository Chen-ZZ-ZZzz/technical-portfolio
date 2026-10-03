"""
Paths resolve from the repo root, never the current directory (2026-10-02).

Systemd units and manual runs start in arbitrary directories. Every file the code reads
or writes must land in the same tree wherever a run starts, relative paths typed on the
command line included, and the code and the tools must agree on one root
(PROJECT_ROOT, which RUBIN_QA_ROOT overrides).
"""

import os
import pathlib
import subprocess
import sys

import pytest

from rubin_qa import config
from rubin_qa.config import PROJECT_ROOT, from_root

TOOLS = pathlib.Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))


def test_from_root_resolves_a_relative_path_against_the_repo_root_wherever_the_run_starts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert from_root("logs/replay_D.json") == PROJECT_ROOT / "logs" / "replay_D.json"
    assert from_root(tmp_path / "x.csv") == tmp_path / "x.csv"  # absolute: unchanged
    assert from_root("~/x.csv") == pathlib.Path.home() / "x.csv"


@pytest.mark.parametrize("tool,argv,target", [
    ("lsst_box_census", ["--boxes", "D", "--csv", "logs/census.csv"], "write_csv"),
    ("replay_tns_check", ["logs/replay_D.json"], "run"),
    ("sample_latency", ["--report", "--log", "state/samples.jsonl"], "report"),
])
def test_a_relative_path_on_the_command_line_lands_in_the_repo(tool, argv, target, tmp_path, monkeypatch):
    module = __import__(tool)
    seen = []
    monkeypatch.setattr(module, target, lambda *a, **k: seen.extend(a) or 0)
    monkeypatch.chdir(tmp_path)  # anywhere but the repo
    monkeypatch.setattr(sys, "argv", [f"{tool}.py", *argv])
    module.main()
    rel = argv[-1]
    assert PROJECT_ROOT / rel in seen and not (tmp_path / rel).exists()


def test_the_code_and_every_tool_share_one_root(tmp_path):
    """With RUBIN_QA_ROOT set, every data path follows it: no tool keeps a root of its own."""
    code = """
import sys
sys.path.insert(0, sys.argv[1])
import host_match_check as h, lsst_box_census as c, sample_latency as s, point_source_snr as p, replay_tns_check as r
from rubin_qa import config, replay, sky_catalog as sc, transient_monitor as m
for path in (h.OUT_DIR, h.SAMPLE_DIR, h.DES_HEAD, c.OUT_DIR, s.LOG_PATH, p.OUT, r.CANDIDATE_CACHE, r.DEFAULT_REPLAY,
             config.REPORTS_DIR, replay.CACHE_DIR, replay.REPORT_DIR, sc.CACHE_DIR, sc.MASKBITS_CACHE_DIR, m.STATE_DIR):
    print(path)
"""
    env = {**os.environ, "RUBIN_QA_ROOT": str(tmp_path)}
    out = subprocess.run([sys.executable, "-c", code, str(TOOLS)], env=env, capture_output=True, text=True, check=True)
    paths = [pathlib.Path(line) for line in out.stdout.splitlines()]
    assert len(paths) == 14
    assert all(path.is_relative_to(tmp_path) for path in paths), [str(p) for p in paths if not p.is_relative_to(tmp_path)]


def test_project_root_is_the_checkout_by_default():
    if os.environ.get("RUBIN_QA_ROOT"):
        pytest.skip("RUBIN_QA_ROOT overrides it here")
    assert config.PROJECT_ROOT == pathlib.Path(config.__file__).resolve().parents[2]
