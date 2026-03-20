DEFAULT_SURVEY    = "ztf"
DEFAULT_PAGE_SIZE = 100

# Classification QA thresholds
HIGH_CONFIDENCE_THRESHOLD = 0.90   # weighted consensus → clean label, no flag
MAJORITY_THRESHOLD        = 0.65   # weighted consensus → majority, minor flag
OUTLIER_PROB_THRESHOLD    = 0.30   # dissenting classifier below this → outlier, not genuine split

# Retry config for rate-limited API calls
RETRY_ATTEMPTS = 2
RETRY_DELAY    = 2.0  # seconds

INTER_OBJECT_DELAY = 0.5  # seconds between objects; increase if hitting rate limits

OUTPUT_COLUMNS = [
    "oid", "ndet", "mag_range", "timespan_days",
    "top_class", "class_prob", "consensus", "n_classifiers",
    "n_agree", "n_disagree",
    "confirmed", "has_issues", "completeness_issues", "flag", "status",
]
