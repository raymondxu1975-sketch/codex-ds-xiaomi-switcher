#!/Volumes/Ex/ai_workspace/Codex Project workplace/New Codex Switcher/.venv/bin/python
import argparse
import copy
import hashlib
import json
import math
import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
import yaml
from flask import Flask, Response, jsonify, request


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
CODEX_CONFIG_PATH = os.environ.get("CODEX_CONFIG_PATH", os.path.expanduser("~/.codex/config.toml"))
DEFAULT_CODEX_MODEL_CATALOG_PATH = os.environ.get(
    "CODEX_MODEL_CATALOG_PATH",
    os.path.join(BASE_DIR, "model-catalogs", "ds-xiaomi-1m.example.json"),
)
AUDIT_LOG_PATH = os.environ.get(
    "SWITCHER_AUDIT_LOG_PATH",
    os.path.join(BASE_DIR, "runtime_logs", "requests.jsonl"),
)
DEFAULT_CONFIG_PATH = os.environ.get(
    "SWITCHER_CONFIG",
    os.path.join(BASE_DIR, "config.local.yaml"),
)

TEXT_PROBES: List[Tuple[str, str]] = [
    ("deepseek-v4-pro", "deepseek-ok"),
    ("mimo-v2.5-pro", "xiaomi-ok"),
]
TOOL_PROBES: List[str] = ["deepseek-v4-pro", "mimo-v2.5-pro"]
TOOL_NAME = "lookup_status"
INITIAL_PROMPT = "Call the lookup_status tool with code='alpha'. Do not answer directly."
FOLLOWUP_PROMPT = "Now answer with exactly the status value returned by the tool."
TOOL_RESULT = {"code": "alpha", "status": "resolved-via-tool"}
ACTIVE_MODEL_ALIASES = {"gpt-5.4-mini"}


