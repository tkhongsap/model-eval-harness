"""
Comprehensive tests for token utility functions.

This module tests the token_utils functions including:
- gemini_cost(): Retrieves Gemini model cost details from SharePoint
- cal_gemini_cost(): Calculates cost for Gemini model usage
"""

from unittest.mock import Mock, patch

import pytest

from src.utils.token_utils import cal_gemini_cost, gemini_cost


class TestGeminiCost:
    """Test suite for gemini_cost function."""

    @pytest.fixture
    def mock_sharepoint_config(self):
        """Fixture for SharePoint configuration."""
        return {
            "control": {
                "client_id": "test_client_id",
                "client_secret": "test_client_secret",
                "tenant_id": "test_tenant_id",
                "site_domain": "test_domain",
                "site_path": "/sites/test",
                "gemini_cost_path": "/config/gemini_cost.yml",
            }
        }

    @pytest.fixture
    def mock_cost_config(self):
        """Fixture for cost configuration."""
        return """
batch:
  gemini-2.5-flash:
    pricing:
      input:
        - type: text
          tokens: 1000000
          cost: 0.075
          unit: USD
        - type: audio
          tokens: 1000000
          cost: 0.15
          unit: USD
      output:
        - type: text
          tokens: 1000000
          cost: 0.30
          unit: USD
  gemini-1.5-pro:
    pricing:
      input:
        - type: text
          tokens: 1000000
          cost: 1.25
          unit: USD
      output:
        - type: text
          tokens: 1000000
          cost: 5.00
          unit: USD
stream:
  gemini-2.5-flash:
    pricing:
      input:
        - type: text
          tokens: 1000000
          cost: 0.10
          unit: USD
      output:
        - type: text
          tokens: 1000000
          cost: 0.40
          unit: USD
"""

    @patch("src.utils.token_utils.load_yaml")
    @patch("src.utils.token_utils.SharePointModule")
    @patch("src.utils.token_utils.load_yaml_str")
    def test_gemini_cost_single_model_success(
        self, mock_load_yaml_str, mock_sharepoint, mock_load_yaml, mock_sharepoint_config, mock_cost_config
    ):
        """Test successful retrieval of cost details for a single model."""
        # Arrange
        mock_load_yaml.return_value = mock_sharepoint_config
        mock_sp_instance = Mock()
        mock_item = Mock()
        mock_item.content = mock_cost_config.encode("utf-8")
        mock_sp_instance.get_item_by_path.return_value = mock_item
        mock_sharepoint.return_value = mock_sp_instance

        from yaml import safe_load

        mock_load_yaml_str.return_value = safe_load(mock_cost_config)

        # Act
        result = gemini_cost("batch", ["gemini-2.5-flash"])

        # Assert
        assert len(result) == 3  # 2 input types + 1 output type
        assert result[0]["model"] == "gemini-2.5-flash"
        assert result[0]["type"] == "batch"
        assert result[0]["pricing_type"] == "input"
        assert result[0]["input_type"] == "text"
        assert result[0]["tokens"] == 1000000
        assert result[0]["cost"] == 0.075

    @patch("src.utils.token_utils.load_yaml")
    @patch("src.utils.token_utils.SharePointModule")
    @patch("src.utils.token_utils.load_yaml_str")
    def test_gemini_cost_multiple_models_success(
        self, mock_load_yaml_str, mock_sharepoint, mock_load_yaml, mock_sharepoint_config, mock_cost_config
    ):
        """Test successful retrieval of cost details for multiple models."""
        # Arrange
        mock_load_yaml.return_value = mock_sharepoint_config
        mock_sp_instance = Mock()
        mock_item = Mock()
        mock_item.content = mock_cost_config.encode("utf-8")
        mock_sp_instance.get_item_by_path.return_value = mock_item
        mock_sharepoint.return_value = mock_sp_instance

        from yaml import safe_load

        mock_load_yaml_str.return_value = safe_load(mock_cost_config)

        # Act
        result = gemini_cost("batch", ["gemini-2.5-flash", "gemini-1.5-pro"])

        # Assert
        assert len(result) == 5  # 3 for flash (2 input + 1 output) + 2 for pro (1 input + 1 output)
        models = {item["model"] for item in result}
        assert "gemini-2.5-flash" in models
        assert "gemini-1.5-pro" in models

    @patch("src.utils.token_utils.load_yaml")
    @patch("src.utils.token_utils.SharePointModule")
    @patch("src.utils.token_utils.load_yaml_str")
    def test_gemini_cost_nonexistent_model_warning(
        self, mock_load_yaml_str, mock_sharepoint, mock_load_yaml, mock_sharepoint_config, mock_cost_config
    ):
        """Test that non-existent model returns empty pricing with warning."""
        # Arrange
        mock_load_yaml.return_value = mock_sharepoint_config
        mock_sp_instance = Mock()
        mock_item = Mock()
        mock_item.content = mock_cost_config.encode("utf-8")
        mock_sp_instance.get_item_by_path.return_value = mock_item
        mock_sharepoint.return_value = mock_sp_instance

        from yaml import safe_load

        mock_load_yaml_str.return_value = safe_load(mock_cost_config)

        # Act
        result = gemini_cost("batch", ["nonexistent-model"])

        # Assert
        assert len(result) == 1
        assert result[0]["model"] == "nonexistent-model"
        assert result[0]["pricing_type"] is None
        assert result[0]["cost"] is None

    @patch("src.utils.token_utils.load_yaml")
    @patch("src.utils.token_utils.SharePointModule")
    @patch("src.utils.token_utils.load_yaml_str")
    def test_gemini_cost_empty_model_list(
        self, mock_load_yaml_str, mock_sharepoint, mock_load_yaml, mock_sharepoint_config, mock_cost_config
    ):
        """Test with empty model list."""
        # Arrange
        mock_load_yaml.return_value = mock_sharepoint_config
        mock_sp_instance = Mock()
        mock_item = Mock()
        mock_item.content = mock_cost_config.encode("utf-8")
        mock_sp_instance.get_item_by_path.return_value = mock_item
        mock_sharepoint.return_value = mock_sp_instance

        from yaml import safe_load

        mock_load_yaml_str.return_value = safe_load(mock_cost_config)

        # Act
        result = gemini_cost("batch", [])

        # Assert
        assert len(result) == 0

    @patch("src.utils.token_utils.load_yaml")
    @patch("src.utils.token_utils.SharePointModule")
    @patch("src.utils.token_utils.load_yaml_str")
    def test_gemini_cost_stream_api_type(
        self, mock_load_yaml_str, mock_sharepoint, mock_load_yaml, mock_sharepoint_config, mock_cost_config
    ):
        """Test with stream API type."""
        # Arrange
        mock_load_yaml.return_value = mock_sharepoint_config
        mock_sp_instance = Mock()
        mock_item = Mock()
        mock_item.content = mock_cost_config.encode("utf-8")
        mock_sp_instance.get_item_by_path.return_value = mock_item
        mock_sharepoint.return_value = mock_sp_instance

        from yaml import safe_load

        mock_load_yaml_str.return_value = safe_load(mock_cost_config)

        # Act
        result = gemini_cost("stream", ["gemini-2.5-flash"])

        # Assert
        assert len(result) == 2  # 1 input + 1 output
        assert result[0]["type"] == "stream"
        assert result[0]["cost"] == 0.10  # Stream pricing is different

    @patch("src.utils.token_utils.load_yaml")
    @patch("src.utils.token_utils.SharePointModule")
    def test_gemini_cost_sharepoint_connection_error(self, mock_sharepoint, mock_load_yaml, mock_sharepoint_config):
        """Test handling of SharePoint connection errors."""
        # Arrange
        mock_load_yaml.return_value = mock_sharepoint_config
        mock_sharepoint.side_effect = Exception("Connection failed")

        # Act & Assert
        with pytest.raises(Exception) as exc_info:
            gemini_cost("batch", ["gemini-2.5-flash"])
        assert "Connection failed" in str(exc_info.value)

    @patch("src.utils.token_utils.load_yaml")
    def test_gemini_cost_missing_config_file(self, mock_load_yaml):
        """Test handling of missing configuration file."""
        # Arrange
        mock_load_yaml.side_effect = FileNotFoundError("Config not found")

        # Act & Assert
        with pytest.raises(FileNotFoundError):
            gemini_cost("batch", ["gemini-2.5-flash"])

    @patch("src.utils.token_utils.load_yaml")
    @patch("src.utils.token_utils.SharePointModule")
    @patch("src.utils.token_utils.load_yaml_str")
    def test_gemini_cost_invalid_yaml_content(
        self, mock_load_yaml_str, mock_sharepoint, mock_load_yaml, mock_sharepoint_config
    ):
        """Test handling of invalid YAML content."""
        # Arrange
        mock_load_yaml.return_value = mock_sharepoint_config
        mock_sp_instance = Mock()
        mock_item = Mock()
        mock_item.content = b"invalid: yaml: content:"
        mock_sp_instance.get_item_by_path.return_value = mock_item
        mock_sharepoint.return_value = mock_sp_instance

        from yaml import YAMLError

        mock_load_yaml_str.side_effect = YAMLError("Invalid YAML")

        # Act & Assert
        with pytest.raises(Exception):
            gemini_cost("batch", ["gemini-2.5-flash"])

    @patch("src.utils.token_utils.load_yaml")
    @patch("src.utils.token_utils.SharePointModule")
    @patch("src.utils.token_utils.load_yaml_str")
    def test_gemini_cost_cost_per_token_calculation(
        self, mock_load_yaml_str, mock_sharepoint, mock_load_yaml, mock_sharepoint_config, mock_cost_config
    ):
        """Test that cost_per_token is calculated correctly."""
        # Arrange
        mock_load_yaml.return_value = mock_sharepoint_config
        mock_sp_instance = Mock()
        mock_item = Mock()
        mock_item.content = mock_cost_config.encode("utf-8")
        mock_sp_instance.get_item_by_path.return_value = mock_item
        mock_sharepoint.return_value = mock_sp_instance

        from yaml import safe_load

        mock_load_yaml_str.return_value = safe_load(mock_cost_config)

        # Act
        result = gemini_cost("batch", ["gemini-2.5-flash"])

        # Assert
        text_input = [r for r in result if r["input_type"] == "text" and r["pricing_type"] == "input"][0]
        expected_cost_per_token = 0.075 / 1000000
        assert text_input["cost_per_token"] == expected_cost_per_token

    @patch("src.utils.token_utils.load_yaml")
    @patch("src.utils.token_utils.SharePointModule")
    @patch("src.utils.token_utils.load_yaml_str")
    def test_gemini_cost_multiple_input_types(
        self, mock_load_yaml_str, mock_sharepoint, mock_load_yaml, mock_sharepoint_config, mock_cost_config
    ):
        """Test handling of multiple input types (text, audio)."""
        # Arrange
        mock_load_yaml.return_value = mock_sharepoint_config
        mock_sp_instance = Mock()
        mock_item = Mock()
        mock_item.content = mock_cost_config.encode("utf-8")
        mock_sp_instance.get_item_by_path.return_value = mock_item
        mock_sharepoint.return_value = mock_sp_instance

        from yaml import safe_load

        mock_load_yaml_str.return_value = safe_load(mock_cost_config)

        # Act
        result = gemini_cost("batch", ["gemini-2.5-flash"])

        # Assert
        input_types = {r["input_type"] for r in result if r["pricing_type"] == "input"}
        assert "text" in input_types
        assert "audio" in input_types


