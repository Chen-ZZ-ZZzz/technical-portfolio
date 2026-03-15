import re

'''1) Extract domain names from emails
Goal: Return list of domains.
Input: ["a@b.com", "tom.smith@sub.example.co.uk", "bad@@x.com"]
Output: ["b.com", "sub.example.co.uk"] (ignore invalid)
'''
domain_input = ["a@b.com", "tom.smith@sub.example.co.uk", "bad@@x.com"]
domain_want = ["b.com", "sub.example.co.uk"]
domain_got = []

for x in domain_input:
    m = re.fullmatch(r"[^@\s]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)", x)
    if m:
        domain_got.append(m.group(1))
