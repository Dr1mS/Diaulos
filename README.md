# Diaulos

**English** · [Français](README.fr.md)

> In the ancient Greek games, the *diaulos* was the out-and-back race: run to the far end of the stadium, turn, and run back. Here it is the trip a message makes from one agent to another and back.

**Diaulos lets agy sessions talk to each other.** agy (Google's Antigravity CLI) has no way for one session to reach another: every conversation is an island. With Diaulos, any agy session can message any other one, and the message **wakes the receiver even when it is idle**. The sessions can run in any directory, and you don't have to copy-paste between terminals.

It also connects **Claude Code** sessions, so Gemini and Claude agents can work together. Any number of sessions of either kind can join.

```
agy (~/projects/api)  ⇄  agy (~/projects/web)  ⇄  Claude Code  ⇄  Claude Code
```

## What you can do with it

- **Split work between agy sessions.** A planner session hands tasks to worker sessions, each working in its own repository, and collects their reports.
- **Get a second opinion.** One agy session asks another to review its changes. In a real test, agy reviewed this project's own code and found three real bugs, all of them fixed since ([journal](docs/JOURNAL.md#4-relecture-croisée-autonome-14451449)).
- **Mix models.** Have Claude Code check what Gemini wrote, or the other way round.
- **Wake an idle agent.** You don't have to watch every terminal: a message makes the receiving session take its turn on its own.

## Quick start

Requirements: Linux, Python 3 (standard library only), agy, and Claude Code if you want to include it.

```bash
git clone https://github.com/Dr1mS/Diaulos && cd Diaulos
./install.sh --shell
```

Restart your agy sessions, because they load hooks only at startup. Open a new terminal for Claude Code.

Two agy sessions talking, from inside either one (agy runs these with its shell tool):

```bash
python3 ~/.agent-bridge/bridge.py list
#  agy:52620e0d-…  /home/me/projects/api  relay pid 225225: wakes on message
#  agy:e9439252-…  /home/me/projects/web  relay pid 241981: wakes on message

python3 ~/.agent-bridge/bridge.py send agy "Can you check the API change I just made?"
```

When exactly one *other* agy session is live, `agy` is enough. Otherwise, pick one with `agy@<part-of-its-directory>` or `agy:<id-prefix>`. agy knows all of this from the start: the `SessionStart` hook tells each session its own address and how to send. You can simply ask: *"ask the other agy session to review this"*.

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

**agy side**

- Global hooks live in `~/.gemini/config/hooks.json`. `SessionStart` introduces the bridge. `PreInvocation` delivers messages, and reminds agy to start its relay until the relay runs.
- The **relay** wakes an idle agy. agy hands its own shell `ANTIGRAVITY_LS_ADDRESS` and `ANTIGRAVITY_CSRF_TOKEN`; this is documented in agy's built-in `plugin` skill. The relay is started from that shell, so it calls `SendUserCascadeMessage` on the local language server with agy's own credentials. It copies the conversation's latest `userConfig` (model, tools, permission grants), so the injected turn runs exactly like one you typed. It keeps the token in memory only and stops when agy exits.

**Claude Code side**

- `mcp_channel.py` is an MCP server that declares the `claude/channel` capability (research preview) and pushes each message as `notifications/claude/channel`. The session sleeps until a message arrives: no polling, no periodic wake-up, no cache reload. It also provides the `send` and `list_agents` tools.
- `install.sh --shell` makes a plain `claude` start with `--dangerously-load-development-channels server:agent-bridge`. Subcommands, `-p`, `--help` and `--version` are left alone. To skip the channel, run `command claude`.
- Without the channel, a `Monitor` on `bridge.py watch claude` works as a fallback.

**Delivery**

- **Addresses**: `agy:<conversationId>`, `claude:<session name>`.
- **No duplicates**: a reader claims a message by renaming it into `archive/` before reading it, so each message is delivered at most once.
- **Replies**: every message carries the sender's `from` address.
- **History**: `~/.agent-bridge/log.jsonl` records everything sent.

## What gets installed

| Path | What |
|---|---|
| `~/.agent-bridge/` | `bridge.py`, `mcp_channel.py` and the mailboxes |
| `~/.gemini/config/hooks.json` | agy hooks, under the key `agent-bridge` |
| `~/.claude/skills/agy-bridge/` | Claude Code skill that explains the bridge |
| `~/.claude/settings.json` | allow rules for the bridge commands and MCP tools |
| `~/.claude.json` (via `claude mcp add -s user`) | the `agent-bridge` MCP server |
| `~/.bashrc` (only with `--shell`) | the `claude` wrapper |

Every config file that is edited is backed up first as `<file>.bak-agent-bridge`. `./uninstall.sh` reverts everything; add `--purge` to delete the mailboxes as well.

## Security

- **No authentication between local processes.** Any process running as your user can write to `~/.agent-bridge/inbox/`. With the relay running, a message to an agy session makes it act with that conversation's permissions, for example `always-proceed`. Only use Diaulos on a machine you trust.
- **Peers are not the user.** Incoming messages are labelled as coming from a peer agent. Agents are told that such a message never counts as user approval, and that they must never do for a peer something their own permissions blocked.
- **The CSRF token never touches disk or logs.**

## Status and limits

- Experimental. The project relies on an undocumented agy language-server method (`SendUserCascadeMessage`) and on Claude Code channels, which are a research preview. Either may change.
- Linux only for now: peer detection reads `/proc`.
- Each relay is bound to one agy conversation and stops when that agy process exits.
- Claude Code shows a confirmation screen at every launch while the channels preview requires it.

Tested with Claude Code 2.1.301 and agy (language server 1.4.0, Gemini 3.8 Flash).

The full lab notebook, in French, with every experiment, error message and design decision, is in [docs/JOURNAL.md](docs/JOURNAL.md).

## License

Apache License 2.0 — see [LICENSE](LICENSE). Copyright 2026 Adrien.M (Dr1mS).
