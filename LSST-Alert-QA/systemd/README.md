# Bright SSO Monitor Setup

Implementation of daily systemd user timer that runs `antares_sso_monitor.py` and logs brightness alerts.

## Files

- `sso-monitor.service.example`
- `sso-monitor.timer.example`

## Install

```bash
# copy both files; drop `.example` suffix; edit paths.
systemctl --user daemon-reload
systemctl --user enable --now sso-monitor.timer
```

## Verify

```bash
systemctl --user list-timers sso-monitor.timer
systemctl --user status sso-monitor.service
journalctl --user -u sso-monitor.service -n 50
```

## Updating
- Changed the Python script :: nothing to do; next timer fire picks it up.
- Changed `.service` or `.timer` :: `systemctl --user daemon-reload`, then `systemctl --user restart sso-monitor.timer`.

## Notes

- `ExecStart` must be an absolute path. `~` is not expanded by systemd.
- `status=203/EXEC` in journalctl = executable not found or not executable (check `chmod +x` and the shebang).
- `Persistent=true` runs a missed job on next boot.
- `ExecStartPre=/bin/sleep 60` lets the network settle before the ANTARES query.
- State file: `logs/bright_sso_state.json`, resolved against the script's own directory (not the CWD).
- `pipeline.py` confirms before long scans, but only on a TTY. Under systemd there is no stdin, so it logs the estimate to stderr and proceeds — the unit will not hang waiting for input. Pass `-y` if you want the prompt skipped when running the same command by hand.
- Each run carries a deadline of 3× its own estimated duration. A run that stops early logs `WARN: ... Upstream or network is stalled` and still writes a partial CSV, so a truncated report in the log means the broker was unwell, not that the job was misconfigured.

---

# Automated ALeRcE LSST Pipeline Setup

Weekly systemd user timer that runs `pipeline.py` on ALeRcE broker.

## Files

- `lsst-pipeline.service.example`
- `lsst-pipeline.timer.example`

## Install

```bash
# copy both files; drop `.example` suffix; edit paths and/or page numbers.
systemctl --user daemon-reload
systemctl --user enable --now lsst-pipeline.timer
```

## Verify

```bash
systemctl --user list-timers lsst-pipeline.timer
systemctl --user status lsst-pipeline.service
```
