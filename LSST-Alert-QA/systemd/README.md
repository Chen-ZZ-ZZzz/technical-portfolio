## Naming convention

Every unit is `lsst-<role>`, hyphenated, and its `SyslogIdentifier` is the same string
as its filename stem — `lsst-latency-sample.service` logs under `lsst-latency-sample`.
Two things fall out of that: `systemctl --user list-timers 'lsst-*'` and
`journalctl -t 'lsst-*'` each sweep the whole set, and a tag seen in the journal names
the unit to inspect without a lookup table.

| unit (`.service` + `.timer`) | cadence | runs | status |
|---|---|---|---|
| `lsst-pipeline-alerce` | weekly | `pipeline.py lsst 256 -q` | active |
| `lsst-pipeline-antares` | daily | `pipeline.py antares 256 -q` | active |
| `lsst-transient-monitor` | daily, 16:00 (your choice) | `python -m rubin_qa.transient_monitor` | active since 2026-10-03 |
| `lsst-sso-monitor` | daily | `antares_sso_monitor.py` | **frozen**; stopped 2026-08-18 |
| `lsst-latency-sample` | hourly, randomized | `tools/sample_latency.py --quiet --quick` | **dormant** since 2026-09-01 |

Each unit is in one of three states:

| state | meaning |
|---|---|
| active | armed and running |
| dormant | not armed, but maintained and ready to re-arm: its code may change and must keep working, so its tests stay in the suite and its `.example` keeps up with the active units |
| frozen | not armed and never edited: its files are SHA-pinned (`tests/test_sso_frozen.py`); its setup section below is the deployment record |

Both stopped units answered their question. Why each was stopped: the project README,
"Bright Solar System Objects (SSO) Monitor" and "Broker Latency Sampling Campaign".

Hyphens are safe here. The character is only special in path-derived units (`.mount`,
`.automount`, `.swap`, where it encodes `/`) and in template instance names — none of
which apply to plain `.service`/`.timer` units.

---

# Running the units

- The long-run confirm prompt only fires on a terminal. Under systemd there is no
  stdin, so a run logs its estimate to stderr and proceeds rather than hanging the
  unit; `-y` skips the prompt for the same command run by hand.
- A pipeline run that hits its deadline logs `WARN: ... Upstream or network is
  stalled` and still writes a partial CSV: a truncated report means the broker was
  unwell, not that the unit is misconfigured.
- After editing an installed unit, run `systemctl --user daemon-reload`: until then
  systemd keeps the definition it loaded, and a machine that suspends rather than
  reboots keeps it for days.

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

Every `.service` here carries the same hardening block, apart from `ReadWritePaths`.
The units are unprivileged user services that do one thing — talk HTTPS to a public
broker and write files under the project directory — so the rest of the block is
deliberately identical across them; copy it verbatim into any new unit. Only
`ReadWritePaths` is tailored, to the directories that unit's run actually writes
(2026-10-04, each unit tested with a live run).

What it buys, in the order it appears:

| directive | effect |
|---|---|
| `NoNewPrivileges` | no setuid/setgid escalation from anything the run execs |
| `ProtectSystem=strict` | the entire filesystem is read-only except `ReadWritePaths` |
| `ReadWritePaths` | the holes: the project directories this unit's run writes, tailored per unit |
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
  optional — set it to your real project path when you edit the example. It lists the
  project directories that unit's run writes, and nothing else can be written, the
  rest of the project included:

  | unit | `ReadWritePaths` |
  |---|---|
  | `lsst-transient-monitor` | `state/`, `cache/` |
  | `lsst-pipeline-alerce`, `lsst-pipeline-antares` | `cache/`, `reports/` |
  | `lsst-latency-sample` | `cache/`, `state/` |

  A unit that starts writing somewhere new fails visibly (`Read-only file system`),
  and the directory is added to that unit only.
- `PYTHONPYCACHEPREFIX` points Python's bytecode at `cache/pycache`: `src/` is not
  writable under the tailored paths, so the `__pycache__` next to the sources could
  not be refreshed.
- `PYTHONUNBUFFERED=1` in every active and dormant unit: without it Python buffers stdout when it
  is not a terminal, so a report would reach the journal in one block at the end of the
  run, stamped at flush time and out of order with the `WARN:`/`ERROR:` lines (stderr).
