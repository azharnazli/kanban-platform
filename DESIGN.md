---
name: Kanban V1 Work Plot
description: Contract-true kanban board drawn as a chart-print plotting table.
colors:
  paper: "#fbfaf7"
  paper-deep: "#f2f0e9"
  ink: "#191c21"
  ink-soft: "#4a5158"
  rule: "#c6cbd4"
  rule-strong: "#7b8494"
  chart-blue: "#1e5f8a"
  chart-blue-deep: "#14486b"
  sea: "#e4eef3"
  sea-line: "#b9cdd9"
  correction-magenta: "#a82460"
  done-green: "#1d6b4f"
typography:
  display:
    fontFamily: "Iowan Old Style, 'Palatino Linotype', Palatino, Georgia, 'Times New Roman', serif"
    fontSize: "1.5rem"
    fontWeight: 600
    lineHeight: 1.15
    letterSpacing: "0.01em"
  title:
    fontFamily: "Iowan Old Style, 'Palatino Linotype', Palatino, Georgia, 'Times New Roman', serif"
    fontSize: "0.9375rem"
    fontWeight: 400
    lineHeight: 1.3
  body:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.45
  label:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
    fontSize: "0.6875rem"
    fontWeight: 400
    letterSpacing: "0.1em"
  data:
    fontFamily: "ui-monospace, 'SFMono-Regular', Menlo, Consolas, 'Liberation Mono', monospace"
    fontSize: "0.75rem"
    fontWeight: 400
rounded:
  none: "0px"
spacing:
  s1: "0.25rem"
  s2: "0.5rem"
  s3: "0.75rem"
  s4: "1rem"
  s5: "1.5rem"
  s6: "2rem"
  s7: "3rem"
components:
  button:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.none}"
    padding: "0.5rem 0.75rem"
    height: "2.25rem"
  button-hover:
    backgroundColor: "{colors.chart-blue-deep}"
    textColor: "{colors.paper}"
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
  button-primary-hover:
    backgroundColor: "{colors.chart-blue-deep}"
    textColor: "{colors.paper}"
  button-quiet:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink-soft}"
  button-danger:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.correction-magenta}"
  button-pressed:
    backgroundColor: "{colors.sea}"
    textColor: "{colors.chart-blue-deep}"
  field:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.none}"
    padding: "0.25rem 0.5rem"
    height: "2.25rem"
  card:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.none}"
    padding: "0.5rem 0.75rem"
    height: "2.75rem"
---

# Design System: Kanban V1 Work Plot

## Overview

**Creative North Star: "The Working Chart Table"**

Kanban V1's board is drawn as a chart-room plotting table: cards sit as plotted labels inside nine ruled status lanes, dependencies are course lines drawn between them, and every quiet surface is a printed sheet of chart paper. The visual system is chart-print: a chart-white ground, ink black text, chart-blue graticule hairlines and interactive strokes, and a single magenta correction color for attention states. There are no rounded corners, no shadows, and no decorative imagery; structure is carried by hairlines, printed state marks, and tabular figures.

The global system is the durable layer documented here: palette roles, type voices, rule geometry, flat depth, and the control vocabulary (buttons, fields, cards, lamps, stamps). The board's passage-plan composition (nine lanes as a plotted route, live dependency course lines, the toggleable graticule state, the title-block masthead) is the **board surface concept** owned by `.impeccable/surfaces/static-index-html.md`, not a global mandate. Other surfaces reuse the chart-print language without being forced into the lane/course-line layout. PRODUCT.md constrains this file in one direction only: the UI is contract-true and honest, so visual devices exist to make state legible, never to decorate.

Token values below are extracted from `static/app.css` `:root` (lines 1-25) and the component rules that consume them; visual evidence is the provenance-carrying raster set in `.impeccable/review/`. Where the system has no answer (for example, it defines no tonal ramps), the gap is stated in the finish review rather than fabricated here.

**Key Characteristics:**
- Flat chart-print surfaces: 1px hairline rules, zero radius, no shadows, no gradients except the graticule ruling.
- Two-voice typography: italic serif titles against upright system UI and mono tabular data; no fetched webfonts.
- One correction accent: magenta only for attention/blocked/rejected states; chart blue is the interactive voice, green the done state.
- Printed state vocabulary: square lamps with inset marks, ink fills for pressed/current controls, 2px ink outlines for focus and selection.
- Graticule as a real user state (toggle), not texture.
- Keyboard-first: every state change is visible and focus is always drawn.

