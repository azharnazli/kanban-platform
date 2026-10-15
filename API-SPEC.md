# Kanban V1 HTTP API Specification

Status: **implemented contract** for the routes, rules, and workflow gates
described below. Everything this document describes is enforced by
`kanban_http` and covered by the functional tests in `tests/`. Semantics that
are **not** implemented are listed in `docs/V1-BACKLOG-ACCEPTANCE.md` and are
explicitly out of this document's normative scope.

This worktree is standalone. It does **not** include the storage adapter
(separate, unapproved `v1-postgres` branch), does not import it, and makes no
integration claim with it. The API is written against the injectable
`CardStore` contract in section 7.

---

## 1. Conventions

- JSON request and response bodies, UTF-8.
- All responses carry `Content-Type: application/json`, except `204` responses,
  which have an empty body and no content type.
- Card ids are **opaque non-empty strings**. The HTTP layer passes ids through
  verbatim (URL-decoded) to the `CardStore` and echo them on responses. Clients
  must not parse or construct ids.
- No authentication/authorization in V1 (see backlog).
- Collection order is the order returned by `CardStore.list_cards()`; the API
  preserves it but clients must not rely on any specific order in V1.
- Unknown request body fields and unknown query parameters are rejected with
  `422 validation_error` (`reason: "unknown_field"` / `"unknown_parameter"`).
- Trailing slashes do not match (`GET /cards/` is `404 not_found`).
- Query parameters may be supplied at most once; repeats are rejected with
  `422 validation_error` (`reason: "duplicate_parameter"`).
- Card and status responses carry no timestamps. Evidence records (reviews,
  integration evidence) carry a server-assigned UTC ISO-8601 `created_at`;
  clients never supply timestamps. Handoffs remain backlog semantics.

## 2. Domain model

Card types:

| `card_type` | allowed parent |
| --- | --- |
| `project_epic` | none (root) |
| `feature` | `project_epic` or none |
| `task` | `feature` or none |

Canonical statuses, in board/progress order:

```
TODO, READY, IN_PROGRESS, REVIEW, CHANGES_REQUESTED, APPROVED, MERGED, CLOSED, BLOCKED
```

- `project_epic` is always a root card.
- Hierarchy and dependencies are **separate concepts**: a task may depend on a
  card of any type and any position in the hierarchy; dependencies never imply
  or alter `parent_id`.
- Card resource JSON (all five core fields always present):

```json
{
  "id": "9f2c1ab34d5e6f70",
  "title": "Ingest pipeline",
  "card_type": "feature",
  "status": "TODO",
  "parent_id": "0b2c43aa91de0001",
  "owner": "alice",
  "priority": "high",
  "requirements": ["parse feed", "emit JSON"],
  "blockers": ["vendor access"]
}
```

- Persisted V1 card fields extend the resource: `owner` and `priority` are
  optional non-empty strings, `requirements` and `blockers` are ordered lists
  of unique non-empty strings. A field that has never been set (or has been
  cleared) is **omitted** from the resource; existing clients that expect
  exactly the five core fields therefore keep working. Clients must not rely
  on the presence of these keys for cards that never set them.
- `blockers` is a free-form annotation field. It is **not** the
  dependency-derived blocking state: `open_dependencies` and `blocked` remain
  computed from dependency edges (section 4.4) and are unaffected by
  `blockers`.

## 3. Error contract

Every error response has this shape:

```json
{
  "error": {
    "code": "dependencies_not_closed",
    "message": "cannot move card to READY while dependencies are not CLOSED",
    "details": {"card_id": "9f2c", "open_dependencies": ["0b2c"]}
  }
}
```

`details` is always an object (possibly empty).

