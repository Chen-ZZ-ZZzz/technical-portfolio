"""
PreToolUse hook: Claude's file-writing tools write only inside the project, the
session's scratchpad and Claude's memory directory for this project (user,
2026-10-03: writing outside the project is prohibited; Claude's own tmp is the
exception, and its memory directory counts as its own storage).

Claude Code passes the tool call as JSON on stdin. Exit 2 blocks the call and
shows stderr to Claude as the reason; exit 0 lets it through. Paths are resolved
(symlinks, "..") before the check, so a link or a parent step cannot lead out.
Anything the hook cannot read is blocked: a guard that fails open is no guard.

Shell commands are not covered here; CLAUDE.md ("Claude's file access") says what is.
"""

import json
import os
import sys
from pathlib import Path

PATH_FIELDS = ("file_path", "notebook_path")  # Write and Edit; NotebookEdit


def allowed_roots(event: dict) -> list[Path]:
    """The project, this session's scratchpad, and this project's memory directory."""
    roots = [os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd")]
    roots.append(event.get("scratchpad_dir"))
    if event.get("transcript_path"):  # ~/.claude/projects/<project>/<session>.jsonl
        roots.append(str(Path(event["transcript_path"]).parent / "memory"))
    return [Path(r).resolve() for r in roots if r]


def blocked_reason(event: dict) -> str | None:
    """Why the call must not run, or None when every path it writes is inside an allowed root."""
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return "no tool input to check"
    paths = [tool_input[f] for f in PATH_FIELDS if isinstance(tool_input.get(f), str)]
    if not paths:
        return f"no path in {sorted(tool_input)} to check"
    roots = allowed_roots(event)
    if not roots:
        return "no project directory known"
    cwd = Path(event.get("cwd") or roots[0])
    for raw in paths:
        path = Path(raw).expanduser()
        path = (path if path.is_absolute() else cwd / path).resolve()
        if not any(path == root or root in path.parents for root in roots):
            return (f"{raw} resolves to {path}, outside the project, the scratchpad and the memory directory. "
                    "Writing outside the project is prohibited (user's rule); ask the user instead.")
    return None


def main() -> int:
    try:
        event = json.load(sys.stdin)
        reason = blocked_reason(event) if isinstance(event, dict) else "hook input is not a JSON object"
    except Exception as e:  # unreadable input: block, never guess
        reason = f"hook could not read its input ({type(e).__name__}: {e})"
    if reason:
        print(f"stay_inside hook: blocked: {reason}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
