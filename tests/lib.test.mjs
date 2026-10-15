import { test } from "node:test";
import assert from "node:assert/strict";
import {
  STATUSES,
  statusMeta,
  statusSteps,
  typeLabel,
  truncateId,
  formatPercent,
  groupCardsByStatus,
  lanesFromBoardPayload,
  lanesFromCardsPayload,
  normalizeDetail,
  buildCardsQuery,
  hasActiveFilters,
  validateFilterValues,
  nextLaneIndex,
  clampCardIndex,
  findLaneOfCard,
  apiError,
  progressSummary,
  statusCountRows,
  describeCardForAnnouncement,
  buildCourseSegments,
  buildHandoffExport,
  handoffFileName,
  EMPTY_FILTERS,
} from "../static/lib.js";

const card = (id, status, extra = {}) => ({
  id,
  title: `Card ${id}`,
  card_type: "task",
  status,
  parent_id: null,
  ...extra,
});

test("STATUSES holds the nine canonical statuses in board order", () => {
  assert.deepEqual(STATUSES, [
    "TODO",
    "READY",
    "IN_PROGRESS",
    "REVIEW",
    "CHANGES_REQUESTED",
    "APPROVED",
    "MERGED",
    "CLOSED",
    "BLOCKED",
  ]);
});

test("statusMeta and statusSteps describe canonical statuses and reject strangers", () => {
  assert.equal(statusMeta("IN_PROGRESS").label, "IN PROGRESS");
  assert.equal(statusMeta("CLOSED").tone, "done");
  assert.deepEqual(statusSteps("READY"), { index: 2, total: 9 });
  assert.deepEqual(statusSteps("NOT_A_STATUS"), { index: null, total: 9 });
});

test("typeLabel maps card types to human labels", () => {
  assert.equal(typeLabel("project_epic"), "Epic");
  assert.equal(typeLabel("feature"), "Feature");
  assert.equal(typeLabel("task"), "Task");
});

test("truncateId keeps short ids and shortens long ones", () => {
  assert.equal(truncateId("abc"), "abc");
  assert.equal(truncateId("0b2c43aa91de0001"), "0b2c43aa…");
});

test("formatPercent trims trailing zeros and survives junk", () => {
  assert.equal(formatPercent(33.33), "33.33%");
  assert.equal(formatPercent(50), "50%");
  assert.equal(formatPercent(0), "0%");
  assert.equal(formatPercent(undefined), "0%");
});

test("groupCardsByStatus returns nine lanes in canonical order and rejects unknown statuses", () => {
  const lanes = groupCardsByStatus([card("a", "READY"), card("b", "TODO"), card("c", "CLOSED")]);
  assert.equal(lanes.length, 9);
  assert.deepEqual(
    lanes.map((lane) => lane.status),
    [...STATUSES],
  );
  assert.deepEqual(lanes[0].cards.map((entry) => entry.id), ["b"]);
  assert.deepEqual(lanes[7].cards.map((entry) => entry.id), ["c"]);
  assert.throws(() => groupCardsByStatus([card("x", "ARCHIVED")]), /non-canonical status/);
});

test("lanesFromBoardPayload validates all nine columns and card shape", () => {
  const columns = STATUSES.map((status) => ({
    status,
    count: status === "TODO" ? 1 : 0,
    cards: status === "TODO" ? [card("a", "TODO")] : [],
  }));
  const lanes = lanesFromBoardPayload({ columns });
  assert.equal(lanes.length, 9);
  assert.equal(lanes[0].cards[0].id, "a");
  assert.throws(() => lanesFromBoardPayload({ columns: columns.slice(1) }), /missing status column/);
  assert.throws(
    () => lanesFromBoardPayload({ columns: columns.map((c, i) => (i === 0 ? { ...c, cards: [{ id: "x" }] } : c)) }),
    /missing field/,
  );
  assert.throws(() => lanesFromBoardPayload({}), /missing columns/);
});

test("lanesFromCardsPayload groups filtered card lists", () => {
  const lanes = lanesFromCardsPayload({ items: [card("a", "BLOCKED"), card("b", "TODO")], count: 2 });
  assert.equal(lanes[0].cards.length, 1);
  assert.equal(lanes[8].cards.length, 1);
});

test("normalizeDetail validates the detail payload", () => {
  const detail = normalizeDetail({
    card: card("a", "BLOCKED"),
    depends_on: ["b"],
    dependents: ["c"],
    open_dependencies: ["b"],
    blocked: true,
  });
  assert.deepEqual(detail.depends_on, ["b"]);
  assert.deepEqual(detail.dependents, ["c"]);
  assert.equal(detail.blocked, true);
  assert.throws(() => normalizeDetail({}), /missing card/);
});

test("buildCardsQuery combines filters with AND and omits empties", () => {
  assert.equal(buildCardsQuery(EMPTY_FILTERS), "");
  assert.equal(
    buildCardsQuery({ status: "TODO", card_type: "task", has_parent: "false", parent_id: "" }),
    "status=TODO&card_type=task&has_parent=false",
  );
});

test("hasActiveFilters and validateFilterValues guard the filter form", () => {
  assert.equal(hasActiveFilters(EMPTY_FILTERS), false);
  assert.equal(hasActiveFilters({ ...EMPTY_FILTERS, status: "TODO" }), true);
  assert.deepEqual(validateFilterValues(EMPTY_FILTERS), []);
  assert.equal(validateFilterValues({ ...EMPTY_FILTERS, status: "NOPE" }).length, 1);
  assert.equal(validateFilterValues({ ...EMPTY_FILTERS, card_type: "story" }).length, 1);
  assert.equal(validateFilterValues({ ...EMPTY_FILTERS, has_parent: "maybe" }).length, 1);
});

