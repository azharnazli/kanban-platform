---
version: 1
slug: "static-index-html"
primary_target: "static/index.html"
related_targets: []
---

# Surface brief — Kanban V1 board (Operate)

Primary target: `static/index.html` (board surface; detail sheet is a state of
this surface). Secondary target: `static/app.js`.

## Scope

Visitor mode: **Operate**. Dials: ENERGY 1 / RHYTHM 1 / MOTION 1 (task-first
consistency; the one authored moment is the course-line reveal, not
choreography). Users read a nine-status board, filter it, open a
card's context, and perform the V1 mutations (status transition, dependency
add/remove, create card). Strategy is contract-true: only routes, fields, and
semantics the V1 API actually enforces; workflow routes absent from the
contract (evidence, revision history) are shown as unavailable, mocked only in
local demo/tests and labeled synthetic.

## Audience and task

Team members tracking epics, features, and tasks. Job: see the whole board at a
glance, understand why a card is blocked, move a card, wire a dependency, by
keyboard as readily as by mouse. Constraints: no build step, no dependencies,
WCAG 2.2 AA-oriented, responsive, offline-testable with no service or DB.

## Direction contract

THESIS: The board is a working chart table: a passage plan where cards are
plotted fixes along nine ruled lanes and dependencies are course lines drawn
between them. It refuses the generic rounded-card-on-gray kanban grid.

OWN-WORLD: Chart-print system: chart-white ground, ink black, chart-blue
graticule hairlines and interactive strokes, a single magenta correction color
for attention states. System UI stack; letterspaced uppercase small labels;
italic card titles against upright tabular data; no rounded corners, no
shadows, hairline rules, printed state marks (hairline detent at rest, filled
pressed, bracketed on focus, struck when spent).

STORY: The user reads where every card sits along nine ruled status lanes,
sees exactly which cards block which through drawn course lines, corrects the
plan with keystrokes, and watches magenta corrections resolve to struck
entries.

FIRST VIEWPORT: A title block and progress table across the top margin (board
name, filters, completion figures, primary actions); below it the chart field:
nine ruled lanes, each with a status header, count, and station mark; card
labels pinned in lanes; dependency course lines drawn live on a ruling layer;
selected card raises its lines.

FORM: Chart-room plotting table, candidate 5 of 7 grounded directions, seed key
7629f72a. Raised by donations: toggleable graticule as a real state (from the
Crouwel specimen); printed control-state vocabulary plus a live ruling layer
(centre-rail reference setting); one coherent form at defined size steps
(bitmap specimen); detail as a full-commitment sheet with adjacent prefetch
(vertical feed).

FINISH: unreviewed and undocumented is unfinished; this build ends with the
finish review, the verdict, DESIGN.md, and every shipping raster carrying its
provenance.

## Memorable moment

Selecting a card raises every course line that touches it across all nine
lanes — the whole dependency web for one card lit at once — and moving a card
that is still blocked draws the magenta correction mark and names the open
dependencies from the API's own 409 payload.

## Unresolved decisions

- None material. No webfonts may be fetched (offline, no dependencies); chart
  character is carried by structure, rules, color roles, case, and tabular
  figures rather than a display face.
