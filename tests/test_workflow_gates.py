"""Functional tests for the Kanban V1 workflow gates.

Covers the READY/IN_PROGRESS readiness gate, the APPROVED approval gate
(criterion-linked reviews at a tested revision), the MERGED integration
evidence gate, and the CLOSED parent-closure gate, plus allowed transitions
and bypass attempts, driven in-process through the WSGI app.

Everything runs against the in-memory reference double (and a local
evidence-journal double defined here): no sockets, no databases, no docker.
"""

import unittest
from datetime import datetime, timedelta

from kanban_http.memory import InMemoryCardStore

from .support import make_app, request


class EvidenceMemoryStore(InMemoryCardStore):
    """InMemoryCardStore plus the optional evidence journal.

    Mirrors the EvidenceStore protocol in kanban_http/domain.py:
    add_evidence persists the record verbatim and returns a store-assigned
    id; list_evidence returns records in insertion order with their ids.
    """

    def __init__(self, dsn=None):
        super().__init__(dsn)
        self._evidence = {}
        self._next_evidence_id = 0

    def add_evidence(self, card_id, kind, record):
        if card_id not in self._cards:
            raise KeyError(card_id)
        self._next_evidence_id += 1
        evidence_id = str(self._next_evidence_id)
        self._evidence.setdefault(card_id, []).append(
            (kind, dict(record), evidence_id)
        )
        return evidence_id

    def list_evidence(self, card_id, kind=None):
        if card_id not in self._cards:
            raise KeyError(card_id)
        items = []
        for stored_kind, record, evidence_id in self._evidence.get(card_id, []):
            if kind is None or stored_kind == kind:
                item = dict(record)
                item["id"] = evidence_id
                items.append(item)
        return items


class GateTestCase(unittest.TestCase):
    store_class = EvidenceMemoryStore

    def setUp(self):
        self.store = self.store_class()
        self.app = make_app(self.store)

    def create(self, title="Card", card_type="task", **payload):
        resp = request(
            self.app,
            "POST",
            "/cards",
            body={"title": title, "card_type": card_type, **payload},
        )
        self.assertEqual(resp.status, 201, resp.raw)
        return resp.json["card"]

    def set_status(self, card_id, status):
        return request(
            self.app, "PATCH", f"/cards/{card_id}/status", body={"status": status}
        )

    def add_review(self, card_id, criterion_id, verdict, revision, actor="qa"):
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

    def assert_error(self, resp, status, code):
        self.assertEqual(resp.status, status, resp.raw)
        self.assertEqual(resp.json["error"]["code"], code, resp.raw)
        return resp.json["error"]["details"]

    def assert_utc_stamp(self, value):
        parsed = datetime.fromisoformat(value)
        self.assertIsNotNone(parsed.tzinfo, f"{value!r} has no timezone")
        self.assertEqual(
            parsed.utcoffset(), timedelta(0), f"{value!r} is not UTC"
        )


