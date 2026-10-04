"""
The stay_inside hook (.claude/hooks/stay_inside.py): Claude's file-writing tools
write only inside the project, the session scratchpad and the project's memory
directory (user, 2026-10-03). Run as Claude Code runs it: JSON on stdin, exit 2 =
blocked, exit 0 = allowed.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "stay_inside.py"


@pytest.fixture
def world(tmp_path):
    """A project, a scratchpad, a memory directory, and somewhere outside all three."""
    w = {name: tmp_path / name for name in ("project", "scratchpad", "claude_project", "outside")}
    for d in w.values():
        d.mkdir()
    (w["claude_project"] / "memory").mkdir()
    w["memory"] = w["claude_project"] / "memory"
    return w


def run(world, tool_input, tool_name="Write", raw=None):
    event = {"tool_name": tool_name, "tool_input": tool_input, "cwd": str(world["project"]),
             "scratchpad_dir": str(world["scratchpad"]),
             "transcript_path": str(world["claude_project"] / "session.jsonl")}
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(world["project"])}
    proc = subprocess.run([sys.executable, str(HOOK)], input=raw if raw is not None else json.dumps(event),
                          capture_output=True, text=True, env=env, timeout=30)
    return proc.returncode, proc.stderr


@pytest.mark.parametrize("where,rel", [
    ("project", "src/new_module.py"),  # a file that does not exist yet
    ("project", "."),
    ("scratchpad", "notes.txt"),
    ("memory", "some-memory.md"),
])
def test_inside_an_allowed_root_passes(world, where, rel):
    assert run(world, {"file_path": str(world[where] / rel), "content": "x"}) == (0, "")


def test_outside_is_blocked_with_the_reason(world):
    code, err = run(world, {"file_path": str(world["outside"] / "x.txt"), "content": "x"})
    assert code == 2 and "blocked" in err and "outside the project" in err


def test_the_rest_of_claudes_project_folder_is_not_memory(world):
    """Transcripts and tool results sit next to memory/: readable, never written."""
    assert run(world, {"file_path": str(world["claude_project"] / "session.jsonl")})[0] == 2


@pytest.mark.parametrize("make_path", [
    lambda w: f"{w['project']}/../outside/x.txt",           # a parent step out
    lambda w: f"{w['project']}/../project_sibling/x.txt",   # a name that only starts like the project
    lambda w: str(w["project"]) + "_evil/x.txt",
])
def test_paths_that_only_look_inside_are_blocked(world, make_path):
    assert run(world, {"file_path": make_path(world)})[0] == 2


def test_a_symlink_inside_the_project_cannot_lead_out(world):
    (world["project"] / "link").symlink_to(world["outside"], target_is_directory=True)
    assert run(world, {"file_path": str(world["project"] / "link" / "x.txt")})[0] == 2


def test_a_relative_path_is_taken_from_the_session_directory(world):
    assert run(world, {"file_path": "docs/a.md"})[0] == 0
    assert run(world, {"file_path": "../outside/a.md"})[0] == 2


def test_notebook_edit_is_checked_on_its_own_field(world):
    assert run(world, {"notebook_path": str(world["project"] / "a.ipynb")}, "NotebookEdit")[0] == 0
    assert run(world, {"notebook_path": str(world["outside"] / "a.ipynb")}, "NotebookEdit")[0] == 2


@pytest.mark.parametrize("tool_input,raw", [
    ({"content": "no path at all"}, None),
    (None, "not json"),
    (None, "[1, 2]"),
    ("a string, not an object", None),
])
def test_what_the_hook_cannot_check_is_blocked_never_waved_through(world, tool_input, raw):
    code, err = run(world, tool_input, raw=raw)
    assert code == 2 and "blocked" in err
