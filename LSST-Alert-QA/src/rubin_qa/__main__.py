"""CLI entry point: python -m rubin_qa [survey] [page_size | oid oid ...]

  survey: ztf (default) | lsst | antares
"""

import argparse
import datetime
import sys

import pandas as pd

from .config import DEFAULT_SURVEY, DEFAULT_PAGE_SIZE, REPORTS_DIR, WARN_PREFIX
from .reporting import run_antares_pipeline, run_pipeline

SUMMARY_COLUMNS = ["oid", "ndet", "top_class", "consensus", "n_classifiers", "status"]


def _dispatch(survey: str, rest: list, quiet: bool) -> pd.DataFrame:
    """Route to the right pipeline. A numeric first target is a page size."""
    if survey == "antares":
        if rest and not rest[0].isdigit():
            if not quiet:
                print(f"=== ANTARES Alert QA Pipeline  locus_ids={rest} ===\n")
            return run_antares_pipeline(locus_ids=rest, quiet=quiet)
        n = int(rest[0]) if rest else DEFAULT_PAGE_SIZE
        if not quiet:
            print(f"=== ANTARES Alert QA Pipeline  page_size={n} ===\n")
        return run_antares_pipeline(page_size=n, quiet=quiet)

    if rest and not rest[0].isdigit():
        if not quiet:
            print(f"=== LSST/ZTF Alert Data Quality Pipeline  survey={survey}  oids={rest} ===\n")
        return run_pipeline(survey=survey, oids=rest, quiet=quiet)

    n = int(rest[0]) if rest else DEFAULT_PAGE_SIZE
    if not quiet:
        print(f"=== LSST/ZTF Alert Data Quality Pipeline  survey={survey}  page_size={n} ===\n")
    return run_pipeline(page_size=n, survey=survey, quiet=quiet)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="LSST/ZTF/ANTARES Alert Data Quality Pipeline"
    )
    parser.add_argument(
        "survey", nargs="?", default=DEFAULT_SURVEY,
        help="Broker/survey: ztf (default) | lsst | antares",
    )
    parser.add_argument(
        "targets", nargs="*",
        help="page_size (int) or explicit oid/locus_id list",
    )
    parser.add_argument(
        "-q", "--quiet", action="store_true",
        help="One-line summary only: broker, totals, output path",
    )
    opts = parser.parse_args()

    survey = opts.survey
    rest   = opts.targets
    quiet  = opts.quiet

    try:
        df = _dispatch(survey, rest, quiet)
    except Exception as e:
        print(
            f"{WARN_PREFIX}{survey}: pipeline failed — {type(e).__name__}: {e}",
            file=sys.stderr,
        )
        sys.exit(1)

    if df.empty:
        # No rows means the fetch failed or returned nothing — writing here
        # would leave a header-only CSV that looks like a successful run.
        print(
            f"{WARN_PREFIX}{survey}: no objects processed — no report written",
            file=sys.stderr,
        )
        sys.exit(1)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    date = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = REPORTS_DIR / f"qa_{survey}_{date}_n{len(df)}.csv"
    df.to_csv(csv_path, index=False)

    if quiet:
        flagged = df["flag"].notna().sum()
        print(f"{survey}  n={len(df)}  flagged={flagged}/{len(df)}  → {csv_path}")
    else:
        print("\n=== QA Report ===")
        print(df[SUMMARY_COLUMNS].to_string())
        print(f"\n{df['flag'].notna().sum()}/{len(df)} objects flagged  |  full report: {csv_path}")


if __name__ == "__main__":
    main()
