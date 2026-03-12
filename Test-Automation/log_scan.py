import re
import sys
from pathlib import Path
from collections import defaultdict

LEVELS = ("ERROR", "WARN")
LOG_PATTERN = re.compile(r'\b(ERROR|WARN)\b')


def scan_file(path: Path) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    with path.open(errors='replace') as f:
        for line in f:
            if match := LOG_PATTERN.search(line):
                counts[match.group(1)] += 1
    return counts


def scan_dir(base: Path) -> dict[Path, dict[str, int]]:
    return {
        path: scan_file(path)
        for path in sorted(base.rglob('*.log'))
    }


def print_summary(results: dict[Path, dict[str, int]]) -> None:
    col = max((len(str(p)) for p in results), default=20)
    header = f"{'FILE':<{col}}  " + "  ".join(f"{lv:>6}" for lv in LEVELS)
    print(header)
    print("-" * len(header))

    totals: dict[str, int] = defaultdict(int)
    for path, counts in results.items():
        row = f"{str(path):<{col}}  " + "  ".join(f"{counts.get(lv, 0):>6}" for lv in LEVELS)
        print(row)
        for lv in LEVELS:
            totals[lv] += counts.get(lv, 0)

    print("-" * len(header))
    print(f"{'TOTAL':<{col}}  " + "  ".join(f"{totals[lv]:>6}" for lv in LEVELS))


def main() -> None:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('.')
    if not target.exists():
        print(f"Error: '{target}' does not exist.", file=sys.stderr)
        sys.exit(1)

    paths = [target] if target.is_file() else None
    results = (
        {target: scan_file(target)} if target.is_file()
        else scan_dir(target)
    )

    if not results:
        print("No .log files found.")
        return

    print_summary(results)


if __name__ == '__main__':
    main()
```

Example output:
```
FILE                          ERROR    WARN
----------------------------------------
logs/app.log                      3       7
logs/worker.log                   1       0
logs/db.log                       0       2
----------------------------------------
TOTAL                             4       9
