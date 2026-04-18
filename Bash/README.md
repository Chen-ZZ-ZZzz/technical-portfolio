# Bash Scripts

This folder contains Bash scripts for automation and system tasks. All scripts share a common skeleton (strict mode, opt-in trace, help block, `main "$@"`) and pass `shellcheck` cleanly.

---

## Skills Demonstrated

| Area | Details |
|------|---------|
| **Unofficial Strict Mode** | `set -euo pipefail` — exit on error, unset variables, and pipeline failures |
| **Debuggable scripts** | `set -o xtrace` gated on `$TRACE` env variable for opt-in debug mode |
| **Argument validation** | `[[ -z "${1-}" ]]` and `[[ $# -gt 0 ]]` for missing- and extra-argument guards; help flag matched with regex (`^-*h(elp)?$`) |
| **Safe defaults under `set -u`** | `${VAR-default}` to handle unset variables without aborting |
| **Portability** | `#!/usr/bin/env bash` shebang for cross-system compatibility |
| **Unix conventions** | Error messages to `stderr` (`>&2`); meaningful exit codes; usage text on `-h`/`--help` |
| **Functions and scoping** | `local` for function-scoped variables; reusable helpers (e.g. `require_cmd`) for preconditions |
| **Isolated `cd` with subshells** | `(cd dir; ...)` to run work in a directory without leaking `$PWD` to the parent |
| **Defensive command exit handling** | `if cmd=$(...)` to capture commands whose non-zero exit isn't a real error (e.g. `grep -c` returning 1 on zero matches) |
| **File operations** | `tar`, `mkdir -p`, `basename`, `dirname`, `date` for archiving and path handling |
| **Parameter expansion** | `${var##*.}` and `${var%.*}` for extension/basename splitting |
| **Safe iteration** | `find -print0` piped via process substitution into `while IFS= read -r -d ''` to handle filenames with spaces or special characters |
| **Static analysis** | All scripts pass `shellcheck` cleanly |

---

## Example Scripts

### Project Backup (`bak_pj.sh`)
Compresses a folder into a timestamped `.tar.gz` archive, stored in a sibling `*-backups` directory. Demonstrates path manipulation with `basename`/`dirname` and idempotent directory creation with `mkdir -p`.

---

### Count Errors (`count_errors.sh`)
Counts occurrences of `ERROR` in all `.log` files under a directory, with a per-file breakdown and a grand total. Demonstrates safe filename handling via `find -print0` + `read -r -d ''`, and defensive exit-code capture around `grep -c` (which returns 1 on zero matches and would otherwise abort under `set -e`).

---

### Portfolio Bootstrap (`portfolio_allinone.sh`)
Clones the portfolio fresh, installs dependencies via `uv`, and runs the full test suite across both projects (`health-checker` and `LSST-Alert-QA`). Demonstrates dependency precondition checks via a reusable `require_cmd` function, isolated subshell execution per project, and exit-code propagation suitable for CI.

---
