import {
  STATUSES,
  statusMeta,
  typeLabel,
  truncateId,
  formatPercent,
  lanesFromBoardPayload,
  lanesFromCardsPayload,
  normalizeDetail,
  EMPTY_FILTERS,
  hasActiveFilters,
  buildCardsQuery,
  validateFilterValues,
  nextLaneIndex,
  clampCardIndex,
  apiError,
  progressSummary,
  describeCardForAnnouncement,
  buildCourseSegments,
  buildHandoffExport,
  handoffFileName,
  statusSteps,
} from "./lib.js";

const SVG_NS = "http://www.w3.org/2000/svg";

const refs = {};
const state = {
  lanes: [],
  cardsById: new Map(),
  filters: { ...EMPTY_FILTERS },
  progress: null,
  focusId: null,
  selectedId: null,
  hoverId: null,
  detailCache: new Map(),
  prefetchTimer: null,
  detailStack: [],
  detail: null,
  pendingFocusId: null,
  loadedOnce: false,
};

class RequestFailure extends Error {
  constructor(message, code = "network_error", details = {}) {
    super(message);
    this.code = code;
    this.details = details;
  }
}

async function api(path, options = {}) {
  const init = { ...options };
  if (init.body) {
    init.headers = { "Content-Type": "application/json", ...(init.headers || {}) };
  }
  init.headers = { Accept: "application/json", ...(init.headers || {}) };
  let response;
  try {
    response = await fetch(path, init);
  } catch {
    throw new RequestFailure("Cannot reach the board service. Is the API running?");
  }
  let payload = null;
  if (response.status !== 204) {
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
  }
  return { ok: response.ok, status: response.status, payload };
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function iconUse(name) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(SVG_NS, "use");
  use.setAttribute("href", `#${name}`);
  svg.append(use);
  return svg;
}

function announce(text) {
  refs.announcer.textContent = "";
  requestAnimationFrame(() => {
    refs.announcer.textContent = text;
  });
}

function announceUrgent(text) {
  refs.alertAnnouncer.textContent = "";
  requestAnimationFrame(() => {
    refs.alertAnnouncer.textContent = text;
  });
}

function setBusy(busy) {
  refs.board.setAttribute("aria-busy", busy ? "true" : "false");
}

function laneElements() {
  return [...refs.field.querySelectorAll(".lane")];
}

function prefersReducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function laneVisibleRange() {
  const lanes = laneElements();
  const start = refs.board.scrollLeft;
  const end = start + refs.board.clientWidth;
  let first = -1;
  let last = -1;
  lanes.forEach((lane, index) => {
    const left = lane.offsetLeft;
    const right = left + lane.offsetWidth;
    if (right > start + 1 && left < end - 1) {
      if (first < 0) first = index;
      last = index;
    }
  });
  if (first < 0 && lanes.length) return { first: 0, last: 0 };
  return { first, last };
}

function updateLaneNav() {
  const lanes = laneElements();
  const max = Math.max(0, refs.board.scrollWidth - refs.board.clientWidth);
  if (!lanes.length) {
    refs.stepPrev.hidden = true;
    refs.stepNext.hidden = true;
    return;
  }
  const { first, last } = laneVisibleRange();
  const ticks = [...refs.laneScale.querySelectorAll(".plot-nav__tick")];
  ticks.forEach((tick, index) => {
    tick.dataset.inview = index >= first && index <= last ? "true" : "false";
  });
  const total = ticks.length || STATUSES.length;
  refs.laneRange.textContent =
    first === last ? `Lane ${first + 1} of ${total}` : `Lanes ${first + 1}–${last + 1} of ${total}`;
  const overflow = max > 1;
  refs.stepPrev.hidden = !overflow;
  refs.stepNext.hidden = !overflow;
  refs.stepPrev.setAttribute("aria-disabled", !overflow || refs.board.scrollLeft <= 1 ? "true" : "false");
  refs.stepNext.setAttribute(
    "aria-disabled",
    !overflow || refs.board.scrollLeft >= max - 1 ? "true" : "false",
  );
}

let navFrame = null;

function scheduleLaneNav() {
  if (navFrame !== null) return;
  navFrame = requestAnimationFrame(() => {
    navFrame = null;
    updateLaneNav();
  });
}

function scrollBoardTo(left) {
  const max = Math.max(0, refs.board.scrollWidth - refs.board.clientWidth);
  const target = Math.max(0, Math.min(left, max));
  refs.board.scrollTo({ left: target, behavior: prefersReducedMotion() ? "auto" : "smooth" });
  updateLaneNav();
}

function laneLabel(index) {
  const lanes = laneElements();
  const lane = lanes[index];
  if (!lane) return null;
  return { label: statusMeta(lane.dataset.status).label, total: lanes.length };
}

function jumpToLane(index) {
  const lane = laneElements()[index];
  if (!lane) return;
  scrollBoardTo(lane.offsetLeft);
  const meta = laneLabel(index);
  if (meta) announce(`Lane ${index + 1} of ${meta.total}: ${meta.label}.`);
}

function stepLanes(direction) {
  const guard = direction > 0 ? refs.stepNext : refs.stepPrev;
  if (guard.getAttribute("aria-disabled") === "true") return;
  const offsets = laneElements().map((lane) => lane.offsetLeft);
  const target =
    direction > 0
      ? offsets.find((offset) => offset > refs.board.scrollLeft + 2)
      : [...offsets].reverse().find((offset) => offset < refs.board.scrollLeft - 2);
  if (target === undefined) return;
  const index = offsets.indexOf(target);
  scrollBoardTo(target);
  const meta = laneLabel(index);
  if (meta) announce(`Lane ${index + 1} of ${meta.total}: ${meta.label}.`);
}

function buildLaneScale() {
  const items = STATUSES.map((status, index) => {
    const meta = statusMeta(status);
    const item = element("li");
    const button = element("button", "plot-nav__tick", String(index + 1));
    button.type = "button";
    button.dataset.status = status;
    button.dataset.inview = "false";
    button.setAttribute("aria-label", `Show lane ${index + 1} of ${STATUSES.length}: ${meta.label}`);
    button.title = meta.label;
    button.addEventListener("click", () => jumpToLane(index));
    item.append(button);
    return item;
  });
  refs.laneScale.replaceChildren(...items);
}

function cardById(id) {
  return state.cardsById.get(id) || null;
}

function cardTitle(id) {
  const card = cardById(id);
  return card ? card.title : id;
}

function invalidateDetail(id) {
  state.detailCache.delete(id);
}

function currentPreviewId() {
  return state.hoverId || state.focusId;
}

function renderBanner(config) {
  refs.bannerRegion.replaceChildren();
  if (!config) return;
  const banner = element("div", `banner${config.kind === "error" ? " banner--error" : ""}`);
  banner.setAttribute("role", config.kind === "error" ? "alert" : "status");
  const text = element("p", "banner__text", config.text);
  banner.append(text);
  if (config.code) {
    banner.append(element("span", "banner__code", config.code));
  }
  for (const action of config.actions || []) {
    const button = element("button", `btn${action.primary ? " btn--primary" : " btn--quiet"}`, action.label);
    button.type = "button";
    button.addEventListener("click", action.onClick);
    banner.append(button);
  }
  refs.bannerRegion.append(banner);
}