- `UV_NO_SYNC=1` in every active and dormant unit, the same rule set the same way as in Claude
  Code's settings: a unit runs outside Claude Code, so without it `uv run` would sync
  the venv to the lockfile, and install packages, whenever the lockfile got ahead of
  it. Dependency changes are a deliberate step taken with `uv add` / `uv sync`.
  No-sync implies `--frozen`, so `uv.lock` is never rewritten either (uv 0.10.11's own
  help). The frozen SSO example keeps its original lines.
- Each active and dormant unit carries a `TimeoutStartSec` sized to its run (`5min` for the sampler,
  `20min` for the pipelines, `2h` for the transient monitor): a oneshot has no start
  timeout by default, and a hung run would make its timer skip every later day.
- `$HOME` stays **readable, not writable** (`ProtectHome` is deliberately **not** set):
  `uv` reads its binary from `~/.local/bin`, its cache at `~/.cache/uv` and any
  interpreters at `~/.local/share/uv`, but under `ProtectSystem=strict` nothing in the
  home directory can be written. Adding `ProtectHome=yes` hides it entirely and breaks
  the units with a confusing cache error. So anything a run writes goes under the
  project (matplotlib's font cache, for one: the comment in `client.py`).
- `%h/.local/bin/uv` in the ANTARES example is a systemd specifier for the user's
  home, so that `ExecStart` needs no editing if `uv` is on the usual path. The other
  examples spell out `/path/to/uv`; either form works.
- A syscall denial shows up as `EPERM` from an ordinary library call, not a crash —
  check `journalctl --user -u <unit> -n 50` if a run starts failing right after a
  dependency upgrade, and verify with `systemd-analyze --user security <unit>`.

---

# Transient Monitor Setup — active since 2026-10-03

Daily systemd user timer that runs the transient monitor on the live footprint
(`LIVE_FOOTPRINT` in `transient_monitor.py`, ecdfs). Its report goes to the journal
under `lsst-transient-monitor`, like every active and dormant unit here. The point-source watch
runs inside it; nothing else to install.

## Files

- `lsst-transient-monitor.service.example`
- `lsst-transient-monitor.timer`: no `.example` and no comments, since it holds no
  paths to fill in; copy it as it is, or change the hour.

## Before installing

- The footprint's state must exist (`state/transient_monitor_ecdfs.json`, written by
  `--init`, done 2026-10-03). The unit never passes `--init`: without state a run
  exits 1 with an `ERROR:` naming the path, so a lost state file can never quietly
  become a first run. `--init` is run by hand, once per footprint.
- What the unit will do, by hand: `uv run python -m rubin_qa.transient_monitor --dry-run`.

## Install

```bash
# copy both files; drop `.example` from the service; edit its paths.
systemctl --user daemon-reload
systemctl --user enable --now lsst-transient-monitor.timer
```

## Verify

```bash
systemctl --user list-timers lsst-transient-monitor.timer
systemctl --user start lsst-transient-monitor.service   # a real run now: saves state, like the daily one
journalctl --user -u lsst-transient-monitor.service --since today -o cat   # the day's report
systemctl --user --failed
```

## Notes

- **The hour is yours to choose.** The example says 16:00, in the machine's local
  time, picked for a machine in Germany so that both surveys' nights are over and
  ingested. Computed over 2026 with the sun at −12°, the latest ends are 12:43 German
  time for Rubin (Cerro Pachón, late June) and 15:03 for ZTF (Palomar, late October).
  ZTF is the only live stream while Rubin is off sky, so 16:00 leaves ALeRCE about an
  hour for the end of the ZTF night. Elsewhere, shift it to the same moment
  (14:00 UTC), or add a time zone to `OnCalendar` (`*-*-* 16:00:00 Europe/Berlin`).
  An earlier run loses nothing (the next one sees what it missed); it only reports
  some objects a day later.
- `Persistent=true`: a run missed while the machine was off or asleep is made up at
  the next start. A catch-up plus the daily run on the same day is harmless: each run
  reports only what changed against the state.
- **No arguments, on purpose.** The footprint
  is the CLI default, so it is named in one place. Never `--init` (see above), and never
  `--dry-run`: nothing would be saved, so every alert would repeat daily.
- **Exit 1 means look**: a listing failed (an outage, not a quiet sky), the state is
  missing, or the network failed after retries. The `ERROR:` line in the journal says
  which, and the unit shows in `systemctl --user --failed`. A missed listing loses
  nothing: parked objects stay parked, and the 14-day window lets the next run catch up.
