# Health Checker -- Network & Log Monitoring Tools

CLI-Werkzeuge zur Netzwerk- und Log-Überwachung für Labor- und CI-Umgebungen.

Python/Bash-based log and network health check toolkit (TCP checks, log parsing, summary reports).

---

## Context

Build practical CLI tools for checking host reachability and scanning log files for errors. Outputs to terminal, JSON, or CSV. Reports saved with timestamped filenames.

---

## Setup

Python 3.13, no external dependencies. pytest for tests.

```
python3 net_check.py hosts.txt -p 22 443
python3 log_scan.py /var/log/
./run_checks.sh hosts.txt /var/log/ -s
python3 -m pytest tests/ -v
```

```
common.py        -- shared: colors, timestamps, report saving, sudo chown
net_check.py     -- host ping + TCP port checks
log_scan.py      -- log file scanning with timestamp extraction
run_checks.sh    -- wrapper to run both tools
hosts.txt        -- sample hosts file
tests/           -- 26 pytest tests, mock data only
```

---

## Skills Learned

| Area | Details |
|------|---------|
| **CLI design** | `argparse` with short/long flags, `--output` format selection, `--save` for persistent reports |
| **Networking** | ICMP ping via `subprocess`, TCP port checks via `socket.create_connection` with latency measurement |
| **Log analysis** | Regex-based level matching, timestamp extraction across 5 formats (ISO, German locale DD.MM.YYYY, syslog BSD, high-precision rsyslog) |
| **Structured output** | JSON and CSV formatters, CI-friendly exit codes (`sys.exit(1)` on failure) |
| **Terminal UX** | ANSI colors with auto-disable when piped; pad-then-color pattern to fix column alignment |
| **Shared code** | `common.py` to DRY colors, timestamps, report saving across both tools |
| **sudo awareness** | `SUDO_UID`/`SUDO_GID` detection to chown reports back to the real user |
| **Error handling** | Permission errors, unreadable files, missing commands -- graceful fallbacks throughout |
| **Testing** | pytest with `monkeypatch`, `tmp_path`, `parametrize`; mocked subprocess and socket |
