"""Focused PostgreSQL regression tests for the persisted V1 card fields.

Independent verification of the create/update/read round-trip for owner,
priority, requirements, and blockers through both the canonical PostgreSQL
store and the HTTP API backed by the PostgreSQL adapter, including:

- durability across adapter/store reopen for created, updated, and cleared
  values;
- rejection of invalid types, blanks, and duplicates (including duplicates
  that only collide after trimming) without persisting anything;
- clear semantics for null and empty-list spellings;
- the free-form blockers field staying distinct from dependency-derived
  blocked status and the readiness gate.

Runs against the disposable PostgreSQL harness (tmpfs data directory, random
127.0.0.1 port, trust auth); existing containers, databases, and volumes are
never touched. Tests are skipped only when docker or the cached image is
missing.
"""

import unittest

import psycopg

from kanban_pg import CardStore as PgCardStore
from kanban_pg import ValidationError
from kanban_pg_adapter import PgCardStoreAdapter
from pg_harness import PostgresUnavailable, TemporaryPostgres

from .support import make_app, request

CORE_FIELDS = {"id", "title", "card_type", "status", "parent_id"}


class CardFieldsPgRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.postgres = TemporaryPostgres()
        try:
            cls.postgres.start()
        except PostgresUnavailable as exc:
            raise unittest.SkipTest(str(exc))
        cls.dsn = cls.postgres.dsn

    @classmethod
    def tearDownClass(cls):
        cls.postgres.stop()

    def setUp(self):
        self.store = PgCardStore(self.dsn)
        self.adapter = PgCardStoreAdapter(self.dsn)
        self.app = make_app(self.adapter)
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute("TRUNCATE cards RESTART IDENTITY CASCADE")

    def tearDown(self):
        self.adapter.close()
        self.store.close()

    def reopen(self):
        """Close and reopen both the adapter and the raw store."""
        self.adapter.close()
        self.store.close()
        self.adapter = PgCardStoreAdapter(self.dsn)
        self.store = PgCardStore(self.dsn)
        self.app = make_app(self.adapter)

    def create(self, **payload):
        payload.setdefault("title", "Card")
        payload.setdefault("card_type", "task")
        resp = request(self.app, "POST", "/cards", body=payload)
        self.assertEqual(resp.status, 201, resp.raw)
        return resp.json["card"]

    def patch(self, card_id, body):
        return request(self.app, "PATCH", f"/cards/{card_id}", body=body)

    def detail_card(self, card_id):
        resp = request(self.app, "GET", f"/cards/{card_id}")
        self.assertEqual(resp.status, 200, resp.raw)
        return resp.json

    def set_status(self, card_id, status):
        return request(
            self.app,
            "PATCH",
            f"/cards/{card_id}/status",
            body={"status": status},
        )

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

    def assert_all_unset(self, card):
        self.assertEqual(set(card) & {"owner", "priority", "requirements", "blockers"}, set())

    def assert_store_all_unset(self, card_id):
        stored = self.store.get_card(card_id)
        self.assertIsNotNone(stored)
        self.assertIsNone(stored.owner)
        self.assertIsNone(stored.priority)
        self.assertEqual(stored.requirements, ())
        self.assertEqual(stored.blockers, ())

    # -- store-level round-trip ------------------------------------------

    def test_store_create_update_read_roundtrip_and_reopen(self):
        card = self.store.create_card(
            "Ship parser",
            "task",
            owner="  alice ",
            priority=" high ",
            requirements=[" parse feed ", "emit JSON"],
            blockers=["  vendor access  "],
        )
        self.assertEqual(card.owner, "alice")
        self.assertEqual(card.priority, "high")
        self.assertEqual(card.requirements, ("parse feed", "emit JSON"))
        self.assertEqual(card.blockers, ("vendor access",))

        listed = self.store.list_cards()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].owner, "alice")
        self.assertEqual(listed[0].requirements, ("parse feed", "emit JSON"))

        updated = self.store.update_card_fields(
            card.id, owner="bob", blockers=[" legal "]
        )
        self.assertEqual(updated.owner, "bob")
        self.assertEqual(updated.priority, "high")
        self.assertEqual(updated.requirements, ("parse feed", "emit JSON"))
        self.assertEqual(updated.blockers, ("legal",))

        reread = self.store.get_card(card.id)
        self.assertEqual(reread.owner, "bob")
        self.assertEqual(reread.priority, "high")
        self.assertEqual(reread.requirements, ("parse feed", "emit JSON"))
        self.assertEqual(reread.blockers, ("legal",))

        self.store.close()
        self.store = PgCardStore(self.dsn)
        reloaded = self.store.get_card(card.id)
        self.assertEqual(reloaded.owner, "bob")
        self.assertEqual(reloaded.priority, "high")
        self.assertEqual(reloaded.requirements, ("parse feed", "emit JSON"))
        self.assertEqual(reloaded.blockers, ("legal",))

    def test_store_rejects_invalid_types_blanks_and_duplicates(self):
        card = self.store.create_card("Card", "task")
        bad_updates = (
            {"owner": 5},
            {"owner": "   "},
            {"owner": ""},
            {"priority": 5},
            {"priority": ""},
            {"requirements": "one"},
            {"requirements": [1]},
            {"requirements": [""]},
            {"requirements": [None]},
            {"requirements": ["a", "a"]},
            {"requirements": ["a", " a "]},
            {"blockers": "vendor"},
            {"blockers": [1]},
            {"blockers": [""]},
            {"blockers": ["a", "a"]},
            {"blockers": ["a", " a "]},
        )
        for changes in bad_updates:
            with self.subTest(changes=changes):
                with self.assertRaises(ValidationError):
                    self.store.update_card_fields(card.id, **changes)
        with self.assertRaises(ValidationError):
            self.store.update_card_fields(card.id)
        with self.assertRaises(ValidationError):
            self.store.update_card_fields(card.id, status="READY")
        with self.assertRaises(ValidationError):
            self.store.update_card_fields(999999, owner="alice")
        self.assert_store_all_unset(card.id)

        bad_creates = (
            {"owner": 5},
            {"owner": " "},
            {"priority": 9},
            {"priority": " "},
            {"requirements": ["x", " x "]},
            {"requirements": [""]},
            {"blockers": [""]},
            {"blockers": ["a", "a"]},
        )
        for kwargs in bad_creates:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValidationError):
                    self.store.create_card("Bad", "task", **kwargs)
        self.assertEqual(len(self.store.list_cards()), 1)

    # -- HTTP create round-trip over PostgreSQL ---------------------------

    def test_http_create_roundtrip_in_every_read_view(self):
        card = self.create(
            title="Ship parser",
            owner="  alice ",
            priority=" high ",
            requirements=[" parse feed ", "emit JSON"],
            blockers=["  vendor access  "],
        )
        expected = {
            "owner": "alice",
            "priority": "high",
            "requirements": ["parse feed", "emit JSON"],
            "blockers": ["vendor access"],
        }
        self.assertEqual(set(card), CORE_FIELDS | set(expected))
        self.assert_optional_fields(card, **expected)

        self.assert_optional_fields(self.detail_card(card["id"])["card"], **expected)

        listed = request(self.app, "GET", "/cards")
        self.assertEqual(listed.status, 200, listed.raw)
        self.assertEqual(listed.json["count"], 1)
        self.assert_optional_fields(listed.json["items"][0], **expected)

        board = request(self.app, "GET", "/board")
        self.assertEqual(board.status, 200, board.raw)
        column = next(
            column for column in board.json["columns"] if column["status"] == "TODO"
        )
        self.assert_optional_fields(column["cards"][0], **expected)

        stored = self.store.get_card(int(card["id"]))
        self.assertEqual(stored.owner, "alice")
        self.assertEqual(stored.priority, "high")
        self.assertEqual(stored.requirements, ("parse feed", "emit JSON"))
        self.assertEqual(stored.blockers, ("vendor access",))

    # -- HTTP update round-trip and reopen durability ----------------------

    def test_http_update_roundtrip_survives_reopen(self):
        card = self.create(
            title="Card",
            owner="alice",
            priority="low",
            requirements=["one"],
            blockers=["vendor"],
        )
        updated = self.patch(card["id"], {"owner": " bob "})
        self.assertEqual(updated.status, 200, updated.raw)
        self.assert_optional_fields(
            updated.json["card"],
            owner="bob",
            priority="low",
            requirements=["one"],
            blockers=["vendor"],
        )

        updated = self.patch(
            card["id"],
            {
                "priority": " urgent ",
                "requirements": [" two ", "three"],
                "blockers": ["  legal  "],
            },
        )
        self.assertEqual(updated.status, 200, updated.raw)
        self.assert_optional_fields(
            updated.json["card"],
            owner="bob",
            priority="urgent",
            requirements=["two", "three"],
            blockers=["legal"],
        )
        self.assertEqual(updated.json["card"]["status"], "TODO")

        self.reopen()

        persisted = self.detail_card(card["id"])["card"]
        self.assert_optional_fields(
            persisted,
            owner="bob",
            priority="urgent",
            requirements=["two", "three"],
            blockers=["legal"],
        )
        listed = request(self.app, "GET", "/cards")
        self.assert_optional_fields(
            listed.json["items"][0],
            owner="bob",
            priority="urgent",
            requirements=["two", "three"],
            blockers=["legal"],
        )
        stored = self.store.get_card(int(card["id"]))
        self.assertEqual(stored.owner, "bob")
        self.assertEqual(stored.priority, "urgent")
        self.assertEqual(stored.requirements, ("two", "three"))
        self.assertEqual(stored.blockers, ("legal",))

    def test_patch_clear_semantics_survive_reopen(self):
        card = self.create(
            owner="alice",
            priority="high",
            requirements=["one", "two"],
            blockers=["vendor"],
        )
        cleared = self.patch(
            card["id"],
            {"owner": None, "priority": None, "requirements": [], "blockers": None},
        )
        self.assertEqual(cleared.status, 200, cleared.raw)
        self.assertEqual(set(cleared.json["card"]), CORE_FIELDS)
        self.assert_store_all_unset(int(card["id"]))

        self.reopen()
        self.assertEqual(set(self.detail_card(card["id"])["card"]), CORE_FIELDS)
        self.assert_store_all_unset(int(card["id"]))

        # The alternate clear spellings (null list, empty list) behave the same.
        again = self.patch(
            card["id"],
            {
                "owner": "alice",
                "priority": "low",
                "requirements": ["one"],
                "blockers": ["vendor"],
            },
        )
        self.assertEqual(again.status, 200, again.raw)
        self.assert_optional_fields(
            again.json["card"],
            owner="alice",
            priority="low",
            requirements=["one"],
            blockers=["vendor"],
        )
        cleared = self.patch(
            card["id"],
            {"owner": None, "priority": None, "requirements": None, "blockers": []},
        )
        self.assertEqual(cleared.status, 200, cleared.raw)
        self.assertEqual(set(cleared.json["card"]), CORE_FIELDS)
        self.assertEqual(set(self.detail_card(card["id"])["card"]), CORE_FIELDS)
        self.assert_store_all_unset(int(card["id"]))

    # -- HTTP rejection over PostgreSQL ------------------------------------

    def test_http_patch_rejects_invalid_values_without_persisting(self):
        card = self.create(title="Card")
        cases = (
            ({"owner": 42}, "owner", "invalid_type"),
            ({"owner": "   "}, "owner", "blank"),
            ({"priority": 9}, "priority", "invalid_type"),
            ({"priority": "\t"}, "priority", "blank"),
            ({"requirements": "one"}, "requirements", "invalid_requirements"),
            ({"requirements": {"one": True}}, "requirements", "invalid_requirements"),
            ({"requirements": [1]}, "requirements", "invalid_requirements"),
            ({"requirements": [""]}, "requirements", "invalid_requirements"),
            ({"requirements": [None]}, "requirements", "invalid_requirements"),
            ({"requirements": ["one", "one"]}, "requirements", "invalid_requirements"),
            ({"requirements": ["one", " one "]}, "requirements", "invalid_requirements"),
            ({"blockers": "vendor"}, "blockers", "invalid_blockers"),
            ({"blockers": [1]}, "blockers", "invalid_blockers"),
            ({"blockers": [""]}, "blockers", "invalid_blockers"),
            ({"blockers": ["a", "a"]}, "blockers", "invalid_blockers"),
            ({"blockers": ["a", " a "]}, "blockers", "invalid_blockers"),
            ({"status": "READY"}, "status", "unknown_field"),
        )
        for body, field, reason in cases:
            with self.subTest(body=body):
                resp = self.patch(card["id"], body)
                self.assert_error(
                    resp, 422, "validation_error", field=field, reason=reason
                )
                self.assertEqual(set(self.detail_card(card["id"])["card"]), CORE_FIELDS)
                self.assert_store_all_unset(int(card["id"]))

    def test_http_create_rejects_invalid_values_without_persisting(self):
        create_cases = (
            ({"owner": 42}, "owner", "invalid_type"),
            ({"owner": ""}, "owner", "blank"),
            ({"priority": " "}, "priority", "blank"),
            ({"requirements": ["a", " a "]}, "requirements", "invalid_requirements"),
            ({"requirements": [None]}, "requirements", "invalid_requirements"),
            ({"blockers": [""]}, "blockers", "invalid_blockers"),
            ({"blockers": ["a", "a"]}, "blockers", "invalid_blockers"),
        )
        for body, field, reason in create_cases:
            with self.subTest(body=body):
                resp = request(
                    self.app,
                    "POST",
                    "/cards",
                    body={"title": "Bad", "card_type": "task", **body},
                )
                self.assert_error(
                    resp, 422, "validation_error", field=field, reason=reason
                )
        listed = request(self.app, "GET", "/cards")
        self.assertEqual(listed.status, 200, listed.raw)
        self.assertEqual(listed.json["count"], 0)

    def test_patch_request_shape_errors(self):
        card = self.create(title="Card")
        empty = self.patch(card["id"], {})
        self.assert_error(empty, 422, "validation_error", reason="required")
        missing = request(self.app, "PATCH", f"/cards/{card['id']}")
        self.assert_error(missing, 400, "invalid_json")
        malformed = request(
            self.app, "PATCH", f"/cards/{card['id']}", raw_body=b"{not json"
        )
        self.assert_error(malformed, 400, "invalid_json")
        not_object = request(
            self.app, "PATCH", f"/cards/{card['id']}", raw_body=b'["owner"]'
        )
        self.assert_error(not_object, 422, "validation_error", reason="not_an_object")
        absent = self.patch("999999", {"owner": "alice"})
        self.assert_error(absent, 404, "not_found")
        collection = request(self.app, "PATCH", "/cards", body={"owner": "alice"})
        self.assert_error(collection, 405, "method_not_allowed")

    # -- blockers field vs dependency-derived blocking ---------------------

    def test_blockers_field_is_distinct_from_computed_blocking(self):
        dependency = self.create(title="Prereq")
        card = self.create(title="Dependent", blockers=["vendor access"])

        detail = self.detail_card(card["id"])
        self.assertEqual(detail["open_dependencies"], [])
        self.assertFalse(detail["blocked"])
        self.assertEqual(detail["card"]["blockers"], ["vendor access"])

        added = request(
            self.app,
            "POST",
            f"/cards/{card['id']}/dependencies",
            body={"depends_on_card_id": dependency["id"]},
        )
        self.assertEqual(added.status, 201, added.raw)
        detail = self.detail_card(card["id"])
        self.assertEqual(detail["open_dependencies"], [dependency["id"]])
        self.assertTrue(detail["blocked"])
        self.assertEqual(detail["card"]["blockers"], ["vendor access"])

        changed = self.patch(card["id"], {"blockers": ["other reason"]})
        self.assertEqual(changed.status, 200, changed.raw)
        detail = self.detail_card(card["id"])
        self.assertEqual(detail["open_dependencies"], [dependency["id"]])
        self.assertTrue(detail["blocked"])
        self.assertEqual(detail["card"]["blockers"], ["other reason"])

        cleared = self.patch(card["id"], {"blockers": []})
        self.assertEqual(cleared.status, 200, cleared.raw)
        detail = self.detail_card(card["id"])
        self.assertEqual(detail["open_dependencies"], [dependency["id"]])
        self.assertTrue(detail["blocked"])
        self.assertNotIn("blockers", detail["card"])

        gate = self.set_status(card["id"], "READY")
        self.assert_error(gate, 409, "dependencies_not_closed")

        free = self.create(title="Free", blockers=["vendor access"])
        moved = self.set_status(free["id"], "READY")
        self.assertEqual(moved.status, 200, moved.raw)
        self.assertEqual(moved.json["card"]["blockers"], ["vendor access"])

        self.patch(card["id"], {"blockers": ["legal"]})
        closed = self.set_status(dependency["id"], "CLOSED")
        self.assertEqual(closed.status, 200, closed.raw)
        detail = self.detail_card(card["id"])
        self.assertEqual(detail["open_dependencies"], [])
        self.assertFalse(detail["blocked"])
        self.assertEqual(detail["card"]["blockers"], ["legal"])

        moved = self.set_status(card["id"], "READY")
        self.assertEqual(moved.status, 200, moved.raw)
        self.assertEqual(moved.json["card"]["blockers"], ["legal"])

        self.reopen()
        detail = self.detail_card(card["id"])
        self.assertEqual(detail["open_dependencies"], [])
        self.assertFalse(detail["blocked"])
        self.assertEqual(detail["card"]["blockers"], ["legal"])


if __name__ == "__main__":
    unittest.main()
