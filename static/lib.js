export const STATUSES = Object.freeze([
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

export const CARD_TYPES = Object.freeze(["project_epic", "feature", "task"]);

export const TYPE_LABELS = Object.freeze({
  project_epic: "Epic",
  feature: "Feature",
  task: "Task",
});

export const STATUS_META = Object.freeze({
  TODO: { label: "TODO", tone: "neutral", lamp: "open" },
  READY: { label: "READY", tone: "ready", lamp: "ready" },
  IN_PROGRESS: { label: "IN PROGRESS", tone: "active", lamp: "active" },
  REVIEW: { label: "REVIEW", tone: "review", lamp: "review" },
  CHANGES_REQUESTED: { label: "CHANGES REQUESTED", tone: "attention", lamp: "attention" },
  APPROVED: { label: "APPROVED", tone: "clear", lamp: "clear" },
  MERGED: { label: "MERGED", tone: "clear", lamp: "clear" },
  CLOSED: { label: "CLOSED", tone: "done", lamp: "done" },
  BLOCKED: { label: "BLOCKED", tone: "hazard", lamp: "hazard" },
});

export function statusMeta(status) {
  return STATUS_META[status] ?? { label: String(status ?? ""), tone: "neutral", lamp: "open" };
}

export function statusIndex(status) {
  return STATUSES.indexOf(status);
}

export function statusSteps(status) {
  const index = statusIndex(status);
  if (index < 0) return { index: null, total: STATUSES.length };
  return { index: index + 1, total: STATUSES.length };
}

export function typeLabel(cardType) {
  return TYPE_LABELS[cardType] ?? String(cardType ?? "");
}

export function truncateId(id, length = 8) {
  const text = String(id ?? "");
  return text.length > length ? `${text.slice(0, length)}…` : text;
}

export function formatPercent(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "0%";
  const fixed = number.toFixed(2).replace(/\.?0+$/, "");
  return `${fixed}%`;
}

function assertCard(card, context) {
  if (!card || typeof card !== "object") {
    throw new Error(`${context}: card entry is not an object`);
  }
  for (const field of ["id", "title", "card_type", "status", "parent_id"]) {
    if (!(field in card)) {
      throw new Error(`${context}: card entry missing field ${field}`);
    }
  }
  return {
    id: String(card.id),
    title: String(card.title),
    card_type: String(card.card_type),
    status: String(card.status),
    parent_id: card.parent_id === null ? null : String(card.parent_id),
  };
}

export function groupCardsByStatus(cards) {
  const grouped = new Map(STATUSES.map((status) => [status, []]));
  const unknown = [];
  for (const card of cards) {
    if (grouped.has(card.status)) grouped.get(card.status).push(card);
    else unknown.push(card);
  }
  if (unknown.length) {
    throw new Error(`board: non-canonical status for card ${unknown[0].id}`);
  }
  return STATUSES.map((status) => ({ status, cards: grouped.get(status) }));
}

export function lanesFromBoardPayload(payload) {
  if (!payload || !Array.isArray(payload.columns)) {
    throw new Error("board: response missing columns");
  }
  const seen = new Set();
  const lanes = payload.columns.map((column) => {
    if (!column || !Array.isArray(column.cards)) {
      throw new Error("board: column missing cards");
    }
    const status = String(column.status);
    seen.add(status);
    return { status, cards: column.cards.map((card) => assertCard(card, "board")) };
  });
  const missing = STATUSES.filter((status) => !seen.has(status));
  if (missing.length) {
    throw new Error(`board: response missing status column(s): ${missing.join(", ")}`);
  }
  const ordered = lanes.sort((a, b) => statusIndex(a.status) - statusIndex(b.status));
  return ordered;
}

export function lanesFromCardsPayload(payload) {
  if (!payload || !Array.isArray(payload.items)) {
    throw new Error("cards: response missing items");
  }
  return groupCardsByStatus(payload.items.map((card) => assertCard(card, "cards")));
}

export function normalizeCard(card, context = "card") {
  return assertCard(card, context);
}

export function normalizeDetail(payload, context = "card detail") {
  if (!payload || !payload.card) {
    throw new Error(`${context}: response missing card`);
  }
  const card = assertCard(payload.card, context);
  const list = (value) => (Array.isArray(value) ? value.map((id) => String(id)) : []);
  return {
    card,
    depends_on: list(payload.depends_on),
    dependents: list(payload.dependents),
    open_dependencies: list(payload.open_dependencies),
    blocked: Boolean(payload.blocked),
  };
}

export const EMPTY_FILTERS = Object.freeze({
  status: "",
  card_type: "",
  has_parent: "",
  parent_id: "",
});

export function hasActiveFilters(filters) {
  return Object.values(filters).some((value) => value !== "");
}

export function buildCardsQuery(filters) {
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.card_type) params.set("card_type", filters.card_type);
  if (filters.has_parent) params.set("has_parent", filters.has_parent);
  if (filters.parent_id) params.set("parent_id", filters.parent_id);
  return params.toString();
}

export function validateFilterValues(filters) {
  const errors = [];
  if (filters.status && !STATUSES.includes(filters.status)) {
    errors.push(`unknown status filter ${filters.status}`);
  }
  if (filters.card_type && !CARD_TYPES.includes(filters.card_type)) {
    errors.push(`unknown card type filter ${filters.card_type}`);
  }
  if (filters.has_parent && !["true", "false"].includes(filters.has_parent)) {
    errors.push("has_parent must be true or false");
  }
  return errors;
}

export function nextLaneIndex(lanes, fromIndex, direction) {
  let index = fromIndex + direction;
  while (index >= 0 && index < lanes.length) {
    if (lanes[index].cards.length > 0) return index;
    index += direction;
  }
  return fromIndex;
}

export function clampCardIndex(lane, index) {
  if (!lane || lane.cards.length === 0) return -1;
  return Math.max(0, Math.min(index, lane.cards.length - 1));
}

export function findLaneOfCard(lanes, cardId) {
  for (let index = 0; index < lanes.length; index += 1) {
    if (lanes[index].cards.some((card) => card.id === cardId)) return index;
  }
  return -1;
}

export function apiError(payload, fallbackMessage = "The request failed.") {
  const error = payload && typeof payload === "object" ? payload.error : null;
  if (error && typeof error === "object") {
    return {
      code: typeof error.code === "string" ? error.code : "unknown_error",
      message: typeof error.message === "string" && error.message ? error.message : fallbackMessage,
      details: error.details && typeof error.details === "object" ? error.details : {},
    };
  }
  return { code: "unknown_error", message: fallbackMessage, details: {} };
}

export function openDependencySet(detail) {
  return new Set(detail ? detail.open_dependencies : []);
}

export function progressSummary(progress) {
  if (!progress || typeof progress !== "object") {
    return { total: 0, closed: 0, percent: 0 };
  }
  return {
    total: Number(progress.total) || 0,
    closed: Number(progress.closed_count) || 0,
    percent: Number(progress.completion_percent) || 0,
  };
}

export function statusCountRows(progress) {
  const byStatus = progress && progress.by_status && typeof progress.by_status === "object"
    ? progress.by_status
    : {};
  return STATUSES.map((status) => ({ status, count: Number(byStatus[status]) || 0 }));
}

export function describeCardForAnnouncement(detail) {
  if (!detail) return "";
  const { card, depends_on, dependents, open_dependencies } = detail;
  const parts = [`Selected ${card.title}.`];
  parts.push(`Status ${statusMeta(card.status).label}.`);
  parts.push(
    depends_on.length
      ? `Waits on ${depends_on.length} card${depends_on.length === 1 ? "" : "s"}.`
      : "No dependencies.",
  );
  parts.push(
    dependents.length
      ? `Holds up ${dependents.length} card${dependents.length === 1 ? "" : "s"}.`
      : "Holds up no cards.",
  );
  if (open_dependencies.length) {
    parts.push(`Blocked by ${open_dependencies.length} open dependenc${open_dependencies.length === 1 ? "y" : "ies"}.`);
  }
  return parts.join(" ");
}

export function buildCourseSegments(detail) {
  if (!detail) return [];
  const open = openDependencySet(detail);
  const segments = [];
  for (const id of detail.depends_on) {
    segments.push({ provider: id, consumer: detail.card.id, open: open.has(id) });
  }
  for (const id of detail.dependents) {
    segments.push({ provider: detail.card.id, consumer: id, open: detail.card.status !== "CLOSED" });
  }
  return segments;
}

export function buildHandoffExport(detail, lookupCard = () => null) {
  if (!detail || !detail.card) return null;
  const card = detail.card;
  const reference = (id) => {
    const target = lookupCard(id);
    return { id, title: target ? target.title : null };
  };
  return {
    task_id: card.id,
    title: card.title,
    objective: null,
    requirements: [],
    acceptance_criteria: [],
    parent: card.parent_id ? reference(card.parent_id) : null,
    owner: null,
    dependencies: detail.depends_on.map(reference),
    blockers: detail.open_dependencies.map(reference),
    evidence: [],
  };
}

export function handoffFileName(cardId) {
  const safe = String(cardId ?? "")
    .replace(/[^A-Za-z0-9._-]+/g, "-")
    .replace(/^[.-]+|[.-]+$/g, "");
  return `handoff-${safe || "card"}.json`;
}
