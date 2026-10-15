"""Local mock of the Kanban V1 HTTP contract for frontend tests and demos.

Serves static/ plus the contract routes from an in-memory synthetic store.
Workflow routes (evidence, history) are opt-in mocks, labeled synthetic, and
exist only with --workflow-mocks or the __mock scenario switch. Never contacts
a real service or database.

Usage: python3 tests/mock_server.py [--port 8765] [--host 127.0.0.1] [--workflow-mocks]
"""

from __future__ import annotations

import argparse
import json
import re
import threading
import time
from http import HTTPStatus
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, quote, unquote
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

STATUSES = [
    "TODO",
    "READY",
    "IN_PROGRESS",
    "REVIEW",
    "CHANGES_REQUESTED",
    "APPROVED",
    "MERGED",
    "CLOSED",
    "BLOCKED",
]
CARD_TYPES = ["project_epic", "feature", "task"]
JSON_HEADERS = [("Content-Type", "application/json")]
CARD_FIELDS = ("id", "title", "card_type", "status", "parent_id")
MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json",
    ".png": "image/png",
    ".ico": "image/x-icon",
}

FIXTURE_CARDS = [
    ("0b2c43aa91de0001", "North Atlantic Survey Grid", "project_epic", "TODO", None),
    ("0b2c43aa91de0002", "Ingest pipeline", "feature", "IN_PROGRESS", "0b2c43aa91de0001"),
    ("0b2c43aa91de0003", "Chart renderer", "feature", "READY", "0b2c43aa91de0001"),
    ("0b2c43aa91de0004", "Tide almanac", "feature", "TODO", "0b2c43aa91de0001"),
    ("9f2c1ab34d5e6f01", "Provision PostgreSQL cluster", "task", "CLOSED", "0b2c43aa91de0002"),
    ("9f2c1ab34d5e6f02", "Wire ingestion adapters", "task", "IN_PROGRESS", "0b2c43aa91de0002"),
    ("9f2c1ab34d5e6f03", "Backfill sounding archive", "task", "BLOCKED", "0b2c43aa91de0002"),
    ("9f2c1ab34d5e6f04", "Sketch chart tiles", "task", "REVIEW", "0b2c43aa91de0003"),
    ("9f2c1ab34d5e6f05", "Plot course lines", "task", "CHANGES_REQUESTED", "0b2c43aa91de0003"),
    ("9f2c1ab34d5e6f06", "Merge chart corrections", "task", "APPROVED", "0b2c43aa91de0003"),
    ("9f2c1ab34d5e6f07", "Publish tide tables", "task", "TODO", "0b2c43aa91de0004"),
    ("9f2c1ab34d5e6f08", "Archive notice bulletins", "task", "MERGED", "0b2c43aa91de0004"),
    ("9f2c1ab34d5e6f09", "Audit depth contours", "task", "TODO", "0b2c43aa91de0003"),
    ("9f2c1ab34d5e6f0a", "Retire legacy fixes", "task", "CLOSED", None),
]

FIXTURE_DEPS = [
    ("9f2c1ab34d5e6f02", "9f2c1ab34d5e6f01"),
    ("9f2c1ab34d5e6f03", "9f2c1ab34d5e6f05"),
    ("9f2c1ab34d5e6f04", "9f2c1ab34d5e6f02"),
    ("9f2c1ab34d5e6f05", "9f2c1ab34d5e6f01"),
    ("9f2c1ab34d5e6f07", "9f2c1ab34d5e6f03"),
    ("9f2c1ab34d5e6f09", "9f2c1ab34d5e6f02"),
    ("0b2c43aa91de0003", "9f2c1ab34d5e6f01"),
    ("9f2c1ab34d5e6f08", "9f2c1ab34d5e6f01"),
    ("9f2c1ab34d5e6f0a", "9f2c1ab34d5e6f01"),
]

