"""
Tests for the ANTARES broker pipeline: client → validator → classifier → row → run.

The ALeRCE path had unit coverage from the start; ANTARES only had the ceiling and
CLI tests, which exercise the loop but never the locus model itself. What is checked
here is mostly the places where ANTARES differs from ALeRCE and where a silent wrong
answer is possible: the ANT/ZTF id routing, the upper-limit filter, num_mag_values
winning over len(dets), and the science/pipeline tag split.

Mock data only — no live API calls.
"""

import json
import pathlib
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pandas as pd
import pytest
from antares_client.exceptions import AntaresException

from rubin_qa import antares_api
from rubin_qa.antares_api import (
    _api_call,
    _search,
    fetch_antares_candidates,
    fetch_antares_locus,
    list_loci,
)
from rubin_qa.__main__ import unknown_tag_split
from rubin_qa.classifier import PIPELINE_TAGS, SCIENCE_TAGS, classify_antares
from rubin_qa.config import OUTPUT_COLUMNS, RETRY_ATTEMPTS, RETRY_DELAY
from rubin_qa.reporting import build_antares_qa_row, run_antares_pipeline
from rubin_qa.validators import validate_antares
from tests import fake_antares


def make_alert(mjd: float, mag: float | None, **extra) -> SimpleNamespace:
    """
    One locus alert. ant_mag=None stands in for a ztf_upper_limit row: ANTARES
    carries non-detections in locus.alerts with no magnitude attached.
    """
    props = {"ant_mag": mag, "ant_passband": "R"}
    props.update(extra)
    return SimpleNamespace(mjd=mjd, properties=props)


def make_locus(
    locus_id: str = "ANT1",
    ra: float = 153.5,
    dec: float = -3.42,
    alerts: list | None = None,
    properties: dict | None = None,
    tags: list | None = None,
) -> SimpleNamespace:
    if alerts is None:
        alerts = [make_alert(60000.0, 18.9), make_alert(60012.0, 18.1)]
    if properties is None:
        properties = {
            "num_mag_values": 2,
            "brightest_alert_magnitude": 18.1,
        }
    if tags is None:
        tags = ["nuclear_transient"]
    return SimpleNamespace(
        locus_id=locus_id, ra=ra, dec=dec,
        alerts=alerts, properties=properties, tags=tags,
    )


class ExplodingLocus(SimpleNamespace):
    """
    Locus whose named attributes raise on access. antares-client builds these
    lazily, so one bad field is a live possibility — the question is whether it
    costs one column or the whole row.
    """

    def __init__(self, broken: set, **kw):
        super().__init__(**kw)
        object.__setattr__(self, "_broken", broken)

    def __getattribute__(self, name):
        if name != "_broken" and name in object.__getattribute__(self, "_broken"):
            raise RuntimeError(f"{name} blew up")
        return object.__getattribute__(self, name)


def patch_search(**attrs):
    """Patch the lazily imported antares_client.search module with a mock."""
    mock = MagicMock()
    for name, value in attrs.items():
        setattr(mock, name, value)
    return patch("rubin_qa.antares_api._search", return_value=mock), mock


class TestApiCall:
    def test_success_returns_result(self):
        fn = MagicMock(return_value="ok")
        result, err = _api_call(fn, "ANT1")
        assert result == "ok"
        assert err is None

    def test_transport_error_retries_then_fails(self, capsys):
        fn = MagicMock(side_effect=httpx.ReadTimeout("timed out"))
        fn.__name__ = "get_by_id"
        with patch("rubin_qa.retry_budget.time.sleep") as mock_sleep:
            result, err = _api_call(fn, "ANT1")
        assert result is None
        assert "timed out" in err
        # Same shape as the ALeRCE client: N attempts → N-1 exponential backoffs.
        expected = [RETRY_DELAY * 2 ** i for i in range(RETRY_ATTEMPTS - 1)]
        assert [c.args[0] for c in mock_sleep.call_args_list] == expected
        assert capsys.readouterr().err.count("WARN: ANTARES get_by_id failed") == RETRY_ATTEMPTS - 1

    def test_antares_exception_is_retried(self):
        """AntaresException is the client's error for an HTTP error answer: a server fault."""
        fn = MagicMock(side_effect=AntaresException({"detail": "server error"}))
        fn.__name__ = "get_by_id"
        with patch("rubin_qa.retry_budget.time.sleep"):
            result, err = _api_call(fn, "ANT1")
        assert result is None
        assert "server error" in err
        assert fn.call_count == RETRY_ATTEMPTS


# --- the retry policy through the real antares-client 2.x, over a fake transport ---------


API_BASE = "https://api.antares.noirlab.edu/v2/"
LOCUS_BODY = {"data": {"id": "ANT1", "ra": 1.0, "dec": 2.0, "grav_wave_events": [],
                       "internal_properties": {"num_mag_values": 3}, "user_properties": {},
                       "lightcurve": "", "tags": ["dimmers"], "catalogs": []}}


