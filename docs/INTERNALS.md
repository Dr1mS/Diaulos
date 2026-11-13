# Diaulos internals

How Diaulos works under the hood. For installing and using it, read the [README](../README.md). The [lab notebook](JOURNAL.md) (in French) tells how each piece was found and tested.

## Overview

Claude Code, agy and Mistral Vibe share no protocol, so Diaulos uses a file mailbox plus the extension points each tool already offers.

```
any agent ── send ──▶  ~/.agent-bridge/inbox/<address>/*.json
                         │
                         ├─▶ agy, working:      PreInvocation hook injects the message before the next model call
                         ├─▶ agy, idle:         relay calls the local language server, which starts a new turn
                         ├─▶ agy CLI, closed:   cli-run runs one turn with agy -p --conversation <id>
                         ├─▶ Claude, idle:      MCP channel server pushes a <channel> event, which starts a new turn
                         ├─▶ vibe TUI:          the relay between vibe-rs and vibe-app-server enqueues it (session/turn/enqueue)
                         ├─▶ vibe, closed:      vibe-run runs one turn with vibe --prompt-file … --resume <session>
                         └─▶ remote:<machine>:  the gateway seals it and publishes it on the relay
```

All state lives in `~/.agent-bridge/` (`AGENT_BRIDGE_HOME`): `inbox/`, `archive/`, `peers/` (live agents), `state/` (flags and logs), `remote/` (pairs and gateway), and `log.jsonl`.

## agy side

- **Hooks.** Global hooks live in `~/.gemini/config/hooks.json`. `SessionStart` introduces the bridge. `PreInvocation` delivers messages, and reminds agy to start its relay until one runs.
- **The relay** wakes an idle agy. agy hands its own shell `ANTIGRAVITY_LS_ADDRESS` and `ANTIGRAVITY_CSRF_TOKEN`, as documented in agy's built-in `plugin` skill; hooks get neither, only `ANTIGRAVITY_CONVERSATION_ID`. The relay is started from that shell with `bridge.py ensure-relay`, and calls `SendUserCascadeMessage` on the local language server with agy's own credentials. It copies the conversation's latest `userConfig` (model, tools, permission grants), so the injected turn runs exactly like one you typed. It keeps the token in memory only.
- **One relay per agy process.** All the conversations of one agy process share its language server, its address and its token, so one relay serves all of them, each with its own inbox. A conversation whose hook finds a running relay for its process joins it, without `ensure-relay` and without the reminder.
  - agy CLI: hooks and shells are children of `agy`. The relay stops when that `agy` exits, and hands any late message to `cli-run`.
  - Antigravity Desktop: hooks and shells are children of the `language_server` process (`antigravity` → `language_server` → `sh`), shared by every conversation of a window. The relay stops when the language server exits.
  - When neither process is found among the hook ancestors, the relay serves its own conversation only and checks the language server's `/healthz`: if it stays unreachable for 120 s, the relay stops and the conversation is marked `gone` until a new hook runs.
- **Conversations without a process.** For an agy CLI conversation whose `agy` has exited, a message starts `bridge.py cli-run`, one per conversation. It waits a second so that a burst of messages goes in one turn, runs `agy -p` in the conversation's directory, and stops once the inbox is empty; it never runs a turn while an `agy` process holds the conversation. These conversations come from agy's index, `~/.gemini/antigravity-cli/conversation_summaries.db`, without subagents and killed ones.
- **Optional listening `Stop` hook**, for when no relay runs: if `~/.agent-bridge/state/agy.listen` exists, the hook waits for a message for the number of seconds written in that file (6 h if empty). It lets go as soon as you type in that conversation or delete the file.
- **Message format**: each message injected into agy is wrapped in `<bridge-message id="..." from="..." ts="...">...</bridge-message>`.

## Antigravity Desktop

