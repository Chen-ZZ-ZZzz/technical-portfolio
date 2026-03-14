
# Python Exercises

A collection of Python exercises.

## Skills Demonstrated

| Area | Details |
|------|---------|
| **Clean code & refactoring** | Decomposing monolithic scripts into single-responsibility functions; eliminating redundancy; early returns to reduce nesting |
| **Idiomatic Python** | Generator functions with `yield` / `yield from`; `zip(*matrix)` transposition; ternary expressions; `sum()` / `any()` over manual accumulators |
| **Data structures** | Choosing `frozenset` for O(1) lookup; `Counter` for frequency counting; dict keying to replace parallel variables |
| **Standard library** | `collections.Counter`, `pathlib.Path.walk()`, `re.sub()` / `re.escape()`, `random.choice()` |
| **Complexity awareness** | Identifying and fixing O(C·R²) → O(C·R) in table formatting; separating width-calculation and printing passes |
| **Regex** | Compiled patterns, anchored character classes, `re.escape()` for safe dynamic pattern building |
| **Error handling** | `try/except ValueError` at input boundaries; keeping computation functions pure and exception-free |
| **Code organisation** | Module-level constants in `UPPER_SNAKE_CASE`; type hints on all signatures; `__main__` guards throughout |


## Example Scripts

### Collatz Sequence (`collatz.py`)

Computes and prints the Collatz sequence from a user-supplied integer down to 1.

**Concepts practiced:** recursion-like iteration, ternary expressions, `try/except`, `__main__` guard.

---

### Coin Toss Game (`coin_toss_guess.py`)

A simple CLI coin toss game where the player gets two guesses.

**Concepts practiced:** input validation loops, function decomposition, `__main__` guard, f-strings.

---

### Streak Finder (`streak_chance.py`)

Runs 10,000 experiments, each flipping a coin 100 times, and calculates the probability of a streak of 6 identical results.

**Concepts practiced:** simulation, generator expressions, constants, complexity analysis.

---

### Chess Board Validator (`chess_validator.py`)

Validates a dictionary representing a chess board, checking square names, piece names, colors, and legal piece counts.

**Concepts practiced:** dict comprehensions, input validation, set lookups, early returns.

---

### Inventory Manager (`inventory.py`)

Displays a formatted inventory and adds items from a loot list.

**Concepts practiced:** dict methods, `collections.Counter`, generator expressions.

---

### Table Printer (`tab_printer.py`)

Takes a list of lists of strings and prints a right-justified table.

**Concepts practiced:** `zip(*matrix)` transposition, list comprehensions, two-pass algorithms, complexity analysis.

---

### Custom `strip()` (`strip_reg.py`)

Reimplements Python's built-in `str.strip()` using regular expressions.

**Concepts practiced:** `re.sub`, raw f-strings, optional arguments, `re.escape`.

---

### Directory Walker (`pyfiles_walk.py`)

Recursively walks a directory tree and yields absolute paths of all `.py` files, skipping `.git`, `.venv`, and `__pycache__`.

**Concepts practiced:** `pathlib.Path.walk()`, generators, `yield from`, `frozenset`, in-place list mutation.

---# Python Exercises — Practice & Refactoring

A collection of Python exercises focused on clean code, idiomatic patterns, and refactoring. Each script was written, reviewed, and improved with attention to readability, correctness, and performance.

---

## Skills Demonstrated

| Area | Details |
|------|---------|
| **Clean code & refactoring** | Decomposing monolithic scripts into single-responsibility functions; eliminating redundancy; early returns to reduce nesting |
| **Idiomatic Python** | Generator functions with `yield` / `yield from`; `zip(*matrix)` transposition; ternary expressions; `sum()` / `any()` over manual accumulators |
| **Data structures** | Choosing `frozenset` for O(1) lookup; `Counter` for frequency counting; dict keying to replace parallel variables |
| **Standard library** | `collections.Counter`, `pathlib.Path.walk()`, `re.sub()` / `re.escape()`, `random.choice()` |
| **Complexity awareness** | Identifying and fixing O(C·R²) → O(C·R) in table formatting; separating width-calculation and printing passes |
| **Regex** | Compiled patterns, anchored character classes, `re.escape()` for safe dynamic pattern building |
| **Error handling** | `try/except ValueError` at input boundaries; keeping computation functions pure and exception-free |
| **Code organisation** | Module-level constants in `UPPER_SNAKE_CASE`; type hints on all signatures; `__main__` guards throughout |
| **CLI with argparse** | `argparse` with usage examples via `epilog`; `parser.error()` for clean input validation; `--help` built-in |
| **File & path operations** | `shutil.copy`, `Path.rename`, `Path.with_name`, `Path.walk()`, `rglob`; safe cross-platform path handling |