| HTTP | `code` | When |
| --- | --- | --- |
| 400 | `invalid_json` | Request body missing or not valid JSON |
| 404 | `not_found` | Card addressed by URL/body path does not exist; unknown route |
| 404 | `dependency_not_found` | `DELETE` of a dependency edge that does not exist |
| 405 | `method_not_allowed` | Path exists, method does not. `details.allowed` lists the verbs |
| 409 | `dependencies_not_closed` | Transition to `READY`/`IN_PROGRESS` with non-`CLOSED` dependencies |
| 409 | `dependency_cycle` | Adding a dependency would create a cycle (self included). `details.path` |
| 409 | `duplicate_dependency` | Dependency edge already exists |
| 409 | `approval_required` | `APPROVED` without declared criteria or without a current-revision approved review for every criterion; `details.reason`, `details.missing_criteria` |
| 409 | `review_rejected` | `APPROVED` blocked by a rejected review at the current revision; `details` carries the review |
| 409 | `integration_evidence_required` | `MERGED` without a passing integration record at the current revision |
| 409 | `integration_checks_failed` | `MERGED` with integration evidence whose checks failed. `details.checks` |
| 409 | `children_not_closed` | `CLOSED` while child cards are not `CLOSED`. `details.open_children` |
| 409 | `acceptance_checks_pending` | `CLOSED` with acceptance criteria lacking a current-revision approved review. `details.pending_checks` |
| 409 | `evidence_store_required` | A request must persist evidence but the injected store implements no evidence journal |
| 422 | `validation_error` | Field/query/body shape or value invalid; `details.field` + `details.reason` |
| 422 | `parent_not_found` | `POST /cards` with `parent_id` referencing a missing card |
| 422 | `dependency_card_not_found` | `POST .../dependencies` referencing a missing card |
| 422 | `criterion_not_found` | `POST .../reviews` targeting a criterion not declared on the card |
| 500 | `store_contract_violation` | Injected store returned data violating the CardStore contract |
| 500 | `internal_error` | Unexpected failure; message/details are generic and never leak internals |

`validation_error` reason codes: `required`, `invalid_type`, `blank`,
`invalid_card_type`, `invalid_status`, `invalid_id`, `epic_must_be_root`,
`invalid_parent_type`, `not_an_object`, `unknown_field`, `unknown_parameter`,
`duplicate_parameter`, `invalid_boolean`, `invalid_criteria`,
`invalid_verdict`, `invalid_checks`, `invalid_requirements`,
`invalid_blockers`.

## 4. Endpoints

### 4.1 `GET /health`

`200`:

```json
{"status": "ok"}
```

### 4.2 `POST /cards`

Create a card.

Request:

```json
{
  "title": "Ingest pipeline",
  "card_type": "feature",
  "status": "TODO",
  "parent_id": "0b2c43aa91de0001",
  "acceptance_criteria": ["ac-1", "ac-2"],
  "owner": "alice",
  "priority": "high",
  "requirements": ["parse feed", "emit JSON"],
  "blockers": ["vendor access"]
}
```

- `title` (required, string): trimmed; must not be blank.
- `card_type` (required): see section 2.
- `status` (optional, default `TODO`): any canonical status. Creating directly
  in `READY`/`IN_PROGRESS` is allowed because a new card has no dependencies;
  creating directly in `APPROVED`/`MERGED`/`CLOSED` is a creation shortcut and
  is not gated (see section 6 for the documented boundary).
- `parent_id` (optional, `null` or non-empty string): validated against
  section 2 rules; unknown parents are `422 parent_not_found`.
- `acceptance_criteria` (optional): a non-empty list of unique non-empty
  strings (stable criterion ids, e.g. `["ac-1", "ac-2"]`), persisted once as
  an evidence record; there is no endpoint to amend them in V1. Persisting
  criteria requires an evidence-journal store (section 7); otherwise the
  request is rejected with `409 evidence_store_required`.
- `owner`, `priority` (optional, `null` or non-empty string): persisted card
  fields; values are trimmed. `null`/absent means unset.
- `requirements`, `blockers` (optional): lists of unique non-empty strings;
  entries are trimmed and must be unique after trimming. Empty lists and
  `null` mean unset. Fields that end up unset are omitted from the response
  card (section 2).

Response `201` with `Location: /cards/{id}`:

```json
{"card": {"id": "9f2c", "title": "Ingest pipeline", "card_type": "feature", "status": "TODO", "parent_id": "0b2c"}}
```

Errors: `400 invalid_json`, `422 validation_error`, `422 parent_not_found`,
`409 evidence_store_required`.

### 4.3 `GET /cards`

List cards.

Query parameters (all optional, combined with AND):

| Parameter | Meaning |
| --- | --- |
| `status` | exact canonical status |
| `card_type` | exact card type |
| `parent_id` | exact parent id |
| `has_parent` | `true` or `false`; `false` returns root/unparented cards |

Response `200`:

```json
{"items": [{"id": "9f2c", "title": "...", "card_type": "feature", "status": "TODO", "parent_id": null}], "count": 1}
```

Errors: `422 validation_error` (invalid filter value, duplicate parameter,
unknown parameter).

### 4.4 `GET /cards/{card_id}`

Card detail with computed dependency views.