function renderSkeleton() {
  const lanes = STATUSES.map((status) => {
    const meta = statusMeta(status);
    const lane = element("section", "lane");
    lane.dataset.status = status;
    const head = element("header", "lane__head");
    const lamp = element("span", `lamp lamp--${meta.tone}`);
    lamp.setAttribute("aria-hidden", "true");
    const heading = element("h2", "", meta.label);
    const count = element("span", "lane__count", "");
    count.setAttribute("aria-hidden", "true");
    head.append(lamp, heading, count);
    const skeleton = element("div", "skeleton");
    skeleton.setAttribute("aria-hidden", "true");
    skeleton.append(element("div", "skeleton__row"), element("div", "skeleton__row"));
    lane.append(head, skeleton);
    return lane;
  });
  refs.field.replaceChildren(refs.rules, ...lanes);
}

function renderProgress(progress) {
  if (!progress) {
    refs.progressPercent.textContent = "–";
    refs.progressFraction.textContent = "";
    refs.progressSr.textContent = "Progress unavailable";
    refs.progressFill.style.width = "0%";
    return;
  }
  const summary = progressSummary(progress);
  const percentText = formatPercent(summary.percent);
  refs.progressPercent.textContent = percentText;
  refs.progressFraction.textContent = `${summary.closed}/${summary.total} closed`;
  refs.progressSr.textContent = `Completion ${percentText}, ${summary.closed} of ${summary.total} cards closed`;
  refs.progressFill.style.width = `${Math.min(100, Math.max(0, summary.percent))}%`;
}

function updateViewNote() {
  const filtered = hasActiveFilters(state.filters);
  const total = state.progress ? state.progress.total : null;
  if (filtered) {
    const shown = state.lanes.reduce((sum, lane) => sum + lane.cards.length, 0);
    refs.viewNote.textContent = `Filtered plot · ${shown} of ${total ?? "?"} cards`;
  } else {
    refs.viewNote.textContent =
      total === null ? "Work plot · nine status lanes" : `Work plot · ${total} card${total === 1 ? "" : "s"} · nine status lanes`;
  }
}

function rebuildCardIndex() {
  state.cardsById = new Map();
  for (const lane of state.lanes) {
    for (const card of lane.cards) {
      state.cardsById.set(card.id, card);
    }
  }
  const options = [...state.cardsById.values()].map((card) => {
    const option = document.createElement("option");
    option.value = card.id;
    option.label = `${truncateId(card.id)} · ${card.title}`;
    return option;
  });
  refs.cardIds.replaceChildren(...options);
}

function buildCardElement(card) {
  const fragment = refs.tplCard.content.cloneNode(true);
  const button = fragment.querySelector(".card");
  button.dataset.cardId = card.id;
  button.tabIndex = -1;
  const title = fragment.querySelector(".card__title");
  title.textContent = card.title;
  fragment.querySelector(".card__type").textContent = typeLabel(card.card_type);
  const id = fragment.querySelector(".card__id");
  id.textContent = truncateId(card.id);
  id.title = card.id;
  if (!card.parent_id) {
    fragment.querySelector(".card__parent").remove();
  }
  const sr = element("span", "sr-only", `Status ${statusMeta(card.status).label}.`);
  button.append(sr);
  return fragment;
}

function renderBoard() {
  const laneElements = state.lanes.map((lane) => {
    const meta = statusMeta(lane.status);
    const fragment = refs.tplLane.content.cloneNode(true);
    const section = fragment.querySelector(".lane");
    section.dataset.status = lane.status;
    const lamp = fragment.querySelector(".lamp");
    lamp.classList.add(`lamp--${meta.tone}`);
    const heading = fragment.querySelector("h2");
    heading.textContent = meta.label;
    heading.id = `lane-title-${lane.status}`;
    section.setAttribute("aria-labelledby", heading.id);
    heading.append(element("span", "sr-only", `, ${lane.cards.length} card${lane.cards.length === 1 ? "" : "s"}`));
    fragment.querySelector(".lane__count").textContent = String(lane.cards.length);
    const list = fragment.querySelector(".lane__cards");
    if (lane.cards.length === 0) {
      const item = element("li");
      item.append(element("p", "lane__empty", "No cards in this lane."));
      list.append(item);
    } else {
      for (const card of lane.cards) {
        list.append(buildCardElement(card));
      }
    }
    return fragment;
  });
  refs.field.replaceChildren(refs.rules, ...laneElements);
  rebuildCardIndex();
  applySelection();
  applyRoving();
  updateLaneNav();
}

function renderEmptyStates() {
  const filtered = hasActiveFilters(state.filters);
  const total = state.progress ? state.progress.total : 0;
  if (filtered) {
    const shown = state.lanes.reduce((sum, lane) => sum + lane.cards.length, 0);
    if (shown === 0) {
      renderBanner({
        kind: "info",
        text: "No cards match the current filter.",
        actions: [{ label: "Clear filter", onClick: clearFilters }],
      });
      return;
    }
    return;
  }
  if (state.loadedOnce && total === 0) {
    renderBanner({
      kind: "info",
      text: "The plot is empty. Create the first card to start the record.",
      actions: [{ label: "New card", onClick: openCreateDialog, primary: true }],
    });
    return;
  }
  renderBanner(null);
}

function applySelection() {
  const preview = currentPreviewId();
  for (const button of refs.field.querySelectorAll(".card")) {
    const id = button.dataset.cardId;
    button.classList.toggle("is-selected", id === preview);
    if (id === state.selectedId) button.setAttribute("aria-current", "true");
    else button.removeAttribute("aria-current");
  }
  drawCourseLines(preview);
}

function applyRoving() {
  const buttons = [...refs.field.querySelectorAll(".card")];
  for (const button of buttons) button.tabIndex = -1;
  if (buttons.length === 0) return;
  const target =
    buttons.find((button) => button.dataset.cardId === state.pendingFocusId) ||
    buttons.find((button) => button.dataset.cardId === state.focusId) ||
    buttons[0];
  target.tabIndex = 0;
  if (state.pendingFocusId) {
    const pending = state.pendingFocusId;
    state.pendingFocusId = null;
    target.focus();
    target.scrollIntoView({ block: "nearest", inline: "nearest" });
    state.focusId = pending;
  }
}

function focusCard(laneIndex, cardIndex) {
  const lane = state.lanes[laneIndex];
  if (!lane) return false;
  const index = clampCardIndex(lane, cardIndex);
  if (index < 0) return false;
  const card = lane.cards[index];
  const button = refs.field.querySelector(`[data-card-id="${CSS.escape(card.id)}"]`);
  if (!button) return false;
  button.focus();
  return true;
}

