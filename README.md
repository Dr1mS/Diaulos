# Diaulos

**English** · [Français](README.fr.md)

[![Tests](https://github.com/Dr1mS/Diaulos/actions/workflows/tests.yml/badge.svg)](https://github.com/Dr1mS/Diaulos/actions/workflows/tests.yml) ![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue) ![Linux | Windows](https://img.shields.io/badge/platform-Linux%20%7C%20Windows-lightgrey) [![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-green)](LICENSE)

> In the ancient Greek games, the *diaulos* was the out-and-back race: run to the far end of the stadium, turn, and run back. Here it is the trip a message makes from one agent to another and back.

**Diaulos lets agy sessions talk to each other.** agy (Google Antigravity) has no way for one session to reach another: every conversation is an island. With Diaulos, any agy session can message any other one, and the message **wakes the receiver even when it is idle**, or even when its terminal is closed. The sessions can run in any directory, and you don't have to copy-paste between terminals.

It works with the agy CLI and with **Antigravity Desktop**, and it also connects **Claude Code** sessions, so Gemini and Claude agents can work together. Any number of sessions of either kind can join, on Linux and on Windows.

```
agy (~/projects/api)  ⇄  agy (~/projects/web)  ⇄  Antigravity Desktop  ⇄  Claude Code
```

## Contents

- [What you can do with it](#what-you-can-do-with-it)
- [Installation](#installation) · [Quick start](#quick-start) · [Command reference](#command-reference)
- [agy CLI and Antigravity Desktop](#agy-cli-and-antigravity-desktop)
- [How it works](#how-it-works)
- [What gets installed](#what-gets-installed) · [Troubleshooting](#troubleshooting)
- [Security](#security) · [Status and limits](#status-and-limits)
- [Development](#development) · [Changelog](CHANGELOG.md) · [Lab notebook](docs/JOURNAL.md)

## What you can do with it

- **Split work between agy sessions.** A planner session hands tasks to worker sessions, each working in its own repository, and collects their reports.
- **Get a second opinion.** One agy session asks another to review its changes. In a real test, agy reviewed this project's own code and found three real bugs, all of them fixed since ([journal](docs/JOURNAL.md#4-relecture-croisée-autonome-14451449)).
- **Mix models.** Have Claude Code check what Gemini wrote, or the other way round.
- **Wake an idle agent.** You don't have to watch every terminal: a message makes the receiving session take its turn on its own.
- **Reach any agy CLI conversation, even one whose terminal is closed.** The bridge runs its turn with `agy -p --conversation <id>`, in its directory, and the agent answers as usual.
- **Start a new agy session from another agent.** `send agy:new-cli "<task>"` starts an agy CLI conversation with that task as its first prompt; `send agy:new "<task>"` opens one in Antigravity Desktop (Linux), without opening a tab or typing anything.
- **Wake an agent from any program.** A script, a CI job or a bot joins as `ext:<name>`: it sends with `bridge.py send <agent> - --from ext:<name>` and reads the agent's replies with `bridge.py watch ext:<name> --json`. **Read [Security](#security) first:** the program feeds outside text to an agent that can run commands.
- **See the messages fly.** With [Paros](https://github.com/Dr1mS/Paros), each session has a pixel pet on your desktop, and every message sent through Diaulos flies from one pet to the other as a letter.

## Paros

Diaulos is compatible with [Paros](https://github.com/Dr1mS/Paros), a desktop companion for Claude Code and agy. Each session gets a small pet at the bottom of the screen that shows what it is doing: the Claude Code and agy CLI sessions, and the Antigravity Desktop conversations that are shown or recently active. When a session sends a message through Diaulos, its pet throws a letter to the pet of the recipient. A message waiting in an agy inbox puts a mailbox beside its pet. Nothing to configure: Paros reads the bridge commands, the inboxes and the peer files of `~/.agent-bridge/`.

![A Claude pet throws a letter, an agy pet reads one, another thinks with the small pets of its subagents, a last one waves at a question](https://raw.githubusercontent.com/Dr1mS/Paros/main/docs/images/agy.png)

## Requirements

- Linux or Windows 10/11
- Python 3.8 or later (standard library only, nothing to `pip install`)
- agy (Antigravity CLI) or Antigravity Desktop
- Claude Code, if you want Claude sessions to join

## Installation

```bash
git clone https://github.com/Dr1mS/Diaulos && cd Diaulos
```

**Linux**

```bash
./install.sh --shell
```

With Antigravity Desktop, add `--auto-relay` (experimental, read [Experimental: auto-relay](#experimental-auto-relay) first).

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
#  agy:52620e0d-…  "api review"  /home/me/projects/api  relay pid 225225: wakes on message
#  agy:e9439252-…  "web"  /home/me/projects/web  agy CLI, no process: wakes with agy -p

python3 ~/.agent-bridge/bridge.py send agy@web "Can you check the API change I just made?"
```

When exactly one *other* agy session is active (an agy CLI that runs, or a conversation loaded in a Desktop window), `agy` alone is enough. agy knows all of this from the start: the `SessionStart` hook tells each session its own address and the exact commands to use. You can simply ask: *"ask the other agy session to review this"*.

In Claude Code, just ask too: *"send this to agy"*. Claude uses the `send` and `list_agents` tools of the `agent-bridge` MCP server.

> **Windows:** Windows PowerShell does not expand `~` in arguments to programs. There, write `python3 $HOME\.agent-bridge\bridge.py …`. Commands suggested to agents use the absolute path of the Python interpreter, and prefix it with `&` if paths contain spaces (PowerShell 5.1 call operator). agy needs nothing special, because its hooks give it the absolute path.

## Command reference

`bridge.py` is `~/.agent-bridge/bridge.py`. Claude Code sessions use the `send` and `list_agents` MCP tools, which take the same addresses and options.

| Command | What it does |
|---|---|
| `bridge.py list` | Live agents: Claude Code sessions, agy conversations of the last 7 days, `ext:` programs, with how each one gets woken |
| `bridge.py list --days N` / `--all` | Also agy conversations modified in the last N days / of any age (`days` / `all` in `list_agents`) |
| `bridge.py send <to> <text…>` | Send a message; `-` as text reads it from stdin |
| `… --from <address>` | Sender address, for a program or a script (agy fills it in itself) |
| `… --workspace <dir>` | Only with `agy:new` and `agy:new-cli`: where the new session works. For `agy:new`, also the name of a Desktop workspace (`workspace` in the MCP `send` tool) |
| `bridge.py watch <address> [--json]` | Print each new message for that address, forever. With `ext:<name>`, registers the program as a live peer |
| `bridge.py recv <address> [--json]` | Print and archive the waiting messages once |
| `bridge.py ensure-relay` | From an agy shell: start the relay of this agy process (agy is reminded to) |
| `bridge.py agy-relay --probe "<text>"` / `--inspect` | From an agy shell: one test delivery / show the model settings of the conversation |

| Address | Who |
|---|---|
| `agy:<conversationId>`, `agy:<id-prefix>` | One agy conversation (CLI or Desktop) |
| `agy:<title>` | An agy conversation by its title (`/rename`, or the one agy gives it); refused when two share it |
| `agy@<part-of-its-folder>` | The agy conversation whose folder contains that text: the only active one, else the only listed one |
| `agy` | The only other active agy session: an agy CLI that runs, or a conversation its Desktop window has loaded |
| `agy:new`, `agy:new@<title>` | A new Antigravity Desktop conversation, named `<title>` (Linux) |
| `agy:new-cli`, `agy:new-cli@<name>` | A new agy CLI conversation, also reachable as `agy:<name>` |
| `claude:<session name>`, `claude` | A Claude Code session started with the channel; `claude` alone: the only other one, or, when none is listed, the shared inbox read by `watch claude` |
| `ext:<name>` | A program connected with `watch ext:<name>` |

## agy CLI and Antigravity Desktop

| | agy CLI | Antigravity Desktop |
|---|---|---|
| Open, after its first message | reachable once agy has run `ensure-relay` (it is reminded to) | reachable without `ensure-relay` once the window has a relay |
| Open, before any message | not reachable: the conversation does not exist yet | not reachable either; the window's other conversations are, with `--auto-relay` |
| Terminal or tab closed | reachable: each batch of messages runs one turn with `agy -p --conversation <id>` | reachable: delivery loads it from disk, and its turn runs in the background |
| Listed | conversations of the last 7 days; `--days N` / `--all` for older ones; any age by exact id or title | same |
| Start a new session from another agent | `agy:new-cli`, `agy:new-cli@<name>` | `agy:new`, `agy:new@<title>` (Linux) |
| Relay | one per `agy` process, stops when it exits | one per window, stops when its language server exits |

### agy CLI conversations without a terminal

`agy -p "<message>" --conversation <id>` runs one turn of an agy CLI conversation, without a terminal and with your usual agy settings. Diaulos uses it for every agy CLI conversation whose `agy` has exited. `list` shows such a conversation as `agy CLI, no process: wakes with agy -p`, and a message to it runs one turn in its directory. If you open the conversation again with `agy --conversation <id>`, its hooks and relay take over.

Starting a session, for example from Claude Code:

```bash
python3 ~/.agent-bridge/bridge.py send agy:new-cli@builder "Build the project and send the errors to claude:api" --workspace ~/projects/api
# once its first turn is done, the bridge sends you its address, e.g. agy:9a0af1a2-…; it is also reachable as agy:builder
```

Without `--workspace`, the conversation starts in the sender's directory. The `@<name>` is known to the bridge only: agy gives the conversation its own title. Open the session in a terminal with `agy --conversation <id>`.

Things to know:

- These turns run with your agy permissions. With `toolPermission: always-proceed`, they run on their own. With a setting that asks before some tools, a turn that needs one waits until `--print-timeout` (30 min) and fails. Diaulos never passes `--dangerously-skip-permissions`.
- The directory should be one of agy's trusted workspaces (`trustedWorkspaces` in `~/.gemini/antigravity-cli/settings.json`). Other directories are not tested.
- Each turn starts a new `agy` process, about 10 s before the model starts thinking.
- A message is passed to `agy -p` as one argument: messages over 100,000 characters are refused and kept as `.failed`.

### Antigravity Desktop

Desktop runs every conversation of a window in one background process, its language server. One relay per window therefore serves all its conversations: those of its tabs, open or closed, and the ones of the last 7 days that the window has not loaded.

- **With `--auto-relay` (recommended on Linux):** as soon as an agent lists the agy sessions or sends to one, Diaulos starts the window's relay by itself.
- **Without it:** one `ensure-relay` per window start is enough, from any conversation of the window.

Starting a session, for example from Claude Code:

```bash
python3 ~/.agent-bridge/bridge.py send agy:new@tester "Run the test suite and send the failures to claude:api" --workspace ~/projects/api
# the bridge answers with the new address, e.g. agy:e59d7273-…; the session is also reachable as agy:tester
```

`--workspace` takes a directory or the name of a Desktop workspace (e.g. `--workspace "my project"`). Without it, the new conversation opens in the workspace of the conversation that started the relay. When the directory belongs to a Desktop workspace, Desktop files the conversation under it; any other directory only becomes its working directory, and the conversation is listed with the plain conversations.

#### Experimental: auto-relay

With `./install.sh --auto-relay` (Linux only, off by default), a Desktop or IDE window needs no `ensure-relay` and no message at all. Desktop starts its language server with its token on the command line (`--csrf_token`). The bridge reads the token there, finds the server's HTTP port among its listening sockets (the one where `/healthz` answers 200), and starts the relay with them.

Two things trigger it:

- an agent looks for agy sessions (`list`, `list_agents`, or `send` to an `agy` address): at most every 10 s, the bridge looks for language servers of your user without a relay, and starts one for each window that has a conversation. Desktop runs no hook before the first message of a conversation, so this is what makes a freshly opened window reachable;
- a hook runs under a language server without a relay.

The token is passed to the relay through its environment and is never written to disk or logs. Each attempt is recorded, without the token, in `~/.agent-bridge/state/auto-relay.log`. With agy CLI, the option does nothing: its language server runs inside `agy`.

Why it is off by default:

- It reads a secret outside the path agy documents (its `run_command` shell). It gives no new access: on Linux, any local process can already read that command line (`ps`), and the relay already holds the same token. But it is the kind of behavior a security tool or a reviewer may flag.
- Where the token sits is undocumented, and an Antigravity update can move it. When that happens, the hook falls back to the reminder.

Re-run `./install.sh` without `--auto-relay` to turn it off.

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

The tools share no protocol, so Diaulos uses a file mailbox plus the extension points each tool already offers.

```
any agent ── send ──▶  ~/.agent-bridge/inbox/<address>/*.json
                         │
                         ├─▶ agy, working:      PreInvocation hook injects the message before the next model call
                         ├─▶ agy, idle:         relay calls the local language server, which starts a new turn
                         ├─▶ agy CLI, closed:   cli-run runs one turn with agy -p --conversation <id>
                         └─▶ Claude, idle:      MCP channel server pushes a <channel> event, which starts a new turn
```

### agy side

- **Hooks.** Global hooks live in `~/.gemini/config/hooks.json`. `SessionStart` introduces the bridge. `PreInvocation` delivers messages, and reminds agy to start its relay until one runs.
- **The relay** wakes an idle agy. agy hands its own shell `ANTIGRAVITY_LS_ADDRESS` and `ANTIGRAVITY_CSRF_TOKEN`, as documented in agy's built-in `plugin` skill; hooks get neither, only `ANTIGRAVITY_CONVERSATION_ID`. The relay is started from that shell with `bridge.py ensure-relay`, and calls `SendUserCascadeMessage` on the local language server with agy's own credentials. It copies the conversation's latest `userConfig` (model, tools, permission grants), so the injected turn runs exactly like one you typed. It keeps the token in memory only.
- **One relay per agy process.** All the conversations of one agy process share its language server, its address and its token, so one relay serves all of them, each with its own inbox. A conversation whose hook finds a running relay for its process joins it, without `ensure-relay` and without the reminder.
  - agy CLI: hooks and shells are children of `agy`. The relay stops when that `agy` exits, and hands any late message to `cli-run`.
  - Antigravity Desktop: hooks and shells are children of the `language_server` process (`antigravity` → `language_server` → `sh`), shared by every conversation of a window. The relay stops when the language server exits.
  - When neither process is found among the hook ancestors, the relay serves its own conversation only and checks the language server's `/healthz`: if it stays unreachable for 120 s, the relay stops and the conversation is marked `gone` until a new hook runs.
- **Conversations without a process.** For an agy CLI conversation whose `agy` has exited, a message starts `bridge.py cli-run`, one per conversation. It waits a second so that a burst of messages goes in one turn, runs `agy -p` in the conversation's directory, and stops once the inbox is empty; it never runs a turn while an `agy` process holds the conversation. These conversations come from agy's index, `~/.gemini/antigravity-cli/conversation_summaries.db`, without subagents and killed ones.
- **Optional listening `Stop` hook**, for when no relay runs: if `~/.agent-bridge/state/agy.listen` exists, the hook waits for a message for the number of seconds written in that file (6 h if empty). It lets go as soon as you type in that conversation or delete the file.
- **Message format**: each message injected into agy is wrapped in `<bridge-message id="..." from="..." ts="...">...</bridge-message>`.

### Antigravity Desktop internals

- **Discovery.** Every 30 s, the relay asks its language server which conversations it has loaded (`GetAllCascadeTrajectories`) and registers them, archived ones excepted. It also reads Desktop's index, `~/.gemini/antigravity/conversation_summaries.db`, for its own conversations of the last 7 days (agy CLI conversations, subagents, killed and archived ones left out). `list --days` / `--all` and a message to an exact id or title register older ones. Delivery loads a conversation from disk first (`GetCascadeTrajectory`).
- **`agy:new`.** A message to `agy:new` waits in its own inbox. The Desktop relay creates the conversation with `StartCascade` (`source: CORTEX_TRAJECTORY_SOURCE_CASCADE_CLIENT`). For a directory of a Desktop workspace (a project in `~/.gemini/config/projects/*.json`), it passes `projectEnvConfig: {projectId, defaultProjectEnvironment: {}}` and no `workspaceUris`, which the server refuses together; any other directory goes in `workspaceUris`. With `@<title>`, it names the conversation with `UpdateConversationAnnotations`. The message then moves to the new conversation's inbox. A conversation with no prompt yet borrows the config of the latest conversation of the window that has one. `send` refuses `agy:new` while no Desktop window has a relay.
- **Data folder.** The relay learns Desktop's data folder from `ANTIGRAVITY_APP_DATA_DIR`. Only the auto-relay reads the language server's command line.

### Claude Code side

- `mcp_channel.py` is an MCP server that declares the `claude/channel` capability (research preview) and pushes each message as `notifications/claude/channel`. The session sleeps until a message arrives: no polling, no periodic wake-up, no cache reload. It also provides the `send` and `list_agents` tools.
- The channel must be enabled at launch: `claude --dangerously-load-development-channels server:agent-bridge`. The `--shell` / `-Shell` option of the installer does it for a plain `claude`:
  - Linux: a function in `~/.bashrc`. Bypass it with `command claude`.
  - Windows PowerShell: a function in your profile. Windows cmd: a `doskey` macro loaded by cmd's `AutoRun`. Bypass both with `claude.cmd`.
  - Subcommands (`claude mcp …`), `-p`, `--help` and `--version` are left alone. Set `AGENT_BRIDGE_CHANNEL=0` to turn the wrapper off for a shell.
- The MCP server is installed for every Claude session, but it reads its inbox only when its parent `claude` was started with the channel. Otherwise a session without the channel would take messages that Claude Code would then drop silently.
- Without the channel, a `Monitor` on `python3 -u ~/.agent-bridge/bridge.py watch claude` works as a fallback. It reads the shared `claude` inbox and expires after 30 min.

### Delivery

- **Addresses**: see [Command reference](#command-reference). A Claude address follows `/rename`: the session switches to its new name within a second, its old names become aliases, and messages still waiting under old names move with it. A message to an unknown `claude:<name>` is refused with the list of live peers, so nothing is lost silently. An agent cannot message itself. Concrete addresses are strictly validated: path separators (`/`, `\`) and `..` are forbidden.
- **Delivery**: each message is written to a temporary JSON file and renamed atomically. A reader claims messages by moving them into `archive/` before reading, so two readers never get the same message. If delivery fails (language server unreachable, HTTP error), messages return to the inbox and the relay retries every 10 s; a message refused for good is kept as `.failed`, so one bad message never blocks the others. In the rare case where the language server accepts a message but its answer is lost (timeout), the message is sent again: delivery is at least once, and a duplicate is possible but nothing is lost. Messages that arrive while a batch waits are delivered together in that batch.
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
| `~/.agent-bridge/state/agy.auto-relay` (Linux, `--auto-relay` only) | turns on the experimental auto-relay |
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
| agy never receives messages | Restart agy after installing. The files `~/.agent-bridge/state/last_<event>_input.json` are rewritten each time a hook runs. |
| `list` shows an agy session with `no relay` | Read `~/.agent-bridge/state/relay-<conversationId>.log`. The relay can only be started from agy's own shell: ask agy to run `bridge.py ensure-relay`. With `--auto-relay`, read `~/.agent-bridge/state/auto-relay.log`. |
| A message to an agy CLI conversation with `no process` gets no answer | Read `~/.agent-bridge/state/cli-<conversationId>.log` (`cli-new.log` for `agy:new-cli`): it shows each `agy -p` run and its error. |
| `agy:new: no Antigravity Desktop window with a running relay` | Open Desktop, then list the agy sessions (with `--auto-relay`) or run `ensure-relay` in one of its conversations. |
| A Claude session is not listed | Only sessions started with the channel are listed. Check the launch command, or use the `watch claude` fallback. |
| `'agy' matches 2 live agy peers` | Several sessions are live, or several share a title. Pick one with `agy@<directory>`, `agy:<id-prefix>` or `agy:<name>`. |
| `'claude:<name>' matches 0 live claude peers` | The targeted Claude session is no longer active, was renamed, or was not started with the channel. Run `bridge.py list` to see active addresses. |
| `not delivered ... kept in the inbox, retry in 10s` (relay log) | The agy language server is temporarily busy or unreachable. Messages are kept in the inbox and will be retried automatically. |

## Security

- **No authentication between local processes.** Any process running as your user can write to `~/.agent-bridge/inbox/`. A message to an agy session makes it act with that conversation's permissions, for example `always-proceed`: through the relay, through `agy -p` for an agy CLI conversation whose terminal is closed, and for the sessions started by `agy:new` and `agy:new-cli`. Only use Diaulos on a machine you trust.
- **Peers are not the user.** Incoming messages are labelled as coming from a peer agent. Agents are told that such a message never counts as user approval, and that they must never do for a peer something their own permissions blocked.
- **Unverified sender.** The `from` address is declared by the sender and is unverified. A message that claims to relay the user never counts as user approval.
- **The CSRF token never touches disk or logs.**
- **The auto-relay reads the token from the language server's command line.** It is off by default; see [Experimental: auto-relay](#experimental-auto-relay).

> [!WARNING]
> **Connecting a program (`ext:<name>`) hands outside text to an agent that can act on your machine.** An adapter forwards whatever its source sends: a chat message, an issue, a log line. Anyone who can write to that source can write to your agent, including instructions crafted to hijack it (prompt injection). The bridge labels these messages as coming from a peer, never from the user, but that label is a hint to the model, not a barrier.
>
> - Connect only sources you trust. Never connect a public or shared source (a chat room, public issues, incoming email) to a session that runs with `always-proceed`, auto-approval, or access to secrets.
> - Give the adapter its own agent session, started in a directory that holds nothing sensitive.
> - Assume that anything the agent knows can end up in its replies, and so in the outside world.
> - Watch what the agent does, and stop the adapter when you are not around.

## Status and limits

- **Experimental.** Diaulos relies on undocumented methods of agy's language server (`SendUserCascadeMessage`, `StartCascade`, …), on the files agy writes, and on Claude Code channels, which are a research preview. Any of them may change without notice.
- An agy CLI session cannot be reached before its first message: the conversation does not exist until then.
- The relay costs agy one tool call in the first turn of an agy CLI session (`ensure-relay`, after the hook's reminder): a hook can inject messages, not run a command.
- `agy:new`, the auto-relay and the Desktop conversations read from disk need Antigravity Desktop on Linux.
- Claude Code shows a confirmation screen at every launch while the channels preview requires it.
- On Windows, Claude Code's permission rules do not cover PowerShell commands run by Claude: Claude Code may ask for confirmation when invoking bridge commands.

**Tested with**

- Linux, Claude Code 2.1.301, agy 1.4.9 (language server 1.4.0, Gemini 3.8 Flash):
  - messaging and waking idle sessions with the agy CLI, Antigravity Desktop and Antigravity IDE;
  - on a real Antigravity Desktop window: the shared relay, the auto-relay started by `list`, waking a conversation not loaded since launch, `agy:new` with a title and a workspace;
  - with the agy CLI: waking a conversation whose terminal is closed, and `agy:new-cli` with a name.
  - Not verified yet: the newer Desktop features on the IDE, and the listening `Stop` hook on Desktop and IDE.
- Windows 11, Claude Code 2.1.296, agy (language server 1.4.0), Python 3.14: hooks, relay waking an idle agy, MCP channel waking an interactive Claude session, and messages both ways.

The full lab notebook, in French, with every experiment, error message and design decision, is in [docs/JOURNAL.md](docs/JOURNAL.md). Changes by version are in [CHANGELOG.md](CHANGELOG.md).

## Development

The tests use only the standard library. They run against stand-ins (a fake Antigravity language server, a fake `agy`) in temporary folders, so they never touch your real sessions:

```bash
python3 -m unittest discover -s tests -v
```

The Desktop and agy CLI tests run on Linux only, and are skipped elsewhere. GitHub Actions runs the suite on Linux (Python 3.8 and 3.12) and Windows at every push and pull request.

## License

Apache License 2.0 — see [LICENSE](LICENSE). Copyright 2026 Adrien.M (Dr1mS).