- `WorkingDirectory` is for `uv`, which finds the project from it. The monitor resolves
  every path from the repo root (`config.PROJECT_ROOT`), never the working directory.
- User timers fire only while the user's systemd manager runs (a login session, or
  `loginctl enable-linger`); `Persistent=true` covers the rest at the next login.
- Checked 2026-10-03, before installing anything. The unit's exact command and
  hardening block ran as a throwaway `systemd-run --user` with `--dry-run`: success
  in 49 s, state untouched. Both files, with paths filled in, pass
  `systemd-analyze --user verify`.

---

# Bright SSO Monitor Setup — frozen; stopped 2026-08-18

> **Frozen.** The unit ran daily from 2026-04-13 to 2026-08-17 (115 scans); it is not
> armed and its files are never edited. The section below is the deployment record. Why it was stopped, and what
> its alerts were: "Bright Solar System Objects (SSO) Monitor" in the project README.

Implementation of daily systemd user timer that ran `antares_sso_monitor.py` and logged brightness alerts.

## Files

- `lsst-sso-monitor.service.example`
- `lsst-sso-monitor.timer` (no paths, so no `.example`)

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

## Notes

- `ExecStart` must be an absolute path. `~` is not expanded by systemd.
- `status=203/EXEC` in journalctl = executable not found or not executable (check `chmod +x` and the shebang).
- `Persistent=true` runs a missed job on next boot.
- `ExecStartPre=/bin/sleep 60` lets the network settle before the ANTARES query.
- State file: `state/bright_sso_state.json`, resolved against the script's own directory (not the CWD).

---

# Broker Latency Sampling Setup — dormant since 2026-09-01

> **Dormant.** The timer ran from 2026-08-11 to 2026-09-01 and is not armed; its
> example is kept current, and the section below re-arms it. What the campaign found, and when to run it
> again: "Broker Latency Sampling Campaign" in the project README.

Hourly systemd user timer that ran `tools/sample_latency.py` and appended one record
to `state/latency_samples.jsonl`. Fed the `SECONDS_PER_OBJECT` constants in
`config.py`, which drive the runtime estimate, the run deadline, and the long-run
confirm prompt.

`tools/sample_latency.py` stays in use while the unit is dormant: `--report` still reads
the collected `state/latency_samples.jsonl`, and a manual run still appends to it.

## Files

- `lsst-latency-sample.service.example`
- `lsst-latency-sample.timer` (no paths, so no `.example`)

## Install

```bash
# copy both files; drop `.example` from the service; edit its paths.
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
  each hour, so all 24 hour-buckets fill in a single day (why the randomisation: the
  README's method).
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
  they carry no timing. Confirmed working: a ZTF `ReadTimeout` at the full request
  timeout (60 s then) was captured on 2026-08-11 at 22:50.
- Load: roughly 40 HTTP calls per run against public broker APIs. At hourly that is
  ~3× what the existing daily pipeline already costs them. Prefer hourly over 30 min,
  and lower `--objects` before raising the frequency.

---

# Automated ALeRcE LSST Pipeline Setup

Weekly systemd user timer that runs `pipeline.py` on ALeRcE broker.

## Files

- `lsst-pipeline-alerce.service.example`
- `lsst-pipeline-alerce.timer` (no paths, so no `.example`)

## Install

```bash
# copy both files; drop `.example` from the service; edit its paths and/or page numbers.
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
- `lsst-pipeline-antares.timer` (no paths, so no `.example`)

## Install

```bash
# copy both files; drop `.example` from the service; edit its paths and/or page numbers.
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

- Why the report has fewer rows than the 256 asked for: `get_random_locus_ids` under
  "Known API Quirks" in the project README.
- `-q` trims stdout to the summary table. The CSV in `reports/` is unaffected.
- Output goes to the journal under `SyslogIdentifier=lsst-pipeline-antares` — see
  "Logging" above.
- `ExecStartPre=/usr/bin/nm-online -q -t 36` waits up to 36s for the network. On a
  machine without NetworkManager, swap it for `/bin/sleep 60` as the SSO monitor
  does, or drop it if the timer never fires near boot.
- A non-existent `ANT...` locus ID answers **500, not 404**, so it burns the full
  retry backoff before being given up on. `ZTF...` IDs return a clean not-found.
