"""Reference in-memory CardStore double.

This is NOT the production adapter and NOT durable storage. It exists so the
HTTP layer can run and be tested in isolation, with zero database or network
involvement.
"""

from __future__ import annotations

from uuid import uuid4


class InMemoryCardStore:
    """Minimal, order-preserving CardStore implementation for tests and demos."""

    def __init__(self, dsn=None):
        self.dsn = dsn
        self._cards = {}
        self._dependencies = {}
        self._closed = False

    def _check_open(self):
        if self._closed:
            raise RuntimeError("InMemoryCardStore is closed")

    def create_card(
        self,
        title,
        card_type,
        status="TODO",
        parent_id=None,
        *,
        owner=None,
        priority=None,
        requirements=None,
        blockers=None,
    ):
        self._check_open()
        card_id = uuid4().hex
        self._cards[card_id] = {
            "id": card_id,
            "title": title,
            "card_type": card_type,
            "status": status,
            "parent_id": parent_id,
            "owner": owner,
            "priority": priority,
            "requirements": list(requirements) if requirements else [],
            "blockers": list(blockers) if blockers else [],
        }
        self._dependencies[card_id] = []
        return card_id

    def get_card(self, card_id):
        self._check_open()
        card = self._cards.get(card_id)
        return dict(card) if card is not None else None

    def list_cards(self):
        self._check_open()
        return [dict(card) for card in self._cards.values()]

    def update_card_fields(self, card_id, **fields):
        self._check_open()
        card = self._cards.get(card_id)
        if card is None:
            raise KeyError(card_id)
        for name, value in fields.items():
            if name in ("requirements", "blockers"):
                card[name] = list(value) if value else []
            else:
                card[name] = value

    def update_status(self, card_id, status):
        self._check_open()
        card = self._cards.get(card_id)
        if card is None:
            raise KeyError(card_id)
        card["status"] = status

    def add_dependency(self, card_id, depends_on_card_id):
        self._check_open()
        if card_id not in self._cards:
            raise KeyError(card_id)
        if depends_on_card_id not in self._cards:
            raise KeyError(depends_on_card_id)
        deps = self._dependencies[card_id]
        if depends_on_card_id in deps:
            raise ValueError(
                f"dependency already exists: {card_id} -> {depends_on_card_id}"
            )
        deps.append(depends_on_card_id)

    def remove_dependency(self, card_id, depends_on_card_id):
        self._check_open()
        deps = self._dependencies.get(card_id)
        if deps is None:
            raise KeyError(card_id)
        try:
            deps.remove(depends_on_card_id)
        except ValueError:
            raise KeyError(depends_on_card_id) from None

    def list_dependencies(self, card_id):
        self._check_open()
        deps = self._dependencies.get(card_id)
        if deps is None:
            raise KeyError(card_id)
        return list(deps)

    def close(self):
        self._closed = True
