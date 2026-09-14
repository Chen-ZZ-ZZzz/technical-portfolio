## Naming convention

Every unit is `lsst-<role>`, hyphenated, and its `SyslogIdentifier` is the same string
as its filename stem — `lsst-latency-sample.service` logs under `lsst-latency-sample`.
Two things fall out of that: `systemctl --user list-timers 'lsst-*'` and
`journalctl -t 'lsst-*'` each sweep the whole set, and a tag seen in the journal names
the unit to inspect without a lookup table.

| unit | identifier | status |
|---|---|---|
| `lsst-pipeline-alerce.{service,timer}` | `lsst-pipeline-alerce` | active |
| `lsst-pipeline-antares.{service,timer}` | `lsst-pipeline-antares` | active |
| `lsst-sso-monitor.{service,timer}` | `lsst-sso-monitor` | **retired 2026-08-18** |
| `lsst-latency-sample.{service,timer}` | `lsst-latency-sample` | **retired 2026-09-01** |

The two retired units answered their question and were disabled; only the two
pipeline timers still fire. Their examples and setup sections are kept below as the
record of how each was deployed and why it was stopped — treat them as history, not
as something to enable. Both retirements are covered in the project README: the SSO
monitor under "Bright Solar System Objects (SSO) Monitor", the sampler under
"Broker Latency Sampling Campaign".

Hyphens are safe here. The character is only special in path-derived units (`.mount`,
`.automount`, `.swap`, where it encodes `/`) and in template instance names — none of
which apply to plain `.service`/`.timer` units.

---

# Logging

Units log to the journal under their `SyslogIdentifier` and set no
`StandardOutput=`/`StandardError=`. A file redirect duplicates output that is already
in the journal and hides the runs that produce none — a job that dies before it can
write (`nm-online` timing out, `uv` failing, a bad import, a full disk) leaves an
empty log and no other trace.

`lsst-sso-monitor` was the deliberate exception while it ran: its
`logs/sso_monitor.log` was read directly as the record of what the monitor saw each
night, independent of journal retention. That log is now a closed record — it is the
source the 2026-08-17 audit reconstructed its test population from, since the loci
that alerted were absent from the state file.

---

# Sandboxing

Every `.service` here carries the same hardening block. The units are unprivileged
user services that do one thing — talk HTTPS to a public broker and write files under
the project directory — so the block is deliberately identical across them rather
than tuned per unit; copy it verbatim into any new unit.

What it buys, in the order it appears:

| directive | effect |
|---|---|
| `NoNewPrivileges` | no setuid/setgid escalation from anything the run execs |
| `ProtectSystem=strict` | the entire filesystem is read-only except `ReadWritePaths` |
| `ReadWritePaths` | the one hole: the project directory, for `logs/` and `reports/` |
| `PrivateTmp` | private `/tmp`, `/var/tmp` |
| `PrivateDevices` | no physical device nodes |
| `RestrictAddressFamilies` | IPv4/IPv6/unix only — no raw packet or netlink sockets |
| `ProtectHostname` | cannot change the system hostname |
| `RestrictNamespaces` | no namespace creation |
| `RestrictSUIDSGID` | cannot create setuid/setgid files |
| `RestrictRealtime` | no realtime scheduling |
| `MemoryDenyWriteExecute` | no W+X mappings |
| `LockPersonality` | no `personality()` changes |
| `SystemCallFilter` | `@system-service` minus `@resources`, `@privileged`, `@keyring` |
| `SystemCallErrorNumber=EPERM` | a blocked syscall fails with `EPERM` rather than `SIGSYS` |

Things that catch people out:

- `ProtectSystem=strict` makes **everything** read-only, so `ReadWritePaths` is not
  optional — set it to your real project path when you edit the example. Anything the
  job writes must live under it: the CSV in `reports/`, the JSONL in `logs/`,
  `logs/bright_sso_state.json`, and any `StandardOutput=append:` target.
- `$HOME` stays writable (`ProtectHome` is deliberately **not** set) because `uv`
  needs its cache at `~/.cache/uv` and its interpreters at `~/.local/share/uv`.
  Adding `ProtectHome=yes` breaks the units with a confusing cache error.
- `%h/.local/bin/uv` in the ANTARES example is a systemd specifier for the user's
  home, so that `ExecStart` needs no editing if `uv` is on the usual path. The other
  examples spell out `/path/to/uv`; either form works.
