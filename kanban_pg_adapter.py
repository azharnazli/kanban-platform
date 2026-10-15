"""Integration glue: expose kanban_pg.CardStore under the HTTP CardStore contract.

This module is purely additive. It does not modify the approved HTTP API
(kanban_http/, one copy) or the approved PostgreSQL adapter (kanban_pg.py);
it only translates between the two interfaces:

- ids: opaque HTTP strings <-> PostgreSQL integer ids (str on the way out,
  int on the way in; malformed strings are treated as unknown ids)
- field name: card_type <-> type
- shape: kanban_pg.Card dataclass <-> the dict shape from kanban_http.store
- persisted card fields: owner, priority, requirements, and blockers are
  passed through verbatim in both directions (requirements/blockers as text
  arrays in PostgreSQL, lists in the HTTP shape); blockers stays a free-form
  card field and never affects dependency-derived blocking
- errors: kanban_pg raises ValidationError (a ValueError) for unknown ids,
  while the CardStore contract requires KeyError; duplicate dependency edges
  remain ValueError, which the contract explicitly allows because the HTTP
  layer pre-checks them before calling the store
- dependency order: the CardStore contract requires insertion order; the
  adapter returns edges ordered by depends_on_card_id, so this glue restores
  insertion order using the created_at timestamp it already persists
- evidence journal: add_evidence/list_evidence implement the optional
  EvidenceStore extension from kanban_http.domain over the additive
  card_evidence table (evidence_schema.sql, applied at startup, independent
  of schema.sql and kanban_pg.py). Acceptance criteria, reviews, and
  integration evidence therefore persist across adapter restarts and the
  APPROVED/MERGED/CLOSED gates work durably.
- dependency cycles: insertion is serialized through a PostgreSQL session
  advisory lock inside kanban_pg.CardStore.add_dependency, the shared write
  path used by this adapter and by direct CardStore users alike. Writers
  sharing a database therefore cannot both pass their read-then-write cycle
  checks, no matter which interface they use.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from kanban_pg import Card
from kanban_pg import CardStore as PgCardStore

_EVIDENCE_SCHEMA_PATH = Path(__file__).with_name("evidence_schema.sql")


class PgCardStoreAdapter:
    """CardStore (kanban_http contract) backed by kanban_pg.CardStore."""

    def __init__(self, dsn: str):
        self._store = PgCardStore(dsn)
        self._conn = None
        try:
            self._conn = psycopg.connect(
                dsn, autocommit=True, row_factory=dict_row
            )
            self._conn.execute(
                _EVIDENCE_SCHEMA_PATH.read_text(encoding="utf-8")
            )
        except Exception:
            self.close()
            raise

    def create_card(
        self,
        title: str,
        card_type: str,
        status: str = "TODO",
        parent_id: Optional[str] = None,
        *,
        owner: Optional[str] = None,
        priority: Optional[str] = None,
        requirements: Optional[List[str]] = None,
        blockers: Optional[List[str]] = None,
    ) -> str:
        card = self._store.create_card(
            title,
            card_type,
            status=status,
            parent_id=None if parent_id is None else _require_pg_id(parent_id),
            owner=owner,
            priority=priority,
            requirements=requirements,
            blockers=blockers,
        )
        return str(card.id)

    def get_card(self, card_id: str) -> Optional[dict]:
        pg_id = _maybe_pg_id(card_id)
        if pg_id is None:
            return None
        card = self._store.get_card(pg_id)
        return None if card is None else _card_to_dict(card)

    def list_cards(self) -> List[dict]:
        return [_card_to_dict(card) for card in self._store.list_cards()]

    def update_card_fields(self, card_id: str, **fields) -> None:
        pg_id = _require_pg_id(card_id)
        if self._store.get_card(pg_id) is None:
            raise KeyError(card_id)
        self._store.update_card_fields(pg_id, **fields)

    def update_status(self, card_id: str, status: str) -> None:
        pg_id = _require_pg_id(card_id)
        if self._store.get_card(pg_id) is None:
            raise KeyError(card_id)
        self._store.update_status(pg_id, status)

    def add_dependency(self, card_id: str, depends_on_card_id: str) -> None:
        pg_card_id = _require_pg_id(card_id)
        pg_depends_on_id = _require_pg_id(depends_on_card_id)
        if self._store.get_card(pg_card_id) is None:
            raise KeyError(card_id)
        if self._store.get_card(pg_depends_on_id) is None:
            raise KeyError(depends_on_card_id)
        self._store.add_dependency(pg_card_id, pg_depends_on_id)

    def remove_dependency(self, card_id: str, depends_on_card_id: str) -> None:
        pg_card_id = _require_pg_id(card_id)
        pg_depends_on_id = _require_pg_id(depends_on_card_id)
        if self._store.get_card(pg_card_id) is None:
            raise KeyError(card_id)
        if not self._store.remove_dependency(pg_card_id, pg_depends_on_id):
            raise KeyError(depends_on_card_id)

    def list_dependencies(self, card_id: str) -> List[str]:
        pg_id = _require_pg_id(card_id)
        if self._store.get_card(pg_id) is None:
            raise KeyError(card_id)
        edges = sorted(
            self._store.list_dependencies(pg_id), key=lambda edge: edge.created_at
        )
        return [str(edge.depends_on_card_id) for edge in edges]

    def add_evidence(self, card_id: str, kind: str, record: dict) -> str:
        pg_id = _require_pg_id(card_id)
        if self._store.get_card(pg_id) is None:
            raise KeyError(card_id)
        row = self._conn.execute(
            "INSERT INTO card_evidence (card_id, kind, record) "
            "VALUES (%s, %s, %s) RETURNING id",
            (pg_id, kind, Jsonb(record)),
        ).fetchone()
        return str(row["id"])

    def list_evidence(
        self, card_id: str, kind: Optional[str] = None
    ) -> List[dict]:
        pg_id = _require_pg_id(card_id)
        if self._store.get_card(pg_id) is None:
            raise KeyError(card_id)
        sql = "SELECT id, record FROM card_evidence WHERE card_id = %s"
        params: List = [pg_id]
        if kind is not None:
            sql += " AND kind = %s"
            params.append(kind)
        sql += " ORDER BY id"
        rows = self._conn.execute(sql, params).fetchall()
        items = []
        for row in rows:
            item = dict(row["record"])
            item["id"] = str(row["id"])
            items.append(item)
        return items

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        self._store.close()

    def __enter__(self) -> "PgCardStoreAdapter":
        return self

    def __exit__(self, *exc_info) -> bool:
        self.close()
        return False


def _card_to_dict(card: Card) -> dict:
    return {
        "id": str(card.id),
        "title": card.title,
        "card_type": card.type,
        "status": card.status,
        "parent_id": None if card.parent_id is None else str(card.parent_id),
        "owner": card.owner,
        "priority": card.priority,
        "requirements": list(card.requirements),
        "blockers": list(card.blockers),
    }


def _maybe_pg_id(card_id) -> Optional[int]:
    try:
        return int(card_id)
    except (TypeError, ValueError):
        return None


def _require_pg_id(card_id) -> int:
    pg_id = _maybe_pg_id(card_id)
    if pg_id is None:
        raise KeyError(card_id)
    return pg_id
