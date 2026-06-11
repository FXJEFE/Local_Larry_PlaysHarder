#!/usr/bin/env python
"""Command-line interface for the MCP Client for Ollama."""

# Import the CLI functionality from the main package
from mcp_client_for_ollama.cli import run_cli

# Re-export the run_cli function
if __name__ == "__main__":
    run_cli()