def load_dotenv() -> None:
    if not os.path.exists(ENV_PATH):
        return
    with open(ENV_PATH, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


load_dotenv()


@dataclass
class ProviderSpec:
    provider_id: str
    label: str
    base_url: str
    models: List[str]
    api_key: str = ""
    api_key_env: str = ""
    timeout: int = 120
    request_defaults: Dict[str, Any] = field(default_factory=dict)
    extra_headers: Dict[str, str] = field(default_factory=dict)
    stream_options: Optional[Dict[str, Any]] = None

    @property
    def chat_url(self) -> str:
        trimmed = self.base_url.rstrip("/")
        if trimmed.endswith("/chat/completions"):
            return trimmed
        return f"{trimmed}/chat/completions"

    def resolved_api_key(self) -> str:
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            return os.environ.get(self.api_key_env, "").strip()
        return ""


app = Flask(__name__)


def default_config() -> Dict[str, Any]:
    return {
        "server": {"host": "127.0.0.1", "port": 5055},
        "providers": {
            "deepseek": {
                "label": "DeepSeek",
                "base_url": "https://api.deepseek.com/v1",
                "api_key_env": "DEEPSEEK_API_KEY",
                "models": ["deepseek-v4-pro"],
                "timeout": 180,
                "request_defaults": {"thinking": {"type": "disabled"}},
                "stream_options": {"include_usage": True},
            },
            "xiaomi": {
                "label": "Xiaomi",
                "base_url": "https://token-plan-sgp.xiaomimimo.com/v1",
                "api_key_env": "XIAOMI_API_KEY",
                "models": ["mimo-v2.5-pro"],
                "timeout": 180,
            },
        },
    }


def load_raw_config() -> Dict[str, Any]:
    config = default_config()
    if not os.path.exists(DEFAULT_CONFIG_PATH):
        return config

    with open(DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as handle:
        file_config = yaml.safe_load(handle) or {}

    for top_level_key, value in file_config.items():
        if isinstance(value, dict) and isinstance(config.get(top_level_key), dict):
            merged = copy.deepcopy(config[top_level_key])
            merged.update(value)
            config[top_level_key] = merged
        else:
            config[top_level_key] = value
    return config


def build_registry() -> Tuple[Dict[str, ProviderSpec], Dict[str, ProviderSpec], Dict[str, Any]]:
    raw_config = load_raw_config()
    providers: Dict[str, ProviderSpec] = {}
    model_index: Dict[str, ProviderSpec] = {}

    for provider_id, payload in (raw_config.get("providers") or {}).items():
        spec = ProviderSpec(
            provider_id=provider_id,
            label=payload.get("label", provider_id),
            base_url=payload.get("base_url", "").strip(),
            models=list(payload.get("models") or []),
            api_key=(payload.get("api_key") or "").strip(),
            api_key_env=(payload.get("api_key_env") or "").strip(),
            timeout=int(payload.get("timeout") or 120),
            request_defaults=copy.deepcopy(payload.get("request_defaults") or {}),
            extra_headers=copy.deepcopy(payload.get("headers") or {}),
            stream_options=copy.deepcopy(payload.get("stream_options")),
        )
        if not spec.base_url or not spec.models:
            continue
        providers[provider_id] = spec
        for model in spec.models:
            model_index[model] = spec

    return providers, model_index, raw_config


def load_codex_config() -> Dict[str, Any]:
    if not os.path.exists(CODEX_CONFIG_PATH):
        return {}
    with open(CODEX_CONFIG_PATH, "rb") as handle:
        return __import__("tomllib").load(handle)


def resolve_model_alias(requested_model: Optional[str], model_index: Dict[str, ProviderSpec]) -> Tuple[Optional[str], Optional[str]]:
    if not requested_model:
        return requested_model, None
    if requested_model in model_index:
        return requested_model, None
    if requested_model not in ACTIVE_MODEL_ALIASES:
        return requested_model, None

    codex_config = load_codex_config()
    active_model = codex_config.get("model")
    active_provider = codex_config.get("model_provider")
    if active_provider != "ds_xiaomi_switcher":
        return requested_model, None
    if active_model not in model_index:
        return requested_model, None
    return active_model, f"active-route:{active_model}"


def clean_schema(node: Any) -> Any:
    if isinstance(node, dict):
        cleaned: Dict[str, Any] = {}
        for key, value in node.items():
            if key in {"additionalProperties", "strict"}:
                continue
            cleaned[key] = clean_schema(value)
        return cleaned
    if isinstance(node, list):
        return [clean_schema(item) for item in node]
    return node


def convert_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    converted = []
    for tool in tools:
        if tool.get("type") != "function":
            continue
        function_payload: Dict[str, Any] = {
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
        }
        if "parameters" in tool:
            function_payload["parameters"] = clean_schema(tool["parameters"])
        converted.append({"type": "function", "function": function_payload})
    return converted


def convert_tool_choice(tool_choice: Any) -> Any:
    if tool_choice in (None, "auto"):
        return "auto"
    if isinstance(tool_choice, str):
        return tool_choice
    if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
        return {"type": "function", "function": {"name": tool_choice.get("name", "")}}
    return "auto"


def normalize_role(role: str) -> str:
    if role == "developer":
        return "system"
    return role


def collect_message_text(content: Any) -> Tuple[str, List[Dict[str, Any]]]:
    if isinstance(content, str):
        return content.strip(), []

    text_parts: List[str] = []
    tool_calls: List[Dict[str, Any]] = []
    if not isinstance(content, list):
        return "", []

    for part in content:
        if not isinstance(part, dict):
            continue
        part_type = part.get("type")
        if part_type in {"text", "input_text", "output_text"}:
            text = (part.get("text") or "").strip()
            if text:
                text_parts.append(text)
        elif part_type == "tool_call":
            tool_calls.append(
                {
                    "id": part.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": part.get("name", ""),
                        "arguments": part.get("arguments", ""),
                    },
                }
            )
    return "\n".join(text_parts), tool_calls


def reorder_tool_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    reordered: List[Dict[str, Any]] = []
    index = 0
    while index < len(messages):
        current = messages[index]
        if current.get("role") != "assistant" or not current.get("tool_calls"):
            reordered.append(current)
            index += 1
            continue

        expected_ids = {call.get("id") for call in current.get("tool_calls", []) if call.get("id")}
        prefix_messages: List[Dict[str, Any]] = []
        tool_messages: List[Dict[str, Any]] = []
        scan = index + 1

        while scan < len(messages) and expected_ids:
            candidate = messages[scan]
            if candidate.get("role") == "tool" and candidate.get("tool_call_id") in expected_ids:
                expected_ids.remove(candidate.get("tool_call_id"))
                tool_messages.append(candidate)
                scan += 1
                continue
            if candidate.get("role") in {"system", "developer"}:
                prefix_messages.append(candidate)
                scan += 1
                continue
            break

        reordered.extend(prefix_messages)
        reordered.append(current)
        reordered.extend(tool_messages)
        index = scan

    return reordered


def extract_messages(data: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Any]:
    tools = convert_tools(list(data.get("tools") or []))
    tool_choice = convert_tool_choice(data.get("tool_choice"))

    if "input" not in data:
        return list(data.get("messages") or []), tools, tool_choice

    input_payload = data.get("input")
    messages: List[Dict[str, Any]] = []
    instructions = data.get("instructions")
    if instructions:
        messages.append({"role": "system", "content": instructions})

    if isinstance(input_payload, str):
        if input_payload.strip():
            messages.append({"role": "user", "content": input_payload.strip()})
        return messages, tools, tool_choice

    if not isinstance(input_payload, list):
        return messages, tools, tool_choice

    pending_tool_calls: List[Dict[str, Any]] = []
    pending_reasoning = ""

    def flush_pending_tool_calls() -> None:
        nonlocal pending_tool_calls, pending_reasoning
        if not pending_tool_calls:
            return
        assistant_message: Dict[str, Any] = {"role": "assistant", "content": "", "tool_calls": pending_tool_calls}
        if pending_reasoning:
            assistant_message["reasoning_content"] = pending_reasoning
        messages.append(assistant_message)
        pending_tool_calls = []
        pending_reasoning = ""

    for item in input_payload:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "message":
            flush_pending_tool_calls()
            role = normalize_role(item.get("role", "user"))
            content_text, tool_calls = collect_message_text(item.get("content"))
            if tool_calls:
                message: Dict[str, Any] = {"role": role, "content": content_text, "tool_calls": tool_calls}
                if item.get("reasoning_content"):
                    message["reasoning_content"] = item["reasoning_content"]
                messages.append(message)
            elif content_text:
                message = {"role": role, "content": content_text}
                if item.get("reasoning_content"):
                    message["reasoning_content"] = item["reasoning_content"]
                messages.append(message)
            continue

        if item_type == "function_call":
            pending_tool_calls.append(
                {
                    "id": item.get("call_id", ""),
                    "type": "function",
                    "function": {
                        "name": item.get("name", ""),
                        "arguments": item.get("arguments", ""),
                    },
                }
            )
            if item.get("reasoning_content") and not pending_reasoning:
                pending_reasoning = item["reasoning_content"]
            continue

        if item_type == "function_call_output":
            flush_pending_tool_calls()
            messages.append({"role": "tool", "tool_call_id": item.get("call_id", ""), "content": item.get("output", "")})

    flush_pending_tool_calls()
    return reorder_tool_messages(messages), tools, tool_choice


def merge_request_defaults(payload: Dict[str, Any], defaults: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(payload)
    for key, value in defaults.items():
        if key not in merged:
            merged[key] = copy.deepcopy(value)
            continue
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            nested = copy.deepcopy(value)
            nested.update(merged[key])
            merged[key] = nested
    return merged


def make_usage(messages: List[Dict[str, Any]], full_text: str, tool_states: Dict[int, Dict[str, Any]], usage: Dict[str, Any]) -> Dict[str, int]:
    if usage:
        prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
        return {"input_tokens": prompt_tokens, "output_tokens": completion_tokens, "total_tokens": total_tokens}

    prompt_chars = len(json.dumps(messages, ensure_ascii=False))
    completion_chars = len(full_text) + sum(len(tool_state.get("arguments", "")) for tool_state in tool_states.values())
    prompt_tokens = max(1, math.ceil(prompt_chars / 4)) if prompt_chars else 0
    completion_tokens = max(1, math.ceil(completion_chars / 4)) if completion_chars else 0
    return {"input_tokens": prompt_tokens, "output_tokens": completion_tokens, "total_tokens": prompt_tokens + completion_tokens}


def upstream_headers(provider: ProviderSpec) -> Dict[str, str]:
    api_key = provider.resolved_api_key()
    if not api_key:
        raise ValueError(f"Provider '{provider.provider_id}' is missing an API key. Set {provider.api_key_env or 'api_key'} first.")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    headers.update(provider.extra_headers)
    return headers


def sse_event(event_name: str, payload: Dict[str, Any]) -> str:
    return f"event: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def append_audit_log(entry: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(AUDIT_LOG_PATH), exist_ok=True)
    with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def make_fingerprint(payload: Any) -> str:
    normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def make_input_preview(messages: List[Dict[str, Any]], limit: int = 160) -> str:
    preferred_messages = [message for message in reversed(messages) if message.get("role") == "user"]
    source_messages = preferred_messages or messages

    preview_parts: List[str] = []
    for message in source_messages:
        content = message.get("content")
        if not isinstance(content, str):
            continue
        stripped = content.strip()
        if stripped:
            preview_parts.append(stripped)
        if len(" | ".join(preview_parts)) >= limit:
            break
    preview = " | ".join(preview_parts)
    if len(preview) > limit:
        return preview[: limit - 3] + "..."
    return preview


def log_request_event(stage: str, request_id: str, payload: Dict[str, Any]) -> None:
    entry = {"ts": utc_now_iso(), "stage": stage, "request_id": request_id}
    entry.update(payload)
    append_audit_log(entry)


def stream_responses(provider: ProviderSpec, model: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]], tool_choice: Any, request_id: str, request_fingerprint: str, input_preview: str, client_path: str) -> Iterable[str]:
    response_id = f"resp_{uuid.uuid4().hex[:12]}"
    usage_payload: Dict[str, Any] = {}
    text_item_id = f"item_{uuid.uuid4().hex[:12]}"
    text_started = False
    text_value = ""
    reasoning_value = ""
    tool_states: Dict[int, Dict[str, Any]] = {}
    sequence_number = 0
    upstream = None

    yield sse_event("response.created", {"type": "response.created", "response": {"id": response_id, "object": "response", "status": "in_progress", "model": model, "output": [], "usage": None}})
    yield sse_event("response.in_progress", {"type": "response.in_progress", "response": {"id": response_id, "object": "response", "status": "in_progress", "model": model, "output": [], "usage": None}})

    payload: Dict[str, Any] = {"model": model, "messages": messages, "stream": True}
    if tools:
        payload["tools"] = tools
    if tool_choice != "auto":
        payload["tool_choice"] = tool_choice
    if provider.stream_options:
        payload["stream_options"] = copy.deepcopy(provider.stream_options)
    payload = merge_request_defaults(payload, provider.request_defaults)

    log_request_event(
        "upstream.request",
        request_id,
        {
            "response_id": response_id,
            "client_path": client_path,
            "requested_model": model,
            "provider_id": provider.provider_id,
            "provider_label": provider.label,
            "upstream_base_url": provider.base_url,
            "upstream_chat_url": provider.chat_url,
            "input_preview": input_preview,
            "input_fingerprint": request_fingerprint,
            "tool_count": len(tools),
        },
    )

    try:
        upstream = requests.post(provider.chat_url, headers=upstream_headers(provider), json=payload, timeout=provider.timeout, stream=True)
        upstream.raise_for_status()

        for raw_line in upstream.iter_lines():
            if not raw_line:
                continue
            decoded = raw_line.decode("utf-8")
            if not decoded.startswith("data:"):
                continue
            data_str = decoded[5:].strip()
            if data_str == "[DONE]":
                continue

            try:
                chunk = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            if chunk.get("error"):
                raise RuntimeError(chunk["error"].get("message") or "Upstream returned an error payload.")
            if chunk.get("usage"):
                usage_payload = chunk["usage"]

            choice = (chunk.get("choices") or [{}])[0]
            delta = choice.get("delta") or {}
            reasoning_delta = delta.get("reasoning_content") or delta.get("reasoning") or ""
            if reasoning_delta:
                reasoning_value += reasoning_delta

            content_delta = delta.get("content") or ""
            if content_delta:
                if not text_started:
                    text_started = True
                    yield sse_event("response.output_item.added", {"type": "response.output_item.added", "output_index": 0, "item": {"id": text_item_id, "type": "message", "status": "in_progress", "role": "assistant", "content": []}})
                    yield sse_event("response.content_part.added", {"type": "response.content_part.added", "item_id": text_item_id, "output_index": 0, "content_index": 0, "part": {"type": "text", "text": ""}})
                text_value += content_delta
                sequence_number += 1
                yield sse_event("response.output_text.delta", {"type": "response.output_text.delta", "item_id": text_item_id, "output_index": 0, "content_index": 0, "delta": content_delta, "sequence_number": sequence_number})

            for tool_call in delta.get("tool_calls") or []:
                index = int(tool_call.get("index") or 0)
                state = tool_states.setdefault(index, {"item_id": f"item_{uuid.uuid4().hex[:12]}", "call_id": "", "name": "", "arguments": "", "started": False})
                if tool_call.get("id"):
                    state["call_id"] = tool_call["id"]
                function_payload = tool_call.get("function") or {}
                if function_payload.get("name"):
                    state["name"] = function_payload["name"]
                arguments_delta = function_payload.get("arguments") or ""
                if not arguments_delta:
                    continue

                output_index = (1 if text_started else 0) + sorted(tool_states).index(index)
                if not state["started"]:
                    state["started"] = True
                    yield sse_event("response.output_item.added", {"type": "response.output_item.added", "output_index": output_index, "item": {"id": state["item_id"], "type": "function_call", "status": "in_progress", "call_id": state["call_id"], "name": state["name"], "arguments": ""}})

                state["arguments"] += arguments_delta
                yield sse_event("response.function_call_arguments.delta", {"type": "response.function_call_arguments.delta", "item_id": state["item_id"], "output_index": output_index, "delta": arguments_delta})

        output_items: List[Dict[str, Any]] = []
        if text_started:
            yield sse_event("response.output_text.done", {"type": "response.output_text.done", "item_id": text_item_id, "output_index": 0, "content_index": 0, "text": text_value})
            yield sse_event("response.content_part.done", {"type": "response.content_part.done", "item_id": text_item_id, "output_index": 0, "content_index": 0, "part": {"type": "text", "text": text_value}})
            completed_message: Dict[str, Any] = {"id": text_item_id, "type": "message", "status": "completed", "role": "assistant", "content": [{"type": "text", "text": text_value}]}
            if reasoning_value:
                completed_message["reasoning_content"] = reasoning_value
            yield sse_event("response.output_item.done", {"type": "response.output_item.done", "output_index": 0, "item": completed_message})
            output_items.append(completed_message)

        for tool_index in sorted(tool_states):
            state = tool_states[tool_index]
            output_index = (1 if text_started else 0) + sorted(tool_states).index(tool_index)
            yield sse_event("response.function_call_arguments.done", {"type": "response.function_call_arguments.done", "item_id": state["item_id"], "output_index": output_index, "arguments": state["arguments"]})
            completed_call: Dict[str, Any] = {"id": state["item_id"], "type": "function_call", "status": "completed", "call_id": state["call_id"], "name": state["name"], "arguments": state["arguments"]}
            if reasoning_value:
                completed_call["reasoning_content"] = reasoning_value
            yield sse_event("response.output_item.done", {"type": "response.output_item.done", "output_index": output_index, "item": completed_call})
            output_items.append(completed_call)

        log_request_event(
            "upstream.completed",
            request_id,
            {
                "response_id": response_id,
                "client_path": client_path,
                "requested_model": model,
                "provider_id": provider.provider_id,
                "provider_label": provider.label,
                "upstream_base_url": provider.base_url,
                "input_preview": input_preview,
                "input_fingerprint": request_fingerprint,
                "usage": make_usage(messages, text_value, tool_states, usage_payload),
                "tool_call_count": len(tool_states),
                "text_chars": len(text_value),
            },
        )
        yield sse_event("response.completed", {"type": "response.completed", "response": {"id": response_id, "object": "response", "status": "completed", "model": model, "output": output_items, "usage": make_usage(messages, text_value, tool_states, usage_payload)}})
    except Exception as exc:
        log_request_event(
            "upstream.failed",
            request_id,
            {
                "response_id": response_id,
                "client_path": client_path,
                "requested_model": model,
                "provider_id": provider.provider_id,
                "provider_label": provider.label,
                "upstream_base_url": provider.base_url,
                "input_preview": input_preview,
                "input_fingerprint": request_fingerprint,
                "error": str(exc),
            },
        )
        yield sse_event("response.failed", {"type": "response.failed", "response": {"id": response_id, "object": "response", "status": "failed", "model": model, "output": [], "usage": None, "error": {"message": str(exc), "type": "upstream_error"}}})
    finally:
        if upstream is not None:
            upstream.close()


