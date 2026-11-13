# Mistral Vibe in Diaulos: design

Status: J1 design, validated by Adrien on 2026-11-12 (review PASS T1/07, then its reserves and the price-cap decision applied). Target: `vibe 2.20.0` (`mistral-vibe` on PyPI), Linux and Windows. §8 (validated by Adrien on 2026-11-13, dropping vibe 2.20) moves the design to vibe 3.x.

Mistral Vibe becomes a third kind of agent, next to Claude Code and agy. A vibe conversation has an address `vibe:<key>`. It lists the other agents, writes to them, and receives their messages whether it is working, idle or closed, with nobody typing in vibe.

Every claim below rests on one of three kinds of evidence:

- **src**: a line of the installed vibe source. Paths are relative to `site-packages/vibe/` in `~/.local/share/uv/tools/mistral-vibe/lib/python3.12/`.
- **sdk**: a line of the MCP SDK that vibe ships, relative to `site-packages/mcp/` in the same folder.
- **probe**: a real run on this machine, with a throwaway `VIBE_HOME`, written up in [`prototypes/vibe/README.md`](../prototypes/vibe/README.md) (runs `p1` to `p4`), [`prototypes/vibe-acp/README.md`](../prototypes/vibe-acp/README.md) (A1 to A5) and [`prototypes/vibe-s9/README.md`](../prototypes/vibe-s9/README.md) (S9: B1 to B3). A measure made once is marked **(1 pass)**.

## 1. What vibe 2.20 offers, and what it does not

| Extension point | Verdict | Evidence |
|---|---|---|
| MCP server (`mcp_servers`, stdio) | Gives tools only. vibe ignores everything a server pushes: `notifications/message`, `tools/list_changed`, `notifications/claude/channel`. No turn starts. `sampling/createMessage` is answered, but outside the conversation. | probe p2; src `core/tools/mcp/tools.py:339` (`ClientSession` without a message handler) |
| MCP server lifetime | Two processes per vibe process. The first lists the tools and exits. The second starts at the first tool call and lives until vibe exits. A session that calls no tool has no server running. | probe p1, p2; src `core/tools/mcp/pool.py:187-193` |
| MCP server environment | Only the SDK defaults (`HOME PATH USER SHELL TERM LOGNAME`…) plus the `env` of the config. It gets no session id and no `VIBE_HOME`. Its parent is the vibe Python process. | probe p1; sdk `client/stdio/__init__.py:28-69, 127` |
| Hooks | Three types only: `post_agent_turn`, `before_tool`, `after_tool`. There is no session-start hook and no hook before a model call. They are experimental and off by default. A `post_agent_turn` hook that waits and answers `deny` starts a turn, but 3 times at most per human turn; the 4th message is lost. The injected text is not shown, and the TUI holds the human's input while the hook waits. | src `core/hooks/models.py:20-23` (`HookType`), `core/hooks/config.py:78`, `core/hooks/_handler.py:25`, `core/agent_loop_hooks.py:418-437`; probe p2 |
| Tool files `$VIBE_HOME/tools/*.py` | **Imported into the vibe process itself** with `exec_module`, once per process. A failing import is swallowed silently. A file whose name starts with `_` is skipped. Import seen in the TUI and in `-p` (probe p3, p4) and in `vibe-acp` (probe S9). | src `core/tools/manager.py:162-181`; probe p3, p4, S9 |
| Native tools in a tool file | A `BaseTool` subclass is a real tool. Its name is the class name in snake case. The default permission is `ask`; a config subclass with `permission = ALWAYS` runs it without approval. `run(args, ctx)` receives `ctx.session_id` (the `session_id` of `meta.json`) and `ctx.session_dir`. | src `core/tools/base.py:69, 117-141, 129, 228, 367-370`, `core/tools/manager.py:565-586`; probe S9 B1, B2 (§4.1) |
| Starting a turn from inside the process | In the TUI, `VibeApp._handle_user_message(text)` starts a turn when the agent is idle; it is what `/loop` calls. Both are private. | src `cli/textual_ui/app.py:581-589, 2001`; probe p3: idle, 0.91 / 1.01 / 1.06 / 1.41 s from file to written reply (4 passes) |
| vibe's input queue | `app._enqueue_prompt_with_resources(text)` queues a prompt, sent after the running turn. Esc pauses the queue (`app._input_queue.paused`). Ctrl+C moves the last queued item back to the input bar. The queue lives in memory only. | src `cli/textual_ui/app.py:607, 1052, 4006, 4166-4168`, `message_queue.py:59`; probe p3 (1 pass) |
| `@path` in a prompt | Inlined: the file's content goes into the message. | probe p3 (`ZEBRA-4417`); src `core/autocompletion/path_prompt.py:54-60, 83` |
| Session id | `xxxxxxxx-xxxx-xxxx-xxxx-<suffix>`. The first 20 hex characters are random. The 12-hex **suffix is kept** when vibe compacts (auto or `/compact`), rewinds or clears, which all create a new session id and a new folder. Compaction and rewind set `parent_session_id` to the previous id; `/clear` sets none. A **subagent** gets a fresh id (new suffix) with `parent_session_id` = the caller. `-p --resume` builds a new `AgentLoop`, so a new suffix too. | src `core/session/session_id.py:8-24`, `core/agent_loop/_loop.py:434, 2212-2222, 2241, 2307`, `core/compaction/manager.py:105`, `core/rewind/manager.py:166`, `core/tools/builtins/task.py:136`; probe s5; probe S9 B3: in one TUI process, `54d77329-…-33fbfd20c13e` (parent null), after `/clear` `801f74e7-…-33fbfd20c13e` (new folder, parent null), after `/compact` `9e339932-…-33fbfd20c13e` (new folder, parent `801f74e7-…`) |
| Sessions on disk | `<save_dir>/session_<YYYYMMDD_HHMMSS>_<first 8 hex>/{meta.json, messages.jsonl}`. `meta.json` holds `session_id`, `parent_session_id`, `title`, `title_source` (`auto` or `manual`), `environment.working_directory`, `start_time`, `end_time`. There is no index. `save_dir` defaults to `$VIBE_HOME/logs/session` and may be absolute in `config.toml` (it is on this machine). | src `core/session/session_logger.py:74`, `core/types.py:163`; probe p3, s5 |
| `/rename <title>` | Rewrites `meta.json` at once, with `title_source: "manual"`. | src `core/session/saved_sessions.py:66`, `cli/commands.py:132`; probe s8 |
| `vibe -p --resume <id>` | **Forks**: new session id, new folder, old messages copied, title lost. The original is left unchanged. | probe s5; src `core/programmatic.py:66-75` |
| Tool permission in `-p` | A tool whose permission is `ask` is skipped (`Tool execution not permitted.`). | probe s6; src `core/agent_loop/_loop.py:1880-1885` |
| `vibe-acp` (Agent Client Protocol on stdio) | `session/load` in a new process keeps the session id, the folder and a manual title, and `messages.jsonl` grows. `session/request_permission` goes to the client, with `toolCallId` only. `session/set_config_option max_turns` caps the turns; `max_tokens` caps each completion only. There is no price limit and no session token limit. Only `mcp_servers` from `config.toml` is used. The working directory is the `cwd` of `session/new` or `session/load`. Options: `-v`, `--setup`. | probe vibe-acp A1 to A5 (cost $0.0094 + $0.0194); src `acp/acp_agent_loop.py:942-976, 951, 1048, 1228-1263, 1238, 1348-1361`, `core/agent_loop/_loop.py:1093-1110` |
| Local API, socket, pipe into a running vibe | None. `grep -rln "socket\|asyncio.start_server\|start_unix_server\|named_pipe\|mkfifo"` over `vibe/` finds two files only: `core/sentry.py:99` (a comment about the host name) and `core/auth/mcp_oauth.py` (the OAuth callback server). | grep run by the architect and by the reviewer (T1/03) |