function handleCardKeydown(event) {
  const button = event.target.closest(".card");
  if (!button) return;
  const laneElement = button.closest(".lane");
  const laneIndex = state.lanes.findIndex((lane) => lane.status === laneElement.dataset.status);
  const cardIndex = state.lanes[laneIndex]?.cards.findIndex((card) => card.id === button.dataset.cardId) ?? -1;
  if (laneIndex < 0 || cardIndex < 0) return;
  const lane = state.lanes[laneIndex];
  let moved = false;
  switch (event.key) {
    case "ArrowRight": {
      const target = nextLaneIndex(state.lanes, laneIndex, 1);
      if (target !== laneIndex) moved = focusCard(target, cardIndex);
      break;
    }
    case "ArrowLeft": {
      const target = nextLaneIndex(state.lanes, laneIndex, -1);
      if (target !== laneIndex) moved = focusCard(target, cardIndex);
      break;
    }
    case "ArrowDown":
      if (cardIndex < lane.cards.length - 1) moved = focusCard(laneIndex, cardIndex + 1);
      break;
    case "ArrowUp":
      if (cardIndex > 0) moved = focusCard(laneIndex, cardIndex - 1);
      break;
    case "Home":
      if (cardIndex > 0) moved = focusCard(laneIndex, 0);
      break;
    case "End":
      if (cardIndex < lane.cards.length - 1) moved = focusCard(laneIndex, lane.cards.length - 1);
      break;
    default:
      return;
  }
  if (moved) event.preventDefault();
}

function schedulePrefetch(id) {
  window.clearTimeout(state.prefetchTimer);
  if (state.detailCache.has(id)) return;
  state.prefetchTimer = window.setTimeout(async () => {
    try {
      const response = await api(`/cards/${encodeURIComponent(id)}`);
      if (!response.ok) return;
      const detail = normalizeDetail(response.payload);
      detail.fields = readCardFields(response.payload.card);
      state.detailCache.set(id, { detail, at: Date.now() });
      if (currentPreviewId() === id) drawCourseLines(id);
    } catch {
      return;
    }
  }, 150);
}

async function fetchFreshDetail(id) {
  const response = await api(`/cards/${encodeURIComponent(id)}`);
  if (!response.ok) {
    const error = apiError(response.payload, "Could not load this card.");
    throw new RequestFailure(error.message, error.code, error.details);
  }
  const detail = normalizeDetail(response.payload);
  detail.fields = readCardFields(response.payload.card);
  state.detailCache.set(id, { detail, at: Date.now() });
  return detail;
}

function rectEdgePoint(elementRect, targetPoint) {
  const center = { x: elementRect.x + elementRect.width / 2, y: elementRect.y + elementRect.height / 2 };
  const dx = targetPoint.x - center.x;
  const dy = targetPoint.y - center.y;
  if (Math.abs(dx) >= Math.abs(dy)) {
    return { x: dx >= 0 ? elementRect.x + elementRect.width : elementRect.x, y: center.y };
  }
  return { x: center.x, y: dy >= 0 ? elementRect.y + elementRect.height : elementRect.y };
}

function drawCourseLines(previewId) {
  refs.rules.replaceChildren();
  refs.rules.classList.remove("chart__rules--active");
  if (!previewId) return;
  const cached = state.detailCache.get(previewId);
  if (!cached) return;
  const fieldRect = refs.field.getBoundingClientRect();
  refs.rules.setAttribute("width", String(refs.field.offsetWidth));
  refs.rules.setAttribute("height", String(refs.field.offsetHeight));
  let drew = false;
  for (const segment of buildCourseSegments(cached.detail)) {
    const provider = refs.field.querySelector(`[data-card-id="${CSS.escape(segment.provider)}"]`);
    const consumer = refs.field.querySelector(`[data-card-id="${CSS.escape(segment.consumer)}"]`);
    if (!provider || !consumer) continue;
    const providerRect = provider.getBoundingClientRect();
    const consumerRect = consumer.getBoundingClientRect();
    const providerCenter = {
      x: providerRect.x + providerRect.width / 2 - fieldRect.x,
      y: providerRect.y + providerRect.height / 2 - fieldRect.y,
    };
    const consumerCenter = {
      x: consumerRect.x + consumerRect.width / 2 - fieldRect.x,
      y: consumerRect.y + consumerRect.height / 2 - fieldRect.y,
    };
    const start = rectEdgePoint(
      { x: providerRect.x - fieldRect.x, y: providerRect.y - fieldRect.y, width: providerRect.width, height: providerRect.height },
      consumerCenter,
    );
    const end = rectEdgePoint(
      { x: consumerRect.x - fieldRect.x, y: consumerRect.y - fieldRect.y, width: consumerRect.width, height: consumerRect.height },
      providerCenter,
    );
    const angle = Math.atan2(end.y - start.y, end.x - start.x);
    const lineEnd = { x: end.x - Math.cos(angle) * 7, y: end.y - Math.sin(angle) * 7 };
    const line = document.createElementNS(SVG_NS, "line");
    line.setAttribute("x1", String(start.x));
    line.setAttribute("y1", String(start.y));
    line.setAttribute("x2", String(lineEnd.x));
    line.setAttribute("y2", String(lineEnd.y));
    if (segment.open) line.classList.add("is-open");
    const startDot = document.createElementNS(SVG_NS, "circle");
    startDot.setAttribute("cx", String(start.x));
    startDot.setAttribute("cy", String(start.y));
    startDot.setAttribute("r", "2.5");
    startDot.setAttribute("stroke", "currentColor");
    startDot.setAttribute("stroke-width", "1");
    const arrow = document.createElementNS(SVG_NS, "path");
    const wing = 2.6;
    const tip = end;
    const back = 8;
    const left = {
      x: tip.x - Math.cos(angle) * back + Math.cos(angle + Math.PI / 2) * wing,
      y: tip.y - Math.sin(angle) * back + Math.sin(angle + Math.PI / 2) * wing,
    };
    const right = {
      x: tip.x - Math.cos(angle) * back - Math.cos(angle + Math.PI / 2) * wing,
      y: tip.y - Math.sin(angle) * back - Math.sin(angle + Math.PI / 2) * wing,
    };
    arrow.setAttribute("d", `M ${tip.x} ${tip.y} L ${left.x} ${left.y} L ${right.x} ${right.y} Z`);
    if (segment.open) arrow.classList.add("is-open");
    refs.rules.append(line, startDot, arrow);
    drew = true;
  }
  if (drew) refs.rules.classList.add("chart__rules--active");
}

async function loadBoard(reason) {
  setBusy(true);
  if (!state.loadedOnce) renderSkeleton();
  try {
    const query = buildCardsQuery(state.filters);
    const [boardResponse, progressResponse] = await Promise.all([
      api(query ? `/cards?${query}` : "/board"),
      api("/progress"),
    ]);
    if (!boardResponse.ok) {
      const error = apiError(boardResponse.payload, "Could not load the board.");
      throw new RequestFailure(error.message, error.code, error.details);
    }
    state.lanes = query ? lanesFromCardsPayload(boardResponse.payload) : lanesFromBoardPayload(boardResponse.payload);
    state.progress = progressResponse.ok ? progressResponse.payload : null;
    state.loadedOnce = true;
    renderProgress(state.progress);
    renderBoard();
    renderEmptyStates();
    updateViewNote();
    if (reason === "initial") {
      announce(`Board loaded. ${state.lanes.reduce((sum, lane) => sum + lane.cards.length, 0)} cards across nine lanes.`);
    } else if (reason === "update") {
      announce("Board updated.");
    } else if (reason === "filter") {
      announce(`Filter applied. ${state.lanes.reduce((sum, lane) => sum + lane.cards.length, 0)} cards shown.`);
    }
  } catch (error) {
    const message = error instanceof RequestFailure ? error.message : "Unexpected error while loading.";
    const code = error instanceof RequestFailure ? error.code : "error";
    renderBanner({
      kind: "error",
      text: message,
      code,
      actions: [{ label: "Retry", onClick: () => loadBoard("retry"), primary: true }],
    });
    announceUrgent(`Board failed to load. ${message}`);
    if (!state.loadedOnce) {
      refs.field.replaceChildren(refs.rules);
    }
  } finally {
    setBusy(false);
  }
}

