// Optional real-browser check, separate from Node's default test discovery.
// Requires Playwright and an installed Chrome.
// Uses a disposable SQLite database, never the user's Zenith data.
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const playwrightModule = process.env.ZENITH_PLAYWRIGHT_MODULE;
const { chromium } = await import(playwrightModule ? pathToFileURL(resolve(playwrightModule)).href : "playwright");
const dataDir = await mkdtemp(join(tmpdir(), "zenith-browser-sync-"));
process.env.ZENITH_DATA_DIR = dataDir;
process.env.OLLAMA_URL = "http://127.0.0.1:1";
for (const key of ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "ZENITH_STT_COMMAND", "ZENITH_TTS_COMMAND"]) delete process.env[key];
let app;
let browser;
try {
  ({ app } = await import("../server.js"));
  const address = await new Promise((resolve, reject) => {
    app.once("error", reject);
    app.listen(0, "127.0.0.1", () => resolve(app.address()));
  });
  const url = `http://127.0.0.1:${address.port}`;
  browser = await chromium.launch({ channel: process.env.ZENITH_BROWSER_CHANNEL || "chrome", headless: true });
  const desktop = await browser.newContext({ serviceWorkers: "block" });
  const phone = await browser.newContext({ serviceWorkers: "block", viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const a = await desktop.newPage();
  const b = await phone.newPage();
  const errors = [];
  for (const page of [a, b]) { page.setDefaultTimeout(10000); page.on("pageerror", (error) => errors.push(error.message)); }
  const signIn = async (page) => {
    await page.goto(url);
    await page.locator("#displayName").fill("Sync test");
    await page.locator("#password").fill("temporary sync test passphrase");
    await page.locator("#authSubmit").click();
    await page.locator("#manager").waitFor({ state: "visible" });
    await page.waitForFunction(() => document.querySelector("#syncStatus").textContent === "Live sync connected");
  };
  const expectTitles = async (page, titles) => {
    await page.waitForFunction((expected) => {
      const actual = [...document.querySelectorAll("#taskList strong")].map((item) => item.textContent).sort();
      return JSON.stringify(actual) === JSON.stringify([...expected].sort());
    }, titles);
  };
  const capture = async (page, title) => {
    await page.locator("#captureTitle").fill(title);
    await page.locator("#captureForm button[type=submit]").click();
  };
  await signIn(a); // First launch sets up the disposable test account.
  await signIn(b); // Separate browser storage means a genuinely separate session.

  await capture(a, "Desktop capture");
  await expectTitles(a, ["Desktop capture"]);
  await expectTitles(b, ["Desktop capture"]);
  await a.locator(".task-details summary").click();
  await a.locator("#title").fill("Detailed task");
  await a.locator("#project").fill("Sync verification");
  await a.locator("#taskForm button[type=submit]").click();
  await expectTitles(a, ["Desktop capture", "Detailed task"]);
  await expectTitles(b, ["Desktop capture", "Detailed task"]);
  console.log("PASS: capture and detailed add appear on both sessions without refreshing");

  await a.locator("#captureTitle").fill("Keep my unsaved draft");
  await b.locator("#taskList li").filter({ hasText: "Desktop capture" }).locator(".edit").click();
  await b.locator("#editTitle").fill("Edited from phone");
  await b.locator("#editForm button[type=submit]").click();
  await expectTitles(a, ["Edited from phone", "Detailed task"]);
  await expectTitles(b, ["Edited from phone", "Detailed task"]);
  assert.equal(await a.locator("#captureTitle").inputValue(), "Keep my unsaved draft");
  await b.locator("#taskList li").filter({ hasText: "Edited from phone" }).locator(".toggle").check();
  await expectTitles(a, ["Detailed task"]);
  await expectTitles(b, ["Detailed task"]);
  assert.equal(await a.locator("#doneCount").textContent(), "1");
  await a.locator("#showCompleted").click();
  await expectTitles(a, ["Edited from phone", "Detailed task"]);
  await a.locator("#taskList li").filter({ hasText: "Edited from phone" }).locator(".toggle").uncheck();
  await expectTitles(b, ["Edited from phone", "Detailed task"]);
  await b.locator("#taskList li").filter({ hasText: "Edited from phone" }).locator(".delete").click();
  await expectTitles(a, ["Detailed task"]);
  await expectTitles(b, ["Detailed task"]);
  console.log("PASS: edit, complete, reopen and delete sync in both directions; drafts survive");

  await phone.setOffline(true);
  await capture(a, "While phone offline");
  await expectTitles(a, ["Detailed task", "While phone offline"]);
  await phone.setOffline(false);
  await expectTitles(b, ["Detailed task", "While phone offline"]);
  console.log("PASS: the phone catches up after reconnecting");

  // Force task reads to fail after a successful POST. The saved task must still
  // render and the input must reset, so the user does not accidentally add twice.
  const failTaskReads = async (route) => {
    if (route.request().method() === "GET") await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: "Simulated refresh failure" }) });
    else await route.continue();
  };
  await a.route("**/api/tasks", failTaskReads);
  await capture(a, "Saved with refresh unavailable");
  await expectTitles(a, ["Detailed task", "While phone offline", "Saved with refresh unavailable"]);
  await expectTitles(b, ["Detailed task", "While phone offline", "Saved with refresh unavailable"]);
  assert.equal(await a.locator("#captureTitle").inputValue(), "");
  assert.equal(await a.locator("#taskError").textContent(), "");
  await a.unroute("**/api/tasks", failTaskReads);
  console.log("PASS: a successful save renders even when the next read fails");

  // Hold an old task response until after a newer save has already rendered.
  let releaseRead;
  const heldRead = new Promise((resolve) => { releaseRead = resolve; });
  let markReadStarted;
  const readStarted = new Promise((resolve) => { markReadStarted = resolve; });
  let intercepted = false;
  const delayTaskRead = async (route) => {
    if (route.request().method() !== "GET" || intercepted) return route.continue();
    intercepted = true;
    markReadStarted();
    await heldRead;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ tasks: [] }) });
  };
  await a.route("**/api/tasks", delayTaskRead);
  const oldRead = a.evaluate(() => loadTasks());
  let readTimeout;
  try {
    await Promise.race([readStarted, new Promise((_, reject) => { readTimeout = setTimeout(() => reject(new Error("Held task read did not start")), 10000); })]);
  } finally { clearTimeout(readTimeout); }
  await capture(a, "Newer than held read");
  const finalTitles = ["Detailed task", "While phone offline", "Saved with refresh unavailable", "Newer than held read"];
  await expectTitles(a, finalTitles);
  releaseRead();
  await oldRead;
  assert.deepEqual((await a.locator("#taskList strong").allTextContents()).sort(), [...finalTitles].sort());
  await expectTitles(b, finalTitles);
  await a.unroute("**/api/tasks", delayTaskRead);
  console.log("PASS: a late old response cannot erase a newer save");
  assert.deepEqual(errors, [], "No unhandled browser errors");
  console.log("Task sync browser verification passed (desktop + phone-sized Chrome, Ollama offline).");
} finally {
  if (browser) await browser.close();
  if (app?.listening) {
    app.closeAllConnections();
    await new Promise((resolve) => app.close(resolve));
  }
  await rm(dataDir, { recursive: true, force: true });
}
