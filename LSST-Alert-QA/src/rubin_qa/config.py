import datetime
import os
import pathlib

DEFAULT_SURVEY    = "ztf"
DEFAULT_PAGE_SIZE = 100

# Every survey the QA pipeline accepts, in help-text order. Each has a measured
# SECONDS_PER_OBJECT (tests/test_ceilings.py).
SURVEYS = ("ztf", "lsst", "antares")
# The surveys the transient monitor watches live. ANTARES is a broker, not a survey,
# so it is not one of them.
LIVE_SURVEYS = ("ztf", "lsst")

# Data paths anchor to the project root, never the caller's CWD: the systemd units
# and manual runs start from arbitrary directories, and a bare relative path would
# read and write wherever the run happened to begin.
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

MJD_J2000 = 51544.5  # MJD of the J2000.0 epoch (2000-01-01 12:00 UTC)


# now_mjd is part of the frozen SSO monitor too: its source is SHA-pinned
# (tests/test_sso_frozen.py), so it does not change.
def now_mjd() -> float:
    """Return the MJD of current time"""
    epoch = datetime.datetime(2000, 1, 1, 12, tzinfo=datetime.timezone.utc)
    now = datetime.datetime.now(datetime.timezone.utc)
    return MJD_J2000 + (now - epoch).total_seconds() / 86400  # 86400s = 1 day

# Sky footprints: cones (ra, dec, radius), the only shape ALeRCE searches.
DEFAULT_RADIUS_DEG = 2.82  # degrees: pi r^2 = 25 deg2; inside = angular separation <= radius
FOOTPRINTS = {
    "C": ((35.7, -9.0, DEFAULT_RADIUS_DEG), "former live candidate v1: just south of the XMM-LSS deep field"),
    "D": ((150.1, 2.5, DEFAULT_RADIUS_DEG), "test replay: COSMOS deep field"),
    "ecdfs": ((53.1, -27.8, DEFAULT_RADIUS_DEG), "the live footprint: ECDFS deep field"),
}
LIVE_FOOTPRINT = "ecdfs"  # the footprint the daily run watches (decided 2026-10-02)

# Survey data facts.
AB_NJY_ZP = 31.4  # m_AB = 31.4 - 2.5 log10(flux / nJy)
ZTF_FID_BAND = {1: "g", 2: "r", 3: "i"}  # ZTF filter id -> band

# Retry policy for a broker call that hit a transient fault (the loop: retry_budget.call).
# ALeRCE returns 504 when its object endpoint is slow under load; a short wait lands on
# the same bad state, so the waits are long enough to ride the burst out.
RETRY_ATTEMPTS = 4    # calls in all, the first included
RETRY_DELAY    = 9.0  # seconds: the first wait (the schedule: retry_budget.call)

# Seconds of silence after which one request to ALeRCE (its broker API, its TNS service)
# is abandoned. It bounds *inactivity*, not call duration: requests applies it per socket
# operation (connect, and the gap between received bytes), so it kills a dead connection,
# while a response that trickles in never trips it (evidence: README, "Known API Quirks").
# Why 60: above ALeRCE's 504 bursts ("slow for tens of seconds"), so genuine slow answers
# still arrive; the candidate fetch, the slowest call in the system, is what keeps it from
# being lower (its timings: README, "Broker Latency Sampling Campaign"). How the ALeRCE
# client gets it: client._force_session_timeout. ANTARES needs no equivalent: antares-client
# applies its own (README, "Known API Quirks").
REQUEST_TIMEOUT = 60.0

# Seconds one run may spend sleeping between retries, both brokers together. Retry is
# per object, so a broker-wide outage would otherwise multiply the waits across the
# whole page. What happens once it is spent: retry_budget.call.
RETRY_BUDGET_SECONDS = 300.0

# Measured wall-clock seconds per object, INTER_OBJECT_DELAY included. Settled by the
# 2026-08/09 sampling campaign: each value sits at or above its survey's measured p95
# (the numbers: README, "Broker Latency Sampling Campaign"). Update if broker response
# times shift. What they size: reporting.estimate_runtime.
SECONDS_PER_OBJECT = {
    "ztf":     3.7,  # 3 ALeRCE calls per object
    "lsst":    2.4,  # 2: ALeRCE's client refuses LSST magstats without a request
    "antares": 1.0,  # 1 locus fetch
}
# Seconds per object for a survey absent from the table above. Deliberately
# pessimistic: a new broker is likelier to be slow than fast. A judgement call, not a
# measurement: where 17.0 came from is in the README's sampling campaign; roughly 5×
# the slowest survey measured is the whole reasoning. Unreachable from the CLI, which
# accepts only surveys the table covers; re-derive it when a fourth broker arrives rather
# than tune it now against no data. Why "too big" has no single answer:
# reporting.estimate_runtime.
DEFAULT_SECONDS_PER_OBJECT = 17.0

# The run deadline as a multiple of the run's own estimate: what it catches is a run
# going far past *its own* estimate, a stalled broker or link, not a busy one (how it
# is applied: reporting.deadline_for, run_pipeline). Why 6× and not 3×: one
# pathological object can block for minutes (the worst case: README, "Known API
# Quirks", the run deadline). Chosen with ztf at 3.7 s per object: a 100-object run
# then absorbs two to three such objects before tripping, where at 3× one bad object
# plus normal variance starts truncating healthy runs.
DEADLINE_SLACK = 6.0
DEADLINE_FLOOR = 300.0  # seconds; keeps short runs from tripping on noise

# Estimated seconds above which a run asks before starting. What each caller does
# without a terminal: __main__._confirm_long_run (the QA pipeline proceeds) and
# replay.confirm_cost (a replay refuses).
CONFIRM_THRESHOLD_SECONDS = 900.0

# Prefixes for operator-facing messages on stderr.
#   WARN:  degraded but recoverable: the run continues (retries, empty pages).
#   ERROR: the operation failed and its caller cannot proceed (failed fetches,
#          aborted runs).
WARN_PREFIX  = "WARN: "
ERROR_PREFIX = "ERROR: "

INTER_OBJECT_DELAY = 0.5  # seconds between objects; increase if hitting rate limits

OUTPUT_COLUMNS = [
    "broker", "oid", "ndet", "mag_range", "timespan_days",
    "top_class", "class_prob", "consensus", "n_classifiers",
    "n_agree", "n_disagree",
    "confirmed", "has_issues", "completeness_issues", "flag", "status",
]
