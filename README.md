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

This setup also installs a direct launcher at `/Users/apexmini/.local/bin/codex-switcher`, so normal use does not need a `python` prefix.

To switch Codex Desktop between the built-in GPT route and the local DeepSeek/Xiaomi route, use:

```bash
codex-switcher use gpt
codex-switcher use deepseek
codex-switcher use xiaomi
codex-switcher use status
```

After running `use gpt`, `use deepseek`, or `use xiaomi`, fully quit and reopen `/Volumes/Ex/Applications/Codex.app`.

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

## Notes

`DASHSCOPE_API_KEY` is unrelated to this switcher. The switcher only needs `DEEPSEEK_API_KEY` and `XIAOMI_API_KEY`, and only the local proxy reads them from `.env`.