function renderDetailLoading(id) {
  const card = cardById(id);
  const body = refs.detailBody;
  body.replaceChildren();
  const head = element("header", "sheet__head");
  const title = element("h2", "sheet__title", card ? card.title : id);
  title.id = "detail-title";
  head.append(title);
  const close = iconButton("Close card detail", "i-close", () => refs.detail.close());
  close.classList.add("sheet__close");
  head.append(close);
  body.append(head);
  const note = element("p", "block__empty", "Loading card detail…");
  note.style.padding = "1rem 1.5rem";
  body.append(note);
}

function iconButton(label, iconName, onClick, extraClass) {
  const button = element("button", `icon-btn${extraClass ? ` ${extraClass}` : ""}`);
  button.type = "button";
  button.setAttribute("aria-label", label);
  button.append(iconUse(iconName));
  button.addEventListener("click", onClick);
  return button;
}

function renderStatusBlock(detail) {
  const block = element("section", "block");
  block.append(element("h3", "block__title", "Position"));
  const meta = statusMeta(detail.card.status);

  if (detail.blocked) {
    const hazard = element("p", "hazard");
    hazard.append(iconUse("i-hazard"));
    const text = element(
      "span",
      "",
      `Blocked: waiting on ${detail.open_dependencies.length} open dependenc${detail.open_dependencies.length === 1 ? "y" : "ies"}.`,
    );
    hazard.append(text);
    block.append(hazard);
  }

  const strip = element("div", "status-strip");
  strip.setAttribute("role", "group");
  strip.setAttribute("aria-label", "Move card to status");
  for (const status of STATUSES) {
    const statusInfo = statusMeta(status);
    const button = element("button", "btn", statusInfo.label);
    button.type = "button";
    if (status === detail.card.status) {
      button.setAttribute("aria-current", "true");
      button.disabled = true;
      button.title = "Current status";
    } else {
      button.addEventListener("click", () => moveStatus(detail.card.id, status));
    }
    strip.append(button);
  }
  block.append(strip);

  const ruler = element("div", "ruler");
  ruler.setAttribute("aria-hidden", "true");
  const steps = statusSteps(detail.card.status);
  const currentIndex = steps.index === null ? -1 : steps.index - 1;
  for (let index = 0; index < STATUSES.length; index += 1) {
    const tick = element("span", "ruler__tick");
    if (index === currentIndex) tick.classList.add("ruler__tick--current");
    ruler.append(tick);
  }
  block.append(ruler);
  block.append(
    element(
      "p",
      "block__note",
      steps.index === null
        ? `Status ${meta.label} is not one of the nine canonical steps.`
        : `Canonical position ${steps.index} of ${steps.total}. Only CLOSED counts as done.`,
    ),
  );

  const feedback = element("p", "alert-line", "");
  feedback.id = "status-feedback";
  feedback.setAttribute("role", "status");
  block.append(feedback);
  return block;
}

function dependencyRow(depId, options) {
  const fragment = refs.tplDepRow.content.cloneNode(true);
  const row = fragment.querySelector(".dep-row");
  const titleButton = fragment.querySelector(".dep-row__title");
  const card = cardById(depId);
  titleButton.textContent = card ? card.title : depId;
  titleButton.title = depId;
  titleButton.addEventListener("click", () => openDetail(depId));
  const status = fragment.querySelector(".dep-row__status");
  const isOpen = options.open;
  const label = card ? statusMeta(card.status).label : "UNKNOWN CARD";
  status.textContent = isOpen ? `${label} · OPEN` : label;
  if (isOpen) status.classList.add("dep-row__status--open");
  const remove = fragment.querySelector(".dep-row__remove");
  if (options.removable) {
    remove.hidden = false;
    remove.setAttribute("aria-label", `Remove dependency on ${card ? card.title : depId}`);
    remove.addEventListener("click", () => removeDependency(options.cardId, depId));
  } else {
    remove.remove();
  }
  return row;
}

function renderDependencyBlock(detail) {
  const block = element("section", "block");
  block.append(element("h3", "block__title", "Dependencies"));

  block.append(element("h4", "block__title", "Waits on"));
  const waits = element("ul", "dep-list");
  if (detail.depends_on.length === 0) {
    waits.append(element("li", "block__empty", "No dependencies. This card can move to READY or IN PROGRESS."));
  } else {
    const openSet = new Set(detail.open_dependencies);
    for (const id of detail.depends_on) {
      waits.append(dependencyRow(id, { open: openSet.has(id), removable: true, cardId: detail.card.id }));
    }
  }
  block.append(waits);

  block.append(element("h4", "block__title", "Holds up"));
  const held = element("ul", "dep-list");
  if (detail.dependents.length === 0) {
    held.append(element("li", "block__empty", "No cards wait on this one."));
  } else {
    const thisOpen = detail.card.status !== "CLOSED";
    for (const id of detail.dependents) {
      held.append(dependencyRow(id, { open: thisOpen, removable: false }));
    }
  }
  block.append(held);

  const form = element("form", "dep-add");
  const field = element("div", "field");
  const label = element("label", "", "Add dependency by card id");
  label.setAttribute("for", "dep-input");
  const input = element("input");
  input.id = "dep-input";
  input.setAttribute("list", "card-ids");
  input.setAttribute("autocomplete", "off");
  input.setAttribute("spellcheck", "false");
  field.append(label, input);
  const addButton = element("button", "btn", "Add");
  addButton.type = "submit";
  form.append(field, addButton);
  const feedback = element("p", "alert-line", "");
  feedback.setAttribute("role", "status");
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const value = input.value.trim();
    if (!value) {
      feedback.className = "alert-line alert-line--hazard";
      feedback.textContent = "Enter the id of the card this one should wait on.";
      input.focus();
      return;
    }
    addDependency(detail.card.id, value, feedback);
  });
  block.append(form, feedback);
  return block;
}

function renderProgressBlock(detail) {
  const block = element("section", "block");
  block.append(element("h3", "block__title", "Progress"));
  const summary = progressSummary(state.progress);
  const cardLabel = statusMeta(detail.card.status).label;
  block.append(
    element(
      "p",
      "block__note",
      `This card: ${cardLabel}. Board: ${formatPercent(summary.percent)} complete, ${summary.closed} of ${summary.total} cards closed.`,
    ),
  );
  return block;
}

