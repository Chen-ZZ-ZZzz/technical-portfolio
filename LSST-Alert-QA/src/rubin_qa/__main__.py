"""CLI entry point: python -m rubin_qa [survey] [page_size | oid oid ...]

  survey: ztf (default) | lsst | antares
"""

import datetime
import pathlib
import sys

from .config import DEFAULT_SURVEY, DEFAULT_PAGE_SIZE
from .reporting import run_antares_pipeline, run_pipeline

SUMMARY_COLUMNS = ["oid", "ndet", "top_class", "consensus", "n_classifiers", "status"]


def main() -> None:
    args = sys.argv[1:]

    survey = args[0] if args else DEFAULT_SURVEY
    rest   = args[1:]

    if survey == "antares":
        if rest and not rest[0].isdigit():
            explicit_ids = rest
            print(f"=== ANTARES Alert QA Pipeline  locus_ids={explicit_ids} ===\n")
            df = run_antares_pipeline(locus_ids=explicit_ids)
        else:
            n = int(rest[0]) if rest else DEFAULT_PAGE_SIZE
            print(f"=== ANTARES Alert QA Pipeline  page_size={n} ===\n")
            df = run_antares_pipeline(page_size=n)
    elif rest and not rest[0].isdigit():
        explicit_oids = rest
        n = len(explicit_oids)
        print(f"=== LSST/ZTF Alert Data Quality Pipeline  survey={survey}  oids={explicit_oids} ===\n")
        df = run_pipeline(survey=survey, oids=explicit_oids)
    else:
        n = int(rest[0]) if rest else DEFAULT_PAGE_SIZE
        print(f"=== LSST/ZTF Alert Data Quality Pipeline  survey={survey}  page_size={n} ===\n")
        df = run_pipeline(page_size=n, survey=survey)

    reports_dir = pathlib.Path("reports")
    reports_dir.mkdir(exist_ok=True)
    date = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = reports_dir / f"qa_{survey}_{date}_n{len(df)}.csv"
    df.to_csv(csv_path, index=False)

    print("\n=== QA Report ===")
    print(df[SUMMARY_COLUMNS].to_string())
    print(f"\n{df['flag'].notna().sum()}/{len(df)} objects flagged  |  full report: {csv_path}")


if __name__ == "__main__":
    main()
