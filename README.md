# New Codex Switcher

This is a first-party local proxy for Codex-style Responses requests. The first cut only targets two upstreams:

- DeepSeek (`deepseek-v4-pro`)
- Xiaomi (`mimo-v2.5-pro`)

It exposes a single local model catalog and translates `/responses` requests into upstream `chat/completions` streams.

## What this version already does

- Serves `/v1/models`
- Serves `/responses` and `/v1/responses`
- Maps Responses input into OpenAI-compatible chat messages
- Translates streaming text and function-call deltas back into Responses SSE events
- Applies DeepSeek-specific `thinking: {type: disabled}` by default

## What this version does not do yet

- No retry or circuit-breaker layer
- No per-provider health scoring
- No persistent logs or admin panel
- No MiniMax / Anthropic adapter

## Quick start

```bash
cd "/Volumes/Ex/ai_workspace/Codex Project workplace/New Codex Switcher"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
cp config.local.example.yaml config.local.yaml
codex-switcher serve
```

## Start 5055

The daily startup command for the main local switcher process is:

```bash
cd "/Volumes/Ex/ai_workspace/Codex Project workplace/New Codex Switcher"
/Users/apexmini/.local/bin/codex-switcher serve --port 5055
```

If you want to confirm that port `5055` is listening:

```bash
lsof -nP -iTCP:5055 -sTCP:LISTEN | cat
```

Codex Desktop uses `http://127.0.0.1:5055/v1` as the custom provider base URL, so if this process is not running, the DeepSeek/Xiaomi route will fail.

The local switcher automatically loads `/Volumes/Ex/ai_workspace/Codex Project workplace/New Codex Switcher/.env`.

Smoke tests:

```bash
cd "/Volumes/Ex/ai_workspace/Codex Project workplace/New Codex Switcher"
source .venv/bin/activate
codex-switcher check
```

Optional focused runs:

```bash
cd "/Volumes/Ex/ai_workspace/Codex Project workplace/New Codex Switcher"
source .venv/bin/activate
codex-switcher check --mode text
codex-switcher check --mode tool
```

Audit log proof:

```bash
cd "/Volumes/Ex/ai_workspace/Codex Project workplace/New Codex Switcher"
/Users/apexmini/.local/bin/codex-switcher audit-tail --lines 10
```

The audit log lives at `/Volumes/Ex/ai_workspace/Codex Project workplace/New Codex Switcher/runtime_logs/requests.jsonl`.
Each request records the selected model, upstream target, a short input preview, and a fingerprint so a Codex Desktop prompt can be matched back to the exact routed upstream request.

When `/Users/apexmini/.codex/config.toml` is currently set to `model_provider = "ds_xiaomi_switcher"`, internal Codex requests for `gpt-5.4-mini` are treated as an alias for the active routed model. For example, if the active model is `mimo-v2.5-pro`, those requests are routed to Xiaomi instead of being rejected as unknown.

## Endpoints

- `GET /health`
- `GET /v1/models`
- `POST /responses`
- `POST /v1/responses`

## Codex config sketch

The local provider can be pointed at this proxy with a config shaped like [codex.config.example.toml](codex.config.example.toml).

Use the proxy root `http://127.0.0.1:5055/v1` as the custom provider base URL. This project responds to both `/responses` and `/v1/responses`, so it is tolerant to either style.

For Codex Desktop, prefer the command-backed auth configuration in [codex.config.example.toml](codex.config.example.toml). That avoids relying on Finder-launched app environment variables.

The verified 1M setup also needs a custom model catalog such as [model-catalogs/ds-xiaomi-1m.example.json](model-catalogs/ds-xiaomi-1m.example.json). In the real Codex config, `model_catalog_json` must be an absolute path.

This setup also installs a direct launcher at `/Users/apexmini/.local/bin/codex-switcher`, so normal use does not need a `python` prefix.

To switch Codex Desktop between the built-in GPT route and the local DeepSeek/Xiaomi route, use:

```bash
codex-switcher use gpt
codex-switcher use deepseek
codex-switcher use xiaomi
codex-switcher use status
```

After running `use gpt`, `use deepseek`, or `use xiaomi`, fully quit and reopen `/Volumes/Ex/Applications/Codex.app`.

Starting in this revision, `codex-switcher use deepseek` and `codex-switcher use xiaomi` write all three settings that were required to make a brand new Codex Desktop thread show and use the 1M budget:

- `model_context_window = 1048576`
- `model_auto_compact_token_limit = 1000000`
- `model_catalog_json = "/absolute/path/to/model-catalogs/ds-xiaomi-1m.example.json"`

The script also leaves GPT mode clean by removing those switcher-specific overrides when you run `codex-switcher use gpt`.

## Why Codex Desktop showed 258K instead of 1M

The root cause was not the proxy itself. Codex Desktop was falling back to built-in model metadata for the UI and thread bootstrap path.

In the local Codex model cache, the built-in `gpt-5.4` entry reported:

