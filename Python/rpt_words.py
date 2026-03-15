"""6) Find repeated words (case-insensitive)
Text: "This is is a test. That that is fine."
Goal: return list of tuples: [(word, start_index), ...]
Hints: backreferences \1, flags re.I."""

import re

def rpt(txt: str) -> list:
    out = []
    prev = None
    # totally gpt5.2. this ver. avoids indexing trap
    # and cleaner

    for m in re.finditer(r"\w+", txt):
        word = m.group(0).lower()
        if word == prev:
            out.append((word, m.start()))
        prev = word

    return out
