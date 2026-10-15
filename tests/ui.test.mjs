import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import {
  startMockServer,
  loadPlaywright,
  launchBrowser,
  waitForBoard,
  laneHeadings,
  laneCounts,
  cardTitles,
  activeCardTitle,
  blockExternalRequests,
} from "./helpers.mjs";

const CANONICAL = [
  "TODO",
  "READY",
  "IN PROGRESS",
  "REVIEW",
  "CHANGES REQUESTED",
  "APPROVED",
  "MERGED",
  "CLOSED",
  "BLOCKED",
];

let browser = null;

before(async () => {
  const playwright = await loadPlaywright();
  if (!playwright) return;
  browser = await launchBrowser(playwright.chromium);
});

after(async () => {
  if (browser) await browser.close();
});

async function withPage(t, options, run) {
  if (!browser) {
    t.skip("playwright-core or a chromium executable is unavailable");
    return;
  }
  const server = await startMockServer({ workflowMocks: options.workflowMocks || false });
  const context = await browser.newContext({
    viewport: options.viewport || { width: 1440, height: 900 },
    reducedMotion: options.reducedMotion || "no-preference",
  });
  const blocked = [];
  await blockExternalRequests(context, (url) => blocked.push(url));
  const page = await context.newPage();
  const requests = [];
  page.on("request", (request) => requests.push(request.url()));
  try {
    if (options.scenario) await server.scenario(options.scenario);
    await page.goto(`${server.baseUrl}/`, options.waitUntil ? { waitUntil: options.waitUntil } : {});
    if (options.skipInitialWait !== true) await waitForBoard(page);
    await run(page, server, { blocked, requests });
  } finally {
    await context.close();
    await server.stop();
  }
}

const DESKTOP_VIEWPORT = { width: 1440, height: 900 };
const MOBILE_VIEWPORT = { width: 390, height: 844 };

async function laneGeometry(page) {
  return page.evaluate(() => {
    const board = document.getElementById("board");
    const lanes = [...document.querySelectorAll("#field .lane")];
    return {
      scrollLeft: board.scrollLeft,
      maxScroll: Math.max(0, board.scrollWidth - board.clientWidth),
      clientWidth: board.clientWidth,
      laneLefts: lanes.map((lane) => lane.offsetLeft),
      laneWidths: lanes.map((lane) => lane.offsetWidth),
    };
  });
}

async function laneFullyVisible(page, index) {
  return page.evaluate((i) => {
    const board = document.getElementById("board");
    const lane = document.querySelectorAll("#field .lane")[i];
    if (!lane) return false;
    const start = board.scrollLeft;
    const end = start + board.clientWidth;
    return lane.offsetLeft >= start - 1 && lane.offsetLeft + lane.offsetWidth <= end + 1;
  }, index);
}

const cardLocator = (page, title) => page.locator(".card", { hasText: title }).first();

async function openCard(page, title) {
  await cardLocator(page, title).click();
  await page.waitForSelector("#detail[open] .status-strip");
}

test("board loads: nine canonical lanes, counts, progress, no skeleton", async (t) => {
  await withPage(t, {}, async (page) => {
    assert.deepEqual(await laneHeadings(page), CANONICAL);
    assert.deepEqual(await laneCounts(page), ["4", "1", "2", "1", "1", "1", "1", "2", "1"]);
    assert.equal((await page.textContent("#progress-percent")).trim(), "14.29%");
    assert.match(await page.textContent("#progress-sr"), /2 of 14 cards closed/);
    assert.match(await page.textContent("#view-note"), /14 cards/);
    assert.equal(await page.locator(".skeleton__row").count(), 0);
    assert.equal(await page.getAttribute("#board", "aria-busy"), "false");
  });
});

test("filters apply with AND semantics and clear back", async (t) => {
  await withPage(t, {}, async (page) => {
    await page.selectOption("#filter-type", "task");
    await page.click('#filters button[type="submit"]');
    await page.waitForFunction(() => document.getElementById("view-note").textContent.includes("Filtered"));
    assert.match(await page.textContent("#view-note"), /Filtered plot · 10 of 14 cards/);
    const titles = await cardTitles(page);
    assert.ok(titles.includes("Plot course lines"));
    assert.ok(!titles.includes("Ingest pipeline"));
    assert.deepEqual(await laneCounts(page), ["2", "0", "1", "1", "1", "1", "1", "2", "1"]);
    await page.click("#filter-clear");
    await page.waitForFunction(() => {
      const note = document.getElementById("view-note").textContent;
      return note.includes("14 cards") && !note.includes("Filtered");
    });
    assert.deepEqual(await laneCounts(page), ["4", "1", "2", "1", "1", "1", "1", "2", "1"]);
  });
});

