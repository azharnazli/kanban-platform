"""Regression tests: concurrent dependency writes over real PostgreSQL.

All PostgreSQL dependency writers share one serialization point: the session
advisory lock inside kanban_pg.CardStore.add_dependency. These tests race
writers through that path - adapter vs adapter, and mixed direct CardStore vs
PgCardStoreAdapter - on opposing edges A->B and B->A. A third connection
holds FOR UPDATE row locks on both cards until PostgreSQL itself reports
every writer blocked on a lock, which guarantees that each writer completed
its read-then-write cycle check before either insert could commit. Without
serialization both inserts pass that check and a cycle is committed; with
the shared lock exactly one insert commits and the other is rejected with
ValidationError, leaving an acyclic graph.

One HTTP test drives the WSGI app with a store wrapper that commits the
opposing edge after the HTTP layer's cycle pre-check but before the store's
serialized cycle check, proving the race loser surfaces as 409
dependency_cycle instead of an internal error.

The tests start their own disposable PostgreSQL container through
pg_harness.py (tmpfs data directory, random 127.0.0.1 port, trust auth;
existing containers and databases are never touched) and are skipped when
docker or the cached PostgreSQL image is unavailable.
"""

from __future__ import annotations

import threading
import time
import unittest

import psycopg

from kanban_pg import CardStore, ValidationError
from kanban_pg_adapter import PgCardStoreAdapter
from pg_harness import PostgresUnavailable, TemporaryPostgres

from .support import make_app, request

_APP_NAMES = (
    "kanban-dep-race-a",
    "kanban-dep-race-b",
    "kanban-dep-race-direct",
)

_CYCLE_QUERY = (
    "WITH RECURSIVE reach(node, target) AS ("
    "    SELECT card_id, depends_on_card_id FROM card_dependencies"
    "    UNION"
    "    SELECT reach.node, d.depends_on_card_id"
    "    FROM reach JOIN card_dependencies d ON d.card_id = reach.target"
    ")"
    " SELECT count(*) FROM reach WHERE node = target"
)


def _tagged_dsn(base: str, application_name: str) -> str:
    separator = "&" if "?" in base else "?"
    return f"{base}{separator}application_name={application_name}"