test("lane navigation helpers skip empty lanes and clamp", () => {
  const lanes = [
    { status: "TODO", cards: [card("a", "TODO"), card("b", "TODO")] },
    { status: "READY", cards: [] },
    { status: "IN_PROGRESS", cards: [card("c", "IN_PROGRESS")] },
  ];
  assert.equal(nextLaneIndex(lanes, 0, 1), 2);
  assert.equal(nextLaneIndex(lanes, 2, 1), 2);
  assert.equal(nextLaneIndex(lanes, 2, -1), 0);
  assert.equal(clampCardIndex(lanes[0], 5), 1);
  assert.equal(clampCardIndex(lanes[1], 0), -1);
  assert.equal(findLaneOfCard(lanes, "c"), 2);
  assert.equal(findLaneOfCard(lanes, "zz"), -1);
});

test("apiError reads the contract error envelope with a fallback", () => {
  const error = apiError({ error: { code: "dependencies_not_closed", message: "no", details: { a: 1 } } });
  assert.equal(error.code, "dependencies_not_closed");
  assert.equal(error.message, "no");
  assert.deepEqual(error.details, { a: 1 });
  assert.equal(apiError(null, "fallback").message, "fallback");
  assert.equal(apiError({}).code, "unknown_error");
});

test("progress helpers summarize the progress payload", () => {
  const progress = { total: 14, closed_count: 2, completion_percent: 14.29, by_status: { TODO: 4 } };
  assert.deepEqual(progressSummary(progress), { total: 14, closed: 2, percent: 14.29 });
  const rows = statusCountRows(progress);
  assert.equal(rows.length, 9);
  assert.equal(rows[0].count, 4);
  assert.equal(rows[3].count, 0);
});

test("describeCardForAnnouncement names status, waits and holds", () => {
  const detail = normalizeDetail({
    card: card("a", "BLOCKED", { title: "Backfill archive" }),
    depends_on: ["b"],
    dependents: ["c", "d"],
    open_dependencies: ["b"],
    blocked: true,
  });
  const text = describeCardForAnnouncement(detail);
  assert.match(text, /Backfill archive/);
  assert.match(text, /Waits on 1 card/);
  assert.match(text, /Holds up 2 cards/);
  assert.match(text, /Blocked by 1 open dependency/);
  assert.equal(describeCardForAnnouncement(null), "");
});

test("buildCourseSegments marks open edges from both directions", () => {
  const blocked = normalizeDetail({
    card: card("a", "BLOCKED"),
    depends_on: ["b", "c"],
    dependents: ["d"],
    open_dependencies: ["c"],
    blocked: true,
  });
  const segments = buildCourseSegments(blocked);
  assert.deepEqual(segments, [
    { provider: "b", consumer: "a", open: false },
    { provider: "c", consumer: "a", open: true },
    { provider: "a", consumer: "d", open: true },
  ]);
  const closed = normalizeDetail({
    card: card("a", "CLOSED"),
    depends_on: [],
    dependents: ["d"],
    open_dependencies: [],
    blocked: false,
  });
  assert.deepEqual(buildCourseSegments(closed), [{ provider: "a", consumer: "d", open: false }]);
});

test("buildHandoffExport carries the contract facts and explicit empties", () => {
  const detail = normalizeDetail({
    card: card("9f2c1ab34d5e6f03", "BLOCKED", {
      title: "Backfill sounding archive",
      parent_id: "0b2c43aa91de0002",
    }),
    depends_on: ["9f2c1ab34d5e6f05"],
    dependents: ["9f2c1ab34d5e6f07"],
    open_dependencies: ["9f2c1ab34d5e6f05"],
    blocked: true,
  });
  const lookup = (id) =>
    ({
      "0b2c43aa91de0002": { title: "Ingest pipeline" },
      "9f2c1ab34d5e6f05": { title: "Plot course lines" },
    })[id] ?? null;
  assert.deepEqual(buildHandoffExport(detail, lookup), {
    task_id: "9f2c1ab34d5e6f03",
    title: "Backfill sounding archive",
    objective: null,
    requirements: [],
    acceptance_criteria: [],
    parent: { id: "0b2c43aa91de0002", title: "Ingest pipeline" },
    owner: null,
    dependencies: [{ id: "9f2c1ab34d5e6f05", title: "Plot course lines" }],
    blockers: [{ id: "9f2c1ab34d5e6f05", title: "Plot course lines" }],
    evidence: [],
  });
});

test("buildHandoffExport keeps unknown lookups, roots and hostile text explicit", () => {
  const detail = normalizeDetail({
    card: card("a", "TODO", { title: 'Quote " and </textarea> \\ backslash' }),
    depends_on: ["zz"],
    dependents: [],
    open_dependencies: [],
    blocked: false,
  });
  const exported = buildHandoffExport(detail, () => null);
  assert.equal(exported.parent, null);
  assert.equal(exported.owner, null);
  assert.deepEqual(exported.dependencies, [{ id: "zz", title: null }]);
  assert.deepEqual(exported.blockers, []);
  assert.deepEqual(JSON.parse(JSON.stringify(exported, null, 2)), exported);
  assert.equal(buildHandoffExport(null), null);
});

test("handoffFileName sanitizes the card id for download", () => {
  assert.equal(handoffFileName("9f2c1ab34d5e6f03"), "handoff-9f2c1ab34d5e6f03.json");
  assert.equal(handoffFileName("../weird id"), "handoff-weird-id.json");
  assert.equal(handoffFileName(""), "handoff-card.json");
});