class TestCalGeminiCost:
    """Test suite for cal_gemini_cost function."""

    @pytest.fixture
    def sample_config(self):
        """Fixture for sample cost configuration."""
        return [
            {
                "type": "batch",
                "model": "gemini-2.5-flash",
                "pricing_type": "input",
                "input_type": "text",
                "tokens": 1000000,
                "cost": 0.075,
                "cost_per_token": 0.000000075,
                "unit": "USD",
            },
            {
                "type": "batch",
                "model": "gemini-2.5-flash",
                "pricing_type": "input",
                "input_type": "audio",
                "tokens": 1000000,
                "cost": 0.15,
                "cost_per_token": 0.00000015,
                "unit": "USD",
            },
            {
                "type": "batch",
                "model": "gemini-2.5-flash",
                "pricing_type": "output",
                "input_type": "text",
                "tokens": 1000000,
                "cost": 0.30,
                "cost_per_token": 0.0000003,
                "unit": "USD",
            },
            {
                "type": "batch",
                "model": "gemini-1.5-pro",
                "pricing_type": "input",
                "input_type": "text",
                "tokens": 1000000,
                "cost": 1.25,
                "cost_per_token": 0.00000125,
                "unit": "USD",
            },
        ]

    def test_cal_gemini_cost_basic_calculation(self, sample_config):
        """Test basic cost calculation."""
        # Act
        cost = cal_gemini_cost(sample_config, "gemini-2.5-flash", "input", "text", 500000)

        # Assert
        expected = (500000 / 1000000) * 0.075
        assert cost == round(expected, 6)

    def test_cal_gemini_cost_zero_tokens(self, sample_config):
        """Test cost calculation with zero tokens."""
        # Act
        cost = cal_gemini_cost(sample_config, "gemini-2.5-flash", "input", "text", 0)

        # Assert
        assert cost == 0.0

    def test_cal_gemini_cost_large_token_count(self, sample_config):
        """Test cost calculation with large token count."""
        # Act
        cost = cal_gemini_cost(sample_config, "gemini-2.5-flash", "input", "text", 10000000)

        # Assert
        expected = (10000000 / 1000000) * 0.075
        assert cost == round(expected, 6)

    def test_cal_gemini_cost_output_pricing(self, sample_config):
        """Test cost calculation for output tokens."""
        # Act
        cost = cal_gemini_cost(sample_config, "gemini-2.5-flash", "output", "text", 1000000)

        # Assert
        assert cost == 0.30

    def test_cal_gemini_cost_audio_input(self, sample_config):
        """Test cost calculation for audio input."""
        # Act
        cost = cal_gemini_cost(sample_config, "gemini-2.5-flash", "input", "audio", 1000000)

        # Assert
        assert cost == 0.15

    def test_cal_gemini_cost_different_model(self, sample_config):
        """Test cost calculation for different model."""
        # Act
        cost = cal_gemini_cost(sample_config, "gemini-1.5-pro", "input", "text", 1000000)

        # Assert
        assert cost == 1.25

    def test_cal_gemini_cost_nonexistent_model(self, sample_config):
        """Test cost calculation with non-existent model returns 0."""
        # Act
        cost = cal_gemini_cost(sample_config, "nonexistent-model", "input", "text", 1000000)

        # Assert
        assert cost == 0.0

    def test_cal_gemini_cost_nonexistent_pricing_type(self, sample_config):
        """Test cost calculation with non-existent pricing type returns 0."""
        # Act
        cost = cal_gemini_cost(sample_config, "gemini-2.5-flash", "invalid", "text", 1000000)

        # Assert
        assert cost == 0.0

    def test_cal_gemini_cost_nonexistent_input_type(self, sample_config):
        """Test cost calculation with non-existent input type returns 0."""
        # Act
        cost = cal_gemini_cost(sample_config, "gemini-2.5-flash", "input", "video", 1000000)

        # Assert
        assert cost == 0.0

    def test_cal_gemini_cost_empty_config(self):
        """Test cost calculation with empty configuration."""
        # Act
        cost = cal_gemini_cost([], "gemini-2.5-flash", "input", "text", 1000000)

        # Assert
        assert cost == 0.0

    def test_cal_gemini_cost_rounding_precision(self, sample_config):
        """Test that cost is rounded to 6 decimal places."""
        # Act
        cost = cal_gemini_cost(sample_config, "gemini-2.5-flash", "input", "text", 333333)

        # Assert
        # Verify it's rounded to 6 decimal places
        assert len(str(cost).split(".")[-1]) <= 6

    def test_cal_gemini_cost_fractional_tokens(self, sample_config):
        """Test cost calculation with fractional ratio."""
        # Act
        cost = cal_gemini_cost(sample_config, "gemini-2.5-flash", "input", "text", 123456)

        # Assert
        expected = (123456 / 1000000) * 0.075
        assert cost == round(expected, 6)

    def test_cal_gemini_cost_exact_token_match(self, sample_config):
        """Test cost calculation when tokens exactly match config."""
        # Act
        cost = cal_gemini_cost(sample_config, "gemini-2.5-flash", "input", "text", 1000000)

        # Assert
        assert cost == 0.075

    def test_cal_gemini_cost_negative_tokens(self, sample_config):
        """Test cost calculation with negative tokens (edge case)."""
        # Act
        cost = cal_gemini_cost(sample_config, "gemini-2.5-flash", "input", "text", -1000)

        # Assert
        # Should calculate even with negative (though not realistic)
        expected = (-1000 / 1000000) * 0.075
        assert cost == round(expected, 6)
