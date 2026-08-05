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
