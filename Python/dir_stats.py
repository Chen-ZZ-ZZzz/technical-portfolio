"""
Level 5 — Scale & performance awareness
Exercise 8: file statistics without Path explosion

Compute:
number of files
total size
largest file

Constraints:
must work on very large trees
minimize Path object creation
explain why you structured it that way

This is where performance awareness matters.
"""

import os

def dir_stats(root: Path):
    number = 0
    total = 0                   # total size
    large = 0                   # largest size
    name = None                 # path of the largest file

    root = Path(root)
    if not root.is_dir():
        raise ValueError("Source must be a directory")

    for dir, _, files in os.walk(root): # this is more ecomomy?
        for file in files:
            fullpath = os.path.join(dir, file)

            try:
                st = os.stat(fullpath)
            except OSError:
                continue        # skill bad files

            number += 1
            size = st.st_size
            total += size

            if size > large:
                large = size
                name = fullpath

    total = total / (1024 * 1024) # MB
    large = large / (1024 * 1024)

    return (
        f"There are {number} files in {root}.\n"
        f"Total size: {total:.2f} MB\n"
        f"Largest file: {name} ({large:.2f} MB)"
)
