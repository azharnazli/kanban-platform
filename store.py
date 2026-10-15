"""CardStore: the injectable storage contract expected by the HTTP layer.

This worktree does NOT bundle the production adapter. The adapter lives on a
separate, unapproved branch (v1-postgres) and nothing here imports or claims
integration with it. The HTTP layer only relies on the methods and shapes
documented below; tests use the in-memory reference double in memory.py.
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class CardStore(Protocol):
    """Storage contract consumed by the Kanban V1 HTTP layer.

    Card shape returned by get_card/list_cards (at least these keys):

        {
            "id": str,
            "title": str,
            "card_type": "project_epic" | "feature" | "task",
            "status": canonical Kanban status,
            "parent_id": str | None,
        }

    Error and return contract:
      - create_card returns the new card id.
      - get_card returns None for unknown ids.
      - update_status / add_dependency / remove_dependency / list_dependencies
        raise KeyError for unknown card ids.
      - list_dependencies(card_id) returns dependency ids in insertion order.
      - Mutating methods return None.
      - The store does not enforce hierarchy, status-transition, or cycle
        rules; those live in the HTTP layer (see domain.py) so that every
        adapter behaves identically.
      - Behavior after close() is undefined; callers must not reuse a closed
        store.
    """

    def create_card(
        self,
        title: str,
        card_type: str,
        status: str = "TODO",
        parent_id: Optional[str] = None,
    ) -> str:
        """Persist a new card and return its id."""
        ...

    def get_card(self, card_id: str) -> Optional[dict]:
        """Return the card with card_id, or None if unknown."""
        ...

    def list_cards(self) -> list[dict]:
        """Return all cards."""
        ...

    def update_status(self, card_id: str, status: str) -> None:
        """Set the card's status. Raises KeyError for unknown cards."""
        ...

    def add_dependency(self, card_id: str, depends_on_card_id: str) -> None:
        """Record that card_id depends on depends_on_card_id."""
        ...

    def remove_dependency(self, card_id: str, depends_on_card_id: str) -> None:
        """Remove the dependency edge; raises KeyError if absent."""
        ...

    def list_dependencies(self, card_id: str) -> list[str]:
        """Return the ids card_id depends on."""
        ...

    def close(self) -> None:
        """Release resources associated with the store."""
        ...