- **Discovery.** Every 30 s, the relay asks its language server which conversations it has loaded (`GetAllCascadeTrajectories`) and registers them, archived ones excepted. It also reads Desktop's index, `~/.gemini/antigravity/conversation_summaries.db`, for its own conversations of the last 7 days (agy CLI conversations, subagents, killed and archived ones left out). `list --days` / `--all` and a message to an exact id or title register older ones. Delivery loads a conversation from disk first (`GetCascadeTrajectory`).
- **`agy:new`.** A message to `agy:new` waits in its own inbox. The Desktop relay creates the conversation with `StartCascade` (`source: CORTEX_TRAJECTORY_SOURCE_CASCADE_CLIENT`). For a directory of a Desktop workspace (a project in `~/.gemini/config/projects/*.json`), it passes `projectEnvConfig: {projectId, defaultProjectEnvironment: {}}` and no `workspaceUris`, which the server refuses together; any other directory goes in `workspaceUris`. With `@<title>`, it names the conversation with `UpdateConversationAnnotations`. The message then moves to the new conversation's inbox. A conversation with no prompt yet borrows the config of the latest conversation of the window that has one. `send` refuses `agy:new` while no Desktop window has a relay.
- **Data folder.** The relay learns Desktop's data folder from `ANTIGRAVITY_APP_DATA_DIR`.
- **Auto-relay** (`install.sh --auto-relay`, Linux). Desktop starts its language server with its token on the command line (`--csrf_token`). The bridge reads the token there, finds the server's HTTP port among its listening sockets (the one where `/healthz` answers 200), and starts the relay with them. It runs when an agent looks for agy sessions (at most every 10 s, for each language server of your user without a relay) and when a hook runs under a language server without a relay. The token goes to the relay through its environment, never to disk or logs; each attempt is recorded, without the token, in `state/auto-relay.log`.

## Claude Code side

- `mcp_channel.py` is an MCP server that declares the `claude/channel` capability (research preview) and pushes each message as `notifications/claude/channel`. The session does no polling and has no periodic wake-up: it sleeps until a message arrives (the server checks the inbox folder every second). It also provides the `send` and `list_agents` tools.
- The channel must be enabled at launch: `claude --dangerously-load-development-channels server:agent-bridge`. The `--shell` / `-Shell` option of the installer does it for a plain `claude`:
  - Linux: a function in `~/.bashrc`. Bypass it with `command claude`.
  - Windows PowerShell: a function in your profile. Windows cmd: a `doskey` macro loaded by cmd's `AutoRun`. Bypass both with `claude.cmd`.
  - Subcommands (`claude mcp …`), `-p`, `--help` and `--version` are left alone. Set `AGENT_BRIDGE_CHANNEL=0` to turn the wrapper off for a shell.
- The MCP server is installed for every Claude session, but it reads its inbox only when its parent `claude` was started with the channel. Otherwise a session without the channel would take messages that Claude Code would then drop silently.
- A session's address is `claude:<session name>`, read from `~/.claude/sessions/<pid>.json`. It follows `/rename`: the old names become aliases and their waiting messages move to the new inbox.
- Without the channel, a `Monitor` on `python3 -u ~/.agent-bridge/bridge.py watch claude` works as a fallback. It reads the shared `claude` inbox and expires after 30 min.

## Mistral Vibe side

Diaulos needs Mistral Vibe 3 (tested with 3.1.2). vibe 3 is a Rust TUI, `vibe-rs`, that talks to a Python `vibe-app-server` in newline-delimited JSON-RPC over stdio. The full design, with the source lines of vibe 3.1.2 it relies on and the results of the gates R1 to R9, is in [VIBE-DESIGN.md](VIBE-DESIGN.md) §8.