class ReadinessGateTest(GateTestCase):
    """READY/IN_PROGRESS require every dependency to be CLOSED."""

    def test_ready_requires_closed_dependencies(self):
        dependency = self.create("Prereq")
        card = self.create("Dependent")
        added = request(
            self.app,
            "POST",
            f"/cards/{card['id']}/dependencies",
            body={"depends_on_card_id": dependency["id"]},
        )
        self.assertEqual(added.status, 201, added.raw)

        details = self.assert_error(
            self.set_status(card["id"], "READY"),
            409,
            "dependencies_not_closed",
        )
        self.assertEqual(details["open_dependencies"], [dependency["id"]])

    def test_in_progress_requires_closed_dependencies(self):
        dependency = self.create("Prereq")
        card = self.create("Dependent")
        request(
            self.app,
            "POST",
            f"/cards/{card['id']}/dependencies",
            body={"depends_on_card_id": dependency["id"]},
        )

        self.assert_error(
            self.set_status(card["id"], "IN_PROGRESS"),
            409,
            "dependencies_not_closed",
        )

    def test_ready_allowed_after_dependency_closed(self):
        dependency = self.create("Prereq")
        card = self.create("Dependent")
        request(
            self.app,
            "POST",
            f"/cards/{card['id']}/dependencies",
            body={"depends_on_card_id": dependency["id"]},
        )
        self.assertEqual(
            self.set_status(dependency["id"], "CLOSED").status, 200
        )

        resp = self.set_status(card["id"], "READY")
        self.assertEqual(resp.status, 200, resp.raw)
        self.assertEqual(resp.json["card"]["status"], "READY")

    def test_reasserting_gated_status_is_a_noop_without_dependencies(self):
        card = self.create("Ready card", status="READY")
        resp = self.set_status(card["id"], "READY")
        self.assertEqual(resp.status, 200, resp.raw)
        self.assertEqual(resp.json["card"]["status"], "READY")

    def test_missing_dependency_card_counts_as_open(self):
        dependency = self.create("Prereq")
        card = self.create("Dependent")
        request(
            self.app,
            "POST",
            f"/cards/{card['id']}/dependencies",
            body={"depends_on_card_id": dependency["id"]},
        )
        del self.store._cards[dependency["id"]]

        details = self.assert_error(
            self.set_status(card["id"], "READY"),
            409,
            "dependencies_not_closed",
        )
        self.assertEqual(details["open_dependencies"], [dependency["id"]])

    def test_ungated_transitions_remain_unrestricted(self):
        card = self.create("Card")
        for status in ("BLOCKED", "REVIEW", "CHANGES_REQUESTED", "TODO"):
            resp = self.set_status(card["id"], status)
            self.assertEqual(resp.status, 200, resp.raw)
            self.assertEqual(resp.json["card"]["status"], status)

    def test_dependency_cycles_are_rejected(self):
        first = self.create("First")
        second = self.create("Second")
        request(
            self.app,
            "POST",
            f"/cards/{second['id']}/dependencies",
            body={"depends_on_card_id": first["id"]},
        )

        self.assert_error(
            request(
                self.app,
                "POST",
                f"/cards/{first['id']}/dependencies",
                body={"depends_on_card_id": second["id"]},
            ),
            409,
            "dependency_cycle",
        )
        self.assert_error(
            request(
                self.app,
                "POST",
                f"/cards/{first['id']}/dependencies",
                body={"depends_on_card_id": first["id"]},
            ),
            409,
            "dependency_cycle",
        )

        ready = self.set_status(first["id"], "READY")
        self.assertEqual(ready.status, 200, ready.raw)


