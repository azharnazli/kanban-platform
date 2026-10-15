"""WSGI test client used by the PostgreSQL integration tests. No sockets."""

from __future__ import annotations

import io
import json
from collections import namedtuple

from kanban_http.app import create_app

Response = namedtuple("Response", ["status", "headers", "json", "raw"])


def make_app(store):
    return create_app(store)


def request(app, method, path, body=None, query=None, raw_body=None):
    """Invoke the WSGI app in-process and return a Response.

    body: JSON-serializable payload dict.
    raw_body: raw bytes, for malformed-body tests (takes precedence).
    """
    if raw_body is not None:
        raw = raw_body
    elif body is not None:
        raw = json.dumps(body).encode("utf-8")
    else:
        raw = b""
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query or "",
        "CONTENT_LENGTH": str(len(raw)),
        "wsgi.input": io.BytesIO(raw),
        "wsgi.errors": io.StringIO(),
        "SERVER_NAME": "testserver",
        "SERVER_PORT": "80",
        "wsgi.url_scheme": "http",
        "HTTP_HOST": "testserver",
    }
    captured = {}

    def start_response(status, headers, exc_info=None):
        captured["status"] = status
        captured["headers"] = headers

    chunks = app(environ, start_response)
    payload = b"".join(chunks)
    parsed = json.loads(payload) if payload else None
    status_code = int(captured["status"].split(" ", 1)[0])
    return Response(status_code, dict(captured["headers"]), parsed, payload)