Response `200`:

```json
{
  "card": {"id": "9f2c", "title": "Ingest pipeline", "card_type": "feature", "status": "TODO", "parent_id": null},
  "depends_on": ["0b2c"],
  "dependents": ["aa11"],
  "open_dependencies": ["0b2c"],
  "blocked": true
}
```

- `depends_on`: ids this card depends on (store order).
- `dependents`: ids of cards that depend on this card; computed by scanning
  `list_cards()` + `list_dependencies()` (acceptable for the V1 contract; an
  adapter may add a dedicated query later).
- `open_dependencies`: dependencies whose card is not `CLOSED`. A dependency id
  whose card is missing from the store counts as open/blocking.
- `blocked`: `true` iff `open_dependencies` is non-empty.

Errors: `404 not_found`, `422 validation_error` for query parameters.

### 4.5 `PATCH /cards/{card_id}/status`

Transition a card's status.

Request:

```json
{"status": "READY"}
```

Enforced rules (all that V1 enforces; see section 6):

- When moving **into** `READY` or `IN_PROGRESS` from a different status, every
  dependency must be `CLOSED`; otherwise `409 dependencies_not_closed` with
  `details.open_dependencies`.
- Moving **into** `APPROVED` requires criterion-linked approval evidence: at
  least one declared acceptance criterion, and an approved review record at
  the card's current revision for every criterion; otherwise
  `409 approval_required`. A rejected review for a declared criterion at the
  current revision yields `409 review_rejected` until a new revision is
  recorded. See sections 4.11 and 6.
- Moving **into** `MERGED` requires integration evidence at the card's
  current revision with all checks `passed`; otherwise
  `409 integration_evidence_required` (no record) or
  `409 integration_checks_failed`. See sections 4.12 and 6.
- Moving **into** `CLOSED` requires every child card to be `CLOSED`
  (`409 children_not_closed`) and every declared acceptance criterion to have
  an approved review at the card's current revision
  (`409 acceptance_checks_pending`). Cards that declare no criteria pass the
  acceptance part vacuously.
- Re-asserting the status the card already has is a no-op and always allowed
  (the gates apply only when the target differs from the current status).
- All other transitions are unrestricted in V1.

Response `200`:

```json
{"card": {"id": "9f2c", "status": "READY", "...": "..."}}
```

Errors: `400 invalid_json`, `404 not_found`, `409 approval_required`,
`409 review_rejected`, `409 integration_evidence_required`,
`409 integration_checks_failed`, `409 children_not_closed`,
`409 acceptance_checks_pending`, `409 dependencies_not_closed`,
`422 validation_error`.

### 4.6 `GET /cards/{card_id}/dependencies`

Response `200`:

```json
{"items": ["0b2c", "aa11"], "count": 2}
```

Errors: `404 not_found`.

### 4.7 `POST /cards/{card_id}/dependencies`

Add `depends_on_card_id` to the card. Dependencies are independent of
hierarchy.

Request:

```json
{"depends_on_card_id": "0b2c"}
```

Checks, in order:

1. target card exists, else `404 not_found`;
2. dependency card exists, else `422 dependency_card_not_found`;
3. edge not already present, else `409 duplicate_dependency`;
4. adding the edge does not create a cycle (including self-dependency), else
   `409 dependency_cycle` with `details.path` listing the cycle starting at
   `depends_on_card_id` and ending at the card.

Response `201` with `Location: /cards/{card_id}/dependencies`:

```json
{"items": ["0b2c", "aa11"], "count": 2}
```

Errors: `400 invalid_json`, `404 not_found`, `409 duplicate_dependency`,
`409 dependency_cycle`, `422 dependency_card_not_found`, `422 validation_error`.

### 4.8 `DELETE /cards/{card_id}/dependencies/{depends_on_card_id}`

Remove the edge. Response `204`, empty body.

Errors: `404 not_found` (card missing), `404 dependency_not_found` (edge
missing).

### 4.9 `GET /board`

Cards grouped into canonical status columns, in canonical order.

Response `200`:

```json
{
  "columns": [
    {"status": "TODO", "count": 1, "cards": [{"id": "9f2c", "...": "..."}]},
    {"status": "READY", "count": 0, "cards": []},
    {"status": "IN_PROGRESS", "count": 0, "cards": []}
  ]
}
```

(All nine columns are always present.)

Errors: `422 validation_error` for query parameters; `500
store_contract_violation` if the store returns a non-canonical status.

