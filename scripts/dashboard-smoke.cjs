/* Dashboard journey smoke. Development-only Playwright; use a dedicated --demo DB.
 * Requires STUDIO_TEST_URL, STUDIO_TEST_TOKEN, STUDIO_SCREENSHOTS_DIR.
 */
"use strict";
const assert = require("node:assert/strict");
const { chromium } = require("playwright");
const fs = require("node:fs");
const path = require("node:path");

(async () => {
  const url = process.env.STUDIO_TEST_URL;
  const token = process.env.STUDIO_TEST_TOKEN;
  const output = process.env.STUDIO_SCREENSHOTS_DIR;
  if (!url || !token || !output)
    throw Error(
      "Set STUDIO_TEST_URL, STUDIO_TEST_TOKEN, STUDIO_SCREENSHOTS_DIR",
    );
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.CHROMIUM_EXECUTABLE_PATH
      ? { executablePath: process.env.CHROMIUM_EXECUTABLE_PATH }
      : {}),
  });
  const errors = [];
  try {
    const page = await browser.newPage({
      viewport: { width: 1440, height: 1080 },
    });
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(url);
    await page.locator("#login-dialog").waitFor({ state: "visible" });
    await page.locator("#login-form [name=token]").fill(token);
    await page.locator("#login-form button").click();
    await page.waitForFunction(() =>
      document.querySelector("#connection").textContent.includes("Đã kết nối"),
    );
    const snapshot = () =>
      page.evaluate(() => fetch("/api/state").then((r) => r.json()));
    const initial = await snapshot();
    assert.equal(initial.demo, true, "Smoke must only run against --demo");
    const initialCount = initial.projects.length;
    const waitState = async (predicate) => {
      const deadline = Date.now() + 60000;
      while (Date.now() < deadline) {
        const data = await snapshot();
        if (predicate(data)) return data;
        await page.waitForTimeout(250);
      }
      throw Error("Timed out waiting for production state");
    };
    const nav = async (view) => page.locator(`.nav[data-view=${view}]`).click();
    const title = "Một phút: vì sao chúng ta trì hoãn?";
    await nav("ideas");
    await page.locator("#new-idea").click();
    await page.locator("#idea-form [name=title]").fill(title);
    await page
      .locator("#idea-form [name=audience]")
      .fill("Người muốn cải thiện thói quen");
    await page
      .locator("#idea-form [name=hook]")
      .fill("Bạn đang trì hoãn chính điều mình muốn làm?");
    await page.locator("#idea-form [name=format]").selectOption("short");
    await page.locator("#idea-form button[type=submit]").click();
    await page.reload();
    await page.waitForFunction(() =>
      document.querySelector("#connection").textContent.includes("Đã kết nối"),
    );
    await nav("ideas");
    assert.equal(
      await page.locator(".idea-card h3").first().textContent(),
      title,
      "Idea survives page reload",
    );
    await page.locator("[data-convert]").first().click();
    assert.equal(
      await page.locator("#create-form [name=format]").inputValue(),
      "short",
    );
    assert.equal(
      await page.locator("#create-form [name=target_minutes]").inputValue(),
      "0.8",
    );
    await page
      .locator("#create-form [name=message]")
      .fill("Bắt đầu bằng một hành động nhỏ.");
    if (!(await page.locator("#create-form [name=language]").isVisible()))
      await page.locator("#create-form details summary").click();
    await page.locator("#create-form [name=language]").selectOption("en");
    await page.locator("#create-form button[type=submit]").click();
    await page.locator("#create-dialog").waitFor({ state: "hidden" });
    const convertedCommand = await page.evaluate(
      () => JSON.parse(localStorage.getItem("xstudio.ideas.v1"))[0].command_id,
    );
    const channel =
      "P" + convertedCommand.replace(/[^a-zA-Z0-9]/g, "").slice(0, 24);
    const data = await waitState((s) =>
      s.projects.some(
        (p) =>
          p.brief?.channel_id === channel &&
          Object.keys(p.reviews).length === 3,
      ),
    );
    const short = data.projects.find((p) => p.brief?.channel_id === channel);
    assert.equal(short.format, "short");
    assert.equal(short.minutes, 0.8);
    assert.equal(short.metadata.language, "en");
    assert.equal(short.brief.audience, "Người muốn cải thiện thói quen");
    assert(
      short.brief.boundaries.some((b) =>
        b.includes("Bắt đầu bằng một hành động nhỏ."),
      ),
    );
    assert(
      short.brief.boundaries.some((b) =>
        b.includes("Bạn đang trì hoãn chính điều mình muốn làm?"),
      ),
    );
    assert(
      short.request.boundaries.some((b) =>
        b.includes("Bắt đầu bằng một hành động nhỏ."),
      ),
    );
    await page.evaluate(() => refresh());
    await nav("ideas");
    assert.equal(
      await page.locator("[data-convert]").count(),
      0,
      "Converted idea must not launch a duplicate project",
    );
    assert.equal(await page.locator(".idea-card [data-project]").count(), 1);
    // Content is treated as text even in idea titles and project brief fields.
    await page.locator("#new-idea").click();
    const literal = '<img src=x onerror="window.dashboardInjected=true">';
    await page.locator("#idea-form [name=title]").fill(literal);
    await page.locator("#idea-form button[type=submit]").click();
    assert.equal(
      await page.locator(".idea-card h3").first().textContent(),
      literal,
    );
    assert.equal(
      await page.evaluate(() => window.dashboardInjected),
      undefined,
    );
    await page.locator("[data-delete-idea]").first().click();
    await page.locator(".toast-undo").click();
    assert.equal(
      await page.locator(".idea-card h3").first().textContent(),
      literal,
      "Undo restores deleted idea",
    );
    await page.locator("[data-delete-idea]").first().click();
    // A command can be accepted while its subsequent plan is rejected.
    await page.locator("#new-idea").click();
    await page
      .locator("#idea-form [name=title]")
      .fill("Kế hoạch thiếu ngân sách");
    await page.locator("#idea-form button[type=submit]").click();
    await page.locator("[data-convert]").first().click();
    if (!(await page.locator("#create-form [name=budget_tokens]").isVisible()))
      await page.locator("#create-form details summary").click();
    await page.locator("#create-form [name=budget_tokens]").fill("1000");
    await page.locator("#create-form button[type=submit]").click();
    await page.locator("#create-dialog").waitFor({ state: "hidden" });
    const rejectedCommand = await page.evaluate(
      () => JSON.parse(localStorage.getItem("xstudio.ideas.v1"))[0].command_id,
    );
    await waitState((s) =>
      s.commands.some((c) => c.id === rejectedCommand && c.planning_error),
    );
    await page.evaluate(() => refresh());
    await nav("ideas");
    assert.equal(
      await page.locator("[data-convert]").count(),
      1,
      "Rejected planning restores the idea's retry action",
    );
    assert.equal(
      await page.evaluate(
        () =>
          JSON.parse(localStorage.getItem("xstudio.ideas.v1"))[0].command_id,
      ),
      null,
    );
    assert(
      !(await page.locator(".idea-card").first().textContent()).includes(
        "Chờ xưởng tạo dự án",
      ),
    );
    await page.locator("[data-delete-idea]").first().click();
    await page.locator("#new-project").click();
    await page
      .locator("#create-form [name=title]")
      .fill("Học cách tập trung trong thế giới nhiều xao nhãng");
    assert.equal(
      await page.locator("#create-form [name=format]").inputValue(),
      "long",
    );
    await page.locator("#create-form button[type=submit]").click();
    await page.locator("#create-dialog").waitFor({ state: "hidden" });
    await waitState(
      (s) =>
        s.projects.length >= initialCount + 2 &&
        s.projects.every((p) => Object.keys(p.reviews).length === 3),
    );
    await page.evaluate(() => refresh());
    await nav("projects");
    const count = (await snapshot()).projects.length;
    assert.equal(
      await page.locator("#project-grid .project-card").count(),
      count,
    );
    await page.locator("[data-layout=board]").click();
    assert.equal(await page.locator(".board-card").count(), count);
    await page.locator("#search").fill("Một phút");
    assert.equal(
      await page.locator(".board-card").count(),
      (await snapshot()).projects.filter((p) => p.title.includes("Một phút"))
        .length,
    );
    await page.locator("#search").fill("không-có-dự-án-này");
    assert.equal(await page.locator(".board-card").count(), 0);
    await page.locator("#search").fill("");
    await page.locator("[data-layout=grid]").click();
    await page
      .locator(`#project-grid .project-card[data-project="${short.id}"]`)
      .click();
    await page.locator("[data-detail-tab=script]").click();
    assert(
      (await page.locator("#detail-body").textContent()).includes(
        short.script.hook,
      ),
    );
    await page.locator("[data-detail-tab=script]").press("ArrowRight");
    assert.equal(
      await page
        .locator("[data-detail-tab=scenes]")
        .getAttribute("aria-selected"),
      "true",
    );
    await page.locator("[data-detail-tab=scenes]").press("ArrowLeft");
    await page.screenshot({
      path: path.join(output, "workflow-script.png"),
      fullPage: true,
    });
    await page.locator("[data-detail-tab=scenes]").click();
    assert.equal(await page.locator(".scene").count(), short.scenes.length);
    await page.locator("[data-detail-tab=review]").click();
    assert.equal(await page.locator(".review-summary article").count(), 3);
    await page.locator("[data-detail-tab=analytics]").click();
    assert(
      (await page.locator("#detail-body").textContent()).includes(
        "Chưa đồng bộ số liệu",
      ),
    );
    await page.locator("[data-close=detail-dialog]").click();
    await nav("review");
    const reason = page.locator("#gate-list input").first();
    await reason.fill("Kiểm tra lời đọc trước khi đăng");
    await page.waitForTimeout(3300);
    assert.equal(
      await reason.inputValue(),
      "Kiểm tra lời đọc trước khi đăng",
      "Polling preserves typed gate reason",
    );
    // Simulate a newer generation arriving while the old checklist stays focused.
    const commandsBefore = (await snapshot()).commands.length;
    await page.evaluate(() => {
      state.gates[0].request_at = "2099-01-01T00:00:00+00:00";
    });
    await page.locator("[data-decision=approve]").first().click();
    assert.equal(
      (await snapshot()).commands.length,
      commandsBefore,
      "Old displayed checklist must not approve a newer gate",
    );
    assert(
      (await page.locator("#toast").textContent()).includes(
        "Checklist đã thay đổi",
      ),
    );
    await page.evaluate(() => refresh());
    const pendingBefore = (await snapshot()).gates.length;
    await page.locator("[data-decision=hold]").first().click();
    assert.equal(
      (await snapshot()).gates.length,
      pendingBefore,
      "Deferral keeps approval pending",
    );
    await nav("settings");
    const newThreads = initial.threads === 3 ? 4 : 3;
    await page
      .locator("#settings-form [name=threads]")
      .fill(String(newThreads));
    await page.locator("#breadcrumb").click();
    await page.evaluate(() => refresh());
    assert.equal(
      await page.locator("#settings-form [name=threads]").inputValue(),
      String(newThreads),
      "Unsaved settings survive polling after blur",
    );
    await page.route("**/api/commands", async (route) => {
      assert.equal(route.request().postDataJSON().kind, "settings");
      await route.fulfill({ json: { id: "delayed-settings" } });
    });
    await page.locator("#settings-form button[type=submit]").click();
    await page.waitForFunction(
      () =>
        !document.querySelector("#settings-form button[type=submit]").disabled,
    );
    await page.locator("#breadcrumb").click();
    await page.evaluate(() => refresh());
    assert.equal(
      await page.locator("#settings-form [name=threads]").inputValue(),
      String(newThreads),
      "Accepted settings survive polling before the worker applies them",
    );
    await page.unroute("**/api/commands");
    await page.locator("#settings-form button[type=submit]").click();
    await waitState((s) => s.threads === newThreads);
    await page.locator("#breadcrumb").click();
    await page.evaluate(() => refresh());
    assert.equal(
      await page.locator("#settings-form [name=threads]").inputValue(),
      String(newThreads),
    );
    await nav("workflow");
    assert.equal(await page.locator(".workflow-step").count(), 8);
    await page.locator("[data-guide-step='2']").first().click();
    await page.locator("[data-guide-format=short]").click();
    assert(
      (await page.locator(".format-note").textContent()).includes("Video ngắn"),
    );
    await page.locator("#toast").waitFor({ state: "hidden" });
    await page.screenshot({
      path: path.join(output, "workflow-playbook.png"),
      fullPage: true,
    });
    await nav("dashboard");
    assert.equal(await page.locator(".journey-step").count(), 8);
    for (const label of await page
      .locator(".journey-step strong")
      .allTextContents())
      assert(label.length < 30, "Stage labels must stay concise");
    await page.locator("#toast").waitFor({ state: "hidden" });
    await page.screenshot({
      path: path.join(output, "workflow-desktop.png"),
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    for (const target of [
      "dashboard",
      "ideas",
      "projects",
      "workflow",
      "review",
      "settings",
    ]) {
      await nav(target);
      assert.equal(
        await page.evaluate(
          () => document.documentElement.scrollWidth > innerWidth,
        ),
        false,
        `No horizontal page overflow: ${target}`,
      );
    }
    await nav("dashboard");
    await page.screenshot({
      path: path.join(output, "workflow-mobile.png"),
      fullPage: true,
    });
    await page.locator("#new-project").click();
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      ),
      false,
      "Mobile dialog must not overflow",
    );
    await page.locator("[data-close=create-dialog]").first().click();
    assert.deepEqual(errors, [], "No browser JavaScript errors");
    console.log(
      JSON.stringify({
        errors,
        projects: count,
        checks:
          "ideas persistence/conversion/undo/rejected-plan retry, creator brief/language, long+short production, Kanban/search, script/scenes/QC, gate generation, settings drafts, playbook, six mobile views",
      }),
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e.stack);
  process.exitCode = 1;
});
