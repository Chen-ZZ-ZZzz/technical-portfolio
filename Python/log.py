'''3) Parse log lines into fields
Input line examples:
2026-01-08 10:15:03 INFO auth user=alice ip=10.0.0.7
2026-01-08 10:15:09 ERROR db user=bob ip=192.168.1.10
Goal: Return dict: {date, time, level, module, user, ip}
'''

import os
from pathlib import Path

LOG_PAT = re.compile(
    r'''
    ^
    (?P<date>\d{4}-\d{2}-\d{2})\s+
    (?P<time>\d{2}:\d{2}:\d{2})\s+
    (?P<level>[A-Z]+)\s+
    (?P<module>[A-Za-z0-9_.-]{2,})\s+
    user=(?P<user>\w+)\s+
    ip=(?P<ip>(?:\d{1,3}\.){3}\d{1,3})
    $
    ''',
    re.X | re.A)