## Colors

A restrained chart palette: warm paper neutrals, blue ink for structure and interaction, one magenta correction color, one green done color.

### Primary
- **Chart Blue** (#1e5f8a): the interactive stroke color: course lines and arrowheads in the ruling layer, lamp marks for the READY, IN_PROGRESS, and REVIEW lanes, and the live ruled strokes of the dependency layer.
- **Chart Blue Deep** (#14486b): link text, hover fills for buttons and quiet controls, pressed-state text on ticks and quiet buttons, and dependency-row title links.

### Secondary
- **Correction Magenta** (#a82460): reserved for correction and attention states: BLOCKED and CHANGES_REQUESTED lamps, open dependency markers, hazard callouts, error banners, and the danger button. A rejected move draws its course line dashed in magenta.

### Tertiary
- **Done Green** (#1d6b4f): completion state only: the progress-scale fill, APPROVED/MERGED clearing lamps, and the CLOSED lamp and count emphasis. Never used for interactive affordances.

### Neutral
- **Chart Paper** (#fbfaf7): the ground of every surface; cards, buttons, fields, sheet, and dialogs all sit on paper.
- **Deep Paper** (#f2f0e9): quiet tonal step: skeleton rows, progress-scale trough, card hover ground, scrollbar track.
- **Ink** (#191c21): primary text, hard rules (masthead bottom, lane head bottom, lane navigator top, sheet left edge), primary button fill, pressed tick fill.
- **Ink Soft** (#4a5158): secondary text: labels, counts, meta rows, quiet button text, idle lamp outline.
- **Rule** (#c6cbd4): the fine lattice: lane dividers, block separators, dependency row outlines, skeleton outlines.
- **Rule Strong** (#7b8494): interactive control borders (fields, quiet buttons, ticks, icon buttons), dependency status text, scrollbar thumb.
- **Sea** (#e4eef3): state wash: info banners, in-view tick fill, pressed-button fill, dependency-title hover ground.
- **Sea Line** (#b9cdd9): the graticule line color and the text-selection background.

### Named Rules
**The One Correction Rule.** Magenta appears only where the plan needs correction or attention (blocked, rejected, error, destructive). It is never a styling accent; its rarity is what makes a blocked move read instantly.
**The Graticule State Rule.** The chart-blue ruling (`--sea-line`) is the chart world made operable: the Graticule control toggles it (`#grid-toggle`, `aria-pressed`, `static/app.js:1226-1231`), so it carries state rather than acting as decoration. This is approved direction (surface brief OWN-WORLD "chart-blue graticule hairlines"; FORM "toggleable graticule as a real state"), and the design detector's `repeating-stripes-gradient` advisory is accepted for exactly this reason: the brief wins.
**The Chart-White Ground Rule.** Surfaces never darken to signal hierarchy; tonal steps (sea, deep paper) mean state (in-view, hover, pressed, loading), not elevation.

Token-pair contrast is asserted by `tests/static.test.mjs:37-69` (18 pairs at >= 4.5:1 text / >= 3:1 non-text). Placeholder and disabled-opacity states are not measured; no WCAG conformance claim is made.

## Typography

**Display Font:** Iowan Old Style (with Palatino Linotype, Palatino, Georgia, Times New Roman, serif)
**Body Font:** system-ui (with -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif)
**Label/Mono Font:** ui-monospace (with SFMono-Regular, Menlo, Consolas, Liberation Mono, monospace)

**Character:** Two voices, both native: an italic serif for anything that names a thing (card titles, sheet titles, the board title) and an upright system UI for all controls and labels, with mono reserved for opaque ids and tabular figures. No webfonts are fetched (offline, no dependencies); chart character is carried by case, letterspacing, hairline rules, and tabular numerals rather than a display face.

### Hierarchy
- **Display** (600, 1.5rem, line-height 1.15, italic, 0.01em tracking): the masthead board title.
- **Title** (400, 0.9375rem, line-height 1.3, italic): board card titles; the detail sheet title runs 1.25rem at 600 with 1.2 line-height, and the create dialog title 1.125rem.
- **Body** (400, 0.875rem, line-height 1.45): controls, notes, banners; ids and codes use the mono face at 0.75-0.8125rem.
- **Label** (400, 0.6875-0.75rem, 0.08-0.1em tracking, uppercase): field labels, block titles, lane headings, card meta, range readout; ink-soft except lane headings.
- **Data** (400, 0.75rem, tabular): lane counts, progress figures, tick numbers, ruler positions; `font-variant-numeric: tabular-nums`.

### Named Rules
**The No-Webfont Rule.** Nothing is fetched at runtime: no font files, no external assets. Identity must survive offline, so structure, rules, case, and tabular figures carry the chart character.
**The Italic-Means-Voice Rule.** Italic is for titles and quiet empty-state notes, never for controls, labels, or data.

## Layout

Composition on the board surface (`static/index.html`): a masthead title block with the board id and a progress readout (figure + scale bar); a filter toolbar; the lane navigator (numbered 1-9 scale); then the chart field, a nine-column grid of lanes. The chart field is `grid-template-columns: repeat(9, minmax(0, 1fr))` with `width: max(100%, 135rem)`, so each lane holds at least 15rem and the field pans horizontally on narrower viewports; lanes run `min-height: 24rem` with sticky lane heads.

Responsive behavior, exact (`static/app.css`):
- **<= 68rem (1088px):** the masthead top grid collapses to one column and the progress readout wraps.
- **<= 47.5rem (760px):** the numbered lane scale hides and prev/next lane step buttons (2.75rem) appear; the chart field becomes `grid-auto-flow: column` with `grid-auto-columns: min(78vw, 18.75rem)` and horizontal scroll snap (`proximity`); interactive targets rise to a 2.75rem minimum (cards 3rem); the detail sheet goes full width; the status strip becomes two columns; toolbar actions take the full row and masthead padding tightens to `--s3`.
- **Wide viewports:** when all nine lanes fit, the step controls hide themselves (JS-driven; asserted at 2560x900 in `tests/ui.test.mjs`).

Spacing rhythm uses the 7-step scale `--s1` 0.25rem through `--s7` 3rem (`static/app.css:17-23`). Lanes pad with `--s3`; blocks and sheet sections pad `--s4/--s5` (reduced to `--s3` on mobile); toolbar gaps run `--s3/--s4`.

Visual evidence: `.impeccable/review/desktop.png` and `.impeccable/review/desktop-lanes-end.png` (1440x900, lanes 1-6 and 4-9), `.impeccable/review/mobile.png` and `.impeccable/review/mobile-lanes-end.png` (390x844, lane 1 and lane 9 reached), `.impeccable/review/filtered-desktop.png` (type=task filter with a printed "No cards in this lane." note).

## Elevation & Depth

Flat by construction: no `box-shadow` anywhere in the system. Depth is conveyed by hairline rules (1px `--rule` or `--ink`), tonal state washes, and a single modal backdrop `rgba(25,28,33,0.35)` behind the detail sheet and the create dialog. Layer order does the rest: the course-line ruling layer sits at z-index 0, lanes at 1, sticky lane heads at 2 (focused ticks join at 2), and the skip link at 10. Modal surfaces are the only things that float, and they float as paper panels with hard ink edges, not shadows.

### Named Rules
**The Flat Chart Rule.** No shadow, no glow, no blur. A surface that needs attention takes a border, a rule, or a state mark, never a shadow.

## Shapes

Right angles everywhere: `border-radius: 0` on buttons, fields, ticks, step buttons, cards, icon buttons, the detail sheet, and the create dialog. Structure is 1px hairline rules; drawn strokes run 1.5px (course lines, icon lines) over a 16px viewBox, with square linecaps on the icon set and round caps only on the drawn course lines. The signature geometry is the printed lamp: a 0.625rem square (border `--ink-soft` at idle) whose variants add inset marks: outline (ready), fill (active), left bar (review), inset fill (attention), full fill (hazard), double bars (clear), full green fill (done). Course lines start at a 2.5px dot and end in a small triangle arrowhead. There are no pills, no ellipses, no clipped corners.

### Named Rules
**The Right-Angle Rule.** Nothing is rounded. State lives in borders, fills, rule weight, and mark geometry, not corner smoothing.

## Components

### Buttons
Chart-print controls: stamped rectangles with uppercase 0.75rem labels, 0.06em tracking, 1px ink border, zero radius, min-height 2.25rem (2.75rem on mobile), padding `--s2/--s3`.
- **Primary:** ink ground, paper text; reserved for the affirmative action of a view (Apply, New card, Create card, Retry).
- **Default:** paper ground, ink border; hover fills chart-blue-deep with paper text.
- **Quiet:** rule-strong border, ink-soft text (Clear, Graticule, Refresh); hover fills chart-blue-deep.
- **Danger:** magenta border and text; hover fills magenta.
- **Pressed/toggled (`aria-pressed="true"`):** sea ground, chart-blue-deep border and text.
- **Disabled:** opacity 0.55 with hover reverting to rest; focus draws the global 2px ink outline at 2px offset.

### Fields
Stacked label plus control; labels are uppercase 0.6875rem with 0.08em tracking in ink-soft. Controls sit on paper with a 1px rule-strong border, zero radius, and a 2.25rem minimum height; card-id inputs run mono 0.8125rem with a 10rem minimum width. Focus uses the shared 2px ink outline; no other focus styling exists.

### Cards (board tickets)
Paper tickets on the chart field: 1px rule-strong border, zero radius, min-height 2.75rem (3rem on mobile), padding `--s2/--s3`. The title is italic serif 0.9375rem; the meta row carries uppercase 0.6875rem type text and a mono id; child cards show a "↳" parent marker. Hover: ink border plus deep-paper ground. Selected or current: ink border plus a 2px inset ink outline.

### Lane headers (signature)
A sticky strip above each lane: printed lamp, uppercase 0.75rem/600 heading with 0.09em tracking, right-aligned tabular count, 1px ink bottom rule. Lamp tone maps per status (`static/lib.js:21-31`): neutral, ready, active, review, attention, hazard, clear, done. Empty lanes print an italic "No cards in this lane." note instead of cards.

### Plot navigation (signature)
The board's measured scale: a numbered 1-9 tick row (2.75rem square ticks, mono figures, 1px rule-strong borders sharing edges via -1px margin), in-view ticks washed sea, hover ticks ink-edged; prev/next step buttons (2.75rem, ink border, `aria-disabled` dimming to rule-strong); a right-aligned range readout ("Lanes 1-6 of 9"). The scale hides below 47.5rem in favour of the step buttons; both hide when all nine lanes fit.

### Detail sheet
A full-height right-side paper sheet (`min(30rem, 100vw)`, 100dvh, 1px ink left edge) over the ink backdrop; italic serif title with an icon close button; a meta row with uppercase labels and a mono id; then ruled blocks: Position (hazard callout, a three-column status strip of small buttons whose current status is disabled and ink-filled, and a printed 9-tick ruler with the current position drawn as a taller ink bar), Dependencies (rows with underlined chart-blue-deep title buttons and magenta "· OPEN" states, plus an add form), Progress, and the contract-absent workflow blocks. On mobile the sheet is full width with a two-column status strip. Evidence: `.impeccable/review/detail-desktop.png`, `.impeccable/review/detail-mobile.png`.

### Evidence stamp
A tiny provenance mark: inline block, 1px rule-strong border, uppercase 0.625rem text with 0.1em tracking in ink-soft ("Synthetic"). It marks mock workflow data as synthetic and appears nowhere else.

### Banners
Info banners: sea ground and rule-strong border with `role="status"`; error banners: paper ground, magenta border and text with `role="alert"`, an optional mono code chip, and a primary retry action.

## Do's and Don'ts

### Do:
- **Do** keep `border-radius` at 0 and carry structure with 1px rules; the system has no `box-shadow`.
- **Do** keep magenta to correction and attention contexts; chart blue is interactive, green is done, neutrals are everything else.
- **Do** use tabular numerals for every count, id, and position.
- **Do** keep the graticule a user-toggled state in chart blue; it is approved direction, not a pattern violation.
- **Do** draw focus with the shared 2px ink outline at 2px offset and keep hover in the chart-blue-deep fill family.
- **Do** treat the surface brief's lane/course-line composition as board-local: it can change without touching this file, while the palette, type voices, rules, and state marks are the durable contract.
- **Do** honor `prefers-reduced-motion` and `forced-colors`; both already have global rules (`static/app.css:1204-1237`).

### Don't:
- **Don't** add shadows, gradients (beyond the graticule ruling), glows, or rounded corners.
- **Don't** fetch webfonts or any external asset; the board stays fully offline (asserted in `tests/static.test.mjs`).
- **Don't** introduce a second attention color or reuse magenta decoratively.
- **Don't** rely on color alone for status; lamp geometry, counts, and text labels accompany every state.
- **Don't** promote the nine-lane passage-plan concept into the global system; it belongs to the board surface.