## 2. Architecture

Everything that touches vibe goes through **one file loaded into the vibe process**, the plugin. agy needed hooks plus a relay; vibe needs neither.

```
any agent ── send ──▶ ~/.agent-bridge/inbox/vibe:<key>/*.json
                         │
                         ├─▶ vibe TUI, idle:      plugin thread → VibeApp._handle_user_message
                         ├─▶ vibe TUI, working:   waits in the inbox; delivered when the turn ends (idle again)
                         └─▶ vibe closed:         bridge.py vibe-run → vibe-acp session/load + session/prompt
vibe ── bridge_send tool (plugin, in-process) ──▶ inbox of the recipient
```

| File | Role | Why |
|---|---|---|
| `bridge/vibe_sessions.py` (new) | Reads vibe's sessions without vibe: `save_dir`, scan of `meta.json`, lineage by suffix, address and name, live and closed peers, `describe`. | Shared by the bridge, the runner and the plugin. Python 3.8, standard library. |
| `bridge/vibe_runner.py` (new) | `vibe-run` (deliver to a closed conversation) and `vibe-new` (start one), as an ACP client of `vibe-acp`. | Separate file: separate owner and tests. |
| `bridge/vibe_plugin.py` (new) | Plugin logic: identity, heartbeat, claiming, message text, delivery choice, in-flight record. No vibe import, so it is tested with a fake app. | Testable without vibe. |
| `install/vibe-tool.py`, installed as `$VIBE_HOME/tools/agent_bridge.py` | Thin shim: the `BridgeSend` and `BridgeListAgents` tools (`BaseTool`, permission `ALWAYS`), and the start of the plugin thread, once per process. | The only file vibe loads. A vibe API change breaks it in one visible place. |
| `bridge/bridge.py`, `bridge/remote.py`, `bridge/mcp_channel.py` | The `vibe` kind in the existing code paths (§6, T3a). | Same pattern as `agy`. |

**Loading inside vibe.** The vibe process already has a top-level package named `vibe` in `sys.modules`, and other names could clash too. So the shim **never adds a folder to `sys.path` itself and never uses a bare `import`**. It loads `vibe_plugin.py` from the absolute `AGENT_BRIDGE_HOME` written by the installer with `importlib.util.spec_from_file_location`, under the module name `agent_bridge_vibe_plugin`. `vibe_plugin.py` loads `bridge.py` and `vibe_sessions.py` the same way, under `agent_bridge_core` and `agent_bridge_vibe_sessions`. When `bridge.py` needs `remote.py` (a send to `remote:`), its `_remote()` adds the bridge folder to `sys.path` (`bridge.py:1746-1752`). In the vibe process, the plugin registers `sys.modules["bridge"]` before that, so `remote.py` finds the already-loaded core. This `remote:` path is the one `sys.path` change in the vibe process: it appends the bridge folder, and only on a `remote:` send. The `remote` name itself is not a vibe module (checked by T3c).

**Not used, one line each:**

- MCP server: it cannot wake vibe, does not know the session, and in `-p` or ACP its tools would need `config.toml` permission edits. Native tools have none of these problems.
- Hooks: experimental, off by default, they hold the TUI while they listen and lose the 4th message.
- No edit of `~/.vibe/config.toml`: installing is one file copy. There is no TOML to merge, and the standard library cannot write TOML.

## 3. Addresses and identity

- **Key: the 12-hex suffix of the session id.** It is the only part vibe keeps across compaction, rewind and `/clear` (§1, "Session id"). A conversation is the set of `meta.json` files with that suffix. Its **current session** is the one with the latest `end_time` (written at every save, src `session_logger.py:432`), not the latest `start_time`: a person can `/resume` an older session of the same suffix (src `app.py:3144`). A fork (`AgentLoop.fork`, src `_loop.py:2241`; ACP `session/fork`, src `acp_agent_loop.py:1828`) keeps the suffix too, so a fork stays in the same conversation, and its latest save makes it current.
- **Canonical address: `vibe:<suffix>`**, for example `vibe:4ea93de2b1c9`. Inbox and peer file are keyed by it. It never changes, so a reply to an old `from` always arrives, and a rename moves nothing.
- **Subagents are left out** by this rule: a session whose `parent_session_id` has a *different* suffix is a subagent. A session whose parent has the *same* suffix is a compacted or rewound continuation, and it stays in its conversation. This replaces the first version's rule "any `parent_session_id` means subagent", which hid the live session after a compaction. Subagents also save under `<parent session folder>/agents` (src `task.py:120-125`), so the `save_dir` scan of §4.7 does not see them anyway; the suffix rule is the second guard.
- **`-p --resume` by a person** gives a new suffix: a new conversation, which is what vibe does too.
- **Name: the `/rename` title** of the current session, when `title_source` is `manual`. Characters outside `[A-Za-z0-9_.-]` become `_`, as for `claude:`. `vibe:<name>` resolves through the same "exact name" path as `agy:<title>` (`bridge.py` `resolve`, `named`). The automatic title (the first prompt, cut) is shown in `list_agents` but is not an address.
- **`from`** is always the canonical address, as `agy:<uuid>` is for agy.
- **Sender identity.** `bridge_send` maps `ctx.session_id` to its suffix, so it does not depend on the environment. A subagent's `bridge_send` uses its own new suffix: it is a separate sender, and a reply to it is refused as an unknown address (it is not listed). The plugin also sets `AGENT_BRIDGE_ADDRESS=vibe:<suffix>` and removes `ANTIGRAVITY_CONVERSATION_ID` from `os.environ`, so a `bridge.py send` from vibe's bash tool has the right `from` (`_default_sender` reads `ANTIGRAVITY_CONVERSATION_ID` first, `bridge.py:471-476`). This removes inherited variables of a vibe started from an agy or Claude shell, which the probe saw (`CLAUDE_CODE_SESSION_ID` in the hook env, probe p1).

## 4. The ten criteria

### 4.1 Send (criterion 1)

- **Mechanism.** The native tool `bridge_send(to, text)` calls `bridge.send(to, text, sender="vibe:<suffix>")` in the vibe process. `bridge_list_agents(all?, days?)` calls `peers()` and `describe()`. Both use a config subclass with `permission = ALWAYS`, so they run without approval in the TUI, in `-p` and in ACP.
- **Evidence.** Probe S9 (`prototypes/vibe-s9/README.md`). B1: a tool file with `BridgeEcho(BaseTool)` and a config subclass with `permission = ALWAYS`; the model sees it as `bridge_echo`. B2: it ran without any approval in the three modes: `-p` without `--auto-approve` (exit 0), `vibe-acp` (0 `session/request_permission`), TUI without `--auto-approve` (no dialog, `✓ Success`); `tool_calls_rejected` 0. In all three, `ctx.session_id` equals `meta.json` `session_id`. Source: `base.py:69` (`ctx.session_id`), `base.py:129` and `manager.py:565-586` (permission resolution).
- **Limits.** If the shim's `BaseTool` import fails on a future vibe, vibe drops the tools silently (`manager.py:180-181`). The shim catches its own errors and writes them to `state/vibe-plugin.log`, and `run-proofs.sh` checks that both tools are listed.
- **A sender nobody reads (decision after T3c, 2026-11-12).** A first real TUI pass looped: the model called `bridge_send` 16 times towards `ext:real`, which had no watcher, until the price cap stopped it ($0.515). `send` does **not** refuse an `ext:` sender without a reader: a script may send first and start `watch ext:<name>` afterwards, as the README's "Programs and scripts" allows. Three guards instead:
  1. At delivery, when a sender is an `ext:` address with no live watcher, the reply line says `ext:<name> has no reader: do not reply with bridge_send` instead of `Reply with the bridge_send tool, to=…` (plugin and `vibe-run`).
  2. A refused send to a dead `ext:` says that the message was not delivered and must not be sent again (text of the `resolve` error for `ext`).
  3. In one vibe process, `bridge_send` to an address that already failed twice answers `stopped: <to> failed twice, do not call bridge_send for it again` without calling `send`.
  The price cap stays the last guard. Tests: T3c (1, 3), T3a (2).

