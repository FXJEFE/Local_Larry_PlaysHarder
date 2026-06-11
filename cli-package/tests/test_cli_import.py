"""Test basic CLI package functionality."""

import importlib.util
import os
import sys


def test_cli_module_exists():
    """Test that the CLI module exists and can be imported."""
    # Check if the module file exists
    cli_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ollmcp", "cli.py")
    assert os.path.isfile(cli_path), "CLI module file not found"
    
    # Try importing the module
    spec = importlib.util.spec_from_file_location("ollmcp.cli", cli_path)
    assert spec is not None, "Failed to create module spec"
    
    cli_module = importlib.util.module_from_spec(spec)
    sys.modules["ollmcp.cli"] = cli_module
    
    try:
        spec.loader.exec_module(cli_module)
        assert hasattr(cli_module, "run_cli"), "CLI module missing run_cli function"
    except Exception as e:
        assert False, f"Failed to import CLI module: {e}"