### 4.10 `GET /progress`

Response `200`:

```json
{
  "total": 3,
  "by_status": {"TODO": 2, "READY": 0, "IN_PROGRESS": 0, "REVIEW": 0, "CHANGES_REQUESTED": 0, "APPROVED": 0, "MERGED": 0, "CLOSED": 1, "BLOCKED": 0},
  "closed_count": 1,
  "completion_percent": 33.33
}
```

- All nine `by_status` keys are always present.
- `completion_percent = round(closed_count * 100 / total, 2)`, `0.0` when
  `total == 0`. In V1 only `CLOSED` counts as done (`MERGED` does not; see
  backlog for the definition).

Errors: `422 validation_error` for query parameters; `500
store_contract_violation` as for `/board`.

### 4.11 `POST /cards/{card_id}/reviews` and `GET /cards/{card_id}/reviews`

Record a criterion-linked review (evidence for the `APPROVED` gate). Requests
require an evidence-journal store (section 7); otherwise `409
evidence_store_required`.

Request:

```json
{
  "criterion_id": "ac-1",
  "verdict": "approved",
  "tested_revision": "rev-2026-10-15",
  "actor": "alice"
}
```

- `criterion_id` (required): must be a criterion declared on the card, else
  `422 criterion_not_found`.
- `verdict` (required): `approved` or `rejected`.
- `tested_revision` (required, non-empty string): the revision tested; a
  review only satisfies the gate for the exact revision.
- `actor` (required, non-empty string): the reviewing identity.
- `created_at` is assigned by the server (UTC ISO-8601) and never accepted
  from the client.

Checks, in order: body shape (`400`/`422`), card exists (`404 not_found`),
evidence journal available (`409 evidence_store_required`), criterion
declared (`422 criterion_not_found`).

Response `201` with `Location: /cards/{card_id}/reviews`:

```json
{"review": {"id": "ev-1", "criterion_id": "ac-1", "verdict": "approved", "tested_revision": "rev-2026-10-15", "actor": "alice", "created_at": "2026-10-15T12:00:00+00:00"}}
```

`GET` returns `200 {"items": [...], "count": n}` in insertion order; without
an evidence journal the list is empty.

Errors: `400 invalid_json`, `404 not_found`, `409 evidence_store_required`,
`422 criterion_not_found`, `422 validation_error` (`invalid_verdict`, etc.).

### 4.12 `POST /cards/{card_id}/integration-evidence` and `GET /cards/{card_id}/integration-evidence`

Record integration evidence (evidence for the `MERGED` gate). Requests
require an evidence-journal store (section 7); otherwise `409
evidence_store_required`.

Request:

```json
{
  "tested_revision": "rev-2026-10-15",
  "integration_ref": "merge-abc123",
  "checks": [{"name": "ci", "status": "passed"}],
  "actor": "ci-bot"
}
```

- `tested_revision` (required, non-empty string): the revision integrated.
- `integration_ref` (required, non-empty string): identifies the merge result
  (merge commit, PR, pipeline run) in the external system. This worktree
  never performs merges; it only records evidence.
- `checks` (required): non-empty list of `{name, status}` objects; names must
  be unique and non-empty, `status` is `passed` or `failed`.
- `actor` (required, non-empty string).
- `created_at` is assigned by the server (UTC ISO-8601).

Checks, in order: body shape (`400`/`422`), card exists (`404 not_found`),
evidence journal available (`409 evidence_store_required`).

Response `201` with `Location: /cards/{card_id}/integration-evidence`:

```json
{"integration_evidence": {"id": "ev-2", "tested_revision": "rev-2026-10-15", "integration_ref": "merge-abc123", "checks": [{"name": "ci", "status": "passed"}], "actor": "ci-bot", "created_at": "2026-10-15T12:00:00+00:00"}}
```

`GET` returns `200 {"items": [...], "count": n}` in insertion order; without
an evidence journal the list is empty.

Errors: `400 invalid_json`, `404 not_found`, `409 evidence_store_required`,
`422 validation_error` (`invalid_checks`, etc.).

### 4.13 `PATCH /cards/{card_id}`

Update the persisted V1 card fields. Status is **not** changed here; use
`PATCH /cards/{card_id}/status` (section 4.5).

Request (at least one field required):

```json
{
  "owner": "bob",
  "priority": null,
  "requirements": ["parse feed v2"],
  "blockers": []
}
```

- `owner`, `priority` (optional): non-empty string after trimming, or `null`
  to clear.
