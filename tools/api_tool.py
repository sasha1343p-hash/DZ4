"""One LangChain tool that covers every User Directory operation."""

from __future__ import annotations

import json
import os
from typing import Literal

import httpx
from langchain_core.tools import tool

Operation = Literal["create_user", "get_user", "update_user", "list_users"]

API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8765")


def _dump(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _call(method: str, path: str, body: dict | None = None) -> str:
    url = API_BASE_URL.rstrip("/") + path
    print(
        f"[tool:user_directory_api] HTTP {method} {url} body={body}",
        flush=True,
    )
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.request(method, url, json=body)
    except httpx.HTTPError as exc:
        result = _dump({"http_status": 0, "body": {"error": "transport", "message": str(exc)}})
        print(f"[tool:user_directory_api] result {result}", flush=True)
        return result
    try:
        parsed = response.json()
    except json.JSONDecodeError:
        parsed = {"raw": response.text}
    result = _dump({"http_status": response.status_code, "body": parsed})
    print(f"[tool:user_directory_api] result {result}", flush=True)
    return result


@tool
def user_directory_api(
    operation: Operation,
    name: str | None = None,
    email: str | None = None,
    role: str | None = None,
    user_id: int | None = None,
    status: str | None = None,
) -> str:
    """Call the User Directory HTTP API. Use this for every user-catalog request.

    Args:
        operation: create_user, get_user, update_user, or list_users. Always required.
        name: User name. Required for create_user. Optional for update_user.
        email: Optional email for create_user or update_user. Omit when unknown.
        role: member, admin, or viewer. Optional. Omit when the user did not name a role.
        user_id: Required for get_user and update_user. Omit it for create_user and list_users.
        status: active or blocked. Only for list_users filter or update_user. Omit otherwise.
    """
    name = (name or "").strip()
    email = (email or "").strip()
    role = (role or "").strip()
    status = (status or "").strip()
    user_id = int(user_id or 0)
    if operation == "create_user":
        body: dict[str, str] = {}
        if name:
            body["name"] = name
        if email:
            body["email"] = email
        if role:
            body["role"] = role
        return _call("POST", "/users", body)

    if operation == "get_user":
        if user_id <= 0:
            result = _dump(
                {
                    "http_status": 0,
                    "body": {"error": "missing_user_id", "message": "user_id is required"},
                }
            )
            print(f"[tool:user_directory_api] result {result}", flush=True)
            return result
        return _call("GET", f"/users/{user_id}")

    if operation == "update_user":
        if user_id <= 0:
            result = _dump(
                {
                    "http_status": 0,
                    "body": {"error": "missing_user_id", "message": "user_id is required"},
                }
            )
            print(f"[tool:user_directory_api] result {result}", flush=True)
            return result
        body = {}
        if name:
            body["name"] = name
        if email:
            body["email"] = email
        if role:
            body["role"] = role
        if status:
            body["status"] = status
        return _call("PATCH", f"/users/{user_id}", body)

    if operation == "list_users":
        path = "/users"
        if status:
            path = f"/users?status={status}"
        return _call("GET", path)

    result = _dump(
        {
            "http_status": 0,
            "body": {
                "error": "unknown_operation",
                "message": f"unsupported operation: {operation}",
            },
        }
    )
    print(f"[tool:user_directory_api] result {result}", flush=True)
    return result