test("keyboard: arrows move focus across lanes, Enter opens, Escape restores focus", async (t) => {
  await withPage(t, {}, async (page) => {
    await page.focus('.lane[data-status="TODO"] .card');
    assert.equal(await activeCardTitle(page), "North Atlantic Survey Grid");
    await page.keyboard.press("ArrowRight");
    assert.equal(await activeCardTitle(page), "Chart renderer");
    await page.keyboard.press("ArrowDown");
    assert.equal(await activeCardTitle(page), "Chart renderer");
    await page.keyboard.press("ArrowLeft");
    assert.equal(await activeCardTitle(page), "North Atlantic Survey Grid");
    await page.keyboard.press("ArrowDown");
    assert.equal(await activeCardTitle(page), "Tide almanac");
    await page.keyboard.press("ArrowUp");
    assert.equal(await activeCardTitle(page), "North Atlantic Survey Grid");
    await page.keyboard.press("Enter");
    await page.waitForSelector("#detail[open]");
    assert.equal((await page.textContent("#detail-title")).trim(), "North Atlantic Survey Grid");
    await page.keyboard.press("Escape");
    await page.waitForFunction(() => !document.getElementById("detail").open);
    assert.equal(await activeCardTitle(page), "North Atlantic Survey Grid");
  });
});

test("card detail shows parent, dependency context, blocked reason and progress", async (t) => {
  await withPage(t, {}, async (page) => {
    await openCard(page, "Backfill sounding archive");
    assert.equal((await page.textContent("#detail-title")).trim(), "Backfill sounding archive");
    assert.match(await page.textContent("#detail"), /Blocked: waiting on 1 open dependency/);
    const waitRow = page.locator("#detail .dep-row", { hasText: "Plot course lines" }).first();
    assert.match(await waitRow.textContent(), /OPEN/);
    assert.match(await page.textContent("#detail"), /Ingest pipeline/);
    assert.match(await page.textContent("#detail"), /14.29% complete, 2 of 14 cards closed/);
    const current = page.locator("#detail .status-strip .btn", { hasText: "BLOCKED" });
    assert.equal(await current.getAttribute("aria-current"), "true");
    assert.equal(await current.isDisabled(), true);
  });
});

test("blocked move surfaces the API 409 with open dependencies", async (t) => {
  await withPage(t, {}, async (page) => {
    await openCard(page, "Backfill sounding archive");
    await page.locator("#detail").getByRole("button", { name: "READY", exact: true }).click();
    await page.waitForFunction(() =>
      (document.querySelector("#status-feedback")?.textContent || "").includes("dependencies are not CLOSED"),
    );
    const feedback = await page.textContent("#status-feedback");
    assert.match(feedback, /cannot move card to READY/);
    assert.match(feedback, /Plot course lines/);
    assert.equal(await page.locator("#detail .status-strip .btn", { hasText: "BLOCKED" }).getAttribute("aria-current"), "true");
  });
});

test("unrestricted status move updates the board lanes", async (t) => {
  await withPage(t, {}, async (page) => {
    await openCard(page, "Wire ingestion adapters");
    await page.locator("#detail").getByRole("button", { name: "REVIEW", exact: true }).click();
    await page.waitForFunction(() => {
      const feedback = document.querySelector("#status-feedback");
      return feedback && feedback.textContent.includes("Moved to REVIEW");
    });
    await page.waitForFunction(() => {
      const review = document.querySelector('.lane[data-status="REVIEW"]');
      return review && review.textContent.includes("Wire ingestion adapters");
    });
    const progress = await page.textContent("#progress-percent");
    assert.equal(progress.trim(), "14.29%");
    assert.equal((await laneCounts(page))[3], "2");
  });
});

test("duplicate dependency is rejected and removal clears the block", async (t) => {
  await withPage(t, {}, async (page) => {
    await openCard(page, "Backfill sounding archive");
    await page.fill("#dep-input", "9f2c1ab34d5e6f05");
    await page.locator("#detail .dep-add").getByRole("button", { name: "Add", exact: true }).click();
    await page.waitForFunction(() => {
      const lines = [...document.querySelectorAll("#detail .alert-line")];
      return lines.some((line) => line.textContent.includes("dependency already exists"));
    });
    const row = page.locator("#detail .dep-row", { hasText: "Plot course lines" }).first();
    await row.locator(".dep-row__remove").click();
    await page.waitForFunction(() => !document.querySelector("#detail .hazard"));
    const detail = await page.textContent("#detail");
    assert.match(detail, /No dependencies\. This card can move to READY or IN PROGRESS\./);
    await page.keyboard.press("Escape");
    await page.waitForFunction(() => !document.getElementById("detail").open);
    const blockedLane = await page.locator('.lane[data-status="BLOCKED"]').textContent();
    assert.match(blockedLane, /Backfill sounding archive/);
  });
});

test("dependency cycle is refused with the path from the API", async (t) => {
  await withPage(t, {}, async (page) => {
    await openCard(page, "Plot course lines");
    await page.fill("#dep-input", "9f2c1ab34d5e6f03");
    await page.locator("#detail .dep-add").getByRole("button", { name: "Add", exact: true }).click();
    await page.waitForFunction(() => {
      const lines = [...document.querySelectorAll("#detail .alert-line")];
      return lines.some((line) => line.textContent.includes("cycle"));
    });
    const feedback = page.locator("#detail .alert-line", { hasText: "cycle" }).first();
    assert.match(await feedback.textContent(), /→/);
  });
});

