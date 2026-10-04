import datetime
import os
import pathlib

DEFAULT_SURVEY    = "ztf"
DEFAULT_PAGE_SIZE = 100

# Every survey the CLI accepts, in help-text order. The CLI validates against this
# rather than passing any string through: `pipeline.py 50` used to parse 50 as a
# survey name and reach the broker with it. Keep in step with SECONDS_PER_OBJECT
# below — a survey here but not there falls back to DEFAULT_SECONDS_PER_OBJECT.
#
# Single source of truth for the survey list: __main__ (argparse choices), profiler
# (same), and both tools/ benchmarks import this rather than restating the tuple.
SURVEYS = ("ztf", "lsst", "antares")
# The surveys the transient monitor watches live: both listed and fetched from ALeRCE
# (2026-10-03). ANTARES is a broker, not a survey: it stays in the QA pipeline's SURVEYS.
LIVE_SURVEYS = ("ztf", "lsst")

# Output paths anchor to the project root, never the caller's CWD — the systemd
# units and manual runs start from arbitrary directories, and a bare relative
# path scatters reports wherever the run happened to begin.
# config.py sits at <root>/src/rubin_qa/, so parents[2] is the root itself.
# RUBIN_QA_ROOT overrides it for installs that are not the src-layout checkout.
PROJECT_ROOT = pathlib.Path(
    os.environ.get("RUBIN_QA_ROOT") or pathlib.Path(__file__).resolve().parents[2]
)
REPORTS_DIR  = PROJECT_ROOT / "reports"
# Runtime directories; what goes in which: CLAUDE.md, "Runtime directories".
CACHE_DIR    = PROJECT_ROOT / "cache"
STATE_DIR    = PROJECT_ROOT / "state"
LOGS_DIR     = PROJECT_ROOT / "logs"


def from_root(path: str | os.PathLike) -> pathlib.Path:
    """
    A path as the docs write it, relative to the repo root (reports/x.csv,
    logs/replay_D_....json); absolute and ~ paths pass through. For paths given on
    the command line: the current directory never decides where a file is read or
    written, so a documented example works from any directory.
    """
    p = pathlib.Path(path).expanduser()
    return p if p.is_absolute() else PROJECT_ROOT / p

# Time reference shared by the transient monitor and the frozen SSO monitor
# (antares_sso_monitor.py), whose queries and state files are in MJD.
MJD_J2000 = 51544.5  # MJD of J2000.0 epoch reference (2000-01-01 12:00 UTC)


def now_mjd() -> float:
    """Return the MJD of current time"""
    epoch = datetime.datetime(2000, 1, 1, 12, tzinfo=datetime.timezone.utc)
    now = datetime.datetime.now(datetime.timezone.utc)
    return MJD_J2000 + (now - epoch).total_seconds() / 86400  # 86400s = 1 day

# Sky footprints: cones (ra, dec, radius), the only shape ALeRCE searches. Shared by the
# transient monitor, its replay and the tools that check catalogues against a footprint.
DEFAULT_RADIUS_DEG = 2.82  # pi r^2 = 25 deg2; membership is angular separation <= radius
FOOTPRINTS = {
    "C": ((35.7, -9.0, DEFAULT_RADIUS_DEG), "former live candidate v1: just south of the XMM-LSS deep field"),
    "D": ((150.1, 2.5, DEFAULT_RADIUS_DEG), "test replay: COSMOS, Rubin data 2026-02-15..05-15"),
    "ecdfs": ((53.1, -27.8, DEFAULT_RADIUS_DEG), "the live footprint: ECDFS deep field"),
}
LIVE_FOOTPRINT = "ecdfs"  # decided 2026-10-02; scanned when neither --footprint nor --cone is given

# Survey data facts used by more than one module (photometry, profiler, reporting).
AB_NJY_ZP = 31.4  # m_AB = 31.4 - 2.5 log10(flux / nJy)
ZTF_FID_BAND = {1: "g", 2: "r", 3: "i"}  # ZTF filter id -> band

# Retry config for rate-limited or timing-out API calls.
# ALeRCE returns 504 when its object endpoint is slow under load; a short wait
# lands on the same bad state, so back off far enough to ride the episode out.
RETRY_ATTEMPTS = 4
RETRY_DELAY    = 9.0  # seconds; exponential backoff → 9, 18, 36

