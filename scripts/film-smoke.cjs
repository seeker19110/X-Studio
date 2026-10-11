/* Isolated film/animation UI smoke. Uses local fixtures; no provider, API key or upload.
 * Run with NODE_PATH pointing to Playwright, optionally CHROMIUM_EXECUTABLE_PATH.
 */
"use strict";
const assert = require("node:assert/strict");
const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

const literal = '<img src=x onerror="window.filmInjected=true">';
const cinema = {
  production_type: "animation", visual_mode: "generated_video",
  visual_style: "2D, watercolor", logline: literal,
  character_bible: "Mây: áo vàng, tóc ngắn; luôn giữ thiết kế giữa các cảnh.",
  negative_prompt: "flicker", seed: 42,
};
const fixture = {
  demo: false, running: false, automation: "manual", threads: 2,
  render_mode: "bounded", style: "cinematic", burn_captions: true,
  providers: { tts: "fake", image: "fake", video: "ffmpeg" },
  scene_video_provider: "none", scene_video_ready: false, platform: "fake",
  queue: 0, deferred: 0, gates: [], events: [], commands: [],
  projects: [{
    id: "film-ui-fixture", title: "Mây và chiếc đèn", format: "long", minutes: 1,
    state: "in_production", updated_at: "2026-10-11T00:00:00Z",
    request: { audience: "Gia đình", budget_tokens: 150000, boundaries: [], ...cinema },
    cinema, reviews: {},
    scenes: [{ id: "S01", duration_s: 5, narration: "Mây mở chiếc đèn.", visual_prompt: literal }],
    media: [{ kind: "scene_video", scene_id: "S01", provider: "comfyui", url: "/media/scene.mp4",
      provenance: { generated_by: "ComfyUI", license: "Workflow license", attribution: "Local" } }],
  }],
};
const commands = [];
const web = path.resolve(__dirname, "../src/studio/web");
const server = http.createServer(async (req, res) => {
  if (req.url === "/api/state") {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify(fixture));
    return;
  }
  if (req.url === "/api/commands") {
    let body = "";
    for await (const chunk of req) body += chunk;
    commands.push(JSON.parse(body));
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ id: "fixture-command-" + commands.length }));
    return;
  }
  const names = { "/": "index.html", "/app.js": "app.js", "/style.css": "style.css" };
  const file = names[req.url];
  if (!file) { res.writeHead(404); res.end(); return; }
  const types = { "index.html": "text/html", "app.js": "application/javascript", "style.css": "text/css" };
  res.writeHead(200, { "Content-Type": types[file] });
  res.end(fs.readFileSync(path.join(web, file)));
});

