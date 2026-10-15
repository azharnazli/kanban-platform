"""Focused PostgreSQL tests for the persisted V1 card fields.

Covers the canonical PostgreSQL store and the HTTP adapter: validation,
create/update round-trips, raw column storage, restart durability, and the
idempotent migration that adds the fields to a database created by the
previous schema.

Uses the disposable PostgreSQL harness (tmpfs data directory, random
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

LEGACY_SCHEMA = """
CREATE TABLE cards (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title TEXT NOT NULL,
    type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'TODO',
    parent_id BIGINT,
    required_parent_type TEXT GENERATED ALWAYS AS (
        CASE type
            WHEN 'feature' THEN 'project_epic'
            WHEN 'task' THEN 'feature'
        END
    ) STORED,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT cards_title_not_blank CHECK (btrim(title) <> ''),
    CONSTRAINT cards_title_length CHECK (char_length(title) <= 200),
    CONSTRAINT cards_type_valid CHECK (type IN ('project_epic', 'feature', 'task')),
    CONSTRAINT cards_status_valid CHECK (status IN (
        'TODO', 'READY', 'IN_PROGRESS', 'REVIEW', 'CHANGES_REQUESTED',
        'APPROVED', 'MERGED', 'CLOSED', 'BLOCKED'
    )),
    CONSTRAINT cards_no_self_parent CHECK (parent_id IS NULL OR parent_id <> id),
    CONSTRAINT cards_epic_has_no_parent CHECK (type <> 'project_epic' OR parent_id IS NULL),
    CONSTRAINT cards_id_type_key UNIQUE (id, type),
    CONSTRAINT cards_parent_type_fkey FOREIGN KEY (parent_id, required_parent_type)
        REFERENCES cards (id, type)
)
"""


class PostgresCardFieldsTest(unittest.TestCase):
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

    # -- store-level round-trips ----------------------------------------

    def test_store_roundtrip_and_restart_durability(self):
        card = self.store.create_card(
            "Ship parser",
            "task",
            owner="alice",
            priority="high",
            requirements=["parse feed", "emit JSON"],
            blockers=["vendor access"],
        )
        self.assertEqual(card.owner, "alice")
        self.assertEqual(card.priority, "high")
        self.assertEqual(card.requirements, ("parse feed", "emit JSON"))
        self.assertEqual(card.blockers, ("vendor access",))

        updated = self.store.update_card_fields(
            card.id,
            owner="bob",
            priority=None,
            requirements=["parse feed v2"],
            blockers=[],
        )
        self.assertEqual(updated.owner, "bob")
        self.assertIsNone(updated.priority)
        self.assertEqual(updated.requirements, ("parse feed v2",))
        self.assertEqual(updated.blockers, ())

        self.store.close()
        self.store = PgCardStore(self.dsn)
        reloaded = self.store.get_card(card.id)
        self.assertEqual(reloaded.owner, "bob")
        self.assertIsNone(reloaded.priority)
        self.assertEqual(reloaded.requirements, ("parse feed v2",))
        self.assertEqual(reloaded.blockers, ())

    def test_store_rejects_invalid_field_values(self):
        card = self.store.create_card("Card", "task")
        bad_updates = (
            {"owner": 5},
            {"owner": "   "},
            {"priority": 5},
            {"priority": ""},
            {"requirements": "one"},
            {"requirements": [""]},
            {"requirements": ["a", " a "]},
            {"requirements": [None]},
            {"blockers": "vendor"},
            {"blockers": [""]},
            {"blockers": ["a", "a"]},
            {"status": "READY"},
        )
        for changes in bad_updates:
            with self.assertRaises(ValidationError):
                self.store.update_card_fields(card.id, **changes)
        with self.assertRaises(ValidationError):
            self.store.update_card_fields(card.id)
        with self.assertRaises(ValidationError):
            self.store.update_card_fields(9999, owner="alice")
        with self.assertRaises(ValidationError):
            self.store.create_card("Bad owner", "task", owner=5)
        with self.assertRaises(ValidationError):
            self.store.create_card("Bad priority", "task", priority=" ")

        reloaded = self.store.get_card(card.id)
        self.assertIsNone(reloaded.owner)
        self.assertIsNone(reloaded.priority)
        self.assertEqual(reloaded.requirements, ())
        self.assertEqual(reloaded.blockers, ())

    def test_store_trims_text_and_list_entries(self):
        card = self.store.create_card(
            "Card",
            "task",
            owner="  alice ",
            priority=" high ",
            requirements=[" one ", "two"],
            blockers=["  vendor "],
        )
        self.assertEqual(card.owner, "alice")
        self.assertEqual(card.priority, "high")
        self.assertEqual(card.requirements, ("one", "two"))
        self.assertEqual(card.blockers, ("vendor",))

    # -- raw schema behavior --------------------------------------------

    def test_fields_are_stored_in_dedicated_columns(self):
        card = self.create(
            owner="alice",
            priority="urgent",
            requirements=["one", "two"],
            blockers=["vendor"],
        )
        with psycopg.connect(self.dsn) as conn:
            row = conn.execute(
                "SELECT owner, priority, requirements, blockers FROM cards "
                "WHERE id = %s",
                (int(card["id"]),),
            ).fetchone()
        self.assertEqual(row, ("alice", "urgent", ["one", "two"], ["vendor"]))

    def test_storage_constraints_reject_bad_values(self):
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            for sql, params, error in (
                (
                    "INSERT INTO cards (title, type, owner) VALUES ('x', 'task', ' ')",
                    (),
                    psycopg.errors.CheckViolation,
                ),
                (
                    "INSERT INTO cards (title, type, priority) VALUES ('x', 'task', '')",
                    (),
                    psycopg.errors.CheckViolation,
                ),
                (
                    "INSERT INTO cards (title, type, requirements) "
                    "VALUES ('x', 'task', ARRAY[''])",
                    (),
                    psycopg.errors.CheckViolation,
                ),
                (
                    "INSERT INTO cards (title, type, blockers) "
                    "VALUES ('x', 'task', ARRAY['a', NULL])",
                    (),
                    psycopg.errors.CheckViolation,
                ),
            ):
                with self.subTest(sql=sql):
                    with self.assertRaises(error):
                        conn.execute(sql, params)

    def test_schema_migration_adds_fields_to_legacy_database(self):
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute("DROP TABLE IF EXISTS card_evidence")
            conn.execute("DROP TABLE IF EXISTS card_dependencies")
            conn.execute("DROP TABLE cards CASCADE")
            conn.execute(LEGACY_SCHEMA)
            legacy_id = conn.execute(
                "INSERT INTO cards (title, type, status) "
                "VALUES ('Legacy card', 'task', 'READY') RETURNING id"
            ).fetchone()[0]

            columns = {
                (row[0], row[1], row[2])
                for row in conn.execute(
                    "SELECT column_name, is_nullable, data_type "
                    "FROM information_schema.columns "
                    "WHERE table_name = 'cards'"
                ).fetchall()
            }
            self.assertNotIn(("owner", "YES", "text"), columns)

            migrated = PgCardStore(self.dsn)
            try:
                columns = {
                    (row[0], row[1], row[2])
                    for row in conn.execute(
                        "SELECT column_name, is_nullable, data_type "
                        "FROM information_schema.columns "
                        "WHERE table_name = 'cards'"
                    ).fetchall()
                }
                self.assertIn(("owner", "YES", "text"), columns)
                self.assertIn(("priority", "YES", "text"), columns)
                self.assertIn(("requirements", "NO", "ARRAY"), columns)
                self.assertIn(("blockers", "NO", "ARRAY"), columns)

                legacy = migrated.get_card(legacy_id)
                self.assertEqual(legacy.title, "Legacy card")
                self.assertEqual(legacy.status, "READY")
                self.assertIsNone(legacy.owner)
                self.assertIsNone(legacy.priority)
                self.assertEqual(legacy.requirements, ())
                self.assertEqual(legacy.blockers, ())

                migrated.update_card_fields(
                    legacy_id,
                    owner="alice",
                    requirements=["one"],
                )
                self.assertEqual(migrated.get_card(legacy_id).owner, "alice")
                self.assertEqual(
                    migrated.get_card(legacy_id).requirements, ("one",)
                )
            finally:
                migrated.close()

            # The migration is idempotent: reapplying the schema is a no-op.
            reapplied = PgCardStore(self.dsn)
            try:
                self.assertEqual(
                    reapplied.get_card(legacy_id).owner, "alice"
                )
            finally:
                reapplied.close()

    # -- HTTP over the PostgreSQL adapter -------------------------------

    def test_http_roundtrip_and_restart_durability(self):
        card = self.create(
            title="Ship parser",
            owner="alice",
            priority="high",
            requirements=["parse feed"],
            blockers=["vendor access"],
        )
        expected = {
            "owner": "alice",
            "priority": "high",
            "requirements": ["parse feed"],
            "blockers": ["vendor access"],
        }
        for name, value in expected.items():
            self.assertEqual(card[name], value, card)

        updated = self.patch(card["id"], {"owner": "bob", "blockers": []})
        self.assertEqual(updated.status, 200, updated.raw)
        self.assertEqual(updated.json["card"]["owner"], "bob")
        self.assertNotIn("blockers", updated.json["card"])

        self.adapter.close()
        self.store.close()
        self.adapter = PgCardStoreAdapter(self.dsn)
        self.store = PgCardStore(self.dsn)
        self.app = make_app(self.adapter)

        persisted = self.detail(card["id"])["card"]
        self.assertEqual(persisted["owner"], "bob")
        self.assertEqual(persisted["priority"], "high")
        self.assertEqual(persisted["requirements"], ["parse feed"])
        self.assertNotIn("blockers", persisted)

        listed = request(self.app, "GET", "/cards")
        self.assertEqual(listed.status, 200, listed.raw)
        self.assertEqual(listed.json["items"][0]["owner"], "bob")

    def test_http_rejects_invalid_values_without_persisting(self):
        card = self.create(title="Card")
        cases = (
            ({"owner": 42}, "owner", "invalid_type"),
            ({"owner": " "}, "owner", "blank"),
            ({"priority": 9}, "priority", "invalid_type"),
            ({"priority": " "}, "priority", "blank"),
            ({"requirements": "one"}, "requirements", "invalid_requirements"),
            ({"requirements": ["a", "a"]}, "requirements", "invalid_requirements"),
            ({"blockers": [""]}, "blockers", "invalid_blockers"),
            ({"status": "READY"}, "status", "unknown_field"),
        )
        for body, field, reason in cases:
            resp = self.patch(card["id"], body)
            self.assert_error(
                resp, 422, "validation_error", field=field, reason=reason
            )
        persisted = self.detail(card["id"])["card"]
        self.assertEqual(
            set(persisted), {"id", "title", "card_type", "status", "parent_id"}
        )

    def test_http_create_validation_over_postgres(self):
        for body, field in (
            ({"owner": 42}, "owner"),
            ({"priority": " "}, "priority"),
            ({"requirements": ["a", " a "]}, "requirements"),
            ({"blockers": [""]}, "blockers"),
        ):
            resp = request(
                self.app,
                "POST",
                "/cards",
                body={"title": "Card", "card_type": "task", **body},
            )
            self.assert_error(resp, 422, "validation_error", field=field)

    def test_http_patch_unknown_card_is_not_found(self):
        resp = self.patch("424242", {"owner": "alice"})
        self.assert_error(resp, 404, "not_found")


if __name__ == "__main__":
    unittest.main()
