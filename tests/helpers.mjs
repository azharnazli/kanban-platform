import { spawn, execSync } from "node:child_process";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import fs from "node:fs";
import path from "node:path";

export const testsDir = path.dirname(fileURLToPath(import.meta.url));
export const repoRoot = path.resolve(testsDir, "..");
export const staticDir = path.join(repoRoot, "static");

export function startMockServer({ workflowMocks = false } = {}) {
  return new Promise((resolve, reject) => {
    const args = [path.join(testsDir, "mock_server.py"), "--port", "0"];
    if (workflowMocks) args.push("--workflow-mocks");
    const child = spawn("python3", args, { stdio: ["ignore", "pipe", "pipe"] });
    let buffer = "";
    let settled = false;
    const timer = setTimeout(() => {
      if (!settled) {
        settled = true;
        child.kill("SIGKILL");
        reject(new Error("mock server did not start in time"));
      }
    }, 15000);
    child.stdout.on("data", (chunk) => {
      buffer += chunk.toString();
      const match = buffer.match(/PORT=(\d+)/);
      if (match && !settled) {
        settled = true;
        clearTimeout(timer);
        const port = Number(match[1]);
        const baseUrl = `http://127.0.0.1:${port}`;
        resolve({
          port,
          baseUrl,
          async scenario(body) {
            const response = await fetch(`${baseUrl}/__mock/scenario`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(body),
            });
            if (!response.ok) throw new Error(`scenario switch failed: ${response.status}`);
            return response.json();
          },
          async reset() {
            await fetch(`${baseUrl}/__mock/reset`, { method: "POST" });
          },
          stop() {
            return new Promise((done) => {
              child.once("exit", () => done());
              child.kill();
              const force = setTimeout(() => {
                child.kill("SIGKILL");
                done();
              }, 3000);
              force.unref();
            });
          },
        });
      }
    });
    child.stderr.on("data", () => {});
    child.once("exit", (code) => {
      if (!settled && code !== null) {
        settled = true;
        clearTimeout(timer);
        reject(new Error(`mock server exited early (${code}): ${buffer}`));
      }
    });
  });
}

export async function loadPlaywright() {
  const require = createRequire(import.meta.url);
  const candidates = [];
  if (process.env.PLAYWRIGHT_CORE) candidates.push(process.env.PLAYWRIGHT_CORE);
  candidates.push("playwright-core");
  try {
    const globalRoot = execSync("npm root -g", { encoding: "utf8" }).trim();
    if (globalRoot) {
      candidates.push(path.join(globalRoot, "agent-browser", "node_modules", "playwright-core"));
    }
  } catch {
    // npm unavailable; keep the remaining candidates
  }
  for (const candidate of candidates) {
    try {
      const resolved = require(candidate);
      if (resolved && resolved.chromium) return resolved;
    } catch {
      continue;
    }
  }
  return null;
}

export function chromiumExecutable() {
  const cacheRoot = path.join(process.env.HOME || "", ".cache", "ms-playwright");
  if (fs.existsSync(cacheRoot)) {
    const entries = fs.readdirSync(cacheRoot).sort();
    for (const entry of entries) {
      if (!entry.startsWith("chromium")) continue;
      for (const binary of ["chrome", "headless_shell"]) {
        const candidate = path.join(cacheRoot, entry, "chrome-linux", binary);
        if (fs.existsSync(candidate)) return candidate;
      }
    }
  }
  for (const system of ["/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"]) {
    if (fs.existsSync(system)) return system;
  }
  return null;
}

export async function launchBrowser(chromium) {
  try {
    return await chromium.launch({ headless: true, args: ["--no-sandbox"] });
  } catch {
    const executablePath = chromiumExecutable();
    if (!executablePath) throw new Error("no chromium executable available for playwright");
    return await chromium.launch({ headless: true, executablePath, args: ["--no-sandbox"] });
  }
}

const LOCAL_HOSTS = new Set(["127.0.0.1", "localhost", "::1", "[::1]"]);

export async function blockExternalRequests(context, onBlocked) {
  await context.route("**/*", (route) => {
    const url = route.request().url();
    const scheme = url.slice(0, url.indexOf(":")).toLowerCase();
    if (scheme === "data" || scheme === "blob" || scheme === "about") return route.continue();
    let host = "";
    try {
      host = new URL(url).hostname;
    } catch {
      host = "";
    }
    if (LOCAL_HOSTS.has(host)) return route.continue();
    if (onBlocked) onBlocked(url);
    return route.abort("blockedbyclient");
  });
}

export async function waitForBoard(page) {
  await page.waitForFunction(() => {
    const board = document.getElementById("board");
    if (!board || board.getAttribute("aria-busy") !== "false") return false;
    if (document.querySelector(".skeleton__row")) return false;
    return document.querySelectorAll(".lane").length === 9;
  });
}

export async function laneHeadings(page) {
  return page.$$eval(".lane__head h2", (nodes) => nodes.map((node) => node.childNodes[0].textContent.trim()));
}

export async function laneCounts(page) {
  return page.$$eval(".lane__count", (nodes) => nodes.map((node) => node.textContent.trim()));
}

export async function cardTitles(page) {
  return page.$$eval(".card__title", (nodes) => nodes.map((node) => node.textContent.trim()));
}

export async function activeCardTitle(page) {
  return page.evaluate(() => {
    const active = document.activeElement;
    if (!active || !active.classList.contains("card")) return null;
    return active.querySelector(".card__title").textContent.trim();
  });
}
