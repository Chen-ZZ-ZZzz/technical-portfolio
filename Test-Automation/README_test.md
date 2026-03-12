
# Test Automation / QA Tools

This folder contains small HIL/SIL test simulations and QA scripts.

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
| **Subprocess & networking** | `subprocess.run` to invoke system `ping`; latency parsing from stdout |
| **Dataclasses** | `@dataclass` for structured per-host results instead of tuples or parallel lists |
| **CI integration** | `sys.exit(1)` on any failure so scripts signal correctly in pipelines |

---

## Example Scripts

### Log Scanner (`log_scan.py`)

Scans `.log` files under a directory (or a single file) for `ERROR` and `WARN` entries, printing a per-file breakdown and a totals row.

**Concepts practiced:** `re`, `pathlib.rglob`, `defaultdict`, walrus operator `:=`, dynamic column formatting, `file=sys.stderr`.

---

### Network Checker (`net_check.py`)

Pings a list of hosts (from a file or comma-separated CLI argument) and prints a `PASS`/`FAIL` report with latency. Exits with code `1` if any host is unreachable, making it CI-pipeline friendly.

**Concepts practiced:** `subprocess.run`, `@dataclass`, stdout parsing, flexible input (`file | inline list`), exit codes for CI integration.

---