class ApprovalGateTest(GateTestCase):
    """APPROVED requires criterion-linked reviews at the current revision."""

    def make_card(self, criteria=("ac-1", "ac-2")):
        return self.create("Approvable", acceptance_criteria=list(criteria))

    def test_creating_with_criteria_requires_evidence_journal(self):
        plain_store = InMemoryCardStore()
        app = make_app(plain_store)
        resp = request(
            app,
            "POST",
            "/cards",
            body={
                "title": "No journal",
                "card_type": "task",
                "acceptance_criteria": ["ac-1"],
            },
        )
        self.assert_error(resp, 409, "evidence_store_required")

    def test_approved_without_declared_criteria_is_rejected(self):
        card = self.create("Criteria-less")
        details = self.assert_error(
            self.set_status(card["id"], "APPROVED"), 409, "approval_required"
        )
        self.assertEqual(details["reason"], "no_acceptance_criteria")
        self.assertEqual(details["missing_criteria"], [])

    def test_approved_without_reviews_is_rejected(self):
        card = self.make_card()
        details = self.assert_error(
            self.set_status(card["id"], "APPROVED"), 409, "approval_required"
        )
        self.assertEqual(details["reason"], "revision_unknown")
        self.assertIsNone(details["current_revision"])
        self.assertEqual(details["missing_criteria"], ["ac-1", "ac-2"])

    def test_approved_requires_every_criterion_at_one_revision(self):
        card = self.make_card()
        first = self.add_review(card["id"], "ac-1", "approved", "rev-1")
        self.assertEqual(first.status, 201, first.raw)

        details = self.assert_error(
            self.set_status(card["id"], "APPROVED"), 409, "approval_required"
        )
        self.assertEqual(details["reason"], "missing_criteria")
        self.assertEqual(details["current_revision"], "rev-1")
        self.assertEqual(details["missing_criteria"], ["ac-2"])

        second = self.add_review(card["id"], "ac-2", "approved", "rev-1")
        self.assertEqual(second.status, 201, second.raw)
        resp = self.set_status(card["id"], "APPROVED")
        self.assertEqual(resp.status, 200, resp.raw)
        self.assertEqual(resp.json["card"]["status"], "APPROVED")

    def test_new_revision_invalidates_prior_approvals(self):
        card = self.make_card()
        for criterion in ("ac-1", "ac-2"):
            self.add_review(card["id"], criterion, "approved", "rev-1")

        advanced = self.add_review(card["id"], "ac-1", "approved", "rev-2")
        self.assertEqual(advanced.status, 201, advanced.raw)

        details = self.assert_error(
            self.set_status(card["id"], "APPROVED"), 409, "approval_required"
        )
        self.assertEqual(details["current_revision"], "rev-2")
        self.assertEqual(details["missing_criteria"], ["ac-2"])

        self.add_review(card["id"], "ac-2", "approved", "rev-2")
        self.assertEqual(self.set_status(card["id"], "APPROVED").status, 200)

    def test_rejected_verdict_blocks_approval(self):
        card = self.make_card(criteria=("ac-1",))
        rejected = self.add_review(
            card["id"], "ac-1", "rejected", "rev-1", actor="reviewer"
        )
        self.assertEqual(rejected.status, 201, rejected.raw)

        details = self.assert_error(
            self.set_status(card["id"], "APPROVED"), 409, "review_rejected"
        )
        self.assertEqual(details["criterion_id"], "ac-1")
        self.assertEqual(details["tested_revision"], "rev-1")
        self.assertEqual(details["actor"], "reviewer")
        self.assert_utc_stamp(details["created_at"])

    def test_stale_rejection_does_not_block(self):
        card = self.make_card(criteria=("ac-1",))
        self.add_review(card["id"], "ac-1", "rejected", "rev-0")
        self.add_review(card["id"], "ac-1", "approved", "rev-1")

        resp = self.set_status(card["id"], "APPROVED")
        self.assertEqual(resp.status, 200, resp.raw)

    def test_reasserting_approved_is_a_noop(self):
        card = self.make_card(criteria=("ac-1",))
        self.add_review(card["id"], "ac-1", "approved", "rev-1")
        self.assertEqual(self.set_status(card["id"], "APPROVED").status, 200)
        self.assertEqual(self.set_status(card["id"], "APPROVED").status, 200)

    def test_unknown_criterion_is_rejected(self):
        card = self.make_card(criteria=("ac-1",))
        details = self.assert_error(
            self.add_review(card["id"], "ac-9", "approved", "rev-1"),
            422,
            "criterion_not_found",
        )
        self.assertEqual(details["criterion_id"], "ac-9")

    def test_invalid_verdict_is_rejected(self):
        card = self.make_card(criteria=("ac-1",))
        resp = self.add_review(card["id"], "ac-1", "maybe", "rev-1")
        details = self.assert_error(resp, 422, "validation_error")
        self.assertEqual(details["reason"], "invalid_verdict")

    def test_invalid_criteria_shape_is_rejected(self):
        for value in ([], ["ac-1", "ac-1"], [""], "ac-1"):
            resp = request(
                self.app,
                "POST",
                "/cards",
                body={
                    "title": "Bad criteria",
                    "card_type": "task",
                    "acceptance_criteria": value,
                },
            )
            details = self.assert_error(resp, 422, "validation_error")
            self.assertEqual(details["reason"], "invalid_criteria", resp.raw)

    def test_review_is_persisted_with_actor_time_and_revision(self):
        card = self.make_card(criteria=("ac-1",))
        created = self.add_review(
            card["id"], "ac-1", "approved", "rev-7", actor="alice"
        )
        self.assertEqual(created.status, 201, created.raw)
        self.assertEqual(
            created.headers.get("Location"), f"/cards/{card['id']}/reviews"
        )
        review = created.json["review"]
        self.assertIsInstance(review["id"], str)
        self.assertTrue(review["id"])
        self.assertEqual(review["criterion_id"], "ac-1")
        self.assertEqual(review["verdict"], "approved")
        self.assertEqual(review["tested_revision"], "rev-7")
        self.assertEqual(review["actor"], "alice")
        self.assert_utc_stamp(review["created_at"])

        listed = request(self.app, "GET", f"/cards/{card['id']}/reviews")
        self.assertEqual(listed.status, 200, listed.raw)
        self.assertEqual(listed.json, {"items": [review], "count": 1})

    def test_review_endpoints_require_evidence_journal(self):
        plain_store = InMemoryCardStore()
        app = make_app(plain_store)
        card = request(
            app,
            "POST",
            "/cards",
            body={"title": "Plain", "card_type": "task"},
        ).json["card"]
        resp = request(
            app,
            "POST",
            f"/cards/{card['id']}/reviews",
            body={
                "criterion_id": "ac-1",
                "verdict": "approved",
                "tested_revision": "rev-1",
                "actor": "alice",
            },
        )
        self.assert_error(resp, 409, "evidence_store_required")
        listed = request(app, "GET", f"/cards/{card['id']}/reviews")
        self.assertEqual(listed.status, 200, listed.raw)
        self.assertEqual(listed.json, {"items": [], "count": 0})


