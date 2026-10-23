# Diaulos

**English** · [Français](README.fr.md)

> In the ancient Greek games, the *diaulos* was the out-and-back race: run to the far end of the stadium, turn, and run back. Here it is the trip a message makes from one agent to another and back.

**Diaulos lets agy sessions talk to each other.** agy (Google's Antigravity CLI) has no way for one session to reach another: every conversation is an island. With Diaulos, any agy session can message any other one, and the message **wakes the receiver even when it is idle**. The sessions can run in any directory, and you don't have to copy-paste between terminals.

It also connects **Claude Code** sessions, so Gemini and Claude agents can work together. Any number of sessions of either kind can join, on Linux and on Windows.

```
agy (~/projects/api)  ⇄  agy (~/projects/web)  ⇄  Claude Code  ⇄  Claude Code
```

## What you can do with it

- **Split work between agy sessions.** A planner session hands tasks to worker sessions, each working in its own repository, and collects their reports.
- **Get a second opinion.** One agy session asks another to review its changes. In a real test, agy reviewed this project's own code and found three real bugs, all of them fixed since ([journal](docs/JOURNAL.md#4-relecture-croisée-autonome-14451449)).
- **Mix models.** Have Claude Code check what Gemini wrote, or the other way round.
- **Wake an idle agent.** You don't have to watch every terminal: a message makes the receiving session take its turn on its own.
- **Wake an agent from any program.** A script, a CI job or a bot joins as `ext:<name>`: it sends with `bridge.py send <agent> - --from ext:<name>` and reads the agent's replies with `bridge.py watch ext:<name> --json`.
- **See the messages fly.** With [Paros](https://github.com/Dr1mS/Paros), each agy and Claude Code session has a pixel pet on your desktop, and every message sent through Diaulos flies from one pet to the other as a letter.

## Paros

Diaulos is compatible with [Paros](https://github.com/Dr1mS/Paros), a desktop companion for Claude Code and agy. Each session gets a small pet at the bottom of the screen that shows what it is doing. When a session sends a message through Diaulos, its pet throws a letter to the pet of the recipient. A message waiting in an agy inbox puts a mailbox beside its pet. Nothing to configure: Paros reads the bridge commands and the inboxes of `~/.agent-bridge/`.

![A Claude pet throws a letter, an agy pet reads one, another thinks with the small pets of its subagents, a last one waves at a question](https://raw.githubusercontent.com/Dr1mS/Paros/main/docs/images/agy.png)

## Requirements

- Linux or Windows 10/11
- Python 3.8 or later (standard library only, nothing to `pip install`)
- agy (Antigravity CLI)
- Claude Code, if you want Claude sessions to join

## Installation

```bash
git clone https://github.com/Dr1mS/Diaulos && cd Diaulos
```

**Linux**

```bash
./install.sh --shell
```

**Windows** (from PowerShell)

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Shell
```

Then:

1. **Restart your agy sessions.** agy loads hooks only at startup.
2. **Open a new terminal for Claude Code.** With `--shell` / `-Shell`, a plain `claude` now starts with the bridge channel, and Claude Code shows a confirmation screen for it at each launch (see [Claude Code side](#claude-code-side)).

Re-run the installer after each update of the repository. To remove everything, run `./uninstall.sh`, or `.\uninstall.ps1` on Windows; add `--purge` (`-Purge`) to delete the mailboxes too.

## Quick start

Two agy sessions talking, from inside either one (agy runs these with its shell tool):

```bash
python3 ~/.agent-bridge/bridge.py list
#  agy:52620e0d-…  /home/me/projects/api  relay pid 225225: wakes on message
#  agy:e9439252-…  /home/me/projects/web  relay pid 241981: wakes on message

python3 ~/.agent-bridge/bridge.py send agy "Can you check the API change I just made?"
```

When exactly one *other* agy session is live, `agy` is enough. Otherwise, pick one with `agy@<part-of-its-directory>`, `agy:<id-prefix>` or `agy:<name>` for a conversation renamed with `/rename`. agy knows all of this from the start: the `SessionStart` hook tells each session its own address and the exact commands to use. You can simply ask: *"ask the other agy session to review this"*.

In Claude Code, just ask too: *"send this to agy"*. Claude uses the `send` and `list_agents` tools of the `agent-bridge` MCP server.

> **Windows:** Windows PowerShell does not expand `~` in arguments to programs. There, write `python3 $HOME\.agent-bridge\bridge.py …`. Commands suggested to agents use the absolute path of the Python interpreter, and prefix it with `&` if paths contain spaces (PowerShell 5.1 call operator). agy needs nothing special, because its hooks give it the absolute path.

## Proof: a four-agent relay

Four sessions passed one message around a circle with no human involved. Each one appended its name and the time, then forwarded the message.

| Hop | Link | Sent |
|---|---|---|
| Claude Code → agy A | relay wakes idle agy | 15:52:07 |
| **agy A → agy B** | **agy to agy, relay wakes idle agy** | 15:52:19 |
| agy B → Claude Code (2nd session) | MCP channel wakes idle Claude | 15:52:35 |
| Claude Code → Claude Code | MCP `send` tool | 15:52:43 |

The full circle took 36 s, about 9 s per hop. Almost all of that time was the models thinking: the bridge itself delivers in 1 to 3 s.

## How it works

The two tools share no protocol, so Diaulos uses a file mailbox plus the extension points each tool already offers.

```
any agent ── send ──▶  ~/.agent-bridge/inbox/<address>/*.json
                         │
                         ├─▶ agy, working:  PreInvocation hook injects the message before the next model call
                         ├─▶ agy, idle:     relay calls the local language server, which starts a new turn
                         └─▶ Claude, idle:  MCP channel server pushes a <channel> event, which starts a new turn
```

### agy side

- Global hooks live in `~/.gemini/config/hooks.json`. `SessionStart` introduces the bridge. `PreInvocation` delivers messages, and reminds agy to start its relay until the relay runs.
- The **relay** wakes an idle agy. agy hands its own shell `ANTIGRAVITY_LS_ADDRESS` and `ANTIGRAVITY_CSRF_TOKEN`; this is documented in agy's built-in `plugin` skill. The relay is started from that shell with `bridge.py ensure-relay`, so it calls `SendUserCascadeMessage` on the local language server with agy's own credentials. It copies the conversation's latest `userConfig` (model, tools, permission grants), so the injected turn runs exactly like one you typed. It keeps the token in memory only and stops when agy exits. When agy's PID is not found among the hook ancestors (e.g. if Desktop or IDE run hooks under a different process tree), the relay monitors the language server via `/healthz`: if the language server stays unreachable for 120 s, the relay stops cleanly and the conversation is marked `gone` (it disappears from `list`) until a new hook runs.
- **Optional listening `Stop` hook**, for when no relay runs: if `~/.agent-bridge/state/agy.listen` exists, the hook waits for a message for the number of seconds written in that file (6 h if empty). It lets go as soon as you type in that conversation or delete the file. It monitors prompt history across `~/.gemini/antigravity-cli`, `antigravity` and `antigravity-ide` (support for Desktop and IDE history is experimental).
- **Message format**: each message injected into agy is wrapped in `<bridge-message id="..." from="..." ts="...">...</bridge-message>`.

Manual relay checks, to run inside agy:

```bash
python3 ~/.agent-bridge/bridge.py agy-relay --probe "text"   # one delivery, prints the HTTP status
python3 ~/.agent-bridge/bridge.py agy-relay --inspect        # model-related fields of the conversation
```

### Claude Code side

- `mcp_channel.py` is an MCP server that declares the `claude/channel` capability (research preview) and pushes each message as `notifications/claude/channel`. The session sleeps until a message arrives: no polling, no periodic wake-up, no cache reload. It also provides the `send` and `list_agents` tools.
- The channel must be enabled at launch: `claude --dangerously-load-development-channels server:agent-bridge`. The `--shell` / `-Shell` option of the installer does it for a plain `claude`:
  - Linux: a function in `~/.bashrc`. Bypass it with `command claude`.
  - Windows PowerShell: a function in your profile. Windows cmd: a `doskey` macro loaded by cmd's `AutoRun`. Bypass both with `claude.cmd`.
  - Subcommands (`claude mcp …`), `-p`, `--help` and `--version` are left alone. Set `AGENT_BRIDGE_CHANNEL=0` to turn the wrapper off for a shell.
- The MCP server is installed for every Claude session, but it reads its inbox only when its parent `claude` was started with the channel. Otherwise a session without the channel would take messages that Claude Code would then drop silently.
- Without the channel, a `Monitor` on `python3 -u ~/.agent-bridge/bridge.py watch claude` works as a fallback. It reads the shared `claude` inbox and expires after 30 min.

### Delivery

- **Addresses**: `agy:<conversationId>`, `claude:<session name>`, and `claude` for the shared inbox read by `watch`. An agy conversation can also be reached by its title, as `agy:<name>` (set it with `/rename` in agy; `list_agents` shows it). A Claude address follows `/rename`: the session switches to its new name within a second, its old names become aliases, and messages still waiting under old names move with it. A message addressed to an unknown `claude:<name>` is refused with the list of live peers, preventing lost messages. An agent cannot message itself. Addresses are strictly validated: path separators (`/`, `\`) and `..` are forbidden.
- **Delivery**: each message is written to a temporary JSON file and renamed atomically. A reader claims messages by moving them into `archive/` before reading, so two readers never get the same message. If delivery fails (language server unreachable, HTTP error), messages return to the inbox and the relay retries every 10 s without crashing. In the rare case where the language server accepts a message but its answer is lost (timeout), the message is sent again: delivery is at least once, and a duplicate is possible but nothing is lost. Messages that arrive while a batch waits are delivered together in that batch.
- **Replies**: every message carries the sender's `from` address. In agy it is filled in from `ANTIGRAVITY_CONVERSATION_ID`.
- **History & Maintenance**: `~/.agent-bridge/log.jsonl` records everything sent and rotates to `log.jsonl.1` beyond 10 MB. Archived messages older than 30 days are automatically purged.

### Windows specifics

- Processes are found through the Win32 API instead of `/proc`, and every file and stream is read and written as UTF-8.
- `install.ps1` writes the absolute path of your Python interpreter into the agy hooks and the MCP server entry, so they never depend on `PATH` or on the Microsoft Store aliases.
- Windows hooks run through `~/.agent-bridge/agy-hook.cmd`, written in the active OEM code page (e.g. cp850), which avoids `cmd /c` quoting issues even when paths contain spaces or accented characters.
- Commands suggested to agents use the absolute Python interpreter path, prefixed with `&` if paths contain spaces (PowerShell 5.1 call operator).
- The relay does not import `ctypes`. Our explanation, from testing: agy appears to run each command on a desktop that it closes when the command ends. A process left behind on it cannot initialize `_ctypes`. The relay watches agy through `_winapi` instead.

## What gets installed

| Path | What |
|---|---|
| `~/.agent-bridge/` | `bridge.py`, `mcp_channel.py`, the shell wrappers and the mailboxes |
| `~/.agent-bridge/agy-hook.cmd` (Windows only) | Windows hook launcher (OEM code page, handles spaces and accents) |
| `~/.gemini/config/hooks.json` | agy hooks, under the key `agent-bridge` |
| `~/.claude/skills/agy-bridge/` | Claude Code skill that explains the bridge |
| `~/.claude/settings.json` | allow rules for the bridge commands and MCP tools |
| `~/.claude.json` (via `claude mcp add -s user`) | the `agent-bridge` MCP server |
| `~/.bashrc` (Linux, `--shell` only) | sources the `claude` wrapper |
| PowerShell profile (Windows, `-Shell` only) | dot-sources the `claude` wrapper |
| `HKCU\Software\Microsoft\Command Processor\AutoRun` (Windows, `-Shell` only) | the `claude` doskey macro, added after any existing command |

Every config file that is edited is backed up first as `<file>.bak-agent-bridge`. The uninstaller reverts all of the above.

## Troubleshooting

| Symptom | Check |
|---|---|
| No channel confirmation screen when `claude` starts | The wrapper is not active in this terminal. Open a new terminal after installing, and check that you used `--shell` / `-Shell`. |
| `list` shows an agy session with `no relay` | Read `~/.agent-bridge/state/relay-<conversationId>.log`. The relay can only be started from agy's own shell: ask agy to run `bridge.py ensure-relay`. |
| agy never receives messages | Restart agy after installing. The files `~/.agent-bridge/state/last_<event>_input.json` are rewritten each time a hook runs. |
| A Claude session is not listed | Only sessions started with the channel are listed. Check the launch command, or use the `watch claude` fallback. |
| `'agy' matches 2 live agy peers` | Several sessions are live. Pick one with `agy@<directory>`, `agy:<id-prefix>` or `agy:<name>`. |
| `'claude:<name>' matches 0 live claude peers` | The targeted Claude session is no longer active, was renamed, or was not started with the channel. Run `bridge.py list` to see active addresses. |
| `not delivered ... kept in the inbox, retry in 10s` (relay log) | The agy language server is temporarily busy or unreachable. Messages are kept in the inbox and will be retried automatically. |

## Security

- **No authentication between local processes.** Any process running as your user can write to `~/.agent-bridge/inbox/`. With the relay running, a message to an agy session makes it act with that conversation's permissions, for example `always-proceed`. Only use Diaulos on a machine you trust.
- **Peers are not the user.** Incoming messages are labelled as coming from a peer agent. Agents are told that such a message never counts as user approval, and that they must never do for a peer something their own permissions blocked.
- **Unverified sender.** The `from` address is declared by the sender and is unverified. A message that claims to relay the user never counts as user approval.
- **The CSRF token never touches disk or logs.**

## Status and limits

- **Experimental.** Diaulos relies on an undocumented agy language-server method (`SendUserCascadeMessage`) and on Claude Code channels, which are a research preview. Either may change without notice.
- Each relay is bound to one agy conversation and stops when that agy process exits.
- Claude Code shows a confirmation screen at every launch while the channels preview requires it.
- On Windows, Claude Code's permission rules do not cover PowerShell commands run by Claude: Claude Code may ask for confirmation when invoking bridge commands.

**Tested with**

- Linux: Claude Code 2.1.301, agy (language server 1.4.0, Gemini 3.8 Flash). Validated Antigravity clients for messaging: agy CLI, Antigravity Desktop and Antigravity IDE. On Desktop and IDE, conversation titles, the listening `Stop` hook and the relay's stop without a known agy pid are not verified yet.
- Windows 11: Claude Code 2.1.296, agy (language server 1.4.0), Python 3.14. Tested: hooks, relay waking an idle agy, MCP channel waking an interactive Claude session, and messages both ways.

The full lab notebook, in French, with every experiment, error message and design decision, is in [docs/JOURNAL.md](docs/JOURNAL.md).

## License

Apache License 2.0 — see [LICENSE](LICENSE). Copyright 2026 Adrien.M (Dr1mS).