test("creating a card adds it to TODO and focuses it", async (t) => {
  await withPage(t, {}, async (page) => {
    await page.click("#new-card");
    await page.fill("#create-title-input", "Test vessel");
    await page.click("#create-submit");
    await page.waitForFunction(() => !document.getElementById("create-dialog").open);
    await page.waitForFunction(() =>
      [...document.querySelectorAll(".card__title")].some((node) => node.textContent.trim() === "Test vessel"),
    );
    assert.equal(await activeCardTitle(page), "Test vessel");
    assert.deepEqual(await laneCounts(page), ["5", "1", "2", "1", "1", "1", "1", "2", "1"]);
  });
});

test("loading state shows the skeleton before the board resolves", async (t) => {
  await withPage(t, { scenario: { delay_ms: 900 }, skipInitialWait: true, waitUntil: "domcontentloaded" }, async (page) => {
    await page.waitForSelector(".skeleton__row");
    assert.equal(await page.getAttribute("#board", "aria-busy"), "true");
    assert.ok((await page.locator(".skeleton__row").count()) > 0);
    await waitForBoard(page);
    assert.equal(await page.locator(".skeleton__row").count(), 0);
  });
});

test("load failure shows an alert banner and retry recovers", async (t) => {
  await withPage(t, { scenario: { mode: "error" }, skipInitialWait: true }, async (page, server) => {
    await page.waitForSelector(".banner--error");
    assert.equal(await page.getAttribute(".banner--error", "role"), "alert");
    assert.match(await page.textContent(".banner--error"), /unexpected server error/);
    await server.scenario({ mode: "normal" });
    await page.getByRole("button", { name: "Retry", exact: true }).click();
    await waitForBoard(page);
    assert.deepEqual(await laneCounts(page), ["4", "1", "2", "1", "1", "1", "1", "2", "1"]);
  });
});

test("empty board teaches the next action and every lane reads empty", async (t) => {
  await withPage(t, { scenario: { mode: "empty" } }, async (page) => {
    assert.match(await page.textContent("#banner-region"), /The plot is empty/);
    assert.deepEqual(await laneCounts(page), ["0", "0", "0", "0", "0", "0", "0", "0", "0"]);
    assert.equal(await page.locator(".lane__empty").count(), 9);
    assert.match(await page.textContent("#progress-sr"), /0 of 0 cards closed/);
  });
});

test("filter with no matches explains and offers a clear action", async (t) => {
  await withPage(t, {}, async (page) => {
    await page.selectOption("#filter-status", "APPROVED");
    await page.selectOption("#filter-parentage", "false");
    await page.click('#filters button[type="submit"]');
    await page.waitForFunction(() => document.getElementById("banner-region").textContent.includes("No cards match"));
    await page.locator("#banner-region").getByRole("button", { name: "Clear filter" }).click();
    await page.waitForFunction(() => document.getElementById("view-note").textContent.includes("14 cards"));
  });
});

test("workflow records read as unavailable against the contract-true API", async (t) => {
  await withPage(t, {}, async (page) => {
    await openCard(page, "Backfill sounding archive");
    await page.waitForFunction(() =>
      document.getElementById("detail").textContent.includes("contract defines no evidence route"),
    );
    const text = await page.textContent("#detail");
    assert.match(text, /contract defines no history route/);
    assert.doesNotMatch(text, /Synthetic/);
  });
});

test("workflow records render labeled synthetic data when mocks are on", async (t) => {
  await withPage(t, { workflowMocks: true }, async (page) => {
    await openCard(page, "Backfill sounding archive");
    await page.waitForSelector("#detail .evidence-list .entry");
    const text = await page.textContent("#detail");
    assert.match(text, /Synthetic/);
    assert.match(text, /Criterion: archive covers sounding years/);
    assert.equal(await page.locator("#detail .history-list .entry").count(), 3);
  });
});

test("accessibility: names, live regions, visible keyboard focus, no page overflow", async (t) => {
  await withPage(t, {}, async (page) => {
    const unnamed = await page.$$eval("button", (buttons) =>
      buttons.filter((button) => !(button.getAttribute("aria-label") || button.textContent.trim())).length,
    );
    assert.equal(unnamed, 0);
    assert.equal(await page.locator('#detail[aria-labelledby="detail-title"]').count(), 1);
    assert.equal(await page.locator("#announcer[aria-live]").count(), 1);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    assert.ok(overflow <= 1, `page overflows horizontally by ${overflow}px`);
    const chartScrolls = await page.evaluate(() => {
      const chart = document.getElementById("board");
      return chart.scrollWidth > chart.clientWidth;
    });
    assert.equal(chartScrolls, true);
    await page.focus('.lane[data-status="TODO"] .card');
    await page.keyboard.press("ArrowRight");
    const outline = await page.evaluate(() => {
      const active = document.activeElement;
      const style = getComputedStyle(active);
      return { width: parseFloat(style.outlineWidth), style: style.outlineStyle };
    });
    assert.ok(outline.width >= 2 && outline.style !== "none", "keyboard focus must be visible");
  });
});

