"""Tests for customer mapping with LMN API integration."""

from unittest.mock import MagicMock, patch

import pytest

from src.mapping.customer_mapping import (
    load_mapping_from_lmn_api,
    CustomerMapping,
)


class TestLoadMappingFromLmnApi:
    """Test load_mapping_from_lmn_api function."""

    def test_loads_from_api_with_db_overrides(self):
        """Loads from LMN API and applies database overrides."""
        api_mappings = {
            "67135": CustomerMapping("67135", "313", "API Customer", "from LMN API"),
            "67200": CustomerMapping("67200", "400", "Another Customer", "from LMN API"),
        }

        db_overrides = {
            "67135": CustomerMapping("67135", "999", "Override Customer", "manual"),
        }

        # Patch the LMN API module's function that gets imported
        with patch("src.lmn.api.load_mapping_from_lmn_api", return_value=api_mappings):
            with patch("src.db.customer_overrides.get_customer_overrides", return_value=db_overrides):
                mappings = load_mapping_from_lmn_api(use_db_overrides=True)

        assert len(mappings) == 2
        # Override should replace API mapping
        assert mappings["67135"].qbo_customer_id == "999"
        assert mappings["67135"].qbo_display_name == "Override Customer"
        # Non-overridden mapping stays from API
        assert mappings["67200"].qbo_customer_id == "400"

    def test_loads_from_api_with_csv_overrides(self):
        """Loads from LMN API and applies CSV overrides when use_db_overrides=False."""
        api_mappings = {
            "67135": CustomerMapping("67135", "313", "API Customer", "from LMN API"),
        }

        csv_overrides = {
            "67135": CustomerMapping("67135", "888", "CSV Override", "from csv"),
        }

        with patch("src.lmn.api.load_mapping_from_lmn_api", return_value=api_mappings):
            with patch("src.mapping.customer_mapping.load_customer_mapping", return_value=csv_overrides):
                mappings = load_mapping_from_lmn_api(use_db_overrides=False)

        assert mappings["67135"].qbo_customer_id == "888"
        assert mappings["67135"].notes == "from csv"

    def test_returns_api_mappings_when_no_overrides(self):
        """Returns API mappings unchanged when no overrides exist."""
        api_mappings = {
            "67135": CustomerMapping("67135", "313", "API Customer", "from LMN API"),
        }

        with patch("src.lmn.api.load_mapping_from_lmn_api", return_value=api_mappings):
            with patch("src.db.customer_overrides.get_customer_overrides", return_value={}):
                mappings = load_mapping_from_lmn_api(use_db_overrides=True)

        assert len(mappings) == 1
        assert mappings["67135"].qbo_customer_id == "313"