class MergeGateTest(GateTestCase):
    """MERGED requires passing integration evidence at the current revision."""

    def test_merged_without_evidence_is_rejected(self):
        card = self.create("Mergeable")
        details = self.assert_error(
            self.set_status(card["id"], "MERGED"),
            409,
            "integration_evidence_required",
        )
        self.assertIsNone(details["current_revision"])

    def test_failed_checks_block_merge(self):
        card = self.create("Mergeable")
        created = self.add_integration(
            card["id"],
            "rev-1",
            [
                {"name": "ci", "status": "failed"},
                {"name": "lint", "status": "passed"},
            ],
        )
        self.assertEqual(created.status, 201, created.raw)

        details = self.assert_error(
            self.set_status(card["id"], "MERGED"),
            409,
            "integration_checks_failed",
        )
        self.assertEqual(details["current_revision"], "rev-1")
        self.assertEqual(details["checks"], [{"name": "ci", "status": "failed"}])

    def test_passing_evidence_allows_merge(self):
        card = self.create("Mergeable")
        created = self.add_integration(
            card["id"], "rev-1", [{"name": "ci", "status": "passed"}]
        )
        self.assertEqual(created.status, 201, created.raw)
        self.assertEqual(
            created.headers.get("Location"),
            f"/cards/{card['id']}/integration-evidence",
        )
        evidence = created.json["integration_evidence"]
        self.assertEqual(evidence["integration_ref"], "merge-1")
        self.assertEqual(evidence["actor"], "ci")
        self.assert_utc_stamp(evidence["created_at"])

        resp = self.set_status(card["id"], "MERGED")
        self.assertEqual(resp.status, 200, resp.raw)
        self.assertEqual(resp.json["card"]["status"], "MERGED")

        listed = request(
            self.app, "GET", f"/cards/{card['id']}/integration-evidence"
        )
        self.assertEqual(listed.status, 200, listed.raw)
        self.assertEqual(listed.json, {"items": [evidence], "count": 1})

    def test_stale_integration_evidence_blocks_merge(self):
        card = self.create("Mergeable", acceptance_criteria=["ac-1"])
        self.add_integration(
            card["id"], "rev-1", [{"name": "ci", "status": "passed"}]
        )
        self.add_review(card["id"], "ac-1", "approved", "rev-2")

        details = self.assert_error(
            self.set_status(card["id"], "MERGED"),
            409,
            "integration_evidence_required",
        )
        self.assertEqual(details["current_revision"], "rev-2")

        self.add_integration(
            card["id"], "rev-2", [{"name": "ci", "status": "passed"}]
        )
        self.assertEqual(self.set_status(card["id"], "MERGED").status, 200)

    def test_invalid_checks_are_rejected(self):
        card = self.create("Mergeable")
        for value in ([], [{"name": "ci", "status": "maybe"}], [{"status": "passed"}]):
            resp = request(
                self.app,
                "POST",
                f"/cards/{card['id']}/integration-evidence",
                body={
                    "tested_revision": "rev-1",
                    "integration_ref": "merge-1",
                    "checks": value,
                    "actor": "ci",
                },
            )
            details = self.assert_error(resp, 422, "validation_error")
            self.assertEqual(details["reason"], "invalid_checks", resp.raw)

    def test_integration_endpoints_require_evidence_journal(self):
        plain_store = InMemoryCardStore()
        app = make_app(plain_store)
        card = request(
            app,
            "POST",
            "/cards",
            body={"title": "Plain", "card_type": "task"},
        ).json["card"]
        resp = request(
            app,
            "POST",
            f"/cards/{card['id']}/integration-evidence",
            body={
                "tested_revision": "rev-1",
                "integration_ref": "merge-1",
                "checks": [{"name": "ci", "status": "passed"}],
                "actor": "ci",
            },
        )
        self.assert_error(resp, 409, "evidence_store_required")


