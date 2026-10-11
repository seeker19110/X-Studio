/* Optional real-browser smoke. Requires Playwright in the local development environment.
 * Start `studio.console --demo` separately. Never point this at a production database.
 * STUDIO_TEST_URL, STUDIO_TEST_TOKEN, STUDIO_SCREENSHOTS_DIR are required.
 */
"use strict";
const { chromium } = require("playwright");
const fs = require("node:fs");
const path = require("node:path");
(async () => {
  const url = process.env.STUDIO_TEST_URL;
  const token = process.env.STUDIO_TEST_TOKEN;
  const output = process.env.STUDIO_SCREENSHOTS_DIR;
  if (!url || !token || !output)
    throw new Error(
      "Set STUDIO_TEST_URL, STUDIO_TEST_TOKEN and STUDIO_SCREENSHOTS_DIR",
    );
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.CHROMIUM_EXECUTABLE_PATH
      ? {
          executablePath: process.env.CHROMIUM_EXECUTABLE_PATH,
          args: [
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--no-zygote",
            "--single-process",
          ],
        }
      : {}),
  });
  try {
    const page = await browser.newPage({
      viewport: { width: 1440, height: 1000 },
    });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(url);
    await page.locator("#login-dialog input").fill(token);
    await page.locator("#login-form button").click();
    await page.waitForFunction(() =>
      document.querySelector("#connection").textContent.includes("Đã kết nối"),
    );
    const initial = await page.evaluate(() =>
      fetch("/api/state").then((r) => r.json()),
    );
    if (!initial.demo)
      throw new Error("This smoke must only run against --demo");
    for (const title of [
      "Đại dương: thế giới chưa được kể",
      "Cỗ máy học hỏi mỗi ngày",
      "Những con phố thức giấc",
    ]) {
      await page.locator("#new-project").click();
      await page.locator("#create-form [name=title]").fill(title);
      await page.locator("#create-form button[type=submit]").click();
      await page.waitForFunction(
        () => !document.querySelector("#create-dialog").open,
      );
    }
    await page.waitForFunction(
      async () => {
        const s = await fetch("/api/state").then((r) => r.json());
        return (
          s.projects.length >= 3 &&
          s.projects.every((p) => Object.keys(p.reviews).length === 3)
        );
      },
      undefined,
      { timeout: 60000 },
    );
    await page.waitForFunction(
      () => document.querySelectorAll(".project-card").length >= 3,
    );
    await page.locator("[data-view=settings]").click();
    await page.locator("#settings-form [name=threads]").fill("3");
    await page.locator("#settings-form button").click();
    await page.waitForFunction(
      async () =>
        (await fetch("/api/state").then((r) => r.json())).threads === 3,
    );
    await page.evaluate(() => refresh());
    await page.screenshot({
      path: path.join(output, "studio-settings.png"),
      fullPage: true,
    });
    await page.locator("[data-view=projects]").click();
    await page.locator("#toast").waitFor({ state: "hidden" });
    await page.screenshot({
      path: path.join(output, "studio-desktop.png"),
      fullPage: true,
    });
    await page.locator(".project-card").first().click();
    await page.screenshot({
      path: path.join(output, "studio-detail.png"),
      fullPage: true,
    });
    await page.locator("[data-close=detail-dialog]").click();
    await page.locator("[data-view=review]").click();
    await page.locator("[data-decision=hold]").first().click();
    await page.locator("[data-view=projects]").click();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({
      path: path.join(output, "studio-mobile.png"),
      fullPage: true,
    });
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth,
    );
    if (errors.length || overflow)
      throw new Error(JSON.stringify({ errors, overflow }));
    console.log(
      JSON.stringify({
        errors,
        overflow,
        projects: await page.locator(".project-card").count(),
      }),
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