# Per-request ceiling forced onto the ALeRCE client, which passes no timeout of
# its own (alerce/utils.py Client._request), leaving requests to block forever.
# Both per-run ceilings below are checked *between* calls, so neither can
# interrupt a socket already blocked in a read. Set above ALeRCE's known 504
# episodes ("slow for tens of seconds") so genuine slow answers still arrive;
# the candidate fetch, the slowest call in the system, is what fixes it at 60
# rather than lower (its timings: README, "Broker Latency Sampling Campaign").
#
# NOTE this bounds *inactivity*, not call duration: requests applies the timeout
# per socket operation (connect, and the gap between received bytes), so a
# response that trickles never trips it. The 2026-08 campaign caught three LSST
# calls returning successfully at 60.7s, 60.7s and 63.3s with this ceiling in
# force. It reliably kills a dead connection — 11 ZTF ReadTimeouts in 328 runs —
# but a slow-but-alive one is bounded only by the run deadline below.
# ANTARES needs no equivalent: antares-client applies its own (README, Known API Quirks).
REQUEST_TIMEOUT = 60.0

# Total time one run may spend sleeping between retries. Retry is per-object, so
# a broker-wide outage would otherwise multiply backoff across the whole page.
# Once spent, remaining calls get their attempts without backoff.
RETRY_BUDGET_SECONDS = 300.0

# Measured wall-clock cost per object, including INTER_OBJECT_DELAY. Update if
# broker response times shift — these only size estimates and deadlines, never
# correctness.
#
# Settled by the 2026-08/09 sampling campaign: each value sits at or above its
# survey's measured p95 (the numbers: README, "Broker Latency Sampling Campaign").
SECONDS_PER_OBJECT = {
    "ztf":     3.7,  # 3 ALeRCE calls per object, ~1.1s each
    "lsst":    2.4,  # query_magstats short-circuits client-side
    "antares": 1.0,  # 1 locus fetch, ~0.5s
}
# Fallback for a survey absent from the table above. Deliberately pessimistic: no
# measurement exists for an unmeasured broker, and a new one is likelier to be slow
# than fast.
#
# A judgement call, not a measurement: where 17.0 came from is in the README's
# sampling campaign. Roughly 5× the slowest survey measured is the whole reasoning.
#
# Its two consumers pull opposite ways, so "is it too big" has no single answer:
# a large value makes estimate_runtime() trip _confirm_long_run() early (good — a
# human looks at the first run against a new broker), and makes deadline_for()
# lax (bad — 6 × 17.0 = 102s/object, so an unknown broker gets the weakest run
# ceiling in the system, exactly where we know least).
#
# Currently unreachable in normal use: the table above covers every survey the CLI
# accepts. The one live path to it is __main__'s survey positional, which takes any
# string, so `pipeline.py 50` parses 50 as a survey name and lands here. Putting
# choices= on that argument closes the hole and leaves this honestly dead until a
# fourth broker arrives — which is the point at which to re-derive the number
# rather than tuning it now against no data.
DEFAULT_SECONDS_PER_OBJECT = 17.0

# The run deadline is derived per job, not fixed: a big scan is entitled to take
# a long time, so a flat ceiling would truncate healthy work. What it catches is
# a run going far past *its own* estimate — that is a stalled broker or link, not
# a busy one. On expiry the loop stops and reports the rows already gathered.
#
# It is 6× and not 3× because of what one pathological object can cost (the worst
# case it absorbs: README, "Known API Quirks", the run deadline). At ztf 3.7s the 6×
# slack gives 22.2s per object of headroom, so a 100-object run absorbs ~3 such
# objects before tripping; at 3× one bad object plus normal variance starts
# truncating healthy runs.
DEADLINE_SLACK = 6.0
DEADLINE_FLOOR = 300.0  # seconds; keeps short runs from tripping on noise

# Estimated runtime above which the CLI confirms before starting. Only prompts on
# a TTY — under systemd it logs the estimate and proceeds.
CONFIRM_THRESHOLD_SECONDS = 900.0

# Prefixes for operator-facing messages on stderr.
#   WARN:  degraded but recoverable — the run continues (retries, empty pages).
#   ERROR: the operation failed and its caller cannot proceed (failed fetches,
#          aborted runs). Every ERROR path either returns no data or exits 1.
WARN_PREFIX  = "WARN: "
ERROR_PREFIX = "ERROR: "

INTER_OBJECT_DELAY = 0.5  # seconds between objects; increase if hitting rate limits

OUTPUT_COLUMNS = [
    "broker", "oid", "ndet", "mag_range", "timespan_days",
    "top_class", "class_prob", "consensus", "n_classifiers",
    "n_agree", "n_disagree",
    "confirmed", "has_issues", "completeness_issues", "flag", "status",
]
