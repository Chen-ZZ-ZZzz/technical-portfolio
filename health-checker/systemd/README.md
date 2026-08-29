## Journal scanner

Periodic scan of the systemd journal for warning-and-above entries,
producing timestamped JSON reports.

Since Debian 12 ships without rsyslog by default, the journal is the
primary log store: `/var/log/syslog` and `auth.log` no longer exist on a
stock system. Scanning `/var/log` therefore misses most of what a machine
logs. This tool reads the journal instead, while `log_scan.py` retains its
file-scanning mode for logs that bypass journald (apt, dpkg, applications
managing their own files).

### Layers

    checker-journal-scan.timer     when to run
    checker-journal-scan.service   environment: working dir, state dir, identifier
    journal_scan.sh                sequencing: extract, convert, scan, commit, rotate
    journal2log.py                 journal JSON -> log_scan-readable lines
    log_scan.py                    matching, counting, reporting

Each layer owns one concern. `journalctl` is invoked only from the shell
script, never from a unit file: `ExecStart` is not a shell, so it supports
no redirection or pipes, and sequencing logic placed there could not be
tested or run by hand.

### Incremental scanning

Each run resumes where the last one stopped, using a journal cursor stored
in `StateDirectory`. A cursor names one specific journal entry by sequence
number, boot ID and timestamps, which makes it immune to clock adjustments
and to the journal's file rotation.

The cursor is committed only after the scan succeeds. `journalctl
--cursor-file` would commit at its own exit, before the pipeline runs, so a
failure downstream would lose that batch permanently — at-most-once
delivery. Committing afterwards gives at-least-once instead: a crash costs
a duplicate batch, not a gap, and duplicates are identifiable by their
cursor.

Because the cursor positions by sequence rather than by time, a run after a
reboot picks up the previous boot's shutdown messages before continuing
into the current boot. Shutdown is where stop-job timeouts and unmount
failures appear, so this matters more than it might seem.

### Priorities, not keywords

Text output formats drop the `PRIORITY` field, and many important entries
(kernel I/O errors, for example) contain no level keyword at all. The
converter therefore maps `PRIORITY` 0–3 to `ERROR` and 4 to `WARN` before
`log_scan.py` sees the text, so filtering is structural rather than
lexical.

This also prevents feedback: the scanner's own summary line enters the
journal at info priority through stdout, below the extraction threshold, so
it can never appear in a later report. Records originating from the
scanner's own unit are excluded explicitly via `_SYSTEMD_USER_UNIT`, a
field journald stamps and clients cannot forge. Messages the service
manager emits *about* the unit are stamped differently and remain visible,
which is how a failed run surfaces in the next report.

### Timer

    OnCalendar=hourly
    Persistent=true
    OnBootSec=16min

Calendar timers read the realtime clock and fire immediately on resume for any
deadline that passed during sleep. `Persistent=true` catches up hour marks
missed while powered off; `OnBootSec` covers quick reboots that cross no hour
boundary. Extra firings cost nothing, since the cursor guarantees each run
scans only what is new.

The service exits 0 whether or not it finds anything. A non-zero exit means
the pipeline broke, not that the journal contains warnings.

### Installation

Copy the unit files to `~/.config/systemd/user/`, drop the `.example`
suffix, and replace `__PROJECT_ROOT__` with your own project path. Then:

    systemctl --user daemon-reload
    systemctl --user start checker-journal-scan.service   # verify first
    systemctl --user status checker-journal-scan.service
    systemctl --user enable --now checker-journal-scan.timer

Reading the journal requires membership in `systemd-journal` or `adm`.
Without it, `journalctl` silently returns only your own user journal rather
than reporting an error.
