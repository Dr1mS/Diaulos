#!/usr/bin/env python3
"""Runs a command as the child of a process named vibe-app-server, as vibe runs its hooks and MCP
servers (tests/test_vibe_hook_mcp.py):

    bash -c 'exec -a vibe-app-server "$0" "$@"' python3 as_app_server.py <command...>

stdin and stdout pass through; exits with the command's code.
"""
import subprocess
import sys

sys.exit(subprocess.call(sys.argv[1:]))
