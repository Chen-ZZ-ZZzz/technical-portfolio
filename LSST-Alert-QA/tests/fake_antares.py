"""
The ANTARES v2 API serving the loci recorded in tests/data/antares_v2, byte for byte, with
the real antares-client on top: GET /v2/loci/<id> answers <id>.json, GET
/v2/loci/<id>/alerts answers <id>_alerts.json, anything else is a 404. A locus recorded
without its alerts is there for its tags only (the client asks for the alerts when they
are read, not before), so it cannot go through fetch_antares_locus whole.

Recorded 2026-11-13, as antares-client 2.0.2 asks (search.get_by_id, then locus.alerts),
each for what it is used for:
  ANT20262tl85caoepac  with its alerts: the locus with no science tag whose pipeline tags
                       come out of order, beside uniform_random_sample: the sorted lists,
                       uniform_random_sample as a pipeline tag, and the row through the
                       pipeline with no unknown tag (unknown_tags [], the summary's none).
  ANT2026sx3pbuillwqe  with its alerts: the locus with one science tag plus an unknown tag
                       (lantern_xgboost_t2.0.7_c0.95) whose row goes through the pipeline:
                       unknown_tags recorded beside a science tag, the summary's count and
                       its read-back from the CSV, and where the count sits on the summary
                       line. The flag-text drill (counting unknown tags from the flag text
                       instead of unknown_tags) and the placement drill (the count moved
                       after newest) rest on it.
  ANT20250fk7udcecmrt  microlensing_candidate as the locus's only science tag: PASS.
  ANT2026b0g1vijrn6ym  microlensing_candidate beside extragalactic (multiple_science_tags),
                       and lantern_xgboost_t2.0.7_c0.95 on a locus with science tags.
  ANT2022r8hygxeyd6qm  no science tag and two unknown tags: the order of the unknown list,
                       and lantern_xgboost_t2.0.7_c0.95 on a locus without science tags.
The tests assert what they need of a recording before relying on it, so a re-recording
whose tags changed fails there, never silently.
"""

import pathlib

import httpx

DATA = pathlib.Path(__file__).parent / "data" / "antares_v2"
API_BASE = "https://api.antares.noirlab.edu/v2/"
LOCI = "/v2/loci/"


def serve_recorded(monkeypatch) -> list[str]:
    """Route antares-client's HTTP client to the recordings. Returns the paths asked, in order."""
    import antares_client.search as search

    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        name = request.url.path.removeprefix(LOCI).replace("/alerts", "_alerts")
        body = DATA / f"{name}.json"
        if not request.url.path.startswith(LOCI) or "/" in name or not body.is_file():
            return httpx.Response(404, json={"detail": "not found"})
        return httpx.Response(200, content=body.read_bytes(), headers={"content-type": "application/json"})

    client = httpx.Client(base_url=API_BASE, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(search.api_client, "client", client)
    return paths


def served_tags(locus_id: str) -> list[str]:
    """A recorded locus's tags in the order the API served them, as fetch_antares_locus reads them."""
    from rubin_qa.antares_api import _search

    locus = _search().get_by_id(locus_id)
    return list(locus.tags)


def recorded_row(locus_id: str) -> dict:
    """A recorded locus through the pipeline's own fetch, validator, classifier and row builder."""
    from rubin_qa.antares_api import fetch_antares_locus
    from rubin_qa.classifier import classify_antares
    from rubin_qa.reporting import build_antares_qa_row
    from rubin_qa.validators import validate_antares

    data = fetch_antares_locus(locus_id)
    assert data["fetch_errors"] == [], f"{locus_id}: recorded without its alerts? {data['fetch_errors']}"
    return build_antares_qa_row(locus_id, data, validate_antares(data), classify_antares(data["tags"]))
