# Bright SSO Monitor Setup

Daily systemd user timer that runs `antares_sso_monitor.py` and logs
brightness alerts.

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
- State file: `bright_sso_state.json` in the script's working directory.
