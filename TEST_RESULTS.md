# End-to-End Smoke Test Results

Date: 2026-05-09

Target service:

- `http://127.0.0.1:5055/v1/responses`

Validated models:

- `deepseek-v4-pro`
- `mimo-v2.5-pro`

Results summary:

- DeepSeek returned the expected final text `deepseek-ok` through the local Responses proxy.
- Xiaomi returned the expected final text `xiaomi-ok` through the local Responses proxy.
- Both providers completed with a valid `response.completed` event.
- Both providers produced token usage payloads in the completed response.
- DeepSeek completed a two-turn function-calling flow and returned `resolved-via-tool` after receiving the tool output.
- Xiaomi completed a two-turn function-calling flow and returned `resolved-via-tool` after receiving the tool output.

Observed behavior differences:

- DeepSeek returned a short plain text response with no visible reasoning payload in the final item.
- Xiaomi returned a valid final text response and also included `reasoning_content` in the completed item.

Reusable command:

```bash
cd "/Volumes/Ex/ai_workspace/Codex Project workplace/New Codex Switcher"
source .venv/bin/activate
python "New Codex_DS_Xiaomi_Switcher.py" check
```