test("click-through: every control responds with no console errors", async (t) => {
  await withPage(t, { workflowMocks: true }, async (page) => {
    const errors = [];
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(message.text());
    });
    page.on("pageerror", (error) => errors.push(String(error)));
    await page.locator("#lane-scale .plot-nav__tick").nth(8).click();
    await page.waitForFunction(() => {
      const board = document.getElementById("board");
      return board.scrollLeft >= board.scrollWidth - board.clientWidth - 1;
    });
    await page.locator("#lane-scale .plot-nav__tick").nth(0).click();
    await page.waitForFunction(() => document.getElementById("board").scrollLeft <= 1);
    await page.click("#lane-step-next");
    await page.waitForFunction(() => document.getElementById("board").scrollLeft > 1);
    await page.click("#lane-step-prev");
    await page.waitForFunction(() => document.getElementById("board").scrollLeft <= 1);
    await page.click("#grid-toggle");
    assert.equal(await page.getAttribute("#grid-toggle", "aria-pressed"), "false");
    await page.click("#grid-toggle");
    await page.click("#refresh");
    await page.waitForFunction(() => document.getElementById("board").getAttribute("aria-busy") === "false");
    await page.selectOption("#filter-status", "BLOCKED");
    await page.click('#filters button[type="submit"]');
    await page.waitForFunction(() => document.getElementById("view-note").textContent.includes("Filtered"));
    await page.click("#filter-clear");
    await page.waitForFunction(() => !document.getElementById("view-note").textContent.includes("Filtered"));
    await openCard(page, "Backfill sounding archive");
    await page.locator("#detail").getByRole("button", { name: "CHANGES REQUESTED", exact: true }).click();
    await page.waitForFunction(() => {
      const feedback = document.querySelector("#status-feedback");
      return feedback && feedback.textContent.includes("Moved to CHANGES REQUESTED");
    });
    await page.keyboard.press("Escape");
    await page.waitForFunction(() => !document.getElementById("detail").open);
    await page.click("#new-card");
    await page.click("#create-cancel");
    await page.waitForFunction(() => !document.getElementById("create-dialog").open);
    assert.deepEqual(errors, []);
  });
});

test("mobile: no page overflow, chart pans internally, tap targets hold", async (t) => {
  await withPage(t, { viewport: { width: 390, height: 844 } }, async (page) => {
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    assert.ok(overflow <= 1, `mobile page overflows horizontally by ${overflow}px`);
    const sizes = await page.evaluate(() => {
      const newCard = document.getElementById("new-card").getBoundingClientRect();
      const chart = document.getElementById("board");
      return {
        buttonHeight: newCard.height,
        chartScrollable: chart.scrollWidth > chart.clientWidth,
        laneWidth: document.querySelector(".lane").getBoundingClientRect().width,
      };
    });
    assert.ok(sizes.buttonHeight >= 44, `toolbar button height ${sizes.buttonHeight} below 44px`);
    assert.equal(sizes.chartScrollable, true);
    assert.ok(sizes.laneWidth >= 240 && sizes.laneWidth <= 310, `lane width ${sizes.laneWidth} unexpected`);
    await openCard(page, "Backfill sounding archive");
    const sheet = await page.evaluate(() => {
      const dialog = document.getElementById("detail").getBoundingClientRect();
      return { width: dialog.width, height: dialog.height, viewport: window.innerWidth };
    });
    assert.ok(sheet.width >= sheet.viewport - 2, "mobile detail sheet should fill the width");
  });
});

test("nine-lane navigation: scale names every lane, jumps reveal each, end state is exact (desktop)", async (t) => {
  await withPage(t, { viewport: DESKTOP_VIEWPORT, reducedMotion: "reduce" }, async (page) => {
    const ticks = page.locator("#lane-scale .plot-nav__tick");
    assert.equal(await ticks.count(), 9);
    const labels = await ticks.evaluateAll((nodes) => nodes.map((node) => node.getAttribute("aria-label")));
    assert.deepEqual(labels, CANONICAL.map((label, index) => `Show lane ${index + 1} of 9: ${label}`));
    assert.match(await page.textContent("#lane-range"), /Lanes 1–6 of 9/);
    assert.deepEqual(
      await ticks.evaluateAll((nodes) => nodes.map((node) => node.dataset.inview)),
      ["true", "true", "true", "true", "true", "true", "false", "false", "false"],
    );
    const geometry = await laneGeometry(page);
    assert.equal(geometry.laneLefts.length, 9);
    for (let index = 0; index < 9; index += 1) {
      await ticks.nth(index).click();
      assert.equal(await laneFullyVisible(page, index), true, `lane ${index + 1} not visible after its jump`);
      assert.equal(await ticks.nth(index).getAttribute("data-inview"), "true", `tick ${index + 1} should read in view`);
    }
    const end = await laneGeometry(page);
    assert.equal(end.scrollLeft, end.maxScroll, "jumping to lane nine must reach the scroll end");
    assert.equal(await laneFullyVisible(page, 8), true);
    assert.match(await page.textContent("#lane-range"), /Lanes 4–9 of 9/);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    assert.ok(overflow <= 1, `page overflows horizontally by ${overflow}px`);
  });
});

