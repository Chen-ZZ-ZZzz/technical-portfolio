'''4) Find all IPv4 addresses and validate range
Goal: Extract IPs, but only keep those where each octet is 0–255.
Regex will find candidates; Python code validates.
Hints: \b, finditer, split by ".".'''

import re

def find_ip(addr: str):

    ip_match = re.finditer(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', addr, re.A)
    # print(ip_match)
    ip_got = []

    for m in ip_match:
        x = m.group(0).split('.')
        # below in comment is my original. gpt 5.2 adds a digits check, and a cleaner loop
        # if 0 <= int(x[0]) <= 255 and 0 <= int(x[1]) <= 255 and 0 <= int(x[2]) <= 255 and 0 <= int(x[3]) <= 255:
        try:
            nums = [int(p) for p in x]
        except ValueError:
            continue
        if any(p != "0" and p.startswith("0") for p in x): # this part added also by gpt, reject octets with leading zeros
            continue
        if all(0 <= n <= 255 for n in nums):
            ip_got.append(m.group(0))

    return ip_got