- `requirements`, `blockers` (optional): list of unique non-empty strings
  (trimmed), `[]` or `null` to clear.
- Only the fields present in the body change; all others are left as they
  were. A body with no fields (`{}`) is rejected (`422 validation_error`,
  `reason: "required"`); a missing or non-JSON body is `400 invalid_json`.
  Unknown fields are rejected as in section 1 (`status` is unknown here).

Checks, in order: body shape (`400`/`422`), card exists (`404 not_found`).

Response `200`:

```json
{"card": {"id": "9f2c", "owner": "bob", "...": "..."}}
```

The response card omits fields that are unset after the update (section 2).

Errors: `400 invalid_json`, `404 not_found`, `422 validation_error`
(`invalid_type`, `blank`, `invalid_requirements`, `invalid_blockers`,
`unknown_field`, `required`).

## 5. Validation rules (implemented)

- `title`: required, string, non-blank after trimming; stored trimmed.
- `card_type`: required, one of `project_epic`, `feature`, `task`.
- `status`: canonical status; default `TODO` on create.
- `parent_id`: `null` or non-empty string; must exist; type compatibility per
  section 2; epics are roots.
- `depends_on_card_id`: required, non-empty string; must exist.
- Ids in JSON must be strings; numbers are rejected (`reason: "invalid_id"`).
- `acceptance_criteria`: optional at create; non-empty list of unique
  non-empty strings (`reason: "invalid_criteria"` otherwise).
- `owner`, `priority`: optional; `null` or a non-empty string after trimming
  (`reason: "invalid_type"` / `"blank"` otherwise). Stored trimmed.
- `requirements`, `blockers`: optional; `null` or a list of unique non-empty
  strings, trimmed and unique after trimming (`reason:
  "invalid_requirements"` / `"invalid_blockers"` otherwise). Stored in order.
- `criterion_id`, `tested_revision`, `actor`, `integration_ref`: required,
  non-empty strings.
- `verdict`: `approved` or `rejected` (`reason: "invalid_verdict"`).
- `checks`: non-empty list of unique `{name, status}` objects; `name` is a
  non-empty string, `status` is `passed` or `failed`
  (`reason: "invalid_checks"`).
- Evidence timestamps are assigned by the server; any client-supplied
  `created_at` is rejected as an unknown field.

## 6. Workflow gates (implemented)

All gates are enforced by the HTTP layer for any injected store. They apply
when the target status differs from the card's current status; re-asserting
the current status is always a no-op.

| Rule | Behavior |
| --- | --- |
| Enter `READY`/`IN_PROGRESS` | Rejected `409 dependencies_not_closed` while any dependency is not `CLOSED` |
| Enter `APPROVED` | Requires ≥1 declared criterion and an approved review record at the current revision for every criterion; else `409 approval_required` (`reason: "no_acceptance_criteria" | "revision_unknown" | "missing_criteria"`). Any rejected review for a declared criterion at the current revision yields `409 review_rejected` |
| Enter `MERGED` | Requires ≥1 integration record at the current revision with all checks `passed`; else `409 integration_evidence_required` or `409 integration_checks_failed` |
| Enter `CLOSED` | Requires every child card `CLOSED` (`409 children_not_closed`, checked first) and every declared criterion approved at the current revision (`409 acceptance_checks_pending`) |
| All other transitions | Unrestricted in V1 |
| Persisted card fields | `owner`, `priority`, `requirements`, and `blockers` never affect any gate. In particular the free-form `blockers` list is unrelated to `open_dependencies`/`blocked`, which stay dependency-derived |
| Current revision | `tested_revision` of the most recently recorded review or integration evidence; `null` until one exists. Recording evidence for a new revision invalidates prior approvals (they stay in history but no longer satisfy gates) |
| Review timestamps | Server-assigned UTC ISO-8601; actor, verdict, criterion, and revision are persisted with the record and echoed in gate errors |
| Criteria declaration | Create-time only (`acceptance_criteria`); there is no V1 endpoint to amend criteria, so they cannot be cleared to bypass a gate |
| Criteria-less cards | Cannot enter `APPROVED` (`reason: "no_acceptance_criteria"`); their closure passes the acceptance part vacuously; `MERGED` still requires integration evidence |
| Ready re-check on re-open | **Not** done in V1: reopening a dependency of a `READY` card does not revert or block it |
| Dependency added to `READY`/`IN_PROGRESS` card | Allowed in V1 even if the new dependency is open; the card keeps its status |
| Cycle rejection | At add time, including self-dependencies |
| Creation shortcut | `POST /cards` may set any canonical status, as in V1 baseline; a brand-new card has no children and no criteria, so closure semantics are unaffected, but clients must not use create to skip the review workflow |

