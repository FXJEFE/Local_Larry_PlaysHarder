# Larry G-Force v2.1 - Clean Production Distribution

This folder contains the clean, production-ready version of the Larry G-Force agent system.

**Skills Philosophy**: Skills are converted into high-quality, fully local Python + Ollama executable MCP variants. No external skill marketplaces. Everything runs on your machine with maximum control and privacy.

## Folder Structure (Professional Framework)

```
GITHUB/
├── src/                    # Main editable source (agent, managers, etc.)
├── config/                 # All configuration (mcp.json, larry_config.json, etc.)
├── prompts/                # System prompts and agent personality
├── skills/                 # Local Python + Ollama MCP skill implementations
├── mcp/                    # MCP client + servers (including fxjefe-local-mcp)
├── core/                   # Core logic (if split from src)
├── tools/                  # Tool wrappers (kali, web, etc.)
├── api/                    # API layer
├── scripts/                # Management & activation scripts
├── logs/                   # Logging framework
├── db/                     # Databases
├── data/                   # User data, RAG, sandbox, etc.
├── docs/                   # Documentation
└── build_locked.py         # Build script for protected executable version
```

## Two Versions

### 1. Editable / Original Version (Recommended for development)
- Located in `src/`
- Fully readable and modifiable.
- Use with `python manage_larry.py ...` or the launchers.

### 2. Locked / Encrypted Version (For distribution / protection)
- Built using `build_locked.py`
- Uses **PyInstaller** (single .exe) + **PyArmor** (code obfuscation + encryption).
- Protects your intellectual property against casual reverse engineering or theft.
- All user-specific settings live in an external `user_config.json` next to the `.exe`.
  - This allows the same executable to be used on multiple PCs with different accounts/tokens.

**To build the locked version:**
```powershell
cd GITHUB
python build_locked.py
```

The resulting executable will be in `dist/LarryGForce-Locked/`.

## Additional MCP Servers

This distribution includes an extra powerful local MCP server:

- **ClawLocal Security & Productivity Suite v1.0** (`mcp-servers/clawlocal/`)
  - PDF tools, browser automation (Playwright), static security scanning, prompt injection detection, safe file operations.
  - Run it separately with `python mcp-servers/clawlocal/claw_local_mcp_server.py`
  - Full README and requirements included in the subfolder.

This gives your agent even more powerful local capabilities while staying 100% offline and auditable.

## Quick Start (Editable)

```powershell
cd GITHUB
python -m venv .venv
.\.venv\Scripts\activate
pip install -r ..\requirements.txt          # adjust path if needed
python manage_larry.py setup
python manage_larry.py status
```

## Important Notes

- Never commit your real `.env` or `user_config.json` with secrets.
- The locked version still requires Ollama to be running on the target machine.
- For maximum protection, combine with code signing the .exe.

**Status**: Production ready (May 2026)