EVIDENCE_FIXTURES = {
    "9f2c1ab34d5e6f03": [
        {
            "label": "Criterion: archive covers sounding years 2019–2025",
            "detail": "Backfill verified against three sample months",
            "actor": "mock-reviewer",
            "at": "2026-10-12T10:30:00Z",
            "synthetic": True,
        },
        {
            "label": "Criterion: spot checks match depth contours",
            "detail": "42 of 42 sampled points within tolerance",
            "actor": "mock-reviewer",
            "at": "2026-10-12T10:34:00Z",
            "synthetic": True,
        },
    ],
    "9f2c1ab34d5e6f05": [
        {
            "label": "Criterion: course lines match divider measurements",
            "detail": "Rendered at 1440 and 390 widths",
            "actor": "mock-reviewer",
            "at": "2026-10-14T09:20:00Z",
            "synthetic": True,
        }
    ],
}

HISTORY_FIXTURES = {
    "9f2c1ab34d5e6f03": [
        {"label": "Created", "actor": "mock-planner", "at": "2026-10-01T08:00:00Z", "synthetic": True},
        {
            "label": "Moved to BLOCKED",
            "detail": "Waiting on Plot course lines",
            "actor": "mock-planner",
            "at": "2026-10-09T14:12:00Z",
            "synthetic": True,
        },
        {
            "label": "Moved to CHANGES_REQUESTED → BLOCKED re-noted",
            "actor": "mock-planner",
            "at": "2026-10-13T11:05:00Z",
            "synthetic": True,
        },
    ]
}


class ApiFailure(Exception):
    def __init__(self, status, code, message, details=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}

    def payload(self):
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


def validation_error(message, field=None, reason=None):
    details = {}
    if field is not None:
        details["field"] = field
    if reason is not None:
        details["reason"] = reason
    return ApiFailure(422, "validation_error", message, details)


class MockState:
    def __init__(self, workflow_mocks=False):
        self.lock = threading.Lock()
        self.workflow_mocks_default = workflow_mocks
        self.reset()

    def reset(self):
        self.cards = {}
        self.order = []
        for card_id, title, card_type, status, parent_id in FIXTURE_CARDS:
            self.cards[card_id] = {
                "id": card_id,
                "title": title,
                "card_type": card_type,
                "status": status,
                "parent_id": parent_id,
            }
            self.order.append(card_id)
        self.deps = {card_id: [] for card_id in self.cards}
        for card_id, dep_id in FIXTURE_DEPS:
            self.deps[card_id].append(dep_id)
        self.mode = "normal"
        self.delay_ms = 0
        self.workflow_mocks = self.workflow_mocks_default

    def snapshot(self):
        return {
            "mode": self.mode,
            "delay_ms": self.delay_ms,
            "workflow_mocks": self.workflow_mocks,
            "card_count": len(self.cards),
        }

    def list_cards(self):
        return [self.cards[card_id] for card_id in self.order if card_id in self.cards]

    def get_card(self, card_id):
        return self.cards.get(card_id)

    def open_dependencies(self, card_id):
        open_ids = []
        for dep_id in self.deps.get(card_id, []):
            dep = self.get_card(dep_id)
            if dep is None or dep.get("status") != "CLOSED":
                open_ids.append(dep_id)
        return open_ids

    def find_cycle(self, card_id, depends_on_card_id):
        stack = [(depends_on_card_id, [depends_on_card_id])]
        seen = set()
        while stack:
            current, path = stack.pop()
            if current == card_id:
                return path + [card_id]
            if current in seen:
                continue
            seen.add(current)
            for nxt in self.deps.get(current, []):
                stack.append((nxt, path + [nxt]))
        return None


