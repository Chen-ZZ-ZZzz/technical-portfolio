
# Test Automation / QA Tools

This folder contains small HIL/SIL test simulations and QA scripts.

# Test Automation & QA Tools

A collection of scripts focused on log analysis, test output processing, and quality assurance tooling. Each script was written, reviewed, and improved with attention to robustness, correctness, and readable reporting.

---

## Skills Demonstrated

| Area | Details |
|------|---------|
| **Log analysis** | Regex-based pattern matching across multiple files; per-file and aggregate reporting |
| **Regex** | `re.compile`, walrus operator `:=` for match-and-bind, `\b` word boundaries |
| **Data aggregation** | `defaultdict(int)` for clean counter accumulation across files |
| **Robust I/O** | `errors='replace'` to handle corrupt or non-UTF-8 log files without crashing |
| **Pathlib** | `rglob('*.log')` for recursive file discovery; single file or directory input |
| **Formatted reporting** | Dynamic column width alignment; tabular summary with totals |
| **CLI conventions** | `sys.argv` for arguments; errors to `stderr`; clean exit codes |

---

## Example Scripts

### Log Scanner (`log_scan.py`)

Scans `.log` files under a directory (or a single file) for `ERROR` and `WARN` entries, printing a per-file breakdown and a totals row.

**Concepts practiced:** `re`, `pathlib.rglob`, `defaultdict`, walrus operator `:=`, dynamic column formatting, `file=sys.stderr`.

---

## Notes

- Scripts accept both a single file and a directory as input where applicable
- All output tables use dynamic column widths to stay readable regardless of path length
- Errors and warnings always go to `stderr`; clean report output to `stdout`