class _RaceWindowStore:
    """Commit a competing edge after the HTTP pre-check, then delegate.

    The HTTP layer runs its own cycle pre-check before calling the store.
    When the handler calls add_dependency, this wrapper first commits the
    opposing edge through a separate direct CardStore session - exactly the
    interleaving a concurrent writer produces - and only then delegates to
    the real adapter, whose serialized cycle check must reject the losing
    edge.
    """

    def __init__(self, inner, competing: CardStore, edge):
        self._inner = inner
        self._competing = competing
        self._edge = edge

    def add_dependency(self, card_id, depends_on_card_id):
        self._competing.add_dependency(*self._edge)
        return self._inner.add_dependency(card_id, depends_on_card_id)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class DependencyWriteSerializationTest(unittest.TestCase):
    """Opposing writers must commit at most one edge and stay acyclic."""

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
        self.first = PgCardStoreAdapter(_tagged_dsn(self.dsn, _APP_NAMES[0]))
        self.second = PgCardStoreAdapter(_tagged_dsn(self.dsn, _APP_NAMES[1]))
        self.direct = CardStore(_tagged_dsn(self.dsn, _APP_NAMES[2]))
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute("TRUNCATE cards RESTART IDENTITY CASCADE")

    def tearDown(self):
        self.first.close()
        self.second.close()
        self.direct.close()

    # -- adapter vs adapter ------------------------------------------------

    def test_opposing_concurrent_inserts_commit_at_most_one(self):
        left = self.first.create_card("Left", "task")
        right = self.second.create_card("Right", "task")

        successes, failures = self._race(
            [
                ("first", self.first, left, right),
                ("second", self.second, right, left),
            ],
            (left, right),
        )

        self.assertEqual(
            len(successes),
            1,
            f"exactly one of the two conflicting inserts may commit; "
            f"successes={successes} failures={self._describe(failures)}",
        )
        self.assertEqual(
            len(failures), 1, f"one insert must be rejected; {self._describe(failures)}"
        )
        _, error = failures[0]
        self.assertIsInstance(error, ValidationError)
        self.assertIn("cycle", str(error).lower())

        edges = self._edges()
        self.assertEqual(
            len(edges), 1, f"final graph must hold exactly one edge, got {edges}"
        )
        self.assertEqual(set(edges[0]), {int(left), int(right)})
        self.assertEqual(self._cycle_count(), 0, "final graph must be acyclic")

    def test_compatible_concurrent_inserts_both_persist(self):
        source = self.first.create_card("Source", "task")
        one = self.first.create_card("One", "task")
        two = self.second.create_card("Two", "task")

        successes, failures = self._race(
            [
                ("first", self.first, source, one),
                ("second", self.second, source, two),
            ],
            (one, two),
        )

        self.assertEqual(
            failures, [], f"compatible inserts must both commit: {self._describe(failures)}"
        )
        self.assertEqual(sorted(successes), ["first", "second"])
        self.assertEqual(len(self._edges()), 2)
        self.assertEqual(self._cycle_count(), 0, "final graph must be acyclic")

    # -- mixed direct-store vs adapter writers -----------------------------

    def test_mixed_writers_opposing_edges_commit_at_most_one(self):
        """Direct CardStore and PgCardStoreAdapter race opposing edges.

        Both writers are released behind the row-lock barrier after both
        completed their read-then-write cycle checks, so only the shared
        serialization in CardStore.add_dependency can prevent a cycle.
        """
        left = self.first.create_card("Left", "task")
        right = self.second.create_card("Right", "task")

        successes, failures = self._race(
            [
                ("direct", self.direct, int(left), int(right)),
                ("adapter", self.first, right, left),
            ],
            (left, right),
        )

        self.assertEqual(
            len(successes),
            1,
            f"exactly one of the mixed-writer inserts may commit; "
            f"successes={successes} failures={self._describe(failures)}",
        )
        self.assertEqual(
            len(failures),
            1,
            f"one mixed-writer insert must be rejected; {self._describe(failures)}",
        )
        _, error = failures[0]
        self.assertIsInstance(error, ValidationError)
        self.assertIn("cycle", str(error).lower())

        edges = self._edges()
        self.assertEqual(
            len(edges), 1, f"final graph must hold exactly one edge, got {edges}"
        )
        self.assertEqual(set(edges[0]), {int(left), int(right)})
        self.assertEqual(self._cycle_count(), 0, "final graph must be acyclic")

    def test_mixed_writers_repeated_races_stay_acyclic(self):
        """Ten unguarded mixed-writer races: whoever wins, one must lose."""
        for iteration in range(10):
            left = self.first.create_card(f"Left {iteration}", "task")
            right = self.second.create_card(f"Right {iteration}", "task")

            successes, failures = self._simultaneous(
                [
                    ("direct", self.direct, int(left), int(right)),
                    ("adapter", self.first, right, left),
                ]
            )

            self.assertEqual(
                len(successes),
                1,
                f"round {iteration}: exactly one insert may commit; "
                f"successes={successes} failures={self._describe(failures)}",
            )
            self.assertEqual(
                len(failures),
                1,
                f"round {iteration}: one insert must be rejected; "
                f"{self._describe(failures)}",
            )
            _, error = failures[0]
            self.assertIsInstance(error, ValidationError)
            self.assertIn("cycle", str(error).lower())

        self.assertEqual(self._cycle_count(), 0, "final graph must be acyclic")

    # -- HTTP race-loser mapping -------------------------------------------

    def test_http_cycle_race_loser_maps_to_409_dependency_cycle(self):
        first = self.first.create_card("First", "task")
        second = self.first.create_card("Second", "task")
        racing = _RaceWindowStore(self.first, self.direct, (int(second), int(first)))
        app = make_app(racing)

        resp = request(
            app,
            "POST",
            f"/cards/{first}/dependencies",
            body={"depends_on_card_id": second},
        )

        self.assertEqual(resp.status, 409, resp.raw)
        self.assertEqual(resp.json["error"]["code"], "dependency_cycle")
        self.assertEqual(resp.json["error"]["details"]["path"], [second, first])

        self.assertEqual(
            self._edges(),
            [(int(second), int(first))],
            "only the directly committed edge may persist",
        )
        self.assertEqual(self._cycle_count(), 0, "final graph must be acyclic")

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _describe(failures):
        return [(name, repr(error)) for name, error in failures]

    def _edges(self):
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            return conn.execute(
                "SELECT card_id, depends_on_card_id FROM card_dependencies"
            ).fetchall()

    def _cycle_count(self):
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            return conn.execute(_CYCLE_QUERY).fetchone()[0]

    def _race(self, tasks, locked_card_ids):
        """Run add_dependency tasks concurrently behind a row-lock barrier.

        The cards in locked_card_ids stay FOR UPDATE locked until
        _wait_until_blocked observes every writer waiting on a lock inside
        PostgreSQL, proving all cycle checks completed before the locks are
        released. Returns (successes, failures): success names and
        (name, exception) pairs.
        """
        blocker = psycopg.connect(self.dsn)
        collected = threading.Lock()
        successes = []
        failures = []
        barrier = threading.Barrier(len(tasks))

        def worker(name, writer, card_id, depends_on_card_id):
            barrier.wait(timeout=10)
            try:
                writer.add_dependency(card_id, depends_on_card_id)
            except Exception as exc:
                with collected:
                    failures.append((name, exc))
            else:
                with collected:
                    successes.append(name)

        try:
            blocker.execute(
                "SELECT id FROM cards WHERE id = ANY(%s) FOR UPDATE",
                (sorted(int(card_id) for card_id in locked_card_ids),),
            )
            threads = [
                threading.Thread(target=worker, args=task, daemon=True)
                for task in tasks
            ]
            for thread in threads:
                thread.start()
            self._wait_until_blocked(blocker, len(tasks))
            blocker.commit()
            for thread in threads:
                thread.join(timeout=30)
                self.assertFalse(thread.is_alive(), "dependency writer did not finish")
        finally:
            blocker.close()
        return successes, failures

    def _simultaneous(self, tasks):
        """Run add_dependency tasks concurrently with a start barrier only."""
        collected = threading.Lock()
        successes = []
        failures = []
        barrier = threading.Barrier(len(tasks))

        def worker(name, writer, card_id, depends_on_card_id):
            barrier.wait(timeout=10)
            try:
                writer.add_dependency(card_id, depends_on_card_id)
            except Exception as exc:
                with collected:
                    failures.append((name, exc))
            else:
                with collected:
                    successes.append(name)

        threads = [
            threading.Thread(target=worker, args=task, daemon=True) for task in tasks
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
            self.assertFalse(thread.is_alive(), "dependency writer did not finish")
        return successes, failures

    def _wait_until_blocked(self, watcher, expected, timeout=15.0):
        deadline = time.monotonic() + timeout
        activity = []
        while time.monotonic() < deadline:
            activity = watcher.execute(
                "SELECT application_name, wait_event_type, wait_event, state "
                "FROM pg_stat_activity WHERE application_name = ANY(%s)",
                (list(_APP_NAMES),),
            ).fetchall()
            blocked = [row for row in activity if row[1] == "Lock"]
            if len(blocked) >= expected:
                return
            time.sleep(0.01)
        self.fail(
            f"expected {expected} dependency writer(s) blocked inside "
            f"PostgreSQL before releasing the row locks; pg_stat_activity "
            f"showed: {activity}"
        )


if __name__ == "__main__":
    unittest.main()