### 4.2 Receive, vibe idle (criterion 2)

- **Mechanism.** At import, the plugin starts one daemon thread per process. It finds the `VibeApp` instance with `gc` (found 0.58 s after import, probe p3). Then, every `POLL_SECONDS` (1 s), it checks five conditions inside vibe's own event loop, with `app.call_from_thread`, so the check and the call happen together:
  1. not `app._agent_running`;
  2. `app._current_bottom_app == BottomApp.Input`;
  3. not `app._queue.draining`;
  4. `len(app._input_queue) == 0` and not `app._input_queue.paused`;
  5. no `!` shell command of the person still running: `app._bash_task` is `None` or done. vibe's own `_is_busy` counts it too (src `app.py:1066-1072`); without this check, a message would start a turn while the person's command runs.
  When all five hold and the inbox has messages, it claims them and calls `app._handle_user_message(text)`. This is the call and the test of vibe's `/loop` runner (src `app.py:581-589`), plus the queue checks.
- **Evidence.** Probe p3, 4 passes, file to written reply 0.91 / 1.01 / 1.06 / 1.41 s, file to delivery 0.03-0.09 s. The message shows on screen as a normal user message.
- **Message format.** The text built by `_incoming_text` (`<bridge-message id from ts>` blocks with the warning header), with two changes. Every `@` becomes `@⁠` (word joiner): `_is_path_char` rejects U+2060 (src `path_prompt.py:83`), so vibe does not inline a file. The last line says `Reply with the bridge_send tool, to=<from>  (you are vibe:<suffix>)`.
- **In-flight record (never lost).** Before the call, the plugin writes `state/vibe-inflight/<suffix>.json` with its pid, the session id and the claimed paths. It deletes the record when the turn has ended, that is, when `_agent_running` is false again and the current session's `messages.jsonl` has grown. That session can be new: after `/clear`, its folder appears only at the end of the next turn (probe S9, **1 pass**), so the plugin takes the current session folder from `app.agent_loop` at that moment, not from the record. Whoever next reads that inbox (a plugin, `vibe-run`, or `peers("vibe")`) puts the claimed messages back in the inbox (`unclaim`, `bridge.py:616-621`) if it finds a record whose pid is dead. A vibe killed in the middle of the turn therefore gets the message again, at most once more. **That replay can be a duplicate.** vibe saves `messages.jsonl` after every step of a turn (src `_loop.py:1337`), so once the first step is saved, the message is already in the history. A kill from then until the record is deleted (seconds to minutes, the length of the turn) gives the message a second time when the conversation is resumed. Diaulos accepts this: a message seen twice is better than a message lost, and the header keeps the same `id`, so the reader can tell. The record is also deleted, without waiting for growth, in two cases, and the message goes back to the inbox (`unclaim`) because it is not in the history:
  - `_handle_user_message` returns without starting a turn. Its signature has no `images` argument (src `app.py:2001-2003`): it prepares images itself, and when that fails it puts the text in the input box and returns (src `app.py:2007-2012`). The plugin cannot prevent this, so it detects it: it notes `app._agent_task` before the call, and a turn has started only if the call created a new one (src `app.py:2032-2034`, `create_task`). `_agent_running` is not used for this, because it can turn true later, inside that task. With no new task, the plugin also clears the input box if it holds exactly the injected text, so that Enter does not send it a second time.
  - The turn ends cancelled (Esc) before any save.
- **J2 criterion.** Under 3 s from `send` to the start of the turn, median of 3 passes, measured by `tests/run-proofs.sh`.
- **Limits.** Six private names (`_handle_user_message`, `_agent_running`, `_current_bottom_app`, `_queue`, `_input_queue`, `_bash_task`). At start, the plugin checks that they exist. If one is missing, it registers the session with `wake: "none"` and claims nothing: messages wait, and `vibe-run` delivers them once vibe has exited. `list_agents` says so. Versions other than 2.20 are tried, and the version goes into `state/vibe-plugin.log`.

### 4.3 Receive, vibe working (criterion 3)

- **Mechanism.** The plugin **does not claim while vibe works**. The message stays in the Diaulos inbox, on disk. When the turn ends and vibe's own queue is empty, the five conditions of §4.2 hold again, and the plugin delivers within one poll (1 s). The first version put the message into vibe's in-memory queue; the review showed that a kill, a crash or Ctrl+C during the turn then lost it.
- **Evidence.** Turn end and queue drain: src `app.py:1066` (`_is_busy` = agent running, bash task, or queue draining), `message_queue.py:292-293` (`draining`). Idle delivery: §4.2. The earlier probe of in-memory queuing (18.7 s, **1 pass**) is no longer the mechanism.
- **Order.** A person's queued prompts go first: the plugin waits while vibe's queue is not empty or is draining. Diaulos messages never overtake them. The inverted order seen in probe p3 (LIME2/LIME3, **1 pass**) cannot happen, because the plugin never delivers while the queue holds anything.
- **Esc.** After Esc, vibe pauses its queue (src `app.py:4006`). With a non-empty paused queue, condition 4 fails, so the plugin waits until the person presses Enter and the queue drains. With an empty queue, Esc only stops the turn, and the plugin delivers at the next poll. `list_agents` shows `paused` while condition 4 fails because of a pause.
- **Impossible.** No delivery in the middle of a turn, between two model calls: vibe has no hook before a model call (src `core/hooks/models.py:20-23`). An `after_tool` hook could append text to a tool result, but it is experimental and off by default. "At the latest at the end of the current turn" holds, plus at most 1 s of polling.

### 4.4 Receive, vibe closed (criterion 4)

- **Mechanism.** `send` to a vibe address without a live TUI plugin starts `bridge.py vibe-run <suffix>`, one per conversation, detached (like `cli-run`). It waits 1 s so that a burst goes in one turn, then speaks ACP to `vibe-acp` in the current session's working directory:
  `initialize` → `session/load {sessionId: <current session>, cwd, mcpServers: []}` → `session/set_config_option max_turns` → `session/prompt {text}`.
  It answers every `session/request_permission` with `reject_once`, because nobody is there to approve. The bridge tools are `ALWAYS`, so they never ask. It loops while the inbox has messages, then closes stdin and exits.
- **Why ACP.** `-p --resume` forks (probe s5): the reply would land in a copy that the person does not see when they resume the original. `session/load` keeps the id and the folder (probe A1).
- **Failures**, as in `cli_run` (`bridge.py:1686-1697`). If `vibe-acp` exits, times out (`CLI_TURN_SECONDS`) or answers with an error, the batch is put back (`unclaim`). After `RELAY_RETRY_SECONDS` it is tried again. After 3 failures, its messages are renamed `.failed`, and the sender gets a `[bridge, not an agent]` note. The in-flight record of §4.2 covers a `vibe-run` that is killed. At most 3 paid attempts per batch.
- **Cost cap.** ACP has no price limit and no session token limit (probe A4). Two caps:
  1. `max_turns` through `session/set_config_option`, a public ACP option (src `_loop.py:1093-1095`). Default 20.
  2. A price cap per run, **best effort, on top of `max_turns`**. `vibe-run` starts `vibe-acp` with `AGENT_BRIDGE_VIBE_MAX_PRICE` (default 0.50). The plugin, loaded in that process too, finds the `AgentLoop` (`gc`), sets `_max_price` and calls `_setup_middleware()`. vibe then adds its own `PriceLimitMiddleware`, the one `-p --max-price` uses (src `_loop.py:1107-1108`, `middleware.py:65`). `set_max_turns` rebuilds the pipeline from `_max_price` too, so the order does not matter. This cap uses private names, so it can be missing after a vibe update. Then only `max_turns` holds: `max_turns` is the public cap, the price cap is an extra guard. When the plugin cannot set the price cap (a name missing, or no `AgentLoop` found), it writes `price cap not set, max_turns only` in `state/vibe-plugin.log` and sets `price_cap: false` next to its `acp_pid` in the peer file (§4.5). `describe` then adds `price cap not set, max_turns only` to the line of that conversation (§4.7).
  Both values can be changed in `~/.agent-bridge/state/vibe-limits.json`. Only `vibe-run` reads it (T3b): a missing or invalid file gives the defaults. `vibe-run` passes the price as `AGENT_BRIDGE_VIBE_MAX_PRICE` and the key as `AGENT_BRIDGE_VIBE_SUFFIX` in the environment of `vibe-acp`; the plugin reads only the environment.
