"""Tests for rubin_qa.client."""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from alerce.exceptions import APIError, ObjectNotFoundError

from rubin_qa.client import _api_call, fetch_candidates, fetch_object_data


class TestApiCall:
    def test_success_returns_result(self):
        fn = MagicMock(return_value="ok")
        result, err = _api_call(fn, "arg")
        assert result == "ok"
        assert err is None

    def test_object_not_found(self):
        fn = MagicMock(side_effect=ObjectNotFoundError("gone"))
        result, err = _api_call(fn)
        assert result is None
        assert err == "not_found"

    def test_api_error_exhausts_retries(self):
        fn = MagicMock(side_effect=APIError("rate limited"))
        with patch("rubin_qa.client.time.sleep"):
            result, err = _api_call(fn)
        assert result is None
        assert "rate limited" in err

    def test_api_error_retries_before_failing(self):
        fn = MagicMock(side_effect=APIError("boom"))
        with patch("rubin_qa.client.time.sleep") as mock_sleep:
            _api_call(fn)
        # RETRY_ATTEMPTS=2 → one retry → one sleep
        assert mock_sleep.call_count == 1

    def test_generic_exception_no_retry(self):
        fn = MagicMock(side_effect=ValueError("unexpected"))
        with patch("rubin_qa.client.time.sleep") as mock_sleep:
            result, err = _api_call(fn)
        assert result is None
        assert "unexpected" in err
        mock_sleep.assert_not_called()


class TestFetchCandidates:
    def _mock_objects(self, oids):
        return pd.DataFrame({"oid": oids})

    def test_returns_deduplicated_oids(self):
        df = self._mock_objects(["ZTF1", "ZTF2", "ZTF1"])
        with patch("rubin_qa.client._client") as mock_client:
            mock_client.query_objects.return_value = df
            result = fetch_candidates(page_size=3)
        assert result == ["ZTF1", "ZTF2"]

    def test_normalises_lsst_integer_oids_to_str(self):
        df = self._mock_objects([100001, 100002])
        with patch("rubin_qa.client._client") as mock_client:
            mock_client.query_objects.return_value = df
            result = fetch_candidates(survey="lsst")
        assert result == ["100001", "100002"]

    def test_returns_empty_list_on_api_error(self):
        with patch("rubin_qa.client._client") as mock_client:
            mock_client.query_objects.side_effect = APIError("down")
            with patch("rubin_qa.client.time.sleep"):
                with pytest.warns(UserWarning, match="fetch_candidates"):
                    result = fetch_candidates()
        assert result == []

    def test_returns_empty_list_on_empty_result(self):
        with patch("rubin_qa.client._client") as mock_client:
            mock_client.query_objects.return_value = pd.DataFrame({"oid": []})
            with pytest.warns(UserWarning, match="fetch_candidates"):
                result = fetch_candidates()
        assert result == []


class TestFetchObjectData:
    def test_returns_all_three_dataframes(self, ztf_dets, ztf_ms, probs_clean):
        with patch("rubin_qa.client._client") as mock_client:
            mock_client.query_detections.return_value    = ztf_dets
            mock_client.query_magstats.return_value      = ztf_ms
            mock_client.query_probabilities.return_value = probs_clean
            result = fetch_object_data("ZTF1")
        assert not result["dets"].empty
        assert not result["ms"].empty
        assert not result["probs"].empty
        assert result["fetch_errors"] == []

    def test_magstats_not_implemented_is_silent_for_lsst(self, ztf_dets, probs_clean):
        """LSST magstats NotImplementedError must not appear in fetch_errors."""
        with patch("rubin_qa.client._client") as mock_client:
            mock_client.query_detections.return_value    = ztf_dets
            mock_client.query_magstats.side_effect       = Exception(
                "Multisurvey query_magstats not implemented."
            )
            mock_client.query_probabilities.return_value = probs_clean
            result = fetch_object_data("100001", survey="lsst")
        assert result["ms"].empty
        assert not any("magstats" in e for e in result["fetch_errors"])

    def test_detection_failure_recorded_in_fetch_errors(self, ztf_ms, probs_clean):
        with patch("rubin_qa.client._client") as mock_client:
            mock_client.query_detections.side_effect     = APIError("timeout")
            mock_client.query_magstats.return_value      = ztf_ms
            mock_client.query_probabilities.return_value = probs_clean
            with patch("rubin_qa.client.time.sleep"):
                result = fetch_object_data("ZTF1")
        assert result["dets"].empty
        assert any("detections" in e for e in result["fetch_errors"])
