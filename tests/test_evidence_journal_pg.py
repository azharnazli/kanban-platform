"""Durable evidence journal regression tests over real PostgreSQL.

Proves that criterion-linked review evidence (criterion, verdict, tested
revision, actor, server-assigned timestamp) is persisted by the adapter in
the card_evidence table, survives closing and reopening the adapter, and
still satisfies the APPROVED workflow gate from the reopened connection.
Also proves the other evidence-dependent gates read persisted state after
reopening: integration evidence supports MERGED, and persisted acceptance
criteria plus reviews support the parent CLOSED gate (and still block it
when the review is absent). Also pins the journal store contract: verbatim
records, insertion order, kind filtering, and KeyError for unknown cards;
the adapter's close behavior; and the generic-500 contract when the journal
write or its connection fails.

Runs against a disposable PostgreSQL container started by pg_harness (tmpfs
data directory, random 127.0.0.1 port, trust auth; existing databases and
containers are never touched) and is skipped when docker or the cached image
is unavailable.
"""

import time
import unittest

import psycopg

from kanban_pg_adapter import PgCardStoreAdapter
from pg_harness import PostgresUnavailable, TemporaryPostgres

from .support import make_app, request


class EvidenceJournalPersistenceTest(unittest.TestCase):
    """Journal durability across adapter restarts and its store contract."""

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

    def create_card(self, **payload):
        resp = request(
            self.app,
            "POST",
            "/cards",
            body={"title": "Journaled", "card_type": "task", **payload},
        )
        self.assertEqual(resp.status, 201, resp.raw)
        return resp.json["card"]

    def add_review(self, card_id, criterion_id, verdict, revision, actor):
        return request(
            self.app,
            "POST",
            f"/cards/{card_id}/reviews",
            body={
                "criterion_id": criterion_id,
                "verdict": verdict,
                "tested_revision": revision,
                "actor": actor,
            },
        )

    def add_integration(self, card_id, revision, checks, ref="merge-1", actor="ci"):
        return request(
            self.app,
            "POST",
            f"/cards/{card_id}/integration-evidence",
            body={
                "tested_revision": revision,
                "integration_ref": ref,
                "checks": checks,
                "actor": actor,
            },
        )

    def set_status(self, card_id, status):
        return request(
            self.app,
            "PATCH",
            f"/cards/{card_id}/status",
            body={"status": status},
        )

    def reopen(self):
        """Restart the adapter and rebuild the app over the same database."""
        self.store.close()
        self.store = PgCardStoreAdapter(self.dsn)
        self.app = make_app(self.store)

    def assert_generic_500(self, resp):
        """The API contract for unexpected failures: no internals leaked."""
        self.assertEqual(resp.status, 500, resp.raw)
        self.assertEqual(
            resp.json,
            {
                "error": {
                    "code": "internal_error",
                    "message": "unexpected server error",
                    "details": {},
                }
            },
        )
        for leaked in (b"psycopg", b"card_evidence", b"Traceback"):
            self.assertNotIn(leaked, resp.raw)

    def test_criterion_evidence_survives_adapter_restart(self):
        card = self.create_card(acceptance_criteria=["ac-1", "ac-2"])
        first = self.add_review(card["id"], "ac-1", "approved", "rev-7", "alice")
        self.assertEqual(first.status, 201, first.raw)
        second = self.add_review(card["id"], "ac-2", "approved", "rev-7", "bob")
        self.assertEqual(second.status, 201, second.raw)
        records = [first.json["review"], second.json["review"]]

        self.store.close()
        self.store = PgCardStoreAdapter(self.dsn)
        self.app = make_app(self.store)

        listed = request(self.app, "GET", f"/cards/{card['id']}/reviews")
        self.assertEqual(listed.status, 200, listed.raw)
        self.assertEqual(listed.json, {"items": records, "count": 2})

        approved = request(
            self.app,
            "PATCH",
            f"/cards/{card['id']}/status",
            body={"status": "APPROVED"},
        )
        self.assertEqual(approved.status, 200, approved.raw)
        self.assertEqual(approved.json["card"]["status"], "APPROVED")

        with psycopg.connect(self.dsn) as conn:
            rows = conn.execute(
                "SELECT kind, record FROM card_evidence "
                "WHERE card_id = %s ORDER BY id",
                (int(card["id"]),),
            ).fetchall()
        self.assertEqual(
            [row[0] for row in rows],
            ["acceptance_criteria", "review", "review"],
        )
        for stored, record in zip(rows[1:], records):
            for field in (
                "criterion_id",
                "verdict",
                "tested_revision",
                "actor",
                "created_at",
            ):
                self.assertEqual(stored[1][field], record[field])

    def test_journal_contract_persists_verbatim_in_insertion_order(self):
        card = self.create_card()
        review_record = {
            "criterion_id": "ac-9",
            "verdict": "approved",
            "tested_revision": "rev-1",
            "actor": "alice",
            "created_at": "2026-10-15T00:00:00+00:00",
        }
        integration_record = {
            "tested_revision": "rev-1",
            "integration_ref": "merge-1",
            "checks": [{"name": "ci", "status": "passed"}],
            "actor": "ci",
            "created_at": "2026-10-15T00:01:00+00:00",
        }
        first_id = self.store.add_evidence(card["id"], "review", review_record)
        second_id = self.store.add_evidence(
            card["id"], "integration", integration_record
        )
        self.assertIsInstance(first_id, str)
        self.assertIsInstance(second_id, str)
        self.assertNotEqual(first_id, second_id)

        everything = self.store.list_evidence(card["id"])
        self.assertEqual(
            [record["id"] for record in everything], [first_id, second_id]
        )
        self.assertEqual(everything[0], {**review_record, "id": first_id})
        self.assertEqual(everything[1], {**integration_record, "id": second_id})
        self.assertEqual(
            self.store.list_evidence(card["id"], "review"),
            [{**review_record, "id": first_id}],
        )
        self.assertEqual(self.store.list_evidence(card["id"], "absent"), [])

        with self.assertRaises(KeyError):
            self.store.add_evidence("999999", "review", review_record)
        with self.assertRaises(KeyError):
            self.store.list_evidence("999999")
        with self.assertRaises(KeyError):
            self.store.list_evidence("not-a-number")

    def test_integration_evidence_supports_merged_after_adapter_restart(self):
        card = self.create_card()
        failed = self.add_integration(
            card["id"], "rev-1", [{"name": "ci", "status": "failed"}]
        )
        self.assertEqual(failed.status, 201, failed.raw)
        passed = self.add_integration(
            card["id"], "rev-1", [{"name": "ci", "status": "passed"}]
        )
        self.assertEqual(passed.status, 201, passed.raw)
        records = [
            failed.json["integration_evidence"],
            passed.json["integration_evidence"],
        ]

        self.reopen()

        listed = request(
            self.app, "GET", f"/cards/{card['id']}/integration-evidence"
        )
        self.assertEqual(listed.status, 200, listed.raw)
        self.assertEqual(listed.json, {"items": records, "count": 2})

        merged = self.set_status(card["id"], "MERGED")
        self.assertEqual(merged.status, 200, merged.raw)
        self.assertEqual(merged.json["card"]["status"], "MERGED")

        with psycopg.connect(self.dsn) as conn:
            rows = conn.execute(
                "SELECT kind, record FROM card_evidence "
                "WHERE card_id = %s ORDER BY id",
                (int(card["id"]),),
            ).fetchall()
        self.assertEqual([row[0] for row in rows], ["integration", "integration"])
        for stored, record in zip(rows, records):
            for field in (
                "tested_revision",
                "integration_ref",
                "checks",
                "actor",
                "created_at",
            ):
                self.assertEqual(stored[1][field], record[field])

    def test_parent_closure_uses_persisted_reviews_after_adapter_restart(self):
        epic = self.create_card(
            title="Epic", card_type="project_epic", acceptance_criteria=["ac-1"]
        )
        child = self.create_card(
            title="Child", card_type="feature", parent_id=epic["id"]
        )
        closed_child = self.set_status(child["id"], "CLOSED")
        self.assertEqual(closed_child.status, 200, closed_child.raw)
        review = self.add_review(epic["id"], "ac-1", "approved", "rev-1", "alice")
        self.assertEqual(review.status, 201, review.raw)

        self.reopen()

        listed = request(self.app, "GET", f"/cards/{epic['id']}/reviews")
        self.assertEqual(listed.status, 200, listed.raw)
        self.assertEqual(
            listed.json, {"items": [review.json["review"]], "count": 1}
        )

        closed = self.set_status(epic["id"], "CLOSED")
        self.assertEqual(closed.status, 200, closed.raw)
        self.assertEqual(closed.json["card"]["status"], "CLOSED")

        with psycopg.connect(self.dsn) as conn:
            rows = conn.execute(
                "SELECT kind FROM card_evidence WHERE card_id = %s ORDER BY id",
                (int(epic["id"]),),
            ).fetchall()
        self.assertEqual([row[0] for row in rows], ["acceptance_criteria", "review"])

    def test_parent_closure_blocks_without_review_after_adapter_restart(self):
        epic = self.create_card(
            title="Epic", card_type="project_epic", acceptance_criteria=["ac-1"]
        )
        child = self.create_card(
            title="Child", card_type="feature", parent_id=epic["id"]
        )
        self.assertEqual(self.set_status(child["id"], "CLOSED").status, 200)

        self.reopen()

        blocked = self.set_status(epic["id"], "CLOSED")
        self.assertEqual(blocked.status, 409, blocked.raw)
        self.assertEqual(blocked.json["error"]["code"], "acceptance_checks_pending")
        self.assertEqual(
            blocked.json["error"]["details"]["pending_checks"], ["ac-1"]
        )
        self.assertIsNone(blocked.json["error"]["details"]["current_revision"])

        review = self.add_review(epic["id"], "ac-1", "approved", "rev-1", "alice")
        self.assertEqual(review.status, 201, review.raw)
        closed = self.set_status(epic["id"], "CLOSED")
        self.assertEqual(closed.status, 200, closed.raw)

    def test_adapter_close_releases_connections_and_repeats_safely(self):
        card = self.create_card()
        with psycopg.connect(self.dsn, autocommit=True) as conn:

            def other_client_backends():
                return conn.execute(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() "
                    "AND pid <> pg_backend_pid() "
                    "AND backend_type = 'client backend'"
                ).fetchone()[0]

            self.assertGreaterEqual(other_client_backends(), 2)
            self.store.close()
            deadline = time.monotonic() + 5.0
            while other_client_backends() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertEqual(other_client_backends(), 0)
            self.store.close()

        self.store = PgCardStoreAdapter(self.dsn)
        self.app = make_app(self.store)
        fetched = request(self.app, "GET", f"/cards/{card['id']}")
        self.assertEqual(fetched.status, 200, fetched.raw)
        self.assertEqual(fetched.json["card"], card)

    def test_journal_write_failure_becomes_generic_500(self):
        card = self.create_card(acceptance_criteria=["ac-1"])
        before = request(self.app, "GET", f"/cards/{card['id']}/reviews")
        self.assertEqual(before.status, 200, before.raw)
        self.assertEqual(before.json, {"items": [], "count": 0})

        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute(
                "ALTER TABLE card_evidence DROP CONSTRAINT IF EXISTS "
                "evidence_write_failure_probe"
            )
            conn.execute(
                "ALTER TABLE card_evidence ADD CONSTRAINT "
                "evidence_write_failure_probe CHECK (kind <> 'review')"
            )
        try:
            resp = self.add_review(card["id"], "ac-1", "approved", "rev-1", "alice")
            self.assert_generic_500(resp)
            with psycopg.connect(self.dsn) as conn:
                persisted = conn.execute(
                    "SELECT count(*) FROM card_evidence WHERE kind = 'review'"
                ).fetchone()[0]
            self.assertEqual(persisted, 0)
        finally:
            with psycopg.connect(self.dsn, autocommit=True) as conn:
                conn.execute(
                    "ALTER TABLE card_evidence DROP CONSTRAINT IF EXISTS "
                    "evidence_write_failure_probe"
                )

    def test_journal_connection_loss_becomes_generic_500(self):
        card = self.create_card(acceptance_criteria=["ac-1"])
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            terminated = conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = current_database() "
                "AND pid <> pg_backend_pid() "
                "AND backend_type = 'client backend'"
            ).fetchall()
        self.assertGreaterEqual(len(terminated), 2)

        resp = self.add_review(card["id"], "ac-1", "approved", "rev-1", "alice")
        self.assert_generic_500(resp)
        with psycopg.connect(self.dsn) as conn:
            persisted = conn.execute(
                "SELECT count(*) FROM card_evidence WHERE kind = 'review'"
            ).fetchone()[0]
        self.assertEqual(persisted, 0)


if __name__ == "__main__":
    unittest.main()
