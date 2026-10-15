# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Static HTML/CSS/JS served from `static/`, no build step, no third-party runtime
dependencies. The brief owns only `static/` and frontend-specific tests and
forbids adding dependencies without need.

## Users

Team members working a kanban board locally or on a self-hosted instance:
people who track epics, features, and tasks, move them through a nine-status
workflow, and manage dependencies between cards. They work at a desk, mouse or
keyboard, and need the board to scan quickly, including with assistive
technology. (Inferred from the frontend brief and the API contract; no human
interview was held because the task directive said to proceed.)

## Product Purpose

A single-page board over the Kanban V1 HTTP API. It renders every card grouped
into the nine canonical status columns, filters the board, opens card detail
with hierarchy and dependency context, and performs the status and dependency
mutations the V1 contract actually supports. Success means a user can read the
whole board at a glance, understand why a card is blocked, and operate
everything by keyboard alone.

## Positioning

Contract-true by construction: the UI exposes exactly the semantics the API
enforces (nine statuses, dependency-independent hierarchy, readiness gating)
and labels anything beyond it as unavailable rather than inventing endpoints.
Workflow routes (evidence, history, approvals) are absent from the V1 contract;
the UI must not pretend they exist.

## Operating Context

- Stable read-only contract: `kanban-platform-integration/API-SPEC.md` and
  `kanban_http/app.py`.
- Routes available: `GET /health`, `POST /cards`, `GET /cards`,
  `GET /cards/{id}`, `PATCH /cards/{id}/status`,
  `GET|POST /cards/{id}/dependencies`,
  `DELETE /cards/{id}/dependencies/{depends_on_card_id}`, `GET /board`,
  `GET /progress`.
- No authentication in V1. JSON over HTTP. Card ids are opaque strings.
- Local development and tests never contact a production service or database.

## Capabilities and Constraints

- Board: all nine status columns in canonical order
  (TODO, READY, IN_PROGRESS, REVIEW, CHANGES_REQUESTED, APPROVED, MERGED,
  CLOSED, BLOCKED), with counts and per-column card lists.
- Filtering: status, card type, parent, has-parent, combined with AND.
- Card detail: parent card, hierarchy children, `depends_on`, `dependents`,
  `open_dependencies`, `blocked`, and board-wide progress.
- Mutations allowed by the contract: status transition, add dependency, remove
  dependency, create card.
- Workflow features absent from the V1 contract (approval evidence, revision
  history, handoffs) are represented honestly as unavailable; where a workflow
  route is needed to demonstrate UI structure, it is a mock, labeled synthetic,
  and never presented as a live endpoint.
- Timestamps do not appear in V1 responses.
- Keyboard operation and accessible structure are hard requirements, not
  enhancements. Error, loading, and empty states are part of the surface.
- No commits, merges, deploys, or global configuration changes.

## Evidence on Hand

- `kanban-platform-integration/API-SPEC.md` (implemented contract) and
  `kanban_http/app.py` (reference implementation), both read-only.
- No real deployment, data, or brand assets. Demo/synthetic cards are authored
  for local use and tests and must be labeled synthetic.

## Product Principles

1. The contract is the truth: never invent routes, fields, or capabilities.
2. Keyboard-first and accessible by default, not as an afterthought.
3. Keep status and dependency semantics visible so users trust why a card is
   blocked or movable.
4. Dense and scannable: a working surface, not a metrics dashboard.
5. Honest states everywhere: loading, empty, error, and unavailable features
   are designed, not hidden.

## Accessibility & Inclusion

Required by the brief: full keyboard operation, visible focus, semantic
structure, screen-reader support, and responsive behavior across viewport
sizes. Target WCAG 2.2 AA-oriented contrast and interaction.
