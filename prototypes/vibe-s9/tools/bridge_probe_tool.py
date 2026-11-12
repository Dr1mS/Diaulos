"""S9 probe: a real BaseTool loaded from $VIBE_HOME/tools/, permission ALWAYS
by default. Returns the text, ctx.session_id and the pid, and logs each call
to $S9_LOG (default: <run>/tool.jsonl, two levels above this file)."""
from __future__ import annotations

import json
import os
import time
from collections.abc import AsyncGenerator
from pathlib import Path

from pydantic import BaseModel, Field

from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolPermission,
)
from vibe.core.types import ToolStreamEvent

LOG = Path(os.environ.get("S9_LOG") or Path(__file__).resolve().parents[2] / "tool.jsonl")


def _log(rec: dict) -> None:
    rec = {"t": round(time.time(), 3), "pid": os.getpid(), **rec}
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


_log({"event": "import", "module": __name__})


class BridgeEchoArgs(BaseModel):
    text: str = Field(description="Text to echo back")


class BridgeEchoResult(BaseModel):
    text: str
    session_id: str | None
    pid: int


class BridgeEchoConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


class BridgeEchoState(BaseToolState):
    pass


class BridgeEcho(
    BaseTool[BridgeEchoArgs, BridgeEchoResult, BridgeEchoConfig, BridgeEchoState]
):
    description = "Echo a text back, with the current session id and process id."

    async def run(
        self, args: BridgeEchoArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | BridgeEchoResult, None]:
        sid = ctx.session_id if ctx else None
        _log({
            "event": "run",
            "text": args.text,
            "session_id": sid,
            "session_dir": str(ctx.session_dir) if ctx and ctx.session_dir else None,
            "has_ctx": ctx is not None,
            "permission": str(self.config.permission),
        })
        yield BridgeEchoResult(text=args.text, session_id=sid, pid=os.getpid())
