import json
import argparse
import sys
from datetime import datetime
from pathlib import Path

# real journal json entry example
# {
# 	"_SYSTEMD_UNIT" : "session-c47.scope",
# 	"__SEQNUM_ID" : "b9465960115f4070a667884cca1fd826",
# 	"SYSLOG_TIMESTAMP" : "Aug 28 07:41:04 ",
# 	"_SOURCE_REALTIME_TIMESTAMP" : "1787895664352613",
# 	"_EXE" : "/usr/sbin/lightdm",
# 	"SYSLOG_IDENTIFIER" : "lightdm",
# 	"_GID" : "0",
# 	"_MACHINE_ID" : "949fb594c0ea4321826d7eacc74c06cb",
# 	"PRIORITY" : "3",
# 	"__SEQNUM" : "784154",
# 	"_BOOT_ID" : "2987136d41a64e209a506b5aa870bd56",
# 	"_SYSTEMD_USER_SLICE" : "-.slice",
# 	"__REALTIME_TIMESTAMP" : "1787895664352627",
# 	"_CAP_EFFECTIVE" : "1ffffffffff",
# 	"__CURSOR" : "s=b9465960115f4070a667884cca1fd826;i=bf71a;b=2987136d41a64e209a506b5aa870bd56;m=3080b49a71;t=65a14e5f81d73;x=17c75ab63b8b8041",
# 	"_SYSTEMD_SESSION" : "c47",
# 	"MESSAGE" : "pam_systemd(lightdm-greeter:session): Failed to release session: Transport endpoint is not connected",
# 	"_SYSTEMD_SLICE" : "user-106.slice",
# 	"_RUNTIME_SCOPE" : "system",
# 	"_PID" : "181820",
# 	"_SYSTEMD_INVOCATION_ID" : "cce5e53175a841eea5bcd7479af3f583",
# 	"_COMM" : "lightdm",
# 	"__MONOTONIC_TIMESTAMP" : "208317749873",
# 	"_TRANSPORT" : "syslog",
# 	"_SELINUX_CONTEXT" : "unconfined\n",
# 	"_CMDLINE" : "lightdm --session-child 19 24",
# 	"_SYSTEMD_OWNER_UID" : "106",
# 	"SYSLOG_FACILITY" : "10",
# 	"_UID" : "0",
# 	"_SYSTEMD_CGROUP" : "/user.slice/user-106.slice/session-c47.scope"
# }

_SERVICE = "checker-journal-scan.service"

_PRIO_LEVELS: dict[str | None, str] = {
    "0": "ERROR",   # emerg
    "1": "ERROR",   # alert
    "2": "ERROR",   # crit
    "3": "ERROR",   # err
    "4": "WARN",    # warning
}


def _prio_conv(prio: str | None) -> str | None:
    """Map journal PRIORITY to a log_scan level keyword, or None if not of interest."""
    return _PRIO_LEVELS.get(prio)


def _msg_conv(msg: str | list[int] | None) -> str | None:
    """Return MESSAGE as text; None if absent or malformed."""
    if isinstance(msg, str):
        return msg or None
    if isinstance(msg, list):
        try:
            return bytes(msg).decode("utf-8", errors="replace")
        except (ValueError, TypeError) as e:
            print(f"[SKIPPED MESSAGE] ({e})", file=sys.stderr)
            return None
    return None


def _rt_conv(rt_stamp: str) -> str:
    """ Convert realtime timestamp to ISO datetime"""
    rt = int(rt_stamp) / 1000000 # in second
    time_obj = datetime.fromtimestamp(rt)
    return f"{time_obj}"


def _own_out(unit: str | None) -> bool:
    """True if the record came from this service's own output."""
    return unit == _SERVICE


def convert_record(line: str) -> str | None:
    """Convert one journal json entry to a log_scan readable line, or None"""
    try:
        entry = json.loads(line)
    except json.JSONDecodeError:
        return None

    if _own_out(entry.get("_SYSTEMD_USER_UNIT")):
        return None

    lvl = _prio_conv(entry.get("PRIORITY"))
    if lvl is None:
        return None

    msg = _msg_conv(entry.get("MESSAGE"))
    if msg is None:
        return None

    ts = _rt_conv(entry.get("__REALTIME_TIMESTAMP"))
    return f"{ts} {lvl}: {msg}"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert journal JSON entry to log-like text for log_scan"
    )
    parser.add_argument(
        "jjson", type=Path, help=".json input, from journalctl -o json"
    )
    parser.add_argument(
        "jlog", type=Path, help=".log output"
    )

    args = parser.parse_args(argv)
    if not args.jjson.exists():
        parser.error(f"{args.jjson} does not exist.")
    return args


def main(argv=None) -> None:
    args = _parse_args(argv)
    logs = []

    with args.jjson.open(encoding="utf-8") as f:
        for line in f:
            converted = convert_record(line)
            if converted is not None:
                logs.append(converted)

    tmp = args.jlog.with_name(args.jlog.name + ".tmp")
    tmp.write_text("".join(f"{line}\n" for line in logs), encoding="utf-8")
    tmp.rename(args.jlog)


if __name__ == "__main__":
    main()
