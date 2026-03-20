# Python Scripts -- Utilities & Exercises

Python-Skripte zu Dateisystem, Textverarbeitung, Regex und CLI-Tooling.

Python scripts covering file system operations, text processing, regex, and CLI tooling. All scripts are importable as modules and usable as standalone CLI tools.

---

## Skills Demonstrated

| Area | Details |
|------|---------|
| **Clean code & refactoring** | Single-responsibility functions; early returns to reduce nesting; dead code elimination |
| **Idiomatic Python** | `yield` / `yield from`; `zip(*matrix)`; ternary expressions; `sum()` / `any()` over manual accumulators; walrus operator `:=` |
| **Regex** | Named groups, backreferences, lookarounds, `re.VERBOSE`, `re.sub` with callables; module-level compiled patterns |
| **Data structures** | `frozenset` for O(1) lookup; `Counter`; `defaultdict`; dict keying to replace parallel variables |
| **Standard library** | `collections`, `pathlib`, `re`, `shutil`, `subprocess`, `argparse`, `dataclasses`, `tempfile`, `os` |
| **File & path operations** | `shutil.copy2`, `Path.rename`, `Path.with_name`, `Path.walk()`, `rglob`; safe cross-platform path handling; atomic writes with `tempfile` + `fsync` |
| **CLI with argparse** | Usage examples via `epilog`; `parser.error()` for input validation; `--help` built-in throughout |
| **Error handling** | `try/except` at input boundaries; `OSError` guards; `on_error` callbacks for `Path.walk()`; encoding fallback (UTF-8 -> latin-1); pure computation functions free of side effects |
| **Performance awareness** | `os.walk()` / `os.stat()` with raw strings for large trees; `os.scandir()` stat caching; minimizing Path object creation at scale |
| **Atomic file operations** | POSIX atomic write pattern (temp + fsync + rename + dir fsync); temp file cleanup on failure; permission preservation |
| **Complexity awareness** | Identifying and fixing O(C*R^2) -> O(C*R); separating passes; early exit on match |
| **Code organisation** | Module-level constants in `UPPER_SNAKE_CASE`; type hints on all signatures; `__main__` guards throughout |

---

## `basics/`

### Coin Toss Game (`coin_toss.py`)
A simple CLI coin toss game where the player gets two guesses.

### Streak Finder (`streak_chance.py`)
Runs 10,000 experiments, each flipping a coin 100 times, and calculates the probability of a streak of 6 identical results.

### Chess Board Validator (`chess_validator.py`)
Validates a dictionary representing a chess board, checking square names, piece names, colors, and legal piece counts.

### Inventory Manager (`inventory.py`)
Displays a formatted inventory and adds items from a loot list.

### Table Printer (`tab_printer.py`)
Takes a list of lists of strings and prints a right-justified table with dynamic column widths.


### Custom `strip()` (`strip_reg.py`)
Reimplements Python's built-in `str.strip()` using regular expressions.

### Collatz Sequence (`collatz.py`)
Computes and prints the Collatz sequence from a user-supplied integer down to 1.

### Mad Libs (`madLibs.py`)
Reads a text file containing placeholder tokens (`NOUN`, `VERB`, etc.), prompts the user interactively, and saves the filled-in result as a new file.

---

## `regex/`

### Email Domain Extractor (`email_domains.py`)
Extracts valid domain names from a list of email addresses, silently ignoring malformed inputs.

### German Phone Validator (`is_phone.py`)
Validates German phone numbers across formats: `+49`, `0049`, and national trunk `0` prefixes. Rejects letters, double plus, and trunk `0` after `+49`.

### Log Line Parser (`log_parse.py`)
Parses structured log lines into field dictionaries (`date`, `time`, `level`, `module`, `user`, `ip`).

### IPv4 Extractor (`find_ip.py`)
Extracts IPv4 addresses from text, validating each octet is 0-255 and rejecting leading zeros.

### Hashtag Extractor (`hash_out.py`)
Extracts hashtags from text without matching `C#` or mid-word `#` signs. Supports Unicode hashtags.

### Secret Masker (`mask.py`)
Masks secret values (`password`, `token`, `api_key`) in config text across different separators. Skips comment lines. Optionally collects revealed values into a sink list.

### Date Format Converter (`datefix.py`)
Converts `DD.MM.YYYY` dates to ISO `YYYY-MM-DD` in a text blob without touching already-correct ISO dates.

### Repeated Words Finder (`rpt_words.py`)
Find repeated consecutive words (case-insensitive) in text.

---

## `filesystem/`

### Directory Walker (`file_walker.py`)
Recursively walks a directory tree and yields absolute paths of all `.py` files, skipping `.git`, `.venv`, and `__pycache__`.

### Copy by Extension (`cp_ext.py`)
Recursively scans a source directory and copies files into separate destination folders organised by extension (`.pdf`, `.jpg`, etc.).

### Find Large Files (`find_large.py`)
Walks a directory and reports files and folders exceeding a configurable size threshold (default 100 MB).

### Empty Directory Detector (`leer_dir.py`)
Finds directories containing no files and no subdirectories. Yields results sorted lexicographically. Handles permission errors via `on_error` callback.

### Log File Grep (`log_reader.py`)
Searches `.txt` and `.log` files for regex matches, streaming line by line. Falls back from UTF-8 to latin-1 on decode errors. Outputs `path:lineno:line` in grep style.

### Atomic Text Writer (`atom_write.py`)
Writes data to a file using the POSIX atomic write pattern: temp file + fsync + rename + directory fsync. Cleans up temp file on failure. Creates parent directories as needed.

### Config Updater (`config_update.py`)
Updates or inserts a `key=value` pair in a config file. Preserves other lines, file permissions, and writes atomically. Detects and rejects duplicate keys.

### Safe Recursive Copy (`copy_tree.py`)
Copies a directory tree without overwriting existing files. Fails fast on conflicts, preserves permissions and timestamps, skips symlinks.

### Directory Statistics (`dir_stats.py`)
Computes file count, total size, and largest file for a directory tree. Uses `os.walk()` and `os.stat()` with raw strings to minimize object creation for large trees.