- `context_window = 272000`
- `effective_context_window_percent = 95`

That yields `272000 * 0.95 = 258400`, which is exactly why the desktop UI showed about `258K`.

Changing only `model_context_window = 1048576` was not enough. The effective 1M result required two more conditions:

- raise the compaction threshold with `model_auto_compact_token_limit = 1000000`
- point Codex Desktop at a custom model catalog where `deepseek-v4-pro`, `mimo-v2.5-pro`, and `gpt-5.4` all advertise `context_window = 1048576`, `max_context_window = 1048576`, and `effective_context_window_percent = 100`

One more behavioral detail mattered: existing threads kept their old metadata. The 1M window appeared on a brand new Codex Desktop thread after the updated config and model catalog were in place.

## 1M checklist

1. Start the local proxy on `127.0.0.1:5055`.
2. Point Codex Desktop at the custom provider from [codex.config.example.toml](codex.config.example.toml).
3. Make `model_catalog_json` an absolute path to [model-catalogs/ds-xiaomi-1m.example.json](model-catalogs/ds-xiaomi-1m.example.json) or your own catalog derived from it.
4. Run `codex-switcher use deepseek` or `codex-switcher use xiaomi`.
5. Fully quit and reopen Codex Desktop.
6. Start a brand new thread and verify the context display is near `1M` instead of `258K`.

If you inspect `codex-switcher use status`, you should now see `model_context_window`, `model_auto_compact_token_limit`, and `model_catalog_json` together.

To prove a specific Codex Desktop reply used DeepSeek, switch to `deepseek`, reopen Codex Desktop, send a prompt with a unique marker such as `Reply with exactly: probe-20260509-abc123`, then run `codex-switcher audit-tail --lines 10` and confirm the newest entries show:

- `requested_model`: `deepseek-v4-pro`
- `provider_label`: `DeepSeek`
- `upstream_base_url`: `https://api.deepseek.com/v1`
- `input_preview` containing your unique marker

## Hooks

Codex hooks are configured in `/Volumes/Ex/ai_workspace/library_data/codex/.codex/hooks.json`.
The active Bash post-tool-use hook currently runs:

```bash
python3 /Users/apexmini/.codex/hooks/capture_tool.py
```

That hook writes Bash tool results into `/Users/apexmini/.openclaw/workspace/runtime/knowledge.db` for OpenClaw knowledge capture. It is independent from the DeepSeek/Xiaomi switcher and does not control provider routing.

Detailed behavior of the `capture_tool.py` hook

- Purpose: capture `bash` tool outputs invoked by Codex's PostToolUse hook, produce a safe summary/preview, and store it in a local OpenClaw SQLite knowledge DB for later retrieval. The hook is intended for local knowledge capture and debugging, not for forwarding secrets or modifying provider routing.
- Who calls it: Codex Desktop (via the configured hooks JSON) triggers PostToolUse hooks after a tool call completes. The hook receives JSON on stdin describing the tool invocation (tool name, command, cwd, tool_response).
- What it stores: the hook extracts the command and a truncated preview of stdout/stderr (up to a configured max length), generates a record with a unique id and timestamp, and inserts it into the `openclaw_memory` table and the `openclaw_memory_fts` index in `knowledge.db`.
- Where it stores it: by default `DB_PATH` in the hook is set to `/Users/apexmini/.openclaw/workspace/runtime/knowledge.db` (see the script for the exact path). The README does not copy outputs; instead the audit log (`runtime_logs/requests.jsonl`) records a reference id or summary so the two stores can be correlated.
- What it skips and why: the hook uses simple heuristics to skip noisy/short commands (e.g. `echo`, `ls`, `pwd`, very short commands) to avoid clutter; it also truncates long outputs. It does not attempt to capture or log environment secrets (it avoids reading `.env`) and will not write keys into the DB.
- Safety notes: the hook runs locally and is independent from upstream routing. By default it will not execute arbitrary code beyond what the tool call provided; enabling additional local execution hooks or broader command handling should be done only with explicit trust and tightened white-lists. If you want stricter masking of possible sensitive substrings (e.g. long base64/hex tokens), modify the hook to apply extra masking before writing.

How to disable or manage the hook

- Disable: remove or comment out the PostToolUse entry in `/Volumes/Ex/ai_workspace/library_data/codex/.codex/hooks.json` or move `capture_tool.py` out of the configured path.
- Tune: edit `capture_tool.py`'s `SKIP_PREFIXES`, `MIN_COMMAND_LEN`, and `MAX_OUTPUT_LEN` constants to change noise filtering and truncation behavior.
- Access control: ensure `knowledge.db` is stored in a directory with restricted filesystem permissions if audit outputs are to be protected.


## Notes

`DASHSCOPE_API_KEY` is unrelated to this switcher. The switcher only needs `DEEPSEEK_API_KEY` and `XIAOMI_API_KEY`, and only the local proxy reads them from `.env`.