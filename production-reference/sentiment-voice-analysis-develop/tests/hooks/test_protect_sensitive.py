"""End-to-end tests for the protect_sensitive PreToolUse hook.

The hook reads a JSON tool-call description on stdin and exits 2 to block the
call or 0 to allow it. These tests drive it as a subprocess (exactly how Claude
Code invokes it) and assert on the exit code.

Regression coverage for finding P1-20: PowerShell (the primary shell on the
Windows dev box) and Grep must be guarded, not just Bash / Read / Write / Edit.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / ".claude" / "hooks" / "protect_sensitive.py"

BLOCK = 2
ALLOW = 0


def _run(tool_name: str, tool_input: dict) -> int:
    """Invoke the hook with a tool call and return its exit code."""
    payload = json.dumps({"tool_name": tool_name, "tool_input": tool_input})
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
    )
    return result.returncode


@pytest.mark.parametrize(
    ("tool_name", "tool_input"),
    [
        # PowerShell — the primary shell on Windows (the core P1-20 gap)
        ("PowerShell", {"command": "Get-Content .env"}),
        ("PowerShell", {"command": "gc .env"}),
        ("PowerShell", {"command": "Get-Content ./config/service_account.pem"}),
        ("PowerShell", {"command": "Select-String -Pattern api_key .env"}),
        ("PowerShell", {"command": "$env:CLIENT_SECRET"}),
        ("PowerShell", {"command": "Write-Output $env:AZURE_API_TOKEN"}),
        ("PowerShell", {"command": "Get-ChildItem Env:"}),
        ("PowerShell", {"command": "gci Env: | Format-List"}),
        ("PowerShell", {"command": "cat .env"}),  # POSIX alias → bash-pattern fallback
        # Grep — matched by the hook config but previously had no branch
        ("Grep", {"path": ".env", "pattern": "."}),
        ("Grep", {"path": "config/credentials.json", "pattern": "key"}),
        # Bash — unchanged, still blocked
        ("Bash", {"command": "cat .env"}),
        ("Bash", {"command": "printenv"}),
        # File tools — unchanged, still blocked
        ("Read", {"file_path": ".env"}),
        ("Read", {"file_path": "certs/server.key"}),
    ],
)
def test_blocks_sensitive_access(tool_name, tool_input):
    """Sensitive reads/searches/dumps are hard-blocked (exit 2)."""
    assert _run(tool_name, tool_input) == BLOCK


@pytest.mark.parametrize(
    ("tool_name", "tool_input"),
    [
        ("PowerShell", {"command": "Get-Content README.md"}),
        ("PowerShell", {"command": "Get-ChildItem ."}),
        ("PowerShell", {"command": "$env:PATH"}),
        ("Grep", {"path": "src", "pattern": "def main"}),
        ("Grep", {"pattern": "TODO"}),  # no path → cwd search, allowed
        ("Read", {"file_path": "README.md"}),
        ("Read", {"file_path": ".env.example"}),  # template file is safe
        ("Bash", {"command": "ls -la"}),
    ],
)
def test_allows_benign_access(tool_name, tool_input):
    """Ordinary, non-sensitive calls are allowed through (exit 0)."""
    assert _run(tool_name, tool_input) == ALLOW
