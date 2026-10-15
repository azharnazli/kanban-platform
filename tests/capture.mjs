import fs from "node:fs";
import path from "node:path";
import {
  startMockServer,
  loadPlaywright,
  launchBrowser,
  waitForBoard,
  blockExternalRequests,
  repoRoot,
} from "./helpers.mjs";

const outDir = path.join(repoRoot, ".impeccable", "review");
fs.mkdirSync(outDir, { recursive: true });

const playwright = await loadPlaywright();
if (!playwright) {
  console.error("playwright-core unavailable; cannot capture");
  process.exit(1);
}
const browser = await launchBrowser(playwright.chromium);
const server = await startMockServer({ workflowMocks: true });

const capture = async (name, viewport, prepare) => {
  const context = await browser.newContext({ viewport, reducedMotion: "reduce" });
  await blockExternalRequests(context);
  const page = await context.newPage();
  await page.goto(`${server.baseUrl}/`);
  await waitForBoard(page);
  if (prepare) await prepare(page);
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: path.join(outDir, name), fullPage: false });
  await context.close();
  console.log(`captured ${name}`);
};

const stepToLaneNine = async (page) => {
  await page.getByRole("button", { name: /Show lane 9 of 9/ }).click();
  await page.waitForFunction(() => {
    const board = document.getElementById("board");
    return board.scrollLeft >= board.scrollWidth - board.clientWidth - 1;
  });
};

const stepMobileToEnd = async (page) => {
  const next = page.locator("#lane-step-next");
  for (let step = 0; step < 12; step += 1) {
    if ((await next.getAttribute("aria-disabled")) === "true") break;
    await next.click();
  }
  await page.waitForFunction(() => {
    const board = document.getElementById("board");
    return board.scrollLeft >= board.scrollWidth - board.clientWidth - 1;
  });
};

await capture("desktop.png", { width: 1440, height: 900 });
await capture("desktop-lanes-end.png", { width: 1440, height: 900 }, stepToLaneNine);
await capture("mobile.png", { width: 390, height: 844 });
await capture("mobile-lanes-end.png", { width: 390, height: 844 }, stepMobileToEnd);
await capture("detail-desktop.png", { width: 1440, height: 900 }, async (page) => {
  await page.locator(".card", { hasText: "Backfill sounding archive" }).first().click();
  await page.waitForSelector("#detail[open] .evidence-list");
  await page.waitForTimeout(200);
});
await capture("detail-mobile.png", { width: 390, height: 844 }, async (page) => {
  await page.locator(".card", { hasText: "Backfill sounding archive" }).first().click();
  await page.waitForSelector("#detail[open] .evidence-list");
  await page.waitForTimeout(200);
});
await capture("filtered-desktop.png", { width: 1440, height: 900 }, async (page) => {
  await page.selectOption("#filter-type", "task");
  await page.click('#filters button[type="submit"]');
  await page.waitForFunction(() => document.getElementById("view-note").textContent.includes("Filtered"));
});

await browser.close();
await server.stop();
console.log("done");
