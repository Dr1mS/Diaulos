"""Diaulos tools for Mistral Vibe: bridge_send and bridge_list_agents (docs/VIBE-DESIGN.md §4.1).

The installer copies this file to $VIBE_HOME/tools/agent_bridge.py and writes the
bridge directory below. vibe imports it in every process (TUI, vibe -p, vibe-acp)
while it builds its tool list; importing it also starts the Diaulos plugin
(bridge/vibe_plugin.py, loaded by path from the bridge directory), once per process.
Both tools run without asking (ToolPermission.ALWAYS): they only write to the
bridge's own files. Errors go to <bridge>/state/vibe-plugin.log, never to vibe.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from collections.abc import AsyncGenerator

from pydantic import BaseModel, Field

from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolPermission,
)
from vibe.core.types import ToolStreamEvent

BRIDGE_DIR = "@AGENT_BRIDGE_HOME@"  # written by the installer
if BRIDGE_DIR.startswith("@"):
    BRIDGE_DIR = os.environ.get("AGENT_BRIDGE_HOME") or os.path.expanduser("~/.agent-bridge")
os.environ.setdefault("AGENT_BRIDGE_HOME", BRIDGE_DIR)
PLUGIN_NAME = "agent_bridge_vibe_plugin"


def _log(text: str) -> None:
    try:
        folder = os.path.join(BRIDGE_DIR, "state")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "vibe-plugin.log"), "a", encoding="utf-8") as f:
            f.write(f"pid {os.getpid()} shim: {text}\n")
    except Exception:
        pass


def _plugin():
    """The plugin module, loaded by path (no sys.path change in vibe's process)."""
    module = sys.modules.get(PLUGIN_NAME)
    if module is not None:
        return module
    path = os.path.join(BRIDGE_DIR, "vibe_plugin.py")
    spec = importlib.util.spec_from_file_location(PLUGIN_NAME, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"no Diaulos plugin at {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[PLUGIN_NAME] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[PLUGIN_NAME]
        raise
    return module


try:
    _plugin().start()
except Exception as e:  # vibe must start even if the bridge is broken
    _log(f"plugin not started: {e!r}")


def _call(name: str, *args, **kwargs) -> str:
    try:
        return str(getattr(_plugin(), name)(*args, **kwargs))
    except SystemExit as e:
        return f"error: {e}"
    except Exception as e:
        _log(f"{name} failed: {e!r}")
        log = os.path.join(BRIDGE_DIR, "state", "vibe-plugin.log")
        return f"error: Diaulos is not available ({e!r}); see {log}"


class BridgeResult(BaseModel):
    result: str


class BridgeSendArgs(BaseModel):
    to: str = Field(description="Address of the agent to message, e.g. claude:<name>, agy:<id>, "
                                "vibe:<id>, ext:<name>, remote:<machine>/<address>; the `from` of a "
                                "message you received is the address to answer")
    text: str = Field(description="The message")


class BridgeSendConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


class BridgeSendState(BaseToolState):
    pass


class BridgeSend(BaseTool[BridgeSendArgs, BridgeResult, BridgeSendConfig, BridgeSendState]):
    description = ("Send a message through Diaulos (the agent bridge) to another agent: a Claude Code, "
                   "agy or vibe session on this machine, or a remote one. Use it to answer a message "
                   "that came from the bridge. The answer, if any, arrives later as a new message.")

    async def run(self, args: BridgeSendArgs, ctx: InvokeContext | None = None
                  ) -> AsyncGenerator[ToolStreamEvent | BridgeResult, None]:
        sid = ctx.session_id if ctx else None
        yield BridgeResult(result=_call("send", args.to, args.text, session_id=sid))


class BridgeListAgentsArgs(BaseModel):
    all: bool = Field(default=False, description="List conversations of any age")
    days: int | None = Field(default=None, description="List conversations of the last `days` days "
                                                       "(default 7)")


class BridgeListAgentsConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


class BridgeListAgentsState(BaseToolState):
    pass


class BridgeListAgents(BaseTool[BridgeListAgentsArgs, BridgeResult, BridgeListAgentsConfig,
                                BridgeListAgentsState]):
    description = "List the agents reachable through Diaulos (the agent bridge), with their addresses."

    async def run(self, args: BridgeListAgentsArgs, ctx: InvokeContext | None = None
                  ) -> AsyncGenerator[ToolStreamEvent | BridgeResult, None]:
        sid = ctx.session_id if ctx else None
        yield BridgeResult(result=_call("list_agents", all=args.all, days=args.days, session_id=sid))
