import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { staticDir } from "./helpers.mjs";

const read = (name) => fs.readFileSync(path.join(staticDir, name), "utf8");

function parseRootVariables(css) {
  const rootMatch = css.match(/:root\s*\{([\s\S]*?)\}/);
  assert.ok(rootMatch, "app.css must define :root tokens");
  const variables = {};
  for (const match of rootMatch[1].matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) {
    variables[match[1]] = match[2].trim();
  }
  return variables;
}

function hexToRgb(hex) {
  const normalized = hex.replace("#", "");
  const full = normalized.length === 3 ? normalized.split("").map((c) => c + c).join("") : normalized;
  return [0, 2, 4].map((offset) => parseInt(full.slice(offset, offset + 2), 16) / 255);
}

function luminance(hex) {
  const [r, g, b] = hexToRgb(hex).map((channel) =>
    channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4,
  );
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a, b) {
  const [lighter, darker] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (lighter + 0.05) / (darker + 0.05);
}

const CONTRAST_PAIRS = [
  ["--ink", "--paper", 4.5],
  ["--ink-soft", "--paper", 4.5],
  ["--blue", "--paper", 4.5],
  ["--blue-deep", "--paper", 4.5],
  ["--magenta", "--paper", 4.5],
  ["--green", "--paper", 4.5],
  ["--paper", "--ink", 4.5],
  ["--paper", "--blue-deep", 4.5],
  ["--paper", "--magenta", 4.5],
  ["--ink", "--paper-deep", 4.5],
  ["--ink-soft", "--paper-deep", 4.5],
  ["--ink", "--sea", 4.5],
  ["--rule-strong", "--paper", 3.0],
  ["--rule-strong", "--paper-deep", 3.0],
  ["--blue", "--paper", 3.0],
  ["--magenta", "--paper", 3.0],
  ["--green", "--paper-deep", 3.0],
  ["--ink", "--paper", 3.0],
];

test("palette tokens meet WCAG contrast for text and non-text roles", () => {
  const variables = parseRootVariables(read("app.css"));
  for (const [foreground, background, threshold] of CONTRAST_PAIRS) {
    assert.ok(variables[foreground], `missing token ${foreground}`);
    assert.ok(variables[background], `missing token ${background}`);
    const ratio = contrast(variables[foreground], variables[background]);
    assert.ok(
      ratio >= threshold,
      `${foreground} on ${background} is ${ratio.toFixed(2)}:1, below ${threshold}:1`,
    );
  }
});

test("index.html carries semantic scaffolding and the nine canonical statuses", () => {
  const html = read("index.html");
  assert.match(html, /<html lang="en">/);
  assert.match(html, /name="viewport"/);
  assert.match(html, /<script type="module" src="app\.js">/);
  const optionValues = [...html.matchAll(/<option value="(TODO|READY|IN_PROGRESS|REVIEW|CHANGES_REQUESTED|APPROVED|MERGED|CLOSED|BLOCKED)">/g)].map(
    (match) => match[1],
  );
  assert.deepEqual(optionValues, [
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
  assert.match(html, /aria-live="polite"/);
  assert.match(html, /aria-live="assertive"/);
  assert.match(html, /<dialog class="sheet" id="detail"/);
});

test("no external resources or remote endpoints in the shipped static files", () => {
  for (const name of ["index.html", "app.css", "app.js", "lib.js"]) {
    const content = read(name);
    const external = content
      .replaceAll("http://www.w3.org/2000/svg", "")
      .match(/https?:\/\//g);
    assert.equal(external, null, `${name} references an external URL`);
  }
});

test("no inline event handlers, innerHTML, or removed focus outlines", () => {
  const html = read("index.html");
  const css = read("app.css");
  const app = read("app.js");
  assert.doesNotMatch(html, /\son[a-z]+=/i);
  assert.doesNotMatch(app, /innerHTML/);
  assert.doesNotMatch(css, /outline:\s*(none|0)/);
  assert.match(css, /:focus-visible/);
  assert.match(css, /prefers-reduced-motion/);
  assert.match(css, /forced-colors/);
});

test("lane navigation scaffolding covers all nine lanes on both viewports", () => {
  const html = read("index.html");
  assert.match(html, /<nav class="plot-nav" aria-label="Status lanes">/);
  assert.match(html, /id="lane-scale"/);
  assert.match(html, /id="lane-range"/);
  assert.match(html, /id="board-hint"/);
  assert.match(html, /aria-describedby="board-hint"/);
  assert.match(html, /id="lane-step-prev"/);
  assert.match(html, /id="lane-step-next"/);
  assert.match(html, /<use href="#i-forward"\/>/);
  const app = read("app.js");
  assert.match(app, /Show lane \$\{index \+ 1\} of \$\{STATUSES\.length\}/);
  const css = read("app.css");
  assert.match(css, /\.plot-nav__tick\[data-inview="true"\]/);
  assert.match(css, /\.plot-nav__step\[hidden\]/);
});

test("workflow records absent from the contract are labeled unavailable, never invented", () => {
  const app = read("app.js");
  assert.match(app, /Not available: the Kanban V1 API contract defines no/);
  const mock = fs.readFileSync(path.join(staticDir, "..", "tests", "mock_server.py"), "utf8");
  assert.match(mock, /synthetic/);
});
