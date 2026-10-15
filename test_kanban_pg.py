import unittest

import psycopg

from kanban_pg import (
    DEFAULT_STATUS,
    STATUSES,
    CardStore,
    ValidationError,
)
from pg_harness import PostgresUnavailable, TemporaryPostgres


class CardStoreIntegrationTest(unittest.TestCase):
    """Integration tests against a disposable real PostgreSQL container."""

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
        self.store = CardStore(self.dsn)
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute("TRUNCATE cards RESTART IDENTITY CASCADE")

    def tearDown(self):
        self.store.close()

    def _raw_conn(self):
        return psycopg.connect(self.dsn, autocommit=True)

    def test_hierarchy_parent_links(self):
        project_epic = self.store.create_card("Platform", "project_epic")
        feature = self.store.create_card(
            "Billing", "feature", parent_id=project_epic.id
        )
        task = self.store.create_card("Invoices", "task", parent_id=feature.id)

        self.assertIsNone(project_epic.parent_id)
        self.assertEqual(feature.parent_id, project_epic.id)
        self.assertEqual(task.parent_id, feature.id)
        self.assertEqual(
            [card.id for card in self.store.list_cards()],
            [project_epic.id, feature.id, task.id],
        )

    def test_standalone_feature_and_task_allowed(self):
        feature = self.store.create_card("Standalone feature", "feature")
        task = self.store.create_card("Standalone task", "task")

        self.assertIsNone(feature.parent_id)
        self.assertIsNone(task.parent_id)

    def test_rejects_invalid_parent(self):
        project_epic = self.store.create_card("Platform", "project_epic")
        feature = self.store.create_card(
            "Billing", "feature", parent_id=project_epic.id
        )

        with self.assertRaises(ValidationError):
            self.store.create_card(
                "Nested project epic", "project_epic", parent_id=project_epic.id
            )
        with self.assertRaises(ValidationError):
            self.store.create_card("Task under epic", "task", parent_id=project_epic.id)
        with self.assertRaises(ValidationError):
            self.store.create_card("Feature under task", "feature", parent_id=feature.id)
        with self.assertRaises(ValidationError):
            self.store.create_card("Task under missing", "task", parent_id=9999)
        with self.assertRaises(ValidationError):
            self.store.create_card("Task under bool", "task", parent_id=True)

        self.assertEqual(len(self.store.list_cards()), 2)

    def test_rejects_invalid_title_and_type(self):
        for bad_title in ("", "   ", 42, None, "x" * 201):
            with self.assertRaises(ValidationError):
                self.store.create_card(bad_title, "task")

        for bad_type in ("story", "project", "epic", None):
            with self.assertRaises(ValidationError):
                self.store.create_card("Bad type", bad_type)

        card = self.store.create_card("  trimmed  ", "task")
        self.assertEqual(card.title, "trimmed")

    def test_default_status_is_todo(self):
        card = self.store.create_card("Defaults", "task")
        self.assertEqual(card.status, DEFAULT_STATUS)
        self.assertEqual(self.store.get_card(card.id).status, DEFAULT_STATUS)

    def test_all_nine_statuses_preserved(self):
        card = self.store.create_card("Workflow", "task")

        for status in STATUSES:
            updated = self.store.update_status(card.id, status)
            self.assertEqual(updated.status, status)
            self.assertEqual(self.store.get_card(card.id).status, status)

        self.assertEqual(
            tuple(STATUSES),
            (
                "TODO",
                "READY",
                "IN_PROGRESS",
                "REVIEW",
                "CHANGES_REQUESTED",
                "APPROVED",
                "MERGED",
                "CLOSED",
                "BLOCKED",
            ),
        )

    def test_rejects_invalid_status(self):
        card = self.store.create_card("Workflow", "task")

        with self.assertRaises(ValidationError):
            self.store.create_card("Bad status", "task", status="DONE")
        with self.assertRaises(ValidationError):
            self.store.update_status(card.id, "done")
        with self.assertRaises(ValidationError):
            self.store.update_status(card.id, None)
        with self.assertRaises(ValidationError):
            self.store.update_status(9999, "TODO")

        self.assertEqual(self.store.get_card(card.id).status, "TODO")

    def test_dependencies_are_separate_from_hierarchy(self):
        project_epic = self.store.create_card("Platform", "project_epic")
        feature = self.store.create_card(
            "Billing", "feature", parent_id=project_epic.id
        )
        first = self.store.create_card("First task", "task", parent_id=feature.id)
        second = self.store.create_card("Second task", "task", parent_id=feature.id)

        self.assertEqual(self.store.list_dependencies(first.id), [])
        dependency = self.store.add_dependency(second.id, first.id)

        self.assertEqual(dependency.card_id, second.id)
        self.assertEqual(dependency.depends_on_card_id, first.id)
        self.assertEqual(
            [edge.depends_on_card_id for edge in self.store.list_dependencies(second.id)],
            [first.id],
        )
        self.assertEqual(self.store.list_dependencies(first.id), [])

        self.assertEqual(self.store.get_card(second.id).parent_id, feature.id)
        self.assertEqual(self.store.get_card(first.id).parent_id, feature.id)

        self.assertTrue(self.store.remove_dependency(second.id, first.id))
        self.assertFalse(self.store.remove_dependency(second.id, first.id))
        self.assertEqual(self.store.list_dependencies(second.id), [])

    def test_rejects_invalid_dependencies(self):
        first = self.store.create_card("First", "task")
        second = self.store.create_card("Second", "task")

        with self.assertRaises(ValidationError):
            self.store.add_dependency(first.id, first.id)
        with self.assertRaises(ValidationError):
            self.store.add_dependency(first.id, 9999)
        with self.assertRaises(ValidationError):
            self.store.add_dependency(9999, first.id)
        with self.assertRaises(ValidationError):
            self.store.add_dependency(first.id, True)

        self.store.add_dependency(first.id, second.id)
        with self.assertRaises(ValidationError):
            self.store.add_dependency(first.id, second.id)
        self.assertEqual(len(self.store.list_dependencies(first.id)), 1)

    def test_rejects_direct_dependency_cycle(self):
        first = self.store.create_card("First", "task")
        second = self.store.create_card("Second", "task")

        self.store.add_dependency(second.id, first.id)

        with self.assertRaises(ValidationError):
            self.store.add_dependency(first.id, second.id)

        self.assertEqual(
            [edge.depends_on_card_id for edge in self.store.list_dependencies(second.id)],
            [first.id],
        )
        self.assertEqual(self.store.list_dependencies(first.id), [])

    def test_rejects_transitive_dependency_cycle(self):
        first = self.store.create_card("First", "task")
        second = self.store.create_card("Second", "task")
        third = self.store.create_card("Third", "task")
        fourth = self.store.create_card("Fourth", "task")

        self.store.add_dependency(second.id, first.id)
        self.store.add_dependency(third.id, second.id)
        self.store.add_dependency(fourth.id, third.id)

        with self.assertRaises(ValidationError):
            self.store.add_dependency(first.id, fourth.id)
        with self.assertRaises(ValidationError):
            self.store.add_dependency(second.id, fourth.id)

        self.store.add_dependency(fourth.id, first.id)

        self.assertEqual(
            [edge.depends_on_card_id for edge in self.store.list_dependencies(fourth.id)],
            [first.id, third.id],
        )
        self.assertEqual(self.store.list_dependencies(first.id), [])

    def test_storage_layer_constraints_hold_without_adapter(self):
        with self._raw_conn() as conn:
            epic_id = conn.execute(
                "INSERT INTO cards (title, type) VALUES ('Platform', 'project_epic') "
                "RETURNING id"
            ).fetchone()[0]
            feature_id = conn.execute(
                "INSERT INTO cards (title, type, parent_id) "
                "VALUES ('Billing', 'feature', %s) RETURNING id",
                (epic_id,),
            ).fetchone()[0]
            task_id = conn.execute(
                "INSERT INTO cards (title, type, parent_id) "
                "VALUES ('Invoices', 'task', %s) RETURNING id",
                (feature_id,),
            ).fetchone()[0]

            with self.assertRaises(psycopg.errors.CheckViolation):
                conn.execute(
                    "INSERT INTO cards (title, type) VALUES ('Bad', 'story')"
                )
            with self.assertRaises(psycopg.errors.CheckViolation):
                conn.execute(
                    "INSERT INTO cards (title, type, status) "
                    "VALUES ('Bad', 'task', 'DONE')"
                )
            with self.assertRaises(psycopg.errors.CheckViolation):
                conn.execute(
                    "INSERT INTO cards (title, type, parent_id) "
                    "VALUES ('Bad', 'project_epic', %s)",
                    (epic_id,),
                )
            with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                conn.execute(
                    "INSERT INTO cards (title, type, parent_id) "
                    "VALUES ('Bad', 'task', %s)",
                    (epic_id,),
                )
            with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                conn.execute(
                    "INSERT INTO cards (title, type, parent_id) "
                    "VALUES ('Bad', 'feature', %s)",
                    (task_id,),
                )
            with self.assertRaises(psycopg.errors.CheckViolation):
                conn.execute(
                    "INSERT INTO card_dependencies (card_id, depends_on_card_id) "
                    "VALUES (%s, %s)",
                    (task_id, task_id),
                )
            with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                conn.execute(
                    "INSERT INTO card_dependencies (card_id, depends_on_card_id) "
                    "VALUES (%s, 9999)",
                    (task_id,),
                )

            conn.execute(
                "INSERT INTO card_dependencies (card_id, depends_on_card_id) "
                "VALUES (%s, %s)",
                (task_id, feature_id),
            )
            with self.assertRaises(psycopg.errors.UniqueViolation):
                conn.execute(
                    "INSERT INTO card_dependencies (card_id, depends_on_card_id) "
                    "VALUES (%s, %s)",
                    (task_id, feature_id),
                )

    def test_data_survives_reopening_connection(self):
        project_epic = self.store.create_card("Platform", "project_epic")
        feature = self.store.create_card(
            "Login", "feature", parent_id=project_epic.id
        )
        self.store.add_dependency(project_epic.id, feature.id)
        self.store.update_status(feature.id, "READY")
        self.store.close()

        self.store = CardStore(self.dsn)

        reloaded_feature = self.store.get_card(feature.id)
        self.assertEqual(reloaded_feature.parent_id, project_epic.id)
        self.assertEqual(reloaded_feature.status, "READY")
        self.assertEqual(
            [edge.depends_on_card_id for edge in self.store.list_dependencies(project_epic.id)],
            [feature.id],
        )

        new_card = self.store.create_card("After reopen", "task")
        self.assertIsNotNone(self.store.get_card(new_card.id))


if __name__ == "__main__":
    unittest.main()
