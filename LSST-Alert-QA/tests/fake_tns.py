"""
ALeRCE's TNS service as it really answers: bodies recorded verbatim on 2026-11-07
(tests/data/tns/), served through requests.post, so a test runs the real tns_once. A fake
that hands tns_once's result to its callers cannot show what tns_once does with a body:
that is how a rejected call read as "not in TNS" for weeks (README, "Replay Validation").
"""

import json
from pathlib import Path
from types import SimpleNamespace

from rubin_qa import tns as t

BODIES = Path(__file__).parent / "data" / "tns"
RECORD = (BODIES / "record.json").read_text()  # SN 2026afma, at the alert position in test_transient_monitor
NONE = (BODIES / "none.json").read_text()
REJECTED = (BODIES / "rejected.json").read_text()
UPSTREAM_ERROR = (BODIES / "upstream_error.json").read_text()


def response(body: str) -> SimpleNamespace:
    return SimpleNamespace(status_code=200, text=body, raise_for_status=lambda: None, json=lambda: json.loads(body))


class Clock:
    """time.sleep and time.monotonic as one fake clock. An ask takes no time unless a test advances it."""

    def __init__(self):
        self.t, self.slept = 1000.0, []

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds

    def monotonic(self) -> float:
        return self.t


def fake_clock(monkeypatch) -> Clock:
    clock = Clock()
    monkeypatch.setattr(t.time, "sleep", clock.sleep)
    monkeypatch.setattr(t.time, "monotonic", clock.monotonic)
    return clock


def serve(monkeypatch, *answers) -> list[dict]:
    """requests.post answering with these in turn, the last one repeating: a body, or an exception to raise. Returns the asks."""
    asked: list[dict] = []

    def post(url, json=None, timeout=None):
        assert url == t.ALERCE_TNS_URL, f"unexpected POST {url}"
        answer = answers[min(len(asked), len(answers) - 1)]
        asked.append(json)
        if isinstance(answer, Exception):
            raise answer
        return response(answer)

    monkeypatch.setattr(t.requests, "post", post)
    return asked
