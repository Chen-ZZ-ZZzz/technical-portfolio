"""8) Mask secrets in config text
Mask values for keys like password=..., token: ..., api_key ...
Keep key, replace value with ***
Work across different separators = : whitespace

import re

def mask(txt, sink=None):
    pat = re.compile(
        r"""
        (?P<key>password|token|api_key)
        (?P<sep>[= :]+)
        (?P<value>
            "[^"]*"         # quoted value
           | [^\s#]+)""",            # or unquoted, stop at space or #
        re.MULTILINE | re.X)

    def rep(ma) -> str:
        va = ma.group("value")
        if len(va) >= 2 and va[0] == '"' and va[-1] == '"':
            va = va[1:-1]

        # gpt if not None, but i think it should be stored in any rate
        if sink is not None:
            sink.append((ma.group("key"), va))
        return f"{ma.group('key')}{ma.group('sep')}***"

    newlist = [pat.sub(rep, f) if not f.strip().startswith("#") else f for f in txt.splitlines()]
    return "\n".join(newlist)
