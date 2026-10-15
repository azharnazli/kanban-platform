"""Domain constants, validation, and rule helpers for the Kanban V1 HTTP contract.

Pure logic only: no HTTP, no storage, no adapter imports. Every rule in this
module is enforced by the HTTP layer for any injected CardStore.
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

CARD_TYPES = ("project_epic", "feature", "task")

STATUSES = (
    "TODO",
    "READY",
    "IN_PROGRESS",
    "REVIEW",
    "CHANGES_REQUESTED",
    "APPROVED",
    "MERGED",
    "CLOSED",
    "BLOCKED",
)

DEFAULT_STATUS = "TODO"

# Statuses whose entry is gated by the readiness rule: every dependency of the
# card must be CLOSED before the transition is accepted.
READINESS_GATED_STATUSES = ("READY", "IN_PROGRESS")

# Allowed parent card types per card type. project_epic is a root (no parent).
PARENT_TYPE_RULES = {
    "project_epic": (),
    "feature": ("project_epic",),
    "task": ("feature",),
}

# Review verdicts and integration check statuses for the workflow gates.
REVIEW_VERDICTS = ("approved", "rejected")
CHECK_STATUSES = ("passed", "failed")

# Evidence journal kinds (see EvidenceStore below).
EVIDENCE_KINDS = ("acceptance_criteria", "review", "integration")

# Methods a store must provide to persist evidence. The base CardStore
# contract has no place for reviews or revisions; without this optional
# extension the evidence-dependent gates treat the card as having no
# evidence at all (see require_approvable/require_mergeable/require_closable).
EVIDENCE_JOURNAL_METHODS = ("add_evidence", "list_evidence")


@runtime_checkable
class EvidenceStore(Protocol):
    """Optional CardStore extension: an append-only evidence journal per card.

    add_evidence(card_id, kind, record) persists the caller-provided record
    verbatim and returns the store-assigned evidence id (a non-empty string).
    Raises KeyError for an unknown card.

    list_evidence(card_id, kind=None) returns the persisted records in
    insertion order, each carrying an added "id" key. With kind given, only
    records of that kind are returned. Raises KeyError for an unknown card.
    """

    def add_evidence(self, card_id: str, kind: str, record: dict) -> str:
        ...

    def list_evidence(
        self, card_id: str, kind: Optional[str] = None
    ) -> list:
        ...


class ApiError(Exception):
    """Error carrying the HTTP contract fields (status, code, message, details)."""

    def __init__(self, status, code, message, details=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}

    def to_payload(self):
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            }
        }


def validation_error(message, *, field=None, reason=None, **extra):
    details = dict(extra)
    if field is not None:
        details["field"] = field
    if reason is not None:
        details["reason"] = reason
    return ApiError(422, "validation_error", message, details)


def validate_card_type(value):
    if value not in CARD_TYPES:
        raise validation_error(
            f"card_type must be one of {list(CARD_TYPES)}",
            field="card_type",
            reason="invalid_card_type",
        )
    return value


def validate_status(value, *, field="status"):
    if value not in STATUSES:
        raise validation_error(
            f"status must be one of {list(STATUSES)}",
            field=field,
            reason="invalid_status",
        )
    return value


def validate_title(value):
    if value is None:
        raise validation_error("title is required", field="title", reason="required")
    if not isinstance(value, str):
        raise validation_error(
            "title must be a string", field="title", reason="invalid_type"
        )
    stripped = value.strip()
    if not stripped:
        raise validation_error(
            "title must not be blank", field="title", reason="blank"
        )
    return stripped


def _validate_optional_text(value, *, field):
    """Validate an optional persisted text field (owner, priority).

    None (or absent) means unset. Otherwise the value must be a non-empty
    string after trimming; the trimmed value is what gets persisted.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise validation_error(
            f"{field} must be a string", field=field, reason="invalid_type"
        )
    stripped = value.strip()
    if not stripped:
        raise validation_error(
            f"{field} must not be blank", field=field, reason="blank"
        )
    return stripped


