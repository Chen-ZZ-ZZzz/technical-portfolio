"""CLI entry point: python -m alerce_qa [survey] [page_size | oid oid ...]"""

import sys

from .config import DEFAULT_SURVEY
from .reporting import run_pipeline


def main() -> None:
    survey = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SURVEY
    rest   = sys.argv[2:]

    if rest and not rest[0].isdigit():
        explicit_oids = rest
        print(f"=== LSST/ZTF Alert Data Quality Pipeline  survey={survey}  oids={explicit_oids} ===\n")
        df = run_pipeline(survey=survey, oids=explicit_oids)
    else:
        page_size = int(rest[0]) if rest else DEFAULT_PAGE_SIZE
        print(f"=== LSST/ZTF Alert Data Quality Pipeline  survey={survey}  page_size={page_size} ===\n")
        df = run_pipeline(page_size=page_size, survey=survey)

    print("\n=== QA Report ===")
    print(df.to_string())
    print(f"\n{df['flag'].notna().sum()}/{len(df)} objects flagged")


if __name__ == "__main__":
    main()
