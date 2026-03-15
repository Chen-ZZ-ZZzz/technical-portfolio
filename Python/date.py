"""9) Convert dates
Convert DD.MM.YYYY to YYYY-MM-DD in a text blob, but don’t touch already-correct ISO dates.
Hints: groups + replacement r"\3-\2-\1".
Also use negative lookbehind or “only match with dots”.
"""
import re

def datefix(txt):
    pat = re.compile(
        r"(?<![A-Za-z0-9_-])(?P<day>\d{2})\.(?P<month>\d{2})\.(?P<year>\d{4})(?![A-Za-z0-9_-])"
        , re.A | re.M)
    def repl(mat):
        return f'{mat.group("year")}-{mat.group("month")}-{mat.group("day")}' # this is is r"\3-\2-\1"

    return pat.sub(repl, txt)