@app.after_request
def add_cors_headers(response: Response) -> Response:
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


@app.get("/")
def index() -> Response:
    providers, _, raw_config = build_registry()
    return jsonify({"service": "new-codex-switcher", "providers": list(providers.keys()), "config_path": DEFAULT_CONFIG_PATH, "env_path": ENV_PATH, "server": raw_config.get("server") or {}})


@app.get("/health")
def health() -> Response:
    providers, model_index, _ = build_registry()
    return jsonify({"ok": True, "providers": list(providers.keys()), "models": sorted(model_index.keys())})


@app.get("/v1/models")
def list_models() -> Response:
    _, model_index, _ = build_registry()
    model_list = []
    for model_name, provider in sorted(model_index.items()):
        model_list.append({"id": model_name, "object": "model", "owned_by": provider.provider_id, "permission": [], "root": model_name})
    return jsonify({"object": "list", "data": model_list})


def build_responses_route() -> Response:
    if request.method == "OPTIONS":
        return Response(status=204)

    _, model_index, _ = build_registry()
    request_body = request.get_json(silent=True) or {}
    messages, tools, tool_choice = extract_messages(request_body)
    request_id = f"req_{uuid.uuid4().hex[:12]}"
    request_fingerprint = make_fingerprint(request_body)
    input_preview = make_input_preview(messages)
    requested_model = request_body.get("model")
    if not requested_model and model_index:
        requested_model = next(iter(model_index.keys()))
    resolved_model, alias_reason = resolve_model_alias(requested_model, model_index)
    provider = model_index.get(resolved_model or "")
    if provider is None:
        log_request_event(
            "route.rejected",
            request_id,
            {
                "client_path": request.path,
                "requested_model": requested_model,
                "resolved_model": resolved_model,
                "input_preview": input_preview,
                "input_fingerprint": request_fingerprint,
                "error": f"Unknown model '{requested_model}'",
            },
        )
        return jsonify({"error": {"message": f"Unknown model '{requested_model}'. Check /v1/models for the supported list.", "type": "invalid_request_error"}}), 400
    log_request_event(
        "route.selected",
        request_id,
        {
            "client_path": request.path,
            "requested_model": requested_model,
            "resolved_model": resolved_model,
            "alias_reason": alias_reason,
            "provider_id": provider.provider_id,
            "provider_label": provider.label,
            "upstream_base_url": provider.base_url,
            "input_preview": input_preview,
            "input_fingerprint": request_fingerprint,
        },
    )
    return Response(stream_responses(provider, resolved_model or requested_model, messages, tools, tool_choice, request_id, request_fingerprint, input_preview, request.path), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


app.add_url_rule("/responses", "responses", build_responses_route, methods=["POST", "OPTIONS"])
app.add_url_rule("/v1/responses", "v1_responses", build_responses_route, methods=["POST", "OPTIONS"])


def post_responses(base_url: str, payload: Dict[str, object]) -> List[Dict[str, object]]:
    response = requests.post(f"{base_url.rstrip('/')}/v1/responses", headers={"Content-Type": "application/json"}, json=payload, stream=True, timeout=240)
    response.raise_for_status()
    return collect_sse_events(response)


def collect_sse_events(response: requests.Response) -> List[Dict[str, object]]:
    events: List[Dict[str, object]] = []
    current_event = ""
    for raw_line in response.iter_lines(decode_unicode=True):
        if raw_line is None:
            continue
        if raw_line.startswith("event: "):
            current_event = raw_line[7:]
            continue
        if raw_line.startswith("data: "):
            payload = raw_line[6:]
            if payload == "[DONE]":
                continue
            events.append({"event": current_event, "data": json.loads(payload)})
    return events


def join_text_deltas(events: Iterable[Dict[str, object]]) -> str:
    text_parts: List[str] = []
    completed = False
    for event in events:
        data = event["data"]
        if data.get("type") == "response.output_text.delta":
            text_parts.append(data.get("delta", ""))
        elif data.get("type") == "response.completed":
            completed = True
    if not completed:
        raise AssertionError("Missing response.completed event")
    return "".join(text_parts)


def extract_usage(events: Iterable[Dict[str, object]]) -> Dict[str, object]:
    for event in events:
        data = event["data"]
        if data.get("type") == "response.completed":
            return data.get("response", {}).get("usage", {})
    return {}


def run_text_probe(base_url: str, model: str, expected_text: str) -> Dict[str, object]:
    events = post_responses(base_url, {"model": model, "input": f"Reply with exactly: {expected_text}"})
    full_text = join_text_deltas(events)
    if full_text != expected_text:
        raise AssertionError(f"{model}: expected '{expected_text}', got '{full_text}'")
    return {"kind": "text", "model": model, "text": full_text, "usage": extract_usage(events)}


def first_tool_turn_payload(model: str) -> Dict[str, object]:
    return {
        "model": model,
        "tool_choice": {"type": "function", "name": TOOL_NAME},
        "input": INITIAL_PROMPT,
        "tools": [{"type": "function", "name": TOOL_NAME, "description": "Look up a short status string for a code.", "parameters": {"type": "object", "properties": {"code": {"type": "string", "description": "Short code to resolve."}}, "required": ["code"], "additionalProperties": False}}],
    }


def second_tool_turn_payload(model: str, call_id: str, arguments: str) -> Dict[str, object]:
    return {
        "model": model,
        "input": [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": INITIAL_PROMPT}]},
            {"type": "function_call", "call_id": call_id, "name": TOOL_NAME, "arguments": arguments},
            {"type": "function_call_output", "call_id": call_id, "output": json.dumps(TOOL_RESULT, ensure_ascii=False)},
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": FOLLOWUP_PROMPT}]},
        ],
    }


def extract_function_call(events: Iterable[Dict[str, object]]) -> Tuple[str, str, str]:
    call_id = ""
    item_id = ""
    arguments = ""
    for event in events:
        data = event["data"]
        event_type = data.get("type")
        if event_type == "response.output_item.added":
            item = data.get("item", {})
            if item.get("type") == "function_call" and item.get("name") == TOOL_NAME:
                call_id = item.get("call_id", "")
                item_id = item.get("id", "")
        elif event_type == "response.function_call_arguments.delta":
            arguments += data.get("delta", "")
        elif event_type == "response.function_call_arguments.done" and not arguments:
            arguments = data.get("arguments", "")
    if not call_id:
        raise AssertionError("Missing function_call output item")
    if not arguments:
        raise AssertionError("Missing streamed function_call arguments")
    return call_id, item_id, arguments


def run_tool_probe(base_url: str, model: str) -> Dict[str, object]:
    initial_events = post_responses(base_url, first_tool_turn_payload(model))
    call_id, item_id, arguments = extract_function_call(initial_events)
    followup_events = post_responses(base_url, second_tool_turn_payload(model, call_id, arguments))
    final_text = join_text_deltas(followup_events)
    if TOOL_RESULT["status"] not in final_text:
        raise AssertionError(f"{model}: expected final text to include '{TOOL_RESULT['status']}', got '{final_text}'")
    return {"kind": "tool", "model": model, "call_id": call_id, "item_id": item_id, "arguments": arguments, "final_text": final_text, "usage": extract_usage(followup_events)}


def run_all(base_url: str, mode: str) -> List[Dict[str, object]]:
    results: List[Dict[str, object]] = []
    if mode in {"text", "all"}:
        for model, expected_text in TEXT_PROBES:
            results.append(run_text_probe(base_url, model, expected_text))
    if mode in {"tool", "all"}:
        for model in TOOL_PROBES:
            results.append(run_tool_probe(base_url, model))
    return results


def print_auth_token() -> int:
    print(os.environ.get("CODEX_SWITCHER_BEARER_TOKEN", "codex-switcher-local-token"))
    return 0


def split_config_preamble(config_text: str) -> Tuple[List[str], List[str]]:
    lines = config_text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("["):
            return lines[:index], lines[index:]
    return lines, []


def write_codex_mode(target: str) -> int:
    if not os.path.exists(CODEX_CONFIG_PATH):
        raise FileNotFoundError(f"Codex config not found: {CODEX_CONFIG_PATH}")

    with open(CODEX_CONFIG_PATH, "r", encoding="utf-8") as handle:
        original_text = handle.read()

    head_lines, tail_lines = split_config_preamble(original_text)
    preserved_lines = []
    for line in head_lines:
        stripped = line.strip()
        if stripped.startswith("model = "):
            continue
        if stripped.startswith("model_provider = "):
            continue
        if stripped.startswith("model_context_window = "):
            continue
        if stripped.startswith("model_auto_compact_token_limit = "):
            continue
        if stripped.startswith("model_catalog_json = "):
            continue
        preserved_lines.append(line)

    new_head = []
    if target == "gpt":
        new_head.append('model = "gpt-5.4"')
    elif target == "deepseek":
        new_head.extend(
            [
                'model = "deepseek-v4-pro"',
                'model_provider = "ds_xiaomi_switcher"',
                'model_context_window = 1048576',
                'model_auto_compact_token_limit = 1000000',
                f'model_catalog_json = {json.dumps(DEFAULT_CODEX_MODEL_CATALOG_PATH)}',
            ]
        )
    elif target == "xiaomi":
        new_head.extend(
            [
                'model = "mimo-v2.5-pro"',
                'model_provider = "ds_xiaomi_switcher"',
                'model_context_window = 1048576',
                'model_auto_compact_token_limit = 1000000',
                f'model_catalog_json = {json.dumps(DEFAULT_CODEX_MODEL_CATALOG_PATH)}',
            ]
        )
    else:
        raise ValueError(f"Unsupported target mode: {target}")

    if preserved_lines and preserved_lines[0] != "":
        new_head.extend(preserved_lines)
    else:
        new_head.extend(preserved_lines)

    new_text = "\n".join(new_head + tail_lines)
    if not new_text.endswith("\n"):
        new_text += "\n"

    with open(CODEX_CONFIG_PATH, "w", encoding="utf-8") as handle:
        handle.write(new_text)

    print(f"Switched Codex default to: {target}")
    print(f"Config path: {CODEX_CONFIG_PATH}")
    if target in {"deepseek", "xiaomi"}:
        print(f"Model catalog path: {DEFAULT_CODEX_MODEL_CATALOG_PATH}")
    return 0


def print_codex_mode_status() -> int:
    with open(CODEX_CONFIG_PATH, "rb") as handle:
        data = __import__("tomllib").load(handle)
    print(json.dumps({
        "config_path": CODEX_CONFIG_PATH,
        "model": data.get("model"),
        "model_provider": data.get("model_provider"),
        "model_context_window": data.get("model_context_window"),
        "model_auto_compact_token_limit": data.get("model_auto_compact_token_limit"),
        "model_catalog_json": data.get("model_catalog_json"),
    }, ensure_ascii=False))
    return 0


def print_audit_log(lines: int) -> int:
    if not os.path.exists(AUDIT_LOG_PATH):
        raise FileNotFoundError(f"Audit log not found: {AUDIT_LOG_PATH}")
    with open(AUDIT_LOG_PATH, "r", encoding="utf-8") as handle:
        entries = handle.readlines()
    for line in entries[-lines:]:
        print(line.rstrip("\n"))
    return 0


def run_server(host_override: Optional[str], port_override: Optional[int]) -> int:
    from waitress import serve

    _, _, raw_config = build_registry()
    server_config = raw_config.get("server") or {}
    host = host_override or server_config.get("host", "127.0.0.1")
    port = port_override or int(server_config.get("port") or 5055)
    print(f"New Codex Switcher listening on http://{host}:{port}")
    print(f"Config path: {DEFAULT_CONFIG_PATH}")
    print(f"Env path: {ENV_PATH}")
    serve(app, host=host, port=port, threads=8)
    return 0


def run_checks(base_url: str, mode: str) -> int:
    for result in run_all(base_url, mode):
        print(json.dumps(result, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")

    serve_parser = subparsers.add_parser("serve", help="Run the local DeepSeek/Xiaomi Responses proxy.")
    serve_parser.add_argument("--host", default=None)
    serve_parser.add_argument("--port", type=int, default=None)

    check_parser = subparsers.add_parser("check", help="Run text and tool smoke tests against the live local proxy.")
    check_parser.add_argument("--base-url", default="http://127.0.0.1:5055")
    check_parser.add_argument("--mode", choices=["all", "text", "tool"], default="all")

    subparsers.add_parser("auth-token", help="Print a local bearer token for Codex custom-provider auth.")

    use_parser = subparsers.add_parser("use", help="Switch the Codex Desktop default model between GPT, DeepSeek, and Xiaomi.")
    use_parser.add_argument("target", choices=["gpt", "deepseek", "xiaomi", "status"])

    audit_parser = subparsers.add_parser("audit-tail", help="Print the newest switcher audit log entries.")
    audit_parser.add_argument("--lines", type=int, default=10)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "serve":
        return run_server(args.host, args.port)
    if args.command == "auth-token":
        return print_auth_token()
    if args.command == "use":
        if args.target == "status":
            return print_codex_mode_status()
        return write_codex_mode(args.target)
    if args.command == "audit-tail":
        return print_audit_log(args.lines)
    return run_checks(getattr(args, "base_url", "http://127.0.0.1:5055"), getattr(args, "mode", "all"))


if __name__ == "__main__":
    sys.exit(main())