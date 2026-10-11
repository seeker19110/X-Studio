"use strict";
const $ = (id) => document.getElementById(id);
const esc = (text) =>
  String(text ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
let state = null,
  filter = "all",
  current = null,
  notified = new Set();
const states = {
  briefed: "Lên ý tưởng",
  researched: "Đã nghiên cứu",
  scripted: "Viết kịch bản",
  in_production: "Đang dựng",
  in_review: "Kiểm tra chất lượng",
  changes_requested: "Đang chỉnh sửa",
  approved: "Đã duyệt",
  scheduled: "Đã lên lịch",
  published: "Đã xuất bản",
  analyzed: "Đã có số liệu",
  blocked: "Cần xử lý",
  escalated: "Cần quyết định",
  closed: "Đã đóng",
};
const phase = {
  briefed: 0,
  researched: 1,
  scripted: 2,
  in_production: 3,
  in_review: 4,
  approved: 4,
  scheduled: 5,
  published: 5,
  analyzed: 5,
};
const modes = {
  manual: "Có người duyệt",
  production: "Tự động sản xuất",
  publish: "Tự động xuất bản",
};
const latest = (project, kind) =>
  [...project.media].reverse().find((m) => m.kind === kind);
function toast(text) {
  $("toast").textContent = text;
  $("toast").style.display = "block";
  setTimeout(() => ($("toast").style.display = "none"), 4000);
}
async function api(path, body) {
  const response = await fetch(
    path,
    body
      ? {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Studio-Request": "1",
          },
          body: JSON.stringify(body),
        }
      : {},
  );
  const data = await response.json();
  if (response.status === 401) {
    if (!$("login-dialog").open) $("login-dialog").showModal();
    throw Error(data.error);
  }
  if (!response.ok) throw Error(data.error || "Không thể kết nối");
  return data;
}
async function command(kind, data = {}) {
  await api("/api/commands", { kind, data });
  toast("Đã nhận yêu cầu. Xưởng sẽ xử lý sau bước đang chạy.");
  await refresh();
}
function switchView(view) {
  document
    .querySelectorAll(".view")
    .forEach((e) => e.classList.toggle("hidden", e.id !== view + "-view"));
  document
    .querySelectorAll(".nav")
    .forEach((e) => e.classList.toggle("active", e.dataset.view === view));
  $("breadcrumb").textContent = {
    projects: "Sản xuất video",
    review: "Kiểm tra & xuất bản",
    activity: "Nhật ký sản xuất",
    settings: "Cấu hình xưởng",
  }[view];
}
function renderProjects() {
  if (!state) return;
  const query = $("search").value.toLowerCase();
  const projects = state.projects.filter(
    (p) =>
      p.title.toLowerCase().includes(query) &&
      (filter === "all" ||
        (filter === "ready"
          ? latest(p, "final_video")
          : !latest(p, "final_video"))),
  );
  $("empty").classList.toggle("hidden", state.projects.length > 0);
  $("project-grid").innerHTML = projects
    .map((p) => {
      const thumb = latest(p, "thumbnail");
      const done = phase[p.state] || 0;
      const bad = ["blocked", "escalated"].includes(p.state);
      return `<button class="project-card" data-project="${esc(p.id)}"><div class="poster">${thumb && thumb.provider !== "fake" ? `<img src="${esc(thumb.url)}" alt="Thumbnail ${esc(p.title)}">` : `<span class="poster-title">${esc(p.title)}</span>`}<span class="poster-label">${p.format === "short" ? "SHORTS" : "ORIGINAL FILM"}</span><span class="duration">${esc(p.minutes)} phút mục tiêu</span></div><div class="card-body"><div class="card-top"><span class="badge ${bad ? "red" : p.state === "in_review" ? "amber" : ""}">${esc(states[p.state] || p.state)}</span><span class="muted">${p.format === "short" ? "9:16" : "16:9"}</span></div><h3>${esc(p.title)}</h3><div class="card-meta"><span>${p.scenes.length} cảnh</span><span>·</span><span>${Object.keys(p.reviews).length}/3 đánh giá</span></div><div class="steps">${[0, 1, 2, 3, 4].map((i) => `<span class="${i < done ? "done" : ""}"></span>`).join("")}</div><div class="card-footer"><span>${latest(p, "final_video") ? "Bản dựng cuối đã sẵn sàng" : "Tiến độ được lưu tự động"}</span><span>Mở dự án ↗</span></div></div></button>`;
    })
    .join("");
  if (state.projects.length && !projects.length)
    $("project-grid").innerHTML = "<p>Không có dự án phù hợp.</p>";
  document
    .querySelectorAll("[data-project]")
    .forEach((b) => (b.onclick = () => detail(b.dataset.project)));
}
function renderGates() {
  const names = {
    plan: "Duyệt kế hoạch",
    publish: "Duyệt xuất bản",
    replies: "Duyệt phản hồi",
    escalation: "Xử lý vấn đề",
  };
  $("gate-list").innerHTML = state.gates.length
    ? state.gates
        .map(
          (g) =>
            `<article class="review-card"><span class="badge amber">${esc(names[g.kind] || g.kind)}</span><h3>${esc(g.id)}</h3><p>${esc(g.hold)}</p><ul>${g.checklist.map((c) => `<li>${esc(c)}</li>`).join("")}</ul><label>Lý do / yêu cầu sửa<input id="reason-${esc(g.id)}" maxlength="500" placeholder="Ghi nhận quyết định của bạn"></label><div class="review-actions">${g.kind === "publish" ? `<button class="button quiet" data-preview="${esc(g.id.slice(4))}">Xem bản dựng</button>` : ""}<button class="button primary" data-gate="${esc(g.id)}" data-decision="approve">Duyệt ${({ plan: "kế hoạch", publish: "& xuất bản", replies: "phản hồi", escalation: "& tiếp tục" })[g.kind] || "quyết định"}</button><button class="button quiet" data-gate="${esc(g.id)}" data-decision="request_changes">Yêu cầu sửa</button><button class="button quiet" data-gate="${esc(g.id)}" data-decision="hold">Để sau</button></div></article>`,
        )
        .join("")
    : '<div class="empty"><h2>Chưa có việc cần duyệt</h2><p>Xưởng sẽ đưa các quyết định cần bạn đến đây.</p></div>';
  document.querySelectorAll("[data-gate]").forEach(
    (b) =>
      (b.onclick = async () => {
        b.disabled = true;
        try {
          await command("gate", {
            subject_id: b.dataset.gate,
            decision: b.dataset.decision,
            request_at: state.gates.find((g) => g.id === b.dataset.gate)
              ?.request_at,
            reason: $("reason-" + b.dataset.gate).value,
          });
        } catch (e) {
          toast(e.message);
        } finally {
          b.disabled = false;
        }
      }),
  );
  document
    .querySelectorAll("[data-preview]")
    .forEach((b) => (b.onclick = () => detail(b.dataset.preview)));
}
function detail(id) {
  current = id;
  const p = state.projects.find((p) => p.id === id);
  if (!p) return;
  const final = latest(p, "final_video") || latest(p, "draft_video");
  $("detail-body").innerHTML =
    `<h2>${esc(p.title)}</h2><p>${esc(states[p.state])} · ${p.scenes.length} cảnh · ${esc(p.minutes)} phút mục tiêu</p>${final && final.provider !== "fake" ? `<video class="player" src="${esc(final.url)}" controls preload="metadata"></video>` : '<div class="empty"><h2>' + (state.demo ? "Đang dùng dữ liệu mô phỏng" : "Chưa có bản dựng để phát") + "</h2><p>" + (state.demo ? "Chế độ demo dùng tệp giả để kiểm tra workflow, không phải video thực tế." : "Bạn có thể theo dõi từng cảnh bên dưới.") + "</p></div>"}<div class="asset-links">${[
      "final_video",
      "captions",
    ]
      .map((k) => {
        const a = latest(p, k);
        return a
          ? `<a href="${esc(a.url)}" download="${esc(p.id)}.${k === "captions" ? "srt" : "mp4"}">↓ ${k === "captions" ? "Phụ đề SRT" : "Tải bản dựng cuối"}</a>`
          : "";
      })
      .join(
        "",
      )}</div><div class="review-summary">${["fact", "rights", "quality"].map((k) => `<span>${{ fact: "Nội dung", rights: "Bản quyền", quality: "Chất lượng" }[k]}: ${esc(p.reviews[k]?.verdict || "chờ kiểm tra")}</span>`).join("")}</div>${p.qc ? `<details><summary>Kết quả đo file thật</summary><pre>${esc(JSON.stringify(p.qc, null, 2))}</pre></details>` : ""}<h3>Câu chuyện qua từng cảnh</h3><div class="scene-list">${p.scenes
      .map((s) => {
        const asset = [...p.media]
          .reverse()
          .find(
            (a) =>
              a.scene_id === s.id &&
              ["scene_image", "scene_video"].includes(a.kind),
          );
        return `<article class="scene">${asset && asset.provider !== "fake" ? (asset.kind === "scene_video" ? `<video src="${esc(asset.url)}" controls preload="metadata"></video>` : `<img src="${esc(asset.url)}" alt="Cảnh ${esc(s.id)}" loading="lazy">`) : ""}<small>${esc(s.id)} · ${s.duration_s.toFixed(1)} giây ${s.locked ? "· Đã khóa" : ""}</small><p>${esc(s.narration)}</p>${asset ? `<p>Nguồn: ${esc(asset.provenance.generated_by)} · ${esc(asset.provenance.license)}<br>${esc(asset.provenance.attribution || "")}</p>` : ""}</article>`;
      })
      .join("")}</div>`;
  if (!$("detail-dialog").open) $("detail-dialog").showModal();
}
function render() {
  const projects = state.projects;
  const complete = projects.filter((p) => latest(p, "final_video")).length;
  $("stat-active").textContent = projects.filter(
    (p) =>
      !latest(p, "final_video") && !["closed", "blocked"].includes(p.state),
  ).length;
  $("stat-review").textContent = state.gates.length;
  $("stat-done").textContent = complete;
  $("stat-mode").textContent = modes[state.automation] || state.automation;
  $("resource-note").textContent =
    `${state.render_mode === "bounded" ? "Dựng từng cảnh" : "Dựng theo đồ thị"} · ${state.threads} luồng CPU`;
  $("nav-count").textContent = projects.length;
  $("gate-count").textContent = state.gates.length;
  $("demo-badge").classList.toggle("hidden", !state.demo);
  $("engine-state").textContent = state.running
    ? "Xưởng đang hoạt động"
    : "Xưởng đã tạm dừng";
  $("run-toggle").textContent = state.running
    ? "Tạm dừng xưởng"
    : "Tiếp tục sản xuất";
  $("queue-note").textContent =
    `${state.queue} bước đang chờ · ${state.deferred} bước đợi thử lại`;
  $("activity-list").innerHTML =
    state.events
      .map(
        (e) =>
          `<div class="activity-row"><time>${esc(new Date(e.time).toLocaleTimeString("vi-VN"))}</time><span>${esc(e.action)} ${e.video_id ? "· " + esc(e.video_id) : ""}</span><span>${esc(e.actor)}</span></div>`,
      )
      .join("") || "<p>Nhật ký xuất hiện khi bạn tạo dự án đầu tiên.</p>";
  $("settings-list").innerHTML = Object.entries({
    "Lời đọc": state.providers.tts,
    "Hình ảnh": state.providers.image,
    "Dựng video": state.providers.video,
    "Nền tảng": state.platform,
    "Vận hành": modes[state.automation],
    "Tài nguyên": `${state.threads} luồng CPU · ${state.render_mode}`,
  })
    .map(
      ([k, v]) =>
        `<div class="setting"><small>${esc(k)}</small><strong>${esc(v)}</strong></div>`,
    )
    .join("");
  const errors = state.commands.filter((c) => c.status === "error");
  $("command-errors").innerHTML =
    errors
      .map(
        (c) =>
          `<p class="notice error">Yêu cầu ${esc(c.kind)}: ${esc(c.error)}</p>`,
      )
      .join("") +
    (state.worker_error
      ? `<p class="error">${esc(state.worker_error)}</p>`
      : "");
  for (const cmd of state.commands) {
    if (cmd.status === "error" && !notified.has(cmd.id)) {
      notified.add(cmd.id);
      toast(cmd.error);
    }
  }
  if (!$("settings-form").contains(document.activeElement)) {
    const fields = $("settings-form").elements;
    for (const key of ["automation", "threads", "style", "burn_captions"])
      fields[key].value = String(state[key] ?? "");
  }
  renderProjects();
  const focused = document.activeElement?.id;
  if (!focused?.startsWith("reason-")) renderGates();
}
async function refresh() {
  try {
    state = await api("/api/state");
    $("connection").textContent = "● Đã kết nối";
    render();
  } catch (e) {
    $("connection").textContent = "○ Mất kết nối";
    if (!$("login-dialog").open) $("command-errors").textContent = e.message;
  }
}
for (const b of document.querySelectorAll("[data-view]"))
  b.onclick = () => switchView(b.dataset.view);
