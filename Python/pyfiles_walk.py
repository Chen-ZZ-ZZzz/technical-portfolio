from pathlib import Path
import os, shutil

"""
Exercise 1 — selective walk

Write a function that:
walks a directory tree
skips directories named .git, .venv, __pycache__
yields absolute Path objects of all .py files

Constraints:
use Path.walk(top_down=True)
prune using dirs
do not use rglob()

Deliverable:
def iter_python_files(base: Path): ...

One sentence explaining a design choice you made
(e.g. why you pruned dirs the way you did, why absolute paths, etc.)
"""

def iter_python_files(base):
    # i use the dirs list because it is already generated, so just pruned off what we dont want
    # loop through files for .py files. root is already Path object
    # couldnt use filter list comprehension to avoid overwriting
    SKIP = (".git", ".venv", "__pycache__")
    # py_path = []

    for root, dirs, files in Path(base).walk(top_down=True):
        dirs[:] = [d for d in dirs if d not in SKIP] # how to replace: [:]
        for f in files:
            if (root / f).suffix == ".py": # The suffix version is safer if you later deal with .pyw, .tar.py
                # py_path.append(root / f) # could i write this part shorter in one statement?
                yield root / f
    # Your function returns a list, which is fine.
    # But in industrial code, you usually want a generator unless you need the full list in memory.

    # return py_path


# pathg = (iter_python_files(test))                                          # lots
# print(list(pathg))
# for p in pathg:
#     print(p)
# for p in pathg:
#     print("SECOND PASS", p) # (nothing)
# print(list(pathg))            # empty[] because generator exhausted
# a generator is created but execution is not yet.
# print(iter_python_files(Path.home() / ".local/share/pipx/venvs/black")) # huge
# print(iter_python_files(Path.home() / ".local/share/doc")) # empty