test("nine-lane navigation: edge steps advance one lane and stop at both boundaries (desktop)", async (t) => {
  await withPage(t, { viewport: DESKTOP_VIEWPORT, reducedMotion: "reduce" }, async (page) => {
    const prev = page.locator("#lane-step-prev");
    const next = page.locator("#lane-step-next");
    assert.equal(await prev.isVisible(), true);
    assert.equal(await next.isVisible(), true);
    assert.equal(await prev.getAttribute("aria-disabled"), "true");
    assert.equal(await next.getAttribute("aria-disabled"), "false");
    const geometry = await laneGeometry(page);
    const laneWidth = geometry.laneLefts[1] - geometry.laneLefts[0];
    let steps = 0;
    while ((await next.getAttribute("aria-disabled")) !== "true" && steps < 20) {
      const before = await page.evaluate(() => document.getElementById("board").scrollLeft);
      await next.click();
      const after = await page.evaluate(() => document.getElementById("board").scrollLeft);
      assert.ok(after > before, `step ${steps + 1} did not advance (${before} -> ${after})`);
      assert.ok(after - before <= laneWidth + 1, `step ${steps + 1} skipped a lane`);
      steps += 1;
    }
    assert.ok(steps > 0 && steps < 20, `unexpected step count ${steps}`);
    const end = await laneGeometry(page);
    assert.equal(end.scrollLeft, end.maxScroll);
    assert.equal(await laneFullyVisible(page, 8), true);
    assert.equal(await next.getAttribute("aria-disabled"), "true");
    assert.equal(await prev.getAttribute("aria-disabled"), "false");
    await next.evaluate((node) => node.click());
    assert.equal(
      await page.evaluate(() => document.getElementById("board").scrollLeft),
      end.scrollLeft,
      "a boundary-disabled step must not move the board",
    );
    let back = 0;
    while ((await prev.getAttribute("aria-disabled")) !== "true" && back < 20) {
      await prev.click();
      back += 1;
    }
    assert.equal(await page.evaluate(() => document.getElementById("board").scrollLeft), 0);
    assert.equal(await prev.getAttribute("aria-disabled"), "true");
    assert.equal(await laneFullyVisible(page, 0), true);
  });
});

test("nine-lane navigation: board region is keyboard scrollable through every lane (desktop)", async (t) => {
  await withPage(t, { viewport: DESKTOP_VIEWPORT, reducedMotion: "reduce" }, async (page) => {
    await page.focus("#board");
    assert.equal(await page.evaluate(() => document.activeElement.id), "board");
    assert.equal(await page.getAttribute("#board", "aria-describedby"), "board-hint");
    const geometry = await laneGeometry(page);
    await page.keyboard.press("ArrowRight");
    assert.equal(
      await page.evaluate(() => document.getElementById("board").scrollLeft),
      geometry.laneLefts[1],
    );
    await page.keyboard.press("ArrowLeft");
    assert.equal(await page.evaluate(() => document.getElementById("board").scrollLeft), 0);
    await page.keyboard.press("End");
    assert.equal(await page.evaluate(() => document.getElementById("board").scrollLeft), geometry.maxScroll);
    assert.equal(await laneFullyVisible(page, 8), true);
    await page.keyboard.press("Home");
    assert.equal(await page.evaluate(() => document.getElementById("board").scrollLeft), 0);
    assert.equal(await laneFullyVisible(page, 0), true);
    let presses = 0;
    while (!(await laneFullyVisible(page, 8)) && presses < 12) {
      await page.keyboard.press("ArrowRight");
      presses += 1;
    }
    assert.ok(presses < 12, "arrow keys failed to reach the last lane");
    assert.equal(await laneFullyVisible(page, 8), true);
  });
});

test("nine-lane navigation: a viewport wide enough for all nine hides the step controls", async (t) => {
  await withPage(t, { viewport: { width: 2560, height: 900 }, reducedMotion: "reduce" }, async (page) => {
    assert.equal(await page.locator("#lane-step-prev").isVisible(), false);
    assert.equal(await page.locator("#lane-step-next").isVisible(), false);
    assert.match(await page.textContent("#lane-range"), /Lanes 1–9 of 9/);
    assert.deepEqual(
      await page.$$eval("#lane-scale .plot-nav__tick", (nodes) => nodes.map((node) => node.dataset.inview)),
      Array(9).fill("true"),
    );
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    assert.ok(overflow <= 1, `page overflows horizontally by ${overflow}px`);
  });
});

