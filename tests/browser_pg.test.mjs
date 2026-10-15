import { test } from "node:test";
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import {
  testsDir,
  repoRoot,
  staticDir,
  loadPlaywright,
  launchBrowser,
  chromiumExecutable,
  waitForBoard,
  laneHeadings,
  laneCounts,
  cardTitles,
} from "./helpers.mjs";

function resolvePython() {
  const venv = path.join(repoRoot, ".venv", "bin", "python");
  if (fs.existsSync(venv)) return venv;
  return "python3";
}
const PYTHON = resolvePython();
const HELPER = path.join(testsDir, "browser_pg_wsgi.py");
const SERVER_START_TIMEOUT_MS = 120000;

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

function delay(ms) {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    timer.unref();
  });
}

function startPgServer() {
  return new Promise((resolve, reject) => {
    const child = spawn(PYTHON, [HELPER, "serve", "--static-dir", staticDir], {
      stdio: ["pipe", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      child.kill("SIGKILL");
      reject(new Error(`browser_pg_wsgi did not become ready in time; stderr:\n${stderr}`));
    }, SERVER_START_TIMEOUT_MS);
    const fail = (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(error);
    };
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString();
      if (settled) return;
      const unavailable = stdout.match(/^UNAVAILABLE (.+)$/m);
      if (unavailable) {
        const error = new Error(unavailable[1]);
        error.unavailable = true;
        fail(error);
        return;
      }
      const ready = stdout.match(/READY port=(\d+) dsn=(\S+) container=(\S+)/);
      if (!ready) return;
      settled = true;
      clearTimeout(timer);
      const container = ready[3];
      resolve({
        child,
        baseUrl: `http://127.0.0.1:${ready[1]}`,
        dsn: ready[2],
        container,
        async stop() {
          if (child.exitCode === null && child.signalCode === null) {
            child.kill("SIGTERM");
            const exited = await Promise.race([
              new Promise((done) => child.once("exit", () => done(true))),
              delay(20000).then(() => false),
            ]);
            if (!exited) {
              child.kill("SIGKILL");
              await new Promise((done) => child.once("exit", done));
            }
          }
          spawnSync("docker", ["rm", "-f", container], { stdio: "ignore" });
          const inspect = spawnSync("docker", ["inspect", container], { stdio: "ignore" });
          return inspect.status !== 0;
        },
      });
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
    });
    child.once("exit", (code, signal) => {
      if (!settled && (code !== null || signal !== null)) {
        fail(new Error(`browser_pg_wsgi exited before ready (code=${code} signal=${signal}); stderr:\n${stderr}`));
      }
    });
  });
}

function readDatabase(dsn) {
  const result = spawnSync(PYTHON, [HELPER, "read", "--dsn", dsn], { encoding: "utf8" });
  if (result.status !== 0) {
    throw new Error(`browser_pg_wsgi read failed (status=${result.status}): ${result.stderr}`);
  }
  return JSON.parse(result.stdout);
}

async function apiGet(baseUrl, route) {
  const response = await fetch(`${baseUrl}${route}`);
  const payload = await response.json();
  return { status: response.status, payload };
}