---

## Example Scripts

### Coin Toss Game (`coin_toss.py`)

A simple CLI coin toss game where the player gets two guesses.

**Concepts practiced:** input validation loops, function decomposition, `__main__` guard, f-strings.

---

### Streak Finder (`streak_finder.py`)

Runs 10,000 experiments, each flipping a coin 100 times, and calculates the probability of a streak of 6 identical results.

**Concepts practiced:** simulation, generator expressions, constants, complexity analysis.

---

### Chess Board Validator (`chess_validator.py`)

Validates a dictionary representing a chess board, checking square names, piece names, colors, and legal piece counts.

**Concepts practiced:** dict comprehensions, input validation, set lookups, early returns.

---

### Inventory Manager (`inventory.py`)

Displays a formatted inventory and adds items from a loot list.

**Concepts practiced:** dict methods, `collections.Counter`, generator expressions.

---

### Table Printer (`table_printer.py`)

Takes a list of lists of strings and prints a right-justified table.

**Concepts practiced:** `zip(*matrix)` transposition, list comprehensions, two-pass algorithms, complexity analysis.

---

### Password Strength Checker (`password_checker.py`)

Checks a password against rules (length, digit, uppercase, lowercase) and reports all failures.

**Concepts practiced:** `re` module, compiled patterns, list comprehensions, f-strings.

---

### Custom `strip()` (`my_strip.py`)

Reimplements Python's built-in `str.strip()` using regular expressions.

**Concepts practiced:** `re.sub`, raw f-strings, optional arguments, `re.escape`.

---

### Directory Walker (`file_walker.py`)

Recursively walks a directory tree and yields absolute paths of all `.py` files, skipping `.git`, `.venv`, and `__pycache__`.

**Concepts practiced:** `pathlib.Path.walk()`, generators, `yield from`, `frozenset`, in-place list mutation.

---

### Collatz Sequence (`collatz.py`)

Computes and prints the Collatz sequence from a user-supplied integer down to 1.

**Concepts practiced:** recursion-like iteration, ternary expressions, `try/except`, `__main__` guard.

---

### Mad Libs (`madLibs.py`)

Reads a text file containing placeholder tokens (`NOUN`, `VERB`, etc.), prompts the user interactively, and saves the filled-in result as a new file.

**Concepts practiced:** `re.sub` with a callable, `Path.read_text` / `write_text`, `argparse`.

---

### Copy by Extension (`my_cp.py`)

Recursively scans a source directory and copies files into separate destination folders organised by extension (`.pdf`, `.jpg`, etc.).

**Concepts practiced:** `shutil.copy`, `rglob`, dict-based extension routing, `argparse`.

---

### Find Large Files (`find_large.py`)

Walks a directory and reports files and folders exceeding a configurable size threshold (default 100 MB).

**Concepts practiced:** `Path.walk()`, `dir_size()` recursion, `follow_symlinks=False`, `OSError` guard, `--mb` flag via `argparse`.

---

### File Renumber / Gap Insert (`re_index.py`)

Closes numbering gaps in prefixed files (e.g. `spam001`, `spam003` → `spam001`, `spam002`) or inserts gaps to make room for new files.

**Concepts practiced:** `Path.glob`, `isdigit()`, `zfill()`, descending sort to avoid overwrite conflicts, `argparse`.

---

### EU Date Renamer (`eu_date.py`)

Recursively renames files containing American-style dates (`MM-DD-YYYY`) to European-style (`DD-MM-YYYY`) using regex named groups.

**Concepts practiced:** `re.compile` with named groups, `re.sub` with callable, `Path.walk(top_down=False)`, `Path.with_name`, `argparse`.

---
