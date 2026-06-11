"""Test version consistency in the package."""

import mcp_client_for_ollama


def test_version_exists():
    """Test that the package has a version."""
    assert hasattr(mcp_client_for_ollama, "__version__")
    assert isinstance(mcp_client_for_ollama.__version__, str)
    assert mcp_client_for_ollama.__version__ != ""