- A syscall denial shows up as `EPERM` from an ordinary library call, not a crash —
  check `journalctl --user -u <unit> -n 50` if a run starts failing right after a
  dependency upgrade, and verify with `systemd-analyze --user security <unit>`.

---

# Bright SSO Monitor Setup — retired 2026-08-18

> **Retired.** The unit ran daily from 2026-04-13 to 2026-08-17 (115 scans) and is
> disabled. Its purpose is fulfilled: the monitor's premise was falsified rather than
> retuned, and the audit that did so is the deliverable. Nothing further is learned by
> continuing to run it, and the alerts it produced are known false positives. The
> section below is kept as the deployment record. See "Bright Solar System Objects
> (SSO) Monitor" in the project README for the finding.

Implementation of daily systemd user timer that ran `antares_sso_monitor.py` and logged brightness alerts.

## Files

- `lsst-sso-monitor.service.example`
- `lsst-sso-monitor.timer.example`

## Install

```bash
# copy both files; drop `.example` suffix; edit paths.
systemctl --user daemon-reload
systemctl --user enable --now lsst-sso-monitor.timer
```

## Verify

```bash
systemctl --user list-timers lsst-sso-monitor.timer
systemctl --user status lsst-sso-monitor.service
journalctl --user -u lsst-sso-monitor.service -n 50
```

## Updating
- Changed the Python script :: nothing to do; next timer fire picks it up.
- Changed `.service` or `.timer` :: `systemctl --user daemon-reload`, then `systemctl --user restart lsst-sso-monitor.timer`.

## Notes

- `ExecStart` must be an absolute path. `~` is not expanded by systemd.
- `status=203/EXEC` in journalctl = executable not found or not executable (check `chmod +x` and the shebang).
- `Persistent=true` runs a missed job on next boot.
- `ExecStartPre=/bin/sleep 60` lets the network settle before the ANTARES query.
- State file: `logs/bright_sso_state.json`, resolved against the script's own directory (not the CWD).
- **The monitor is a documented negative result** — an ANTARES locus is a sky position, not an object, so it cannot track a mover, and every alert it raised was a stationary variable star or galaxy. See "Bright Solar System Objects (SSO) Monitor" in the project README before acting on anything this unit logged. It was run as an experiment, never as a detector, and the experiment is over.
- `pipeline.py` confirms before long scans, but only on a TTY. Under systemd there is no stdin, so it logs the estimate to stderr and proceeds — the unit will not hang waiting for input. Pass `-y` if you want the prompt skipped when running the same command by hand.
- Each run carries a deadline of 6× its own estimated duration. A run that stops early logs `WARN: ... Upstream or network is stalled` and still writes a partial CSV, so a truncated report in the log means the broker was unwell, not that the job was misconfigured.

---

# Broker Latency Sampling Setup — retired 2026-09-01

