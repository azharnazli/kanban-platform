"""PostgreSQL-backed card persistence for the kanban platform.

Hierarchy follows the corrected contract: project_epic -> feature -> task,
with standalone features and tasks allowed (no parent). Cards carry one of
the nine workflow statuses in STATUSES. Hierarchy parent links are stored on
cards.parent_id; dependency edges are stored separately in
card_dependencies and never mix with hierarchy links.

schema.sql enforces all of this at the storage layer; this store validates
the same rules up front to raise clean ValidationError messages. Dependency
cycles, direct or transitive, are rejected by the store as well. Every
dependency insert is serialized through a PostgreSQL session advisory lock
inside add_dependency, so all writers sharing a database - direct CardStore
users and adapters alike - cannot both pass their read-then-write cycle
checks and commit opposing edges.

Cards also carry the persisted V1 fields owner, priority, requirements, and
blockers. owner and priority are optional opaque non-empty strings;
requirements and blockers are ordered lists of unique non-empty strings,
stored as text arrays. blockers is a free-form card field and never affects
dependency-derived blocking (open_dependencies / blocked) or any workflow
gate.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Tuple

import psycopg
from psycopg.rows import dict_row

CARD_TYPES: Tuple[str, ...] = ("project_epic", "feature", "task")

STATUSES: Tuple[str, ...] = (
    "TODO",
    "READY",
    "IN_PROGRESS",
    "REVIEW",
    "CHANGES_REQUESTED",
    "APPROVED",
    "MERGED",
    "CLOSED",
    "BLOCKED",
)

PARENT_TYPES: Dict[str, FrozenSet[str]] = {
    "project_epic": frozenset(),
    "feature": frozenset({"project_epic"}),
    "task": frozenset({"feature"}),
}

DEFAULT_STATUS = "TODO"
MAX_TITLE_LENGTH = 200

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")

_CARD_COLUMNS = (
    "id, title, type, status, parent_id, owner, priority, "
    "requirements, blockers, created_at"
)

# Persisted card fields beyond the original five. HTTP clients may provide
# them at creation and change them through update_card_fields.
CARD_FIELDS = ("owner", "priority", "requirements", "blockers")

# Session advisory-lock key ("kanban_dep_edges" reduced to fit PostgreSQL's
# signed 64-bit advisory-lock namespace) that serializes dependency inserts
# across every CardStore connection sharing a database.
_DEPENDENCY_LOCK_KEY = int.from_bytes(b"kanban_dep_edges", "big") % 2**63


class ValidationError(ValueError):
    """Raised when card data fails validation."""


@dataclass(frozen=True)
class Card:
    id: int
    title: str
    type: str
    status: str
    parent_id: Optional[int]
    created_at: datetime
    owner: Optional[str] = None
    priority: Optional[str] = None
    requirements: Tuple[str, ...] = ()
    blockers: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Dependency:
    card_id: int
    depends_on_card_id: int
    created_at: datetime


class CardStore:
    """Persist kanban cards and dependency edges in PostgreSQL."""

    def __init__(self, dsn: str):
        self._conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
        self._conn.execute(_SCHEMA_PATH.read_text(encoding="utf-8"))

    def create_card(
        self,
        title: str,
        card_type: str,
        status: str = DEFAULT_STATUS,
        parent_id: Optional[int] = None,
        *,
        owner: Optional[str] = None,
        priority: Optional[str] = None,
        requirements: Optional[List[str]] = None,
        blockers: Optional[List[str]] = None,
    ) -> Card:
        clean_title = self._validate_title(title)
        self._validate_type(card_type)
        self._validate_status(status)
        self._validate_parent(card_type, parent_id)
        clean_owner = self._validate_optional_text(owner, "owner")
        clean_priority = self._validate_optional_text(priority, "priority")
        clean_requirements = self._validate_items(requirements, "requirements")
        clean_blockers = self._validate_items(blockers, "blockers")

        try:
            row = self._conn.execute(
                f"INSERT INTO cards (title, type, status, parent_id, owner, "
                f"priority, requirements, blockers) "
                f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                f"RETURNING {_CARD_COLUMNS}",
                (
                    clean_title,
                    card_type,
                    status,
                    parent_id,
                    clean_owner,
                    clean_priority,
                    list(clean_requirements),
                    list(clean_blockers),
                ),
            ).fetchone()
        except psycopg.errors.IntegrityError as exc:
            raise ValidationError(_integrity_message(exc)) from exc
        return _row_to_card(row)

    def update_card_fields(self, card_id: int, **fields) -> Card:
        """Update the persisted V1 card fields given as keyword arguments.

        Only the fields present in `fields` change; owner/priority accept
        None to clear them, requirements/blockers accept None or an empty
        list to clear them. Raises ValidationError (a ValueError) for
        unknown field names, invalid values, or an unknown card.
        """
        unknown = sorted(name for name in fields if name not in CARD_FIELDS)
        if unknown:
            raise ValidationError(
                f"unknown card field(s): {', '.join(unknown)}; "
                f"expected one of {', '.join(CARD_FIELDS)}"
            )
        if not fields:
            raise ValidationError("at least one card field must be provided")

        assignments = []
        params: List = []
        if "owner" in fields:
            assignments.append("owner = %s")
            params.append(self._validate_optional_text(fields["owner"], "owner"))
        if "priority" in fields:
            assignments.append("priority = %s")
            params.append(
                self._validate_optional_text(fields["priority"], "priority")
            )
        if "requirements" in fields:
            assignments.append("requirements = %s")
            params.append(
                list(self._validate_items(fields["requirements"], "requirements"))
            )
        if "blockers" in fields:
            assignments.append("blockers = %s")
            params.append(
                list(self._validate_items(fields["blockers"], "blockers"))
            )

        try:
            row = self._conn.execute(
                f"UPDATE cards SET {', '.join(assignments)} WHERE id = %s "
                f"RETURNING {_CARD_COLUMNS}",
                (*params, card_id),
            ).fetchone()
        except psycopg.errors.IntegrityError as exc:
            raise ValidationError(_integrity_message(exc)) from exc
        if row is None:
            raise ValidationError(f"card {card_id} does not exist")
        return _row_to_card(row)

    def get_card(self, card_id: int) -> Optional[Card]:
        row = self._conn.execute(
            f"SELECT {_CARD_COLUMNS} FROM cards WHERE id = %s", (card_id,)
        ).fetchone()
        return None if row is None else _row_to_card(row)

    def list_cards(self) -> List[Card]:
        rows = self._conn.execute(
            f"SELECT {_CARD_COLUMNS} FROM cards ORDER BY id"
        ).fetchall()
        return [_row_to_card(row) for row in rows]

    def update_status(self, card_id: int, status: str) -> Card:
        self._validate_status(status)
        try:
            row = self._conn.execute(
                f"UPDATE cards SET status = %s WHERE id = %s "
                f"RETURNING {_CARD_COLUMNS}",
                (status, card_id),
            ).fetchone()
        except psycopg.errors.IntegrityError as exc:
            raise ValidationError(_integrity_message(exc)) from exc
        if row is None:
            raise ValidationError(f"card {card_id} does not exist")
        return _row_to_card(row)

    def add_dependency(self, card_id: int, depends_on_card_id: int) -> Dependency:
        for value, label in ((card_id, "card_id"), (depends_on_card_id, "depends_on_card_id")):
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValidationError(f"{label} must be an integer")
        if card_id == depends_on_card_id:
            raise ValidationError("a card cannot depend on itself")
        self._conn.execute(
            "SELECT pg_advisory_lock(%s::bigint)", (_DEPENDENCY_LOCK_KEY,)
        )
        try:
            for value, label in ((card_id, "card"), (depends_on_card_id, "depends-on card")):
                if self.get_card(value) is None:
                    raise ValidationError(f"{label} {value} does not exist")
            if self._would_create_cycle(card_id, depends_on_card_id):
                raise ValidationError(
                    f"card {card_id} cannot depend on card {depends_on_card_id}: "
                    f"that would create a dependency cycle"
                )
            try:
                row = self._conn.execute(
                    "INSERT INTO card_dependencies (card_id, depends_on_card_id) "
                    "VALUES (%s, %s) "
                    "RETURNING card_id, depends_on_card_id, created_at",
                    (card_id, depends_on_card_id),
                ).fetchone()
            except psycopg.errors.UniqueViolation as exc:
                raise ValidationError(
                    f"card {card_id} already depends on {depends_on_card_id}"
                ) from exc
            except psycopg.errors.IntegrityError as exc:
                raise ValidationError(_integrity_message(exc)) from exc
            return Dependency(
                card_id=row["card_id"],
                depends_on_card_id=row["depends_on_card_id"],
                created_at=row["created_at"],
            )
        finally:
            self._conn.execute(
                "SELECT pg_advisory_unlock(%s::bigint)", (_DEPENDENCY_LOCK_KEY,)
            )

    def remove_dependency(self, card_id: int, depends_on_card_id: int) -> bool:
        cursor = self._conn.execute(
            "DELETE FROM card_dependencies "
            "WHERE card_id = %s AND depends_on_card_id = %s",
            (card_id, depends_on_card_id),
        )
        return cursor.rowcount > 0

    def list_dependencies(self, card_id: int) -> List[Dependency]:
        rows = self._conn.execute(
            "SELECT card_id, depends_on_card_id, created_at "
            "FROM card_dependencies WHERE card_id = %s "
            "ORDER BY depends_on_card_id",
            (card_id,),
        ).fetchall()
        return [
            Dependency(
                card_id=row["card_id"],
                depends_on_card_id=row["depends_on_card_id"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "CardStore":
        return self

    def __exit__(self, *exc_info) -> bool:
        self.close()
        return False

    @staticmethod
    def _validate_title(title: str) -> str:
        if not isinstance(title, str):
            raise ValidationError("title must be a string")
        clean = title.strip()
        if not clean:
            raise ValidationError("title must not be blank")
        if len(clean) > MAX_TITLE_LENGTH:
            raise ValidationError(
                f"title must be at most {MAX_TITLE_LENGTH} characters"
            )
        return clean

    @staticmethod
    def _validate_type(card_type: str) -> None:
        if card_type not in CARD_TYPES:
            raise ValidationError(
                f"invalid card type: {card_type!r}; "
                f"expected one of {', '.join(CARD_TYPES)}"
            )

    @staticmethod
    def _validate_status(status: str) -> None:
        if status not in STATUSES:
            raise ValidationError(
                f"invalid status: {status!r}; "
                f"expected one of {', '.join(STATUSES)}"
            )

    @staticmethod
    def _validate_optional_text(value: Optional[str], label: str) -> Optional[str]:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValidationError(f"{label} must be a string or None")
        clean = value.strip()
        if not clean:
            raise ValidationError(f"{label} must not be blank")
        return clean

    @staticmethod
    def _validate_items(
        value: Optional[List[str]], label: str
    ) -> Tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)):
            raise ValidationError(
                f"{label} must be a list of unique non-empty strings or None"
            )
        seen = set()
        items = []
        for item in value:
            if not isinstance(item, str):
                raise ValidationError(
                    f"{label} must be a list of unique non-empty strings or None"
                )
            clean = item.strip()
            if not clean:
                raise ValidationError(
                    f"{label} must be a list of unique non-empty strings or None"
                )
            if clean in seen:
                raise ValidationError(f"{label} must contain unique entries")
            seen.add(clean)
            items.append(clean)
        return tuple(items)

    def _would_create_cycle(self, card_id: int, depends_on_card_id: int) -> bool:
        row = self._conn.execute(
            "WITH RECURSIVE transitive(card_id) AS ("
            "    SELECT %s::bigint"
            "    UNION"
            "    SELECT d.depends_on_card_id"
            "    FROM card_dependencies d"
            "    JOIN transitive t ON d.card_id = t.card_id"
            ")"
            " SELECT 1 FROM transitive WHERE card_id = %s LIMIT 1",
            (depends_on_card_id, card_id),
        ).fetchone()
        return row is not None

    def _validate_parent(self, card_type: str, parent_id: Optional[int]) -> None:
        if parent_id is None:
            return
        if not isinstance(parent_id, int) or isinstance(parent_id, bool):
            raise ValidationError("parent_id must be an integer or None")
        allowed = PARENT_TYPES[card_type]
        if not allowed:
            raise ValidationError(f"{card_type} cards cannot have a parent")
        parent = self.get_card(parent_id)
        if parent is None:
            raise ValidationError(f"parent card {parent_id} does not exist")
        if parent.type not in allowed:
            raise ValidationError(
                f"a {card_type} cannot have a {parent.type} parent; "
                f"expected {', '.join(sorted(allowed))}"
            )


def _integrity_message(exc: psycopg.Error) -> str:
    return exc.diag.message_primary or str(exc)


def _row_to_card(row: dict) -> Card:
    return Card(
        id=row["id"],
        title=row["title"],
        type=row["type"],
        status=row["status"],
        parent_id=row["parent_id"],
        owner=row["owner"],
        priority=row["priority"],
        requirements=tuple(row["requirements"] or ()),
        blockers=tuple(row["blockers"] or ()),
        created_at=row["created_at"],
    )
