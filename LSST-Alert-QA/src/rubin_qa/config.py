import os
import pathlib

DEFAULT_SURVEY    = "ztf"
DEFAULT_PAGE_SIZE = 100

# Output paths anchor to the project root, never the caller's CWD — the systemd
# units and manual runs start from arbitrary directories, and a bare relative
# path scatters reports wherever the run happened to begin.
# config.py sits at <root>/src/rubin_qa/, so parents[2] is the root itself.
# RUBIN_QA_ROOT overrides it for installs that are not the src-layout checkout.
PROJECT_ROOT = pathlib.Path(
    os.environ.get("RUBIN_QA_ROOT") or pathlib.Path(__file__).resolve().parents[2]
)
REPORTS_DIR  = PROJECT_ROOT / "reports"

# Classification QA thresholds
HIGH_CONFIDENCE_THRESHOLD = 0.90   # weighted consensus → clean label, no flag
MAJORITY_THRESHOLD        = 0.65   # weighted consensus → majority, minor flag
OUTLIER_PROB_THRESHOLD    = 0.30   # dissenting classifier below this → outlier, not genuine split

# Retry config for rate-limited or timing-out API calls.
# ALeRCE returns 504 when its object endpoint is slow under load; a short wait
# lands on the same bad state, so back off far enough to ride the episode out.
RETRY_ATTEMPTS = 4
RETRY_DELAY    = 9.0  # seconds; exponential backoff → 9, 18, 36

# Per-request ceiling forced onto the ALeRCE client, which passes no timeout of
# its own (alerce/utils.py Client._request), leaving requests to block forever.
# Both per-run ceilings below are checked *between* calls, so neither can
# interrupt a socket already blocked in a read — without this a single hung
# connection stalls a run indefinitely. Set above ALeRCE's known 504 episodes
# ("slow for tens of seconds") so genuine slow answers still arrive.
# ANTARES needs no equivalent: antares-client applies its own 60s timeout.
REQUEST_TIMEOUT = 60.0

# Total time one run may spend sleeping between retries. Retry is per-object, so
# a broker-wide outage would otherwise multiply backoff across the whole page.
# Once spent, remaining calls get their attempts without backoff.
RETRY_BUDGET_SECONDS = 300.0

# Measured wall-clock cost per object, including INTER_OBJECT_DELAY (2026-08-11,
# 20 samples per survey over two rounds). Update if broker response times shift —
# these only size estimates and deadlines, never correctness.
#
# These moved a lot between 2026-08-06 and 2026-08-11: ztf was 17.0 (~5.6s per
# ALeRCE call), now ~3.2s for all three calls together. Whether 17.0 was a bad
# week or 3.2 is a good day is still unsettled, so DEADLINE_SLACK below is sized
# to tolerate a return to the old latency rather than these numbers being final.
SECONDS_PER_OBJECT = {
    "ztf":     3.7,  # 3 ALeRCE calls per object, ~1.1s each
    "lsst":    2.4,  # query_magstats short-circuits client-side
    "antares": 1.0,  # 1 locus fetch, ~0.5s
}
# No measurement exists for an unmeasured broker, and a new one is likelier to be
# slow than fast — so this stays deliberately pessimistic (4.6× the slowest survey
# we have measured) rather than tracking the table above.
DEFAULT_SECONDS_PER_OBJECT = 17.0

# The run deadline is derived per job, not fixed: a big scan is entitled to take
# a long time, so a flat ceiling would truncate healthy work. What it catches is
# a run going far past *its own* estimate — that is a stalled broker or link, not
# a busy one. On expiry the loop stops and reports the rows already gathered.
#
# The slack absorbs two different things, which is why it is 6× and not 3×:
# per-run variance, and drift in SECONDS_PER_OBJECT between the days we measure
# it. At ztf 3.7s the deadline allows 22.2s per object — still above the 17.0s
# ALeRCE actually charged on 2026-08-06 — so a return to that latency degrades
# the estimate without truncating an otherwise healthy run.
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
    "oid", "ndet", "mag_range", "timespan_days",
    "top_class", "class_prob", "consensus", "n_classifiers",
    "n_agree", "n_disagree",
    "confirmed", "has_issues", "completeness_issues", "flag", "status",
]
