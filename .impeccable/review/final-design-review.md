# Final design review: Kanban V1 board (`static/index.html`)

Documentation-only review and finish pass. No product source, test code,
screenshot, requirements, QA report, or other file was changed. Documentation
artifacts written by this pass: this review, root `DESIGN.md`, and
`.impeccable/design.json`. This is a design-review record, not a QA approval,
not a merge gate, and not a WCAG conformance claim.

- Worktree: `/home/azharnazli/projects/kanban-platform-worktree/kanban-platform-integrated`
- HEAD: `23a478176c16a57941a507b67bb663d698fd63ac` (branch `v1-integrated`)
- Dirty state: entire tree untracked; `git ls-files` = 0 tracked files (the
  initial commit `23a4781` contains none of them). Corrected count:
  `git status --porcelain -uall` = 53 entries after this pass (51 before it:
  50 pre-existing files plus this review artifact). Plain
  `git status --porcelain` reports 23 because it collapses untracked
  directories; the corrected methodology counts with `-uall`.
- Authoritative direction: `.impeccable/surfaces/static-index-html.md`,
  `PRODUCT.md`, and root `DESIGN.md` (created in this pass from the same
  direction and the actual `static/app.css` tokens; section 6b)
- Skills applied: impeccable core + craft floor; antislop core, `<antislop-ui>`,
  `<antislop-human>`, `<antislop-layoutmobile>`
- antislop usage mode: `during`, applied while the design work proceeded. The
  earlier "unresolved" statement is corrected: the mode question could not be
  answered (the clarification probe returned unavailable), and per the PM
  substitution the session proceeded in `during` mode rather than leaving the
  mode unresolved. This review is a follow-on artifact, not the mode's source.

## 1. Candidate fingerprint

Fingerprint algorithm (reproducible, path-explicit; replaces the earlier
manifest-of-hashes method, which was implicit about ordering and exclusion):

1. Source set: `find . \( -name .git -o -name .venv -o -name __pycache__ \) -prune -o -type f -print`
   from the repo root, strip leading `./`, then exclude the documenting
   artifacts themselves — `DESIGN.md`, `.impeccable/design.json`, and
   `.impeccable/review/final-design-review.md` — so the fingerprint cannot
   recurse into its own record; sort `LC_ALL=C`.
2. Stream, per path in sorted order: `<relative path bytes>` `NUL` `<file bytes>` `NUL`.
3. Fingerprint = SHA-256 of the whole stream.

- File count: **50** (the source set above; the seven review rasters are
  included, the three documenting artifacts are excluded)
- Candidate fingerprint: `bba7d23058c3e0e30bd68a320f412b57eb31cef1ead269f03f08f9169ff691e4`
- Stability: stable across this pass; documentation-only additions are
  excluded from the source set by definition, and no product byte changed
  (the raster hashes in section 6 are unchanged from the original capture).

## 2. Design read and dials (from the surface brief)

Reading this as: an Operate board for team members tracking epics/features/tasks,
in a chart-print plotting-table visual language, dial
ENERGY 1 / RHYTHM 1 / MOTION 1 (`.impeccable/surfaces/static-index-html.md:15`).

- One authored moment: course-line reveal; no choreography (same line).
- Thesis: nine ruled lanes as a passage plan; magenta correction color for
  blocked/rejected moves (brief lines 33-46).
- No webfonts fetched; character carried by structure, rules, case, tabular
  figures (brief line 74-76).

## 3. Detector result (actual run)

Command:

```sh
/home/azharnazli/.agents/skills/impeccable/scripts/impeccable detect --json \
  static/index.html static/app.css static/app.js static/lib.js
```

Result: exactly one finding.

| Field | Value |
| --- | --- |
| rule | `repeating-stripes-gradient` |
| severity | **advisory** |
| file | `static/index.html` (scan attribution; the gradients live in `static/app.css:491-496`) |
| snippet | `repeating-gradient decorative stripes` |
| per-file runs | `app.css` -> `[]`; `index.html` -> 1 advisory; `app.js` + `lib.js` -> `[]` |

Rationale from graticule identity: the brief's OWN-WORLD specifies
"chart-blue graticule hairlines" and FORM names a "toggleable graticule as a
real state" (brief lines 38-40, 56-58). The impeccable craft floor allows
two-axis grid overlays only when an actual canvas, map, blueprint, or measuring
tool is under them; here the world is a chart/plotting table, and the graticule
is user-toggleable (`#grid-toggle`, `aria-pressed`, `static/app.js:1226-1231`),
so it carries state, not decoration. The brief wins over the generic advisory.
Advisory accepted with this recorded reason; no ignore config was written
(no mutation).