for (const b of document.querySelectorAll("[data-close]"))
  b.onclick = () => $(b.dataset.close).close();
for (const id of ["new-project", "empty-create"])
  $(id).onclick = () => $("create-dialog").showModal();
for (const b of document.querySelectorAll("[data-filter]"))
  b.onclick = () => {
    filter = b.dataset.filter;
    document
      .querySelectorAll(".tab")
      .forEach((t) => t.classList.toggle("selected", t === b));
    renderProjects();
  };
$("search").oninput = renderProjects;
$("run-toggle").onclick = () => {
  if (state)
    command(state.running ? "pause" : "run").catch((e) => toast(e.message));
};
$("login-dialog").addEventListener("cancel", (e) => e.preventDefault());
$("login-form").onsubmit = async (e) => {
  e.preventDefault();
  try {
    await api("/api/login", { token: new FormData(e.target).get("token") });
    $("login-dialog").close();
    e.target.reset();
    await refresh();
  } catch (err) {
    $("login-error").textContent = err.message;
  }
};
$("create-form").onsubmit = async (e) => {
  e.preventDefault();
  const values = Object.fromEntries(new FormData(e.target));
  values.target_minutes = Number(values.target_minutes);
  values.budget_tokens = Number(values.budget_tokens);
  values.boundaries = values.boundaries.split("\n").filter(Boolean);
  const button = e.target.querySelector("[type=submit]");
  button.disabled = true;
  try {
    await command("create", values);
    $("create-dialog").close();
    e.target.reset();
    switchView("projects");
  } catch (err) {
    $("create-error").textContent = err.message;
  } finally {
    button.disabled = false;
  }
};
$("settings-form").onsubmit = async (e) => {
  e.preventDefault();
  const values = Object.fromEntries(new FormData(e.target));
  values.threads = Number(values.threads);
  values.burn_captions = values.burn_captions === "true";
  try {
    await command("settings", values);
  } catch (err) {
    toast(err.message);
  }
};
refresh();
setInterval(() => {
  if (!$("login-dialog").open) refresh();
}, 3000);
