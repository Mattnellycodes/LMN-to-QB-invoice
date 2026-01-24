"""Tests for QBO customer operations."""

import os
from unittest.mock import MagicMock, patch

import pytest

from src.qbo.customers import (
    get_api_base_url,
    get_all_customers,
    get_customer_by_id,
    search_customers_by_name,
)


class TestGetApiBaseUrl:
    """Test get_api_base_url function."""

    def test_returns_production_url_by_default(self):
        """Returns production URL when QBO_ENVIRONMENT not set."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("QBO_ENVIRONMENT", None)
            url = get_api_base_url()

        assert url == "https://quickbooks.api.intuit.com/v3/company"

    def test_returns_production_url_explicitly(self):
        """Returns production URL when QBO_ENVIRONMENT is 'production'."""
        with patch.dict(os.environ, {"QBO_ENVIRONMENT": "production"}):
            url = get_api_base_url()

        assert url == "https://quickbooks.api.intuit.com/v3/company"

    def test_returns_sandbox_url(self):
        """Returns sandbox URL when QBO_ENVIRONMENT is 'sandbox'."""
        with patch.dict(os.environ, {"QBO_ENVIRONMENT": "sandbox"}):
            url = get_api_base_url()

        assert url == "https://sandbox-quickbooks.api.intuit.com/v3/company"


class TestGetAllCustomers:
    """Test get_all_customers function."""

    def test_returns_customers_list(self):
        """Returns list of customers from QBO API."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "QueryResponse": {
                "Customer": [
                    {"Id": "1", "DisplayName": "Customer One"},
                    {"Id": "2", "DisplayName": "Customer Two"},
                ]
            }
        }
        mock_response.raise_for_status = MagicMock()

        with patch("src.qbo.customers.get_qbo_credentials", return_value=("token", "realm")):
            with patch("src.qbo.customers.requests.get", return_value=mock_response):
                customers = get_all_customers()

        assert len(customers) == 2
        assert customers[0]["Id"] == "1"
        assert customers[1]["DisplayName"] == "Customer Two"

    def test_handles_pagination(self):
        """Handles multiple pages of results."""
        first_response = MagicMock()
        first_response.json.return_value = {
            "QueryResponse": {
                "Customer": [{"Id": str(i)} for i in range(1000)]
            }
        }
        first_response.raise_for_status = MagicMock()

        second_response = MagicMock()
        second_response.json.return_value = {
            "QueryResponse": {
                "Customer": [{"Id": "1001"}, {"Id": "1002"}]
            }
        }
        second_response.raise_for_status = MagicMock()

        with patch("src.qbo.customers.get_qbo_credentials", return_value=("token", "realm")):
            with patch("src.qbo.customers.requests.get", side_effect=[first_response, second_response]):
                customers = get_all_customers()

        assert len(customers) == 1002

    def test_returns_empty_list_when_no_customers(self):
        """Returns empty list when no customers found."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"QueryResponse": {}}
        mock_response.raise_for_status = MagicMock()

        with patch("src.qbo.customers.get_qbo_credentials", return_value=("token", "realm")):
            with patch("src.qbo.customers.requests.get", return_value=mock_response):
                customers = get_all_customers()

        assert customers == []


class TestGetCustomerById:
    """Test get_customer_by_id function."""

    def test_returns_customer(self):
        """Returns customer when found."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "Customer": {"Id": "123", "DisplayName": "Test Customer"}
        }
        mock_response.raise_for_status = MagicMock()

        with patch("src.qbo.customers.get_qbo_credentials", return_value=("token", "realm")):
            with patch("src.qbo.customers.requests.get", return_value=mock_response):
                customer = get_customer_by_id("123")

        assert customer["Id"] == "123"
        assert customer["DisplayName"] == "Test Customer"

    def test_returns_none_when_not_found(self):
        """Returns None when customer not found (404)."""
        mock_response = MagicMock()
        mock_response.status_code = 404

        with patch("src.qbo.customers.get_qbo_credentials", return_value=("token", "realm")):
            with patch("src.qbo.customers.requests.get", return_value=mock_response):
                customer = get_customer_by_id("999")

        assert customer is None


class TestSearchCustomersByName:
    """Test search_customers_by_name function."""

    def test_returns_matching_customers(self):
        """Returns customers matching search query."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "QueryResponse": {
                "Customer": [
                    {"Id": "1", "DisplayName": "Smith Residence"},
                    {"Id": "2", "DisplayName": "Smithson Corp"},
                ]
            }
        }
        mock_response.raise_for_status = MagicMock()

        with patch("src.qbo.customers.get_qbo_credentials", return_value=("token", "realm")):
            with patch("src.qbo.customers.requests.get", return_value=mock_response):
                customers = search_customers_by_name("Smith")

        assert len(customers) == 2

    def test_escapes_single_quotes(self):
        """Escapes single quotes in search query."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"QueryResponse": {"Customer": []}}
        mock_response.raise_for_status = MagicMock()

        with patch("src.qbo.customers.get_qbo_credentials", return_value=("token", "realm")):
            with patch("src.qbo.customers.requests.get", return_value=mock_response) as mock_get:
                search_customers_by_name("O'Brien")

        # Check the query was escaped
        call_args = mock_get.call_args
        query = call_args[1]["params"]["query"]
        assert "O\\'Brien" in query

    def test_returns_empty_list_when_no_matches(self):
        """Returns empty list when no matches found."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"QueryResponse": {}}
        mock_response.raise_for_status = MagicMock()

        with patch("src.qbo.customers.get_qbo_credentials", return_value=("token", "realm")):
            with patch("src.qbo.customers.requests.get", return_value=mock_response):
                customers = search_customers_by_name("NonexistentCustomer")

        assert customers == []