(async () => {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const browser = await chromium.launch({ headless: true,
    ...(process.env.CHROMIUM_EXECUTABLE_PATH ? { executablePath: process.env.CHROMIUM_EXECUTABLE_PATH } : {}),
  });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1080 } });
    page.setDefaultTimeout(5000);
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto("http://127.0.0.1:" + server.address().port);
    await page.waitForFunction(() => document.querySelector("#connection").textContent.includes("Đã kết nối"));
    const field = (name) => page.locator(`#create-form [name="${name}"]`);
    await page.locator("#new-project").click();
    assert.equal(await field("production_type").inputValue(), "explainer");
    assert.equal(await field("visual_mode").inputValue(), "images");
    assert.equal(await field("visual_mode").locator("option[value=generated_video]").evaluate((option) => option.disabled), true);
    await field("production_type").selectOption("animation");
    assert.equal(await field("goal").inputValue(), "entertainment", "Narrative content defaults to entertainment");
    assert.equal(await field("audience").inputValue(), "Khán giả yêu thích câu chuyện");
    assert.equal(await field("format").inputValue(), "long", "Story type must not force aspect ratio");
    await field("logline").fill("Mây đi tìm ngọn đèn cuối cùng.");
    await field("character_bible").fill("Mây: áo vàng, tóc ngắn.");
    await page.locator('[data-style-preset="2d"]').click();
    await field("visual_style").fill("2D, watercolor");
    await field("seed").fill("42");
    await field("negative_prompt").fill("flicker");
    // Background state updates must preserve a creator's draft.
    await page.evaluate(() => refresh());
    assert.equal(await field("visual_style").inputValue(), "2D, watercolor");
    await field("production_type").selectOption("explainer");
    assert.equal(await field("logline").isVisible(), false);
    assert.equal(await field("logline").isDisabled(), true);
    await field("title").fill("Video kiến thức");
    await page.locator("#create-form [type=submit]").click();
    await page.locator("#create-dialog").waitFor({ state: "hidden" });
    assert.equal(commands[0].data.production_type, "explainer");
    assert.equal(commands[0].data.visual_mode, "images");
    assert.equal(commands[0].data.logline, "", "Hidden cinema draft must not leak into an explainer");
    fixture.scene_video_provider = "fake";
    fixture.scene_video_ready = true;
    await page.evaluate(() => refresh());
    await page.locator("#new-project").click();
    await field("audience").fill("Gia đình có trẻ nhỏ");
    await field("production_type").selectOption("film");
    assert.equal(await field("goal").inputValue(), "entertainment");
    assert.equal(await field("audience").inputValue(), "Gia đình có trẻ nhỏ", "A chosen audience survives content type changes");
    await field("goal").selectOption("education");
    await page.evaluate(() => refresh());
    assert.equal(await field("goal").inputValue(), "education", "Polling must not replace a chosen goal");
    await field("goal").selectOption("inspiration");
    await field("production_type").selectOption("animation");
    assert.equal(await field("goal").inputValue(), "inspiration", "A chosen goal survives content type changes");
    await field("production_type").selectOption("film");
    assert.equal(await field("visual_mode").inputValue(), "images", "Selecting film must not opt into fake generation");
    assert((await page.locator("#scene-video-note").textContent()).includes("Mô phỏng"));
    await field("visual_mode").selectOption("generated_video");
    await field("title").fill("Phim thử nghiệm");
    await field("logline").fill("Mây đi tìm ngọn đèn cuối cùng.");
    await field("character_bible").fill("Mây: áo vàng, tóc ngắn.");
    await field("visual_style").fill("2D, watercolor");
    await field("seed").fill("42");
    await field("negative_prompt").fill("flicker");
    // A provider removed while this draft is open must not silently change its mode.
    fixture.scene_video_provider = "none";
    fixture.scene_video_ready = false;
    await page.evaluate(() => refresh());
    await page.locator("#create-form [type=submit]").click();
    assert.equal(commands.length, 1);
    assert((await page.locator("#create-error").textContent()).includes("Chưa cấu hình"));
    assert.equal(await field("visual_mode").inputValue(), "generated_video");
    fixture.scene_video_provider = "fake";
    fixture.scene_video_ready = true;
    await page.evaluate(() => refresh());
    await page.locator("#create-form [type=submit]").click();
    await page.locator("#create-dialog").waitFor({ state: "hidden" });
    assert.equal(commands[1].data.production_type, "film");
    assert.equal(commands[1].data.visual_mode, "generated_video");
    assert.equal(commands[1].data.seed, 42);
    assert.equal(commands[1].data.visual_style, "2D, watercolor");
    assert.equal(commands[1].data.character_bible, "Mây: áo vàng, tóc ngắn.");
    fixture.scene_video_provider = "comfyui";
    await page.evaluate(() => refresh());
    await page.locator("#new-project").click();
    assert((await page.locator("#scene-video-note").textContent()).includes("Đã cấu hình"));
    assert.equal(await field("visual_mode").inputValue(), "images");
    await page.locator("[data-close=create-dialog]").first().click();
    await page.locator('.nav[data-view="projects"]').click();
    await page.locator('#project-grid [data-project="film-ui-fixture"]').click();
    assert((await page.locator("#detail-body").textContent()).includes(cinema.character_bible));
    assert((await page.locator("#detail-body").textContent()).includes(literal));
    assert.equal(await page.evaluate(() => window.filmInjected), undefined);
    await page.locator("[data-detail-tab=scenes]").click();
    assert.equal(await page.locator(".storyboard-prompt p").textContent(), literal);
    assert.equal(await page.locator(".scene video").count(), 1);
    assert.equal(await page.locator('.scene a[download]').getAttribute("href"), "/media/scene.mp4");
    fixture.projects[0].media[0].provider = "fake";
    await page.evaluate(() => refresh());
    assert.equal(await page.locator(".scene video").count(), 0);
    assert((await page.locator(".scene-placeholder").textContent()).includes("mô phỏng"));
    // A newer replacement image supersedes an older clip and its rights evidence.
    fixture.projects[0].media[0].provider = "comfyui";
    fixture.projects[0].media.push({
      kind: "scene_image", scene_id: "S01", provider: "local", url: "/media/replacement.png",
      provenance: { generated_by: "Replacement artwork", license: "Replacement image license", attribution: "New artwork" },
    });
    await page.evaluate(() => refresh());
    assert.equal(await page.locator(".scene video").count(), 0, "A replacement image hides the older clip");
    assert.equal(await page.locator(".scene a[download]").count(), 0, "An older clip must not remain downloadable");
    assert.equal(await page.locator(".scene img").getAttribute("src"), "/media/replacement.png");
    assert(!(await page.locator(".asset-provenance").textContent()).includes("Workflow license"));
    assert((await page.locator(".asset-provenance").textContent()).includes("Replacement image license"));
    await page.locator("[data-close=detail-dialog]").click();
    // Existing idea conversion still fills its format and brief.
    await page.locator('.nav[data-view="ideas"]').click();
    await page.locator("#new-idea").click();
    await page.locator('#idea-form [name=title]').fill("Câu chuyện ngắn");
    await page.locator('#idea-form [name=format]').selectOption("short");
    await page.locator('#idea-form [type=submit]').click();
    await page.locator("[data-convert]").click();
    assert.equal(await field("format").inputValue(), "short");
    assert.equal(await field("production_type").inputValue(), "explainer");
    assert.equal(await field("title").inputValue(), "Câu chuyện ngắn");
    await field("production_type").selectOption("animation");
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    assert.equal(await page.evaluate(() => { const d = document.querySelector('#create-dialog'); return d.scrollWidth > d.clientWidth; }), false);
    if (process.env.STUDIO_SCREENSHOTS_DIR) {
      fs.mkdirSync(process.env.STUDIO_SCREENSHOTS_DIR, { recursive: true });
      await page.screenshot({ path: path.join(process.env.STUDIO_SCREENSHOTS_DIR, "film-create-mobile.png"), fullPage: true });
      await page.setViewportSize({ width: 1440, height: 1080 });
      await page.screenshot({ path: path.join(process.env.STUDIO_SCREENSHOTS_DIR, "film-create-desktop.png"), fullPage: true });
    }
    assert.deepEqual(errors, [], "No browser JavaScript errors");
    console.log(JSON.stringify({ checks: "film/animation brief, independent aspect ratio, provider states, draft preservation, payload, storyboard escaping, clip preview/download, fake media labels, idea conversion, mobile", errors }));
  } finally { await browser.close(); await new Promise((resolve) => server.close(resolve)); }
})().catch((error) => { server.close(); console.error(error.stack); process.exitCode = 1; });
