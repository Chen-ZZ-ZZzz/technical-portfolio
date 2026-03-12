import subprocess
import sys
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class HostResult:
    host: str
    passed: bool
    latency_ms: float | None = field(default=None)


def ping(host: str, count: int = 1, timeout: int = 2) -> HostResult:
    try:
        result = subprocess.run(
            ['ping', '-c', str(count), '-W', str(timeout), host],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            latency = _parse_latency(result.stdout)
            return HostResult(host=host, passed=True, latency_ms=latency)
    except FileNotFoundError:
        print("Error: 'ping' command not found.", file=sys.stderr)
        sys.exit(1)
    return HostResult(host=host, passed=False)


def _parse_latency(output: str) -> float | None:
    """Extract avg latency from ping output, e.g. 'rtt min/avg/max/mdev = 1.2/3.4/5.6/0.8 ms'"""
    for line in output.splitlines():
        if 'avg' in line and '=' in line:
            try:
                return float(line.split('=')[1].strip().split('/')[1])
            except (IndexError, ValueError):
                return None
    return None


def load_hosts(source: str) -> list[str]:
    """Accept a file path or a comma-separated list of hosts."""
    path = Path(source)
    if path.exists():
        return [h.strip() for h in path.read_text().splitlines() if h.strip() and not h.startswith('#')]
    return [h.strip() for h in source.split(',') if h.strip()]


def print_report(results: list[HostResult]) -> None:
    col = max(len(r.host) for r in results)
    header = f"{'HOST':<{col}}  {'STATUS':<6}  LATENCY"
    print(header)
    print('-' * len(header))
    for r in results:
        status = 'PASS' if r.passed else 'FAIL'
        latency = f"{r.latency_ms:.1f} ms" if r.latency_ms is not None else '-'
        print(f"{r.host:<{col}}  {status:<6}  {latency}")
    print('-' * len(header))
    passed = sum(r.passed for r in results)
    print(f"{passed}/{len(results)} hosts reachable")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: net_check.py <hosts_file | host1,host2,...>", file=sys.stderr)
        sys.exit(1)

    hosts = load_hosts(sys.argv[1])
    if not hosts:
        print("Error: no hosts found.", file=sys.stderr)
        sys.exit(1)

    results = [ping(host) for host in hosts]
    print_report(results)
    if any(not r.passed for r in results):
        sys.exit(1)


if __name__ == '__main__':
    main()
```

Example output:
```
HOST              STATUS  LATENCY
---------------------------------
google.com        PASS    12.3 ms
192.168.1.1       PASS     1.1 ms
10.0.0.99         FAIL    -
unreachable.host  FAIL    -
---------------------------------
2/4 hosts reachable
