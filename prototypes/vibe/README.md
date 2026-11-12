# Vibe probes (throwaway prototype, T2)

Real probes on Mistral Vibe 2.20.0 (Linux, 2026-11-12), to feed `docs/VIBE-DESIGN.md`. Nothing here is product code. Every run uses a throwaway `VIBE_HOME` under `out/<run>/vibe_home`; `~/.vibe` is never written (one exception, fixed: see "Traps").

## Tools

| File | What |
|---|---|
| `setup_home.sh <run> [push]` | Builds `out/<run>/` with a throwaway `VIBE_HOME`: a copy of `~/.vibe/config.toml` with the probe MCP server in `mcp_servers` and `save_dir` moved into the run, `hooks.toml` with the probe hook on all three hook types, and the probe plugin in `tools/`. Source it. The API key stays in the OS keyring, which vibe reads by variable name. |
| `probe_mcp.py` | MCP stdio server. Logs its start (pid, parent chain, cwd, env), every message from vibe, and, with `push`, sends a log notification, `tools/list_changed`, `notifications/claude/channel` and a `sampling/createMessage` request 2 s after `initialized`. Tool: `ping`. |
| `probe_hook.py` | Hook. Logs its stdin, parents, env. On `post_agent_turn`, waits `$PROBE_LISTEN` s for `out/<run>/inject`, then answers `{"decision":"deny","reason":<text>}`. |
| `tools/agent_bridge_probe.py` | "Tool" file loaded in the vibe process from `$VIBE_HOME/tools/`. No `BaseTool` class. At import it logs, then a thread finds the `VibeApp` instance and delivers `out/<run>/inject.txt`: `app._handle_user_message(text)` if the agent is idle and the input is showing (same test as vibe's `/loop` runner), else `app._enqueue_prompt_with_resources(text)`. |
| `pty_drive.py <dir> <s> -- vibe ...` | Runs the interactive TUI in a pseudo-terminal with no human; types the bytes of `<dir>/keys`. |
| `screen.py <dir>` | Renders what the TUI shows now (needs `pyte`, in a venv). |
| `measure_inject.py <dir> <token> [timeout] [inject\|inject.txt]` | Drops one message and times: claimed, delivered, reply written to the transcript. |
| `s5_resume.sh <dir>` | One `vibe -p --resume <id> --max-price 0.10` turn on a closed session; lists the session folders before and after. |
| `s6_permission.sh` | Section 6: the two `-p` runs, without then with a tool permission. |
| `sanitize_out.py [dir-or-file ...]` | Before commit: the account's home path becomes `/home/<compte>`, `username` becomes `<compte>`, `client_key` becomes `<masqué>`; env dumps reduced to names (values of `VIBE_*`/`PROBE_*` kept), parent chains cut above vibe. |
| `check_anon.sh <dir> ...` | Counts, per category, what would leak in the committed files (never prints the values). |

Every programmatic call used `--max-price 0.10`. Raw terminal captures and copied configs are not committed (`.gitignore`).

## Runs and results

### 1. MCP server (`out/p1-mcp`, `out/p2-idle`)

`source setup_home.sh p1-mcp; cd $WORK; vibe -p "Call the tool probe_ping exactly once, then answer with the single word DONE." --max-price 0.10 --trust --auto-approve --output json`

- Loaded from `mcp_servers` (stdio). Tool name seen by the model: `probe_ping` (`<server>_<tool>`).
- Two server processes per vibe process: the first answers `tools/list` and its stdin closes at once; the second starts at the first `tools/call` and lives until vibe exits (`probe.jsonl`: pid 208773 then 208818). Same in the TUI (p2: 213363 then 213696). No call, no long-lived server.
- Parent: the vibe Python process (`.../mistral-vibe/bin/python .../bin/vibe`). cwd: the vibe working directory.
- Environment: only the MCP SDK default (`HOME LC_CTYPE LOGNAME PATH SHELL TERM USER`, plus `SUBPROCESS_BAGGAGE`, `SUBPROCESS_SENTRY_TRACE`) and the `env` of the config. No `VIBE_HOME`, no session id.
- `initialize` capabilities: `{}` for the listing process, `{"sampling": {}}` for the call process.

### 2. Waking an idle vibe

**From the MCP server: no.** With `push` (`out/p2-idle`), vibe ignored `notifications/message`, `tools/list_changed` and `notifications/claude/channel`; no turn started. It answered `sampling/createMessage` (empty text) outside the conversation; no turn either. Code: `ClientSession` is built without a message handler (`core/tools/mcp/tools.py:339`).

**Hook `post_agent_turn` that listens (`out/p2-idle`): works, with hard limits.** `source setup_home.sh p2-idle push; PROBE_LISTEN=600 python3 pty_drive.py $PROBE_DIR 900 -- vibe --trust --auto-approve --workdir $WORK "<prompt>"`, then `measure_inject.py out/p2-idle PONG<n> 60`.
- 2 passes recorded (`measure.jsonl`, PONG2 and PONG3): file to hook 0.16 s and 0.13 s; file to reply written 1.11 s and 1.16 s. PONG1 also got its reply, but its timing was not recorded.
- The message goes in as a user message flagged `injected: true`, and is not shown on screen; the screen shows `✗ [probe-post] Failed, retrying (n retries remaining)` and the reply (`screen-final.txt`).
- Three injections at most: the fourth (PONG4) is claimed and then lost, `✗ [probe-post] Failed, retries exhausted (3/3)`, and no hook runs again until a human turn. The counter resets on a human message (`_loop.py:1281`) or when the same hook answers allow (`_handler.py`, `track_no_retry`).
- A message typed while the hook listens is not sent (single observation). In `out/p2-idle/probe.jsonl`, the last `post_agent_turn` hook starts and then has neither an `inject` nor a `listen-timeout` record: it never ended on its own. `screen-final.txt` (lines 38, 40, 42) shows the end state, after HUMAN2 was typed and then Esc was pressed: `⎣ Interrupted · What should Vibe do instead?`, `» Queued — press Enter to send, type to add`, and `> Reply with the single word HUMAN2.` as the queued message. The transcript has no HUMAN2. The key presses themselves (HUMAN2 and Enter, then Esc, sent by `pty_drive.py`) are not logged, and the screen during the wait was not captured.

**Plugin in `$VIBE_HOME/tools/` (`out/p3-plugin`): works, no limit seen.** `source setup_home.sh p3-plugin; PROBE_LISTEN=0 python3 pty_drive.py $PROBE_DIR 1500 -- vibe --trust --auto-approve --workdir $WORK "Answer with the single word READY."`, then `measure_inject.py out/p3-plugin KIWI<n> 60 inject.txt`.
- Idle, 4 passes (`measure-idle.jsonl`): file to delivery 0.03 to 0.09 s; file to reply written 0.91, 1.01, 1.06, 1.41 s. Path `handle_user_message`, no human key press.
- The message is an ordinary user message (`injected: false`) and is shown on screen as one (`> Message from probe: ...`).
- Import: once per vibe process, TUI (`tui_loaded: true`, `VibeApp` found 0.58 s after import) and `-p` (`tui_loaded: false`) (`plugin.jsonl`, `out/p4-perm/plugin.jsonl`). Module name is derived from the file path.

### 3. Hooks and delivery during a turn

- Hooks exist, experimental: `enable_experimental_hooks = true` or `VIBE_ENABLE_EXPERIMENTAL_HOOKS=true`; files `<project>/.vibe/hooks.toml` (trusted folder) then `$VIBE_HOME/hooks.toml`. Types: `post_agent_turn`, `before_tool`, `after_tool`, and no other: the hook type enum has exactly these three members (`core/hooks/models.py:20-23`, `class HookType`), so there is no hook before a model call and no session-start hook. All three ran in `-p` and in the TUI.
- Hook stdin: `session_id`, `transcript_path`, `cwd`, `parent_session_id`, `hook_event_name` (+ tool fields). Hook env: the full vibe env (it includes `CLAUDE_CODE_SESSION_ID` when vibe is started from a Claude session). The hook's `/bin/sh` parent is the vibe process.
- `after_tool` can return `hook_specific_output.additional_context` (code: `models.py`); not probed live.
- Plugin, message during a turn (single observation, `measure-busy.jsonl`, LIME1 during `sleep 20`): path `enqueue_prompt`, sent by vibe's own queue at the end of the turn, reply at 18.7 s.
- After Esc during a turn (single observation each for LIME2 and LIME3; `screen-esc.txt`, `measure-paused.jsonl`): the queue pauses; the message stays visible under `» Queued — press Enter to send, type to add`, not sent. A message delivered next while idle (LIME3) goes through `handle_user_message` and is answered before LIME2 (`screen-paused-then-inject.txt`): order inverted. LIME2 was sent only when Enter was pressed later, together with the garbled `/rename` text of section 5 (transcript lines 24 and 25, one reply).
- `@path` in an injected text is inlined: `See @README.md` became `See README.md` followed by the file's content (`ZEBRA-4417`) in the transcript (`out/p3-plugin` session).

### 4. `vibe -p --resume` on a closed session (`out/p3-plugin/s5.txt`)

`./s5_resume.sh out/p3-plugin` → `vibe -p "Reply with the single word RESUMED1." --resume <id> --max-price 0.10 --trust --workdir <work> --output text`

- Answer `RESUMED1`, exit 0. It does not append: vibe wrote a new folder with a new id (`54ff6bf8-...`, 28 messages = the 26 copied + 2), `parent_session_id` null, title back to `auto` from the first message. The original (`71861a26-...`, title `probe-s8`) is unchanged.
- Sessions: `<save_dir>/session_<YYYYMMDD_HHMMSS>_<first 8 hex of id>/{meta.json,messages.jsonl}`; `save_dir` defaults to `$VIBE_HOME/logs/session`. `messages.jsonl` is written at the end of each turn. No separate index: `meta.json` carries `session_id`, `title`, `title_source`, `environment.working_directory`, `start_time`, `end_time`.
- Code only, not run: interactive `--resume <id>` keeps the id and folder (`cli/cli.py:211-221`, `resume_existing_session`). ACP `session/load` keeps them too, probed for real in `../vibe-acp/README.md`.

### 5. New session with a first prompt and a name

- `vibe [options] "<prompt>"` starts the TUI and runs the prompt as the first turn (runs p2 and p3).
- No option names or titles a session: in `vibe --help` (`out/vibe-help.txt`), the only `NAME` arguments are `--agent NAME` (agent profile) and `--worktree NAME` (git branch), and "title" does not appear.
- `/rename probe-s8` in the TUI: the final `meta.json` of the session has `title: "probe-s8"`, `title_source: "manual"` (code: `saved_sessions.py:66` writes both). Only the final state is recorded, not the moment of the write. The first attempt to type the command was garbled by the pty driver: the transcript holds a user message `//rename probe-s8rename probe-s8` sent to the model (`out/p3-plugin`, session `71861a26`, line 25); the next attempt, typed one key at a time, renamed the session. The automatic title is the first prompt, cut (`title_source: "auto"`).

### 6. Tool permission in `-p` (`out/p4-perm/s6.txt`)

`./s6_permission.sh` (the exact commands of the run: `vibe -p "Call the tool probe_ping exactly once, then answer with the single word DONE." --max-price 0.10 --trust --output text`, twice, with the setting below appended to the throwaway `config.toml` between the two).

- Without a setting: the MCP call returns `Tool execution not permitted.`, the server sees no `tools/call`.
- With these two lines appended to `config.toml`, the call runs:

```toml
[tools.probe_ping]
permission = "always"
```

## Traps

- The copied `config.toml` has an absolute `save_dir` (`~/.vibe/logs/session`). The first run (p1) wrote its session there; it was moved into `out/p1-mcp/` within the minute and `setup_home.sh` now rewrites `save_dir`.
- Typing `/` in the TUI switches the input to command mode; the screen shows `/ rename ...`. Typing a whole command in one write through the pty garbled it (see section 5); type it key by key, with pauses.