## 4. Quality gates (PASS = proven by cited evidence; BLOCKED = not provable here)

### 4.1 antislop Hard Gate

| Rule | Result | Evidence |
| --- | --- | --- |
| R-02 em dash | PASS | `rg "—" static/ tests/` -> no match in shipped UI or test code (only the brief's own prose) |
| R-03 mobile | PASS (automated) | ui tests `mobile: no page overflow, chart pans internally, tap targets hold`; `nine-lane navigation: mobile shows the range cue, holds 44px steps, and reaches lane nine (mobile)`; overflow <= 1px asserted |
| R-17 numbers | PASS | no user-facing invented statistics; progress figures computed from API payload; mock fixtures labeled synthetic (`tests/mock_server.py:3-7`) |
| R-18 testimonials | PASS (N/A) | none exist |
| R-23 assets | PASS (N/A) | no logo/avatar imagery; title text only; no assets invented |
| R-24 navigation | PASS | only in-surface controls; skip link target `#board` exists; lane nav targets exist |
| R-25 contrast | PASS (token subset only) / BLOCKED (full) | `tests/static.test.mjs:37-69` asserts 18 token pairs >= 4.5:1 / 3:1 and passed; extra computed pairs: blue-deep on sea 8.22:1, ink-soft on sea 6.83:1, rule-strong on paper-deep 3.31:1. Placeholder, disabled-opacity and full state coverage unverified -> section 7 |
| R-26 interactive | PASS | every control has a handler (`static/app.js:1186-1263`); click map in 5.2; no dead controls found |
| R-27 states | PASS | tests: `loading state shows the skeleton...`, `load failure shows an alert banner and retry recovers`, `empty board teaches...`, `filter with no matches...`; empty lane copy `static/app.js:358-361` |
| R-28 FAQ | PASS (N/A) | no FAQ |
| R-32 keyboard | PASS (automated) / BLOCKED (manual AT) | test `keyboard: arrows move focus across lanes, Enter opens, Escape restores focus`; `nine-lane navigation: board region is keyboard scrollable...`; visible focus asserted in `accessibility: names, live regions, visible keyboard focus, no page overflow`; no `outline: none` (`tests/static.test.mjs:105-115`) |
| R-33 patch scripts | PASS | no source/CSS rewriting scripts in repo |
| R-34 themes | PASS (N/A) | single light chart-print scheme per brief; no theme toggle shipped |
| R-35 verify before deliver | PASS (automated walkthrough) | 24 browser tests run, click-through test asserts zero console errors; 7 screenshots; no manual user walkthrough performed |
| R-36 claims | PASS | no security/compliance/performance claims |
| R-37 direction | PASS | pinned brief with dials; not "draft without direction" |
| R-38 real content | PASS | workflow records are contract-absent, shown as "Not available", mock data labeled "Synthetic" (`static/app.js:832-855`) |

### 4.2 antislop Purpose-Gate

| Rule | Result | Evidence / written reason |
| --- | --- | --- |
| R-01 gradients | PASS | only the graticule hairlines and skeleton pulse; no decorative gradients |
| R-04 icons | PASS | authored inline SVG symbol set (`static/index.html:15-39`), square linecaps, functional glyphs |
| R-06 typography | PASS | system UI stack; serif italic titles; tabular numerals + system mono for ids/figures; reason recorded in brief (offline, no webfonts) |
| R-07 background | PASS with reason | graticule = chart world, toggleable state; detector advisory recorded in section 3 |
| R-08 arrows | PASS | arrows only on directional lane-step controls; "→" appears only inside the API cycle-path error data |
| R-09 badges | PASS | "Synthetic" stamp is a functional provenance label |
| R-10 / R-12 / R-13 | PASS (N/A) | no glassmorphism, no `box-shadow`, no glow (grep) |
| R-14 cards | PASS | kanban cards are the content model, not a feature-card template |
| R-19 motion | PASS | 150-200ms state transitions, sheet-in, course-line fade, skeleton pulse; `prefers-reduced-motion` honored (`static/app.css:1204-1212`); matches MOTION 1 |
| R-22 illustrations | PASS (N/A) | none; SVG is geometry only (rules, lines, lamps) |

### 4.3 Liveliness

| Item | Result | Evidence |
| --- | --- | --- |
| Dials explicit | PASS | brief line 15 |
| Consistent with dials | PASS | nine uniform lanes, one authored reveal = RHYTHM 1 / MOTION 1 |
| Focal point per screen | PASS | board field; detail sheet when open |
| Structural whitespace | PASS | spacing scale tokens `--s1..--s7` (`static/app.css:17-23`) |
| One deliberate accent | PASS | magenta reserved for blocked/rejected/correction states |
| Identity motif | PASS | graticule + printed lamps + italic card titles + course lines + measured scale nav |
| Design Read declared | PASS | section 2, sourced from brief (not invented) |

### 4.4 Craftsmanship and Quality Locks

| Item | Result | Evidence |
| --- | --- | --- |
| C-1 intentionality | PASS | every visual device traceable to brief OWN-WORLD/FORM |
| C-2 functional completeness | PASS | click map 5.2; no dead controls found in audited surfaces |
| C-3 content-driven | PASS | board/filters/detail/progress all map to real V1 routes in PRODUCT.md |
| C-4 resilience | PARTIAL | states/breakpoints automated; manual AT/zoom/touch BLOCKED (section 7) |
| C-5 evidence over claims | PASS | synthetic labels; no invented claims |
| R-05 template layout | PASS (N/A) | app surface, not a landing template |
| R-11 radius | PASS | radius 0 throughout; no pills |
| R-15 / R-16 CTA/buzzwords | PASS | labels: Apply, Clear, Graticule, Refresh, New card; no banned buzzwords in shipped UI |
| R-20 identity | PASS | chart-table world is not a stock kanban grid; swap test holds on graticule+typography alone |
| R-21 theme | PASS (N/A) | fixed light scheme is the brief's world (chart-white ground) |
| R-29 palette | PASS | paper/ink neutrals + chart blue + single magenta accent + green "done"; within cap |
| R-30 clone | PASS | no recognizable product clone |
| R-31 reasons written | PASS (in brief) | direction contract carries the reasons; `DESIGN.md` now records the system (section 6b) |

### 4.5 Impeccable craft floor

| Check | Result | Evidence |
| --- | --- | --- |
| Contrast | PASS (token subset) / BLOCKED (full) | as R-25 row |
| Depth | PASS | border-only elevation; no shadows |
| Spacing | PASS (visual/source) | scale tokens, tight groups; no computed-measure audit performed |
| Type | PASS (visual/source) | 6px to 24px steps, italic serif titles; 65-75ch measure not measured |
| Motion | PASS | one authored moment; reduced-motion honored |
| States | PASS | hover/focus/disabled/loading/error/empty present and tested |
| Browser surfaces | PASS | `::selection`, `caret-color`, custom scrollbars, focus-visible, underline offsets, tabular numerals all themed (`static/app.css:47-50, 75-78, 420-480`) |
| Copy | PASS | controls name their action; errors name the problem and recovery (`static/app.js:1040-1055`) |
| Coverage | PASS | all brief capabilities present: nine lanes, filters, detail, mutations, keyboard |

No false WCAG claim: the above proves token-pair contrast and automated
structure only. WCAG 2.2 AA conformance is NOT claimed.

## 5. Exact suite evidence

Environment: node v24.12.0; playwright-core 1.58.2 (agent-browser global);
Chromium from `~/.cache/ms-playwright/chromium-1247`; mock server
`tests/mock_server.py` (localhost, stdlib only). No lint or typecheck tooling
exists in the repo (no `package.json`, eslint, prettier, pyproject, ruff).

### 5.1 Runs performed during this review (read-only)

| Command | Result |
| --- | --- |
| `node --test tests/lib.test.mjs tests/static.test.mjs` | 22 pass, 0 fail, 0 skipped (74.6ms) |
| `node --test tests/ui.test.mjs` | 24 pass, 0 fail, 0 skipped (18.4s) |

Both runs left the candidate fingerprint unchanged (no repo writes).

### 5.2 Flow evidence (exact test names, all in `tests/ui.test.mjs`)

- Keyboard: `keyboard: arrows move focus across lanes, Enter opens, Escape restores focus`;
  `nine-lane navigation: board region is keyboard scrollable through every lane (desktop)`;
  visible focus asserted in `accessibility: names, live regions, visible keyboard focus, no page overflow`.
- Desktop: default 1440x900 context; `nine-lane navigation: scale names every lane, jumps reveal each, end state is exact (desktop)`;
  `nine-lane navigation: edge steps advance one lane and stop at both boundaries (desktop)`;
  `nine-lane navigation: a viewport wide enough for all nine hides the step controls` (2560x900).
- Mobile: `mobile: no page overflow, chart pans internally, tap targets hold` (390x844);
  `nine-lane navigation: mobile shows the range cue, holds 44px steps, and reaches lane nine (mobile)`.
- Touch: BLOCKED. Mobile tests use viewport emulation and mouse clicks; no
  touch-event dispatch, no real device, no `hasTouch` context. Only tap-target
  sizes are asserted (>= 44px).
- Browser: Chromium headless only. `browser suite stays local: every request targets the mock origin, none blocked`
  plus `tests/helpers.mjs:127-142` external-request blocker.
- States: `loading state shows the skeleton before the board resolves`;
  `load failure shows an alert banner and retry recovers`;
  `empty board teaches the next action and every lane reads empty`;
  `filter with no matches explains and offers a clear action`.
- Contract honesty: `workflow records read as unavailable against the contract-true API`;
  `workflow records render labeled synthetic data when mocks are on`.

### 5.3 Interactive click map (control -> first evidence)

| Control | Evidence |
| --- | --- |
| Filter status / type / parentage selects | `filters apply with AND semantics and clear back`; `filter with no matches explains and offers a clear action` |
| Apply, Clear | same two tests; `click-through: every control responds with no console errors` |
| Graticule `#grid-toggle` | `click-through: every control responds with no console errors` (aria-pressed flips) |
| Refresh `#refresh` | same test |
| New card, Create, Cancel | `creating a card adds it to TODO and focuses it`; `click-through...` opens and cancels the dialog |
| Lane scale ticks 1-9 | `click-through...`; `nine-lane navigation: scale names every lane, jumps reveal each, end state is exact (desktop)`; `browser suite stays local...` |
| Lane step prev/next | `click-through...`; `nine-lane navigation: edge steps advance one lane and stop at both boundaries (desktop)`; mobile lane test |
| Card click opens detail | `card detail shows parent, dependency context, blocked reason and progress` and 5 more tests via `openCard` (tests/ui.test.mjs:95-98) |
| Card Enter / Escape | `keyboard: arrows move focus across lanes, Enter opens, Escape restores focus` |
| Status strip buttons | `blocked move surfaces the API 409 with open dependencies`; `unrestricted status move updates the board lanes`; `click-through...` |
| Add dependency input + Add | `duplicate dependency is rejected and removal clears the block`; `dependency cycle is refused with the path from the API` |
| Remove dependency (X) | `duplicate dependency is rejected and removal clears the block` |
| Board ArrowLeft/Right/Home/End | `nine-lane navigation: board region is keyboard scrollable through every lane (desktop)` |
| Escape closes detail | keyboard test; duplicate-dependency test; `click-through...` |

Interactive elements with NO click/focus evidence in any suite (honest gap):
skip link, `#filter-parent` id input, `#create-parent` input, detail sheet
close button by mouse (Escape only), detail back button (stack depth > 1),
`dep-row__title` and parent-card link buttons, and the 409 "Open:" dependency
link buttons. These are present and wired in source but unproven by tests.

## 6. Screenshots (all seven inspected)

All are outputs of `tests/capture.mjs:56-74` (7 capture calls, 7 files; the
set matches exactly). All screenshot mtimes (09:27:20-22) postdate the last
source edits (latest `static/app.css` 09:24:38), so they are fresh for the
candidate fingerprint. All seven sha256 values below were re-verified in this
pass (unchanged). No unused screenshots exist as capture output; before this
pass provenance lived only in `capture.mjs`; all seven are now referenced by
`DESIGN.md` (Layout and Components) and hashed here.

| File (`.impeccable/review/`) | Viewport | Capture | sha256 |
| --- | --- | --- | --- |
| `desktop.png` | 1440x900 | initial board | `726060f3184e71d721a8941521e760ab3ce9ec28fc2817f71a6ca77d34ae9ad9` |
| `desktop-lanes-end.png` | 1440x900 | jump to lane 9 | `755763e9d8f9e6aff7d1a7427eecb2440fbf5a132669d97396bdebf013156057` |
| `mobile.png` | 390x844 | initial board | `4ac75117780f4620c5a6191753848007d8db7540ae437fdcc076b9a4b6206819` |
| `mobile-lanes-end.png` | 390x844 | step to lane 9 | `fa350d65cdd0c229bec94deece992394d7defc72bff58a9902c8d88245a8c34e` |
| `detail-desktop.png` | 1440x900 | Backfill detail + synthetic evidence | `10f226a74dca56182d121ecb23ca2b286adb8a00b6ebfc04aa278a14033122dc` |
| `detail-mobile.png` | 390x844 | Backfill detail, full-width sheet | `0c9130eaca41d27cb010cf8d7907bb3da7cdadc6cd6896061ac1fefa8b2e7136` |
| `filtered-desktop.png` | 1440x900 | filter type=task | `bb52da2ba368b13bd637b057cda897bb0bb9901cc68fbfb6448dd18aff0d37ca` |

Visual confirmations: nine ruled lanes with printed lamps and tabular counts;
magenta open course line drawn in `detail-desktop.png` (blocked card) and in
the board behind the sheet; "No cards in this lane." empty state in
`filtered-desktop.png`; scope 1-9 lane nav with range readout at both ends;
mobile sheets fill the width with two-column status strip.

### 6b. Documentation artifact provenance (this pass)

| File | sha256 |
| --- | --- |
| `DESIGN.md` | `a0068383170ef59d1f5cf3ee2d33e26e36fc15ce03807abddcd531842e41aa4a` |
| `.impeccable/design.json` | `f01ff397783f2b98229746f89d0f47e1d09cfe4fe64c241b685803c7e3c06434` |

Both are documentation-only. `DESIGN.md` follows the DESIGN.md format (YAML
token frontmatter plus the eight canonical sections in order; validated in
this pass). The sidecar follows `.impeccable/design.json` schemaVersion 2
(`extensions` + `components` + `narrative`; validated: JSON parses, every
`{colors.*}`/`{rounded.*}` component reference resolves to a frontmatter
token, and every snippet class is `ds-` prefixed and resolves only `:root`
tokens). The sidecar carries no `tonalRamp` values: the project defines no
tonal scale, so synthesizing ramps would fabricate values not present in the
system; the gap is stated here rather than filled.

## 7. Open gaps (explicitly unproven)

- Manual screen-reader audit: **BLOCKED**. No NVDA/VoiceOver/Orca session and no
  AT-user test. Only static ARIA presence and DOM assertions exist.
- Manual accessibility/touch walkthrough beyond automation: **BLOCKED**
  (no manual keyboard-only pass, no 200% zoom/reflow check, no real touch).
- Integrated browser-to-PostgreSQL flow: **BLOCKED**. Browser tests run against
  `tests/mock_server.py`; no browser test drives `kanban_pg_wsgi` with the real
  adapter.
- Crash recovery: **BLOCKED**. No test covers backend restart, mid-session
  failure recovery, or retry beyond the transient error banner.
- OS-level network namespace: none. Isolation is in-browser request blocking
  (`tests/helpers.mjs:127-142`) plus loopback mock; the OS network is not
  namespaced.
- Cross-browser: **BLOCKED**. Chromium only.
- Placeholder text / disabled-opacity contrast: **BLOCKED** (not measured).
- DESIGN.md and sidecar: **created in this pass** (documentation-only;
  hashes and validation in section 6b). The finish documentation gap is
  closed. Remaining documentation gap: the sidecar omits tonal ramps because
  the project defines no tonal scale (stated, not synthesized).
- Lint/typecheck: no tooling exists; none run.
- No WCAG 2.2 AA conformance claim is made anywhere.

## 8. Review outcome (bounded)

Within the audited, automated scope: no design-gate FAIL found; all 46 node
tests pass; the single detector advisory (`repeating-stripes-gradient`) is
accepted as the approved, user-toggleable chart graticule with the direction
cited in section 3; hard-gate copy, states, keyboard, mobile, and
contract-honesty checks pass on cited evidence. This pass adds the finish
documentation (`DESIGN.md`, `.impeccable/design.json`) with SHA-256
provenance in section 6b and changes no product byte (the candidate
fingerprint in section 1 covers the same 50-file source set). Items in
section 7 remain unproven and are marked BLOCKED, not PASS: manual
screen-reader AT, full contrast-state coverage, 200% zoom/reflow, real touch,
cross-browser, crash recovery, and the integrated browser-to-PostgreSQL flow.
No overall accessibility conformance is claimed anywhere in this review, and
the artifact does not approve QA, commit, or merge anything.

Review artifact hash and final candidate fingerprint are reported by the
reviewing session alongside delivery of this file.
