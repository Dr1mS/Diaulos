# vibe-acp probe (throwaway prototype, T2 follow-up)

One real probe of `vibe-acp` (Mistral Vibe 2.20.0, Linux, 2026-11-12), asked by the Architect (A1 to A5) to see whether ACP can resume a closed vibe session in the same session, where `vibe -p --resume` forks (see `../vibe/README.md`, section 4).

## Run

`./run_probe.sh` (full output: `out-run.txt`; every JSON-RPC frame: `out/run/acp-new.jsonl`, `out/run/acp-load.jsonl`).

- Throwaway `VIBE_HOME` = `out/run/vibe_home`: copy of `~/.vibe/config.toml`, `save_dir` moved into the run, the probe MCP server `../vibe/probe_mcp.py` declared as `probe` in `mcp_servers`, no `[tools.*]` permission. API key from the OS keyring.
- `acp_probe.py` is the ACP client (standard library, newline-delimited JSON-RPC on the agent's stdio). It answers `session/request_permission` with the option of kind `allow_once`.
- Step 1, process 1: `initialize`, `session/new`, `session/set_config_option` `max_turns=3` and `max_tokens=40000`, prompt `Reply with the single word OK1.`, stdin closed.
- Step 2: title written into `meta.json` (`acp-probe-title`, `title_source: manual`), as `/rename` does.
- Step 3, process 2: `initialize`, `session/load <id>`, same caps, prompt `Call the tool probe_ping exactly once, then answer with the single word OK2.`
- Both `session/new` and `session/load` also passed one extra server in `mcpServers` (`extra`, same probe server, own log).

Outputs cleaned before commit with `python3 ../vibe/sanitize_out.py out out-run.txt` (home path, `username`, `client_key`, env values, launcher command lines) and checked with `../vibe/check_anon.sh prototypes/vibe-acp` from the repository.

## Results

**A1, same session.** `session/load` kept the id (`ca66e9f6-...`) and the folder (`session_20261112_131947_ca66e9f6`): only one folder exists after both steps. `messages.jsonl` went from 2 to 6 lines (OK1, then the step 3 user message, tool call, tool result, OK2). The manual title and `title_source: manual` were kept.

**A2, permissions.** With no `[tools.probe_ping]` setting, process 2 sent `session/request_permission` (`toolCall.toolCallId` only) with options `allow_once`, `allow_always`, `allow_always_permanent` (kind `allow_always`), `reject_once`. Answered `allow_once`: the tool ran (`out/run/config-server.jsonl`: 1 `tools/call`; tool result in the transcript). The request does not name the tool; the name came 4 ms before, in two `session/update` frames `sessionUpdate: tool_call` with the same `toolCallId`, `title: "probe_ping"` and `_meta.tool_name: "probe_ping"` (`out/run/acp-load.jsonl`).

**A3, MCP servers.** Only `mcp_servers` from `config.toml` is used. The `mcpServers` passed in `session/new` and `session/load` was ignored: the `extra` server never started (no `extra.jsonl`). The `probe` server from the config started 3 times: one listing in process 1, then one listing and one call process in process 2. In the code, `new_session` and `load_session` take `mcp_servers` and never read it (`acp/acp_agent_loop.py:942-976`, `1228-1263`).

**A4, cost cap.** No price cap in ACP: `max_price` appears nowhere under `vibe/acp/`; the price limit is set only through `run_programmatic` (`core/programmatic.py:32`, `cli/cli.py:299`). `session/set_config_option` accepts `max_turns` and `max_tokens` (`acp/acp_agent_loop.py:1348-1361`), both answered without error:
- `max_turns` rebuilds the middleware (`core/agent_loop/_loop.py:1093-1095`), so the turn limit applies.
- `max_tokens` only sets `self._max_tokens` (`_loop.py:1097-1098`), the per-completion output limit passed to the model (`_loop.py:2012`). It does not cap the session: the session limit is `_max_session_tokens` (`_loop.py:1110`), which ACP cannot set.

Real cost (`meta.json` `stats.session_cost`, Mistral prices 1.5 / 7.5 $ per million): step 1 $0.0094 (6,149 tokens); step 3 $0.0194 (12,559 tokens). After `session/load`, `stats` counts only the new process. Total: $0.029.

**A5, launch.** Binary: `~/.local/share/uv/tools/mistral-vibe/bin/vibe-acp` (also on `PATH` as `vibe-acp` when uv's bin is there). Options: `-v` and `--setup` only (`acp/entrypoint.py:32-39`); no working-directory option. The working directory is the `cwd` field of `session/new` / `session/load`, and vibe-acp does `os.chdir(cwd)` on it (`acp_agent_loop.py:951`, `1238`). The probe also started the process with that cwd.

## Not probed

- `session/load` on a session open in a TUI: excluded by the design (the live plugin serves that session).
- A turn limit actually reached, and a refusal (`reject_once`).