test("nine-lane navigation: mobile shows the range cue, holds 44px steps, and reaches lane nine (mobile)", async (t) => {
  await withPage(t, { viewport: MOBILE_VIEWPORT, reducedMotion: "reduce" }, async (page) => {
    assert.deepEqual(await laneHeadings(page), CANONICAL);
    assert.match(await page.textContent("#lane-range"), /Lanes 1–2 of 9/);
    assert.equal(await page.locator("#lane-scale").isVisible(), false);
    const next = page.locator("#lane-step-next");
    assert.equal(await next.isVisible(), true);
    const size = await next.evaluate((node) => {
      const rect = node.getBoundingClientRect();
      return { width: rect.width, height: rect.height };
    });
    assert.ok(size.width >= 44 && size.height >= 44, `step control ${size.width}x${size.height} below 44px`);
    const start = await laneGeometry(page);
    await next.click();
    assert.equal(await page.evaluate(() => document.getElementById("board").scrollLeft), start.laneLefts[1]);
    assert.equal(await laneFullyVisible(page, 1), true);
    assert.match(await page.textContent("#lane-range"), /Lanes 2–3 of 9/);
    let steps = 0;
    while ((await next.getAttribute("aria-disabled")) !== "true" && steps < 20) {
      await next.click();
      steps += 1;
    }
    assert.ok(steps < 20, "mobile steps failed to reach the end");
    const end = await laneGeometry(page);
    assert.equal(end.scrollLeft, end.maxScroll);
    assert.equal(await laneFullyVisible(page, 8), true);
    assert.match(await page.textContent("#lane-range"), /Lanes 8–9 of 9/);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    assert.ok(overflow <= 1, `mobile page overflows horizontally by ${overflow}px`);
  });
});

test("browser suite stays local: every request targets the mock origin, none blocked", async (t) => {
  await withPage(t, {}, async (page, server, { blocked, requests }) => {
    await openCard(page, "Backfill sounding archive");
    await page.keyboard.press("Escape");
    await page.waitForFunction(() => !document.getElementById("detail").open);
    await page.locator("#lane-scale .plot-nav__tick").nth(8).click();
    assert.ok(requests.length > 0, "expected the page to issue requests");
    for (const url of requests) {
      assert.ok(
        url.startsWith(server.baseUrl) || url.startsWith("data:"),
        `request escaped the local mock origin: ${url}`,
      );
    }
    assert.deepEqual(blocked, [], "external requests must never be needed");
  });
});

const HANDOFF_FIXTURE = {
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
};

async function openHandoff(page, title) {
  await openCard(page, title);
  await page.waitForSelector("#detail[open] .handoff__json");
}

test("handoff export: JSON names the card, parent, dependencies and blockers", async (t) => {
  await withPage(t, {}, async (page) => {
    await openHandoff(page, "Backfill sounding archive");
    const raw = await page.inputValue("#detail .handoff__json");
    assert.deepEqual(JSON.parse(raw), HANDOFF_FIXTURE);
    assert.match(raw, /"objective": null/);
    assert.match(raw, /"evidence": \[\]/);
  });
});

test("handoff export: copy writes the JSON by explicit keyboard action", async (t) => {
  await withPage(t, {}, async (page) => {
    await openHandoff(page, "Backfill sounding archive");
    await page.evaluate(() => {
      window.__handoffCopied = [];
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: {
          writeText: async (text) => {
            window.__handoffCopied.push(text);
          },
        },
      });
    });
    const copy = page.locator("#detail").getByRole("button", { name: "Copy JSON", exact: true });
    await copy.focus();
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => document.querySelector("#detail .alert-line--ok") !== null);
    const copied = await page.evaluate(() => window.__handoffCopied);
    assert.equal(copied.length, 1);
    assert.deepEqual(JSON.parse(copied[0]), HANDOFF_FIXTURE);
    assert.match(await page.textContent("#detail .alert-line--ok"), /copied to the clipboard/);
  });
});

test("handoff export: unavailable clipboard degrades to a selected manual copy", async (t) => {
  await withPage(t, {}, async (page) => {
    await openHandoff(page, "Backfill sounding archive");
    await page.evaluate(() => {
      Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined });
    });
    await page.locator("#detail").getByRole("button", { name: "Copy JSON", exact: true }).click();
    const feedback = page.locator("#detail .alert-line", { hasText: "Clipboard unavailable" });
    await feedback.waitFor();
    const selection = await page.evaluate(() => {
      const textarea = document.getElementById("handoff-json");
      return {
        focused: document.activeElement === textarea,
        start: textarea.selectionStart,
        end: textarea.selectionEnd,
        length: textarea.value.length,
      };
    });
    assert.equal(selection.focused, true);
    assert.equal(selection.start, 0);
    assert.equal(selection.end, selection.length);
    assert.ok(selection.length > 0, "the fallback must leave the JSON selectable");
  });
});

test("handoff export: download produces the JSON file on demand", async (t) => {
  await withPage(t, {}, async (page) => {
    await openHandoff(page, "Backfill sounding archive");
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.locator("#detail").getByRole("button", { name: "Download JSON", exact: true }).click(),
    ]);
    assert.equal(download.suggestedFilename(), "handoff-9f2c1ab34d5e6f03.json");
    const filePath = await download.path();
    const raw = fs.readFileSync(filePath, "utf8");
    assert.deepEqual(JSON.parse(raw), HANDOFF_FIXTURE);
  });
});