function readCardFields(card) {
  const source = card && typeof card === "object" ? card : {};
  const text = (value) => (typeof value === "string" && value.trim() ? value : null);
  const list = (value) =>
    Array.isArray(value) ? value.filter((entry) => typeof entry === "string" && entry.trim()) : [];
  return {
    owner: text(source.owner),
    priority: text(source.priority),
    requirements: list(source.requirements),
    blockers: list(source.blockers),
  };
}

function parseFieldLines(value) {
  return String(value)
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

function firstDuplicate(entries) {
  const seen = new Set();
  for (const entry of entries) {
    if (seen.has(entry)) return entry;
    seen.add(entry);
  }
  return null;
}

function renderFieldsBlock(detail) {
  const fields = detail.fields || readCardFields(null);
  const block = element("section", "block");
  const title = element("h3", "block__title", "Card fields");
  title.id = "fields-block-title";
  title.tabIndex = -1;
  block.append(title);

  const summary = element("dl", "field-summary");
  const textRow = (label, value) => {
    summary.append(element("dt", "", label));
    const dd = element("dd");
    if (value) dd.textContent = value;
    else dd.append(element("span", "block__empty", "Not set."));
    summary.append(dd);
  };
  const listRow = (label, entries) => {
    summary.append(element("dt", "", label));
    const dd = element("dd");
    if (entries.length) {
      const list = element("ul", "field-list");
      for (const entry of entries) list.append(element("li", "", entry));
      dd.append(list);
    } else {
      dd.append(element("span", "block__empty", "None recorded."));
    }
    summary.append(dd);
  };
  textRow("Owner", fields.owner);
  textRow("Priority", fields.priority);
  listRow("Requirements", fields.requirements);
  listRow("Blockers", fields.blockers);
  block.append(summary);

  block.append(
    element(
      "p",
      "block__note",
      "Requirements and blocker notes are free-form annotations; they do not gate movement. Readiness still follows open dependencies.",
    ),
  );

  const form = element("form", "fields-form");
  form.id = "fields-form";
  const textField = (id, labelText, value) => {
    const field = element("div", "field");
    const label = element("label", "", labelText);
    label.setAttribute("for", id);
    const input = element("input");
    input.id = id;
    input.type = "text";
    input.autocomplete = "off";
    input.spellcheck = false;
    input.value = value || "";
    field.append(label, input);
    return { field, input };
  };
  const linesField = (id, labelText, entries) => {
    const field = element("div", "field");
    const label = element("label", "", labelText);
    label.setAttribute("for", id);
    const hintId = `${id}-hint`;
    const textarea = element("textarea");
    textarea.id = id;
    textarea.rows = 3;
    textarea.spellcheck = false;
    textarea.value = entries.join("\n");
    textarea.setAttribute("aria-describedby", hintId);
    const hint = element("p", "field__hint", "One entry per line. Saving with this empty clears it.");
    hint.id = hintId;
    field.append(label, textarea, hint);
    return { field, input: textarea };
  };
  const ownerField = textField("field-owner", "Owner", fields.owner);
  const priorityField = textField("field-priority", "Priority", fields.priority);
  const requirementsField = linesField("field-requirements", "Requirements", fields.requirements);
  const blockersField = linesField("field-blockers", "Blockers", fields.blockers);
  form.append(ownerField.field, priorityField.field, requirementsField.field, blockersField.field);

  const actions = element("div", "fields-form__actions");
  const save = element("button", "btn btn--primary", "Save fields");
  save.type = "submit";
  actions.append(save);
  form.append(actions);

  const feedback = element("p", "alert-line", "");
  feedback.id = "fields-feedback";
  feedback.setAttribute("role", "status");
  feedback.tabIndex = -1;
  form.append(feedback);

  const inputs = {
    owner: ownerField.input,
    priority: priorityField.input,
    requirements: requirementsField.input,
    blockers: blockersField.input,
  };
  for (const input of Object.values(inputs)) {
    input.addEventListener("input", () => {
      input.removeAttribute("aria-invalid");
    });
  }
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    saveCardFields(detail, inputs, feedback, save);
  });

  block.append(form);
  return block;
}

async function saveCardFields(detail, inputs, feedback, saveButton) {
  const owner = inputs.owner.value.trim();
  const priority = inputs.priority.value.trim();
  const requirements = parseFieldLines(inputs.requirements.value);
  const blockers = parseFieldLines(inputs.blockers.value);
  const invalidField = (input, message) => {
    feedback.className = "alert-line alert-line--hazard";
    feedback.textContent = message;
    input.setAttribute("aria-invalid", "true");
    input.focus();
    announceUrgent(message);
  };
  const duplicateRequirements = firstDuplicate(requirements);
  if (duplicateRequirements) {
    invalidField(
      inputs.requirements,
      `Requirements must be unique: "${duplicateRequirements}" appears more than once.`,
    );
    return;
  }
  const duplicateBlockers = firstDuplicate(blockers);
  if (duplicateBlockers) {
    invalidField(
      inputs.blockers,
      `Blocker notes must be unique: "${duplicateBlockers}" appears more than once.`,
    );
    return;
  }
  feedback.className = "alert-line";
  feedback.textContent = "Saving fields…";
  saveButton.disabled = true;
  try {
    const response = await api(`/cards/${encodeURIComponent(detail.card.id)}`, {
      method: "PATCH",
      body: JSON.stringify({
        owner: owner || null,
        priority: priority || null,
        requirements,
        blockers,
      }),
    });
    if (response.ok) {
      announce(`Fields saved for ${detail.card.title}.`);
      await refreshDetailAndBoard(detail.card.id);
      const fresh = refs.detailBody.querySelector("#fields-feedback");
      if (fresh) {
        fresh.className = "alert-line alert-line--ok";
        fresh.textContent = "Fields saved.";
        fresh.focus();
      }
      return;
    }
    const error = apiError(response.payload, "The field update was rejected.");
    feedback.className = "alert-line alert-line--hazard";
    feedback.textContent = error.message;
    const fieldName = error.details && error.details.field;
    const target = inputs[fieldName];
    if (target) {
      target.setAttribute("aria-invalid", "true");
      target.focus();
    } else {
      feedback.focus();
    }
    announceUrgent(error.message);
  } catch (error) {
    const message = error instanceof RequestFailure ? error.message : "Could not save the fields.";
    feedback.className = "alert-line alert-line--hazard";
    feedback.textContent = message;
    feedback.focus();
    announceUrgent(message);
  } finally {
    saveButton.disabled = false;
  }
}

function handoffText(detail) {
  return JSON.stringify(buildHandoffExport(detail, cardById), null, 2);
}

async function copyHandoff(textarea, feedback) {
  feedback.className = "alert-line";
  feedback.textContent = "Copying handoff JSON…";
  try {
    if (!navigator.clipboard || typeof navigator.clipboard.writeText !== "function") {
      throw new Error("clipboard unavailable");
    }
    await navigator.clipboard.writeText(textarea.value);
    feedback.className = "alert-line alert-line--ok";
    feedback.textContent = "Handoff JSON copied to the clipboard.";
    announce("Handoff JSON copied to the clipboard.");
  } catch {
    textarea.focus();
    textarea.select();
    feedback.className = "alert-line alert-line--hazard";
    feedback.textContent = "Clipboard unavailable. The JSON is selected below; copy it manually.";
    announce("Clipboard unavailable. The handoff JSON is selected below; copy it manually.");
  }
}

