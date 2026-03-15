# Python Scripts — Utilities & Exercises

Python scripts covering file system operations, text processing, regex, and CLI tooling.

---

## Skills Demonstrated

| Area | Details |
|------|---------|
| **Clean code & refactoring** | Single-responsibility functions; early returns to reduce nesting; dead code elimination |
| **Idiomatic Python** | `yield` / `yield from`; `zip(*matrix)`; ternary expressions; `sum()` / `any()` over manual accumulators; walrus operator `:=` |
| **Regex** | Named groups, backreferences, lookarounds, `re.VERBOSE`, `re.sub` with callables; module-level compiled patterns |
| **Data structures** | `frozenset` for O(1) lookup; `Counter`; `defaultdict`; dict keying to replace parallel variables |
| **Standard library** | `collections`, `pathlib`, `re`, `shutil`, `subprocess`, `argparse`, `dataclasses` |
| **File & path operations** | `shutil.copy`, `Path.rename`, `Path.with_name`, `Path.walk()`, `rglob`; safe cross-platform path handling |
| **CLI with argparse** | Usage examples via `epilog`; `parser.error()` for input validation; `--help` built-in throughout |
| **Error handling** | `try/except` at input boundaries; `OSError` guards; pure computation functions free of side effects |
| **Complexity awareness** | Identifying and fixing O(C·R²) → O(C·R); separating passes; early exit on match |
| **Code organisation** | Module-level constants in `UPPER_SNAKE_CASE`; type hints on all signatures; `__main__` guards throughout |

---

## Example Scripts

### Coin Toss Game (`coin_toss.py`)
A simple CLI coin toss game where the player gets two guesses.

### Streak Finder (`streak_finder.py`)
Runs 10,000 experiments, each flipping a coin 100 times, and calculates the probability of a streak of 6 identical results.

### Chess Board Validator (`chess_validator.py`)
Validates a dictionary representing a chess board, checking square names, piece names, colors, and legal piece counts.

### Inventory Manager (`inventory.py`)
Displays a formatted inventory and adds items from a loot list.

### Table Printer (`table_printer.py`)
Takes a list of lists of strings and prints a right-justified table with dynamic column widths.

### Password Strength Checker (`password_checker.py`)
Checks a password against rules (length, digit, uppercase, lowercase) and reports all failures at once.

### Custom `strip()` (`my_strip.py`)
Reimplements Python's built-in `str.strip()` using regular expressions.

### Directory Walker (`file_walker.py`)
Recursively walks a directory tree and yields absolute paths of all `.py` files, skipping `.git`, `.venv`, and `__pycache__`.

### Collatz Sequence (`collatz.py`)
Computes and prints the Collatz sequence from a user-supplied integer down to 1.

### Mad Libs (`madLibs.py`)
Reads a text file containing placeholder tokens (`NOUN`, `VERB`, etc.), prompts the user interactively, and saves the filled-in result as a new file.

### Copy by Extension (`my_cp.py`)
Recursively scans a source directory and copies files into separate destination folders organised by extension (`.pdf`, `.jpg`, etc.).

### Find Large Files (`find_large.py`)
Walks a directory and reports files and folders exceeding a configurable size threshold (default 100 MB).

### File Renumber / Gap Insert (`re_index.py`)
Closes numbering gaps in prefixed files (e.g. `spam001`, `spam003` → `spam001`, `spam002`) or inserts gaps to make room for new files.

### EU Date Renamer (`eu_date.py`)
Recursively renames files containing American-style dates (`MM-DD-YYYY`) to European-style (`DD-MM-YYYY`) using regex named groups.

### Email Domain Extractor (`email_domains.py`)
Extracts valid domain names from a list of email addresses, silently ignoring malformed inputs.

### German Phone Validator (`is_phone.py`)
Validates German phone numbers across formats: `+49`, `0049`, and national trunk `0` prefixes. Rejects letters, double plus, and trunk `0` after `+49`.

### Log Line Parser (`log_parse.py`)
Parses structured log lines into field dictionaries (`date`, `time`, `level`, `module`, `user`, `ip`).

### IPv4 Extractor (`find_ip.py`)
Extracts IPv4 addresses from text, validating each octet is 0–255 and rejecting leading zeros.

### Hashtag Extractor (`hash_out.py`)
Extracts hashtags from text without matching `C#` or mid-word `#` signs. Supports Unicode hashtags.

### Whitespace Normalizer (`clr_white.py`)
Replaces runs of spaces and tabs with a single space while preserving newlines.

### Secret Masker (`mask.py`)
Masks secret values (`password`, `token`, `api_key`) in config text across different separators. Skips comment lines. Optionally collects revealed values into a sink list.

### Date Format Converter (`datefix.py`)
Converts `DD.MM.YYYY` dates to ISO `YYYY-MM-DD` in a text blob without touching already-correct ISO dates.