test("handoff export: opening the card exports nothing on its own", async (t) => {
  await withPage(t, {}, async (page, server, { requests }) => {
    await page.evaluate(() => {
      window.__handoffCopied = [];
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: {
          writeText: async (text) => {
            window.__handoffCopied.push(text);
          },
        },
      });
    });
    const downloads = [];
    page.on("download", (download) => downloads.push(download.suggestedFilename()));
    const writes = [];
    page.on("request", (request) => {
      if (request.method() !== "GET") writes.push(`${request.method()} ${request.url()}`);
    });
    await openHandoff(page, "Backfill sounding archive");
    await page.waitForTimeout(300);
    assert.deepEqual(await page.evaluate(() => window.__handoffCopied), []);
    assert.deepEqual(downloads, []);
    assert.deepEqual(writes, [], "opening the detail must not mutate anything");
    assert.deepEqual(requests.filter((url) => /handoff/i.test(url)), []);
    const response = await fetch(`${server.baseUrl}/cards/9f2c1ab34d5e6f03`);
    assert.equal(response.ok, true);
    const payload = await response.json();
    assert.equal(payload.card.status, "BLOCKED", "the export must never move the card");
  });
});

const FIELD_CARD_ID = "9f2c1ab34d5e6f03";

function jsonFulfil(body, status = 200) {
  return { status, contentType: "application/json", body: JSON.stringify(body) };
}

function fieldCard(overrides = {}) {
  return {
    id: FIELD_CARD_ID,
    title: "Backfill sounding archive",
    card_type: "task",
    status: "BLOCKED",
    parent_id: "0b2c43aa91de0002",
    ...overrides,
  };
}

function fieldDetail(overrides = {}) {
  return {
    card: fieldCard(overrides),
    depends_on: ["9f2c1ab34d5e6f05"],
    dependents: [],
    open_dependencies: ["9f2c1ab34d5e6f05"],
    blocked: true,
  };
}

function routeFieldCard(page, handler) {
  return page.route(`**/cards/${FIELD_CARD_ID}`, handler);
}

test("card fields: detail shows persisted values and prefills the edit form", async (t) => {
  await withPage(t, {}, async (page) => {
    const detail = fieldDetail({
      owner: "alice",
      priority: "high",
      requirements: ["parse feed", "emit JSON"],
      blockers: ["vendor access"],
    });
    await routeFieldCard(page, (route) =>
      route.request().method() === "GET" ? route.fulfill(jsonFulfil(detail)) : route.continue(),
    );
    await openCard(page, "Backfill sounding archive");
    await page.waitForSelector("#detail .field-summary");
    const summary = await page.textContent("#detail .field-summary");
    assert.match(summary, /Owner\s*alice/);
    assert.match(summary, /Priority\s*high/);
    assert.match(summary, /parse feed/);
    assert.match(summary, /emit JSON/);
    assert.match(summary, /vendor access/);
    assert.equal(await page.inputValue("#field-owner"), "alice");
    assert.equal(await page.inputValue("#field-priority"), "high");
    assert.equal(await page.inputValue("#field-requirements"), "parse feed\nemit JSON");
    assert.equal(await page.inputValue("#field-blockers"), "vendor access");
    assert.match(await page.textContent("#detail .block__note"), /do not gate movement/);
  });
});

test("card fields: unset values read as honestly empty", async (t) => {
  await withPage(t, {}, async (page) => {
    await openCard(page, "Plot course lines");
    await page.waitForSelector("#detail .field-summary");
    const summary = await page.textContent("#detail .field-summary");
    assert.match(summary, /Owner\s*Not set\./);
    assert.match(summary, /Priority\s*Not set\./);
    assert.match(summary, /Requirements\s*None recorded\./);
    assert.match(summary, /Blockers\s*None recorded\./);
    assert.equal(await page.inputValue("#field-owner"), "");
    assert.equal(await page.inputValue("#field-priority"), "");
    assert.equal(await page.inputValue("#field-requirements"), "");
    assert.equal(await page.inputValue("#field-blockers"), "");
  });
});

