#!/usr/bin/env python3
"""Hooks of the Diaulos plugin for Mistral Vibe 3 (docs/VIBE-DESIGN.md §8.2, §8.9.5).
Python 3.8+, standard library only.

    vibe_hook.py session_start    vibe sends the hook payload as JSON on stdin
    vibe_hook.py pre_agent

The installer writes both commands into ~/.vibe/plugins/diaulos/ai.mistral.vibe/hooks.toml,
with the absolute path of its Python and of this file.

session_start: writes state/vibe-current/<vibe-app-server pid>.json with the main session
that this vibe-app-server serves now (after startup, resume, clear or fork). vibe_mcp.py reads
it to know the sender of `send`. The answer shows the address in the TUI (system_message) and
tells it to the model (additional_context, added to the next message).

pre_agent, three cases:
  - the turn was sent by the relay (its prompt is vibe's <session_message/> block from a "diaulos:"
    sender; vibe gives the hook that block alone, gate R6) or by vibe-run (the bridge header):
    nothing, or, when SHOW_RELAY_LINE is true, one line "message from <sender>: <start>";
  - a message the person typed, in a session that a live relay serves: nothing, the relay delivers;
  - a message the person typed, with no relay (a TUI opened with the real `vibe`): the waiting
    messages of the conversation are claimed and added after the person's message
    (additional_context), with one line "message(s) from <senders>" on screen.
  In a print-mode run of vibe-run (AGENT_BRIDGE_VIBE_RUN set), nothing: vibe-run delivers.

A hook never breaks vibe: any error gives an empty answer (vibe goes on) and one line in
state/vibe-hook.log, which never holds a message text.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bridge  # noqa: E402  (bridge.py ships next to this file)

SHOW_RELAY_LINE = False  # gate R6: True only if vibe's own display shows neither sender nor text
PREVIEW = 120


def log(text):
    try:
        path = bridge._path("state", "vibe-hook.log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write("%s %.3f pid %d %s\n" % (bridge._now(), time.time(), os.getpid(), text))
    except Exception:
        pass


def sessions():
    return bridge._vibe()


def runner():
    return bridge._vibe_runner()


def session_start(payload):
    sid = payload.get("session_id")
    vs = sessions()
    address = vs.address_of(sid)
    if not address or payload.get("parent_session_id"):
        return None
    app = vs.app_server_pid()
    if not app:
        log("session_start %s: no vibe-app-server above pid %d" % (address, os.getpid()))
        return None
    run = bool(os.environ.get("AGENT_BRIDGE_VIBE_RUN"))
    vs.write_current(app, sid, payload.get("cwd"), payload.get("source"), run)
    log("session_start %s source %s app %d%s" % (address, payload.get("source"), app, " run" if run else ""))
    if run:
        return None
    context = ("Diaulos (agent bridge): this vibe conversation is %s. Other agents (Claude Code, agy, other "
               "vibe conversations) can message it; their messages arrive as <bridge-message> blocks. "
               "To message an agent, use the `send` tool of the `diaulos` MCP server (to, text); "
               "`list_agents` lists who is reachable." % address)
    return {"system_message": "Diaulos: this conversation is %s" % address,
            "hook_specific_output": {"additional_context": context}}


def _senders(prompt):
    found = []
    for part in prompt.split('<bridge-message id="')[1:]:
        head = part.split(">", 1)[0]
        if ' from="' in head:
            sender = head.split(' from="', 1)[1].split('"', 1)[0]
            if sender not in found:
                found.append(sender)
    return found


def pre_agent(payload):
    if os.environ.get("AGENT_BRIDGE_VIBE_RUN") or payload.get("parent_session_id"):
        return None
    vs, rn = sessions(), runner()
    suffix = vs.suffix_of(payload.get("session_id"))
    if not suffix:
        return None
    prompt = payload.get("prompt") if isinstance(payload.get("prompt"), str) else ""
    relay = suffix in vs.live_relays()
    log("pre_agent vibe:%s: delivery %s, relay %s" % (suffix, rn.is_delivery(prompt), relay))
    if rn.is_delivery(prompt):
        senders = _senders(prompt) or [x for x in [rn.relay_sender(prompt)] if x]
        log("pre_agent vibe:%s: relay delivery from %s" % (suffix, ", ".join(senders) or "?"))
        if not SHOW_RELAY_LINE or not senders:
            return None
        body = prompt.split('<bridge-message id="', 1)[-1].split(">", 1)[-1].strip().replace("\n", " ")
        return {"system_message": "message from %s: %s" % (", ".join(senders), body[:PREVIEW])}
    if relay:
        return None  # the relay delivers to this session
    me = "vibe:" + suffix
    with bridge._locked(bridge._path("state", "vibe-" + suffix), wait=1.0):
        claimed = bridge.claim(me)
    if not claimed:
        return None
    msgs = [m for _, m in claimed]
    log("pre_agent %s: added %s to the person's message" % (me, [m["id"] for m in msgs]))
    senders = sorted({m["from"] for m in msgs})
    return {"system_message": "message%s from %s" % ("s" if len(msgs) > 1 else "", ", ".join(senders)),
            "hook_specific_output": {"additional_context": rn.incoming_text(msgs, me)}}


def main(argv):
    event = argv[1] if len(argv) > 1 else ""
    answer = None
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            payload = {}
        if event == "session_start":
            answer = session_start(payload)
        elif event == "pre_agent":
            answer = pre_agent(payload)
    except (Exception, SystemExit) as e:  # fail open: vibe goes on without us
        log("%s error %r" % (event, e))
        answer = None
    if answer:
        sys.stdout.write(json.dumps(answer, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
