
# Python Projects

This folder contains Python scripts demonstrating:

- Automation and utility scripts
- Data parsing and processing
- Test automation utilities


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

---