The re-check and dependency-addition boundaries are deliberate V1 choices;
their stronger semantics are tracked in `docs/V1-BACKLOG-ACCEPTANCE.md`.

## 7. CardStore contract (injectable; not bundled)

The API depends only on this interface (see `kanban_http/store.py`). The
concrete adapter is out of scope for this worktree.

| Method | Contract |
| --- | --- |
| `create_card(title, card_type, status="TODO", parent_id=None, *, owner=None, priority=None, requirements=None, blockers=None) -> id` | persists and returns the new id; the persisted fields are keyword-only and optional |
| `get_card(id) -> card \| None` | `None` for unknown ids |
| `list_cards() -> list[card]` | all cards |
| `update_card_fields(id, **fields) -> None` | changes any subset of `owner`, `priority`, `requirements`, `blockers`; `None` (owner/priority/requirements/blockers) and `[]` (requirements/blockers) clear; `KeyError` for unknown id |
| `update_status(id, status) -> None` | `KeyError` for unknown id |
| `add_dependency(card_id, depends_on_card_id) -> None` | `KeyError` for unknown ids; duplicate edge may raise `ValueError` (HTTP layer pre-checks) |
| `remove_dependency(card_id, depends_on_card_id) -> None` | `KeyError` if card or edge missing |
| `list_dependencies(card_id) -> list[id]` | `KeyError` for unknown id; insertion order |
| `close() -> None` | release resources; behavior after close is undefined |

Card shape returned by `get_card`/`list_cards` (at least these keys; extra
fields are ignored by responses):

```json
{"id": "str", "title": "str", "card_type": "str", "status": "str", "parent_id": "str | null",
 "owner": "str | null", "priority": "str | null",
 "requirements": ["str"], "blockers": ["str"]}
```

The four persisted-field keys are optional for minimal stores; a store that
omits them is treated as having no values for them. Stores that expose them
must return `owner`/`priority` as `str` or `null` and `requirements`/
`blockers` as lists of strings; anything else is a
`500 store_contract_violation`.

Hierarchy, status, card-field validation, and cycle rules are enforced by the
HTTP layer, not by the adapter, so every adapter behaves identically. If a
store violates the shape contract, the API answers
`500 store_contract_violation` rather than leaking adapter internals.

### Evidence journal (optional CardStore extension)

Acceptance criteria, reviews, and integration evidence are persisted through
an optional two-method extension of the store contract (the `EvidenceStore`
protocol in `kanban_http/domain.py`):

| Method | Contract |
| --- | --- |
| `add_evidence(card_id, kind, record) -> id` | persist the caller record verbatim (kinds: `acceptance_criteria`, `review`, `integration`) and return the store-assigned evidence id; `KeyError` for unknown cards |
| `list_evidence(card_id, kind=None) -> list[record]` | records in insertion order, each with an added `id`; filtered by `kind` when given; `KeyError` for unknown cards |

Stores that implement both methods enable the full evidence-gated workflow.
Stores without the journal (including the current PostgreSQL adapter) treat
the card as having no evidence: criteria cannot be declared or reviewed
(`409 evidence_store_required` on the evidence endpoints), `APPROVED` is
rejected (`409 approval_required`, reason `no_acceptance_criteria`) and
`MERGED` is rejected (`409 integration_evidence_required`). `READY`/
`IN_PROGRESS` and child-closure gates need no journal, so a minimal store
keeps working unchanged. Adding these two methods (plus their DDL) to the
PostgreSQL adapter is the smallest contract delta needed for evidence
persistence there; it is not part of this worktree (see
`docs/V1-BACKLOG-ACCEPTANCE.md`).

## 8. Non-goals of this worktree

- No storage adapter, DDL, or database access.
- No frontend.
- No authentication/authorization, pagination, or ordering guarantees in V1.
- No handoff records and no automatic handoff (backlog in the companion
  document); automatic handoff is never V1.
- The approval, merge-evidence, and closure gates **are** implemented at the
  HTTP layer (section 6). Evidence persistence over the concrete PostgreSQL
  adapter is pending the two-method journal extension (section 7); no
  database schema or DDL ships in this worktree.
- No integration with the hierarchy or PostgreSQL worktrees.
