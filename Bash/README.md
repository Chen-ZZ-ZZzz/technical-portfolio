
# Bash Scripts

This folder contains Bash scripts for automation and system tasks.

---

## Skills Demonstrated

| Area | Details |
|------|---------|
| **Error handling** | `set -euo pipefail` for strict failure modes; exit on unset variables or pipe failures |
| **Argument validation** | `${1:?message}` for compact missing-argument guards |
| **Portability** | `#!/usr/bin/env bash` shebang for cross-system compatibility |
| **Unix conventions** | Error messages to `stderr` (`>&2`); meaningful exit codes |
| **File operations** | `tar`, `mkdir -p`, `basename`, `dirname`, `date` for archiving and path handling |
| **Safe iteration** | `find -print0` + `read -r -d ''` to handle filenames with spaces or special characters |

---

## Example Scripts

### Project Backup (`bak_pj.sh`)

Compresses a folder into a timestamped `.tar.gz` archive, stored in a sibling `*-backups` directory.

**Concepts practiced:** argument validation, `tar`, `date`, `mkdir -p`, stderr vs stdout, portable shebang.

---

### Count Errors (`count_errors.sh`)

Counts occurrences of `ERROR` in all `.log` files under a directory, with a per-file breakdown and a grand total.

**Concepts practiced:** `find -print0` / `read -r -d ''` for safe filename handling, `grep -c`, process substitution `< <(...)`, Bash arithmetic.

---
