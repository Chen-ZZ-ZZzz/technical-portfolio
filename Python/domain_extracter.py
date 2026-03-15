import re

for x in domain_input:
    m = re.fullmatch(r"[^@\s]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)", x)
    if m:
        domain_got.append(m.group(1))