function downloadHandoff(detail, textarea, feedback) {
  const name = handoffFileName(detail.card.id);
  const blob = new Blob([textarea.value], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
  feedback.className = "alert-line alert-line--ok";
  feedback.textContent = `Downloaded ${name}.`;
  announce(`Handoff JSON downloaded as ${name}.`);
}

function renderHandoffBlock(detail) {
  const block = element("section", "block");
  block.append(element("h3", "block__title", "Handoff export"));
  block.append(
    element(
      "p",
      "block__note",
      "Manual JSON export of this card. Fields the V1 contract does not define are exported empty; nothing is sent and nothing changes.",
    ),
  );
  const textarea = element("textarea", "handoff__json");
  textarea.id = "handoff-json";
  textarea.readOnly = true;
  textarea.spellcheck = false;
  textarea.setAttribute("aria-label", "Handoff export JSON");
  textarea.value = handoffText(detail);
  const actions = element("div", "handoff__actions");
  const feedback = element("p", "alert-line", "");
  feedback.setAttribute("role", "status");
  const copy = element("button", "btn", "Copy JSON");
  copy.type = "button";
  copy.addEventListener("click", () => copyHandoff(textarea, feedback));
  const download = element("button", "btn btn--quiet", "Download JSON");
  download.type = "button";
  download.addEventListener("click", () => downloadHandoff(detail, textarea, feedback));
  actions.append(copy, download);
  block.append(textarea, actions, feedback);
  return block;
}

function workflowSection(kind) {
  const block = element("section", "block");
  const heading = kind === "evidence" ? "Approval evidence" : "Revision history";
  block.append(element("h3", "block__title", heading));
  const container = element("div", "workflow-section");
  container.dataset.kind = kind;
  const loading = element("p", "block__empty", "Loading…");
  container.append(loading);
  block.append(container);
  return block;
}

function renderWorkflowSection(container, result) {
  container.replaceChildren();
  if (result.state === "loading") {
    container.append(element("p", "block__empty", "Loading…"));
    return;
  }
  if (result.state === "unavailable") {
    container.append(
      element(
        "p",
        "block__empty",
        `Not available: the Kanban V1 API contract defines no ${container.dataset.kind} route. Tracked as backlog; this surface does not invent it.`,
      ),
    );
    return;
  }
  if (result.state === "error") {
    const line = element("p", "alert-line alert-line--hazard", result.error ? result.error.message : "Could not load.");
    container.append(line);
    return;
  }
  if (result.items.length === 0) {
    container.append(element("p", "block__empty", "No entries."));
    return;
  }
  if (result.synthetic) {
    const note = element("p", "block__note");
    note.append(element("span", "evidence-stamp", "Synthetic"), document.createTextNode(" Local mock data, not from a live workflow service."));
    container.append(note);
  }
  const list = element("ul", container.dataset.kind === "evidence" ? "evidence-list" : "history-list");
  for (const item of result.items) {
    const entry = element("li", "entry");
    const text = element("span", "entry__text");
    text.append(element("strong", "", item.title));
    if (item.detail) text.append(document.createTextNode(` · ${item.detail}`));
    if (item.synthetic) text.append(document.createTextNode(" "), element("span", "evidence-stamp", "Synthetic"));
    const metaParts = [item.actor, item.at].filter(Boolean);
    const meta = element("span", "entry__meta", metaParts.join(" · "));
    entry.append(text, meta);
    list.append(entry);
  }
  container.append(list);
}

function normalizeWorkflowEntry(item) {
  if (!item || typeof item !== "object") {
    return { title: "Entry", detail: "", actor: "", at: "", synthetic: false };
  }
  return {
    title: String(item.label ?? item.title ?? "Entry"),
    detail: item.detail !== undefined || item.description !== undefined ? String(item.detail ?? item.description) : "",
    actor: item.actor ? String(item.actor) : "",
    at: item.at ? String(item.at) : "",
    synthetic: Boolean(item.synthetic),
  };
}

async function fetchWorkflowSection(id, kind) {
  try {
    const response = await api(`/cards/${encodeURIComponent(id)}/${kind}`);
    if (response.status === 405) return { state: "unavailable", items: [], synthetic: false };
    if (response.status === 404) {
      const error = response.payload ? apiError(response.payload) : null;
      if (error && error.details && error.details.card_id) {
        return { state: "error", items: [], synthetic: false, error };
      }
      return { state: "unavailable", items: [], synthetic: false };
    }
    if (!response.ok) {
      return { state: "error", items: [], synthetic: false, error: apiError(response.payload) };
    }
    const payload = response.payload || {};
    const items = Array.isArray(payload.items) ? payload.items.map(normalizeWorkflowEntry) : [];
    return { state: "ready", items, synthetic: Boolean(payload.synthetic) || items.some((item) => item.synthetic) };
  } catch (error) {
    return {
      state: "error",
      items: [],
      synthetic: false,
      error: { message: error instanceof RequestFailure ? error.message : "Could not load." },
    };
  }
}

function renderDetail(detail) {
  const body = refs.detailBody;
  body.replaceChildren();
  const head = element("header", "sheet__head");
  if (state.detailStack.length > 1) {
    const back = iconButton("Back to previous card", "i-back", () => {
      state.detailStack.pop();
      openDetail(state.detailStack[state.detailStack.length - 1], { pushed: true });
    });
    head.append(back);
  }
  const title = element("h2", "sheet__title", detail.card.title);
  title.id = "detail-title";
  head.append(title);
  head.append(iconButton("Close card detail", "i-close", () => refs.detail.close(), "sheet__close"));
  body.append(head);

  const metaList = element("dl", "sheet__meta");
  const metaPairs = [
    ["Type", typeLabel(detail.card.card_type)],
    ["Status", statusMeta(detail.card.status).label],
    ["Id", detail.card.id, "mono"],
  ];
  for (const [label, value, className] of metaPairs) {
    metaList.append(element("dt", "", label));
    const dd = element("dd", className || "", value);
    if (label === "Id") dd.title = detail.card.id;
    metaList.append(dd);
  }
  body.append(metaList);

  if (detail.card.parent_id) {
    const parentRow = element("div", "sheet__meta");
    parentRow.append(element("dt", "", "Parent"));
    const parentButton = element("button", "dep-row__title", `${truncateId(detail.card.parent_id)} · ${cardTitle(detail.card.parent_id)}`);
    parentButton.type = "button";
    parentButton.title = detail.card.parent_id;
    parentButton.addEventListener("click", () => openDetail(detail.card.parent_id));
    const wrapper = element("dd");
    wrapper.append(parentButton);
    parentRow.append(wrapper);
    body.append(parentRow);
  }

  body.append(renderFieldsBlock(detail));
  body.append(renderStatusBlock(detail));
  body.append(renderDependencyBlock(detail));
  body.append(renderProgressBlock(detail));
  body.append(renderHandoffBlock(detail));
  const evidence = workflowSection("evidence");
  const history = workflowSection("history");
  body.append(evidence, history);
  return { evidence: evidence.querySelector(".workflow-section"), history: history.querySelector(".workflow-section") };
}

async function refreshDetailAndBoard(id) {
  invalidateDetail(id);
  await loadBoard("update");
  if (!refs.detail.open || state.selectedId !== id) return;
  try {
    const detail = await fetchFreshDetail(id);
    state.detail = detail;
    const sections = renderDetail(detail);
    announce(describeCardForAnnouncement(detail));
    for (const [kind, container] of [["evidence", sections.evidence], ["history", sections.history]]) {
      fetchWorkflowSection(id, kind).then((result) => renderWorkflowSection(container, result));
    }
  } catch (error) {
    const message = error instanceof RequestFailure ? error.message : "Could not refresh this card.";
    announceUrgent(message);
  }
}

async function openDetail(id, options = {}) {
  if (!options.pushed) {
    if (state.detailStack[state.detailStack.length - 1] !== id) {
      state.detailStack.push(id);
    }
  }
  state.selectedId = id;
  state.detail = null;
  const wasOpen = refs.detail.open;
  renderDetailLoading(id);
  if (!wasOpen) refs.detail.showModal();
  applySelection();
  announce(`Opening ${cardTitle(id)}.`);
  try {
    const detail = await fetchFreshDetail(id);
    if (state.selectedId !== id) return;
    state.detail = detail;
    const sections = renderDetail(detail);
    if (wasOpen) {
      const back = refs.detailBody.querySelector(".sheet__head .icon-btn");
      if (back) back.focus();
    }
    announce(describeCardForAnnouncement(detail));
    for (const [kind, container] of [["evidence", sections.evidence], ["history", sections.history]]) {
      fetchWorkflowSection(id, kind).then((result) => renderWorkflowSection(container, result));
    }
  } catch (error) {
    const message = error instanceof RequestFailure ? error.message : "Could not load this card.";
    const body = refs.detailBody;
    body.replaceChildren();
    body.append(element("p", "banner banner--error", message));
    announceUrgent(message);
  }
}

async function moveStatus(id, target) {
  const feedback = refs.detailBody.querySelector("#status-feedback");
  const card = cardById(id);
  const targetLabel = statusMeta(target).label;
  if (feedback) {
    feedback.className = "alert-line";
    feedback.textContent = `Moving to ${targetLabel}…`;
  }
  try {
    const response = await api(`/cards/${encodeURIComponent(id)}/status`, {
      method: "PATCH",
      body: JSON.stringify({ status: target }),
    });
    if (response.ok) {
      announce(`${card ? card.title : id} moved to ${targetLabel}.`);
      await refreshDetailAndBoard(id);
      const fresh = refs.detailBody.querySelector("#status-feedback");
      if (fresh) {
        fresh.className = "alert-line alert-line--ok";
        fresh.textContent = `Moved to ${targetLabel}.`;
      }
      return;
    }
    const error = apiError(response.payload, "The move was rejected.");
    if (feedback) {
      feedback.className = "alert-line alert-line--hazard";
      feedback.textContent = error.message;
      if (error.code === "dependencies_not_closed" && Array.isArray(error.details.open_dependencies)) {
        const list = element("span", "");
        error.details.open_dependencies.forEach((depId, index) => {
          if (index > 0) list.append(document.createTextNode(", "));
          const link = element("button", "dep-row__title", cardTitle(depId));
          link.type = "button";
          link.title = depId;
          link.addEventListener("click", () => openDetail(depId));
          list.append(link);
        });
        feedback.append(document.createTextNode(" Open: "), list, document.createTextNode("."));
      }
    }
    announceUrgent(`${targetLabel} move rejected. ${error.message}`);
  } catch (error) {
    const message = error instanceof RequestFailure ? error.message : "The move failed.";
    if (feedback) {
      feedback.className = "alert-line alert-line--hazard";
      feedback.textContent = message;
    }
    announceUrgent(message);
  }
}

async function addDependency(cardId, dependsOnId, feedback) {
  feedback.className = "alert-line";
  feedback.textContent = "Adding dependency…";
  try {
    const response = await api(`/cards/${encodeURIComponent(cardId)}/dependencies`, {
      method: "POST",
      body: JSON.stringify({ depends_on_card_id: dependsOnId }),
    });
    if (response.ok) {
      announce(`Dependency added: ${cardTitle(cardId)} now waits on ${cardTitle(dependsOnId)}.`);
      await refreshDetailAndBoard(cardId);
      return;
    }
    const error = apiError(response.payload, "The dependency was rejected.");
    feedback.className = "alert-line alert-line--hazard";
    feedback.textContent =
      error.code === "dependency_cycle" && Array.isArray(error.details.path)
        ? `${error.message} Path: ${error.details.path.join(" → ")}.`
        : error.message;
    announceUrgent(error.message);
  } catch (error) {
    const message = error instanceof RequestFailure ? error.message : "Could not add the dependency.";
    feedback.className = "alert-line alert-line--hazard";
    feedback.textContent = message;
    announceUrgent(message);
  }
}

async function removeDependency(cardId, dependsOnId) {
  try {
    const response = await api(
      `/cards/${encodeURIComponent(cardId)}/dependencies/${encodeURIComponent(dependsOnId)}`,
      { method: "DELETE" },
    );
    if (response.ok) {
      announce(`Dependency removed: ${cardTitle(cardId)} no longer waits on ${cardTitle(dependsOnId)}.`);
      invalidateDetail(dependsOnId);
      await refreshDetailAndBoard(cardId);
      return;
    }
    const error = apiError(response.payload, "Could not remove the dependency.");
    announceUrgent(error.message);
  } catch (error) {
    announceUrgent(error instanceof RequestFailure ? error.message : "Could not remove the dependency.");
  }
}

function openCreateDialog() {
  refs.createForm.reset();
  refs.createError.hidden = true;
  refs.createError.textContent = "";
  refs.createDialog.showModal();
  refs.createTitleInput.focus();
}

async function submitCreate(event) {
  event.preventDefault();
  const title = refs.createTitleInput.value.trim();
  if (!title) {
    refs.createError.hidden = false;
    refs.createError.textContent = "Enter a title for the card.";
    refs.createTitleInput.focus();
    return;
  }
  const body = {
    title,
    card_type: refs.createType.value,
  };
  const parentId = refs.createParent.value.trim();
  if (parentId) body.parent_id = parentId;
  const owner = refs.createOwner.value.trim();
  const priority = refs.createPriority.value.trim();
  const requirements = parseFieldLines(refs.createRequirements.value);
  const blockers = parseFieldLines(refs.createBlockers.value);
  const duplicateRequirements = firstDuplicate(requirements);
  if (duplicateRequirements) {
    refs.createError.hidden = false;
    refs.createError.textContent = `Requirements must be unique: "${duplicateRequirements}" appears more than once.`;
    refs.createRequirements.focus();
    return;
  }
  const duplicateBlockers = firstDuplicate(blockers);
  if (duplicateBlockers) {
    refs.createError.hidden = false;
    refs.createError.textContent = `Blocker notes must be unique: "${duplicateBlockers}" appears more than once.`;
    refs.createBlockers.focus();
    return;
  }
  if (owner) body.owner = owner;
  if (priority) body.priority = priority;
  if (requirements.length) body.requirements = requirements;
  if (blockers.length) body.blockers = blockers;
  refs.createSubmit.disabled = true;
  try {
    const response = await api("/cards", { method: "POST", body: JSON.stringify(body) });
    if (response.ok) {
      const created = response.payload?.card;
      refs.createDialog.close();
      if (created?.id) state.pendingFocusId = String(created.id);
      announce(`Card created: ${created ? created.title : title}.`);
      await loadBoard("update");
      return;
    }
    const error = apiError(response.payload, "The card was rejected.");
    refs.createError.hidden = false;
    refs.createError.textContent = error.message;
    announceUrgent(error.message);
  } catch (error) {
    const message = error instanceof RequestFailure ? error.message : "Could not create the card.";
    refs.createError.hidden = false;
    refs.createError.textContent = message;
    announceUrgent(message);
  } finally {
    refs.createSubmit.disabled = false;
  }
}

function applyFilters(event) {
  event.preventDefault();
  const filters = {
    status: refs.filterStatus.value,
    card_type: refs.filterType.value,
    has_parent: refs.filterParentage.value,
    parent_id: refs.filterParent.value.trim(),
  };
  const errors = validateFilterValues(filters);
  if (errors.length) {
    renderBanner({ kind: "error", text: errors[0], code: "validation_error" });
    return;
  }
  state.filters = filters;
  loadBoard("filter");
}

function clearFilters() {
  refs.filterForm.reset();
  state.filters = { ...EMPTY_FILTERS };
  loadBoard("filter");
}

function bindEvents() {
  refs.field.addEventListener("click", (event) => {
    const card = event.target.closest(".card");
    if (card) openDetail(card.dataset.cardId);
  });
  refs.field.addEventListener("keydown", handleCardKeydown);
  refs.field.addEventListener("focusin", (event) => {
    const card = event.target.closest(".card");
    if (!card) return;
    const id = card.dataset.cardId;
    if (state.focusId !== id) {
      state.focusId = id;
      const cardData = cardById(id);
      if (cardData) {
        announce(`${cardData.title}. ${statusMeta(cardData.status).label}. Press Enter to open the card detail.`);
      }
    }
    applySelection();
    schedulePrefetch(id);
  });
  refs.field.addEventListener("mouseover", (event) => {
    const card = event.target.closest(".card");
    if (!card) return;
    const id = card.dataset.cardId;
    if (state.hoverId === id) return;
    if (state.detailCache.has(id)) {
      state.hoverId = id;
      applySelection();
    }
  });
  refs.field.addEventListener("mouseout", (event) => {
    const card = event.target.closest(".card");
    if (!card) return;
    if (state.hoverId && !card.contains(event.relatedTarget)) {
      state.hoverId = null;
      applySelection();
    }
  });
  refs.filterForm.addEventListener("submit", applyFilters);
  refs.filterClear.addEventListener("click", clearFilters);
  refs.gridToggle.addEventListener("click", () => {
    const pressed = refs.gridToggle.getAttribute("aria-pressed") === "true";
    refs.gridToggle.setAttribute("aria-pressed", pressed ? "false" : "true");
    refs.field.classList.toggle("chart__field--grid", !pressed);
    announce(pressed ? "Graticule off." : "Graticule on.");
  });
  refs.board.addEventListener("scroll", scheduleLaneNav, { passive: true });
  refs.board.addEventListener("keydown", (event) => {
    if (event.target !== refs.board) return;
    if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      event.preventDefault();
      stepLanes(event.key === "ArrowRight" ? 1 : -1);
    } else if (event.key === "Home") {
      event.preventDefault();
      scrollBoardTo(0);
    } else if (event.key === "End") {
      event.preventDefault();
      scrollBoardTo(refs.board.scrollWidth);
    }
  });
  refs.stepPrev.addEventListener("click", () => stepLanes(-1));
  refs.stepNext.addEventListener("click", () => stepLanes(1));
  refs.refresh.addEventListener("click", () => loadBoard("update"));
  refs.newCard.addEventListener("click", openCreateDialog);
  refs.createForm.addEventListener("submit", submitCreate);
  refs.createCancel.addEventListener("click", () => refs.createDialog.close());
  refs.detail.addEventListener("close", () => {
    state.selectedId = null;
    state.detail = null;
    state.detailStack = [];
    applySelection();
  });
  const observer = new ResizeObserver(() => {
    drawCourseLines(currentPreviewId());
    scheduleLaneNav();
  });
  observer.observe(refs.field);
}

