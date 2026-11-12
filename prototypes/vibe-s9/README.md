# S9 probe: a BaseTool with permission ALWAYS (throwaway prototype)

Real probe on Mistral Vibe 2.20.0 (Linux, 2026-11-12), asked by the Architect (B1 to B3) for `docs/VIBE-DESIGN.md`. Every run uses a throwaway `VIBE_HOME` under `out/<run>/vibe_home`; `~/.vibe` is never written.

## Tools

| File | What |
|---|---|
| `tools/bridge_probe_tool.py` | B1. Real `BaseTool` subclass `BridgeEcho` (args: `text`), config `BridgeEchoConfig(BaseToolConfig)` with `permission = ToolPermission.ALWAYS` by default. `run()` returns `text`, `ctx.session_id` and `os.getpid()`, and logs each import and call to `$S9_LOG` (`out/<run>/tool.jsonl`). The tool name seen by the model is `bridge_echo` (`BaseTool.get_name`, snake case of the class name). |
| `setup_home.sh <run>` | Builds `out/<run>/` with a throwaway `VIBE_HOME`: a copy of `~/.vibe/config.toml` with `save_dir` moved into the run, and the tool copied into `vibe_home/tools/`. No MCP server, no hook, no `[tools.bridge_echo]` setting (the script checks it). |
| `run_s9.sh` | B2 (b) and (c): the exact commands of runs `p-mode` and `acp-mode`. |
| `run_tui.sh` | B2 (a) and B3: the steps of run `tui-mode`, written as one script after the run. The run itself was driven step by step with the same commands; the script was checked with `bash -n` and not run again. |

Outputs cleaned with `python3 ../vibe/sanitize_out.py out` and checked with `../vibe/check_anon.sh prototypes/vibe-s9` from the repository.

## Results

### B2. Seen by the model and run without an approval request

| Mode | Command | Tool log (`tool.jsonl`) | Approval |
|---|---|---|---|
| (b) `vibe -p`, no `--auto-approve` | `vibe -p "Call the tool bridge_echo once with text S9P, then reply with its session_id only." --max-price 0.10 --trust --output text` | `run`, `text: S9P`, `session_id: c7a20d11-7bcf-f7ae-c2da-6ce49b6f1fd9`, `permission: always` | none needed: the answer is the session id (`out/p-mode/run.txt`; the exit code was not recorded); `meta.json` `tool_calls_rejected: 0`. For contrast, an MCP tool with no setting is refused in `-p` (`../vibe/README.md`, section 6). |
| (c) vibe-acp | `session/new`, then `session/prompt` with the same kind of prompt (text `S9A`), through `../vibe-acp/acp_probe.py` | `run`, `text: S9A`, `session_id: b73a5854-5e4b-3d9c-7839-eb77fb8ffa78` | `session/request_permission` received: **0** (`grep -c request_permission out/acp-mode/acp-new.jsonl`); summary `permission_requests: []` (`out/acp-mode/run.txt`). |
| (a) TUI, no `--auto-approve` | `vibe --trust --workdir <work> "Call the tool bridge_echo once with text S9T1, then reply with its session_id only."` in `../vibe/pty_drive.py` | `run`, `text: S9T1`, `session_id: 54d77329-81e4-ee13-45a0-33fbfd20c13e`, 3.6 s after import | `meta.json` `tool_calls_rejected: 0`. The screen, captured only after the reply, shows no dialog: `screen-1-call.txt` shows the prompt (line 32), `✓ Success` (line 37), then the id. The driver wrote no key before the reply (its first keys are `/clear`); key presses are not logged. |

In all three modes:
- `ctx.session_id` equals the `session_id` of the session's `meta.json`, and `ctx.session_dir` is that session's folder (`tool.jsonl` against `meta.json`).
- `meta.json` stats: `tool_calls_agreed` 1, `tool_calls_rejected` 0 for each call.
- The tool is imported once per vibe process (`event: import`, one line per run; in the TUI, the three calls share pid 288419).
- The code path: `ctx` is built in `core/agent_loop/_loop.py:1767-1785` with `session_id=self.session_id`; the default config comes from the tool's own config class (`core/tools/manager.py:565-586`, `_get_tool_config_class`, `core/tools/base.py:228`).

### B3. Session id after `/clear` and `/compact` (TUI, run `tui-mode`)

Steps: call (S9T1), `/clear`, call (S9T2), `/compact`, call (S9T3). Each slash command typed one key at a time.

| Step | `ctx.session_id` | Folder | `parent_session_id` | Messages |
|---|---|---|---|---|
| 1. first call | `54d77329-81e4-ee13-45a0-33fbfd20c13e` | `session_20261112_132953_54d77329` | null | 4 |
| 2. after `/clear` | `801f74e7-28e4-2a71-c2cb-33fbfd20c13e` | `session_20261112_133012_801f74e7` (new) | null | 4 |
| 3. after `/compact` | `9e339932-2fc5-4d38-2168-33fbfd20c13e` | `session_20261112_133050_9e339932` (new) | `801f74e7-28e4-2a71-c2cb-33fbfd20c13e` | 5 |

- Both commands make a new session id and a new folder; the old folders stay as they were.
- The last 12 hex digits stay the same across both (`33fbfd20c13e`): confirmed, as in `core/session/session_id.py:8-19` (`generate_session_id(suffix=...)`) called from `_reset_session` (`_loop.py:2212-2224`).
- `/clear` does not link the new session to the old one (`_reset_session(keep_parent=False)`, `_loop.py:2307`); `/compact` does (`parent_session_id` = the session before).
- After `/clear`, the screen shows `Conversation history cleared!` (`out/tui-mode/screen-2-clear.txt`). A folder listing taken then (single observation, output not saved) still had only the first folder: the new folder is written at the end of the next turn.
- `/compact` shows the change on screen: `session: 801f74e7 (before compaction) → 9e339932 (after compaction)` (`out/tui-mode/screen-4-compact.txt`). The new session's first user message is the summary (title `You are continuing a trajectory after a context co…`). Its stats are carried over from the session before (`tool_calls_agreed: 2`).

Consequence for a plugin that keys on the session id: it must expect the id to change inside one TUI process after `/clear` and `/compact`. The stable suffix, or the `parent_session_id` after `/compact`, ties the sessions together; nothing ties them after `/clear` except the suffix.

## Cost

`meta.json` `stats.session_cost`: `-p` $0.0204; vibe-acp $0.0198; TUI sessions $0.0217, $0.0221 and $0.0657.

- The third TUI session carries over the stats of the second one after `/compact` (`steps` 6 against 3, `session_prompt_tokens` 34,792 against 13,622). Its $0.0657 = the second session's $0.0221 + $0.0437 for the compaction and the S9T3 call together; these two cannot be told apart from these files.
- So the TUI cost is $0.0217 + $0.0657 = $0.0874, and the S9 total is $0.0204 + $0.0198 + $0.0874 = **$0.128** (not the sum of the five numbers).
- Only `-p` has a price cap (`--max-price 0.10`). vibe-acp has none (see `../vibe-acp/README.md`, A4), and the TUI has no price option.

## Not probed

- `/rewind` and fork (same `_reset_session` and suffix in the code, `_loop.py:2241`, `core/rewind/manager.py:166`).
- vibe-acp `session/load` with the tool, and a refusal.
