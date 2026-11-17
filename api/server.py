"""Minimal User Directory API.

Operations:
- POST   /users          create a user
- GET    /users/{id}     fetch one user
- PATCH  /users/{id}     update name, email, role, or status
- GET    /users          list users, optional ?status=
- GET    /health         liveness probe
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HOST = "127.0.0.1"
PORT = 8765
ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("USER_DB_PATH", ROOT / "data" / "users.json"))

ROLES = {"member", "admin", "viewer"}
STATUSES = {"active", "blocked"}

_lock = threading.Lock()
_users: dict[int, dict] = {}
_next_id = 1


def _load_store() -> None:
    global _next_id
    if not DB_PATH.exists():
        _users.clear()
        _next_id = 1
        return
    data = json.loads(DB_PATH.read_text(encoding="utf-8"))
    _users.clear()
    _users.update({int(key): value for key, value in data.get("users", {}).items()})
    _next_id = int(data.get("next_id", 1))


def _save_store() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "next_id": _next_id,
        "users": {str(key): value for key, value in _users.items()},
    }
    DB_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def reset_store() -> None:
    """Clear users and persist the empty catalog."""
    global _next_id
    with _lock:
        _users.clear()
        _next_id = 1
        _save_store()


_load_store()


def _error(code: str, message: str) -> dict:
    return {"error": code, "message": message}


class UserDirectoryHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        print(f"[api] {self.address_string()} {fmt % args}", flush=True)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        return data

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path == "/health":
            self._send(200, {"status": "ok"})
            return
        if path == "/users":
            status_filter = (parse_qs(parsed.query).get("status") or [None])[0]
            if status_filter is not None and status_filter not in STATUSES:
                self._send(400, _error("bad_status", "status must be active or blocked"))
                return
            with _lock:
                users = list(_users.values())
            if status_filter:
                users = [user for user in users if user["status"] == status_filter]
            self._send(200, {"users": users, "count": len(users)})
            return
        if path.startswith("/users/"):
            user_id = _parse_user_id(path)
            if user_id is None:
                self._send(400, _error("bad_id", "user id must be a positive integer"))
                return
            with _lock:
                user = _users.get(user_id)
            if user is None:
                self._send(404, _error("not_found", f"user {user_id} not found"))
                return
            self._send(200, {"user": user})
            return
        self._send(404, _error("not_found", f"path {path} not found"))

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path != "/users":
            self._send(404, _error("not_found", f"path {path} not found"))
            return
        try:
            payload = self._read_json()
        except (json.JSONDecodeError, ValueError) as exc:
            self._send(400, _error("bad_json", str(exc)))
            return
        name = str(payload.get("name", "")).strip()
        if not name:
            self._send(400, _error("missing_name", "name is required"))
            return
        email = str(payload.get("email", "")).strip()
        if email and "@" not in email:
            self._send(400, _error("bad_email", "email must contain @"))
            return
        role = str(payload.get("role", "member")).strip() or "member"
        if role not in ROLES:
            self._send(400, _error("bad_role", "role must be member, admin, or viewer"))
            return
        global _next_id
        with _lock:
            user = {
                "id": _next_id,
                "name": name,
                "email": email,
                "role": role,
                "status": "active",
            }
            _users[_next_id] = user
            _next_id += 1
            _save_store()
        self._send(201, {"user": user})

    def do_PATCH(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        if not path.startswith("/users/"):
            self._send(404, _error("not_found", f"path {path} not found"))
            return
        user_id = _parse_user_id(path)
        if user_id is None:
            self._send(400, _error("bad_id", "user id must be a positive integer"))
            return
        try:
            payload = self._read_json()
        except (json.JSONDecodeError, ValueError) as exc:
            self._send(400, _error("bad_json", str(exc)))
            return
        updates: dict[str, str] = {}
        if "name" in payload:
            name = str(payload.get("name", "")).strip()
            if not name:
                self._send(400, _error("bad_name", "name cannot be empty"))
                return
            updates["name"] = name
        if "email" in payload:
            email = str(payload.get("email", "")).strip()
            if email and "@" not in email:
                self._send(400, _error("bad_email", "email must contain @"))
                return
            updates["email"] = email
        if "role" in payload:
            role = str(payload.get("role", "")).strip()
            if role not in ROLES:
                self._send(400, _error("bad_role", "role must be member, admin, or viewer"))
                return
            updates["role"] = role
        if "status" in payload:
            status = str(payload.get("status", "")).strip()
            if status not in STATUSES:
                self._send(400, _error("bad_status", "status must be active or blocked"))
                return
            updates["status"] = status
        if not updates:
            self._send(400, _error("empty_update", "provide name, email, role, or status"))
            return
        with _lock:
            user = _users.get(user_id)
            if user is None:
                self._send(404, _error("not_found", f"user {user_id} not found"))
                return
            user.update(updates)
            stored = dict(user)
            _save_store()
        self._send(200, {"user": stored})


def _parse_user_id(path: str) -> int | None:
    raw = path.removeprefix("/users/").split("/", 1)[0]
    if not raw.isdigit():
        return None
    user_id = int(raw)
    if user_id <= 0:
        return None
    return user_id


def serve(host: str = HOST, port: int = PORT) -> None:
    server = ThreadingHTTPServer((host, port), UserDirectoryHandler)
    print(f"[api] listening on http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    serve()
