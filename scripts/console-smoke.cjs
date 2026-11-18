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
    for (const [index, title] of [
      "Đại dương: thế giới chưa được kể",
      "Cỗ máy học hỏi mỗi ngày",
      "Những con phố thức giấc",
    ].entries()) {
      await page.locator("#new-project").click();
      await page.locator("#create-form [name=title]").fill(title);
      await page.locator("#create-form button[type=submit]").click();
      await page.waitForFunction(
        () => !document.querySelector("#create-dialog").open,
      );
      if (index === 0) {
        await page.waitForFunction(
          (count) => document.querySelectorAll("#project-grid .project-card").length > count,
          initial.projects.length,
        );
        await page.locator("#project-grid .project-card").last().click();
        if (await page.locator('[data-detail-tab="review"]').count())
          await page.locator('[data-detail-tab="review"]').click();
        await page.waitForFunction(
          () => {
            const summary = document.querySelector(".review-summary");
            return summary && !/chờ kiểm tra|Chưa kiểm tra/.test(summary.textContent);
          },
          undefined,
          { timeout: 20000 },
        );
        await page.locator("[data-close=detail-dialog]").click();
      }
    }
    await page.waitForFunction(
      async (expected) => {
        const s = await fetch("/api/state").then((r) => r.json());
        return (
          s.projects.length >= expected &&
          s.projects.every((p) => Object.keys(p.reviews).length === 3)
        );
      },
      initial.projects.length + 3,
      { timeout: 60000 },
    );
    await page.waitForFunction(
      () => document.querySelectorAll("#project-grid .project-card").length >= 3,
    );
    await page.locator("nav [data-view=settings]").click();
    // Exercise admission messages and older servers without the optional field.
    const storageFixture = {
      total_bytes: 3 * 1024 ** 3,
      categories: { cache: 1024 ** 3, temp: 0, uploads: 0, outputs: 2 * 1024 ** 3, database: 0 },
      complete: true,
      limits: { soft_limit_bytes: 2 * 1024 ** 3, hard_limit_bytes: 4 * 1024 ** 3 },
      status: "normal",
    };
    for (const [storage, expected] of [
      [undefined, null],
      [storageFixture, "Dung lượng sản xuất"],
      [{ ...storageFixture, status: "warning" }, "Cần kiểm tra"],
      [{ ...storageFixture, status: "blocked" }, "Tạm ngừng nhận dự án mới"],
      [{ ...storageFixture, status: "blocked", complete: false }, "Chưa đo được đầy đủ"],
    ]) {
      await page.route("**/api/state", async (route) => {
        const response = await route.fetch();
        const updated = await response.json();
        updated.storage = storage;
        await route.fulfill({ response, json: updated });
      });
      await page.evaluate(() => refresh());
      const region = page.locator("#storage-status");
      if (expected === null) {
        if (await region.isVisible()) throw new Error("Missing storage field must hide the status");
      } else {
        if (!(await region.isVisible()) || !(await region.textContent()).includes(expected) ||
            !(await region.textContent()).includes("Đã dùng 3 GiB"))
          throw new Error(`Storage status was not rendered: ${expected}`);
        await page.setViewportSize({ width: 390, height: 844 });
        if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth))
          throw new Error("Storage status overflows on mobile");
        await page.setViewportSize({ width: 1440, height: 1000 });
      }
      await page.unroute("**/api/state");
    }
    await page.evaluate(() => refresh());
    const threads = initial.threads === 3 ? 4 : 3;
    await page.locator("#settings-form [name=threads]").fill(String(threads));
    await page.locator("#breadcrumb").click();
    await page.evaluate(() => refresh());
    if ((await page.locator("#settings-form [name=threads]").inputValue()) !== String(threads))
      throw new Error("Polling discarded the unsaved settings draft");
    await page.locator("#settings-form button").click();
    await page.waitForFunction(
      async (expected) =>
        (await fetch("/api/state").then((r) => r.json())).threads === expected,
      threads,
    );
    await page.evaluate(() => refresh());
    await page.screenshot({
      path: path.join(output, "studio-settings.png"),
      fullPage: true,
    });
    await page.locator("nav [data-view=projects]").click();
    await page.locator("#toast").waitFor({ state: "hidden" });
    await page.screenshot({
      path: path.join(output, "studio-desktop.png"),
      fullPage: true,
    });
    await page.locator("#project-grid .project-card").first().click();
    await page.screenshot({
      path: path.join(output, "studio-detail.png"),
      fullPage: true,
    });
    await page.locator("[data-close=detail-dialog]").click();
    await page.locator("nav [data-view=review]").click();
    const reason = page.locator("#gate-list input").first();
    await reason.fill("Giữ lại để kiểm tra nguồn tư liệu");
    await page.locator("#breadcrumb").click();
    await page.evaluate(() => refresh());
    if ((await reason.inputValue()) !== "Giữ lại để kiểm tra nguồn tư liệu")
      throw new Error("Polling discarded the gate decision draft");
    await reason.focus();
    await page.evaluate(() => refresh());
    if (!(await reason.evaluate((input) => document.activeElement === input)))
      throw new Error("Polling discarded focus on the gate decision draft");
    await page.route("**/api/state", async (route) => {
      const response = await route.fetch();
      const updated = await response.json();
      updated.gates[0].request_at += ":new-generation";
      updated.gates[0].checklist = ["Yêu cầu mới cần đọc lại trước khi duyệt"];
      await route.fulfill({ response, json: updated });
    });
    await page.evaluate(() => refresh());
    if ((await reason.inputValue()) !== "" ||
        !(await page.locator("#gate-list").textContent()).includes("Yêu cầu mới cần đọc lại"))
      throw new Error("A revised gate retained the previous decision draft");
    await page.unroute("**/api/state");
    await page.evaluate(() => refresh());
    await page.locator("[data-decision=hold]").first().click();
    await page.locator("nav [data-view=projects]").click();
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
        projects: await page.locator("#project-grid .project-card").count(),
      }),
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