test("card fields: saving PATCHes trimmed values, clears empty ones, refreshes detail", async (t) => {
  await withPage(t, {}, async (page) => {
    let patchBody = null;
    let current = fieldDetail({
      owner: "alice",
      priority: "high",
      requirements: ["parse feed"],
      blockers: ["vendor access"],
    });
    await routeFieldCard(page, (route) => {
      if (route.request().method() !== "PATCH") return route.fulfill(jsonFulfil(current));
      patchBody = JSON.parse(route.request().postData());
      const card = fieldCard();
      if (patchBody.owner) card.owner = patchBody.owner;
      if (patchBody.priority) card.priority = patchBody.priority;
      if (patchBody.requirements.length) card.requirements = patchBody.requirements;
      if (patchBody.blockers.length) card.blockers = patchBody.blockers;
      current = fieldDetail();
      current.card = card;
      return route.fulfill(jsonFulfil({ card }));
    });
    await openCard(page, "Backfill sounding archive");
    await page.waitForSelector("#fields-form");
    await page.fill("#field-owner", "  bob  ");
    await page.fill("#field-priority", "");
    await page.fill("#field-requirements", "parse feed v2\n\n emit JSON ");
    await page.click('#fields-form button[type="submit"]');
    await page.waitForFunction(() =>
      (document.querySelector("#fields-feedback")?.textContent || "").includes("Fields saved"),
    );
    assert.deepEqual(patchBody, {
      owner: "bob",
      priority: null,
      requirements: ["parse feed v2", "emit JSON"],
      blockers: ["vendor access"],
    });
    const summary = await page.textContent("#detail .field-summary");
    assert.match(summary, /Owner\s*bob/);
    assert.match(summary, /Priority\s*Not set\./);
    assert.match(summary, /parse feed v2/);
    assert.equal(await page.inputValue("#field-priority"), "");
    const focused = await page.evaluate(() => document.activeElement && document.activeElement.id);
    assert.equal(focused, "fields-feedback");
  });
});

test("card fields: server validation errors surface and focus the offending field", async (t) => {
  await withPage(t, {}, async (page) => {
    const initial = fieldDetail({ owner: "alice" });
    await routeFieldCard(page, (route) => {
      if (route.request().method() === "PATCH") {
        return route.fulfill(
          jsonFulfil(
            {
              error: {
                code: "validation_error",
                message: "requirements must be a list of unique non-empty strings",
                details: { field: "requirements", reason: "invalid_requirements" },
              },
            },
            422,
          ),
        );
      }
      return route.fulfill(jsonFulfil(initial));
    });
    await openCard(page, "Backfill sounding archive");
    await page.waitForSelector("#fields-form");
    await page.fill("#field-requirements", "one\ntwo");
    await page.click('#fields-form button[type="submit"]');
    await page.waitForFunction(() =>
      (document.querySelector("#fields-feedback")?.className || "").includes("alert-line--hazard"),
    );
    assert.match(await page.textContent("#fields-feedback"), /unique non-empty strings/);
    const state = await page.evaluate(() => ({
      focused: document.activeElement && document.activeElement.id,
      invalid: document.querySelector("#field-requirements")?.getAttribute("aria-invalid"),
    }));
    assert.equal(state.focused, "field-requirements");
    assert.equal(state.invalid, "true");
    assert.match(await page.textContent("#detail .field-summary"), /alice/);
  });
});

test("card fields: duplicate lines are refused before any request", async (t) => {
  await withPage(t, {}, async (page) => {
    let patches = 0;
    await routeFieldCard(page, (route) => {
      if (route.request().method() === "PATCH") {
        patches += 1;
        return route.continue();
      }
      return route.fulfill(jsonFulfil(fieldDetail()));
    });
    await openCard(page, "Backfill sounding archive");
    await page.fill("#field-requirements", "one\none");
    await page.click('#fields-form button[type="submit"]');
    await page.waitForFunction(() =>
      (document.querySelector("#fields-feedback")?.textContent || "").includes("must be unique"),
    );
    assert.equal(patches, 0, "a locally refused duplicate must not reach the API");
    const focused = await page.evaluate(() => document.activeElement && document.activeElement.id);
    assert.equal(focused, "field-requirements");
  });
});

test("card creation exposes the persisted fields and drops blank lines", async (t) => {
  await withPage(t, {}, async (page) => {
    const posts = [];
    await page.route("**/cards", (route) => {
      if (route.request().method() !== "POST") return route.continue();
      const body = JSON.parse(route.request().postData());
      posts.push(body);
      return route.fulfill(
        jsonFulfil(
          {
            card: {
              id: "new000000000001",
              title: body.title,
              card_type: body.card_type,
              status: "TODO",
              parent_id: body.parent_id ?? null,
            },
          },
          201,
        ),
      );
    });
    await page.click("#new-card");
    await page.fill("#create-title-input", "Field survey");
    await page.fill("#create-owner", "  alice ");
    await page.fill("#create-priority", "high");
    await page.fill("#create-requirements", "one\n\ntwo ");
    await page.fill("#create-blockers", "vendor");
    await page.click("#create-submit");
    await page.waitForFunction(() => !document.getElementById("create-dialog").open);
    assert.deepEqual(posts, [
      {
        title: "Field survey",
        card_type: "task",
        owner: "alice",
        priority: "high",
        requirements: ["one", "two"],
        blockers: ["vendor"],
      },
    ]);

    await page.click("#new-card");
    await page.fill("#create-title-input", "Duplicate lines");
    await page.fill("#create-requirements", "one\none");
    await page.click("#create-submit");
    await page.waitForFunction(() => !document.getElementById("create-error").hidden);
    assert.match(await page.textContent("#create-error"), /must be unique/);
    assert.equal(posts.length, 1, "the duplicate must be refused before any request");
    assert.equal(await page.evaluate(() => document.getElementById("create-dialog").open), true);
  });
});