- **A native plugin.** `install/vibe_install.py`, called by all four install and uninstall scripts, copies `install/vibe-plugin/` to `$VIBE_HOME/plugins/diaulos/`, with the absolute paths of the bridge and of its Python filled in. The plugin declares the MCP server `diaulos` (`vibe_mcp.py`, through the launcher `vibe-mcp`), two hooks (`session_start` and `pre_agent`, both run by `vibe_hook.py`) and a skill. The installer backs up `config.toml` and writes one marked block that gives `[tools.diaulos_send]` and `[tools.diaulos_list_agents]` `permission = "always"`. It stops the vibe part, writing nothing, when `config.toml` already declares a server named `diaulos` (vibe would disable ours) or one of those tables. Below vibe 3 it says so and stops. It removes the vibe 2.20 shim `tools/agent_bridge.py` when it is ours.
- **Address.** A session id is `xxxxxxxx-xxxx-xxxx-xxxx-<suffix>`; the address is `vibe:<suffix>` (12 hex digits), or `vibe:<name>` after `/rename`. vibe 3 keeps the id through `/compact` and `--resume`; `/clear` starts a new id, so a new address. A conversation is every saved session (`<save_dir>/unified/<id>/meta.json`) with one suffix; its current session is the one saved last. A session whose parent has another suffix is a subagent and is left out. A session that dies before its first saved message has no folder and is not listed.
- **Sender.** The `session_start` hook writes `state/vibe-current/<vibe-app-server pid>.json` with the main session that this server serves now (start, resume, clear, fork). The MCP server, one per `vibe-app-server`, reads it at each `send`, so the sender follows `/resume` and `/clear`. A subagent sends as its main session. The hook also shows the address in the TUI and tells it to the model.
- **The relay** (`vibe_relay.py`) wakes an open TUI. `vibe-diaulos` (`vibe_diaulos.py`) runs vibe's own launcher, `exec_rust_cli(argv, app_server_cmd=[…, vibe_relay.py, --real, vibe-app-server])`, under vibe's own Python, so `vibe-rs` talks to the relay, which starts the real server. Two loops copy whole lines both ways, unchanged. In its own thread, the relay follows the TUI's main session (the answers to `session/start`, `session/resume`, `session/continue` and `session/history/clear`, plus the hook's file; the newest wins; `session/fork` is not followed, as `/branch` leaves the TUI on its session). Every 0.5 s it claims the inbox of `vibe:<suffix>` and sends `session/turn/enqueue` with `delivery: "start_turn"` and a sender `{"sessionId": "diaulos:<from>", "title": "<from>"}`. vibe starts a turn on an idle session and shows the sender and the text itself. During a running turn, the message joins that turn: the model answers both in the same turn. The answer to the relay's own request ids (`diaulos-relay-<n>`) never reaches the TUI. An error or no answer in 10 s puts the messages back; after 3 failed deliveries in a row, the relay turns waking off for that session and `list` says so. SIGINT is ignored, so Ctrl+C reaches vibe; when `vibe-rs` is gone, the relay stops the real server and exits with its code. `--resume` first goes through an empty session for about 1.2 s: a message waits at most that long.
- **Which uses go through the relay.** `vibe-diaulos` sends the 13 spellings that never open the TUI to the real `vibe`, unchanged: `-p`, `--prompt`, `--prompt-file`, `-v`, `--version`, `-h`, `--help`, `--setup`, `--check-upgrade`, `mcp`, `config`, `help`, `update`. If vibe's launcher cannot be imported or has no `app_server_cmd`, the real `vibe` runs with no relay and one line says so. With `--shell`, the installer adds a `vibe` function (bash, PowerShell) that calls `vibe-diaulos`.
- **A TUI with no relay.** A TUI opened with the real `vibe` is not woken. The `pre_agent` hook recognises a turn sent by the relay (the `diaulos:` prefix of the sender's session id) or by `vibe-run` and does nothing; for a message the person typed in a session that no live relay serves, it claims the waiting messages and adds them after that message.
- **In-flight record.** Before a request, the relay (and `vibe-run`, before a run) writes `state/vibe-inflight/<suffix>.json` with its pid, the claimed messages and the request's entry id. If the process dies before the answer, whoever reads vibe peers next puts the messages back, unless the session already holds that entry (`recover_inflight`).
- **Closed conversation.** `send` starts `bridge.py vibe-run <suffix>` (one per conversation, detached, like `cli-run`). It waits 1 s for a burst, then runs `vibe --prompt-file <batch> --resume <session> --max-price <cap> --max-turns <n> --output json --output-dir <tmp>` in the conversation's folder, with `AGENT_BRIDGE_VIBE_RUN=1` so the hooks leave delivery to it, and reads the cost and the outcome from `export.json`. Nobody is there to approve: only the tools allowed without asking run. The caps are vibe's own, per run, from `state/vibe-limits.json` (`max_turns` 20, `max_price` 0.50, 3 attempts). `Session is already open` means a TUI came back: the messages wait for it. `vibe:new` goes through `bridge.py vibe-new`, one `vibe -p --workdir <dir>` per message.
- **Who holds a conversation.** `state/vibe-relay/<relay pid>.json` (session, `tui_pid`, `relay_pid`, `real_pid`, `wake`) marks a TUI that the relay wakes; `state/vibe-current/` marks any live TUI or `vibe -p`. The peer file `peers/vibe-<suffix>.json` keeps `runner_pid` and the aliases of `vibe:new@<name>`.
- **Loops.** Each `@` in a delivered text gets a word joiner after it, so vibe does not inline a file. `send` stops sending to an address that failed twice in the same MCP server process.
- **Logs.** `state/vibe-mcp.log`, `vibe-hook.log`, `vibe-relay.log` (only whitelisted keys, never a message text; `AGENT_BRIDGE_VIBE_RELAY_LOG=0` turns it off), `vibe-<suffix>.log` (`vibe-run`) and `vibe-new.log`.
- **A private protocol.** The relay depends on vibe's app-server protocol, which is not public. Run `DIAULOS_REAL_VIBE=1 bash tests/run-proofs.sh` again after each vibe update.

## Delivery

- **Addresses.** A message to an unknown `claude:<name>` is refused with the list of live peers, so nothing is lost silently. An agent cannot message itself. Concrete addresses are strictly validated: path separators are forbidden, and an address cannot end in `.` or `..`.
- **Claims.** Each message is written to a temporary JSON file and renamed atomically. A reader claims messages by moving them into `archive/` before reading, so two readers never get the same message. On Windows, a reader first creates a `.claim` marker with `O_EXCL`, because the rename alone was seen to let two readers through.
- **Failures.** If delivery fails (language server unreachable, HTTP error), messages return to the inbox and the relay retries every 10 s; a message refused for good is kept as `.failed`, so one bad message never blocks the others. If the language server accepts a message but its answer is lost (timeout), the message is sent again, so a duplicate is possible. A reader killed between the claim and the delivery loses the message. Messages that arrive while a batch waits are delivered together in that batch.
- **Replies.** Every message carries the sender's `from` address. In agy it is filled in from `ANTIGRAVITY_CONVERSATION_ID`.
- **History.** `log.jsonl` records everything sent and rotates to `log.jsonl.1` beyond 10 MB. Archived messages older than 30 days are purged.

## Other machines

`bridge/remote.py`, loaded only for remote work (it needs `cryptography`).

- **Addresses.** `remote:<machine>/<address>` resolves locally to the inbox `remote:<machine>`; the message keeps `<address>` as `remote_to`, and the other machine resolves it with its own sessions.
- **Pairing.** `invite` builds a code `diaulos1.<base64>` holding the relay URL, a 256-bit secret and an expiry (15 min). Both sides derive a pairing topic and a MAC key from the secret with HKDF-SHA256. The joiner publishes an X25519 public key with an HMAC; the inviter answers with its own; both derive the root key with HKDF over the shared secret, salted with the invite secret and bound to both public keys. A 6-digit code derived from the root key is shown on both sides; each side then publishes an authenticated `ok` or `no`, and a pair is saved only when both said `ok`. The pairing topic is read with `since=all`, so no clock is involved. `invite`, `join`, `allow` and `relay-server` refuse to run without a TTY.
- **Frames.** Per direction, HKDF gives a key and a relay topic (`dl` + 30 base32 characters). A payload (JSON) is padded to a multiple of 256 bytes and cut into chunks of 2,900 bytes; each chunk becomes `d1` + base64url(nonce ‖ ChaCha20-Poly1305(seq, part, parts ‖ chunk)), with the topic as associated data, which stays under ntfy.sh's 4,096-byte limit. The receiver accepts a sequence number only above the last one it handled, and refuses payloads older than 13 h.
- **Payloads.** `msg` (a message), `notice` (a fixed code: `refused`, `rate`, `unavailable`, rendered by the receiving side), `roster` (the sessions the other machine may write to first, at most every 30 min) and `roster?` (sent when a gateway starts).
- **Policy.** A message is delivered if its resolved address matches the pair's allow list (`fnmatch`; `agy:new` and `agy:new-cli` only by exact name), or if that exact address wrote to the machine in the last 24 h and was a live session then. Refusals and unknown targets get the same notice. At most 20 inbound messages and one notice a minute per machine. A sender from another machine can never be forwarded to a third one.
- **Gateway.** One process for every pair (`bridge.py remote gateway`). Outbound: it claims one message at a time from each `remote:<machine>` inbox and publishes its frames, paced by a token bucket (55 at once, then one every 5 s, ntfy.sh's limits). Inbound: one streaming subscription per relay (`/<topics>/json?since=…`), reconnected on errors. A separate thread writes a heartbeat to `remote/gateway.json` every 10 s; a gateway counts as running only with a heartbeat under 60 s and a live pid. State (sequence numbers, `since`, reply contacts) is written with `fsync`. It starts after pairing, from the agy `SessionStart` hook (not on Windows), when the MCP channel starts, and on a send.
- **Relay.** `remote relay-server` implements the part of the ntfy API the gateway uses: `POST /<topic>` and `GET /<topic>[,…]/json?since=<all|time|id|duration>[&poll=1]`. Messages stay 12 h within a 64 MiB budget and 500 per topic. Per client (/64 for IPv6; the last `X-Forwarded-For` hop behind `--trusted-proxy`): a token bucket shared by posts and subscriptions, and at most 8 subscriptions. Connections are capped per address and in total, and a request that is not complete after 30 s is closed.

## What gets installed

| Path | What |
|---|---|
| `~/.agent-bridge/` | `bridge.py`, `mcp_channel.py`, `remote.py`, `vibe_sessions.py`, `vibe_runner.py`, `vibe_mcp.py`, `vibe_hook.py`, `vibe_relay.py`, `vibe_diaulos.py`, the command `vibe-diaulos` (`vibe-diaulos.cmd` on Windows), the shell wrappers and the mailboxes |
| `~/.agent-bridge/remote/` (after pairing) | the keys of paired machines and the gateway state |
| `~/.agent-bridge/agy-hook.cmd` (Windows) | Windows hook launcher (OEM code page, handles spaces and accents) |
| `~/.agent-bridge/state/agy.auto-relay` (Linux, `--auto-relay`) | turns on the experimental auto-relay |
| `~/.gemini/config/hooks.json` | agy hooks, under the key `agent-bridge` |
| `~/.vibe/plugins/diaulos/` (`$VIBE_HOME/plugins/`, when vibe 3 is installed) | the Mistral Vibe plugin: MCP server `diaulos`, hooks `session_start` and `pre_agent`, a skill |
| `~/.vibe/config.toml` | one marked block that lets the two `diaulos` tools run without approval |
| `~/.claude/skills/agy-bridge/`, `~/.claude/skills/diaulos-remote/` | Claude Code skills |
| `~/.claude/settings.json` | allow rules for the bridge commands and MCP tools |
| `~/.claude.json` (via `claude mcp add -s user`) | the `agent-bridge` MCP server |
| `~/.bashrc` (Linux, `--shell`) | sources the `claude` wrapper and the `vibe` function |
| PowerShell profile (Windows, `-Shell`) | dot-sources the `claude` wrapper and the `vibe` function |
| `HKCU\Software\Microsoft\Command Processor\AutoRun` (Windows, `-Shell`) | the `claude` doskey macro, added after any existing command |

Every config file that is edited is backed up first as `<file>.bak-agent-bridge`. The uninstaller reverts all of the above; the mailboxes and the keys stay unless `--purge` (`-Purge`). A systemd unit or a scheduled task you created for the gateway is yours to remove.

## Windows specifics

- Processes are found through the Win32 API instead of `/proc`, and every file and stream is read and written as UTF-8.
- `install.ps1` writes the absolute path of your Python interpreter into the agy hooks and the MCP server entry, so they never depend on `PATH` or on the Microsoft Store aliases.
- Windows hooks run through `~/.agent-bridge/agy-hook.cmd`, written in the active OEM code page (e.g. cp850), which avoids `cmd /c` quoting issues even when paths contain spaces or accented characters.
- Commands suggested to agents use the absolute Python interpreter path, prefixed with `&` if paths contain spaces (PowerShell 5.1 call operator).
- Mistral Vibe: `install.ps1` and `uninstall.ps1` call the same `install/vibe_install.py` as the shell scripts, which writes `vibe-diaulos.cmd`. Nothing of the vibe part has been tried on Windows yet (`vibe-rs.exe`, `vibe-diaulos.cmd`, `vibe-mcp.cmd`, the PowerShell function, Ctrl+C).
- The relay does not import `ctypes`. Our explanation, from testing: agy appears to run each command on a desktop that it closes when the command ends. A process left behind on it cannot initialize `_ctypes`. The relay watches agy through `_winapi` instead. For the same reason, the agy hook does not start the remote gateway on Windows.
