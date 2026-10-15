"""Integration tests: Kanban V1 HTTP API over the approved PostgreSQL adapter.

Requests are driven through the WSGI callable in-process against a disposable
PostgreSQL container started by pg_harness (tmpfs data directory, random
127.0.0.1 port, trust auth; existing databases and containers are never
touched). Storage goes through the additive glue in kanban_pg_adapter; the
approved kanban_http package and kanban_pg adapter are imported unchanged.

Also covers the persisted V1 card fields (owner, priority, requirements,
blockers): HTTP create/read/update/clear and validation, PostgreSQL column
storage and durability across store close/reopen, the additive schema
migration from a legacy cards table, and the free-form blockers field staying
distinct from dependency-derived open_dependencies/blocked.

The tests are skipped when docker or the cached PostgreSQL image is missing.
"""

import importlib
import os
import re
import unittest

import psycopg

from kanban_http.store import CardStore
from kanban_pg import CardStore as PgCardStore
from kanban_pg_adapter import PgCardStoreAdapter
from pg_harness import PostgresUnavailable, TemporaryPostgres

from .support import make_app, request

ID_SHAPE = re.compile(r"^[0-9]+$")

CORE_FIELDS = {"id", "title", "card_type", "status", "parent_id"}

# The cards table as created by the schema.sql revision before the persisted
# card fields existed. Used to prove the additive ALTER TABLE migration in
# schema.sql upgrades an existing legacy database in place.
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


