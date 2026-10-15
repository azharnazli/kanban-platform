# Kanban V1 Backlog and Acceptance Coverage

This document defines the V1 semantics that are **not part of the implemented
HTTP contract** in `docs/API-SPEC.md`, together with the acceptance criteria
that will prove them once implemented. It also records exactly which parts of
the objective are already implemented and tested in this worktree, including
the workflow gates (readiness, approval, merge evidence, parent closure).

Legend:

- **[IMPLEMENTED]** — enforced in this worktree today and covered by tests
  (`tests/test_workflow_gates.py`, `tests/test_integration_pg.py`).
- **[DEFINED]** — the semantics and wire contract are fixed here and enforced
  at the HTTP/domain layer, but durable persistence for them still requires
  the evidence-journal extension on a concrete adapter (see "Contract
  extension" below).
- **[BACKLOG]** — semantics agreed; no code implements them yet.

No item below claims integration with the `v1-postgres` adapter, the hierarchy
worktree, or any database. The adapter branch is separate and unapproved; the
read-only integration tree seeds this worktree but is not modified.

---

## Traceability: objective → status → evidence

| Objective requirement | Status | Where |
| --- | --- | --- |
| Bounded HTTP API over injectable `CardStore` | [IMPLEMENTED] | `kanban_http/app.py`, `kanban_http/store.py`; both test suites |
| Concrete routes, request/response/error contract | [IMPLEMENTED] | `docs/API-SPEC.md` §3, §4; both test suites |
| Dependency-cycle rejection | [IMPLEMENTED] at HTTP layer | `kanban_http/domain.py:find_cycle`; `ReadinessGateTest.test_dependency_cycles_are_rejected` |
| Prerequisites `CLOSED` before `READY`/`IN_PROGRESS` | [IMPLEMENTED] at transition time | `kanban_http/app.py:update_status`; `ReadinessGateTest` |
| Criterion-linked approval (tested revision/actor/timestamp) | [IMPLEMENTED] at HTTP/domain layer; adapter persistence [DEFINED] | `kanban_http/domain.py:require_approvable`; `ApprovalGateTest`; B3 |
| `MERGED` requires integration evidence | [IMPLEMENTED] at HTTP/domain layer; adapter persistence [DEFINED] | `kanban_http/domain.py:require_mergeable`; `MergeGateTest`; B4 |
| Parent closure requires children `CLOSED` + verified acceptance checks | [IMPLEMENTED] at HTTP/domain layer; adapter persistence [DEFINED] | `kanban_http/domain.py:require_closable`; `ClosureGateTest`; B5 |
| Board / detail / filter / progress | [IMPLEMENTED] baseline | `/board`, `/progress`, `GET /cards?...`, `GET /cards/{id}`; integration tests |
| Manual handoff | [BACKLOG] B7 | this document, B7 |
| Automatic handoff (out of scope, never V1) | excluded | — |

---

## B1. Dependency-cycle rejection

**Status:** [IMPLEMENTED] at the HTTP layer; adapter-level hardening [BACKLOG].

Implemented semantics (see `docs/API-SPEC.md` §4.7, §6):

- Adding an edge that would create a cycle — directly, transitively, or as a
  self-dependency — is rejected with `409 dependency_cycle` and
  `details.path` from `depends_on_card_id` back to the card.
- Duplicate edges are rejected first with `409 duplicate_dependency`.
- The check walks existing edges via `list_dependencies`; diamonds are legal.

Remaining backlog:

- B1.1 Adapter-level uniqueness/cycle constraints for multi-writer
  deployments (the HTTP check is read-then-write, not transactional; the
  PostgreSQL store validates cycles again in `kanban_pg.py`, but the HTTP
  layer cannot rely on that for arbitrary adapters).
- B1.2 Batch dependency operations with atomic all-or-nothing validation.

Acceptance criteria:

1. Given A→B and B→C, `POST /cards/A/dependencies {"depends_on_card_id": "C"}` returns `409 dependency_cycle` and no edge is persisted.
2. Given two concurrent compatible boolean no-op cases (diamond), both edges persist.
3. After B1.1, a direct store-level `add_dependency` of a cycle fails too.

## B2. Prerequisites CLOSED before READY/IN_PROGRESS

**Status:** [IMPLEMENTED] at transition time; stateful reconciliation [BACKLOG].

Implemented semantics:

- Entering `READY` or `IN_PROGRESS` from a different status requires every
  dependency card to be `CLOSED`; otherwise `409 dependencies_not_closed`
  with `details.open_dependencies`.
- Missing dependency cards count as open (blocking).
- Re-asserting the current gated status is a no-op and allowed.
- V1 does **not** revert or re-block a card when a dependency reopens, and
  allows adding an open dependency to a `READY` card (documented looseness;
  covered by `ReadinessGateTest`).

Remaining backlog:

- B2.1 Reconciliation policy: reopening a dependency of a `READY`/`IN_PROGRESS`
  card either (a) auto-transitions the dependent to `BLOCKED` with a recorded
  reason, or (b) blocks *all* transitions out of the gated state until closed.
  Decision required; proposal is (a) at adapter transaction time.
- B2.2 Creation gate review: decide whether `POST /cards` may create cards
  directly in `READY`/`IN_PROGRESS` once dependency records can be supplied at
  creation (currently allowed because new cards have no dependencies).

Acceptance criteria:

1. Given card T depends on open card P, `PATCH /cards/T/status {"status":"READY"}` and `{"status":"IN_PROGRESS"}` each return `409 dependencies_not_closed` listing P.
2. After P is `CLOSED`, the same transitions succeed.
3. After B2.1, reopening P moves T out of `READY`/`IN_PROGRESS` to `BLOCKED` and records the reason.

## B3. Criterion-linked approval (tested revision / actor / timestamp)

**Status:** [IMPLEMENTED] at the HTTP/domain layer (see `docs/API-SPEC.md`
§4.11, §6); durable adapter persistence [DEFINED] via the evidence journal.

Implemented semantics:

- A card declares **acceptance criteria** (stable ids, e.g. `ac-1`) at
  creation via `acceptance_criteria`; the declaration is persisted as an
  evidence record and cannot be amended in V1.
- An approval is a **ReviewRecord**: `{id, criterion_id, verdict: "approved"
  | "rejected", tested_revision, actor, created_at}`, created through
  `POST /cards/{id}/reviews` and listed by `GET /cards/{id}/reviews`.
- `tested_revision` is an opaque revision identifier (commit, artifact, build
  id) of the work actually tested; `actor` is the reviewing identity;
  `created_at` is assigned by the server in UTC ISO-8601 and never accepted
  from clients.
- Approvals are **revision-scoped**: a record approves a criterion only for
  the exact `tested_revision`. The card's current revision is the
  `tested_revision` of the most recently recorded review or integration
  record; recording evidence for a new revision invalidates prior approvals
  (history is kept, the gate no longer accepts them).
- `PATCH {"status": "APPROVED"}` is rejected `409 approval_required` when the
  card declares no criteria (`reason: "no_acceptance_criteria"`), when no
  revision is known (`reason: "revision_unknown"`), or when criteria lack an
  approved review at the current revision (`reason: "missing_criteria"`,
  `details.missing_criteria`). Any rejected verdict for a declared criterion
  at the current revision yields `409 review_rejected` with the record's
  criterion, revision, actor, and timestamp in `details`.
- Reviews for unknown criteria are rejected `422 criterion_not_found`;
  verdicts outside `approved`/`rejected` are `422 validation_error`
  (`invalid_verdict`).

Remaining backlog:

- B3.1 Adapter persistence: implement the evidence journal (below) so
  criteria/reviews survive on PostgreSQL; until then an adapter without the
  journal cannot declare criteria at all and `APPROVED` is rejected for every
  card.
- B3.2 Criteria amendment workflow (versioned criteria) — V1 is create-time
  only, which intentionally prevents clearing criteria to bypass the gate.
- B3.3 Waiver records for criteria that are consciously not verified (also
  referenced by B5).
- B3.4 Auto-transition on rejection: whether a rejected verdict should also
  move the card to `CHANGES_REQUESTED` automatically (V1 leaves the status
  untouched; callers make the transition explicitly).
- B3.5 Review history paging and reviewer/approver separation policy.

Acceptance criteria (implemented and covered by `ApprovalGateTest`):

1. Approving all criteria at revision R lets the card reach `APPROVED`.
2. Advancing the card to revision R+1 makes the `APPROVED` transition fail with `409 approval_required` until re-reviewed.
3. A rejected verdict at the current revision prevents `APPROVED` and is queryable with actor, revision, criterion, and timestamp (`GET .../reviews` and `details`).
4. Timestamps are UTC ISO-8601; clients cannot supply them.

## B4. MERGED requires integration evidence

**Status:** [IMPLEMENTED] at the HTTP/domain layer (see `docs/API-SPEC.md`
§4.12, §6); durable adapter persistence [DEFINED] via the evidence journal.

Implemented semantics:

- Integration evidence is an **IntegrationRecord**: `{id, tested_revision,
  integration_ref, checks: [{name, status: "passed" | "failed"}], actor,
  created_at}` created through `POST /cards/{id}/integration-evidence` and
  listed by `GET /cards/{id}/integration-evidence`.
- `integration_ref` identifies the merge result (merge commit, PR, pipeline
  run) in the external system. This worktree never performs merges or commits;
  it only records evidence. `created_at` is server-assigned UTC ISO-8601.
- Gate: `PATCH {"status": "MERGED"}` requires at least one integration record
  whose `tested_revision` equals the card's current revision and whose checks
  are all `passed`. Otherwise `409 integration_evidence_required` (no record
  at the current revision, including stale-only evidence) or `409
  integration_checks_failed` (`details.checks` lists the failing checks of
  the most recent record at the current revision).
- `MERGED` is not `CLOSED`. `CLOSED` remains the only done-state for
  `/progress`.

Remaining backlog:

- B4.1 Adapter persistence (same journal delta as B3.1).
- B4.2 Decision record: "at least one passing record at the current revision
  admits MERGED" (implemented) versus "the latest record at the current
  revision must pass". V1 accepts the former so retries after a fixed failure
  do not need a new revision.
- B4.3 Evidence ingestion from real CI/merge systems (webhooks, signatures);
  V1 is an explicit client call with actor identity only.

Acceptance criteria (implemented and covered by `MergeGateTest`):

1. Without evidence, the `MERGED` transition fails with `409 integration_evidence_required`.
2. Evidence for revision R with all checks passed allows `MERGED`; a failed check yields `409 integration_checks_failed` naming it.
3. Evidence for a stale revision does not satisfy the gate.
4. `MERGED` cards remain counted as not-done in `/progress`.

## B5. Parent closure requires children CLOSED + verified acceptance checks

**Status:** [IMPLEMENTED] at the HTTP/domain layer (see `docs/API-SPEC.md`
§4.5, §6); durable adapter persistence [DEFINED] via the evidence journal.

Implemented semantics:

- Closing a card that has children requires **every child** (cards whose
  `parent_id` is this card) to be `CLOSED` first; otherwise `409
  children_not_closed` with `details.open_children` (checked before the
  acceptance rule).
- Closing additionally requires that the card's own acceptance checks are
  **verified**: every declared criterion has an approved review record at the
  card's current revision; otherwise `409 acceptance_checks_pending` with
  `details.pending_checks`. Cards that declare no criteria pass this part
  vacuously (V1 boundary).
- Parent closure is computed from `parent_id` links only; dependency edges
  never satisfy parent-closure requirements.
- In this V1 scope the children rule applies to `CLOSED` only; `MERGED` is
  gated solely by B4 evidence.

Remaining backlog:

- B5.1 Decision record: whether `MERGED` should also require children
  `CLOSED` (the original sketch said "closing (or merging)"; this worktree
  gates closure only, per the approved scope).
- B5.2 Explicit waiver records (B3.3) as an alternative to an approved
  review for closure.
- B5.3 Adapter persistence (same journal delta as B3.1); until then closure
  gates degrade to the children rule plus vacuous acceptance, which keeps the
  existing PostgreSQL integration tests behaviorally unchanged.

Acceptance criteria (implemented and covered by `ClosureGateTest`):

1. Closing an epic with an open child fails `409 children_not_closed` listing the child.
2. Closing a feature with all children closed but a pending acceptance check fails `409 acceptance_checks_pending`.
3. Parent closure is computed from `parent_id` links only; dependencies never satisfy parent-closure requirements.

## B6. Board / detail / filter / progress

**Status:** [IMPLEMENTED] baseline; refinements [BACKLOG].

Implemented (see `docs/API-SPEC.md` §4.3, §4.4, §4.9, §4.10):

- `GET /board`: nine canonical columns, counts, cards.
- `GET /progress`: totals per status, `closed_count`, `completion_percent`.
- `GET /cards` filters: `status`, `card_type`, `parent_id`, `has_parent`.
- `GET /cards/{id}`: card, `depends_on`, `dependents`, `open_dependencies`,
  `blocked`.

Remaining backlog:

- B6.1 Pagination and stable ordering contract (`limit`, cursor).
- B6.2 Dependency-aware filters: `ready=true` (no open deps), `blocked=true`.
- B6.3 WIP limits per column (configurable; `409 wip_limit_exceeded`).
- B6.4 Descendant/ancestor (`subtree=`) filters built on the hierarchy.

Acceptance criteria (when implemented):

1. Listing a 100+ card board returns stable pages whose union equals the unpaginated result.
2. `ready=true` returns exactly the cards whose dependencies are all `CLOSED`.
3. WIP limit on a column rejects the move that would exceed it with `409 wip_limit_exceeded` and leaves status unchanged.

## B7. Manual handoff

**Status:** [BACKLOG]. Automatic handoff is out of scope for V1 entirely.

Semantics fixed in V1:

- A handoff is an explicit, human/agent-initiated transfer of responsibility:
  **HandoffRecord** `{id, card_id, from_actor, to_actor, reason, created_at}`.
- A handoff **never** changes status automatically. Callers that want a status
  effect (e.g. `BLOCKED` while waiting on another team) perform a separate
  `PATCH /cards/{id}/status`; the transition audit trail links the two only by
  timestamp.
- Future endpoints (non-normative sketch): `POST /cards/{id}/handoffs`,
  `GET /cards/{id}/handoffs`.
- Validation: `to_actor` required non-empty; `from_actor` optional (defaults
  to the authenticated actor once auth exists); `reason` optional for
  transfers to a person, required when the reason is `blocked`/`escalation`.

Acceptance criteria (when implemented):

1. Creating a handoff records `from_actor`, `to_actor`, optional `reason`, and a UTC timestamp.
2. Card status is unchanged by the handoff call itself.
3. Handoff history is queryable in order.
4. No V1 or future endpoint performs handoff without an explicit request (no automatic handoff).

---

## Contract extension (evidence journal)

The base 9-method `CardStore` contract cannot persist acceptance criteria,
reviews, revisions, or integration records. The implemented gates therefore
depend on a minimal two-method extension, declared as the `EvidenceStore`
protocol in `kanban_http/domain.py` and documented in `docs/API-SPEC.md` §7:

- `add_evidence(card_id, kind, record) -> id`
- `list_evidence(card_id, kind=None) -> list[record]`

The HTTP layer persists criteria, review, and integration records through
these methods with server-assigned UTC timestamps, and derives the card's
current revision and gate verdicts from the journal.

**Smallest contract delta for the PostgreSQL adapter** (separate approval,
not done in this worktree; `kanban_pg_adapter.py`, `kanban_pg.py`, and
`schema.sql` remain untouched here):

1. One append-only table (sketch):
   `card_evidence (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
   card_id BIGINT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
   kind TEXT NOT NULL, record JSONB NOT NULL,
   created_at TIMESTAMPTZ NOT NULL DEFAULT now())` — `record` stores the
   caller record verbatim.
2. `add_evidence` inserts, returns `str(id)`; `list_evidence` selects by
   `card_id` (and `kind`) ordered by `id`, merging the row id into the
   record.
3. No changes to the existing 9 methods or tables.

Until that delta lands on an adapter, the behavior matrix is: journal-backed
stores enforce all gates with durable evidence; stores without the journal
treat the card as evidence-less — `READY`/`IN_PROGRESS` dependencies and
child closure still enforce, the evidence endpoints report
`409 evidence_store_required`, and `APPROVED`/`MERGED` are rejected (no
criteria can exist, no integration evidence can exist).

## Coverage summary

- Implemented + tested: routes, error contract, hierarchy validation, cycle
  rejection, READY/IN_PROGRESS readiness gate, criterion-linked approval gate,
  MERGED integration-evidence gate, parent-closure gate (children + verified
  criteria), board/detail/filters/progress.
- Defined, pending adapter adoption: evidence journal persistence (B3.1,
  B4.1, B5.3).
- Defined, not implemented: B1.1–B1.2, B2.1–B2.2, B3.2–B3.5, B4.2–B4.3,
  B5.1–B5.2, B6.1–B6.4, B7.
- Explicitly out of scope: database schema/adapter changes in this worktree,
  hierarchy worktree, integrations/merges/commits performed by the platform,
  automatic handoff, frontend.