- **Evidence.** Probe vibe-acp A1: after `session/load` in a second process, the same id `ca66e9f6-…`, one folder, `messages.jsonl` from 2 to 6 lines, title and `title_source: manual` kept. A2: options `allow_once`, `allow_always`, `allow_always_permanent`, `reject_once`. A4: costs $0.0094 and $0.0194. A5: binary `vibe-acp` next to `vibe`. **Not probed yet.** Checked in T3b: a turn limit reached, a `reject_once` answer. Checked in T3c (fake `AgentLoop`), then for real in T5b once T4 installs the shim: the plugin price cap, and `session/load` in `vibe-acp` with the shim and plugin loaded (S9 probed the tool in a `session/new` ACP session only). The S9 TUI result "no dialog" rests on the final screen (`✓ Success`); the key presses were not logged, so T3c repeats it in a pty with a log of every key sent.
- **Decisions after the Tester's first pass (T7, 2026-11-12), fixed in J2:**
  - **P1, a killed TUI waits for a Diaulos command.** Recovery (§4.2) runs only when someone reads peers. The MCP channel of every Claude Code session (`mcp_channel.py`, its poll loop) also calls `peers("vibe")` every 30 s, which recovers dead in-flight records and starts `vibe-run`. Criterion: TUI killed mid-turn, no command typed, a Claude session open: the reply arrives within 60 s. With no Claude session open, the message still waits for the next Diaulos command: a limit for the README.
  - **P3, a turn stopped by the price cap is silent.** `vibe-run` detects the stop (vibe's `Price limit exceeded` text in the reply, as it detects `Turn limit of`, or a session cost at or above the cap). It logs `stopped at price cap` instead of `delivered`, and tells each sender with a `[bridge, not an agent]` note that the turn stopped at the cap. The turn limit gets the same note. Criterion: run 3b of `docs/TEST-VIBE.md` gives the log line and the note.
  - **P4, cut work resurfaces in another sender's turn.** When a turn stopped at a cap, `vibe-run` writes `state/vibe-cut/<suffix>`. The next delivered text, from the plugin or `vibe-run`, starts with `Note: the previous bridge turn here stopped at a limit; do not finish its work unless this message asks for it.`, and the file is deleted. vibe keeps its history: Diaulos cannot remove the cut work, only say it. Criterion: runs 3b then 10 give the note in run 10's text.
  - **P2, P5, P6: limits written in J3**, no code change. P2: the automatic title of a conversation woken by Diaulos is the bridge header (vibe titles from the first prompt). P5: delivery after a busy turn takes about 1 s more than the turn end (1 s poll), measured 1.06 to 1.21 s; the target reads "about 1 s". P6: `bridge_list_agents` in `vibe -p` does not list its own conversation, which has no TUI and no runner.
- **Fallback if ACP cannot run** (no `vibe-acp`, or every attempt fails): the messages stay `.failed` or waiting, the plugin delivers waiting ones the next time the conversation opens in a TUI, and `list_agents` says `closed: waits until reopened`.

### 4.5 Concurrency: TUI, `vibe-run` and a person (criteria 3 and 4)

- **One lock per conversation**, `state/vibe-<suffix>` taken with `_locked` (`bridge.py:229-254`), around every "check, then claim or start" step, in the plugin and in `vibe-run`. The lock is held only for that step (read the peer file, then claim or write the pid), never for a whole turn. `_locked` is best effort: after 2 s of waiting it proceeds without the lock, and it steals a lock older than 10 s. So it narrows the window between the check and the load; it does not close it. The pid fields below and the in-flight record cover what is left.
- **The peer file `peers/vibe-<suffix>.json` holds one field per role**: `tui_pid` (a TUI plugin), `runner_pid` (`vibe-run`), `acp_pid` (its `vibe-acp` child), each with its own heartbeat time. Nobody writes another role's field. `wake` is computed when the peers are listed, so it no longer swings between writers.
- **`vibe-run` never starts while `tui_pid` is alive**, and stops between two turns when a TUI appears. The TUI plugin then delivers.
- **A TUI plugin does not claim while `runner_pid` is alive.** It waits until the runner has finished its turn and exited.
- **What stays possible:** a person opens `vibe --resume <id>` while `vibe-run` is in the middle of a turn. Diaulos cannot stop vibe from opening. The TUI then loads the history without the runner's turn, and the two processes write the same `messages.jsonl`. Mitigation: the TUI plugin sees `runner_pid` alive at start and shows a vibe notice (`app.notify`, Textual public API) saying that a background turn is running and that its reply will be in the session after `/resume`. The runner's turn is short (`max_turns`). This limit goes in the README.

### 4.6 New conversation (criterion 5)

- **Mechanism.** `vibe:new` and `vibe:new@<name>`, with `workspace` (default: the sender's directory), go to a `vibe:new` inbox. `bridge.py vibe-new` (one worker, like `cli-new`) runs ACP `session/new {cwd}`, then `session/prompt`, with the caps of §4.4. It registers the new conversation as `vibe:<suffix>`, with `vibe:<name>` as an alias kept by Diaulos only: it does not write vibe's `meta.json`. It then tells the sender the address and the command to open it in a terminal: `vibe --resume <id>` (src `cli/cli.py:211-221` keeps the id).
- **Impossible.** Opening an interactive TUI window for the person. There is no window to attach to, as with `agy:new-cli`. The `@<name>` is not vibe's title: the `/resume` picker shows the automatic title.

### 4.7 Discovery (criterion 6)

- **Live conversations.** The plugin heartbeat (every 10 s) writes `tui_pid` (or, in a `-p` or ACP process, nothing for the TUI role), the current `session`, `workspace`, `name`, `paused`, and the plugin's `wake` ability (`inject` or `none`).
- **Closed conversations.** `peers("vibe")` scans `save_dir`. It reads `[session_logging] save_dir` from `$VIBE_HOME/config.toml` with `tomllib` on Python 3.11 and later. On 3.8 to 3.10 it reads that one key with a line parser: the `save_dir = "<string>"` line inside the `[session_logging]` table, basic string with `\\` and `\"` escapes. The default is `$VIBE_HOME/logs/session`. It groups the `meta.json` files by suffix, leaves out subagents (§3), keeps conversations whose latest `end_time` is within `RECENT_DAYS`, and lists them as asleep. `days` and `all` work as for agy.
- **`describe`.** `vibe:<suffix>  "<name or auto title>"  <workspace>  <how>`, with `how` among `TUI: wakes on message`, `TUI, paused (Esc): delivers after Enter`, `TUI, no injection (vibe <version>): waits until vibe exits`, `background turn running (vibe-run)`, `closed: wakes with vibe-acp`. When the peer file holds `price_cap: false`, `how` ends with `, price cap not set, max_turns only`.
- **Unknown address.** Refused with the list, by the existing `resolve` path, as for `claude:`.

### 4.8 Installation (criterion 7)

- `install.sh` copies the three new bridge files with the rest. If `vibe` is on `PATH` or `$VIBE_HOME` (default `~/.vibe`) exists, it writes `$VIBE_HOME/tools/agent_bridge.py` from `install/vibe-tool.py`, with the absolute `AGENT_BRIDGE_HOME` filled in. If `tools/` did not exist, it records that in `~/.agent-bridge/state/vibe-install.json`. If a different `agent_bridge.py` already exists, it saves it as `agent_bridge.py.bak-agent-bridge` first. **No vibe config file is edited.**
- `uninstall.sh` removes `tools/agent_bridge.py` and the `tools/__pycache__/agent_bridge*.pyc` that vibe's import leaves (seen in `prototypes/vibe/out/p3-plugin/vibe_home/tools/`). It restores the `.bak-agent-bridge` copy if there is one. It removes `tools/__pycache__` and `tools/` only if they are then empty and the install created them. The bridge's own `--purge` does not touch vibe sessions.
- `install.ps1` / `uninstall.ps1`: the same, with `$env:VIBE_HOME` or `%USERPROFILE%\.vibe`. The shim is pure Python, uses no `/proc`, and finds `vibe-acp` with `shutil.which` (`vibe-acp.exe` in the uv tool `bin`). Parity is in the code. It is **tested on Windows only if a Windows machine is available**; the J3 docs say which.

### 4.9 Nothing breaks, tests (criterion 8)

- The existing suite (`tests/test_*.py`) still passes unchanged, on the CI matrix (Python 3.8 to 3.12, `.github/workflows/tests.yml`). The new bridge files are Python 3.8 code (no `tomllib` without the fallback, no `match`). `vibe_plugin.py` also runs inside vibe's 3.12, but is tested on 3.8 as well.
- New tests with fakes in `tests/fakes/`, written **first** (T5a, §6):
  - `tests/fakes/vibe-acp`: a Python stand-in for the ACP subset (`initialize`, `session/new`, `session/load`, `session/set_config_option`, `session/prompt`, and one `session/request_permission` on demand). It logs each call to `$FAKE_VIBE_LOG`, writes `meta.json` into a temporary `VIBE_HOME`, and answers the sender through `bridge.py send --from vibe:<suffix>`, like `tests/fakes/agy`. It can be told to exit in the middle of a prompt.
  - `tests/fakes/vibe_app.py`: a `FakeVibeApp` that copies the real shapes: `_agent_running`, `_current_bottom_app`, `_queue` (a controller with `draining`), `_input_queue` (with `paused` and `len`), `_handle_user_message`, `call_from_thread`.
  - `tests/fakes/vibe_sessions/`: `meta.json` sets for a compacted conversation (same suffix, parent set), a cleared one, a subagent (other suffix), and a renamed one.
- `tests/run-proofs.sh` runs the real checks (T3b, T3c) with a throwaway `VIBE_HOME`, through a pseudo-terminal for the TUI (from `prototypes/vibe/pty_drive.py`). Every real call is capped (§4.11).

### 4.10 Docs (criterion 9)

At J3 (architect): a `vibe` section in `docs/INTERNALS.md`, `README.md` and `README.fr.md`, and a `CHANGELOG.md` entry. They cover install, address, and limits: private API, no delivery in the middle of a turn, Esc, a person opening a session during a background turn, `vibe:new` without a window, and the Windows test status. The Claude Code skill `install/claude-skill/` and the MCP instructions name `vibe:` (T3a). `docs/JOURNAL.md` gets the lab story in French (programmer).

### 4.11 Cost (criterion 10)

- Every real call in tests and proofs: `vibe -p … --max-price 0.50` at most (the probes used 0.10), or `vibe-run` / `vibe-new` with `max_turns 5` and the plugin price cap at 0.50 or less (§4.4). On short sessions, a turn costs about $0.01 to $0.02 (probe A4). `run-proofs.sh` prints the cost from each session's `meta.json` `stats.session_cost`.
- The product default for `vibe-run` is `max_turns 20` (public, always applied) and, best effort, a price cap of $0.50 per run. After a vibe update the price cap can be missing; then only `max_turns` holds, and the logs say so (§4.4). Other limits: at most 3 attempts per batch, set in `state/vibe-limits.json`.

## 5. Plugin step by step

1. At import: guard against a second start (a module-level flag). Load `vibe_plugin.py` by path (§2). Never raise: errors go to `state/vibe-plugin.log`.
2. The thread waits up to 10 s for `VibeApp`. Without one, the process is `-p` or ACP. There, it applies the price cap from `AGENT_BRIDGE_VIBE_MAX_PRICE` if set, records its pid as `acp_pid` when `AGENT_BRIDGE_VIBE_SUFFIX` is set (by `vibe-run`), and does nothing else.
3. In the TUI, every second: read `app.agent_loop.session_id` (it changes with compaction, rewind, `/clear`, `/resume`). Recompute the suffix, the address and the environment variables. Write the heartbeat every 10 s. Finish or recover in-flight records. Deliver if §4.2 allows it.
4. At exit (`atexit`): clear `tui_pid`. The conversation is then listed as closed.

## 6. J2 work split

Each task owns its files; nobody else edits them during J2. All tasks are for the Programmer, who may hand the fakes to the Executor.

| id | Owner | Task | Files | Depends on | Done when (with output in the handoff note) |
|---|---|---|---|---|---|
| T5a | programmer | Fakes and session fixtures | `tests/fakes/vibe-acp`, `tests/fakes/vibe_app.py`, `tests/fakes/vibe_sessions/` | — | The fakes run alone: `vibe-acp` answers a scripted ACP exchange; `FakeVibeApp` exposes the names of §4.2 with the real shapes. |
| T3a | programmer | `vibe` kind in the bridge core | `bridge/vibe_sessions.py`, `bridge/bridge.py`, `bridge/remote.py`, `bridge/mcp_channel.py`, `install/claude-skill/`, `tests/test_vibe_sessions.py` | T5a | `ADDRESS_RE` gains `vibe`; `REMOTE_RE` and the target regex at `remote.py:816` accept it (`LOCAL_RE` follows `ADDRESS_RE`); `vibe:new` exact only in remote policy. `resolve` accepts `vibe` (`bridge.py:412`). With the fixtures: a compacted conversation is one entry under its suffix, with its latest session; a subagent is left out; `vibe:<name>` resolves a renamed one; an unknown `vibe:x` is refused with the list. `resolve` widens to `days=None` for `vibe` as for `agy` (`bridge.py:425`), so an exact address of an old conversation still resolves. The current session is the latest `end_time`; a fork keeps the suffix. `main` gains the subcommands `vibe-run <suffix>` and `vibe-new`, as `cli-run` and `cli-new` (`bridge.py:1792-1795`), and `send` to a closed conversation starts one `vibe-run`. `describe` adds `price cap not set, max_turns only` when a fixture peer file holds `price_cap: false`. Tests in `tests/test_vibe_sessions.py`. The MCP instructions and the `send` description name `vibe:`. `save_dir` is read on 3.8 and on 3.12. |
| T3b | programmer | ACP runner | `bridge/vibe_runner.py`, `tests/test_vibe_runner.py` | T3a | With the fake: 3 messages sent within 1 s give 1 `session/prompt`; the same session id before and after; `reject_once` is sent on a permission request; nothing runs while `tui_pid` is alive; a fake that dies mid-prompt gives a retry, then `.failed` after 3, and the sender's note; `vibe:new@x` registers `vibe:<suffix>` with alias `vibe:x` and tells the sender. Real, throwaway `VIBE_HOME`: one `vibe-run` keeps the id and folder, cost printed; `max_turns 1` stops at the limit; `vibe-run` reads `state/vibe-limits.json` (defaults when missing or invalid) and starts the fake with `AGENT_BRIDGE_VIBE_MAX_PRICE` and `AGENT_BRIDGE_VIBE_SUFFIX` set (the fake records its environment). The price cap itself is the plugin's: T3c and T5b. |
| T3c | programmer | Plugin and shim | `bridge/vibe_plugin.py`, `install/vibe-tool.py`, `tests/test_vibe_plugin.py` | T3a | With `FakeVibeApp`: idle leads to `_handle_user_message`; running, draining, non-empty or paused queue lead to no claim; `@` is escaped; an exception unclaims; a missing private name gives `wake: none` and no claim; a dead pid in an in-flight record puts the messages back once; a turn cancelled before any save puts them back; a `FakeVibeApp` with `images_abort=True` (no new `_agent_task`, text put in the input box) leads to unclaim, record deleted and input box cleared; `_bash_task` running leads to no claim. ACP mode (no `VibeApp`), with a fake `AgentLoop`: `AGENT_BRIDGE_VIBE_MAX_PRICE` sets `_max_price` and calls `_setup_middleware()`; `AGENT_BRIDGE_VIBE_SUFFIX` makes the plugin write `acp_pid` and `price_cap: true`; a fake without `_max_price` gives the log line `price cap not set, max_turns only`, `price_cap: false`, and no exception. Real TUI (pty): idle delivery under 3 s, median of 3 passes; a message sent during a `sleep 20` turn is delivered after it, within 1 s of the end (3 passes); vibe killed during the delivered turn gets the message again after restart; `bridge_send` from vibe arrives in a Claude inbox with `from = vibe:<suffix>`; `bridge.py send` run from vibe's bash tool gives the same `from` (the bash tool passes `os.environ`, src `bash.py:133`); the TUI "no approval" check of S9 is repeated with every key logged. No `sys.path` change in the vibe process, except the bridge folder appended by `_remote()` on a `remote:` send; a test with a fake `remote` module checks that vibe's modules still import. |
| T4 | programmer | Installers | `install.sh`, `uninstall.sh`, `install.ps1`, `uninstall.ps1` | T3c | In a temporary `HOME` and `VIBE_HOME`, two cases: `tools/` absent, and `tools/agent_bridge.py` already present with other content. After install then uninstall (with vibe run once in between, so `__pycache__` exists), `diff -r` against the copy made before install is empty. PowerShell: same logic, run only if a Windows machine is available. |
| T5b | programmer | Proofs and CI | `tests/run-proofs.sh`, `.github/workflows/tests.yml` if needed | T3b, T3c, T4 | `python3 -m unittest` passes all old and new tests, with counts. CI matrix green on 3.8 and 3.12. `run-proofs.sh` runs the real checks of T3b and T3c, prints each delay (3 passes) and each cost, and no real call exceeds §4.11. Real price cap, with the shim installed by T4 in a throwaway `VIBE_HOME`: `vibe-run` with a cap of $0.001 stops at the cap, and the peer file holds `price_cap: true`. |

Order: T5a, then T3a, then T3b and T3c in parallel (separate files), then T4, then T5b. The Tester (J2, J3) starts from T5b. The architect writes the J3 docs from this file and from the final code.

## 7. Risks

- **Private API**: delivery (§4.2) and the ACP price cap (§4.4) depend on private names of `VibeApp` and `AgentLoop`. Mitigation: a check at start, `wake: none` (messages wait for `vibe-run`), the vibe version in the log, and `run-proofs.sh` after every vibe upgrade.
- **`gc` scan to find `VibeApp`**: one pass of about 0.6 s at start (probe p3), then never again.
- **A person opening a session during a background turn** (§4.5): reduced, not removed.
- **The person's own MCP servers start in `vibe-run` too** (only `config.toml` counts, probe A3), as when they open the session themselves.

## 8. Moving to vibe 3.x (T10c)

Status: validated by Adrien on 2026-11-13 (review PASS T10/08), with his decision to drop vibe 2.20 (§8.5). Adrien updated vibe to 3.1.2, and the whole vibe part of Diaulos stopped working. This section says what replaces it. Evidence is the T10a run of `run-proofs.sh` (152 tests, 7 FAIL, 1 ERROR) and the Programmer's T10b probes (`tests/vibe3/probe_mcp.py`, `probe_hook.py`, `tui_probe.py`, throwaway `VIBE_HOME`, devstral-small). The vibe reference is the skill that vibe 3.1.2 ships, `plugins/builtins/vibe/skills/vibe/SKILL.md` (**skill** below, with its line).

### 8.1 What changed in vibe 3.x

- **Custom Python tools are gone.** vibe lists `~/.vibe/tools/*.py` without importing them and warns at every start: "Custom Python tool `agent_bridge` is no longer supported and was not loaded" (src `app_server/_ignored_tool_settings.py`). Our shim no longer loads, so `bridge_send`, `bridge_list_agents` and the TUI plugin are all dead.
- **The TUI is a Rust binary** (`_bin/vibe-rs`). `cli/textual_ui` no longer exists. Python runs only in `vibe-app-server`, which the TUI starts on stdio. Nothing of the 2.20 delivery (§4.2, §5) can be carried over: there is no Python TUI object left to patch.
- **What vibe offers instead**: stdio MCP servers, hooks (`session_start`, `pre_agent`, `post_agent`, `pre_tool`, `post_tool`, `notification`, `session_end`), skills, and native plugins that bundle MCP servers, hooks and skills in one folder under `~/.vibe/plugins/` (skill 747-1052, 1438-1516).
- **Sessions** live in `<save_dir>/unified/<uuid>/` (`meta.json`, `journal/*.jsonl`, `CURRENT`, …), without `messages.jsonl`. `meta.json` no longer has `session_cost`; vibe still computes it (src `app_server/models.py:484`) and vibe-acp sends it (src `acp/agent.py:1792-1795`). `vibe_sessions.py` sees 0 of 7 sessions, so `list` shows no vibe address. The uuid keeps its 8-4-4-4-12 form.
- **A session is held by one process.** While a TUI has it open, `vibe -p --resume <id>` exits with rc 2, "Error: Session is already open: <id>", and `vibe-acp` `session/load` answers error -31002 with the same text (probe Q6).
- **`vibe -p` has its own caps**: `--max-price` (cumulative cost of the run's root session and subagents), `--max-turns`, `--max-tokens`, `--time-limit`, `--output json`, `--prompt-file`. `vibe -p --resume <id>` on a closed session keeps its id and its context (probe Q7).

### 8.2 Decision

Diaulos ships a native vibe plugin, `~/.vibe/plugins/diaulos/`, copied by the installer from `install/vibe-plugin/` in the repository. It holds six parts:

| Part | File | Role |
|---|---|---|
| manifest | `plugin.json`, with the `ai.mistral.vibe` extension (`schemaVersion` 1, `toolNamespace` `diaulos`) | names the plugin; without this extension vibe loads none of its hooks (skill 1486) |
| MCP server | `mcp.json`, starting `~/.agent-bridge/vibe_mcp.py` | the tools `send` and `list_agents` of the server `diaulos` (§8.7: the key was `bridge` before the gates) |
| `session_start` hook | `ai.mistral.vibe/hooks.toml`, running `~/.agent-bridge/vibe_hook.py` | registers the session as a peer, and tells the model its address |
| `post_agent` hook | same file | delivers waiting messages when a turn ends, and listens for new ones (§8.3) |
| `pre_agent` hook | same file | adds messages that waited to the person's next message |
| skill | `skills/diaulos/SKILL.md` | how to answer, the addresses, the anti-retry rule (§4.1) |

Why a plugin, not lines in the person's `config.toml` and `hooks.toml`: one folder to copy and to remove, nothing merged into the person's files, and no clash with an existing `mcp_servers = []` line (T10b found that one breaks a `[[mcp_servers]]` block). Gate G2 says whether one line of `config.toml` is still needed for the permission.

Why MCP for the tools, not a skill alone: with a skill, each send is a `bash` call that asks for approval, and the message text must be quoted for the shell. An MCP tool takes `to` and `text` as JSON, can run with `permission = "always"` (probe Q2: the key is `[tools.<alias>_<remote name>]`), and returns the same errors as today.

**Identity.** One MCP process serves one `vibe-app-server`, not one session, and gets no session id (probe Q1). The `session_start` hook gets it (`session_id`, `transcript_path`, `cwd`, `source`). It writes `state/vibe-current/<app-server pid>.json` with the current main session. `vibe_mcp.py` reads the file of its parent pid at each call, so `/resume`, `/clear` and a fork are followed. The address is `vibe:<last 12 hex of the uuid>`, and `vibe:<name>` after `/rename`. One uuid is one conversation and one address: unlike 2.20, `/clear` gives a new uuid, so a new address (T10d, `20-programmer.md`: `session_start` gets `source=clear` and the new id). The old address is then a closed conversation, which `vibe-run` serves in the background; a `/rename` name stays with its uuid, and the new conversation has none until renamed. `/compact` keeps the uuid and the address. Before its first message, a new conversation has no folder in `unified/` yet: only `state/vibe-current/` knows its address, and a message sent to it waits for the `pre_agent` hook of that first message. A subagent sends as its main session, since `session_start` never runs for subagents.

### 8.3 Delivery

| vibe state | 2.20 design | 3.x design |
|---|---|---|
| Working | after the turn, by the plugin | after the turn: the `post_agent` hook claims the waiting messages and answers `{"decision": "deny", "reason": <bridge header + messages>}`, which vibe sends to the model as a retry message (probe Q3) |
| Idle after a turn | woken in about 1 s | the same `post_agent` hook keeps listening after the turn ends (a long poll on the inbox, like agy's listening `Stop` hook), and answers as above when a message arrives. The TUI stays usable while it waits (probe Q4: keys echoed 0.21 s later). Gate G1. **Dropped after G1 failed: see §8.7** |
| Idle before the first message | woken | not woken: no hook runs before the first message. The message waits for the `pre_agent` hook (the person's first message) or for the TUI to close |
| Closed | `vibe-run` with `vibe-acp` and the plugin's price cap | `vibe-run` with `vibe -p --resume <id> --prompt-file <message> --max-price 0.50 --max-turns <n> --output json`. "Session is already open" is not a failure: the TUI came back, and the message waits for it |
| New (`vibe:new`) | `vibe-acp` | `vibe -p --workdir <dir> --prompt-file … --max-price 0.50 --output json`; the new id comes from the JSON output |

Limits that come with it:

- **Three wakes per turn.** vibe accepts at most 3 `post_agent` retries per turn (`_MAX_POST_AGENT_RETRIES`). The hook puts every waiting message in one retry. After the third, it stops listening, and the next messages wait for the person's next message (`pre_agent`) or for the TUI to close.
- **The person does not see the message text.** The TUI shows "Requested turn revision" and the hook name, not the reason (probe Q3). Gate G3 tries `system_message` to show "Diaulos: message from <sender>".
- **The hook ends on its own** a few seconds before its `timeout` (vibe kills the process tree at the timeout), with an empty answer, so the turn is accepted.
- **The price cap is vibe's own** and covers the whole background run, subagents included. It is still per run, not a total budget. `vibe-run` reads the cost from `export.json` (`--output-dir`) for its log.
- **The rule "never in the middle of a turn" stays.** `post_tool` could append a message to a tool result mid-turn; Diaulos does not use it.

Not chosen: the built-in `cron` tool with a `pre_agent` hook that answers `deny` when the inbox is empty. It would wake an idle TUI every 30 s at no model cost (T10b: a denied turn has no `tokenUsage`), but each denied turn shows "Blocked the message: …" at error level, so a red line every 30 s, and the model has to create the cron itself. To try only if G1 fails.

### 8.4 Gates for the Programmer, before the code

- **G1, listening `post_agent`.** In the real TUI: a message typed and sent with Enter while the hook listens. Is it handled at once (the hook stopped or ignored), or does it wait for the hook? Does the TUI show the turn as running? Does Esc stop the hook? If the person's message waits, the listening part is dropped: the hook only delivers what is already waiting, and an idle TUI is not woken.
- **G2, permission in a plugin.** The tool key of a plugin MCP server (`[tools.<?>_send]`), and whether `permission = "always"` can come from the plugin. If not, the installer adds one marked block to `config.toml`, after a backup, and the uninstaller removes it.
- **G3, `system_message`** on a `post_agent` and a `pre_agent` answer: shown in the TUI or not.
- **G4, hooks in `vibe -p`.** `session_start` and `post_agent` run in `vibe -p --resume`, so `vibe-run` sends with the right `from` and drains later messages in the same run.

### 8.5 vibe 2.20

Not supported any more. The only install is 3.1.2, vibe asks to update itself every day, and two delivery paths would double every test. The installer reads `vibe --version`: below 3, it skips the vibe part and says "Diaulos needs Mistral Vibe 3 or later: run `vibe update`". It also removes our old shim `~/.vibe/tools/agent_bridge.py` when it is ours, which ends vibe's warning at every start. `bridge/vibe_plugin.py` (TUI patch, ACP price cap), `install/vibe-tool.py` and `state/vibe-limits.json` go; the parts of `vibe_plugin.py` that send and list move to `vibe_mcp.py`.

### 8.6 Cost

- **New**: `bridge/vibe_mcp.py` (stdio JSON-RPC, reusing the framing of `mcp_channel.py`), `bridge/vibe_hook.py` (three hooks), the plugin folder `install/vibe-plugin/`.
- **Changed**: `vibe_sessions.py` (the `unified/` format, names after `/rename`), `vibe_runner.py` (`vibe -p` and its caps instead of ACP, the "already open" case), `vibe_install.py`, the uninstallers, the vibe tests and their fakes (which copy 2.20 today), `run-proofs.sh`.
- **Removed**: the shim, the TUI patch, the ACP price cap, `vibe-limits.json`.
- About one day of Programmer work plus a Tester pass, after G1 to G4 (a few cents of devstral-small). The docs (README, INTERNALS, CHANGELOG) change afterwards: idle delivery becomes "after a turn" instead of "at any time", and the line "0.7 to 1.0 s" goes until it is measured again.
- **Windows**: hooks run in Git Bash, or PowerShell without it (skill 1033). The installer writes the absolute path of the Python it runs with in the hook commands, and in the launcher that `mcp.json` starts (§8.7: `mcp.json` itself refuses an absolute path). Untested.

### 8.7 After the gates (T10d)

The Programmer ran G1 to G4 on 2026-11-13 (`tests/vibe3/gates.py`, `gates_tui.py`, `gate_hook.py`; handoff `T10-vibe3/14-programmer.md` and its raw logs). Results, and what they change:

| Gate | Result | Change to §8.2-§8.6 |
|---|---|---|
| G1, listening `post_agent` | **Fail.** While the hook waits, the TUI shows "Generating… (Esc to interrupt · Enter to steer…)" The model's answer stays on screen, but vibe writes it to the journal only when the hook ends. A message sent with Enter is "Queued" and waits for the hook (its `pre_agent` starts 23.21 s after Enter, the hook ended at 22.98 s). Esc ends the turn and kills the hook without SIGTERM. | The `post_agent` hook only delivers what already waits, then exits at once. It never listens. |
| G2, permission | The key is `[tools.<server key in mcp.json>_<remote tool name>]`. A plugin cannot set it (`permission` exists only for a plugin's subagents, src `core/plugins/_native.py`). | The installer writes one marked block into `config.toml` (below). |
| G3, `system_message` | Shown: `⚠ [<plugin name>:<hook name>] <text>` for `pre_agent` and for a `post_agent` deny, `✓ …` for `session_start`. It replaces "Requested turn revision". The `reason` text stays hidden. | Each delivery shows `⚠ [diaulos:…] message from <sender>` in the TUI. |
| G4, hooks in `vibe -p` | Yes: `session_start` (`startup` or `resume`, same `session_id`), `pre_agent`, `post_agent`; a `post_agent` deny drains a later message in the same run. | None. |

**Decisions.**

1. **The MCP command is a launcher inside the plugin.** A plugin's stdio command must be a bare executable found on `PATH` or a path starting with `./`; an absolute path is refused ("stdio command must be a bare executable or start with './'"). `mcp.json` runs `./vibe-mcp` (`./vibe-mcp.cmd` on Windows; not tried yet, gate G7 in §8.8), a two-line launcher that the installer writes with the absolute path of its own Python and of `~/.agent-bridge/vibe_mcp.py`. Not `python3` from `PATH`: it may be another Python than the one Diaulos was installed with, and on Windows it may be the Store alias. The launcher only reads; vibe runs the plugin from a read-only copy (`<save_dir>/plugins/packages/<hash>/`), so nothing may be written next to it. Hook commands are shell lines and keep absolute paths.
2. **Exact manifests.** `plugin.json` has `"$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"`, `mcp.json` has `"$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"`, and the server has `"type": "stdio"`. Without them vibe drops the plugin ("No supported plugin manifest was found") or the server.
3. **Server key `diaulos`, tools `send` and `list_agents`.** The permission key depends on the server key, not on the plugin: a server with the same name in the person's config would share its permission keys, and vibe then disables the plugin's server with a warning (`MCP_SERVER_SHADOWED`, src `core/plugins/_native.py:1790-1829`). With `diaulos`, vibe shows the tool as `mcp_diaulos.send`, which also matches the tool names of Claude Code's channel. The `config.toml` block is:

   ```toml
   # >>> diaulos (written by the Diaulos installer; removed by the uninstaller) >>>
   [tools.diaulos_send]
   permission = "always"

   [tools.diaulos_list_agents]
   permission = "always"
   # <<< diaulos <<<
   ```

   The installer backs up `config.toml` first. Two cases make it stop the vibe part, write neither the plugin nor the block, and say why:
   - `config.toml` already declares an MCP server named `diaulos` (compared as vibe normalizes names). vibe would disable the plugin's server, and `permission = "always"` would go to that other server. Message: "Mistral Vibe already has an MCP server named diaulos: rename or remove it, then run the installer again".
   - `[tools.diaulos_send]` or `[tools.diaulos_list_agents]` already exists without our markers (TOML refuses a table defined twice).

   A project `.vibe/config.toml` can also declare a `diaulos` server; the installer cannot see it. vibe then shows its own warning, and the README says so in Troubleshooting. The uninstaller removes only the marked block. The README and the skill name the tools `send` and `list_agents` of the `diaulos` server; the words `bridge_send` and `bridge_list_agents` go.
4. **A delivery after a busy turn costs one model call and drops the model's last answer from its memory.** A `post_agent` deny is a "turn revision": vibe marks the answer it just wrote `discarded` (`"cause": "retry"`) and asks again with the reason. The person still sees that answer on screen; the model no longer has it in its context and writes a new one that also handles the message. Tool calls made during the turn stay. The `reason` therefore starts with one line saying so: "Your last answer is still on the person's screen. New Diaulos messages arrived:", then the messages. Not chosen instead: delivering busy-turn messages only with the person's next message (`pre_agent`), which keeps the answer but makes them wait for a person.
5. **Price cap.** `export.json` counts only the last of the calls when a retry discards one (G4: two calls of 8,498 and 8,520 input tokens, `usage.input_tokens` 8,520). Whether `--max-price` counts the discarded calls is not checked. Bound: at most 3 discarded calls per turn (`_MAX_POST_AGENT_RETRIES`), so a background run can end above `--max-price 0.50` by at most 3 calls per turn, `--max-turns` bounding the turns. In `-p` sessions the journal's `tokenUsage` is cumulative over the session (T10c, `10-programmer.md`: 8,581, then 17,209, then 25,957 input tokens), so `vibe-run` logs the cost of its run as the last `tokenUsage` of the run minus the last one before it, priced with the model's prices from the config. Whether that cumulative value includes a discarded call is not known: G6 checks it with `--max-price`. Until then the log line gives the cost and, next to it, the number of `discarded` answers with `"cause": "retry"` in the run ("cost 0.0021 $, 1 discarded call not counted" when G6 shows they are not in `tokenUsage`).

### 8.8 Receiving while the TUI is idle: Adrien's choice

**Options A and B below were refused by Adrien on 13 Nov.; a native wake is being searched (T12).** This section will be rewritten after T12.

After G1, an open TUI that is idle cannot be woken by a hook. Two options remain. Both keep the rest as in §8.3: after a busy turn (decision 4 above), with the person's next message (`pre_agent`, with a `⚠ [diaulos:…] message from <sender>` line), and in the background once the TUI is closed (`vibe-run`, within about 30 s when a Claude Code session with the channel is open). While a TUI holds the session, `vibe-run` cannot open it ("Session is already open"), so in both options a message to an idle TUI that stays open waits.

| | **A. No wake while idle** (proposed) | **B. A repeated prompt (`cron`)** |
|---|---|---|
| How | Nothing more. `list` says `TUI: delivers at its next turn`, and the sender's `send` result says the conversation is idle in a TUI and gets the message at its next turn or once closed. | The model schedules a prompt every 30 s with vibe's `cron` tool (only the model can: `/loop` sends the request to the model). Each tick goes through `pre_agent`: inbox empty, the hook answers `deny` and no model call is made (T10b: no `tokenUsage`); inbox not empty, the hook replaces the tick's text with the messages (`hook_specific_output.prompt`) and the turn runs. |
| Delay, idle TUI | Until the person sends a message or closes vibe: minutes or hours. vibe 2.20: 0.7 to 1.0 s. | 30 s at most for the tick, plus the turn. |
| Model cost | None more. | No call on an empty tick. One call per session to create the schedule. |
| Screen | One `⚠` line per delivery. | Per tick, probably the tick's prompt row (`⏵ …`, skill 226) and the `pre_agent` line: up to 120 ticks an hour, so up to about 240 lines an hour of an idle TUI. G3 changes the colour (a warning with our own text instead of "Blocked the message", error level) but not the count. |
| Risks | None new. | The model must create the schedule (devstral-small often misreads instructions, T10b). The schedule stays in the session (`scheduled-loops.json`) after Diaulos is uninstalled. Maximum 50 schedules per session. |
| Work | Included in T11. | About half a day more, after a gate G5. |

**Proposal: A now, B only if G5 passes.** A keeps the TUI clean and costs nothing, but drops the idle criterion for an open TUI: the person gets the message when they next type or close vibe. B gets the criterion back within about 30 s, at the price of screen noise that only a real run can measure.

- **G5** (no longer relevant: B was refused) (only if Adrien wants B): in the real TUI, one `cron` schedule of 30 s and a `pre_agent` that denies an empty tick with an empty `system_message`. Count the rows each empty tick adds to the screen, over 5 ticks; check that a non-empty tick runs the messages as its prompt; check that the person's own message typed during a tick is not lost. B is kept only if an empty tick adds no row.
- **G6** (in T11 in any case): one `vibe -p` run whose `post_agent` discards an answer, with `--max-price` set just above one call: does `--max-price` count the discarded call, and does the cumulative `tokenUsage` of the journal include it?
- **G7** (first step of T11): the launcher. `./` is relative to the plugin root, and vibe runs the plugin from a copy (`<save_dir>/plugins/packages/<hash>/`). Check that `./vibe-mcp` starts from that copy, that the copy keeps the executable bit, and that the server answers `tools/list`. If the bit is lost, `mcp.json` runs `"command": "sh", "args": ["./vibe-mcp"]` instead (`sh` is a bare executable on `PATH`). On Windows, `./vibe-mcp.cmd` is not tried at all.