def validate_owner(value):
    return _validate_optional_text(value, field="owner")


def validate_priority(value):
    return _validate_optional_text(value, field="priority")


def validate_string_list(value, *, field):
    """Validate an optional persisted list field (requirements, blockers).

    None (or absent) means unset. Otherwise the value must be a list of
    unique non-empty strings; entries are trimmed before uniqueness is
    checked and before they are persisted.
    """
    if value is None:
        return None
    if not isinstance(value, list):
        raise validation_error(
            f"{field} must be a list of unique non-empty strings",
            field=field,
            reason=f"invalid_{field}",
        )
    seen = set()
    items = []
    for item in value:
        if not isinstance(item, str):
            raise validation_error(
                f"{field} must be a list of unique non-empty strings",
                field=field,
                reason=f"invalid_{field}",
            )
        clean = item.strip()
        if not clean or clean in seen:
            raise validation_error(
                f"{field} must be a list of unique non-empty strings",
                field=field,
                reason=f"invalid_{field}",
            )
        seen.add(clean)
        items.append(clean)
    return items


def validate_id_field(value, *, field, required=True):
    """Validate an opaque card id field. Returns the value or None."""
    if value is None:
        if required:
            raise validation_error(f"{field} is required", field=field, reason="required")
        return None
    if not isinstance(value, str) or not value.strip():
        raise validation_error(
            f"{field} must be a non-empty string",
            field=field,
            reason="invalid_id",
        )
    return value


def validate_parent(store, card_type, parent_id):
    """Enforce the V1 hierarchy rules for a (possibly absent) parent_id.

    Caller has already shape-validated parent_id as a non-empty string or None.
    """
    if parent_id is None:
        return None
    if card_type == "project_epic":
        raise validation_error(
            "project_epic cards are roots: parent_id must be null",
            field="parent_id",
            reason="epic_must_be_root",
        )
    parent = store.get_card(parent_id)
    if parent is None:
        raise ApiError(
            422,
            "parent_not_found",
            f"parent card {parent_id!r} does not exist",
            {"field": "parent_id", "parent_id": parent_id},
        )
    allowed = PARENT_TYPE_RULES[card_type]
    parent_type = parent.get("card_type")
    if parent_type not in allowed:
        raise validation_error(
            f"{card_type} cards may only have a parent of type {list(allowed)} (or none)",
            field="parent_id",
            reason="invalid_parent_type",
            parent_card_type=parent_type,
        )
    return parent_id


def find_cycle(store, card_id, depends_on_card_id):
    """Return the cycle path if adding card_id -> depends_on_card_id closes a cycle.

    The returned path starts at depends_on_card_id and ends at card_id. A
    self-dependency yields [card_id]. Returns None when the edge is safe.
    """
    stack = [(depends_on_card_id, [depends_on_card_id])]
    visited = set()
    while stack:
        node, path = stack.pop()
        if node == card_id:
            return path
        if node in visited:
            continue
        visited.add(node)
        for nxt in store.list_dependencies(node):
            stack.append((nxt, path + [nxt]))
    return None


def open_dependencies(store, card_id):
    """Dependency ids of card_id that are not CLOSED.

    A dependency whose card is missing from the store is treated as open
    (blocking), never as satisfied.
    """
    open_ids = []
    for dep_id in store.list_dependencies(card_id):
        dep = store.get_card(dep_id)
        if dep is None or dep.get("status") != "CLOSED":
            open_ids.append(dep_id)
    return open_ids


# -- evidence journal (optional store extension) --------------------------


def has_evidence_journal(store):
    """True when the injected store implements the evidence journal."""
    return all(
        callable(getattr(store, method, None))
        for method in EVIDENCE_JOURNAL_METHODS
    )


def journal_records(store, card_id, kind=None):
    """Evidence records for card_id (oldest first); [] without a journal."""
    if not has_evidence_journal(store):
        return []
    return list(store.list_evidence(card_id, kind))


