# Changelog

All notable changes to Diaulos. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/). The lab notebook, [docs/JOURNAL.md](docs/JOURNAL.md) (in French), tells how and why each change was made.

## [Unreleased]

### Added

- **Mistral Vibe 3.** vibe 3 conversations join as a third kind of agent, `vibe:<id>` (the last 12 hex digits of the session id, kept across `/compact` and `--resume`; `/clear` gives a new one) or `vibe:<name>` after `/rename`. The installer copies a native vibe plugin to `~/.vibe/plugins/diaulos/` (an MCP server `diaulos` with the tools `send` and `list_agents`, the hooks `session_start` and `pre_agent`, a skill) and writes one marked block, after a backup, in `~/.vibe/config.toml` so that both tools run without approval; it stops without writing anything if that file already has an MCP server named `diaulos`. `vibe-diaulos` (and, with `--shell`, a `vibe` function) opens the TUI with a small Diaulos relay between vibe's TUI and its app server: a message wakes an idle TUI, and one sent during a turn joins that turn; vibe shows the sender and the text. The uses that never open the TUI (`-p`, `mcp`, `config`, `update`, `--version` and the others) go to the real `vibe` unchanged. A TUI opened with the real `vibe` gets its messages with the person's next prompt. A closed conversation runs one turn in the same session with `vibe --prompt-file … --resume <session>` (`vibe-run`), capped by vibe's own `--max-turns` and `--max-price` per run (`state/vibe-limits.json`: 20 turns, 0.50). `vibe:new` and `vibe:new@<name>` start a new conversation with `vibe -p`. Limits: the relay relies on vibe's app-server protocol and launcher, which vibe does not document, so a vibe update can break it; `vibe-diaulos` then opens vibe without the relay and says so, and messages arrive with the person's next prompt. `/branch` makes a copy with its own address, a closed conversation, while the TUI keeps its own. `vibe:new` opens no TUI window: the new conversation runs in the background and can be opened with `vibe --resume`. A conversation that ends before its first saved message is not listed. A `vibe-run` that finds the session open in a TUI (`Session is already open`) leaves the message waiting for that TUI. Tested for real on Linux with vibe 3.1.2 (gates R1 to R9, `docs/VIBE-DESIGN.md` §8.9.11); `vibe-run` and `vibe:new` ran against a stand-in `vibe` only. Not tried on Windows. Design: `docs/VIBE-DESIGN.md` §8.
- **vibe 2.20 is no longer supported.** The first version of the vibe part, built for vibe 2.20 (a tool file in `~/.vibe/tools/`, delivery through vibe's TUI internals, closed conversations through `vibe-acp`), was dropped when vibe 3 replaced that TUI. The installer removes the old `~/.vibe/tools/agent_bridge.py` when it is ours and puts back a file of yours that it had saved; below vibe 3 it says "Diaulos needs Mistral Vibe 3 or later" and installs nothing for vibe.
- **Other machines.** `remote:<machine>/<address>` reaches a session on a machine paired with this one, on any network, through a relay (ntfy.sh by default, or `remote relay-server`). A person pairs the two machines once, at a terminal, with `remote invite` and `remote join`: a one-time code, an X25519 exchange and a 6-digit code compared on both sides. Messages are end-to-end encrypted with ChaCha20-Poly1305, with one key and one relay topic per direction. Each machine has an allow list, replies reach the session that wrote first, and messages are rate-limited and never hop to a third machine. The gateway starts with agy and Claude Code sessions, on the first send or with `remote start`, and the new `diaulos-remote` skill explains how and gives the security rules. Needs the `cryptography` package; the rest of Diaulos stays standard library only. Tested for real between two machines on different networks through ntfy.sh. Journal §21.
- **State of the art** (`docs/ETAT-DE-L-ART.md`, French): Diaulos compared with MCP mailboxes, tmux orchestrators, Claude Code agent teams and the A2A, ACP and ANP protocols.

### Changed

- **README rewritten** for users, in both languages: what Diaulos does, an illustrated demo, a comparison with other approaches, then installation, usage, other machines, security and troubleshooting. Implementation details moved to `docs/INTERNALS.md`; `docs/images/` holds the demo GIF (rendered by `make_demo.py`) and two diagrams.
- `bridge.py remote gateway` takes over from a gateway already running, so a systemd unit can own it.

### Fixed

- Windows: two readers could get the same message. A reader now takes a message only after creating a marker with `O_EXCL`; found by the new Windows CI job.

## [0.3.0] - 2026-10-28

### Added

- **Antigravity Desktop.** One relay per window serves all its conversations, and a conversation whose hook finds that relay joins it without `ensure-relay`. The relay registers the conversations the window has loaded, and Desktop's own conversations of the last 7 days from its index; delivery loads a conversation from disk. Journal §15–17.
- **`agy:new` and `agy:new@<title>`** start a new Desktop conversation with the message as its first prompt. `--workspace` (MCP: `workspace`) takes a directory or the name of a Desktop workspace, and the conversation is filed under that workspace. Linux. Journal §17–18.
- **Experimental `install.sh --auto-relay`** (Linux, off by default): listing or messaging agy sessions starts the relay of each Desktop window, with the token read from its language server's command line. Journal §15–16.
- **agy CLI conversations whose terminal is closed** stay reachable: a message runs one turn with `agy -p --conversation <id>` in the conversation's directory (`bridge.py cli-run`). Journal §19.
- **`agy:new-cli` and `agy:new-cli@<name>`** start a new agy CLI conversation with `agy -p`; the name becomes a bridge alias. Journal §19.
- **`list --days N` and `list --all`** (MCP: `days`, `all`) list agy conversations older than 7 days; an exact id or title reaches a conversation of any age. Journal §18.
- A test suite (standard library only, with a fake language server and a fake `agy`) and a GitHub Actions workflow on Linux and Windows.

### Changed

- The relay of Antigravity Desktop knows its language server's PID and stops as soon as the window closes, instead of polling `/healthz` for 120 s.
- A new conversation without a prompt borrows the config of the latest conversation of the window that has one.
- `agy` alone counts only the active agy sessions (an agy CLI that runs, a conversation loaded in a Desktop window), and `agy@<folder>` prefers a single active match: listing sleeping and older conversations no longer makes them ambiguous.
- `install.sh` rejects unknown options.
- README: contents, command reference, a section per agy client, internals moved apart.

## [0.2.0] - 2026-10-23

### Added

- **`ext:<name>` peers**: any program joins the bridge with `bridge.py watch ext:<name> --json`, is listed like an agent, and gets the replies sent to it. `watch` and `recv` take `--json`. Journal §14.
- Security warning about connecting outside sources to an agent.
- Compatibility with [Paros](https://github.com/Dr1mS/Paros), which shows the messages as letters between pets.

## [0.1.0] - 2026-10-16

### Added

- File mailbox in `~/.agent-bridge`, shared by any number of agy and Claude Code sessions, with at-least-once delivery.
- agy side: global hooks (`SessionStart`, `PreInvocation`, listening `Stop`) and the relay that wakes an idle agy through its language server.
- Claude Code side: MCP channel server with the `send` and `list_agents` tools, and the opt-in `--shell` wrapper so a plain `claude` starts with the channel.
- Addressing by conversation id, title (`/rename`), folder and session name, following renames on both sides.
- Windows support.
- Fixes from a cross audit with agy. Journal §13.

[Unreleased]: https://github.com/Dr1mS/Diaulos/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/Dr1mS/Diaulos/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Dr1mS/Diaulos/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Dr1mS/Diaulos/releases/tag/v0.1.0
