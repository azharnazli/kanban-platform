"""WSGI application implementing the Kanban V1 HTTP contract.

Stdlib only. The storage layer is injected as a CardStore (see store.py);
this module never imports a concrete adapter, a database driver, or a network
client. Routes, validation, readiness gating, and cycle rejection belong to
this HTTP layer so that any conforming adapter behaves identically.
"""

from __future__ import annotations

import json
import re
from http import HTTPStatus
from urllib.parse import parse_qs, quote, unquote

from .domain import (
    DEFAULT_STATUS,
    READINESS_GATED_STATUSES,
    STATUSES,
    ApiError,
    find_cycle,
    open_dependencies,
    validate_card_type,
    validate_id_field,
    validate_parent,
    validate_status,
    validate_title,
    validation_error,
)
from .store import CardStore

JSON_CONTENT_TYPE = ("Content-Type", "application/json")
_CARD_FIELDS = ("id", "title", "card_type", "status", "parent_id")
_FILTER_KEYS = ("status", "card_type", "parent_id", "has_parent")


def create_app(store: CardStore) -> "KanbanApp":
    """Return a WSGI callable serving the Kanban V1 HTTP API over store."""
    return KanbanApp(store)


class KanbanApp:
    """WSGI application. The injected store is the only persistence."""

    def __init__(self, store: CardStore) -> None:
        self.store = store
        self._routes = (
            (re.compile(r"^/health$"), "GET", self.health),
            (re.compile(r"^/cards$"), "POST", self.create_card),
            (re.compile(r"^/cards$"), "GET", self.list_cards),
            (re.compile(r"^/cards/(?P<card_id>[^/]+)$"), "GET", self.get_card),
            (
                re.compile(r"^/cards/(?P<card_id>[^/]+)/status$"),
                "PATCH",
                self.update_status,
            ),
            (
                re.compile(r"^/cards/(?P<card_id>[^/]+)/dependencies$"),
                "POST",
                self.add_dependency,
            ),
            (
                re.compile(r"^/cards/(?P<card_id>[^/]+)/dependencies$"),
                "GET",
                self.list_dependencies,
            ),
            (
                re.compile(
                    r"^/cards/(?P<card_id>[^/]+)/dependencies/(?P<depends_on_card_id>[^/]+)$"
                ),
                "DELETE",
                self.remove_dependency,
            ),
            (re.compile(r"^/board$"), "GET", self.board),
            (re.compile(r"^/progress$"), "GET", self.progress),
        )

    # -- WSGI plumbing -------------------------------------------------

    def __call__(self, environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "/") or "/"
        query = parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True)
        raw = self._read_body(environ)
        try:
            status, headers, payload = self._dispatch(method, path, query, raw)
        except ApiError as exc:
            status, headers, payload = exc.status, [JSON_CONTENT_TYPE], exc.to_payload()
        except Exception:
            status = 500
            headers = [JSON_CONTENT_TYPE]
            payload = {
                "error": {
                    "code": "internal_error",
                    "message": "unexpected server error",
                    "details": {},
                }
            }
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        headers = list(headers) + [("Content-Length", str(len(body)))]
        start_response(f"{status} {HTTPStatus(status).phrase}", headers)
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
        allowed = set()
        for pattern, verb, handler in self._routes:
            match = pattern.match(path)
            if match is None:
                continue
            allowed.add(verb)
            if verb != method:
                continue
            params = {name: unquote(value) for name, value in match.groupdict().items()}
            return handler(query=query, raw=raw, **params)
        if allowed:
            raise ApiError(
                405,
                "method_not_allowed",
                f"{method} is not allowed for {path}",
                {"allowed": sorted(allowed)},
            )
        raise ApiError(404, "not_found", f"no route for {method} {path}")

    # -- request helpers ------------------------------------------------

    @staticmethod
    def _json_object(raw):
        if not raw:
            raise ApiError(400, "invalid_json", "request body must be a JSON object")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError(
                400, "invalid_json", "request body is not valid JSON"
            ) from None
        if not isinstance(data, dict):
            raise validation_error(
                "request body must be a JSON object",
                field="body",
                reason="not_an_object",
            )
        return data

    @staticmethod
    def _reject_unknown_fields(data, allowed):
        unknown = sorted(key for key in data if key not in allowed)
        if unknown:
            raise validation_error(
                f"unknown field(s): {', '.join(unknown)}",
                field=unknown[0],
                reason="unknown_field",
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

    def _reject_query(self, query):
        self._unknown_query(query)

    def _require_card(self, card_id):
        card = self.store.get_card(card_id)
        if card is None:
            raise ApiError(
                404,
                "not_found",
                f"card {card_id!r} does not exist",
                {"card_id": card_id},
            )
        return card

    @staticmethod
    def _present_card(card):
        missing = [field for field in _CARD_FIELDS if field not in card]
        if missing:
            raise ApiError(
                500,
                "store_contract_violation",
                f"store returned a card missing field(s): {', '.join(missing)}",
                {"missing_fields": missing},
            )
        return {field: card[field] for field in _CARD_FIELDS}

    def _dependency_open(self, dep_id):
        dep = self.store.get_card(dep_id)
        return dep is None or dep.get("status") != "CLOSED"

    # -- handlers --------------------------------------------------------

    def health(self, query, raw):
        self._reject_query(query)
        return 200, [JSON_CONTENT_TYPE], {"status": "ok"}

    def create_card(self, query, raw):
        self._reject_query(query)
        data = self._json_object(raw)
        self._reject_unknown_fields(
            data, ("title", "card_type", "status", "parent_id")
        )
        title = validate_title(data.get("title"))
        card_type = validate_card_type(data.get("card_type"))
        status = data.get("status", DEFAULT_STATUS)
        validate_status(status)
        parent_id = validate_id_field(
            data.get("parent_id"), field="parent_id", required=False
        )
        validate_parent(self.store, card_type, parent_id)
        card_id = self.store.create_card(
            title, card_type, status=status, parent_id=parent_id
        )
        card = self._present_card(self._require_card(card_id))
        headers = [
            JSON_CONTENT_TYPE,
            ("Location", f"/cards/{quote(card['id'], safe='')}"),
        ]
        return 201, headers, {"card": card}

    def list_cards(self, query, raw):
        self._unknown_query(query, _FILTER_KEYS)
        status_filter = self._single_value(query, "status")
        card_type_filter = self._single_value(query, "card_type")
        parent_filter = self._single_value(query, "parent_id")
        has_parent_filter = self._single_value(query, "has_parent")
        if status_filter is not None:
            validate_status(status_filter)
        if card_type_filter is not None:
            validate_card_type(card_type_filter)
        if parent_filter is not None and not parent_filter:
            raise validation_error(
                "parent_id must be a non-empty string",
                field="parent_id",
                reason="invalid_id",
            )
        if has_parent_filter is not None:
            if has_parent_filter not in ("true", "false"):
                raise validation_error(
                    "has_parent must be 'true' or 'false'",
                    field="has_parent",
                    reason="invalid_boolean",
                )
            has_parent_filter = has_parent_filter == "true"
        cards = [self._present_card(card) for card in self.store.list_cards()]
        if status_filter is not None:
            cards = [card for card in cards if card["status"] == status_filter]
        if card_type_filter is not None:
            cards = [card for card in cards if card["card_type"] == card_type_filter]
        if parent_filter is not None:
            cards = [card for card in cards if card["parent_id"] == parent_filter]
        if has_parent_filter is not None:
            cards = [
                card
                for card in cards
                if (card["parent_id"] is not None) == has_parent_filter
            ]
        return 200, [JSON_CONTENT_TYPE], {"items": cards, "count": len(cards)}

    def get_card(self, card_id, query, raw):
        self._reject_query(query)
        card = self._present_card(self._require_card(card_id))
        depends_on = list(self.store.list_dependencies(card["id"]))
        blockers = open_dependencies(self.store, card["id"])
        dependents = []
        for other in self.store.list_cards():
            other_id = other.get("id")
            if other_id == card["id"]:
                continue
            if card["id"] in set(self.store.list_dependencies(other_id)):
                dependents.append(other_id)
        payload = {
            "card": card,
            "depends_on": depends_on,
            "dependents": dependents,
            "open_dependencies": blockers,
            "blocked": bool(blockers),
        }
        return 200, [JSON_CONTENT_TYPE], payload

    def update_status(self, card_id, query, raw):
        self._reject_query(query)
        data = self._json_object(raw)
        self._reject_unknown_fields(data, ("status",))
        if "status" not in data:
            raise validation_error("status is required", field="status", reason="required")
        target = validate_status(data["status"])
        card = self._require_card(card_id)
        if target in READINESS_GATED_STATUSES and target != card.get("status"):
            blockers = open_dependencies(self.store, card_id)
            if blockers:
                raise ApiError(
                    409,
                    "dependencies_not_closed",
                    f"cannot move card to {target} while dependencies are not CLOSED",
                    {"card_id": card_id, "open_dependencies": blockers},
                )
        try:
            self.store.update_status(card_id, target)
        except KeyError:
            raise ApiError(
                404,
                "not_found",
                f"card {card_id!r} does not exist",
                {"card_id": card_id},
            ) from None
        card = self._present_card(self._require_card(card_id))
        return 200, [JSON_CONTENT_TYPE], {"card": card}

    def add_dependency(self, card_id, query, raw):
        self._reject_query(query)
        data = self._json_object(raw)
        self._reject_unknown_fields(data, ("depends_on_card_id",))
        depends_on_card_id = validate_id_field(
            data.get("depends_on_card_id"), field="depends_on_card_id", required=True
        )
        self._require_card(card_id)
        if self.store.get_card(depends_on_card_id) is None:
            raise ApiError(
                422,
                "dependency_card_not_found",
                f"card {depends_on_card_id!r} does not exist",
                {"depends_on_card_id": depends_on_card_id},
            )
        existing = list(self.store.list_dependencies(card_id))
        if depends_on_card_id in existing:
            raise ApiError(
                409,
                "duplicate_dependency",
                "dependency already exists",
                {"card_id": card_id, "depends_on_card_id": depends_on_card_id},
            )
        cycle = find_cycle(self.store, card_id, depends_on_card_id)
        if cycle is not None:
            raise ApiError(
                409,
                "dependency_cycle",
                "dependency would create a cycle",
                {"path": cycle},
            )
        try:
            self.store.add_dependency(card_id, depends_on_card_id)
        except KeyError:
            raise ApiError(
                404,
                "not_found",
                f"card {card_id!r} does not exist",
                {"card_id": card_id},
            ) from None
        items = list(self.store.list_dependencies(card_id))
        headers = [
            JSON_CONTENT_TYPE,
            ("Location", f"/cards/{quote(card_id, safe='')}/dependencies"),
        ]
        return 201, headers, {"items": items, "count": len(items)}

    def list_dependencies(self, card_id, query, raw):
        self._reject_query(query)
        self._require_card(card_id)
        items = list(self.store.list_dependencies(card_id))
        return 200, [JSON_CONTENT_TYPE], {"items": items, "count": len(items)}

    def remove_dependency(self, card_id, depends_on_card_id, query, raw):
        self._reject_query(query)
        self._require_card(card_id)
        if depends_on_card_id not in set(self.store.list_dependencies(card_id)):
            raise ApiError(
                404,
                "dependency_not_found",
                "dependency does not exist",
                {"card_id": card_id, "depends_on_card_id": depends_on_card_id},
            )
        try:
            self.store.remove_dependency(card_id, depends_on_card_id)
        except KeyError:
            raise ApiError(
                404,
                "dependency_not_found",
                "dependency does not exist",
                {"card_id": card_id, "depends_on_card_id": depends_on_card_id},
            ) from None
        return 204, [], None

    def board(self, query, raw):
        self._reject_query(query)
        columns = {status: [] for status in STATUSES}
        for card in self.store.list_cards():
            presented = self._present_card(card)
            status = presented["status"]
            if status not in columns:
                raise ApiError(
                    500,
                    "store_contract_violation",
                    f"store returned card with non-canonical status {status!r}",
                    {"card_id": presented["id"], "status": status},
                )
            columns[status].append(presented)
        payload = {
            "columns": [
                {"status": status, "count": len(columns[status]), "cards": columns[status]}
                for status in STATUSES
            ]
        }
        return 200, [JSON_CONTENT_TYPE], payload

    def progress(self, query, raw):
        self._reject_query(query)
        counts = {status: 0 for status in STATUSES}
        total = 0
        for card in self.store.list_cards():
            presented = self._present_card(card)
            status = presented["status"]
            if status not in counts:
                raise ApiError(
                    500,
                    "store_contract_violation",
                    f"store returned card with non-canonical status {status!r}",
                    {"card_id": presented["id"], "status": status},
                )
            counts[status] += 1
            total += 1
        closed = counts["CLOSED"]
        percent = round(closed * 100 / total, 2) if total else 0.0
        return 200, [JSON_CONTENT_TYPE], {
            "total": total,
            "by_status": counts,
            "closed_count": closed,
            "completion_percent": percent,
        }