def declared_criteria(store, card_id):
    """Acceptance criterion ids declared for the card (latest declaration)."""
    records = journal_records(store, card_id, "acceptance_criteria")
    if not records:
        return []
    criteria = records[-1].get("criteria")
    if not isinstance(criteria, list):
        return []
    return [
        criterion
        for criterion in criteria
        if isinstance(criterion, str) and criterion
    ]


def current_revision(store, card_id):
    """tested_revision of the most recently recorded evidence, or None.

    Reviews and integration evidence both carry a tested_revision; the latest
    such record defines the card's current revision, so recording evidence
    for a new revision invalidates approvals taken at an earlier one.
    """
    for record in reversed(journal_records(store, card_id)):
        revision = record.get("tested_revision")
        if isinstance(revision, str) and revision:
            return revision
    return None


def approved_criteria_at(store, card_id, revision):
    """Criterion ids carrying an approved review record at revision."""
    return {
        record.get("criterion_id")
        for record in journal_records(store, card_id, "review")
        if record.get("verdict") == "approved"
        and record.get("tested_revision") == revision
    }


def open_children(store, card_id):
    """Ids of child cards (parent_id == card_id) that are not CLOSED.

    Closure is computed from hierarchy links only; dependency edges never
    satisfy (or block) parent closure.
    """
    return [
        card.get("id")
        for card in store.list_cards()
        if card.get("parent_id") == card_id and card.get("status") != "CLOSED"
    ]


def require_approvable(store, card_id):
    """Raise the APPROVED-gate error unless the card is approvable.

    Approvable means: at least one declared acceptance criterion and every
    criterion has an approved review record whose tested_revision equals the
    card's current revision. Any rejected verdict for a declared criterion at
    the current revision blocks APPROVED until a new revision is recorded.
    """
    criteria = declared_criteria(store, card_id)
    revision = current_revision(store, card_id)
    if not criteria:
        raise ApiError(
            409,
            "approval_required",
            "cannot move card to APPROVED: no acceptance criteria are declared",
            {
                "card_id": card_id,
                "reason": "no_acceptance_criteria",
                "current_revision": revision,
                "missing_criteria": [],
            },
        )
    rejected = [
        record
        for record in journal_records(store, card_id, "review")
        if record.get("verdict") == "rejected"
        and record.get("criterion_id") in criteria
        and record.get("tested_revision") == revision
    ]
    if rejected:
        record = rejected[-1]
        raise ApiError(
            409,
            "review_rejected",
            "cannot move card to APPROVED: a rejected review at the "
            "current revision blocks approval",
            {
                "card_id": card_id,
                "criterion_id": record.get("criterion_id"),
                "tested_revision": revision,
                "actor": record.get("actor"),
                "created_at": record.get("created_at"),
            },
        )
    approved = approved_criteria_at(store, card_id, revision)
    missing = [
        criterion for criterion in criteria if criterion not in approved
    ]
    if missing:
        raise ApiError(
            409,
            "approval_required",
            "cannot move card to APPROVED: criteria lack an approved "
            "review at the current revision",
            {
                "card_id": card_id,
                "reason": (
                    "revision_unknown" if revision is None else "missing_criteria"
                ),
                "current_revision": revision,
                "missing_criteria": missing,
            },
        )


def _checks_all_passed(checks):
    return (
        isinstance(checks, list)
        and bool(checks)
        and all(
            isinstance(check, dict) and check.get("status") == "passed"
            for check in checks
        )
    )


