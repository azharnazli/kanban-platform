"""Focused tests for the persisted V1 card fields on the HTTP layer.

Covers owner, priority, requirements, and blockers: request validation,
create and update round-trips, clearing, API compatibility for cards that
leave the fields unset, and the independence of the free-form blockers field
from dependency-derived blocking and the workflow gates.

Everything runs against the in-memory reference store: no sockets, no
database, no docker.
"""

import unittest

from kanban_http.memory import InMemoryCardStore

from .support import make_app, request

CORE_FIELDS = {"id", "title", "card_type", "status", "parent_id"}


class CardFieldsHttpTest(unittest.TestCase):
    def setUp(self):
        self.store = InMemoryCardStore()
        self.app = make_app(self.store)

    def create(self, **payload):
        payload.setdefault("title", "Card")
        payload.setdefault("card_type", "task")
        resp = request(self.app, "POST", "/cards", body=payload)
        self.assertEqual(resp.status, 201, resp.raw)
        return resp.json["card"]

    def patch(self, card_id, body):
        return request(self.app, "PATCH", f"/cards/{card_id}", body=body)

    def detail(self, card_id):
        resp = request(self.app, "GET", f"/cards/{card_id}")
        self.assertEqual(resp.status, 200, resp.raw)
        return resp.json

    def assert_error(self, resp, status, code, *, field=None, reason=None):
        self.assertEqual(resp.status, status, resp.raw)
        self.assertEqual(resp.json["error"]["code"], code, resp.raw)
        details = resp.json["error"]["details"]
        if field is not None:
            self.assertEqual(details.get("field"), field, resp.raw)
        if reason is not None:
            self.assertEqual(details.get("reason"), reason, resp.raw)
        return details

    def assert_optional_fields(self, card, **expected):
        for name, value in expected.items():
            self.assertIn(name, card, card)
            self.assertEqual(card[name], value, card)

    # -- round-trips ----------------------------------------------------

    def test_create_roundtrips_all_fields_in_every_read_view(self):
        card = self.create(
            title="Ship parser",
            owner="alice",
            priority="high",
            requirements=["parse feed", "emit JSON"],
            blockers=["vendor access"],
        )
        expected = {
            "owner": "alice",
            "priority": "high",
            "requirements": ["parse feed", "emit JSON"],
            "blockers": ["vendor access"],
        }
        self.assertEqual(set(card), CORE_FIELDS | set(expected))
        self.assert_optional_fields(card, **expected)

        listed = request(self.app, "GET", "/cards")
        self.assertEqual(listed.status, 200, listed.raw)
        self.assertEqual(listed.json["count"], 1)
        self.assert_optional_fields(listed.json["items"][0], **expected)

        self.assert_optional_fields(self.detail(card["id"])["card"], **expected)

        board = request(self.app, "GET", "/board")
        self.assertEqual(board.status, 200, board.raw)
        column = next(
            column for column in board.json["columns"] if column["status"] == "TODO"
        )
        self.assert_optional_fields(column["cards"][0], **expected)

    def test_unset_fields_are_omitted_for_compatibility(self):
        card = self.create(title="Plain")
        self.assertEqual(set(card), CORE_FIELDS)
        self.assertEqual(set(self.detail(card["id"])["card"]), CORE_FIELDS)

    def test_create_trims_text_and_list_entries(self):
        card = self.create(
            owner="  alice  ",
            priority="  high ",
            requirements=["  one  ", "two"],
            blockers=["  waiting  "],
        )
        self.assert_optional_fields(
            card,
            owner="alice",
            priority="high",
            requirements=["one", "two"],
            blockers=["waiting"],
        )

    def test_create_accepts_empty_lists_as_unset(self):
        card = self.create(requirements=[], blockers=[])
        self.assertEqual(set(card), CORE_FIELDS)

    def test_patch_updates_each_field_and_leaves_others_untouched(self):
        card = self.create(
            owner="alice",
            priority="low",
            requirements=["one"],
            blockers=["vendor"],
        )
        updated = self.patch(card["id"], {"owner": "bob"})
        self.assertEqual(updated.status, 200, updated.raw)
        self.assert_optional_fields(
            updated.json["card"],
            owner="bob",
            priority="low",
            requirements=["one"],
            blockers=["vendor"],
        )

        updated = self.patch(card["id"], {"priority": "urgent"})
        self.assertEqual(updated.status, 200, updated.raw)
        self.assert_optional_fields(
            updated.json["card"],
            owner="bob",
            priority="urgent",
            requirements=["one"],
            blockers=["vendor"],
        )

        updated = self.patch(card["id"], {"requirements": ["two", "three"]})
        self.assertEqual(updated.status, 200, updated.raw)
        self.assert_optional_fields(
            updated.json["card"],
            owner="bob",
            priority="urgent",
            requirements=["two", "three"],
            blockers=["vendor"],
        )

        updated = self.patch(card["id"], {"blockers": ["legal"]})
        self.assertEqual(updated.status, 200, updated.raw)
        self.assert_optional_fields(
            updated.json["card"],
            owner="bob",
            priority="urgent",
            requirements=["two", "three"],
            blockers=["legal"],
        )
        stored = self.detail(card["id"])["card"]
        self.assert_optional_fields(
            stored,
            owner="bob",
            priority="urgent",
            requirements=["two", "three"],
            blockers=["legal"],
        )

    def test_patch_clears_fields_with_null_and_empty_list(self):
        card = self.create(
            owner="alice",
            priority="high",
            requirements=["one"],
            blockers=["vendor"],
        )
        cleared = self.patch(
            card["id"],
            {"owner": None, "priority": None, "requirements": [], "blockers": None},
        )
        self.assertEqual(cleared.status, 200, cleared.raw)
        self.assertEqual(set(cleared.json["card"]), CORE_FIELDS)
        self.assertEqual(set(self.detail(card["id"])["card"]), CORE_FIELDS)

    def test_patch_does_not_change_status(self):
        card = self.create(status="REVIEW", owner="alice")
        updated = self.patch(card["id"], {"owner": "bob"})
        self.assertEqual(updated.json["card"]["status"], "REVIEW")

    # -- validation -----------------------------------------------------

    def test_create_rejects_invalid_owner(self):
        for value, reason in ((42, "invalid_type"), ("   ", "blank")):
            resp = request(
                self.app,
                "POST",
                "/cards",
                body={"title": "Card", "card_type": "task", "owner": value},
            )
            self.assert_error(
                resp, 422, "validation_error", field="owner", reason=reason
            )

    def test_create_rejects_invalid_priority(self):
        for value, reason in ((7, "invalid_type"), (" ", "blank")):
            resp = request(
                self.app,
                "POST",
                "/cards",
                body={"title": "Card", "card_type": "task", "priority": value},
            )
            self.assert_error(
                resp, 422, "validation_error", field="priority", reason=reason
            )

    def test_create_rejects_invalid_requirements(self):
        for value in ("one", 5, [1], [""], [None], ["one", "one"], ["one", " one "]):
            resp = request(
                self.app,
                "POST",
                "/cards",
                body={"title": "Card", "card_type": "task", "requirements": value},
            )
            self.assert_error(
                resp,
                422,
                "validation_error",
                field="requirements",
                reason="invalid_requirements",
            )

    def test_create_rejects_invalid_blockers(self):
        for value in ("vendor", 5, [1], [""], ["a", "a"]):
            resp = request(
                self.app,
                "POST",
                "/cards",
                body={"title": "Card", "card_type": "task", "blockers": value},
            )
            self.assert_error(
                resp,
                422,
                "validation_error",
                field="blockers",
                reason="invalid_blockers",
            )

    def test_patch_rejects_invalid_values(self):
        card = self.create(title="Card")
        cases = (
            ({"owner": 42}, "owner", "invalid_type"),
            ({"owner": " "}, "owner", "blank"),
            ({"priority": 9}, "priority", "invalid_type"),
            ({"priority": ""}, "priority", "blank"),
            ({"requirements": "one"}, "requirements", "invalid_requirements"),
            ({"requirements": ["a", "a"]}, "requirements", "invalid_requirements"),
            ({"blockers": [""]}, "blockers", "invalid_blockers"),
        )
        for body, field, reason in cases:
            resp = self.patch(card["id"], body)
            self.assert_error(
                resp, 422, "validation_error", field=field, reason=reason
            )
        self.assertEqual(set(self.detail(card["id"])["card"]), CORE_FIELDS)

    def test_patch_rejects_unknown_field(self):
        card = self.create(title="Card")
        resp = self.patch(card["id"], {"status": "READY"})
        self.assert_error(
            resp, 422, "validation_error", field="status", reason="unknown_field"
        )

    def test_patch_rejects_empty_body(self):
        card = self.create(title="Card")
        resp = self.patch(card["id"], {})
        self.assert_error(resp, 422, "validation_error", reason="required")

    def test_patch_rejects_malformed_and_non_object_bodies(self):
        card = self.create(title="Card")
        resp = request(
            self.app,
            "PATCH",
            f"/cards/{card['id']}",
            raw_body=b"{not json",
        )
        self.assert_error(resp, 400, "invalid_json")
        resp = request(
            self.app,
            "PATCH",
            f"/cards/{card['id']}",
            raw_body=b'["owner"]',
        )
        self.assert_error(
            resp, 422, "validation_error", reason="not_an_object"
        )

    def test_patch_unknown_card_is_not_found(self):
        resp = self.patch("missing", {"owner": "alice"})
        self.assert_error(resp, 404, "not_found")

    def test_patch_rejects_query_parameters(self):
        card = self.create(title="Card")
        resp = request(
            self.app,
            "PATCH",
            f"/cards/{card['id']}",
            body={"owner": "alice"},
            query="force=true",
        )
        self.assert_error(
            resp, 422, "validation_error", field="force", reason="unknown_parameter"
        )

    # -- semantics stay separate from gates -----------------------------

    def test_blockers_field_does_not_affect_dependency_blocking(self):
        dependency = self.create(title="Prereq")
        card = self.create(title="Dependent", blockers=["vendor access"])
        added = request(
            self.app,
            "POST",
            f"/cards/{card['id']}/dependencies",
            body={"depends_on_card_id": dependency["id"]},
        )
        self.assertEqual(added.status, 201, added.raw)

        detail = self.detail(card["id"])
        self.assertEqual(detail["open_dependencies"], [dependency["id"]])
        self.assertTrue(detail["blocked"])
        self.assertEqual(detail["card"]["blockers"], ["vendor access"])

        gate = request(
            self.app,
            "PATCH",
            f"/cards/{card['id']}/status",
            body={"status": "READY"},
        )
        self.assertEqual(gate.status, 409, gate.raw)
        self.assertEqual(gate.json["error"]["code"], "dependencies_not_closed")

    def test_blockers_alone_do_not_block_transitions(self):
        card = self.create(title="Card", blockers=["vendor access"])
        moved = request(
            self.app,
            "PATCH",
            f"/cards/{card['id']}/status",
            body={"status": "READY"},
        )
        self.assertEqual(moved.status, 200, moved.raw)
        self.assertEqual(moved.json["card"]["status"], "READY")

    def test_persisted_fields_survive_status_transitions(self):
        card = self.create(
            owner="alice", requirements=["one"], blockers=["vendor"]
        )
        moved = request(
            self.app,
            "PATCH",
            f"/cards/{card['id']}/status",
            body={"status": "IN_PROGRESS"},
        )
        self.assertEqual(moved.status, 200, moved.raw)
        self.assert_optional_fields(
            moved.json["card"],
            owner="alice",
            requirements=["one"],
            blockers=["vendor"],
        )


class CardFieldsStoreContractTest(unittest.TestCase):
    """Malformed optional fields from a store are contract violations."""

    class _BadStore(InMemoryCardStore):
        def __init__(self, field, value):
            super().__init__()
            self._field = field
            self._value = value

        def get_card(self, card_id):
            card = super().get_card(card_id)
            if card is not None:
                card[self._field] = self._value
            return card

    def test_wrong_optional_field_types_are_stripped_to_500(self):
        for field, value in (
            ("owner", 42),
            ("owner", ""),
            ("priority", 7),
            ("requirements", "one"),
            ("requirements", [1]),
            ("blockers", 5),
        ):
            with self.subTest(field=field, value=value):
                store = self._BadStore(field, value)
                app = make_app(store)
                resp = request(
                    app,
                    "POST",
                    "/cards",
                    body={"title": "Card", "card_type": "task"},
                )
                self.assertEqual(resp.status, 500, resp.raw)
                self.assertEqual(
                    resp.json["error"]["code"],
                    "store_contract_violation",
                    resp.raw,
                )


if __name__ == "__main__":
    unittest.main()