@pytest.fixture
def fake_api(monkeypatch):
    """
    Swap the HTTP client antares-client talks through for one whose transport answers
    from `answer` (status, body, content type) or raises it; count the requests. The
    swap is where the client reads it (search.api_client.client), and the real client
    code runs on top: its status handling, its schema, its None for a 404.
    """
    import antares_client.search as search

    state = {"answer": (200, LOCUS_BODY, "application/json"), "requests": 0}

    def handler(request):
        state["requests"] += 1
        answer = state["answer"]
        if isinstance(answer, Exception):
            raise answer
        status, body, ctype = answer
        content = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        return httpx.Response(status, content=content, headers={"content-type": ctype})

    client = httpx.Client(base_url=API_BASE, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(search.api_client, "client", client)
    return state


class TestRetryPolicyThroughTheClient:
    """
    Each case is an answer the live v2 API gives or can give (probed 2026-11-03: README,
    "Known API Quirks"), sent through antares-client 2.x unmodified. Removing any entry
    from antares_errors(), or the hook, turns one of these red.
    """

    def call(self, fn_name: str, *args):
        with patch("rubin_qa.retry_budget.time.sleep") as mock_sleep:
            result = _api_call(getattr(_search(), fn_name), *args)
        return result, mock_sleep

    def test_a_connect_timeout_is_retried(self, fake_api):
        fake_api["answer"] = httpx.ConnectTimeout("connect timed out")
        (result, err), _ = self.call("get_by_id", "ANT1")
        assert result is None and "connect timed out" in err
        assert fake_api["requests"] == RETRY_ATTEMPTS

    def test_a_404_is_an_answer_not_retried(self, fake_api):
        """The bogus-ANT-id case: the v2 API answers 404, the client returns None."""
        fake_api["answer"] = (404, {"detail": "Locus not found for id: ANT_NOPE"}, "application/json")
        (result, err), mock_sleep = self.call("get_by_id", "ANT_NOPE")
        assert (result, err) == (None, None)
        assert fake_api["requests"] == 1
        mock_sleep.assert_not_called()

    def test_a_missing_locus_reaches_the_report_as_not_found(self, fake_api):
        fake_api["answer"] = (404, {"detail": "Locus not found for id: ANT_NOPE"}, "application/json")
        with patch("rubin_qa.retry_budget.time.sleep"):
            data = fetch_antares_locus("ANT_NOPE")
        assert data["fetch_errors"] == ["locus:not_found"]
        assert fake_api["requests"] == 1

    @pytest.mark.parametrize("status", [400, 422])
    def test_a_malformed_request_is_an_answer_not_retried(self, fake_api, status):
        fake_api["answer"] = (status, {"detail": [{"msg": "Can't parse elasticsearch_query"}]},
                              "application/json")
        with patch("rubin_qa.retry_budget.time.sleep") as mock_sleep:
            result, err = _api_call(list_loci, 3)  # the pipeline's listing
        assert result is None
        assert err.startswith(f"bad_request: HTTP {status}: ") and "Can't parse" in err
        assert fake_api["requests"] == 1
        mock_sleep.assert_not_called()

    def test_a_5xx_with_a_json_body_is_retried(self, fake_api):
        fake_api["answer"] = (503, {"detail": "unavailable"}, "application/json")
        (result, err), _ = self.call("get_by_id", "ANT1")
        assert result is None and "unavailable" in err
        assert fake_api["requests"] == RETRY_ATTEMPTS

    def test_a_5xx_with_a_plain_text_body_is_retried(self, fake_api):
        """The live API's 500 is text/plain: the client's own response.json() then fails."""
        fake_api["answer"] = (500, "Internal Server Error", "text/plain; charset=utf-8")
        (result, err), _ = self.call("get_by_id", "ANT1")
        assert result is None
        assert fake_api["requests"] == RETRY_ATTEMPTS

    def test_another_4xx_is_retried_as_on_alerces_side(self, fake_api):
        """Only ANSWERED_STATUSES are answers: a 429 is the server asking to come back later."""
        fake_api["answer"] = (429, {"detail": "slow down"}, "application/json")
        (result, err), _ = self.call("get_by_id", "ANT1")
        assert result is None and "slow down" in err
        assert fake_api["requests"] == RETRY_ATTEMPTS

    @pytest.mark.parametrize("data,says", [
        ({"id": "ANT1", "lightcurve": ""}, "validation error"),  # pydantic's ValidationError
        ({"id": "ANT1"}, "'lightcurve'"),  # the client's own pre-validator: a bare KeyError
    ])
    def test_a_schema_mismatch_fails_at_once(self, fake_api, data, says):
        """Deterministic: resending cannot fix it, and it must not pass as data either."""
        fake_api["answer"] = (200, {"data": data}, "application/json")
        (result, err), mock_sleep = self.call("get_by_id", "ANT1")
        assert result is None and says in err
        assert fake_api["requests"] == 1
        mock_sleep.assert_not_called()

    def test_a_good_answer_comes_back_as_a_locus(self, fake_api):
        (locus, err), _ = self.call("get_by_id", "ANT1")
        assert err is None
        assert (locus.locus_id, locus.tags, locus.properties["num_mag_values"]) == ("ANT1", ["dimmers"], 3)

    def test_the_hook_is_installed_once(self):
        from rubin_qa.antares_api import _raise_answered_status

        hooks = _search().api_client.client.event_hooks["response"]
        _search()
        assert hooks.count(_raise_answered_status) == 1


V2_DATA = pathlib.Path(__file__).parent / "data" / "antares_v2"
V2_LOCUS = "ANT2026qki5p5s9l5r0"


class TestARealV2Response:
    """
    One real locus as the v2 API served it on 2026-11-03 (tests/data/antares_v2: the
    locus and its alerts, byte for byte), through the client and our whole row. It pins
    the live shape the hand-built fakes above must match: one upper limit and two
    detections, num_mag_values 2, no faintest_alert_magnitude.
    """

    @pytest.fixture
    def v2_api(self, monkeypatch):
        import antares_client.search as search

        routes = {f"/v2/loci/{V2_LOCUS}": V2_DATA / f"{V2_LOCUS}.json",
                  f"/v2/loci/{V2_LOCUS}/alerts": V2_DATA / f"{V2_LOCUS}_alerts.json"}
        paths = []

        def handler(request):
            paths.append(request.url.path)
            body = routes.get(request.url.path)
            if body is None:
                return httpx.Response(404, json={"detail": "not found"})
            return httpx.Response(200, content=body.read_bytes(),
                                  headers={"content-type": "application/json"})

        client = httpx.Client(base_url=API_BASE, transport=httpx.MockTransport(handler))
        monkeypatch.setattr(search.api_client, "client", client)
        return paths

    def test_the_locus_and_its_alerts_take_two_requests(self, v2_api):
        data = fetch_antares_locus(V2_LOCUS)
        assert data["fetch_errors"] == []
        assert v2_api == [f"/v2/loci/{V2_LOCUS}", f"/v2/loci/{V2_LOCUS}/alerts"]

    def test_detections_magnitudes_and_tags(self, v2_api):
        data = fetch_antares_locus(V2_LOCUS)
        dets = data["dets"]
        assert len(dets) == 2  # the upper limit (no ant_mag) is filtered out
        assert sorted(dets["ant_passband"]) == ["R", "g"]
        assert set(dets["ant_survey"]) == {1}  # 1 = ZTF detection
        assert (dets["ra"] == 108.43503210001187).all()  # the locus position, on every alert
        assert int(data["ms"]["ndet"].iloc[0]) == 2
        assert data["ms"]["magmin"].iloc[0] == pytest.approx(17.6084, abs=1e-4)
        assert data["tags"] == ["lc_feature_extractor"]

    def test_the_newest_alert_is_a_ztf_detection(self, v2_api):
        data = fetch_antares_locus(V2_LOCUS)
        assert data["ms"]["newest_survey"].iloc[0] == "ZTF"
        row = build_antares_qa_row(V2_LOCUS, data, validate_antares(data), classify_antares(data["tags"]))
        assert row["survey"] == "ZTF"

    def test_the_newest_detection_is_the_newer_of_the_two_ztf_detections(self, v2_api):
        """The upper limit is older here; the newest is the 2026-11-03 detection, no LSST, nothing unknown."""
        data = fetch_antares_locus(V2_LOCUS)
        row = build_antares_qa_row(V2_LOCUS, data, validate_antares(data), classify_antares(data["tags"]))
        assert row["newest_ztf_mjd"] == pytest.approx(61347.4599884, abs=1e-6)
        assert (row["newest_lsst_mjd"], row["unknown_survey_dets"]) == (None, 0)

    def test_mag_range_comes_from_the_detections(self, v2_api):
        """
        The API serves no faintest_alert_magnitude (none of 40 live loci on 2026-11-03),
        so both ends come from the detections; the server's brightest agrees with them.
        """
        data = fetch_antares_locus(V2_LOCUS)
        issues = validate_antares(data)
        row = build_antares_qa_row(V2_LOCUS, data, issues, classify_antares(data["tags"]))
        assert row["ndet"] == 2
        assert row["mag_range"] == pytest.approx(18.8833 - 17.6084, abs=1e-4)
        assert "brightest_mismatch" not in issues


def loci(*ids):
    """What antares-client's search yields: Locus objects, read here for their locus_id."""
    return iter([SimpleNamespace(locus_id=i) for i in ids])


class TestFetchAntaresCandidates:
    def test_deduplicates_locus_ids_preserving_order(self):
        search = MagicMock(return_value=loci("ANT1", "ANT2", "ANT1", "ANT3", "ANT2"))
        patcher, _ = patch_search(search=search)
        with patcher:
            result = fetch_antares_candidates(page_size=5)
        search.assert_called_once_with({}, limit=5)  # one page of n
        # A listing over several pages can repeat loci (README, Known API Quirks): one row per ID
        assert result == ["ANT1", "ANT2", "ANT3"]

    def test_returns_empty_list_on_error(self, capsys):
        patcher, _ = patch_search(search=MagicMock(side_effect=httpx.ConnectError("down")))
        with patcher, patch("rubin_qa.retry_budget.time.sleep"):
            result = fetch_antares_candidates(page_size=10)
        assert result == []
        assert "ERROR: fetch_antares_candidates" in capsys.readouterr().err

    def test_an_empty_listing_is_not_an_error(self):
        patcher, _ = patch_search(search=MagicMock(return_value=loci()))
        with patcher:
            assert fetch_antares_candidates(page_size=10) == []


LISTING = V2_DATA / "loci_limit6.json"


@pytest.fixture
def listing_api(monkeypatch):
    """
    The listing as the v2 API serves it, from one recorded page of 6 loci (tests/data/
    antares_v2/loci_limit6.json, 2026-11-04, byte for byte): a request gets the loci at its
    offset, as many as its limit, and a next link in the server's own form (http://, offset
    and limit), which the server redirects (301) to https. The real 2.0.2 client runs on
    top. Returns the recorded IDs in order and the requests seen.
    """
    import antares_client.search as search

    recorded = json.loads(LISTING.read_bytes())
    pool = recorded["data"]
    seen = []

    def handler(request):
        url = request.url
        if url.scheme == "http":
            seen.append("redirect")
            return httpx.Response(301, headers={"location": str(url.copy_with(scheme="https"))})
        limit, offset = int(url.params["limit"]), int(url.params.get("offset", 0))
        seen.append(("page", offset, limit))
        more = offset + limit < len(pool)
        nxt = (f"http://api.antares.noirlab.edu/v2/loci?sort=-properties.newest_alert_observation_time"
               f"&elasticsearch_query=%7B%7D&offset={offset + limit}&limit={limit}") if more else None
        return httpx.Response(200, json={**recorded, "data": pool[offset:offset + limit],
                                         "links": {**recorded["links"], "next": nxt}})

    client = httpx.Client(base_url=API_BASE, transport=httpx.MockTransport(handler), follow_redirects=True)
    monkeypatch.setattr(search.api_client, "client", client)
    return [d["locus"]["locus_id"] for d in pool], seen


class TestTheListingIsOnePage:
    """
    One page of min(n, ANTARES_MAX_PAGE_SIZE) (2026-11-04): n up to the cap is one request,
    so no page boundary for a tie to repeat loci across, and no http:// redirect.
    """

    def test_n_up_to_the_cap_is_one_request_of_limit_n(self, listing_api):
        pool, seen = listing_api
        assert fetch_antares_candidates(page_size=4) == pool[:4]
        assert seen == [("page", 0, 4)]

    def test_n_above_the_cap_walks_pages_of_the_cap(self, listing_api, monkeypatch):
        pool, seen = listing_api
        monkeypatch.setattr(antares_api, "ANTARES_MAX_PAGE_SIZE", 2)
        assert fetch_antares_candidates(page_size=5) == pool[:5]
        assert seen == [("page", 0, 2), "redirect", ("page", 2, 2), "redirect", ("page", 4, 2)]

    def test_the_cap_is_the_clients_own_ceiling(self):
        """antares-client 2.0.2 refuses a listing of more than 1000 loci, before any request; the cap is that ceiling."""
        import antares_client.search as search

        assert antares_api.ANTARES_MAX_PAGE_SIZE == 1000
        with pytest.raises(ValueError):
            search.get_random_locus_ids(1001)
        assert len({d["locus"]["locus_id"] for d in json.loads(LISTING.read_bytes())["data"]}) == 6  # one page: no repeats


class TestFetchAntaresLocus:
    def test_ant_id_routes_to_get_by_id(self):
        by_id, by_ztf = MagicMock(return_value=make_locus()), MagicMock()
        patcher, _ = patch_search(get_by_id=by_id, get_by_ztf_object_id=by_ztf)
        with patcher:
            fetch_antares_locus("ANT2020hnj2u")
        by_id.assert_called_once_with("ANT2020hnj2u")
        by_ztf.assert_not_called()

    def test_ztf_id_routes_to_get_by_ztf_object_id(self):
        by_id, by_ztf = MagicMock(), MagicMock(return_value=make_locus())
        patcher, _ = patch_search(get_by_id=by_id, get_by_ztf_object_id=by_ztf)
        with patcher:
            fetch_antares_locus("ZTF20aafqubg")
        by_ztf.assert_called_once_with("ZTF20aafqubg")
        by_id.assert_not_called()

    def test_upper_limits_are_excluded_from_lightcurve(self):
        """ztf_upper_limit alerts ride in locus.alerts with no ant_mag."""
        locus = make_locus(alerts=[
            make_alert(60000.0, 18.9),
            make_alert(60003.0, None),   # upper limit
            make_alert(60012.0, 18.1),
        ])
        patcher, _ = patch_search(get_by_id=MagicMock(return_value=locus))
        with patcher:
            data = fetch_antares_locus("ANT1")
        assert len(data["dets"]) == 2
        assert data["dets"]["ant_mag"].notna().all()

    def test_ndet_prefers_num_mag_values_over_row_count(self):
        """
        ANTARES applies quality cuts the raw alert stream does not reflect, so
        num_mag_values sits below len(dets). Taking the row count instead would
        silently overstate every ndet in the report.
        """
        locus = make_locus(
            alerts=[make_alert(60000.0 + i, 19.0) for i in range(5)],
            properties={
                "num_mag_values": 3,
                "brightest_alert_magnitude": 18.1,
            },
        )
        patcher, _ = patch_search(get_by_id=MagicMock(return_value=locus))
        with patcher:
            data = fetch_antares_locus("ANT1")
        assert len(data["dets"]) == 5
        assert int(data["ms"]["ndet"].iloc[0]) == 3

    def test_ms_falls_back_to_row_count_without_num_mag_values(self):
        locus = make_locus(properties={"brightest_alert_magnitude": 18.1})
        patcher, _ = patch_search(get_by_id=MagicMock(return_value=locus))
        with patcher:
            data = fetch_antares_locus("ANT1")
        assert int(data["ms"]["ndet"].iloc[0]) == 2
        assert data["ms"]["magmax"].iloc[0] == 18.9  # from the detections, not the properties

    @pytest.mark.parametrize("codes,survey", [
        ((1, 4), "LSST"),   # the newest alert is LSST's
        ((4, 2), "ZTF"),    # a ZTF upper limit is newest: still ZTF
        ((1, 9), None),     # a code ANT_SURVEY does not know
        ((1, None), None),  # the newest alert carries no code
    ])
    def test_the_newest_alert_names_the_survey(self, codes, survey):
        """The survey that put the locus in the sample: its newest alert's, upper limits included."""
        older, newest = codes
        alerts = [make_alert(60000.0, 18.5, ant_survey=older),
                  make_alert(60010.0, None if newest == 2 else 18.1, ant_survey=newest)]
        patcher, _ = patch_search(get_by_id=MagicMock(return_value=make_locus(alerts=alerts)))
        with patcher:
            data = fetch_antares_locus("ANT1")
        assert data["ms"]["newest_survey"].iloc[0] == survey

    def test_the_newest_detection_per_survey_comes_from_the_detections_mag_range_uses(self):
        """
        2026-11-04: one definition of a detection, the alerts with ant_mag. A newer ZTF upper
        limit (code 2, no magnitude) never counts as the newest ZTF detection.
        """
        alerts = [make_alert(60000.0, 18.9, ant_survey=1), make_alert(60005.0, 18.5, ant_survey=4),
                  make_alert(60008.0, 18.4, ant_survey=1), make_alert(60010.0, None, ant_survey=2)]
        patcher, _ = patch_search(get_by_id=MagicMock(return_value=make_locus(alerts=alerts)))
        with patcher:
            data = fetch_antares_locus("ANT1")
        ms = data["ms"].iloc[0]
        assert (ms["newest_ztf_mjd"], ms["newest_lsst_mjd"], ms["unknown_survey_dets"]) == (60008.0, 60005.0, 0)
        row = build_antares_qa_row("ANT1", data, validate_antares(data), classify_antares(data["tags"]))
        assert (row["newest_ztf_mjd"], row["newest_lsst_mjd"], row["unknown_survey_dets"]) == (60008.0, 60005.0, 0)

    @pytest.mark.parametrize("code", [9, None])
    def test_a_detection_of_no_known_survey_is_counted_never_dropped(self, code):
        """A code ANT_SURVEY does not know, or none: the detection stays, in unknown_survey_dets."""
        alerts = [make_alert(60000.0, 18.9, ant_survey=1), make_alert(60012.0, 18.1, ant_survey=code)]
        patcher, _ = patch_search(get_by_id=MagicMock(return_value=make_locus(alerts=alerts)))
        with patcher:
            data = fetch_antares_locus("ANT1")
        row = build_antares_qa_row("ANT1", data, validate_antares(data), classify_antares(data["tags"]))
        assert (row["newest_ztf_mjd"], row["newest_lsst_mjd"], row["unknown_survey_dets"]) == (60000.0, None, 1)
        assert row["mag_range"] == pytest.approx(0.8)  # the same detection is in mag_range too

    def test_both_magnitude_ends_come_from_the_same_detections(self):
        locus = make_locus(alerts=[make_alert(60000.0, 18.9), make_alert(60001.0, None),
                                   make_alert(60012.0, 18.1)],
                           properties={"num_mag_values": 2, "brightest_alert_magnitude": 18.1,
                                       "faintest_alert_magnitude": 99.0})  # ignored if ever served
        patcher, _ = patch_search(get_by_id=MagicMock(return_value=locus))
        with patcher:
            data = fetch_antares_locus("ANT1")
        ms = data["ms"].iloc[0]
        assert (ms["magmin"], ms["magmax"], ms["brightest_alert_magnitude"]) == (18.1, 18.9, 18.1)
        assert "brightest_mismatch" not in validate_antares(data)

    def test_a_locus_without_usable_detections_is_flagged_not_silent(self):
        """Only upper limits, while the server still names a brightest: a FLAG row, never a quiet one."""
        locus = make_locus(alerts=[make_alert(60000.0, None), make_alert(60001.0, None)],
                           properties={"num_mag_values": 0, "brightest_alert_magnitude": 18.1})
        patcher, _ = patch_search(get_by_id=MagicMock(return_value=locus))
        with patcher:
            data = fetch_antares_locus("ANT1")
        issues = validate_antares(data)
        row = build_antares_qa_row("ANT1", data, issues, classify_antares(data["tags"]))
        assert "no_detections" in issues
        assert row["status"] == "FLAG" and row["mag_range"] != row["mag_range"]  # flagged, and NaN

    @pytest.mark.parametrize("served,flagged", [(17.5, True), (18.1 + 5e-5, False), (None, False)])
    def test_the_servers_brightest_is_checked_against_the_detections(self, served, flagged):
        """A different brightest means the server counted a different set of alerts."""
        props = {"num_mag_values": 2}
        if served is not None:
            props["brightest_alert_magnitude"] = served
        patcher, _ = patch_search(get_by_id=MagicMock(return_value=make_locus(properties=props)))
        with patcher:
            data = fetch_antares_locus("ANT1")
        assert ("brightest_mismatch" in validate_antares(data)) is flagged

    def test_locus_coordinates_are_copied_onto_every_alert(self):
        locus = make_locus(ra=10.5, dec=-2.25)
        patcher, _ = patch_search(get_by_id=MagicMock(return_value=locus))
        with patcher:
            dets = fetch_antares_locus("ANT1")["dets"]
        assert (dets["ra"] == 10.5).all()
        assert (dets["dec"] == -2.25).all()

    def test_missing_ztf_object_returns_not_found(self):
        patcher, _ = patch_search(get_by_ztf_object_id=MagicMock(return_value=None))
        with patcher:
            data = fetch_antares_locus("ZTF_missing")
        assert data["fetch_errors"] == ["locus:not_found"]
        assert data["dets"].empty and data["tags"] == []

    def test_fetch_failure_is_reported_as_locus_error(self):
        fn = MagicMock(side_effect=httpx.ReadTimeout("timed out"))
        fn.__name__ = "get_by_id"
        patcher, _ = patch_search(get_by_id=fn)
        with patcher, patch("rubin_qa.retry_budget.time.sleep"):
            data = fetch_antares_locus("ANT1")
        assert data["fetch_errors"][0].startswith("locus:")
        assert data["ms"].empty

    def test_alerts_are_optional(self):
        locus = make_locus(alerts=[])
        patcher, _ = patch_search(get_by_id=MagicMock(return_value=locus))
        with patcher:
            data = fetch_antares_locus("ANT1")
        assert data["dets"].empty
        assert data["fetch_errors"] == []
        assert int(data["ms"]["ndet"].iloc[0]) == 2


class TestDeferredImport:
    def test_missing_client_raises_an_actionable_error(self):
        """
        The import is deferred so ALeRCE-only users never need antares-client.
        If it is missing the message has to say what to install — this is the
        first thing an operator sees when the ANTARES survey is selected.
        """
        with patch.dict(sys.modules, {"antares_client": None}):
            with pytest.raises(ImportError, match="pip install antares-client"):
                _search()

    def test_returns_the_search_module_when_installed(self):
        """Every other test patches _search, so pin what it actually hands back."""
        import antares_client.search

        assert _search() is antares_client.search

    def test_import_happens_at_call_time_not_module_import(self):
        """
        rubin_qa.antares_api must import cleanly without the broker package,
        or importing reporting.py would break the ALeRCE path too.
        """
        import importlib

        with patch.dict(sys.modules, {"antares_client": None}):
            importlib.reload(importlib.import_module("rubin_qa.antares_api"))


class TestFetchAntaresLocusDegradation:
    """One unusable field must cost a column, not the row."""

    def test_broken_alerts_still_yield_magstats_and_tags(self):
        locus = ExplodingLocus(
            {"alerts"},
            locus_id="ANT1", ra=1.0, dec=2.0,
            properties={"num_mag_values": 4, "brightest_alert_magnitude": 18.0},
            tags=["nuclear_transient"],
        )
        patcher, _ = patch_search(get_by_id=MagicMock(return_value=locus))
        with patcher:
            data = fetch_antares_locus("ANT1")
        assert data["fetch_errors"] == ["alerts:alerts blew up"]
        assert data["dets"].empty
        assert int(data["ms"]["ndet"].iloc[0]) == 4
        assert data["tags"] == ["nuclear_transient"]

    def test_broken_properties_still_yield_alerts_and_tags(self):
        locus = ExplodingLocus(
            {"properties"},
            locus_id="ANT1", ra=1.0, dec=2.0,
            alerts=[make_alert(60000.0, 18.9), make_alert(60010.0, 18.1)],
            tags=["dimmers"],
        )
        patcher, _ = patch_search(get_by_id=MagicMock(return_value=locus))
        with patcher:
            data = fetch_antares_locus("ANT1")
        assert data["fetch_errors"] == ["properties:properties blew up"]
        assert len(data["dets"]) == 2
        assert data["ms"].empty
        assert data["tags"] == ["dimmers"]

    def test_broken_tags_still_yield_the_lightcurve(self):
        locus = ExplodingLocus(
            {"tags"},
            locus_id="ANT1", ra=1.0, dec=2.0,
            alerts=[make_alert(60000.0, 18.9)],
            properties={"num_mag_values": 1},
        )
        patcher, _ = patch_search(get_by_id=MagicMock(return_value=locus))
        with patcher:
            data = fetch_antares_locus("ANT1")
        assert data["fetch_errors"] == ["tags:tags blew up"]
        assert len(data["dets"]) == 1

    def test_every_field_broken_degrades_without_raising(self):
        locus = ExplodingLocus(
            {"alerts", "properties", "tags"}, locus_id="ANT1", ra=1.0, dec=2.0,
        )
        patcher, _ = patch_search(get_by_id=MagicMock(return_value=locus))
        with patcher:
            data = fetch_antares_locus("ANT1")
        assert len(data["fetch_errors"]) == 3
        assert data["dets"].empty and data["ms"].empty and data["tags"] == []

    def test_degraded_locus_becomes_a_flag_row_not_a_crash(self):
        """
        End to end: a properties failure leaves no magstats, so the row builder
        falls back to counting alert rows and the validator flags the gap.
        """
        locus = ExplodingLocus(
            {"properties"},
            locus_id="ANT1", ra=1.0, dec=2.0,
            alerts=[make_alert(60000.0, 18.9), make_alert(60010.0, 18.1)],
            tags=["nuclear_transient"],
        )
        patcher, _ = patch_search(get_by_id=MagicMock(return_value=locus))
        with patcher:
            data = fetch_antares_locus("ANT1")
        issues = validate_antares(data)
        row = build_antares_qa_row("ANT1", data, issues, classify_antares(data["tags"]))
        assert "fetch_error_properties" in issues and "mag_null" in issues
        assert row["ndet"] == 2                      # from len(dets), ms is empty
        assert row["mag_range"] != row["mag_range"]  # NaN — no magstats to subtract
        assert row["timespan_days"] == pytest.approx(10.0)
        assert row["status"] == "FLAG"


class TestValidateAntares:
    def test_complete_locus_has_no_issues(self, antares_data):
        assert validate_antares(antares_data) == []

    def test_empty_locus_short_circuits(self, antares_data_empty):
        issues = validate_antares(antares_data_empty)
        assert issues == ["no_detections", "no_classification"]

    def test_single_detection_is_unconfirmed(self, antares_data):
        antares_data["ms"] = pd.DataFrame([{"ndet": 1, "magmin": 18.1, "magmax": 18.1}])
        assert "ndet_lt_2" in validate_antares(antares_data)

    def test_ndet_comes_from_ms_not_row_count(self, antares_data):
        """3 alert rows but ANTARES counted 1 usable magnitude → unconfirmed."""
        antares_data["ms"] = pd.DataFrame([{"ndet": 1, "magmin": 18.1, "magmax": 18.9}])
        assert len(antares_data["dets"]) == 3
        assert "ndet_lt_2" in validate_antares(antares_data)

    def test_missing_coordinates_flagged(self, antares_data):
        antares_data["dets"] = antares_data["dets"].drop(columns=["dec"])
        assert "coordinates_missing" in validate_antares(antares_data)

    def test_missing_magnitudes_flagged(self, antares_data):
        antares_data["ms"] = pd.DataFrame([{"ndet": 3, "magmin": None, "magmax": None}])
        assert "mag_null" in validate_antares(antares_data)

    def test_a_nan_minimum_beside_the_servers_brightest_is_mag_null_not_silent(self, antares_data):
        """
        abs(NaN - brightest) > tolerance is False: were the comparison reached with a NaN
        minimum, nothing would fire and mag_range would be quietly empty again.
        """
        antares_data["ms"] = pd.DataFrame([{"ndet": 3, "magmin": float("nan"), "magmax": float("nan"),
                                            "brightest_alert_magnitude": 18.1}])
        issues = validate_antares(antares_data)
        assert "mag_null" in issues and "brightest_mismatch" not in issues

    def test_untagged_locus_has_no_classification(self, antares_data):
        antares_data["tags"] = []
        assert "no_classification" in validate_antares(antares_data)

    def test_fetch_errors_become_issue_tokens(self, antares_data):
        antares_data["fetch_errors"] = ["locus:timed out", "tags:boom"]
        issues = validate_antares(antares_data)
        assert "fetch_error_locus" in issues and "fetch_error_tags" in issues


class TestClassifyAntares:
    def test_single_science_tag_passes(self):
        cl = classify_antares(["nuclear_transient"])
        assert cl["verdict"] == "pass"
        assert cl["top_class"] == "nuclear_transient"
        assert cl["consensus"] == 1.0
        assert cl["n_classifiers"] == 1 and cl["flag"] is None

    def test_pipeline_tags_do_not_count_toward_consensus(self):
        """One science tag plus three pipeline tags is still an unambiguous locus."""
        cl = classify_antares([
            "nuclear_transient", "lc_feature_extractor", "high_snr", "in_LSSTDDF",
        ])
        assert cl["verdict"] == "pass"
        assert cl["n_classifiers"] == 1
        assert cl["top_class"] == "nuclear_transient"

    def test_two_science_tags_split_the_vote(self):
        cl = classify_antares(["extragalactic", "dimmers"])
        assert cl["consensus"] == 0.5
        assert cl["verdict"] == "review_major"
        assert cl["n_agree"] == 1 and cl["n_disagree"] == 1

    def test_top_class_is_sorted_and_joined(self):
        cl = classify_antares(["nuclear_transient", "dimmers", "extragalactic"])
        assert cl["top_class"] == "dimmers, extragalactic, nuclear_transient"
        assert cl["n_classifiers"] == 3

    def test_review_minor_is_unreachable(self):
        """
        consensus is 1/n, so two tags give 0.50 — under MAJORITY_THRESHOLD (0.65).
        Nothing between "one tag" and "ambiguous" exists in this scheme. Asserted
        rather than left implicit so a threshold change surfaces here.
        """
        for n in range(2, 8):
            cl = classify_antares(sorted(
                ["dimmers", "extragalactic", "nuclear_transient", "blue_transient",
                 "in_m31", "nova_test", "sso_confirmed"]
            )[:n])
            assert cl["verdict"] == "review_major"

    def test_pipeline_only_tags_name_what_was_present(self):
        cl = classify_antares(["lc_feature_extractor", "high_snr"])
        assert cl["verdict"] == "review_major"
        assert cl["n_classifiers"] == 0
        assert "no_science_tags" in cl["flag"]
        assert "lc_feature_extractor" in cl["flag"] and "high_snr" in cl["flag"]

    def test_unknown_tags_are_reported_not_silently_ignored(self):
        """A new ANTARES tag must be visible, not absorbed into 'no classification'."""
        cl = classify_antares(["brand_new_filter_2027"])
        assert "unknown: ['brand_new_filter_2027']" in cl["flag"]
        assert cl["n_classifiers"] == 0

    def test_no_tags_at_all(self):
        """no_classification has one owner, validate_antares (2026-11-04): the classifier adds nothing."""
        cl = classify_antares([])
        assert (cl["flag"], cl["flag_reason"]) == (None, None)
        assert cl["top_class"] is None and cl["consensus"] is None


LANTERN = "lantern_xgboost_t2.0.7_c0.95"


def tag_split(tags: list) -> tuple[list, list, list]:
    """A recording's tags by class, in served order: what a test asserts of it before relying on it."""
    return ([t for t in tags if t in SCIENCE_TAGS], [t for t in tags if t in PIPELINE_TAGS],
            [t for t in tags if t not in SCIENCE_TAGS and t not in PIPELINE_TAGS])


class TestTagClassesOnRecordedLoci:
    """
    The tag classes and their text on real loci (tests/fake_antares.py: recorded 2026-11-13,
    byte for byte, with the real client on top). Each test first asserts what it needs of
    its recording.
    """

    @pytest.fixture(autouse=True)
    def recorded(self, monkeypatch):
        return fake_antares.serve_recorded(monkeypatch)

    @pytest.mark.parametrize("locus_id,min_unknown", [
        ("ANT20262tl85caoepac", 0),  # its pipeline tags served out of order
        ("ANT2022r8hygxeyd6qm", 2),  # two unknown tags, so their order is tested too
    ])
    def test_tag_lists_are_sorted_whatever_order_antares_serves(self, locus_id, min_unknown):
        """
        ANTARES serves a locus's tags in no fixed order (README, Known API Quirks): the flag's
        lists and unknown_tags are sorted, so the served order and its reverse give one text.
        """
        tags = fake_antares.served_tags(locus_id)
        science, pipeline, unknown = tag_split(tags)
        assert science == [] and len(pipeline) >= 2 and len(unknown) >= min_unknown
        cl, reversed_cl = classify_antares(tags), classify_antares(tags[::-1])
        assert cl["flag"] == f"no_science_tags (pipeline: {sorted(pipeline)}; unknown: {sorted(unknown)})"
        assert (cl["flag"], cl["unknown_tags"]) == (reversed_cl["flag"], reversed_cl["unknown_tags"])
        assert cl["unknown_tags"] == sorted(unknown)

    def test_uniform_random_sample_is_a_pipeline_tag(self):
        """
        Chen, 2026-11-13: ANTARES describes it as 1% of all LSST alerts, uniformly and randomly
        sampled. How an alert was picked says nothing about the object.
        """
        tags = fake_antares.served_tags("ANT20262tl85caoepac")
        assert "uniform_random_sample" in tags and tag_split(tags)[0] == []
        cl = classify_antares(tags)
        assert cl["flag"] == ("no_science_tags (pipeline: ['in_LSSTDDF', 'lc_feature_extractor', "
                              "'lsst_scimma_quality_transient', 'uniform_random_sample']; unknown: [])")
        assert (cl["flag_reason"], cl["verdict"], cl["unknown_tags"]) == ("no_science_tags", "review_major", [])

    def test_no_tag_is_in_both_sets(self):
        """classify_antares looks for science tags first: a tag in both sets would score as science unseen."""
        assert not SCIENCE_TAGS & PIPELINE_TAGS

    @pytest.mark.parametrize("locus_id,other_science,expected", [
        ("ANT20250fk7udcecmrt", [], ("microlensing_candidate", "pass", None)),
        ("ANT2026b0g1vijrn6ym", ["extragalactic"],
         ("extragalactic, microlensing_candidate", "review_major", "multiple_science_tags")),
    ])
    def test_microlensing_candidate_is_a_science_tag(self, locus_id, other_science, expected):
        """
        Chen, 2026-11-13: a microlensing event is a class claim. Alone it is the locus's one
        science tag (PASS); beside another it splits the vote.
        """
        tags = fake_antares.served_tags(locus_id)
        assert "microlensing_candidate" in tags
        assert sorted(t for t in tag_split(tags)[0] if t != "microlensing_candidate") == other_science
        cl = classify_antares(tags)
        assert (cl["top_class"], cl["verdict"], cl["flag_reason"]) == expected

    @pytest.mark.parametrize("locus_id", ["ANT2026b0g1vijrn6ym", "ANT2022r8hygxeyd6qm"])
    def test_lantern_xgboost_stays_unknown_pending_a_decision(self, locus_id):
        """
        Pins a pending decision, not a permanent rule: lantern_xgboost_t2.0.7_c0.95 is
        unclassified until Chen decides what the tag is, and no prefix rule classes
        lantern_xgboost_* (Chen, 2026-11-13; CLAUDE.md, "QA pipeline"). The round that
        classifies it changes this test on purpose.
        """
        tags = fake_antares.served_tags(locus_id)
        assert LANTERN in tags
        assert LANTERN in classify_antares(tags)["unknown_tags"]

    def test_unknown_tags_are_recorded_beside_a_science_tag(self):
        """
        The row's unknown_tags is written whether or not the locus has a science tag: the flag
        names unknown tags only when it has none, so without the column this locus's
        lantern tag would reach no report.
        """
        assert tag_split(fake_antares.served_tags("ANT2026sx3pbuillwqe"))[0] != []
        row = fake_antares.recorded_row("ANT2026sx3pbuillwqe")
        assert row["status"] == "PASS" and row["flag"] is None
        assert row["unknown_tags"] == [LANTERN]
        assert fake_antares.recorded_row("ANT20262tl85caoepac")["unknown_tags"] == []

    def test_the_summary_counts_unknown_tags_and_reads_back_from_the_csv(self, tmp_path):
        """The journal rotates; the report alone reproduces the count (README, Pipeline QA Report)."""
        df = pd.DataFrame([fake_antares.recorded_row(i) for i in ("ANT2026sx3pbuillwqe", "ANT20262tl85caoepac")],
                          columns=OUTPUT_COLUMNS)
        assert unknown_tag_split(df) == f"unknown tags: {LANTERN} 1"
        df.to_csv(tmp_path / "r.csv", index=False)
        assert unknown_tag_split(pd.read_csv(tmp_path / "r.csv")) == f"unknown tags: {LANTERN} 1"
        assert unknown_tag_split(df.iloc[1:]) == "unknown tags: none"

    def test_a_report_without_the_column_is_not_recorded_never_none(self, tmp_path):
        """A report written before unknown_tags existed: no count is not a count of none."""
        df = pd.DataFrame([fake_antares.recorded_row("ANT20262tl85caoepac")], columns=OUTPUT_COLUMNS)
        assert unknown_tag_split(df.drop(columns="unknown_tags")) == "unknown tags: not recorded"
        df.loc[len(df)] = df.iloc[0]
        df.at[1, "unknown_tags"] = None
        assert unknown_tag_split(df) == "unknown tags: none; not recorded on 1 of 2"


class TestBuildAntaresQaRow:
    def test_clean_locus_passes(self, antares_data):
        cl = classify_antares(antares_data["tags"])
        row = build_antares_qa_row("ANT1", antares_data, [], cl)
        assert row["status"] == "PASS"
        assert row["oid"] == "ANT1" and row["ndet"] == 3
        assert row["confirmed"] is True
        assert row["flag"] is None

    def test_schema_matches_alerce_output_columns(self, antares_data):
        cl = classify_antares(antares_data["tags"])
        row = build_antares_qa_row("ANT1", antares_data, [], cl)
        assert list(row) == OUTPUT_COLUMNS
        assert row["broker"] == "antares"

    def test_mag_range_and_timespan_derived(self, antares_data):
        cl = classify_antares(antares_data["tags"])
        row = build_antares_qa_row("ANT1", antares_data, [], cl)
        assert row["mag_range"] == pytest.approx(0.8)     # 18.9 − 18.1
        assert row["timespan_days"] == pytest.approx(12.0)  # 60012 − 60000

    def test_completeness_issues_force_flag_status(self, antares_data):
        cl = classify_antares(antares_data["tags"])
        row = build_antares_qa_row("ANT1", antares_data, ["ndet_lt_2"], cl)
        assert row["status"] == "FLAG"
        assert row["has_issues"] is True
        assert "completeness: ndet_lt_2" in row["flag"]

    def test_multiple_science_tags_yield_review_major(self, antares_data):
        antares_data["tags"] = ["extragalactic", "dimmers"]
        cl = classify_antares(antares_data["tags"])
        row = build_antares_qa_row("ANT1", antares_data, [], cl)
        assert row["status"] == "REVIEW_MAJOR"
        assert row["n_classifiers"] == 2

    def test_single_science_tag_is_not_penalised_for_being_alone(self, antares_data):
        """
        The ALeRCE row flags n_classifiers < 2 as insufficient_classifiers. ANTARES
        tags are not classifier votes, so one tag is a complete answer — this is the
        one place the two row builders must disagree.
        """
        cl = classify_antares(["nuclear_transient"])
        row = build_antares_qa_row("ANT1", antares_data, [], cl)
        assert row["n_classifiers"] == 1
        assert row["status"] == "PASS"

    def test_empty_locus_degrades_without_raising(self, antares_data_empty):
        cl = classify_antares([])
        issues = validate_antares(antares_data_empty)
        row = build_antares_qa_row("ANT1", antares_data_empty, issues, cl)
        assert row["status"] == "FLAG"
        assert row["ndet"] == 0 and row["confirmed"] is False
        assert row["mag_range"] != row["mag_range"]  # NaN


class TestRunAntaresPipeline:
    def _locus_data(self, tags):
        return {
            "dets": pd.DataFrame({
                "ra": [1.0, 1.0], "dec": [2.0, 2.0],
                "ant_mag": [19.0, 18.5], "mjd": [60000.0, 60010.0],
            }),
            "ms": pd.DataFrame([{"ndet": 2, "magmin": 18.5, "magmax": 19.0}]),
            "tags": tags,
            "fetch_errors": [],
        }

    def test_one_row_per_locus_with_full_schema(self):
        from rubin_qa import antares_api

        with patch.object(
            antares_api, "fetch_antares_locus",
            lambda lid: self._locus_data(["nuclear_transient"]),
        ):
            df = run_antares_pipeline(
                locus_ids=["ANT1", "ANT2", "ANT3"],
                inter_object_delay=0, quiet=True,
            )
        assert len(df) == 3
        assert list(df.columns) == OUTPUT_COLUMNS
        assert list(df["oid"]) == ["ANT1", "ANT2", "ANT3"]
        assert set(df["status"]) == {"PASS"}

    def test_explicit_ids_skip_the_candidate_fetch(self):
        from rubin_qa import antares_api

        fetch = MagicMock()
        with patch.object(antares_api, "fetch_antares_candidates", fetch), \
             patch.object(
                 antares_api, "fetch_antares_locus",
                 lambda lid: self._locus_data(["dimmers"]),
             ):
            run_antares_pipeline(locus_ids=["ANT1"], inter_object_delay=0, quiet=True)
        fetch.assert_not_called()

    def test_candidate_fetch_used_when_no_ids_given(self):
        from rubin_qa import antares_api

        with patch.object(
            antares_api, "fetch_antares_candidates",
            MagicMock(return_value=["ANT7", "ANT8"]),
        ), patch.object(
            antares_api, "fetch_antares_locus",
            lambda lid: self._locus_data(["extragalactic"]),
        ):
            df = run_antares_pipeline(page_size=2, inter_object_delay=0, quiet=True)
        assert list(df["oid"]) == ["ANT7", "ANT8"]

    def test_a_listing_run_says_what_it_covered(self):
        """How many loci were checked of how many asked, and how many exposures they span."""
        from rubin_qa import antares_api

        newest = {"ANT1": 61347.4756, "ANT2": 61347.4756, "ANT3": 61347.4812}
        survey = {"ANT1": "ZTF", "ANT2": "ZTF", "ANT3": None}

        def data(lid):
            d = self._locus_data(["dimmers"])
            d["ms"]["newest_alert_observation_time"] = newest[lid]
            d["ms"]["newest_survey"] = survey[lid]
            return d

        with patch.object(antares_api, "fetch_antares_candidates",
                          MagicMock(return_value=list(newest))), \
             patch.object(antares_api, "fetch_antares_locus", data):
            df = run_antares_pipeline(page_size=5, inter_object_delay=0, quiet=True)
        assert df.attrs["sample"] == {"asked": 5, "listed": 3, "checked": 3, "newest_times": 2,
                                      "newest_mjd": (61347.4756, 61347.4812),
                                      "surveys": {"ZTF": 2, "unknown": 1}}

    def test_every_row_records_its_survey_and_the_split_counts_the_rows(self):
        """
        The comparability rule's input lives in the CSV, not only in the journal (which
        rotates): each row's survey, and the sample line's split is a count of those rows.
        """
        from rubin_qa import antares_api

        survey = {"ANT1": "ZTF", "ANT2": "LSST", "ANT3": None, "ANT4": "ZTF"}

        def data(lid):
            d = self._locus_data(["dimmers"])
            d["ms"]["newest_survey"] = survey[lid]
            return d

        with patch.object(antares_api, "fetch_antares_candidates", MagicMock(return_value=list(survey))), \
             patch.object(antares_api, "fetch_antares_locus", data):
            df = run_antares_pipeline(page_size=4, inter_object_delay=0, quiet=True)
        assert list(df["survey"].fillna("")) == ["ZTF", "LSST", "", "ZTF"]  # unknown: an empty cell
        counted = df["survey"].fillna("unknown").value_counts().to_dict()
        assert df.attrs["sample"]["surveys"] == counted == {"ZTF": 2, "LSST": 1, "unknown": 1}

    def test_a_run_on_given_ids_states_no_sample(self):
        from rubin_qa import antares_api

        with patch.object(antares_api, "fetch_antares_locus", lambda lid: self._locus_data(["dimmers"])):
            df = run_antares_pipeline(locus_ids=["ANT1"], inter_object_delay=0, quiet=True)
        assert "sample" not in df.attrs

    def test_empty_candidate_list_returns_typed_empty_frame(self):
        """
        A failed candidate fetch must still return the report schema — __main__
        writes no CSV and exits 1 on an empty frame, and a bare DataFrame() would
        break the column check on the way there.
        """
        from rubin_qa import antares_api

        with patch.object(
            antares_api, "fetch_antares_candidates", MagicMock(return_value=[])
        ):
            df = run_antares_pipeline(page_size=10, inter_object_delay=0, quiet=True)
        assert df.empty
        assert list(df.columns) == OUTPUT_COLUMNS

    def test_one_bad_locus_does_not_abort_the_run(self):
        """A 500 on one locus costs one FLAG row, not the whole daily report."""
        from rubin_qa import antares_api

        def flaky(lid):
            if lid == "ANT2":
                return {
                    "dets": pd.DataFrame(), "ms": pd.DataFrame(), "tags": [],
                    "fetch_errors": ["locus:500 server error"],
                }
            return self._locus_data(["nuclear_transient"])

        with patch.object(antares_api, "fetch_antares_locus", flaky):
            df = run_antares_pipeline(
                locus_ids=["ANT1", "ANT2", "ANT3"],
                inter_object_delay=0, quiet=True,
            )
        assert len(df) == 3
        assert list(df["status"]) == ["PASS", "FLAG", "PASS"]
        assert "fetch_error_locus" in df.iloc[1]["completeness_issues"]

    def test_inter_object_delay_paces_the_loop(self):
        """Rate-limit pacing is per locus, and 0 must mean no sleep at all."""
        from rubin_qa import antares_api

        with patch.object(
            antares_api, "fetch_antares_locus",
            lambda lid: self._locus_data(["nuclear_transient"]),
        ), patch("rubin_qa.reporting.time.sleep") as mock_sleep:
            run_antares_pipeline(
                locus_ids=["ANT1", "ANT2", "ANT3"], inter_object_delay=0.5, quiet=True,
            )
            assert [c.args[0] for c in mock_sleep.call_args_list] == [0.5, 0.5, 0.5]
            mock_sleep.reset_mock()
            run_antares_pipeline(
                locus_ids=["ANT1"], inter_object_delay=0, quiet=True,
            )
            mock_sleep.assert_not_called()

    def test_progress_output_suppressed_by_quiet(self, capsys):
        from rubin_qa import antares_api

        with patch.object(
            antares_api, "fetch_antares_locus",
            lambda lid: self._locus_data(["nuclear_transient"]),
        ):
            run_antares_pipeline(locus_ids=["ANT1"], inter_object_delay=0, quiet=True)
            assert capsys.readouterr().out == ""
            run_antares_pipeline(locus_ids=["ANT1"], inter_object_delay=0, quiet=False)
            assert "ANT1" in capsys.readouterr().out


# --- the ANTARES helpers the repo shares (one definition each, 2026-10-03) -----------------


class TestSharedAntaresHelpers:
    def test_errors_are_transport_antares_and_json_decode_errors(self):
        """What each entry stands for: TestRetryPolicyThroughTheClient."""
        import json

        from antares_client.exceptions import AntaresException

        from rubin_qa.antares_api import antares_errors
        assert antares_errors() == (httpx.TransportError, AntaresException, json.JSONDecodeError)

    @pytest.mark.parametrize("value,named", [("null", False), (None, False), ("", False), ("31521", True)])
    def test_ssnamenr_placeholders_are_not_asteroids(self, value, named):
        from rubin_qa.antares_api import is_solar_system
        assert is_solar_system({"ztf_ssnamenr": value}) is named

    def test_locus_survey_ids(self):
        from rubin_qa.antares_api import locus_survey_ids
        props = {"survey": {"ztf": {"id": ["ZTF26a"]}, "lsst": {"dia_object_id": [17]}}}
        assert locus_survey_ids(props) == {"ztf": ["ZTF26a"], "lsst": ["17"]}
        assert locus_survey_ids({"ztf_object_id": "ZTF19b"})["ztf"] == ["ZTF19b"]

    def test_sky_distance_clause(self):
        from rubin_qa.antares_api import sky_distance_clause
        assert sky_distance_clause((150.1, 2.5, 2.82)) == {
            "sky_distance": {"radius": "2.82 degree", "location": "10h00m24s +02d30m00s"}}

    def test_sky_distance_clause_is_what_the_clients_own_cone_search_sends(self, monkeypatch):
        """The oracle: antares-client 2.x's cone_search, for a radius it accepts (≤ 1°)."""
        import antares_client.search as search
        from astropy.coordinates import Angle, SkyCoord

        from rubin_qa.antares_api import sky_distance_clause
        sent = []
        monkeypatch.setattr(search, "search", lambda query, **kw: sent.append(query) or iter(()))
        search.cone_search(SkyCoord(53.1, -27.8, unit="deg"), Angle(0.5, "deg"))
        assert sent[0]["query"]["bool"]["filter"] == sky_distance_clause((53.1, -27.8, 0.5))

    def test_the_tools_use_these_very_functions_not_copies(self):
        """Two copies drift apart, and a fix in one quietly misses the other."""
        import pathlib
        import sys
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
        import asteroid_probe

        # defined in antares_api, not in the tool (identity is fragile here: the deferred-import
        # tests above reload the module)
        assert asteroid_probe.is_solar_system.__module__ == "rubin_qa.antares_api"
        assert asteroid_probe.locus_survey_ids.__module__ == "rubin_qa.antares_api"
        tools = pathlib.Path(__file__).resolve().parents[1] / "tools"
        for path in tools.glob("*.py"):
            text = path.read_text()
            for marker in ('"ztf_ssnamenr") not in', '{"sky_distance": {', "TransportError, AntaresException"):
                assert marker not in text, f"{path.name} re-implements an antares_api helper ({marker})"
