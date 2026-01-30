"""Tests for LMN API client."""

from unittest.mock import patch, MagicMock

import pytest

from src.lmn.api import (
    get_lmn_token,
    get_job_matching,
    build_mapping_from_lmn,
    load_mapping_from_lmn_api,
)


# Sample API response matching real format
SAMPLE_LMN_RESPONSE = [
    {
        "JobsiteID": 67135,
        "LMNAccountID": 7473,
        "AccountingID": "313",
        "JobName": "Roe, Sandra",
        "CustomerName": "Roe, Sandra",
        "JobAddress": "329 Lindley Place, Bozeman, Montana 59715",
    },
    {
        "JobsiteID": 67137,
        "LMNAccountID": 7473,
        "AccountingID": "456",
        "JobName": "Smith Residence",
        "CustomerName": "Smith, John",
        "JobAddress": "123 Main St, Bozeman, Montana 59715",
    },
    {
        "JobsiteID": 67139,
        "LMNAccountID": 7474,
        "AccountingID": "",  # Empty - should be skipped
        "JobName": "Test Job",
        "CustomerName": "Test Customer",
    },
]


class TestGetLmnToken:
    """Tests for get_lmn_token function."""

    def test_returns_token_when_set(self):
        """Token is returned when environment variable is set."""
        with patch.dict("os.environ", {"LMN_API_TOKEN": "test_token_123"}):
            assert get_lmn_token() == "test_token_123"

    def test_returns_none_when_not_set(self):
        """None is returned when environment variable is not set."""
        with patch.dict("os.environ", {}, clear=True):
            assert get_lmn_token() is None


class TestGetJobMatching:
    """Tests for get_job_matching function."""

    def test_raises_when_no_token(self):
        """Raises ValueError when LMN_API_TOKEN is not set."""
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="LMN_API_TOKEN"):
                get_job_matching()

    def test_returns_json_response(self):
        """Returns parsed JSON from API response."""
        mock_response = MagicMock()
        mock_response.json.return_value = SAMPLE_LMN_RESPONSE

        with patch.dict("os.environ", {"LMN_API_TOKEN": "test_token"}):
            with patch("src.lmn.api.requests.get", return_value=mock_response) as mock_get:
                result = get_job_matching()

                assert result == SAMPLE_LMN_RESPONSE
                mock_get.assert_called_once()
                # Verify auth header
                call_kwargs = mock_get.call_args[1]
                assert "Bearer test_token" in call_kwargs["headers"]["Authorization"]

    def test_raises_on_http_error(self):
        """Raises exception on HTTP error."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception("401 Unauthorized")

        with patch.dict("os.environ", {"LMN_API_TOKEN": "bad_token"}):
            with patch("src.lmn.api.requests.get", return_value=mock_response):
                with pytest.raises(Exception):
                    get_job_matching()


class TestBuildMappingFromLmn:
    """Tests for build_mapping_from_lmn function."""

    def test_builds_mapping_dict(self):
        """Converts API response to CustomerMapping dict."""
        result = build_mapping_from_lmn(SAMPLE_LMN_RESPONSE)

        assert len(result) == 2  # Third item has empty AccountingID
        assert "67135" in result
        assert "67137" in result
        assert "67139" not in result  # Skipped due to empty AccountingID

    def test_mapping_values(self):
        """CustomerMapping objects have correct values."""
        result = build_mapping_from_lmn(SAMPLE_LMN_RESPONSE)

        mapping = result["67135"]
        assert mapping.jobsite_id == "67135"
        assert mapping.qbo_customer_id == "313"
        assert mapping.qbo_display_name == "Roe, Sandra"
        assert mapping.notes == "From LMN API"

    def test_uses_jobname_when_no_customer_name(self):
        """Falls back to JobName when CustomerName is empty."""
        data = [{"JobsiteID": 123, "AccountingID": "456", "JobName": "Test Job", "CustomerName": ""}]
        result = build_mapping_from_lmn(data)

        assert result["123"].qbo_display_name == "Test Job"

    def test_handles_empty_list(self):
        """Returns empty dict for empty input."""
        result = build_mapping_from_lmn([])
        assert result == {}

    def test_skips_entries_without_jobsite_id(self):
        """Skips entries missing JobsiteID."""
        data = [{"AccountingID": "123", "CustomerName": "Test"}]
        result = build_mapping_from_lmn(data)
        assert result == {}


class TestLoadMappingFromLmnApi:
    """Tests for load_mapping_from_lmn_api convenience function."""

    def test_fetches_and_builds_mapping(self):
        """Fetches from API and builds mapping."""
        with patch("src.lmn.api.get_job_matching", return_value=SAMPLE_LMN_RESPONSE):
            result = load_mapping_from_lmn_api()

            assert len(result) == 2
            assert "67135" in result
            assert "67137" in result