function cacheRefs() {
  refs.board = document.getElementById("board");
  refs.field = document.getElementById("field");
  refs.rules = document.getElementById("rules");
  refs.laneScale = document.getElementById("lane-scale");
  refs.laneRange = document.getElementById("lane-range");
  refs.stepPrev = document.getElementById("lane-step-prev");
  refs.stepNext = document.getElementById("lane-step-next");
  refs.bannerRegion = document.getElementById("banner-region");
  refs.viewNote = document.getElementById("view-note");
  refs.progressPercent = document.getElementById("progress-percent");
  refs.progressFraction = document.getElementById("progress-fraction");
  refs.progressSr = document.getElementById("progress-sr");
  refs.progressFill = document.getElementById("progress-fill");
  refs.filterForm = document.getElementById("filters");
  refs.filterStatus = document.getElementById("filter-status");
  refs.filterType = document.getElementById("filter-type");
  refs.filterParentage = document.getElementById("filter-parentage");
  refs.filterParent = document.getElementById("filter-parent");
  refs.filterClear = document.getElementById("filter-clear");
  refs.gridToggle = document.getElementById("grid-toggle");
  refs.refresh = document.getElementById("refresh");
  refs.newCard = document.getElementById("new-card");
  refs.detail = document.getElementById("detail");
  refs.detailBody = document.getElementById("detail-body");
  refs.createDialog = document.getElementById("create-dialog");
  refs.createForm = document.getElementById("create-form");
  refs.createTitleInput = document.getElementById("create-title-input");
  refs.createType = document.getElementById("create-type");
  refs.createParent = document.getElementById("create-parent");
  refs.createOwner = document.getElementById("create-owner");
  refs.createPriority = document.getElementById("create-priority");
  refs.createRequirements = document.getElementById("create-requirements");
  refs.createBlockers = document.getElementById("create-blockers");
  refs.createError = document.getElementById("create-error");
  refs.createSubmit = document.getElementById("create-submit");
  refs.createCancel = document.getElementById("create-cancel");
  refs.cardIds = document.getElementById("card-ids");
  refs.announcer = document.getElementById("announcer");
  refs.alertAnnouncer = document.getElementById("alert-announcer");
  refs.tplLane = document.getElementById("tpl-lane");
  refs.tplCard = document.getElementById("tpl-card");
  refs.tplDepRow = document.getElementById("tpl-dep-row");
}

function init() {
  cacheRefs();
  bindEvents();
  buildLaneScale();
  renderSkeleton();
  updateLaneNav();
  loadBoard("initial");
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