class MockApp:
    def __init__(self, static_dir: Path, workflow_mocks=False):
        self.static_dir = static_dir
        self.state = MockState(workflow_mocks=workflow_mocks)
        self.routes = (
            (re.compile(r"^/health$"), "GET", self.health),
            (re.compile(r"^/cards$"), "POST", self.create_card),
            (re.compile(r"^/cards$"), "GET", self.list_cards),
            (re.compile(r"^/cards/(?P<card_id>[^/]+)$"), "GET", self.get_card),
            (re.compile(r"^/cards/(?P<card_id>[^/]+)/status$"), "PATCH", self.update_status),
            (re.compile(r"^/cards/(?P<card_id>[^/]+)/dependencies$"), "POST", self.add_dependency),
            (re.compile(r"^/cards/(?P<card_id>[^/]+)/dependencies$"), "GET", self.list_dependencies),
            (
                re.compile(r"^/cards/(?P<card_id>[^/]+)/dependencies/(?P<depends_on_card_id>[^/]+)$"),
                "DELETE",
                self.remove_dependency,
            ),
            (re.compile(r"^/board$"), "GET", self.board),
            (re.compile(r"^/progress$"), "GET", self.progress),
            (re.compile(r"^/__mock/scenario$"), "GET", self.mock_get_scenario),
            (re.compile(r"^/__mock/scenario$"), "POST", self.mock_set_scenario),
            (re.compile(r"^/__mock/reset$"), "POST", self.mock_reset),
        )

    def __call__(self, environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "/") or "/"
        query = parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True)
        raw = self._read_body(environ)
        if method == "GET" and not path.startswith(("/cards", "/board", "/progress", "/health", "/__mock")):
            static = self._serve_static(path, start_response)
            if static is not None:
                return static
        try:
            with self.state.lock:
                if self.state.delay_ms:
                    time.sleep(self.state.delay_ms / 1000)
                status, headers, payload = self._dispatch(method, path, query, raw)
        except ApiFailure as exc:
            status, headers, payload = exc.status, JSON_HEADERS, exc.payload()
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        headers = list(headers) + [("Content-Length", str(len(body)))]
        start_response(f"{status} {HTTPStatus(status).phrase}", headers)
        return [body]

    def _serve_static(self, path, start_response):
        relative = unquote(path.lstrip("/")) or "index.html"
        target = (self.static_dir / relative).resolve()
        try:
            target.relative_to(self.static_dir.resolve())
        except ValueError:
            return None
        if not target.is_file():
            return None
        body = target.read_bytes()
        content_type = MIME_TYPES.get(target.suffix, "application/octet-stream")
        headers = [
            ("Content-Type", content_type),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
        ]
        start_response("200 OK", headers)
        return [body]

    @staticmethod
    def _read_body(environ):
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            return b""
        return environ["wsgi.input"].read(length)

    def _dispatch(self, method, path, query, raw):
        if self.state.mode == "error" and method == "GET" and path in ("/board", "/cards", "/progress"):
            raise ApiFailure(500, "internal_error", "unexpected server error", {})
        allowed = set()
        for pattern, verb, handler in self.routes:
            match = pattern.match(path)
            if match is None:
                continue
            allowed.add(verb)
            if verb != method:
                continue
            params = {name: unquote(value) for name, value in match.groupdict().items()}
            return handler(query=query, raw=raw, **params)
        if allowed:
            raise ApiFailure(
                405,
                "method_not_allowed",
                f"{method} is not allowed for {path}",
                {"allowed": sorted(allowed)},
            )
        raise ApiFailure(404, "not_found", f"no route for {method} {path}")

    @staticmethod
    def _json_object(raw):
        if not raw:
            raise ApiFailure(400, "invalid_json", "request body must be a JSON object")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiFailure(400, "invalid_json", "request body is not valid JSON") from None
        if not isinstance(data, dict):
            raise validation_error("request body must be a JSON object", field="body", reason="not_an_object")
        return data

    @staticmethod
    def _reject_unknown_fields(data, allowed):
        unknown = sorted(key for key in data if key not in allowed)
        if unknown:
            raise validation_error(
                f"unknown field(s): {', '.join(unknown)}", field=unknown[0], reason="unknown_field"
            )

    @staticmethod
    def _unknown_query(query, allowed=()):
        unknown = sorted(key for key in query if key not in allowed)
        if unknown:
            raise validation_error(
                f"unknown query parameter(s): {', '.join(unknown)}",
                field=unknown[0],
                reason="unknown_parameter",
            )

    @staticmethod
    def _single_value(query, key):
        values = query.get(key)
        if values is None:
            return None
        if len(values) > 1:
            raise validation_error(
                f"query parameter {key!r} must be given at most once",
                field=key,
                reason="duplicate_parameter",
            )
        return values[0]

    def _require_card(self, card_id):
        card = self.state.get_card(card_id)
        if card is None:
            raise ApiFailure(404, "not_found", f"card {card_id!r} does not exist", {"card_id": card_id})
        return card

    @staticmethod
    def _present_card(card):
        return {field: card[field] for field in CARD_FIELDS}

    @staticmethod
    def _validate_status(value):
        if not isinstance(value, str):
            raise validation_error("status must be a string", field="status", reason="invalid_status")
        if value not in STATUSES:
            raise validation_error(f"invalid status {value!r}", field="status", reason="invalid_status")
        return value

    @staticmethod
    def _validate_card_type(value):
        if not isinstance(value, str):
            raise validation_error("card_type must be a string", field="card_type", reason="invalid_card_type")
        if value not in CARD_TYPES:
            raise validation_error(f"invalid card_type {value!r}", field="card_type", reason="invalid_card_type")
        return value

    def _validate_parent(self, card_type, parent_id):
        if parent_id is None:
            return
        parent = self.state.get_card(parent_id)
        if parent is None:
            raise ApiFailure(422, "parent_not_found", f"parent card {parent_id!r} does not exist", {"parent_id": parent_id})
        if card_type == "project_epic":
            raise validation_error("project_epic cards must be root cards", field="parent_id", reason="epic_must_be_root")
        allowed = "project_epic" if card_type == "feature" else "feature"
        if parent["card_type"] != allowed:
            raise validation_error(
                f"{card_type} parent must be a {allowed} card",
                field="parent_id",
                reason="invalid_parent_type",
            )

    def health(self, query, raw):
        self._unknown_query(query)
        return 200, JSON_HEADERS, {"status": "ok"}

    def create_card(self, query, raw):
        self._unknown_query(query)
        data = self._json_object(raw)
        self._reject_unknown_fields(data, ("title", "card_type", "status", "parent_id"))
        title = data.get("title")
        if title is None:
            raise validation_error("title is required", field="title", reason="required")
        if not isinstance(title, str):
            raise validation_error("title must be a string", field="title", reason="invalid_type")
        title = title.strip()
        if not title:
            raise validation_error("title must not be blank", field="title", reason="blank")
        card_type = self._validate_card_type(data.get("card_type"))
        status = self._validate_status(data.get("status", "TODO"))
        parent_id = data.get("parent_id")
        if parent_id is not None:
            if not isinstance(parent_id, str) or not parent_id:
                raise validation_error("parent_id must be a non-empty string", field="parent_id", reason="invalid_id")
        self._validate_parent(card_type, parent_id)
        card_id = f"new{len(self.state.order) + 1:014d}"[:16]
        self.state.cards[card_id] = {
            "id": card_id,
            "title": title,
            "card_type": card_type,
            "status": status,
            "parent_id": parent_id,
        }
        self.state.order.append(card_id)
        self.state.deps[card_id] = []
        headers = [*JSON_HEADERS, ("Location", f"/cards/{quote(card_id, safe='')}")]
        return 201, headers, {"card": self._present_card(self.state.cards[card_id])}

    def list_cards(self, query, raw):
        self._unknown_query(query, ("status", "card_type", "parent_id", "has_parent"))
        status_filter = self._single_value(query, "status")
        card_type_filter = self._single_value(query, "card_type")
        parent_filter = self._single_value(query, "parent_id")
        has_parent_filter = self._single_value(query, "has_parent")
        if status_filter is not None:
            self._validate_status(status_filter)
        if card_type_filter is not None:
            self._validate_card_type(card_type_filter)
        if parent_filter is not None and not parent_filter:
            raise validation_error("parent_id must be a non-empty string", field="parent_id", reason="invalid_id")
        if has_parent_filter is not None:
            if has_parent_filter not in ("true", "false"):
                raise validation_error("has_parent must be 'true' or 'false'", field="has_parent", reason="invalid_boolean")
            has_parent_filter = has_parent_filter == "true"
        cards = [self._present_card(card) for card in self.state.list_cards()]
        if status_filter is not None:
            cards = [card for card in cards if card["status"] == status_filter]
        if card_type_filter is not None:
            cards = [card for card in cards if card["card_type"] == card_type_filter]
        if parent_filter is not None:
            cards = [card for card in cards if card["parent_id"] == parent_filter]
        if has_parent_filter is not None:
            cards = [card for card in cards if (card["parent_id"] is not None) == has_parent_filter]
        return 200, JSON_HEADERS, {"items": cards, "count": len(cards)}

    def get_card(self, card_id, query, raw):
        self._unknown_query(query)
        card = self._require_card(card_id)
        depends_on = list(self.state.deps.get(card_id, []))
        open_ids = self.state.open_dependencies(card_id)
        dependents = []
        for other in self.state.list_cards():
            if other["id"] == card_id:
                continue
            if card_id in self.state.deps.get(other["id"], []):
                dependents.append(other["id"])
        return 200, JSON_HEADERS, {
            "card": self._present_card(card),
            "depends_on": depends_on,
            "dependents": dependents,
            "open_dependencies": open_ids,
            "blocked": bool(open_ids),
        }

    def update_status(self, card_id, query, raw):
        self._unknown_query(query)
        data = self._json_object(raw)
        self._reject_unknown_fields(data, ("status",))
        if "status" not in data:
            raise validation_error("status is required", field="status", reason="required")
        target = self._validate_status(data["status"])
        card = self._require_card(card_id)
        if target in ("READY", "IN_PROGRESS") and target != card.get("status"):
            blockers = self.state.open_dependencies(card_id)
            if blockers:
                raise ApiFailure(
                    409,
                    "dependencies_not_closed",
                    f"cannot move card to {target} while dependencies are not CLOSED",
                    {"card_id": card_id, "open_dependencies": blockers},
                )
        card["status"] = target
        return 200, JSON_HEADERS, {"card": self._present_card(card)}

    def add_dependency(self, card_id, query, raw):
        self._unknown_query(query)
        data = self._json_object(raw)
        self._reject_unknown_fields(data, ("depends_on_card_id",))
        depends_on_card_id = data.get("depends_on_card_id")
        if depends_on_card_id is None:
            raise validation_error("depends_on_card_id is required", field="depends_on_card_id", reason="required")
        if not isinstance(depends_on_card_id, str) or not depends_on_card_id:
            raise validation_error(
                "depends_on_card_id must be a non-empty string",
                field="depends_on_card_id",
                reason="invalid_id",
            )
        self._require_card(card_id)
        if self.state.get_card(depends_on_card_id) is None:
            raise ApiFailure(
                422,
                "dependency_card_not_found",
                f"card {depends_on_card_id!r} does not exist",
                {"depends_on_card_id": depends_on_card_id},
            )
        existing = self.state.deps.get(card_id, [])
        if depends_on_card_id in existing:
            raise ApiFailure(
                409,
                "duplicate_dependency",
                "dependency already exists",
                {"card_id": card_id, "depends_on_card_id": depends_on_card_id},
            )
        cycle = self.state.find_cycle(card_id, depends_on_card_id)
        if cycle is not None:
            raise ApiFailure(409, "dependency_cycle", "dependency would create a cycle", {"path": cycle})
        existing.append(depends_on_card_id)
        items = list(existing)
        headers = [*JSON_HEADERS, ("Location", f"/cards/{quote(card_id, safe='')}/dependencies")]
        return 201, headers, {"items": items, "count": len(items)}

    def list_dependencies(self, card_id, query, raw):
        self._unknown_query(query)
        self._require_card(card_id)
        items = list(self.state.deps.get(card_id, []))
        return 200, JSON_HEADERS, {"items": items, "count": len(items)}

    def remove_dependency(self, card_id, depends_on_card_id, query, raw):
        self._unknown_query(query)
        self._require_card(card_id)
        if depends_on_card_id not in self.state.deps.get(card_id, []):
            raise ApiFailure(
                404,
                "dependency_not_found",
                "dependency does not exist",
                {"card_id": card_id, "depends_on_card_id": depends_on_card_id},
            )
        self.state.deps[card_id].remove(depends_on_card_id)
        return 204, [], None

    def board(self, query, raw):
        self._unknown_query(query)
        columns = {status: [] for status in STATUSES}
        for card in self.state.list_cards():
            columns[card["status"]].append(self._present_card(card))
        payload = {
            "columns": [
                {"status": status, "count": len(columns[status]), "cards": columns[status]}
                for status in STATUSES
            ]
        }
        return 200, JSON_HEADERS, payload

    def progress(self, query, raw):
        self._unknown_query(query)
        counts = {status: 0 for status in STATUSES}
        for card in self.state.list_cards():
            counts[card["status"]] += 1
        total = len(self.state.cards)
        closed = counts["CLOSED"]
        percent = round(closed * 100 / total, 2) if total else 0.0
        return 200, JSON_HEADERS, {
            "total": total,
            "by_status": counts,
            "closed_count": closed,
            "completion_percent": percent,
        }

    def _workflow_unavailable(self):
        raise ApiFailure(404, "not_found", "no route for workflow record", {})

    def mock_get_scenario(self, query, raw):
        self._unknown_query(query)
        return 200, JSON_HEADERS, self.state.snapshot()

    def mock_set_scenario(self, query, raw):
        self._unknown_query(query)
        data = self._json_object(raw)
        self._reject_unknown_fields(data, ("mode", "delay_ms", "workflow_mocks"))
        mode = data.get("mode")
        if mode is not None and mode not in ("normal", "empty", "error", "slow"):
            raise validation_error("unknown mode", field="mode", reason="invalid_type")
        if mode == "normal":
            saved_workflow = self.state.workflow_mocks
            self.state.reset()
            self.state.workflow_mocks = saved_workflow
        if mode is not None:
            self.state.mode = mode
        if "delay_ms" in data:
            delay = data["delay_ms"]
            if not isinstance(delay, int) or delay < 0 or delay > 10000:
                raise validation_error("delay_ms must be 0..10000", field="delay_ms", reason="invalid_type")
            self.state.delay_ms = delay
        if "workflow_mocks" in data:
            self.state.workflow_mocks = bool(data["workflow_mocks"])
        if self.state.mode == "empty":
            self.state.cards = {}
            self.state.order = []
            self.state.deps = {}
        return 200, JSON_HEADERS, self.state.snapshot()

    def mock_reset(self, query, raw):
        self._unknown_query(query)
        saved_workflow = self.state.workflow_mocks
        self.state.reset()
        self.state.workflow_mocks = saved_workflow
        return 200, JSON_HEADERS, self.state.snapshot()