class ClosureGateTest(GateTestCase):
    """CLOSED requires children CLOSED and verified acceptance criteria."""

    def test_open_children_block_parent_closure(self):
        epic = self.create("Platform", "project_epic")
        child = self.create("Ingest", "feature", parent_id=epic["id"])
        self.create("Parser", "task", parent_id=child["id"])

        details = self.assert_error(
            self.set_status(epic["id"], "CLOSED"), 409, "children_not_closed"
        )
        self.assertEqual(details["open_children"], [child["id"]])

    def test_pending_acceptance_blocks_parent_closure(self):
        epic = self.create(
            "Platform", "project_epic", acceptance_criteria=["ac-1"]
        )
        child = self.create("Ingest", "feature", parent_id=epic["id"])
        self.assertEqual(self.set_status(child["id"], "CLOSED").status, 200)

        details = self.assert_error(
            self.set_status(epic["id"], "CLOSED"),
            409,
            "acceptance_checks_pending",
        )
        self.assertEqual(details["pending_checks"], ["ac-1"])
        self.assertIsNone(details["current_revision"])

    def test_parent_closes_when_children_closed_and_criteria_approved(self):
        epic = self.create(
            "Platform", "project_epic", acceptance_criteria=["ac-1"]
        )
        child = self.create("Ingest", "feature", parent_id=epic["id"])
        self.assertEqual(self.set_status(child["id"], "CLOSED").status, 200)
        self.add_review(epic["id"], "ac-1", "approved", "rev-1")

        resp = self.set_status(epic["id"], "CLOSED")
        self.assertEqual(resp.status, 200, resp.raw)
        self.assertEqual(resp.json["card"]["status"], "CLOSED")

    def test_dependencies_do_not_satisfy_closure(self):
        dependency = self.create("Prereq")
        card = self.create("Independent")
        request(
            self.app,
            "POST",
            f"/cards/{card['id']}/dependencies",
            body={"depends_on_card_id": dependency["id"]},
        )

        resp = self.set_status(card["id"], "CLOSED")
        self.assertEqual(resp.status, 200, resp.raw)

    def test_children_must_close_before_parent(self):
        feature = self.create("Feature", "feature")
        task = self.create("Task", "task", parent_id=feature["id"])

        self.assert_error(
            self.set_status(feature["id"], "CLOSED"), 409, "children_not_closed"
        )
        self.assertEqual(self.set_status(task["id"], "CLOSED").status, 200)
        self.assertEqual(self.set_status(feature["id"], "CLOSED").status, 200)


class MinimalStoreCompatibilityTest(GateTestCase):
    """A plain CardStore (e.g. the PostgreSQL adapter) keeps working."""

    store_class = InMemoryCardStore

    def test_criteria_less_card_can_still_close(self):
        card = self.create("Plain")
        resp = self.set_status(card["id"], "CLOSED")
        self.assertEqual(resp.status, 200, resp.raw)

    def test_merge_requires_the_journal_and_is_rejected_without_it(self):
        card = self.create("Plain")
        self.assert_error(
            self.set_status(card["id"], "MERGED"),
            409,
            "integration_evidence_required",
        )

    def test_approval_requires_the_journal_and_is_rejected_without_it(self):
        card = self.create("Plain")
        details = self.assert_error(
            self.set_status(card["id"], "APPROVED"), 409, "approval_required"
        )
        self.assertEqual(details["reason"], "no_acceptance_criteria")


if __name__ == "__main__":
    unittest.main()
