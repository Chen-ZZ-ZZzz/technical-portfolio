"""7) Normalize whitespace, but keep newlines
Goal: Replace runs of spaces/tabs with one space, but don’t destroy line breaks.
Input:
"hello\t\tworld\nthis   is\tok"
Output:
"hello world\nthis is ok"
Hints: character class [ \t]+."""

import re

def clr_white(bad):
    # print(f"This is the text needed to be normalized:\n{bad}")
    # good = re.sub(r"[ \t]+", " ", bad)
    # print(f"Thi is the normalized text:\n{good}")
    return re.sub(r"[ \t]+", " ", bad)
