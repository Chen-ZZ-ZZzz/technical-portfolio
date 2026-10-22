"""
net_check.py -- ping and port-check hosts, report PASS/FAIL with latency
=========================================================================
Usage:
    python3 net_check.py hosts.txt
    python3 net_check.py google.com,1.1.1.1
    python3 net_check.py hosts.txt --port 22 443
    python3 net_check.py hosts.txt -o json
    python3 net_check.py hosts.txt -o csv > results.csv
    python3 net_check.py hosts.txt -o csv --save

Host file format (one host per line, # comments allowed):
    google.com
    192.168.1.1
    # lab switch -- offline for maintenance
    10.0.0.5
"""

import argparse
import csv
import io
import json
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from common import (
    _color_status,
    _green,
    _red,
    report_timestamp,
    save_report,
)


# -- Data classes --


@dataclass
class PortResult:
    port: int
    open: bool
    latency_ms: float | None = None


@dataclass
class HostResult:
    host: str
    ping_passed: bool
    ping_latency_ms: float | None = None
    port_results: list[PortResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """Host passes if ping succeeds. Port results are informational."""
        return self.ping_passed


# -- Checks --


def ping(host: str, count: int = 1, timeout: int = 2) -> tuple[bool, float | None]:
    """ICMP ping. Returns (success, latency_ms)."""
    try:
        result = subprocess.run(
            ["ping", "-c", str(count), "-W", str(timeout), host],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return True, _parse_latency(result.stdout)
    except FileNotFoundError:
        print("Error: 'ping' command not found.", file=sys.stderr)
        sys.exit(1)
    return False, None


def _parse_latency(output: str) -> float | None:
    """Extract avg latency from 'rtt min/avg/max/mdev = .../AVG/...' line."""
    for line in output.splitlines():
        if "avg" in line and "=" in line:
            try:
                return float(line.split("=")[1].strip().split("/")[1])
            except (IndexError, ValueError):
                return None
    return None


def check_port(host: str, port: int, timeout: float = 2.0) -> PortResult:
    """TCP connect to host:port. Returns PortResult with open status and latency."""
    try:
        start = time.monotonic()
        with socket.create_connection((host, port), timeout=timeout):
            elapsed = (time.monotonic() - start) * 1000
            return PortResult(port=port, open=True, latency_ms=round(elapsed, 1))
    except (OSError, socket.timeout):
        return PortResult(port=port, open=False)


def check_host(host: str, ports: list[int], timeout: int = 2) -> HostResult:
    """Run ping + optional port checks for one host."""
    passed, latency = ping(host, timeout=timeout)
    port_results = [check_port(host, p, timeout=timeout) for p in ports]
    return HostResult(
        host=host,
        ping_passed=passed,
        ping_latency_ms=latency,
        port_results=port_results,
    )


def load_hosts(source: str) -> list[str]:
    """Accept a file path or a comma-separated list of hosts."""
    path = Path(source)
    if path.exists():
        if not path.is_file():
            print(f"Error: '{source}' is not a regular file.", file=sys.stderr)
            sys.exit(1)
        try:
            text = path.read_text(encoding="utf-8")
        except PermissionError:
            print(f"Error: no permission to read '{source}'.", file=sys.stderr)
            sys.exit(1)
        except OSError as e:
            print(f"Error reading '{source}': {e}", file=sys.stderr)
            sys.exit(1)
        return [
            h.strip()
            for h in text.splitlines()
            if h.strip() and not h.startswith("#")
        ]
    return [h.strip() for h in source.split(",") if h.strip()]


# -- Output formatters --


def _fmt_latency(ms: float | None) -> str:
    return f"{ms:.1f} ms" if ms is not None else "-"


def _print_table(results: list[HostResult], ports: list[int],
                 file=None, plain: bool = False) -> None:
    """Human-readable table. plain=True disables ANSI colors (for saving)."""
    p = lambda *a, **kw: print(*a, **kw, file=file)
    color = (lambda s, ok: s) if plain else _color_status

    p(f"Report: {report_timestamp()}\n")
    host_col = max(len(r.host) for r in results)

    # Header
    parts = [f"{'HOST':<{host_col}}  {'PING':<6}  {'LATENCY':<10}"]
    for port in ports:
        parts.append(f"  :{port:<5}  {'LAT':<10}")
    parts.append("  RESULT")
    header = "".join(parts)
    p(header)
    p("-" * len(header))

    # Rows
    for r in results:
        ping_label = "OK" if r.ping_passed else "FAIL"
        line = f"{r.host:<{host_col}}  {color(f'{ping_label:<6}', r.ping_passed)}  {_fmt_latency(r.ping_latency_ms):<10}"
        for pr in r.port_results:
            port_label = "OPEN" if pr.open else "CLOSED"
            line += f"  {color(f'{port_label:<6}', pr.open)}  {_fmt_latency(pr.latency_ms):<10}"
        result_label = "PASS" if r.passed else "FAIL"
        line += f"  {color(result_label, r.passed)}"
        p(line)

    p("-" * len(header))
    passed = sum(r.passed for r in results)
    p(f"{passed}/{len(results)} hosts healthy")


def _format_json(results: list[HostResult]) -> str:
    """JSON output for programmatic consumption."""
    data = {
        "timestamp": report_timestamp(),
        "results": [],
    }
    for r in results:
        entry = {
            "host": r.host,
            "ping": r.ping_passed,
            "ping_latency_ms": r.ping_latency_ms,
            "passed": r.passed,
        }
        if r.port_results:
            entry["ports"] = [
                {"port": pr.port, "open": pr.open, "latency_ms": pr.latency_ms}
                for pr in r.port_results
            ]
        data["results"].append(entry)
    return json.dumps(data, indent=2)


def _format_csv(results: list[HostResult], ports: list[int]) -> str:
    """CSV output for spreadsheets and CI artifact collection."""
    buf = io.StringIO()
    fieldnames = ["host", "ping", "ping_latency_ms"]
    for p in ports:
        fieldnames += [f"port_{p}", f"port_{p}_latency_ms"]
    fieldnames.append("result")

    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for r in results:
        row = {
            "host": r.host,
            "ping": "OK" if r.ping_passed else "FAIL",
            "ping_latency_ms": r.ping_latency_ms or "",
            "result": "PASS" if r.passed else "FAIL",
        }
        for pr in r.port_results:
            row[f"port_{pr.port}"] = "OPEN" if pr.open else "CLOSED"
            row[f"port_{pr.port}_latency_ms"] = pr.latency_ms or ""
        writer.writerow(row)
    return buf.getvalue()


# -- CLI --


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ping and port-check hosts, report PASS/FAIL with latency.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 net_check.py hosts.txt\n"
            "  python3 net_check.py google.com,1.1.1.1\n"
            "  python3 net_check.py hosts.txt --port 22 443\n"
            "  python3 net_check.py hosts.txt -o json\n"
            "  python3 net_check.py hosts.txt -o csv > results.csv\n"
            "  python3 net_check.py hosts.txt -o csv --save"
        ),
    )
    parser.add_argument(
        "source", help="Host file or comma-separated list of hosts"
    )
    parser.add_argument(
        "--port", "-p", type=int, nargs="+", default=[], metavar="PORT",
        help="TCP ports to check in addition to ICMP ping",
    )
    parser.add_argument(
        "--output", "-o", choices=["table", "json", "csv"], default="table",
        help="Output format (default: table)",
    )
    parser.add_argument(
        "--save", "-s", action="store_true",
        help="Save report to reports/ with timestamped filename",
    )
    parser.add_argument(
        "--timeout", "-t", type=int, default=2,
        help="Timeout in seconds for ping and port checks (default: 2)",
    )
    args = parser.parse_args()

    hosts = load_hosts(args.source)
    if not hosts:
        print("Error: no hosts found.", file=sys.stderr)
        sys.exit(1)

    results = [check_host(h, ports=args.port, timeout=args.timeout) for h in hosts]

    if args.output == "json":
        output = _format_json(results)
    elif args.output == "csv":
        output = _format_csv(results, args.port)
    else:
        _print_table(results, args.port)
        output = None

    if output is not None and not args.save:
        print(output, end="" if args.output == "csv" else "\n")

    if args.save:
        save_fmt = args.output if args.output != "table" else "json"
        if output is None or save_fmt != args.output:
            if save_fmt == "json":
                save_content = _format_json(results)
            elif save_fmt == "csv":
                save_content = _format_csv(results, args.port)
            else:
                buf = io.StringIO()
                _print_table(results, args.port, file=buf, plain=True)
                save_content = buf.getvalue()
        else:
            save_content = output
        saved = save_report(save_content, save_fmt, "net_check")
        if saved:
            print(f"Report saved: {saved}", file=sys.stderr)

    if any(not r.passed for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
