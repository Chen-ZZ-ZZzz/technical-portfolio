
# Test Automation / QA Tools

This folder contains small HIL/SIL test simulations and QA scripts.

log_scan.py and net_check.py are the original scripts for the Health Checker project.

---

## Example Scripts

### Log Scanner (`log_scan.py`)

Scans `.log` files under a directory (or a single file) for `ERROR` and `WARN` entries, printing a per-file breakdown and a totals row.

---

### Network Checker (`net_check.py`)

Pings a list of hosts (from a file or comma-separated CLI argument) and prints a `PASS`/`FAIL` report with latency. Exits with code `1` if any host is unreachable, making it CI-pipeline friendly.

---