def workflow_routes(app: MockApp):
    def evidence(self, card_id, query, raw):
        self._unknown_query(query)
        if not self.state.workflow_mocks:
            self._workflow_unavailable()
        self._require_card(card_id)
        return 200, JSON_HEADERS, {"items": EVIDENCE_FIXTURES.get(card_id, []), "synthetic": True}

    def history(self, card_id, query, raw):
        self._unknown_query(query)
        if not self.state.workflow_mocks:
            self._workflow_unavailable()
        self._require_card(card_id)
        items = HISTORY_FIXTURES.get(
            card_id,
            [{"label": "Created", "actor": "mock-planner", "at": "2026-10-01T08:00:00Z", "synthetic": True}],
        )
        return 200, JSON_HEADERS, {"items": items, "synthetic": True}

    app.routes = app.routes + (
        (re.compile(r"^/cards/(?P<card_id>[^/]+)/evidence$"), "GET", lambda query, raw, card_id: evidence(app, card_id, query, raw)),
        (re.compile(r"^/cards/(?P<card_id>[^/]+)/history$"), "GET", lambda query, raw, card_id: history(app, card_id, query, raw)),
    )
    return app


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


def main():
    parser = argparse.ArgumentParser(description="Kanban V1 mock server for frontend tests and demos.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--workflow-mocks", action="store_true")
    parser.add_argument("--static-dir", default=str(Path(__file__).resolve().parent.parent / "static"))
    args = parser.parse_args()
    static_dir = Path(args.static_dir).resolve()
    app = MockApp(static_dir, workflow_mocks=args.workflow_mocks)
    workflow_routes(app)
    httpd = make_server(
        args.host,
        args.port,
        app,
        server_class=ThreadingWSGIServer,
        handler_class=WSGIRequestHandler,
    )
    print(f"PORT={httpd.server_port}", flush=True)
    print(f"Kanban mock serving {static_dir} and the V1 contract routes", flush=True)
    print("Workflow mocks: " + ("on (synthetic)" if args.workflow_mocks else "off"), flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
