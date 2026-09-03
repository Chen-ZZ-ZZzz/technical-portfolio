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
# interrupt a socket already blocked in a read. Set above ALeRCE's known 504
# episodes ("slow for tens of seconds") so genuine slow answers still arrive;
# the candidate fetch, the slowest call in the system, is what fixes it at 60
# rather than lower (measured p95 30.9s, max 45.4s over 98 samples).
#
# NOTE this bounds *inactivity*, not call duration: requests applies the timeout
# per socket operation (connect, and the gap between received bytes), so a
# response that trickles never trips it. The 2026-08 campaign caught three LSST
# calls returning successfully at 60.7s, 60.7s and 63.3s with this ceiling in
# force. It reliably kills a dead connection — 11 ZTF ReadTimeouts in 328 runs —
# but a slow-but-alive one is bounded only by the run deadline below.
# ANTARES needs no equivalent: antares-client applies its own 60s timeout.
REQUEST_TIMEOUT = 60.0

# Total time one run may spend sleeping between retries. Retry is per-object, so
# a broker-wide outage would otherwise multiply backoff across the whole page.
# Once spent, remaining calls get their attempts without backoff.
RETRY_BUDGET_SECONDS = 300.0

# Measured wall-clock cost per object, including INTER_OBJECT_DELAY. Update if
# broker response times shift — these only size estimates and deadlines, never
# correctness.
#
# Settled by the sampling campaign (2026-08-11 to 2026-09-01, 328 runs, ~1600
# object timings per survey). Each value sits at or above the measured p95 of
# per-run medians, so a typical run finishes inside its estimate:
#
#   survey    p50    p95    p99   worst run   constant
#   ztf      3.38   3.75   3.84       4.31        3.7
#   lsst     2.19   2.38   2.50       2.71        2.4
#   antares  0.93   1.36   1.45       1.53        1.0
#
# The 17.0s ztf figure from 2026-08-06 did not recur once in 21 days and was an
# episode, not a baseline. Latency proved flat by hour (3.19-3.61 across the 19
# hours sampled), flat by weekday, and free of collection bias (timer 3.34 vs
# manual 3.41). Hours 02-06 are unsampled and stay that way by choice: the
# machine is off overnight, so no run is ever issued then.
SECONDS_PER_OBJECT = {
    "ztf":     3.7,  # 3 ALeRCE calls per object, ~1.1s each
    "lsst":    2.4,  # query_magstats short-circuits client-side
    "antares": 1.0,  # 1 locus fetch, ~0.5s
}
# Fallback for a survey absent from the table above. Deliberately pessimistic: no
# measurement exists for an unmeasured broker, and a new one is likelier to be slow
# than fast.
#
# The value is a judgement call, not a measurement — do not read it as one. It was
# 17.0 because that was ALeRCE's measured ztf cost on 2026-08-06; the sampling
# campaign then showed that figure to be an episode (see SECONDS_PER_OBJECT above),
# so the provenance is gone and only the pessimism is deliberate. Roughly 5× the
# slowest survey we have measured is the whole of the reasoning.
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
# It is 6× and not 3× because of what a single pathological object can cost.
# RETRY_BUDGET_SECONDS caps retry *sleep*, not socket time: once the budget is
# spent _api_call keeps attempting with no backoff, and each attempt can still
# block for REQUEST_TIMEOUT. Worst case for one ztf object is 3 calls × 4
# attempts × 60s = 720s of blocking, and nothing but this deadline stops it.
# At ztf 3.7s the 6× slack gives 22.2s per object of headroom, so a 100-object
# run absorbs ~3 such objects before tripping. At 3× one bad object plus normal
# variance starts truncating healthy runs.
#
# (Sizing it to survive a return to 17.0s/object was the original rationale;
# the campaign retired that contingency, but not the number — see above.)
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