class PostgresHttpIntegrationTest(unittest.TestCase):
    """Create/list/detail/dependency/status roundtrips over real PostgreSQL."""

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
        self.store = PgCardStoreAdapter(self.dsn)
        self.app = make_app(self.store)
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute("TRUNCATE cards RESTART IDENTITY CASCADE")

    def tearDown(self):
        self.store.close()

    def create(self, title, card_type, **payload):
        resp = request(
            self.app,
            "POST",
            "/cards",
            body={"title": title, "card_type": card_type, **payload},
        )
        self.assertEqual(resp.status, 201, resp.raw)
        card = resp.json["card"]
        self.assert_id_shape(card["id"])
        self.assertEqual(resp.headers.get("Location"), f"/cards/{card['id']}")
        return card

    def assert_id_shape(self, value):
        self.assertIsInstance(value, str, f"id must be a string, got {value!r}")
        self.assertRegex(value, ID_SHAPE, f"id {value!r} is not a decimal string")
        self.assertEqual(value, str(int(value)))

    def patch(self, card_id, body, query=None):
        return request(
            self.app, "PATCH", f"/cards/{card_id}", body=body, query=query
        )

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

    def _load_wsgi_module(self):
        os.environ["KANBAN_PG_DSN"] = self.dsn
        module = importlib.import_module("kanban_pg_wsgi")
        self.assertEqual(module.DSN_ENV_VAR, "KANBAN_PG_DSN")
        return module

    def test_adapter_satisfies_cardstore_contract(self):
        self.assertIsInstance(self.store, CardStore)

    def test_create_list_detail_roundtrip(self):
        epic = self.create("Platform", "project_epic")
        feature = self.create("Ingest pipeline", "feature", parent_id=epic["id"])
        task = self.create(
            "Write parser", "task", parent_id=feature["id"], status="READY"
        )

        for card in (epic, feature, task):
            self.assertEqual(
                set(card), {"id", "title", "card_type", "status", "parent_id"}
            )
        self.assertIsNone(epic["parent_id"])
        self.assertEqual(feature["parent_id"], epic["id"])
        self.assertEqual(feature["card_type"], "feature")
        self.assertEqual(task["parent_id"], feature["id"])
        self.assertEqual(task["status"], "READY")

        listed = request(self.app, "GET", "/cards")
        self.assertEqual(listed.status, 200, listed.raw)
        self.assertEqual(listed.json["count"], 3)
        self.assertEqual(
            sorted(card["id"] for card in listed.json["items"]),
            sorted((epic["id"], feature["id"], task["id"])),
        )

        filtered = request(self.app, "GET", "/cards", query="card_type=feature")
        self.assertEqual(filtered.status, 200, filtered.raw)
        self.assertEqual([card["id"] for card in filtered.json["items"]], [feature["id"]])

        roots = request(self.app, "GET", "/cards", query="has_parent=false")
        self.assertEqual(roots.status, 200, roots.raw)
        self.assertEqual([card["id"] for card in roots.json["items"]], [epic["id"]])

        detail = request(self.app, "GET", f"/cards/{feature['id']}")
        self.assertEqual(detail.status, 200, detail.raw)
        self.assertEqual(detail.json["card"], feature)
        self.assertEqual(detail.json["depends_on"], [])
        self.assertEqual(detail.json["dependents"], [])
        self.assertEqual(detail.json["open_dependencies"], [])
        self.assertFalse(detail.json["blocked"])

        with psycopg.connect(self.dsn) as conn:
            rows = conn.execute(
                "SELECT id, type, status, parent_id FROM cards ORDER BY id"
            ).fetchall()
        self.assertEqual(
            [
                (
                    str(row[0]),
                    row[1],
                    row[2],
                    None if row[3] is None else str(row[3]),
                )
                for row in rows
            ],
            [
                (epic["id"], "project_epic", "TODO", None),
                (feature["id"], "feature", "TODO", epic["id"]),
                (task["id"], "task", "READY", feature["id"]),
            ],
        )

    def test_dependency_and_status_roundtrip(self):
        first = self.create("First", "task")
        second = self.create("Second", "task")

        added = request(
            self.app,
            "POST",
            f"/cards/{second['id']}/dependencies",
            body={"depends_on_card_id": first["id"]},
        )
        self.assertEqual(added.status, 201, added.raw)
        self.assertEqual(added.json, {"items": [first["id"]], "count": 1})
        self.assertEqual(
            added.headers.get("Location"), f"/cards/{second['id']}/dependencies"
        )
        self.assert_id_shape(added.json["items"][0])

        listing = request(self.app, "GET", f"/cards/{second['id']}/dependencies")
        self.assertEqual(listing.status, 200, listing.raw)
        self.assertEqual(listing.json, {"items": [first["id"]], "count": 1})

        detail = request(self.app, "GET", f"/cards/{second['id']}")
        self.assertEqual(detail.json["depends_on"], [first["id"]])
        self.assertEqual(detail.json["open_dependencies"], [first["id"]])
        self.assertTrue(detail.json["blocked"])
        dependents = request(self.app, "GET", f"/cards/{first['id']}")
        self.assertEqual(dependents.json["dependents"], [second["id"]])
        for value in (
            detail.json["depends_on"]
            + detail.json["open_dependencies"]
            + dependents.json["dependents"]
        ):
            self.assert_id_shape(value)

        gate = request(
            self.app,
            "PATCH",
            f"/cards/{second['id']}/status",
            body={"status": "READY"},
        )
        self.assertEqual(gate.status, 409, gate.raw)
        self.assertEqual(gate.json["error"]["code"], "dependencies_not_closed")
        self.assertEqual(
            gate.json["error"]["details"]["open_dependencies"], [first["id"]]
        )

        closed = request(
            self.app,
            "PATCH",
            f"/cards/{first['id']}/status",
            body={"status": "CLOSED"},
        )
        self.assertEqual(closed.status, 200, closed.raw)
        self.assertEqual(closed.json["card"]["status"], "CLOSED")

        ready = request(
            self.app,
            "PATCH",
            f"/cards/{second['id']}/status",
            body={"status": "READY"},
        )
        self.assertEqual(ready.status, 200, ready.raw)
        self.assertEqual(ready.json["card"]["status"], "READY")

        detail = request(self.app, "GET", f"/cards/{second['id']}")
        self.assertEqual(detail.json["open_dependencies"], [])
        self.assertFalse(detail.json["blocked"])

        removed = request(
            self.app,
            "DELETE",
            f"/cards/{second['id']}/dependencies/{first['id']}",
        )
        self.assertEqual(removed.status, 204, removed.raw)
        self.assertEqual(removed.raw, b"")

        missing = request(
            self.app,
            "DELETE",
            f"/cards/{second['id']}/dependencies/{first['id']}",
        )
        self.assertEqual(missing.status, 404, missing.raw)
        self.assertEqual(missing.json["error"]["code"], "dependency_not_found")

    def test_dependency_listing_preserves_insertion_order(self):
        first = self.create("First", "task")
        second = self.create("Second", "task")
        third = self.create("Third", "task")

        for dependency in (second, first):
            added = request(
                self.app,
                "POST",
                f"/cards/{third['id']}/dependencies",
                body={"depends_on_card_id": dependency["id"]},
            )
            self.assertEqual(added.status, 201, added.raw)

        listing = request(self.app, "GET", f"/cards/{third['id']}/dependencies")
        self.assertEqual(listing.status, 200, listing.raw)
        self.assertEqual(listing.json["items"], [second["id"], first["id"]])

    def test_persisted_card_field_roundtrip_over_postgres(self):
        card = self.create(
            "Ship parser",
            "task",
            owner="  alice  ",
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
        for name, value in expected.items():
            self.assertEqual(card[name], value, card)

        listed = request(self.app, "GET", "/cards")
        self.assertEqual(listed.status, 200, listed.raw)
        self.assertEqual(listed.json["count"], 1)
        for name, value in expected.items():
            self.assertEqual(listed.json["items"][0][name], value, listed.json)

        self.assertEqual(self.detail(card["id"])["card"], card)

        board = request(self.app, "GET", "/board")
        self.assertEqual(board.status, 200, board.raw)
        todo = next(
            column for column in board.json["columns"] if column["status"] == "TODO"
        )
        self.assertEqual(todo["cards"], [card])

        with psycopg.connect(self.dsn) as conn:
            row = conn.execute(
                "SELECT owner, priority, requirements, blockers FROM cards "
                "WHERE id = %s",
                (int(card["id"]),),
            ).fetchone()
        self.assertEqual(
            row, ("alice", "high", ["parse feed", "emit JSON"], ["vendor access"])
        )

        updated = self.patch(
            card["id"],
            {
                "owner": " bob ",
                "priority": "urgent",
                "requirements": [" two ", "three"],
                "blockers": [" legal "],
            },
        )
        self.assertEqual(updated.status, 200, updated.raw)
        updated_card = updated.json["card"]
        self.assertEqual(updated_card["owner"], "bob")
        self.assertEqual(updated_card["priority"], "urgent")
        self.assertEqual(updated_card["requirements"], ["two", "three"])
        self.assertEqual(updated_card["blockers"], ["legal"])
        self.assertEqual(updated_card["status"], "TODO")
        self.assertEqual(updated_card["title"], "Ship parser")

        partial = self.patch(card["id"], {"priority": None})
        self.assertEqual(partial.status, 200, partial.raw)
        self.assertNotIn("priority", partial.json["card"])
        self.assertEqual(partial.json["card"]["owner"], "bob")
        self.assertEqual(partial.json["card"]["requirements"], ["two", "three"])

        cleared = self.patch(
            card["id"],
            {"owner": None, "priority": None, "requirements": [], "blockers": None},
        )
        self.assertEqual(cleared.status, 200, cleared.raw)
        self.assertEqual(set(cleared.json["card"]), CORE_FIELDS)
        self.assertEqual(set(self.detail(card["id"])["card"]), CORE_FIELDS)

        with psycopg.connect(self.dsn) as conn:
            row = conn.execute(
                "SELECT owner, priority, requirements, blockers FROM cards "
                "WHERE id = %s",
                (int(card["id"]),),
            ).fetchone()
        self.assertEqual(row, (None, None, [], []))

        empty = self.create("Empty lists", "task", requirements=[], blockers=[])
        self.assertEqual(set(empty), CORE_FIELDS)

    def test_persisted_card_field_validation_over_postgres(self):
        card = self.create("Card", "task")

        patch_cases = (
            ({"owner": 42}, "owner", "invalid_type"),
            ({"owner": "   "}, "owner", "blank"),
            ({"priority": 9}, "priority", "invalid_type"),
            ({"priority": "\t"}, "priority", "blank"),
            ({"requirements": "one"}, "requirements", "invalid_requirements"),
            ({"requirements": [1]}, "requirements", "invalid_requirements"),
            ({"requirements": [""]}, "requirements", "invalid_requirements"),
            ({"requirements": [None]}, "requirements", "invalid_requirements"),
            ({"requirements": ["a", "a"]}, "requirements", "invalid_requirements"),
            ({"requirements": ["a", " a "]}, "requirements", "invalid_requirements"),
            ({"blockers": "vendor"}, "blockers", "invalid_blockers"),
            ({"blockers": [""]}, "blockers", "invalid_blockers"),
            ({"blockers": ["a", "a"]}, "blockers", "invalid_blockers"),
            ({"blockers": ["a", " a "]}, "blockers", "invalid_blockers"),
            ({"status": "READY"}, "status", "unknown_field"),
        )
        for body, field, reason in patch_cases:
            with self.subTest(body=body):
                self.assert_error(
                    self.patch(card["id"], body),
                    422,
                    "validation_error",
                    field=field,
                    reason=reason,
                )
                self.assertEqual(
                    set(self.detail(card["id"])["card"]), CORE_FIELDS
                )

        create_cases = (
            ({"owner": 42}, "owner", "invalid_type"),
            ({"owner": ""}, "owner", "blank"),
            ({"priority": " "}, "priority", "blank"),
            ({"requirements": ["a", " a "]}, "requirements", "invalid_requirements"),
            ({"requirements": [None]}, "requirements", "invalid_requirements"),
            ({"blockers": [""]}, "blockers", "invalid_blockers"),
            ({"blockers": ["a", "a"]}, "blockers", "invalid_blockers"),
            ({"state": "x"}, "state", "unknown_field"),
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

        self.assert_error(
            self.patch(card["id"], {}), 422, "validation_error", reason="required"
        )
        self.assert_error(
            self.patch(card["id"], {"owner": "alice"}, query="force=true"),
            422,
            "validation_error",
            field="force",
            reason="unknown_parameter",
        )
        self.assert_error(
            request(self.app, "PATCH", f"/cards/{card['id']}"),
            400,
            "invalid_json",
        )
        self.assert_error(
            request(
                self.app, "PATCH", f"/cards/{card['id']}", raw_body=b"{not json"
            ),
            400,
            "invalid_json",
        )
        self.assert_error(
            request(
                self.app, "PATCH", f"/cards/{card['id']}", raw_body=b'["owner"]'
            ),
            422,
            "validation_error",
            reason="not_an_object",
        )
        self.assert_error(
            self.patch("424242", {"owner": "alice"}), 404, "not_found"
        )

        listed = request(self.app, "GET", "/cards")
        self.assertEqual(listed.status, 200, listed.raw)
        self.assertEqual(listed.json["count"], 1)

    def test_persisted_card_fields_survive_store_reopen(self):
        card = self.create(
            "Ship parser",
            "task",
            owner="alice",
            priority="low",
            requirements=["one"],
            blockers=["vendor"],
        )
        updated = self.patch(card["id"], {"owner": "bob", "priority": None})
        self.assertEqual(updated.status, 200, updated.raw)
        self.assertEqual(updated.json["card"]["owner"], "bob")
        self.assertNotIn("priority", updated.json["card"])

        self.store.close()
        self.store = PgCardStoreAdapter(self.dsn)
        self.app = make_app(self.store)

        persisted = self.detail(card["id"])["card"]
        self.assertEqual(persisted["owner"], "bob")
        self.assertNotIn("priority", persisted)
        self.assertEqual(persisted["requirements"], ["one"])
        self.assertEqual(persisted["blockers"], ["vendor"])

        listed = request(self.app, "GET", "/cards")
        self.assertEqual(listed.status, 200, listed.raw)
        self.assertEqual(listed.json["items"][0]["owner"], "bob")
        self.assertEqual(listed.json["items"][0]["blockers"], ["vendor"])

        with PgCardStore(self.dsn) as direct:
            reloaded = direct.get_card(int(card["id"]))
            self.assertIsNotNone(reloaded)
            self.assertEqual(reloaded.owner, "bob")
            self.assertIsNone(reloaded.priority)
            self.assertEqual(reloaded.requirements, ("one",))
            self.assertEqual(reloaded.blockers, ("vendor",))

        fresh = self.create(
            "After reopen", "task", priority="high", blockers=["vendor"]
        )
        self.assertEqual(fresh["priority"], "high")
        self.assertEqual(fresh["blockers"], ["vendor"])

    def test_blockers_field_is_distinct_from_computed_blocking(self):
        dependency = self.create("Prereq", "task")
        card = self.create("Dependent", "task", blockers=["vendor access"])

        detail = self.detail(card["id"])
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
        detail = self.detail(card["id"])
        self.assertEqual(detail["open_dependencies"], [dependency["id"]])
        self.assertTrue(detail["blocked"])
        self.assertEqual(detail["card"]["blockers"], ["vendor access"])

        cleared = self.patch(card["id"], {"blockers": []})
        self.assertEqual(cleared.status, 200, cleared.raw)
        self.assertNotIn("blockers", cleared.json["card"])
        detail = self.detail(card["id"])
        self.assertEqual(detail["open_dependencies"], [dependency["id"]])
        self.assertTrue(detail["blocked"])

        gate = request(
            self.app,
            "PATCH",
            f"/cards/{card['id']}/status",
            body={"status": "READY"},
        )
        self.assertEqual(gate.status, 409, gate.raw)
        self.assertEqual(gate.json["error"]["code"], "dependencies_not_closed")

        free = self.create("Free", "task", blockers=["not a dependency"])
        moved = request(
            self.app,
            "PATCH",
            f"/cards/{free['id']}/status",
            body={"status": "READY"},
        )
        self.assertEqual(moved.status, 200, moved.raw)
        self.assertEqual(moved.json["card"]["status"], "READY")
        self.assertEqual(
            moved.json["card"]["blockers"], ["not a dependency"]
        )

        closed = request(
            self.app,
            "PATCH",
            f"/cards/{dependency['id']}/status",
            body={"status": "CLOSED"},
        )
        self.assertEqual(closed.status, 200, closed.raw)
        detail = self.detail(card["id"])
        self.assertEqual(detail["open_dependencies"], [])
        self.assertFalse(detail["blocked"])

    def test_schema_upgrade_from_legacy_table(self):
        self.store.close()
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute("DROP TABLE IF EXISTS card_evidence")
            conn.execute("DROP TABLE IF EXISTS card_dependencies")
            conn.execute("DROP TABLE IF EXISTS cards CASCADE")
            conn.execute(LEGACY_SCHEMA)
            legacy_id = conn.execute(
                "INSERT INTO cards (title, type, status) "
                "VALUES ('Legacy card', 'task', 'READY') RETURNING id"
            ).fetchone()[0]

            before = {
                row[0]
                for row in conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'cards'"
                ).fetchall()
            }
            self.assertNotIn("owner", before)
            self.assertNotIn("priority", before)
            self.assertNotIn("requirements", before)
            self.assertNotIn("blockers", before)

        self.store = PgCardStoreAdapter(self.dsn)
        self.app = make_app(self.store)

        with psycopg.connect(self.dsn, autocommit=True) as conn:
            after = {
                (row[0], row[1], row[2])
                for row in conn.execute(
                    "SELECT column_name, is_nullable, data_type "
                    "FROM information_schema.columns WHERE table_name = 'cards'"
                ).fetchall()
            }
        self.assertIn(("owner", "YES", "text"), after)
        self.assertIn(("priority", "YES", "text"), after)
        self.assertIn(("requirements", "NO", "ARRAY"), after)
        self.assertIn(("blockers", "NO", "ARRAY"), after)

        legacy_id = str(legacy_id)
        self.assert_id_shape(legacy_id)
        legacy = self.detail(legacy_id)["card"]
        self.assertEqual(legacy["title"], "Legacy card")
        self.assertEqual(legacy["status"], "READY")
        self.assertEqual(set(legacy), CORE_FIELDS)

        updated = self.patch(
            legacy_id,
            {
                "owner": "alice",
                "requirements": ["one"],
                "blockers": ["legacy dep"],
            },
        )
        self.assertEqual(updated.status, 200, updated.raw)
        self.assertEqual(updated.json["card"]["owner"], "alice")
        self.assertEqual(updated.json["card"]["requirements"], ["one"])
        self.assertEqual(updated.json["card"]["blockers"], ["legacy dep"])

        fresh = self.create("Fresh", "task", owner="bob", blockers=["vendor"])
        self.assertEqual(fresh["owner"], "bob")

        # The migration is idempotent: a second start reapplies the schema
        # without clobbering migrated rows.
        self.store.close()
        self.store = PgCardStoreAdapter(self.dsn)
        self.app = make_app(self.store)

        reread = self.detail(legacy_id)["card"]
        self.assertEqual(reread["owner"], "alice")
        self.assertEqual(reread["requirements"], ["one"])
        self.assertEqual(reread["blockers"], ["legacy dep"])

        with PgCardStore(self.dsn) as direct:
            legacy_row = direct.get_card(int(legacy_id))
            self.assertEqual(legacy_row.owner, "alice")
            self.assertEqual(legacy_row.requirements, ("one",))
            self.assertEqual(legacy_row.blockers, ("legacy dep",))

    def test_unknown_and_malformed_ids_map_to_http_errors(self):
        for path in ("/cards/9999", "/cards/not-a-number"):
            resp = request(self.app, "GET", path)
            self.assertEqual(resp.status, 404, resp.raw)
            self.assertEqual(resp.json["error"]["code"], "not_found")

        resp = request(
            self.app,
            "PATCH",
            "/cards/not-a-number/status",
            body={"status": "TODO"},
        )
        self.assertEqual(resp.status, 404, resp.raw)
        self.assertEqual(resp.json["error"]["code"], "not_found")

        card = self.create("Existing", "task")

        resp = request(
            self.app,
            "POST",
            f"/cards/{card['id']}/dependencies",
            body={"depends_on_card_id": "nope"},
        )
        self.assertEqual(resp.status, 422, resp.raw)
        self.assertEqual(resp.json["error"]["code"], "dependency_card_not_found")

        resp = request(
            self.app,
            "POST",
            "/cards/424242/dependencies",
            body={"depends_on_card_id": card["id"]},
        )
        self.assertEqual(resp.status, 404, resp.raw)
        self.assertEqual(resp.json["error"]["code"], "not_found")

    def test_wsgi_entry_point_requires_explicit_dsn_env_var(self):
        module = self._load_wsgi_module()
        with self.assertRaises(module.ConfigError):
            module.load_dsn({})
        saved = os.environ.pop(module.DSN_ENV_VAR, None)
        try:
            self.assertEqual(module.main([]), 2)
        finally:
            if saved is not None:
                os.environ[module.DSN_ENV_VAR] = saved

    def test_wsgi_entry_point_uses_explicit_environment_variable(self):
        module = self._load_wsgi_module()
        try:
            health = request(module.application, "GET", "/health")
            self.assertEqual(health.status, 200, health.raw)
            self.assertEqual(health.json, {"status": "ok"})

            created = request(
                module.application,
                "POST",
                "/cards",
                body={"title": "From env", "card_type": "task"},
            )
            self.assertEqual(created.status, 201, created.raw)
            card_id = created.json["card"]["id"]
            self.assert_id_shape(card_id)

            with psycopg.connect(self.dsn) as conn:
                row = conn.execute(
                    "SELECT title FROM cards WHERE id = %s", (int(card_id),)
                ).fetchone()
            self.assertEqual(row, ("From env",))

            detail = request(module.application, "GET", f"/cards/{card_id}")
            self.assertEqual(detail.status, 200, detail.raw)
            self.assertEqual(detail.json["card"]["id"], card_id)
        finally:
            module.application.store.close()


if __name__ == "__main__":
    unittest.main()