def require_mergeable(store, card_id):
    """Raise the MERGED-gate error unless integration evidence allows merge.

    MERGED requires at least one integration evidence record whose
    tested_revision equals the card's current revision and whose checks are
    all "passed". Without any such record the transition is rejected; when
    records exist but none pass, the failing checks of the most recent
    record at the current revision are reported.
    """
    revision = current_revision(store, card_id)
    records = [
        record
        for record in journal_records(store, card_id, "integration")
        if revision is not None
        and record.get("tested_revision") == revision
    ]
    if not records:
        raise ApiError(
            409,
            "integration_evidence_required",
            "cannot move card to MERGED: no integration evidence at the "
            "current revision",
            {"card_id": card_id, "current_revision": revision},
        )
    for record in records:
        if _checks_all_passed(record.get("checks")):
            return
    failing = [
        check
        for check in records[-1].get("checks", [])
        if not (isinstance(check, dict) and check.get("status") == "passed")
    ]
    raise ApiError(
        409,
        "integration_checks_failed",
        "cannot move card to MERGED: integration checks failed at the "
        "current revision",
        {
            "card_id": card_id,
            "current_revision": revision,
            "checks": failing,
        },
    )


def require_closable(store, card_id):
    """Raise the CLOSED-gate error unless the card may close.

    Closing requires every child card (cards whose parent_id is this card)
    to be CLOSED, and every declared acceptance criterion to have an
    approved review record at the card's current revision. Cards that
    declare no acceptance criteria pass the acceptance part vacuously.
    Children are checked first.
    """
    blocking = open_children(store, card_id)
    if blocking:
        raise ApiError(
            409,
            "children_not_closed",
            "cannot move card to CLOSED while child cards are not CLOSED",
            {"card_id": card_id, "open_children": blocking},
        )
    criteria = declared_criteria(store, card_id)
    if not criteria:
        return
    revision = current_revision(store, card_id)
    approved = approved_criteria_at(store, card_id, revision)
    pending = [
        criterion for criterion in criteria if criterion not in approved
    ]
    if pending:
        raise ApiError(
            409,
            "acceptance_checks_pending",
            "cannot move card to CLOSED: acceptance criteria lack an "
            "approved review at the current revision",
            {
                "card_id": card_id,
                "current_revision": revision,
                "pending_checks": pending,
            },
        )


# -- evidence field validation --------------------------------------------


def validate_acceptance_criteria(value):
    """Validate the optional acceptance_criteria create-body field.

    None (or absent) means the card declares no criteria. Otherwise the
    value must be a non-empty list of unique non-empty strings.
    """
    if value is None:
        return []
    if not isinstance(value, list) or not value:
        raise validation_error(
            "acceptance_criteria must be a non-empty list of unique "
            "non-empty strings",
            field="acceptance_criteria",
            reason="invalid_criteria",
        )
    seen = set()
    criteria = []
    for item in value:
        if (
            not isinstance(item, str)
            or not item.strip()
            or item in seen
        ):
            raise validation_error(
                "acceptance_criteria must be a non-empty list of unique "
                "non-empty strings",
                field="acceptance_criteria",
                reason="invalid_criteria",
            )
        seen.add(item)
        criteria.append(item)
    return criteria


def validate_verdict(value):
    if value not in REVIEW_VERDICTS:
        raise validation_error(
            f"verdict must be one of {list(REVIEW_VERDICTS)}",
            field="verdict",
            reason="invalid_verdict",
        )
    return value


def validate_checks(value):
    """Validate integration checks: non-empty list of unique {name, status}."""
    if not isinstance(value, list) or not value:
        raise validation_error(
            "checks must be a non-empty list of {name, status} objects",
            field="checks",
            reason="invalid_checks",
        )
    seen = set()
    checks = []
    for item in value:
        name = item.get("name") if isinstance(item, dict) else None
        status = item.get("status") if isinstance(item, dict) else None
        if (
            not isinstance(item, dict)
            or set(item) != {"name", "status"}
            or not isinstance(name, str)
            or not name.strip()
            or name.strip() in seen
            or status not in CHECK_STATUSES
        ):
            raise validation_error(
                "each check must be {name: non-empty unique string, "
                "status: 'passed' | 'failed'}",
                field="checks",
                reason="invalid_checks",
            )
        seen.add(name.strip())
        checks.append({"name": name.strip(), "status": status})
    return checks
