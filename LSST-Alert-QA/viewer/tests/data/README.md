# Viewer test data

The example files the viewer's rules name ([SPEC.md](../../SPEC.md)), one line each: what it is, and what it checks. `real/` holds reports the monitor wrote; `made/` holds what the real ones can't provide.

## real/

Live reports copied unchanged from the monitor's `reports/`.

- `transient_ecdfs_20261102_163727.json`: the only real report with a failed listing, LSST's (a 422 from ALeRCE; exit 1). For the run time shown in UTC, and for the message naming the failed survey without the error's text.
- `transient_ecdfs_20261105_160051.json`: an ordinary live run (exit 0, 2 alerts). For the report page's heading and its Raw JSON link.
- `transient_ecdfs_20261109_160151.json`: no case of its own; kept as the template of `made/transient_<f>oobar_20251004_000000.json` (live, exit 0, no alerts, `answered_mjd` on both listings).
- `transient_ecdfs_20261110_160051.json`: 6 alerts while `objects.kept` is 7, so the alert count is read from `alerts`; and exit 0, for the message of a run without errors.
- `transient_ecdfs_20261111_160243.json`: `state_before.reported` is 8, for the count of objects reported earlier.

## made/

### From runs of the monitor

Runs on 2026-11-12 with a scratch root (`RUBIN_QA_ROOT` pointed at a scratch directory, since deleted), each with the failure stated in its line.

- `transient_ecdfs_20261112_095400_init.json`: an `--init` run whose two listings both failed. Empty state, and ALeRCE pointed at `127.0.0.1:9` through the alerce client's `ALERCE_CONFIG_PATH`, so both listings fail with "connection refused", and `newest_mjd` and `answered_mjd` are null.
- `transient_ecdfs_20261112_095402.json`: the same as a live run.
- `transient_ecdfs_20261112_095404.json`: the short report: exit 1 with no listings at all, only `error` and the retry `warnings`. Reachable only by injection, because `gather`'s calls catch their own network errors: `discover_alerce` was replaced by a function raising a network error ("down"), with the retry wait set to 0 s.
- `transient_ecdfs_20261112_111059.json`: both listings failed on a state with history; no rule names it yet. A live run on a copy of the real state of 2026-11-12, with every proxy variable pointed at `127.0.0.1:9`, so the failure text is a ProxyError, not the name-resolution error an offline machine gives; `newest_mjd` is carried over from the state, `answered_mjd` is null.
- `transient_ecdfs_20261112_224310.json`: ZTF's listing failed alone. A live run on a copy of the real state after that day's 16:00 run, with `--radius 0.5`, so its `cone` reads radius 0.5, not ECDFS's 2.82: the full radius would have meant ~5800 light-curve calls (~80 min); this run took 8.4 min. `ALERCE_CONFIG_PATH` redirected only ZTF's URL to `127.0.0.1:9`, so LSST and TNS stayed live: ZTF failed (connection refused, `newest_mjd` carried, `answered_mjd` null), LSST listed 764 objects, no alerts, exit 1. The real state's hashes were unchanged afterwards.

### By hand

- `transient_<f>oobar_20251004_000000.json`: made from `real/transient_ecdfs_20261109_160151.json` for three cases: the footprint `<f>oobar` shown as text, the Raw JSON link to a file whose name holds angle brackets, and an impossible cone, `[200.5, 90.0, 1.25]`: centred on the north celestial pole, which Rubin never sees, while the listings count LSST objects. Changed from the template: `footprint`, `cone`, `run_utc` (2025-10-03 22:00:00 UTC; 00:00:00 local in the file name), `run_mjd` and `since_mjd`, written with the monitor's own writer (`_write_report`). Everything else is the template's, so the times inside `listings`, and the first detection in `parked.new`, lie after the run.

Three files that are not reports, named like live reports so that the overview reads them. Each holds exactly the bytes shown, with no newline at the end.

- `transient_foobar_20251001_000000.json`: `"foobar"`: valid JSON, but a string, so it has no `run_utc`.
- `transient_foobar_20251002_000000.json`: `{}`: valid JSON, an object without `run_utc`.
- `transient_foobar_20251003_000000.json`: `{"mode":`: not valid JSON, cut off after its first key.