test("integrated browser acceptance: static UI against kanban_pg_wsgi over disposable PostgreSQL", async (t) => {
  const pythonProbe = spawnSync(PYTHON, ["-c", "import psycopg"], { stdio: "ignore" });
  if (pythonProbe.error || pythonProbe.status !== 0) {
    t.skip(`python interpreter ${PYTHON} cannot import psycopg`);
    return;
  }
  const playwright = await loadPlaywright();
  if (!playwright) {
    t.skip("playwright-core is unavailable");
    return;
  }
  if (!chromiumExecutable()) {
    t.skip("no chromium executable is available for Playwright");
    return;
  }

  let server;
  try {
    server = await startPgServer();
  } catch (error) {
    if (error.unavailable) {
      t.skip(`disposable PostgreSQL unavailable: ${error.message}`);
      return;
    }
    throw error;
  }

  const baseUrl = server.baseUrl;
  const loopbackOrigin = new URL(baseUrl).origin;
  let browser = null;
  let context = null;
  let containerRemoved = false;
  try {
    browser = await launchBrowser(playwright.chromium);
    context = await browser.newContext({ viewport: { width: 1440, height: 900 } });

    const blocked = [];
    const requested = [];
    await context.route("**/*", (route) => {
      const url = route.request().url();
      let origin = "";
      let protocol = "";
      try {
        const parsed = new URL(url);
        origin = parsed.origin;
        protocol = parsed.protocol;
      } catch {
        origin = "";
        protocol = "";
      }
      if (origin === loopbackOrigin || protocol === "data:" || protocol === "about:") {
        return route.continue();
      }
      blocked.push(url);
      return route.abort("blockedbyclient");
    });

    const page = await context.newPage();
    page.on("request", (request) => requested.push(request.url()));
    const responses = [];
    page.on("response", (response) => responses.push({ url: response.url(), status: response.status() }));
    const consoleErrors = [];
    page.on("console", (message) => {
      if (message.type() === "error") {
        consoleErrors.push({ text: message.text(), url: message.location().url || "" });
      }
    });
    page.on("pageerror", (error) => consoleErrors.push({ text: String(error), url: "" }));

    await page.goto(`${baseUrl}/`);
    await waitForBoard(page);

    assert.deepEqual(await laneHeadings(page), CANONICAL, "nine canonical lanes must render");
    assert.deepEqual(await laneCounts(page), Array(9).fill("0"), "disposable database starts empty");
    assert.equal((await page.textContent("#view-note")).trim(), "Work plot · 0 cards · nine status lanes");

    // Create the first card through the real UI.
    await page.click("#new-card");
    await page.fill("#create-title-input", "Browser PG Alpha");
    await page.click("#create-submit");
    await page.waitForFunction(() => !document.getElementById("create-dialog").open);
    await page.waitForFunction(() =>
      [...document.querySelectorAll(".lane[data-status='TODO'] .card__title")].some(
        (node) => node.textContent.trim() === "Browser PG Alpha",
      ),
    );
    const alphaId = await page
      .locator('.lane[data-status="TODO"] .card[data-card-id]')
      .first()
      .getAttribute("data-card-id");
    assert.match(alphaId, /^\d+$/, `card id must be a decimal string, got ${alphaId}`);
    const afterCreate = await laneCounts(page);
    assert.equal(afterCreate[0], "1", "created card must land in TODO");
    assert.equal(afterCreate[1], "0", "created card must not land anywhere else");

    // Independent API read: inspect the transition rules before mutating.
    const alphaInspect = await apiGet(baseUrl, `/cards/${alphaId}`);
    assert.equal(alphaInspect.status, 200);
    assert.equal(alphaInspect.payload.card.status, "TODO");
    assert.deepEqual(alphaInspect.payload.depends_on, []);
    assert.deepEqual(alphaInspect.payload.open_dependencies, []);
    assert.equal(alphaInspect.payload.blocked, false, "a card with no open dependencies may enter READY");

    // Open card detail and move to a contract-valid status (no open dependencies).
    await page.locator('.lane[data-status="TODO"] .card').first().click();
    await page.waitForSelector("#detail[open] .status-strip");
    assert.equal((await page.textContent("#detail-title")).trim(), "Browser PG Alpha");
    const currentTodo = page.locator("#detail .status-strip .btn", { hasText: "TODO" });
    assert.equal(await currentTodo.getAttribute("aria-current"), "true");
    assert.equal(await currentTodo.isDisabled(), true);
    await page.locator("#detail").getByRole("button", { name: "READY", exact: true }).click();
    await page.waitForFunction(() => {
      const feedback = document.querySelector("#status-feedback");
      return feedback && feedback.textContent.includes("Moved to READY");
    });
    await page.waitForFunction(() => {
      const ready = document.querySelector('.lane[data-status="READY"]');
      return ready && ready.textContent.includes("Browser PG Alpha");
    });
    assert.deepEqual(
      await laneCounts(page),
      ["0", "1", "0", "0", "0", "0", "0", "0", "0"],
      "the UI must move the card between lanes",
    );
    await page.keyboard.press("Escape");
    await page.waitForFunction(() => !document.getElementById("detail").open);

    // Filters use the real query endpoints and clear back.
    await page.selectOption("#filter-status", "READY");
    await page.click('#filters button[type="submit"]');
    await page.waitForFunction(() =>
      document.getElementById("view-note").textContent.includes("Filtered plot · 1 of 1 cards"),
    );
    assert.deepEqual(await cardTitles(page), ["Browser PG Alpha"]);
    assert.deepEqual(await laneCounts(page), ["0", "1", "0", "0", "0", "0", "0", "0", "0"]);
    await page.click("#filter-clear");
    await page.waitForFunction(
      () => document.getElementById("view-note").textContent.trim() === "Work plot · 1 card · nine status lanes",
    );

    // Create a second card and wire a dependency through the UI.
    await page.click("#new-card");
    await page.fill("#create-title-input", "Browser PG Beta");
    await page.click("#create-submit");
    await page.waitForFunction(() => !document.getElementById("create-dialog").open);
    await page.waitForFunction(() =>
      [...document.querySelectorAll(".lane[data-status='TODO'] .card__title")].some(
        (node) => node.textContent.trim() === "Browser PG Beta",
      ),
    );
    const betaId = await page
      .locator('.lane[data-status="TODO"] .card[data-card-id]')
      .first()
      .getAttribute("data-card-id");
    assert.match(betaId, /^\d+$/);
    assert.notEqual(betaId, alphaId);
    await page.locator('.lane[data-status="TODO"] .card').first().click();
    await page.waitForSelector("#detail[open] .status-strip");
    assert.equal((await page.textContent("#detail-title")).trim(), "Browser PG Beta");
    await page.fill("#dep-input", alphaId);
    await page.locator("#detail .dep-add").getByRole("button", { name: "Add", exact: true }).click();
    await page.waitForSelector("#detail .hazard");
    const depRow = page.locator("#detail .dep-row", { hasText: "Browser PG Alpha" }).first();
    assert.match(await depRow.textContent(), /READY · OPEN/);
    assert.match(await page.textContent("#detail .hazard"), /Blocked: waiting on 1 open dependency/);

    // The contract rejects entering READY with an open dependency (real 409).
    await page.locator("#detail").getByRole("button", { name: "READY", exact: true }).click();
    await page.waitForFunction(() =>
      (document.querySelector("#status-feedback")?.textContent || "").includes("dependencies are not CLOSED"),
    );
    assert.equal(
      await page.locator("#detail .status-strip .btn", { hasText: "TODO" }).getAttribute("aria-current"),
      "true",
      "the rejected move must not change the status",
    );
    assert.deepEqual(await laneCounts(page), ["1", "1", "0", "0", "0", "0", "0", "0", "0"]);
    await page.keyboard.press("Escape");
    await page.waitForFunction(() => !document.getElementById("detail").open);

    // Persistence proof 1: independent API reads through the production WSGI app.
    const alphaAfter = await apiGet(baseUrl, `/cards/${alphaId}`);
    assert.equal(alphaAfter.status, 200);
    assert.equal(alphaAfter.payload.card.status, "READY");
    assert.equal(alphaAfter.payload.blocked, false);
    const betaAfter = await apiGet(baseUrl, `/cards/${betaId}`);
    assert.equal(betaAfter.status, 200);
    assert.equal(betaAfter.payload.card.status, "TODO");
    assert.deepEqual(betaAfter.payload.depends_on, [alphaId]);
    assert.deepEqual(betaAfter.payload.open_dependencies, [alphaId]);
    assert.equal(betaAfter.payload.blocked, true);
    const betaDeps = await apiGet(baseUrl, `/cards/${betaId}/dependencies`);
    assert.equal(betaDeps.status, 200);
    assert.deepEqual(betaDeps.payload, { items: [alphaId], count: 1 });

    // Persistence proof 2: separate psycopg read of the disposable database.
    const database = readDatabase(server.dsn);
    const rowsById = new Map(database.cards.map((card) => [card.id, card]));
    assert.deepEqual(rowsById.get(alphaId), {
      id: alphaId,
      title: "Browser PG Alpha",
      card_type: "task",
      status: "READY",
      parent_id: null,
    });
    assert.equal(rowsById.get(betaId).title, "Browser PG Beta");
    assert.equal(rowsById.get(betaId).status, "TODO");
    assert.deepEqual(database.dependencies, [
      { card_id: betaId, depends_on_card_id: alphaId },
    ]);

    // Persisted card fields: read honestly empty, then edit through the UI.
    const betaEmpty = await apiGet(baseUrl, `/cards/${betaId}`);
    assert.equal("owner" in betaEmpty.payload.card, false, "unset fields are omitted");
    await page.locator('.lane[data-status="TODO"] .card', { hasText: "Browser PG Beta" }).click();
    await page.waitForSelector("#detail[open] #fields-form");
    assert.match(await page.textContent("#detail .field-summary"), /Owner\s*Not set\./);
    await page.fill("#field-owner", "carol");
    await page.fill("#field-priority", "urgent");
    await page.fill("#field-requirements", "one\ntwo");
    await page.fill("#field-blockers", "vendor access");
    await page.click('#fields-form button[type="submit"]');
    await page.waitForFunction(() =>
      (document.querySelector("#fields-feedback")?.textContent || "").includes("Fields saved"),
    );
    const betaFilled = await apiGet(baseUrl, `/cards/${betaId}`);
    assert.equal(betaFilled.payload.card.owner, "carol");
    assert.equal(betaFilled.payload.card.priority, "urgent");
    assert.deepEqual(betaFilled.payload.card.requirements, ["one", "two"]);
    assert.deepEqual(betaFilled.payload.card.blockers, ["vendor access"]);

    // Clearing through the form removes the fields from the resource again.
    await page.fill("#field-owner", "");
    await page.fill("#field-blockers", "");
    await page.click('#fields-form button[type="submit"]');
    await page.waitForFunction(() =>
      (document.querySelector("#fields-feedback")?.textContent || "").includes("Fields saved"),
    );
    const betaCleared = await apiGet(baseUrl, `/cards/${betaId}`);
    assert.equal("owner" in betaCleared.payload.card, false);
    assert.equal("blockers" in betaCleared.payload.card, false);
    assert.equal(betaCleared.payload.card.priority, "urgent");
    assert.deepEqual(betaCleared.payload.card.requirements, ["one", "two"]);

    // Duplicate lines are refused locally; the stored fields stay untouched.
    await page.fill("#field-requirements", "one\none");
    await page.click('#fields-form button[type="submit"]');
    await page.waitForFunction(() =>
      (document.querySelector("#fields-feedback")?.textContent || "").includes("must be unique"),
    );
    const betaRefused = await apiGet(baseUrl, `/cards/${betaId}`);
    assert.deepEqual(betaRefused.payload.card.requirements, ["one", "two"]);
    await page.keyboard.press("Escape");
    await page.waitForFunction(() => !document.getElementById("detail").open);

    // The create dialog carries the same optional fields through POST.
    await page.click("#new-card");
    await page.fill("#create-title-input", "Browser PG Gamma");
    await page.fill("#create-owner", "dana");
    await page.fill("#create-priority", "low");
    await page.fill("#create-requirements", "alpha\nbeta");
    await page.fill("#create-blockers", "permit");
    await page.click("#create-submit");
    await page.waitForFunction(() => !document.getElementById("create-dialog").open);
    await page.waitForFunction(() =>
      [...document.querySelectorAll('.lane[data-status="TODO"] .card__title')].some(
        (node) => node.textContent.trim() === "Browser PG Gamma",
      ),
    );
    const gammaId = await page
      .locator('.lane[data-status="TODO"] .card', { hasText: "Browser PG Gamma" })
      .getAttribute("data-card-id");
    const gammaRead = await apiGet(baseUrl, `/cards/${gammaId}`);
    assert.equal(gammaRead.payload.card.title, "Browser PG Gamma");
    assert.equal(gammaRead.payload.card.owner, "dana");
    assert.equal(gammaRead.payload.card.priority, "low");
    assert.deepEqual(gammaRead.payload.card.requirements, ["alpha", "beta"]);
    assert.deepEqual(gammaRead.payload.card.blockers, ["permit"]);

    // Console hygiene: no JavaScript console errors or page exceptions. Chromium
    // itself logs every non-2xx response as a resource error, so each such entry
    // must map to a deliberately exercised failing request (contract-absent
    // workflow probes -> 404, the blocked transition -> 409), nothing else.
    const runtimeErrors = consoleErrors.filter((entry) => !entry.text.startsWith("Failed to load resource:"));
    assert.deepEqual(runtimeErrors, [], `unexpected console errors: ${JSON.stringify(runtimeErrors)}`);
    const failedResponses = responses.filter((response) => response.status >= 400);
    assert.ok(failedResponses.length > 0, "the flow must exercise real non-2xx responses");
    for (const entry of consoleErrors) {
      const status = Number(entry.text.match(/status of (\d+)/)?.[1]);
      assert.ok(
        failedResponses.some((response) => response.url === entry.url && response.status === status),
        `console error has no matching observed response: ${JSON.stringify(entry)}`,
      );
    }
    for (const response of failedResponses) {
      assert.ok(
        response.status === 404 || response.status === 409,
        `unexpected failing response ${response.status} ${response.url}`,
      );
      if (response.status === 404) {
        assert.match(response.url, /\/cards\/\d+\/(evidence|history)$/, `unexpected 404 at ${response.url}`);
      } else {
        assert.match(response.url, /\/cards\/\d+\/status$/, `unexpected 409 at ${response.url}`);
      }
    }
    assert.deepEqual(blocked, [], `external requests must never be needed: ${blocked.join(", ")}`);
    assert.ok(requested.length > 0, "the page must issue real requests");
    for (const url of requested) {
      assert.ok(
        url.startsWith(baseUrl) || url.startsWith("data:"),
        `request escaped the loopback origin: ${url}`,
      );
    }
  } finally {
    if (context) await context.close();
    if (browser) await browser.close();
    containerRemoved = await server.stop();
  }

  assert.equal(containerRemoved, true, `disposable container ${server.container} still exists after teardown`);
});
