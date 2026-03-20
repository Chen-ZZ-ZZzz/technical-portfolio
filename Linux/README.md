# Linux Tools -- Daily-Use CLI Utilities

Kleine CLI-Werkzeuge für den täglichen Einsatz unter Linux.

Small Python CLI tools for everyday file management on Linux.

---

## Skills Demonstrated

| Area | Details |
|------|---------|
| **CLI design** | `argparse` with short/long flags, dry-run by default, `epilog` examples |
| **Regex** | Named groups, backreferences, module-level compiled patterns |
| **File operations** | `Path.walk()`, `Path.rename()`, `rglob`; safe renaming with conflict detection |
| **Unicode handling** | Transliteration via `unidecode`; safe ASCII filename generation |
| **Error handling** | `parser.error()` for input validation; graceful skip on conflicts |

---

## Scripts

### Filename Sanitizer (`san.py`)
Sanitizes file and directory names for shell safety. Transliterates Unicode to ASCII (ae->ae, ss->ss, e->e), replaces unsafe characters with underscores, and collapses duplicates. Dry-run by default, recursive or shallow mode.

### File Renumber / Gap Insert (`re_index.py`)
Closes numbering gaps in prefixed files (e.g. `spam001`, `spam003` -> `spam001`, `spam002`) or inserts gaps to make room for new files.

### EU Date Renamer (`eu_date.py`)
Recursively renames files containing American-style dates (`MM-DD-YYYY`) to European-style (`DD-MM-YYYY`) using regex named groups.

### Whitespace Normalizer (`clr_white.py`)
Replaces runs of spaces and tabs with a single space while preserving newlines.