> **Retired.** The timer ran from 2026-08-11 to 2026-09-01 and is disabled. Its
> purpose is fulfilled: the campaign settled the `SECONDS_PER_OBJECT` constants
> (each sits at or above its survey's p95, so no change was needed) and showed the
> 17.0s ZTF figure that prompted the sampling to have been an episode, not a
> baseline. Latency proved flat by hour and by weekday, so further hourly sampling
> buys nothing. The section below is kept as the deployment record; re-enable the
> pair only to re-measure after a broker change, and expect to run it for weeks
> rather than days. See "Broker Latency Sampling Campaign" in the project README for
> the numbers.

Hourly systemd user timer that ran `tools/sample_latency.py` and appended one record
to `logs/latency_samples.jsonl`. Fed the `SECONDS_PER_OBJECT` constants in
`config.py`, which drive the runtime estimate, the run deadline, and the long-run
confirm prompt.

`tools/sample_latency.py` itself is **not** retired — `--report` still reads the
collected `logs/latency_samples.jsonl`, and a manual run still appends to it.

## Files

- `lsst-latency-sample.service.example`
- `lsst-latency-sample.timer.example`

## Install

```bash
# copy both files; drop `.example` suffix; edit paths.
systemctl --user daemon-reload
systemctl --user enable --now lsst-latency-sample.timer
```

## Verify

```bash
systemctl --user list-timers lsst-latency-sample.timer
journalctl --user -u lsst-latency-sample.service -n 20
uv run tools/sample_latency.py --report --by-hour
```

## Notes

- `OnCalendar=hourly` + `RandomizedDelaySec=3600` puts one sample somewhere inside
  each hour, so all 24 hour-buckets fill in a single day. The randomisation is the
  point — a fixed offset samples the same minute of every hour and cannot separate
  time-of-day from load.
- `Persistent=true`: catches up a run missed while the machine was off. The counter-
  argument is that a catch-up fires at boot rather than at a random hour — but that
  only matters on a machine with a regular boot schedule, and with `Persistent=false`
  the hours the machine is habitually off are never sampled at all. Missing whole
  hour-ranges distorts the picture more than mild clustering does.
- Every record carries `uptime_s` and `source` (`timer` when systemd set
  `INVOCATION_ID`, else `manual`), so catch-up runs stay identifiable. `--report`
  counts boot-adjacent samples (<600s uptime) and `--report --exclude-boot` drops
  them — the bias is filtered at analysis time instead of prevented in the unit.
- The service runs `--quick`, skipping the `page_size=100` candidate fetch. That is
  the heaviest query in the sample and does not need hourly cadence; take it on
  occasional manual runs instead. Note `--quick` is not free — the per-object pass
  still issues a small `query_objects` per ALeRCE survey.
- `--quiet` suppresses the full JSON printout, leaving one summary line per run.
  Nothing is lost: `sample_latency.py` writes the record itself, straight to the
  JSONL log, and stdout only ever carried a digest of it.
- The unit sets no `StandardOutput=`/`StandardError=`, so both go to the journal
  under `SyslogIdentifier=lsst-latency-sample`. Redirecting them to a file duplicates the
  summary line and buries the one case that is not in the JSONL at all: a run that
  dies before it can append (`nm-online` timing out, `uv` failing, a bad import, a
  full disk). Those leave no record for `--report` to count, so the journal — with
  exit status and unit context attached — is the only place they surface.
- A broker failure is recorded as a sample rather than crashing the unit — those
  records are the most valuable ones, and `--report` lists them separately since
  they carry no timing. Confirmed working: a ZTF `ReadTimeout` at the 60s
  `REQUEST_TIMEOUT` was captured on 2026-08-11 at 22:50.
- Load: roughly 40 HTTP calls per run against public broker APIs. At hourly that is
  ~3× what the existing daily pipeline already costs them. Prefer hourly over 30 min,
  and lower `--objects` before raising the frequency.
- `logs/` is gitignored, so samples are local-only and survive neither a clone nor a
  wipe of that directory.

---

# Automated ALeRcE LSST Pipeline Setup

Weekly systemd user timer that runs `pipeline.py` on ALeRcE broker.

## Files

- `lsst-pipeline-alerce.service.example`
- `lsst-pipeline-alerce.timer.example`

## Install

```bash
# copy both files; drop `.example` suffix; edit paths and/or page numbers.
systemctl --user daemon-reload
systemctl --user enable --now lsst-pipeline-alerce.timer
```

## Verify

```bash
systemctl --user list-timers lsst-pipeline-alerce.timer
systemctl --user status lsst-pipeline-alerce.service
```

---

# Automated ANTARES Pipeline Setup

Daily systemd user timer that runs `pipeline.py antares`.

## Files

- `lsst-pipeline-antares.service.example`
- `lsst-pipeline-antares.timer.example`

## Install

```bash
# copy both files; drop `.example` suffix; edit paths and/or page numbers.
systemctl --user daemon-reload
systemctl --user enable --now lsst-pipeline-antares.timer
```

## Verify

```bash
systemctl --user list-timers lsst-pipeline-antares.timer
systemctl --user status lsst-pipeline-antares.service
journalctl --user -u lsst-pipeline-antares.service -n 50
```

## Notes

- The unit asks for 256 locus IDs and typically gets 217-228 unique ones back:
  ANTARES' random-ordered query repeats loci across pages, costing 10-15%. One row is
  emitted per deduplicated ID, so a short report means fewer unique IDs, never
  dropped rows.
- `-q` trims stdout to the summary table. The CSV in `reports/` is unaffected.
- Output goes to the journal under `SyslogIdentifier=lsst-pipeline-antares` — see
  "Logging" above.
- `ExecStartPre=/usr/bin/nm-online -q -t 36` waits up to 36s for the network. On a
  machine without NetworkManager, swap it for `/bin/sleep 60` as the SSO monitor
  does, or drop it if the timer never fires near boot.
- A non-existent `ANT...` locus ID answers **500, not 404**, so it burns the full
  retry backoff before being given up on. `ZTF...` IDs return a clean not-found.
