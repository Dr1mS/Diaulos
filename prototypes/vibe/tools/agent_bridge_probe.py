"""Throwaway vibe "tool" file, loaded by vibe's ToolManager from
$VIBE_HOME/tools/ with exec_module, inside the vibe process. It defines no
BaseTool class, so it adds no tool; it only runs code at import.

It logs to $PROBE_DIR/plugin.jsonl:
- each import (pid, argv, whether the TUI module is loaded),
- when it finds the running VibeApp instance (TUI only),
- each delivery of $PROBE_DIR/inject.txt: the file is claimed, then a
  coroutine runs on the app's event loop. If the agent is idle and the input
  is showing, it calls app._handle_user_message(text), which mounts the
  message and starts a turn (the same path as vibe's own /loop runner);
  otherwise it calls app._enqueue_prompt_with_resources(text), which puts it
  in vibe's prompt queue, drained at the end of the turn.
"""

import gc
import json
import os
import sys
import threading
import time

_DIR = os.environ.get("PROBE_DIR", "/tmp")
_LOG = os.path.join(_DIR, "plugin.jsonl")


def _log(kind, **data):
    rec = {"t": round(time.time(), 3), "pid": os.getpid(), "kind": kind, **data}
    with open(_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _find_app():
    mod = sys.modules.get("vibe.cli.textual_ui.app")
    if mod is None:
        return None
    cls = getattr(mod, "VibeApp", None)
    if cls is None:
        return None
    for obj in gc.get_objects():
        if isinstance(obj, cls):
            return obj
    return None


async def _deliver(app, text, t_file):
    from vibe.cli.textual_ui.app import BottomApp

    idle = (not app._agent_running) and app._current_bottom_app == BottomApp.Input
    path = "handle_user_message" if idle else "enqueue_prompt"
    _log("deliver", path=path, agent_running=app._agent_running,
         bottom=str(app._current_bottom_app), since_file=round(time.time() - t_file, 3))
    if idle:
        await app._handle_user_message(text)
    else:
        await app._enqueue_prompt_with_resources(text)


def _watch():
    app = None
    started = time.time()
    while app is None:
        app = _find_app()
        if app is None:
            if time.time() - started > 120:
                _log("no-app", tui_loaded="vibe.cli.textual_ui.app" in sys.modules)
                return
            time.sleep(0.5)
    _log("app-found", after=round(time.time() - started, 3), cls=type(app).__name__)
    inject = os.path.join(_DIR, "inject.txt")
    while True:
        if os.path.exists(inject):
            claimed = inject + ".claimed"
            try:
                t_file = os.stat(inject).st_mtime
                os.replace(inject, claimed)
                with open(claimed, encoding="utf-8") as f:
                    text = f.read()
                os.remove(claimed)
            except OSError as e:
                _log("claim-error", error=repr(e))
            else:
                _log("claimed", since_file=round(time.time() - t_file, 3), text=text[:200])
                try:
                    app.call_from_thread(_deliver, app, text, t_file)
                except Exception as e:  # app closed, loop gone, ...
                    _log("deliver-error", error=repr(e))
                    return
        time.sleep(0.1)


_log("import", argv=sys.argv, module=__name__,
     tui_loaded="vibe.cli.textual_ui.app" in sys.modules,
     threads=[t.name for t in threading.enumerate()])
if os.environ.get("PROBE_PLUGIN_WATCH", "1") == "1" and not any(
        t.name == "agent-bridge-probe" for t in threading.enumerate()):
    threading.Thread(target=_watch, name="agent-bridge-probe", daemon=True).start()
