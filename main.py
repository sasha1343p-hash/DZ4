"""CLI entrypoint: python main.py \"создай пользователя с именем Alex\"."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

from agent_factory import build_agent, load_user_template  # noqa: E402
from api.server import PORT, serve  # noqa: E402


def message_text(message) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "\n".join(part for part in parts if part)
    return str(content)


def ensure_api() -> str:
    base = os.getenv("API_BASE_URL", f"http://127.0.0.1:{PORT}").rstrip("/")
    if _healthy(base):
        print(f"[main] API already running at {base}", flush=True)
        return base
    parsed = urlparse(base)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or PORT
    thread = threading.Thread(
        target=serve,
        kwargs={"host": host, "port": port},
        name="user-directory-api",
        daemon=True,
    )
    thread.start()
    for _ in range(50):
        if _healthy(base):
            print(f"[main] API started at {base}", flush=True)
            return base
        time.sleep(0.1)
    raise RuntimeError(f"User Directory API did not start at {base}")


def _healthy(base: str) -> bool:
    try:
        response = httpx.get(f"{base}/health", timeout=0.5)
        return response.status_code == 200
    except httpx.HTTPError:
        return False


def render_trace(result: dict) -> str:
    messages = result.get("messages", [])
    lines: list[str] = []
    for index, message in enumerate(messages):
        role = getattr(message, "type", type(message).__name__)
        tool_calls = getattr(message, "tool_calls", None) or []
        if tool_calls:
            lines.append(f"[trace] {role} tool_calls={tool_calls}")
        elif role == "tool":
            lines.append(f"[trace] tool {getattr(message, 'name', '')}: {message_text(message)}")
        elif role == "ai" and index != len(messages) - 1:
            lines.append(f"[trace] ai: {message_text(message)}")
    return "\n".join(lines)


def _parse_fields(text: str) -> dict[str, str]:
    fields = {"Status": "", "Action": "", "Data": "", "Errors": ""}
    for line in text.splitlines():
        stripped = line.strip()
        for key in fields:
            prefix = f"{key}:"
            if stripped.startswith(prefix):
                fields[key] = stripped[len(prefix) :].strip()
    return fields


def _last_tool(result: dict) -> tuple[dict | None, dict]:
    calls: list[dict] = []
    payload: dict | None = None
    for message in result.get("messages", []):
        tool_calls = getattr(message, "tool_calls", None) or []
        if tool_calls:
            calls = tool_calls
        if getattr(message, "type", None) != "tool":
            continue
        text = message_text(message)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict) and "http_status" in parsed:
            payload = parsed
        else:
            payload = {"http_status": 0, "body": {"error": "tool_error", "message": text}}
    args = calls[-1].get("args", {}) if calls else {}
    return payload, args


def _four(status: str, action: str, data: str, errors: str) -> str:
    action = " ".join(action.split())
    return f"Status: {status}\nAction: {action}\nData: {data}\nErrors: {errors}"


def _success_action(operation: str, args: dict, body: dict) -> str:
    if operation == "create_user":
        name = (body.get("user") or {}).get("name") or args.get("name") or ""
        return f"создал пользователя {name}".strip()
    if operation == "get_user":
        return f"получил пользователя с id {args.get('user_id')}"
    if operation == "update_user":
        status = args.get("status") or ""
        if status == "blocked":
            return f"заблокировал пользователя с id {args.get('user_id')}"
        if status == "active":
            return f"активировал пользователя с id {args.get('user_id')}"
        return f"обновил пользователя с id {args.get('user_id')}"
    if operation == "list_users":
        return f"получил список пользователей, count={body.get('count')}"
    return f"выполнил операцию {operation}"


def build_contract(result: dict, raw: str) -> str:
    """Four-line contract. Data and Status come from the tool result, not from a paraphrase."""
    fields = _parse_fields(raw)
    payload, args = _last_tool(result)
    if payload is None:
        action = fields["Action"] or "запрос вне зоны ответственности User Directory"
        errors = fields["Errors"]
        if not errors or errors == "none":
            errors = "запрос вне зоны ответственности User Directory"
        return _four("error", action, "{}", errors)

    http_status = int(payload.get("http_status") or 0)
    body = payload.get("body")
    if not isinstance(body, dict):
        body = {"raw": body}
    operation = str(args.get("operation") or "")
    if http_status in (200, 201):
        data = json.dumps(body, ensure_ascii=False)
        return _four("success", _success_action(operation, args, body), data, "none")
    if http_status == 0 and body.get("error") == "tool_error":
        message = str(body.get("message") or "tool invocation failed")
        return _four("error", f"не выполнил {operation or 'операцию'}", "{}", message)
    message = str(body.get("message") or body.get("error") or f"http {http_status}")
    data = json.dumps(body, ensure_ascii=False)
    return _four("error", f"не выполнил {operation or 'операцию'}: {message}", data, message)


def run_query(query: str) -> str:
    ensure_api()
    agent = build_agent()
    user_message = load_user_template().format(query=query)
    result = agent.invoke(
        {"messages": [{"role": "user", "content": user_message}]},
        config={"recursion_limit": 10},
    )
    raw = message_text(result["messages"][-1]).strip()
    contract = build_contract(result, raw)
    trace = render_trace(result)
    if raw != contract:
        draft = "[trace] model_draft: " + raw.replace("\n", " | ")
        trace = f"{trace}\n{draft}" if trace else draft
    if trace:
        print(trace, flush=True)
    return contract


def main() -> None:
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print('Usage: python main.py "создай пользователя с именем Alex"', file=sys.stderr)
        raise SystemExit(2)
    query = sys.argv[1]
    print(run_query(query))


if __name__ == "__main__":
    main()
