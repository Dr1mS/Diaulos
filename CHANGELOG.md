# Changelog

All notable changes to Diaulos. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/). The lab notebook, [docs/JOURNAL.md](docs/JOURNAL.md) (in French), tells how and why each change was made.

## [Unreleased]

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
