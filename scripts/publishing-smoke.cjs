/* Read-only publishing regression. Requires Playwright, STUDIO_TEST_URL and
 * STUDIO_TEST_TOKEN; all synthetic states are intercepted in this browser.
 */
"use strict";
const assert = require("node:assert/strict");
const { chromium } = require("playwright");
const path = require("node:path");
const fs = require("node:fs");

(async () => {
  const url = process.env.STUDIO_TEST_URL;
  const token = process.env.STUDIO_TEST_TOKEN;
  if (!url || !token) throw Error("Set STUDIO_TEST_URL and STUDIO_TEST_TOKEN");
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({
      viewport: { width: 1440, height: 1000 },
    });
    const errors = [],
      commands = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.route("**/api/commands", (route) => {
      commands.push(route.request().postData());
      return route.abort();
    });
    await page.goto(url + "/#publishing");
    await page.locator("#login-form [name=token]").fill(token);
    await page.locator("#login-form button").click();
    await page.waitForFunction(() =>
      document.querySelector("#connection").textContent.includes("Đã kết nối"),
    );
    const initial = await page.evaluate(() =>
      fetch("/api/state").then((r) => r.json()),
    );
    assert(
      initial.projects.length,
      "Requires a project to exercise publication transitions",
    );
    const candidates = initial.projects.filter(
      (p) =>
        p.publication ||
        p.media.some((m) => m.kind === "final_video") ||
        initial.gates.some((g) => g.kind === "publish" && g.id.endsWith(p.id)),
    );
    assert.equal(
      await page.locator(".publishing-card").count(),
      candidates.length,
    );
    if (initial.demo) {
      assert.match(
        await page.locator("#publishing-platform").innerText(),
        /mô phỏng/,
      );
      assert.equal(
        await page.locator('#publishing-view a[href*="fake.video"]').count(),
        0,
      );
    }
    await page.reload();
    await page.locator(".publishing-card").first().waitFor();
    assert.equal(
      await page.locator(".view:not(.hidden)").getAttribute("id"),
      "publishing-view",
    );
    if (!initial.demo && (await page.locator("[data-video-preview]").count())) {
      await page.locator("[data-video-preview]").first().click();
      await page.waitForFunction(() => {
        const player = document.querySelector("#detail-dialog video");
        return player && player.readyState >= 2 && player.videoWidth > 0;
      });
      const playback = await page
        .locator("#detail-dialog video")
        .evaluate(async (player) => {
          player.muted = true;
          await player.play();
          await new Promise((resolve) => setTimeout(resolve, 1000));
          player.pause();
          return {
            time: player.currentTime,
            duration: player.duration,
            error: player.error?.code,
          };
        });
      assert(playback.time > 0.5, "Actual MP4 advances during playback");
      assert(playback.duration > 0);
      assert.equal(playback.error, undefined);
      await page.locator('[data-close="detail-dialog"]').click();
    }
    await page.locator("[data-publishing-project]").first().click();
    await page.locator("#detail-dialog").waitFor({ state: "visible" });
    assert.equal(
      await page
        .locator("[data-detail-tab=review]")
        .getAttribute("aria-selected"),
      "true",
    );
    assert.equal(
      await page.locator('#detail-dialog a[href*="fake.video"]').count(),
      0,
    );
    await page.locator("[data-open-publishing]").click();
    await page.locator(".nav[data-view=dashboard]").click();
    await page.locator(".journey-step[data-view=publishing]").click();
    assert.equal(new URL(page.url()).hash, "#publishing");
    for (const width of [1920, 1440, 1280, 1024, 768, 390, 360]) {
      await page.setViewportSize({ width, height: 1000 });
      for (const view of [
        "dashboard",
        "ideas",
        "projects",
        "workflow",
        "review",
        "publishing",
        "activity",
        "settings",
      ]) {
        await page.locator(`.nav[data-view=${view}]`).click();
        const layout = await page.evaluate(() => ({
          overflow: document.documentElement.scrollWidth > innerWidth,
          active: document.querySelectorAll(".nav.active").length,
          visible: document.querySelectorAll(".view:not(.hidden)").length,
        }));
        assert(!layout.overflow, `${view} overflow at ${width}`);
        assert.equal(layout.active, 1);
        assert.equal(layout.visible, 1);
      }
    }
    await page.locator(".nav[data-view=publishing]").click();
    if (process.env.STUDIO_SCREENSHOTS_DIR) {
      const output = process.env.STUDIO_SCREENSHOTS_DIR;
      fs.mkdirSync(output, { recursive: true });
      await page.setViewportSize({ width: 1440, height: 1000 });
      await page.screenshot({
        path: path.join(output, "workflow-publishing.png"),
        fullPage: true,
      });
      await page.setViewportSize({ width: 390, height: 844 });
      await page.screenshot({
        path: path.join(output, "workflow-publishing-mobile.png"),
        fullPage: true,
      });
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
    const template = structuredClone(initial.projects[0]);
    let fixture;
    await page.route("**/api/state", (route) =>
      route.fulfill({ json: fixture }),
    );
    const show = async (project, gate = null) => {
      fixture = {
        ...initial,
        demo: false,
        platform: "youtube",
        projects: project ? [project] : [],
        gates: gate ? [gate] : [],
      };
      await page.evaluate(() => refresh());
      await page.locator(".nav[data-view=publishing]").click();
      await page.locator("[data-publishing-filter=all]").click();
    };
    const project = structuredClone(template);
    project.media = project.media.map((m) => ({ ...m, provider: "studio" }));
    project.title = '<img src=x onerror="window.unsafe=true">';
    project.metadata.title = project.title;
    project.publication = null;
    const gate = {
      id: "publish-" + project.id,
      kind: "publish",
      request_at: "2026-10-11T00:00:00Z",
      checklist: ["Kiểm tra bản cuối"],
      hold: null,
    };
    await show(project, gate);
    assert.match(
      await page.locator(".publishing-card").innerText(),
      /Chờ duyệt xuất bản/,
    );
    assert.equal(await page.locator(".publishing-card h3 img").count(), 0);
    await page.locator("[data-publishing-gate]").click();
    const input = page.locator("#gate-list input");
    assert(await input.evaluate((e) => e === document.activeElement));
    await input.fill("Cần kiểm tra phụ đề trước khi duyệt");
    await page.evaluate(() => refresh());
    assert.equal(
      await input.inputValue(),
      "Cần kiểm tra phụ đề trước khi duyệt",
    );
    project.publication = {
      ...template.publication,
      status: "scheduled",
      url: "https://www.youtube.com/watch?v=fixture-only",
    };
    await show(project);
    assert.equal(await page.locator("#publishing-scheduled").innerText(), "1");
    await page.locator("[data-publishing-filter=published]").click();
    assert.equal(await page.locator(".publishing-card").count(), 0);
    assert.equal(
      await page
        .locator("[data-publishing-filter=published]")
        .getAttribute("aria-pressed"),
      "true",
    );
    await page.locator(".nav[data-view=review]").click();
    await page.locator('#gate-list a[href="#publishing"]').click();
    assert.equal(new URL(page.url()).hash, "#publishing");
    for (const status of ["published", "failed", "rolled_back"]) {
      project.publication.status = status;
      await show(project);
      await page
        .locator(
          `[data-publishing-filter=${status === "published" ? "published" : "attention"}]`,
        )
        .click();
      assert.equal(await page.locator(".publishing-card").count(), 1);
    }
    project.publication.url = "javascript:window.unsafe=true";
    await show(project);
    assert.equal(
      await page.locator('#publishing-view a[href^="javascript:"]').count(),
      0,
    );
    project.publication.url = "https://fake.video/fixture";
    await show(project);
    assert.match(
      await page.locator(".publishing-card").innerText(),
      /Mô phỏng/,
    );
    assert.equal(
      await page.locator('#publishing-view a[href*="fake.video"]').count(),
      0,
    );
    project.publication = null;
    await show(project);
    fixture.platform = "fake";
    await page.evaluate(() => refresh());
    assert.match(
      await page.locator("#publishing-platform").innerText(),
      /BẢN DỰNG CỤC BỘ/,
    );
    assert.doesNotMatch(
      await page.locator(".publishing-card").innerText(),
      /Mô phỏng/,
    );
    assert.equal(await page.locator("[data-video-preview]").count(), 1);
    fixture.platform = "youtube";
    fixture.youtube_oauth_configured = false;
    await page.evaluate(() => refresh());
    assert.match(await page.locator("#publishing-platform").innerText(), /BẢN DỰNG CỤC BỘ/);
    assert.match(await page.locator(".publishing-card").innerText(), /Chưa kết nối YouTube/);
    await show(null);
    assert.match(
      await page.locator("#publishing-list").innerText(),
      /Chưa có video để xuất bản/,
    );
    assert.deepEqual(
      commands,
      [],
      "Viewing publication and opening a gate must not publish",
    );
    assert.deepEqual(errors, []);
    console.log(
      "Passed: publication states, filters, gate focus, reload, escaping, no publish commands, 56 responsive navigation checks.",
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
