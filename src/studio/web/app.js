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
const html = (id, content) => {
  if ($(id) && $(id).innerHTML !== content) $(id).innerHTML = content;
};
const latest = (p, kind) => [...p.media].reverse().find((m) => m.kind === kind);
const safeURL = (value) => {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : "";
  } catch {
    return "";
  }
};
const external = (url, label) =>
  safeURL(url)
    ? `<a href="${esc(safeURL(url))}" target="_blank" rel="noopener noreferrer">${esc(label)} ↗</a>`
    : esc(label);
const number = (n) => Number(n || 0).toLocaleString("vi-VN");
const date = (value) =>
  value
    ? new Date(value).toLocaleDateString("vi-VN", {
        timeZone: "Asia/Ho_Chi_Minh",
        day: "2-digit",
        month: "2-digit",
      })
    : "";
let state = null,
  filter = "all",
  current = null,
  view = "dashboard",
  layout = "grid",
  publishingFilter = "all",
  detailTab = "overview",
  guideStep = 0,
  guideFormat = "long",
  editingIdea = null,
  convertedIdea = null;
let notified = new Set(),
  refreshing = false,
  detailSignature = "",
  settingsDirty = false,
  pendingSettings = null;
const states = {
  briefed: "Đang nghiên cứu",
  researched: "Sẵn sàng viết",
  scripted: "Đã có kịch bản",
  in_production: "Đang sản xuất",
  in_review: "Chờ kiểm tra",
  changes_requested: "Đang chỉnh sửa",
  approved: "Đã duyệt",
  scheduled: "Đã lên lịch",
  published: "Đã xuất bản",
  analyzed: "Đã có số liệu",
  blocked: "Cần xử lý",
  escalated: "Cần quyết định",
  closed: "Đã đóng",
};
const modes = {
  manual: "Duyệt từng bước",
  production: "Tự động sản xuất",
  publish: "Tự động xuất bản",
};
const productionLabels = {
  explainer: "Video thuyết minh",
  film: "Phim ngắn",
  animation: "Hoạt hình",
};
const stylePresets = {
  cinematic: "Điện ảnh, ánh sáng tự nhiên, chuyển động máy quay có chủ đích, bảng màu nhất quán.",
  "2d": "Hoạt hình 2D, nét vẽ rõ ràng, bảng màu hài hòa, thiết kế nhân vật nhất quán.",
  "3d": "Hoạt hình 3D, ánh sáng mềm, vật liệu chi tiết, chuyển động giàu biểu cảm.",
  anime: "Anime, nét vẽ sắc, màu cel shading, biểu cảm rõ, bố cục giàu cảm xúc.",
};
const cinemaOf = (p) => p.cinema || p.request || {};
const productionBadges = (p) => {
  const c = cinemaOf(p);
  return `<div class="production-badges"><span class="badge">${esc(productionLabels[c.production_type] || productionLabels.explainer)}</span>${c.visual_mode === "generated_video" ? '<span class="badge">Clip AI</span>' : ""}</div>`;
};
function cinemaBrief(p) {
  const c = cinemaOf(p);
  return `<div class="cinema-brief"><div class="brief-grid"><div><small>LOẠI NỘI DUNG</small><p>${esc(productionLabels[c.production_type] || productionLabels.explainer)}</p></div><div><small>CÁCH TẠO HÌNH ẢNH</small><p>${c.visual_mode === "generated_video" ? "Clip AI từng cảnh" : "Ảnh có chuyển động"}</p></div></div>${[
    ["Phong cách hình ảnh", c.visual_style],
    ["Tóm tắt câu chuyện", c.logline],
    ["Hồ sơ nhân vật", c.character_bible],
    ["Chi tiết hình ảnh cần tránh", c.negative_prompt],
  ].filter(([, value]) => value).map(([label, value]) => `<h4>${label}</h4><p class="script-text">${esc(value)}</p>`).join("")}${c.production_type && c.production_type !== "explainer" && c.seed != null ? `<p class="muted">Seed tạo hình: ${esc(c.seed)}</p>` : ""}</div>`;
}
const views = {
  dashboard: [
    "Tổng quan",
    "Mọi ý tưởng đều có một hành trình.",
    "Tập trung vào câu chuyện. Theo dõi mọi bước, từ ý tưởng đến video đã xuất bản.",
  ],
  ideas: [
    "Kho ý tưởng",
    "Lưu cảm hứng. Nuôi câu chuyện.",
    "Bản nháp lưu trên trình duyệt này. Chuyển thành dự án khi brief đã sẵn sàng.",
  ],
  projects: [
    "Dự án video",
    "Những câu chuyện đang thành hình.",
    "Video thuyết minh, phim ngắn hay hoạt hình: mỗi dự án đều có tiến độ và một bước tiếp theo rõ ràng.",
  ],
  workflow: [
    "Quy trình chuẩn",
    "Một quy trình. Nhiều câu chuyện hay.",
    "Tám bước từ brief đến đo lường, với đầu ra và tiêu chí kiểm tra cho từng bước.",
  ],
  review: [
    "Phòng duyệt",
    "Chất lượng trước mỗi lần xuất bản.",
    "Xem nội dung, nguồn tư liệu và bản dựng trước khi quyết định.",
  ],
  publishing: [
    "Xuất bản",
    "Từ bản dựng đến người xem.",
    "Theo dõi video chờ duyệt, lịch đăng và kết quả xuất bản tại một nơi.",
  ],
  activity: [
    "Hoạt động",
    "Mỗi bước đều có dấu vết.",
    "Nhật ký gần nhất của xưởng, được ghi từ các sự kiện sản xuất thực tế.",
  ],
  settings: [
    "Cấu hình",
    "Thiết lập xưởng theo cách của bạn.",
    "Chọn mức tự động hóa, phong cách hình ảnh và tài nguyên sản xuất.",
  ],
};
const iconPaths = {
  idea: '<path d="M9 18h6m-5 3h4M8.5 14.5a6 6 0 1 1 7 0C14 15.5 14 17 14 18h-4c0-1 0-2.5-1.5-3.5Z"/>',
  research:
    '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5M8 8h5m-5 4h3"/>',
  script: '<path d="M14 3H5v18h14V8l-5-5Zm0 0v5h5M8 12h8m-8 4h6"/>',
  media:
    '<rect x="3" y="4" width="18" height="16" rx="3"/><circle cx="8" cy="9" r="1.5"/><path d="m3 17 5-5 4 4 4-6 5 7"/>',
  edit: '<path d="m9 6 10 6-10 6V6Z"/><rect x="2" y="3" width="20" height="18" rx="3"/>',
  check: '<path d="m8 12 3 3 5-6M12 2l9 4v6c0 5-9 10-9 10S3 17 3 12V6l9-4Z"/>',
  publish: '<path d="M12 16V3m-5 5 5-5 5 5M4 14v6h16v-6"/>',
  learn: '<path d="M3 3v18h18M7 16l4-6 4 3 6-8"/>',
  arrow: '<path d="M5 12h14m-6-6 6 6-6 6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
};
const icon = (name) =>
  `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${iconPaths[name] || iconPaths.idea}</svg>`;
const stages = [
  {
    title: "Ý tưởng & brief",
    label: "Brief",
    icon: "idea",
    owner: "Bạn · Biên tập",
    output: "Brief rõ mục tiêu, khán giả, thông điệp và định dạng.",
    checks: [
      "Một video giải quyết một nhu cầu cụ thể.",
      "Xác định người xem và điều họ nhận được.",
      "Chọn định dạng, thời lượng, ngôn ngữ và giới hạn nội dung.",
    ],
    long: "Video dài: một câu hỏi trung tâm, các chương hỗ trợ và lời hứa rõ ràng với người xem.",
    short:
      "Video ngắn: một thông điệp, một payoff. Chọn thời lượng gọn và khung hình 9:16.",
    action: "Lưu ý tưởng → hoàn thiện brief → bắt đầu dự án.",
    gate: "Kế hoạch đi qua cửa duyệt plan; chế độ sản xuất có thể duyệt kế hoạch theo policy đã cấu hình.",
  },
  {
    title: "Nghiên cứu & nguồn",
    label: "Nghiên cứu",
    icon: "research",
    owner: "Nghiên cứu · Kiểm chứng",
    output:
      "Hồ sơ nghiên cứu, nguồn tham khảo và danh sách luận điểm cần kiểm chứng.",
    checks: [
      "Mỗi luận điểm quan trọng có nguồn có thể kiểm tra.",
      "Phân biệt dữ kiện, nhận định và điều chưa chắc chắn.",
      "Ghi nhận nguồn, ngày truy cập và rủi ro của chủ đề.",
    ],
    long: "Video dài: dựng luận điểm theo chương; dùng nhiều nguồn cho những nhận định quan trọng.",
    short:
      "Video ngắn: kiểm chứng dữ kiện chính; sự ngắn gọn không thay thế tính chính xác.",
    action: "Kiểm tra nguồn trong tab Kịch bản của dự án.",
    gate: "Review nội dung phải đạt trước khi xuất bản.",
  },
  {
    title: "Kịch bản & hook",
    label: "Kịch bản",
    icon: "script",
    owner: "Biên kịch · Biên tập",
    output:
      "Hook → nội dung → payoff → CTA, kèm ghi chú hình ảnh và ước lượng thời lượng.",
    checks: [
      "Hook nói rõ lý do người xem nên ở lại.",
      "Lời đọc tự nhiên, đủ ngắn để nói thành tiếng.",
      "Thứ tự luận điểm mạch lạc; CTA phù hợp mục tiêu.",
    ],
    long: "Video dài: chia chương có nhịp nghỉ, ví dụ và điểm chuyển; kết luận đáp lại câu hỏi mở đầu.",
    short:
      "Video ngắn: vào thẳng vấn đề trong vài giây đầu, một ý chính và kết thúc rõ.",
    action: "Đọc kịch bản, hook, CTA và nguồn trong không gian dự án.",
    gate: "Kiểm tra biên tập là bước SOP khuyến nghị; hệ thống hiện không có nút duyệt kịch bản riêng.",
  },
  {
    title: "Storyboard & tư liệu",
    label: "Tư liệu",
    icon: "media",
    owner: "Sản xuất · Hình ảnh · Âm thanh",
    output:
      "Danh sách cảnh với lời đọc, hình/footage, thời lượng, voice và hồ sơ quyền sử dụng.",
    checks: [
      "Mỗi cảnh có lời đọc và hình ảnh hỗ trợ thông điệp.",
      "Tư liệu, nhạc và font có quyền sử dụng rõ ràng.",
      "Phong cách hình ảnh, giọng đọc và tỷ lệ khung hình nhất quán.",
    ],
    long: "Video dài: thiết kế hình ảnh theo chương; dùng cache và khóa cảnh đã đạt để tiết kiệm công dựng.",
    short:
      "Video ngắn: bố cục 9:16, chữ dễ đọc trên điện thoại; giữ nội dung chính tránh vùng giao diện nền tảng.",
    action:
      "Xem cảnh, nghe lời đọc và kiểm tra nguồn tài nguyên trong tab Cảnh & tư liệu.",
    gate: "Tư liệu không rõ quyền sử dụng phải được xử lý trước cửa duyệt xuất bản.",
  },
  {
    title: "Dựng & phụ đề",
    label: "Dựng phim",
    icon: "edit",
    owner: "Dựng phim · Hậu kỳ",
    output: "Bản nháp, bản dựng cuối và phụ đề SRT; sửa theo từng cảnh.",
    checks: [
      "Nhịp dựng hỗ trợ câu chuyện, hình và lời khớp nhau.",
      "Nhạc không lấn lời đọc; phụ đề dễ đọc, không tràn.",
      "Xem lại toàn bộ bản dựng, ghi rõ cảnh cần sửa.",
    ],
    long: "Video dài: kiểm tra chuyển chương, tính liên tục, khoảng nghỉ và mốc chapter.",
    short:
      "Video ngắn: loại đoạn dư thừa; xem lại trên màn hình dọc và kiểm tra nhịp mở đầu.",
    action:
      "Xem và tải bản dựng trong tab Tổng quan. Dữ liệu demo không phải video thật.",
    gate: "Bản dựng được editor review và sửa theo cảnh; SOP khuyến nghị con người xem trọn video trước khi đăng.",
  },
  {
    title: "Kiểm tra chất lượng",
    label: "Kiểm tra",
    icon: "check",
    owner: "Nội dung · Bản quyền · QC",
    output: "Ba review nội dung/bản quyền/chất lượng và kết quả đo file thật.",
    checks: [
      "Review nội dung, bản quyền và chất lượng đều pass.",
      "Có bản cuối; kiểm tra codec, thời lượng, hình và tiếng.",
      "Tựa đề, thumbnail và mô tả phản ánh đúng nội dung.",
    ],
    long: "Video dài: kiểm tra toàn bộ file, thời lượng mục tiêu và credits theo tài nguyên đã sử dụng.",
    short:
      "Video ngắn: kiểm tra tỷ lệ 9:16, caption và độ rõ của chữ ở kích thước điện thoại.",
    action: "Mở tab Kiểm tra & đăng để đọc từng finding và kết quả QC.",
    gate: "QC thực tế và các review phải đạt; khi chưa đạt, yêu cầu sửa hoặc giữ lại.",
  },
  {
    title: "Đóng gói & xuất bản",
    label: "Xuất bản",
    icon: "publish",
    owner: "SEO · Bạn · Nền tảng",
    output:
      "Tiêu đề, mô tả, thumbnail, phụ đề, credits và liên kết nền tảng từ adapter.",
    checks: [
      "Kiểm tra đúng kênh, tiêu đề, mô tả và credits.",
      "Xem bản cuối, đọc checklist và duyệt cửa publish.",
      "Xác nhận URL/trạng thái đăng từ nền tảng.",
    ],
    long: "Video dài: mô tả có cấu trúc, chapters hợp lệ và thumbnail rõ ở kích thước nhỏ.",
    short:
      "Video ngắn: kiểm tra tên video, format và nội dung hiển thị. Đăng TikTok/Reels cần adapter riêng.",
    action:
      "Theo dõi tại Xuất bản và quyết định trong Phòng duyệt. Adapter hiện có là YouTube; các kênh khác chưa được kết nối.",
    gate: "Hệ thống chỉ xuất bản qua cửa publish hoặc policy đã được cấu hình cho chế độ tự xuất bản.",
  },
  {
    title: "Đo lường & cải tiến",
    label: "Đo lường",
    icon: "learn",
    owner: "Phân tích · Biên tập",
    output:
      "Số liệu nền tảng, bài học về hook/retention và ý tưởng cho vòng tiếp theo.",
    checks: [
      "Chỉ dùng số liệu đã đồng bộ từ tài khoản nền tảng.",
      "So sánh với baseline của kênh và các video cùng định dạng.",
      "Biến mỗi phát hiện thành một thay đổi cụ thể cho video kế tiếp.",
    ],
    long: "Video dài: đọc CTR, thời lượng xem và retention theo chương; xem lại sau 24 giờ, 7 ngày, 28 ngày.",
    short:
      "Video ngắn: chú ý đoạn mở đầu, tỷ lệ xem hết và thời lượng xem; so sánh với video ngắn cùng độ dài.",
    action:
      "Tab Số liệu hiển thị khi có performance snapshot. Lịch kiểm tra là SOP, chưa phải lịch tự động.",
    gate: "Chưa có dữ liệu phải hiển thị chưa đồng bộ; không thay bằng số minh họa.",
  },
];
const sources = [
  ["FFmpeg", "Dựng phim & kiểm tra file", "https://github.com/FFmpeg/FFmpeg"],
  ["ComfyUI", "Workflow sinh clip; bộ kết nối HTTP của X-Studio", "https://github.com/Comfy-Org/ComfyUI"],
  ["Wan2.2", "Mô hình video dùng qua workflow phù hợp với GPU", "https://github.com/Wan-Video/Wan2.2"],
  ["AnimateDiff", "Chuyển động hoạt hình trong workflow tùy chọn", "https://github.com/guoyww/AnimateDiff"],
  ["Blender", "Render hoạt hình 3D; nhập clip đã dựng từ thư viện", "https://github.com/blender/blender"],
  [
    "Remotion",
    "Template, preview & đồ họa theo dữ liệu",
    "https://github.com/remotion-dev/remotion",
  ],
  [
    "Whisper",
    "Nhận dạng lời nói & phụ đề từ audio",
    "https://github.com/openai/whisper",
  ],
  [
    "MoviePy",
    "Compositing video bằng Python",
    "https://github.com/Zulko/moviepy",
  ],
  ["n8n", "Workflow, retry & cửa duyệt", "https://github.com/n8n-io/n8n"],
  [
    "Postiz",
    "Đóng gói, lịch đăng & analytics đa kênh",
    "https://github.com/gitroomhq/postiz-app",
  ],
];
function stageOf(p) {
  if (p.state === "analyzed" || p.performance) return 7;
  if (["approved", "scheduled", "published"].includes(p.state)) return 6;
  if (p.state === "in_review" || latest(p, "final_video")) return 5;
  if (latest(p, "draft_video") || p.state === "changes_requested") return 4;
  if (p.state === "in_production" || p.scenes.length) return 3;
  if (p.state === "scripted" || p.script) return 2;
  return 1;
}
function toast(message) {
  $("toast").textContent = message;
  $("toast").style.display = "block";
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => ($("toast").style.display = "none"), 4500);
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
  const result = await api("/api/commands", { kind, data });
  toast("Đã nhận yêu cầu. Xưởng sẽ xử lý sau bước đang chạy.");
  await refresh();
  return result;
}
function switchView(next) {
  if (!views[next]) return;
  view = next;
  if (location.hash !== "#" + next) history.replaceState(null, "", "#" + next);
  document
    .querySelectorAll(".view")
    .forEach((e) => e.classList.toggle("hidden", e.id !== next + "-view"));
  document.querySelectorAll(".nav").forEach((e) => {
    e.classList.toggle("active", e.dataset.view === next);
    if (e.dataset.view === next) e.setAttribute("aria-current", "page");
    else e.removeAttribute("aria-current");
  });
  $("breadcrumb").textContent = views[next][0];
  $("page-title").textContent = views[next][1];
  $("page-description").textContent = views[next][2];
  document
    .querySelector(".overview")
    .classList.toggle("hidden", next === "publishing");
  if (next === "ideas") renderIdeas();
  if (next === "workflow") renderGuide();
}
function card(p, compact = false) {
  const thumb = latest(p, "thumbnail"),
    phase = stageOf(p),
    bad = ["blocked", "escalated"].includes(p.state),
    published = ["published", "analyzed"].includes(p.state);
  return `<button class="project-card ${compact ? "compact" : ""}" data-project="${esc(p.id)}"><div class="poster poster-${p.format}">${thumb && thumb.provider !== "fake" ? `<img src="${esc(thumb.url)}" alt="Thumbnail ${esc(p.title)}" loading="lazy">` : `<div class="poster-art" aria-hidden="true">${icon(p.format === "short" ? "publish" : "edit")}</div><span class="poster-title">${esc(p.title)}</span>`}<span class="poster-label">${p.format === "short" ? "SHORT VIDEO" : "LONG VIDEO"}</span><span class="duration">${esc(p.minutes)} phút · ${p.format === "short" ? "9:16" : "16:9"}</span></div><div class="card-body"><div class="card-top"><span class="badge ${bad ? "red" : published ? "success" : p.state === "in_review" ? "amber" : ""}">${esc(states[p.state] || p.state)}</span><span class="muted">${esc(date(p.updated_at))}</span></div><h3>${esc(p.title)}</h3>${productionBadges(p)}<div class="card-meta"><span>${p.scenes.length} cảnh</span><span>·</span><span>${Object.values(p.reviews).filter((r) => r.verdict === "pass").length}/3 review đạt</span></div><div class="steps" aria-label="Bước ${phase + 1}/8: ${esc(stages[phase].title)}">${stages.map((_, i) => `<span class="${i < phase ? "done" : i === phase ? "current" : ""}"></span>`).join("")}</div><div class="card-footer"><span>${bad ? "Mở để xem vấn đề" : stages[phase].label}</span><span>Mở dự án ↗</span></div></div></button>`;
}
function bindProjects() {
  document
    .querySelectorAll("[data-project]")
    .forEach((b) => (b.onclick = () => detail(b.dataset.project)));
}
function filteredProjects() {
  const query = $("search").value.trim().toLocaleLowerCase("vi");
  return state.projects.filter(
    (p) =>
      p.title.toLocaleLowerCase("vi").includes(query) &&
      (filter === "all" ||
        (filter === "ready"
          ? latest(p, "final_video")
          : !latest(p, "final_video") &&
            !["closed", "published", "analyzed"].includes(p.state))),
  );
}
function renderProjects() {
  if (!state) return;
  const projects = filteredProjects();
  $("empty").classList.toggle("hidden", state.projects.length > 0);
  $("project-grid").classList.toggle("hidden", layout !== "grid");
  $("project-board").classList.toggle(
    "hidden",
    layout !== "board" || !state.projects.length,
  );
  $("project-count").textContent = `${projects.length} dự án`;
  html(
    "project-grid",
    projects.map((p) => card(p)).join("") ||
      (state.projects.length
        ? '<div class="empty"><h3>Không tìm thấy video</h3><p>Thử tên khác hoặc chọn Tất cả dự án.</p></div>'
        : ""),
  );
  const groups = [
    [
      "Chuẩn bị",
      "research",
      (p) => ["briefed", "researched"].includes(p.state),
    ],
    ["Kịch bản", "script", (p) => p.state === "scripted"],
    [
      "Sản xuất",
      "edit",
      (p) => ["in_production", "changes_requested"].includes(p.state),
    ],
    [
      "Kiểm tra & đăng",
      "check",
      (p) => ["in_review", "approved", "scheduled"].includes(p.state),
    ],
    [
      "Đã xuất bản",
      "publish",
      (p) => ["published", "analyzed"].includes(p.state),
    ],
    [
      "Cần xử lý / đóng",
      "clock",
      (p) => ["blocked", "escalated", "closed"].includes(p.state),
    ],
  ];
  html(
    "project-board",
    groups
      .map(([name, symbol, test]) => {
        const items = projects.filter(test);
        return `<section class="stage-column"><h3>${icon(symbol)} ${name}<span>${items.length}</span></h3>${items.map((p) => `<button class="board-card" data-project="${esc(p.id)}"><span class="badge">${p.format === "short" ? "9:16 · Video ngắn" : "16:9 · Video dài"}</span>${productionBadges(p)}<h4>${esc(p.title)}</h4><p>${esc(states[p.state])}</p><small>${p.scenes.length} cảnh · ${esc(p.minutes)} phút</small>${icon("arrow")}</button>`).join("") || '<p class="column-empty">Chưa có dự án</p>'}</section>`;
      })
      .join(""),
  );
  bindProjects();
}
function renderDashboard() {
  const projects = state.projects,
    waiting = state.gates.length;
  $("dashboard-count").textContent = `${projects.length} dự án trong xưởng`;
  html(
    "workflow-track",
    stages
      .map((s, i) => {
        const count = projects.filter(
          (p) =>
            stageOf(p) === i &&
            !["blocked", "escalated", "closed"].includes(p.state),
        ).length;
        return `<button class="journey-step ${count ? "current" : ""}" ${i === 6 ? 'data-view="publishing"' : `data-guide="${i}"`} aria-label="${esc(s.title)}: ${count} dự án"><span class="step-icon">${icon(s.icon)}</span><span class="step-number">0${i + 1}</span><strong>${s.label}</strong><span class="step-count">${i === 0 ? `${ideas.length} ý tưởng` : `${count} dự án`}</span></button>`;
      })
      .join(""),
  );
  html(
    "recent-projects",
    [...projects]
      .reverse()
      .slice(0, 4)
      .map((p) => card(p, true))
      .join("") ||
      '<div class="empty dashboard-empty"><div class="empty-icon">✦</div><h3>Ý tưởng đầu tiên đang chờ bạn</h3><p>Chọn video dài hoặc ngắn, viết brief và để xưởng bắt đầu.</p><button class="button primary" data-create>Bắt đầu một dự án ' +
        icon("arrow") +
        "</button></div>",
  );
  const problems = projects.filter((p) =>
    ["blocked", "escalated"].includes(p.state),
  );
  html(
    "next-actions",
    `${waiting ? `<button class="action-item warning" data-view="review"><span class="action-icon">${icon("check")}</span><span><strong>${waiting} quyết định chờ bạn</strong><small>Kiểm tra kế hoạch hoặc bản dựng</small></span>${icon("arrow")}</button>` : '<div class="action-item"><span class="action-icon">' + icon("check") + "</span><span><strong>Không có quyết định đang chờ</strong><small>Xưởng sẽ đưa yêu cầu duyệt đến đây.</small></span></div>"}${problems.map((p) => `<button class="action-item warning" data-project="${esc(p.id)}"><span class="action-icon">!</span><span><strong>${esc(p.title)}</strong><small>${esc(states[p.state])} · mở để xem chi tiết</small></span>${icon("arrow")}</button>`).join("")}<button class="action-item" data-view="ideas"><span class="action-icon">${icon("idea")}</span><span><strong>${ideas.length ? `${ideas.length} ý tưởng trong kho` : "Ghi lại ý tưởng mới"}</strong><small>Chuẩn bị câu chuyện cho vòng tiếp theo</small></span>${icon("arrow")}</button><button class="action-item" data-view="workflow"><span class="action-icon">${icon("learn")}</span><span><strong>Checklist cho một video tốt</strong><small>Đầu ra và tiêu chí của từng bước</small></span>${icon("arrow")}</button><div class="production-note"><span class="badge ${state.demo ? "amber" : "success"}">${state.demo ? "DEMO OFFLINE" : "XƯỞNG ĐÃ KẾT NỐI"}</span><p>${state.demo ? "Dữ liệu mô phỏng giúp thử quy trình. Tệp demo không phải video thật." : `Đang dùng nền tảng ${esc(state.platform)}. ${state.automation === "publish" ? "Policy tự xuất bản đang bật." : "Bản dựng chờ duyệt trước khi đăng."}`}</p></div>`,
  );
  bindDynamic();
}
function renderGuide() {
  const s = stages[guideStep];
  html(
    "workflow-guide",
    `<div class="guide-toolbar"><span class="badge">PLAYBOOK · 8 BƯỚC</span><div class="tabs"><button class="tab ${guideFormat === "long" ? "selected" : ""}" data-guide-format="long">Video dài · 16:9</button><button class="tab ${guideFormat === "short" ? "selected" : ""}" data-guide-format="short">Video ngắn · 9:16</button></div></div><div class="guide-layout"><nav class="guide-nav" aria-label="Các bước quy trình">${stages.map((step, i) => `<button class="workflow-step ${i === guideStep ? "selected" : ""}" data-guide-step="${i}" ${i === guideStep ? 'aria-current="step"' : ""}><span class="step-number">0${i + 1}</span>${icon(step.icon)}<span>${step.title}</span></button>`).join("")}</nav><article class="guide-content"><span class="eyebrow">BƯỚC 0${guideStep + 1} · ${esc(s.owner)}</span><h2>${s.title}</h2><div class="deliverable"><span>${icon(s.icon)}</span><div><small>ĐẦU RA CẦN CÓ</small><p>${s.output}</p></div></div><h3>Tiêu chí hoàn thành</h3><ul class="guide-checklist">${s.checks.map((c) => `<li><span>✓</span>${c}</li>`).join("")}</ul><div class="format-note"><span class="badge">${guideFormat === "short" ? "VIDEO NGẮN" : "VIDEO DÀI"}</span><p>${s[guideFormat]}</p></div><div class="notice"><strong>Điểm kiểm soát</strong><p>${s.gate}</p></div><p>${s.action}</p><div class="guide-actions"><button class="button quiet" data-guide-step="${Math.max(0, guideStep - 1)}" ${guideStep === 0 ? "disabled" : ""}>← Bước trước</button><button class="button primary" data-guide-step="${Math.min(7, guideStep + 1)}" ${guideStep === 7 ? "disabled" : ""}>Bước tiếp theo →</button></div></article></div><div class="section-heading"><h2>Nền tảng nghiên cứu</h2><span class="muted">Repo chính chủ · tham khảo cho thiết kế</span></div><div class="sources-grid">${sources.map(([name, desc, url]) => `<a class="source-card" href="${url}" target="_blank" rel="noopener noreferrer"><strong>${name} ↗</strong><p>${desc}</p></a>`).join("")}</div><p class="muted">X-Studio dùng Python + FFmpeg + SQLite, có bộ kết nối ComfyUI qua HTTP. Bạn cần tự cài máy chủ và workflow phù hợp; Wan2.2, AnimateDiff và Blender chưa được cài tự động. Các repo khác là nguồn tham khảo cho thiết kế.</p>`,
  );
  document.querySelectorAll("[data-guide-step]").forEach(
    (b) =>
      (b.onclick = () => {
        guideStep = Number(b.dataset.guideStep);
        renderGuide();
      }),
  );
  document.querySelectorAll("[data-guide-format]").forEach(
    (b) =>
      (b.onclick = () => {
        guideFormat = b.dataset.guideFormat;
        renderGuide();
      }),
  );
}
function renderGates() {
  // Preserve editing only while the displayed gate generations remain current.
  const displayed = [...$("gate-list").querySelectorAll("input")];
  if (
    $("gate-list").contains(document.activeElement) &&
    displayed.length === state.gates.length &&
    displayed.every((input) =>
      state.gates.some(
        (g) =>
          input.id === "reason-" + g.id &&
          input.dataset.requestAt === g.request_at,
      ),
    )
  )
    return;
  const names = {
    plan: "Duyệt kế hoạch",
    publish: "Duyệt xuất bản",
    replies: "Duyệt phản hồi",
    escalation: "Xử lý vấn đề",
  };
  const reasons = Object.fromEntries(
    [...$("gate-list").querySelectorAll("input")].map((e) => [
      e.id + "|" + e.dataset.requestAt,
      e.value,
    ]),
  );
  html(
    "gate-list",
    state.gates.length
      ? state.gates
          .map((g) => {
            const p = state.projects.find((p) => g.id.endsWith(p.id));
            return `<article class="review-card"><div class="card-top"><span class="badge amber">${esc(names[g.kind] || g.kind)}</span><small>${esc(date(g.request_at))}</small></div><h3>${esc(p?.title || g.id)}</h3><p>${esc(g.hold || "Đọc checklist và xác nhận quyết định của bạn.")}</p><ul class="guide-checklist">${g.checklist.map((c) => `<li><span>□</span>${esc(c)}</li>`).join("")}</ul><label>Lý do / yêu cầu sửa<input id="reason-${esc(g.id)}" data-request-at="${esc(g.request_at)}" maxlength="500" placeholder="Ghi rõ điều cần sửa hoặc lý do duyệt" value="${esc(reasons["reason-" + g.id + "|" + g.request_at] || "")}"></label><div class="review-actions">${p ? `<button class="button quiet" data-preview="${esc(p.id)}">Mở dự án</button>` : ""}<button class="button primary" data-gate="${esc(g.id)}" data-request-at="${esc(g.request_at)}" data-decision="approve">Duyệt ${{ plan: "kế hoạch", publish: "& xuất bản", replies: "phản hồi", escalation: "& tiếp tục" }[g.kind] || "quyết định"}</button>${g.kind === "publish" ? `<button class="button quiet" data-gate="${esc(g.id)}" data-request-at="${esc(g.request_at)}" data-decision="request_changes">Yêu cầu sửa</button>` : ""}<button class="button quiet" data-gate="${esc(g.id)}" data-request-at="${esc(g.request_at)}" data-decision="hold">Để sau</button></div></article>`;
          })
          .join("")
      : '<div class="empty"><div class="empty-icon">' +
          icon("check") +
          '</div><h2>Chưa có việc cần duyệt</h2><p>Xem video đã lên lịch hoặc đã đăng trong mục Xuất bản.</p><a class="button primary" href="#publishing">Xem lịch đăng & video →</a></div>',
  );
  document.querySelectorAll("[data-gate]").forEach(
    (b) =>
      (b.onclick = async () => {
        const reason = $("reason-" + b.dataset.gate).value;
        if (b.dataset.decision === "hold") {
          toast("Đã để lại quyết định. Yêu cầu duyệt vẫn đang chờ bạn.");
          switchView("dashboard");
          return;
        }
        const gate = state.gates.find((g) => g.id === b.dataset.gate);
        if (!gate || gate.request_at !== b.dataset.requestAt) {
          toast(
            "Checklist đã thay đổi. Kiểm tra lại yêu cầu mới trước khi duyệt.",
          );
          b.blur();
          renderGates();
          return;
        }
        if (b.dataset.decision === "request_changes" && !reason.trim()) {
          toast("Ghi rõ yêu cầu sửa để xưởng có thể xử lý.");
          $("reason-" + b.dataset.gate).focus();
          return;
        }
        b.disabled = true;
        try {
          await command("gate", {
            subject_id: b.dataset.gate,
            decision: b.dataset.decision,
            request_at: b.dataset.requestAt,
            reason: reason || "Đã kiểm tra trong X-Studio",
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
function emptyPanel(title, note) {
  return `<div class="empty"><h3>${esc(title)}</h3><p>${esc(note)}</p></div>`;
}
function demoPublication(p) {
  const url = safeURL(p.publication?.url);
  return (
    state.demo ||
    (state.platform === "fake" && Boolean(p.publication)) ||
    latest(p, "final_video")?.provider === "fake" ||
    (url && new URL(url).hostname === "fake.video")
  );
}
function publishingStatus(p) {
  if (p.publication) return p.publication.status;
  if (state.gates.some((g) => g.kind === "publish" && g.id.endsWith(p.id)))
    return "ready";
  return ["blocked", "escalated", "changes_requested"].includes(p.state)
    ? "attention"
    : "checking";
}
function renderPublishing() {
  const simulated = state.demo;
  const localOnly = state.platform === "fake" ||
    (state.platform === "youtube" && state.youtube_oauth_configured === false);
  html(
    "publishing-platform",
    `<div><span class="badge ${simulated ? "amber" : "success"}">${simulated ? "MÔ PHỎNG OFFLINE" : localOnly ? "BẢN DỰNG CỤC BỘ" : esc(state.platform === "youtube" ? "YOUTUBE" : state.platform)}</span><h2>${simulated ? "Bạn đang xem xuất bản mô phỏng" : localOnly ? "Video thật, sẵn sàng xem trước" : "Nền tảng xuất bản: " + esc(state.platform === "youtube" ? "YouTube" : state.platform)}</h2><p>${simulated ? "Lịch đăng và trạng thái bên dưới là dữ liệu demo. Chưa có video nào được đăng lên YouTube thật. Đăng thật cần kết nối YouTube, tư liệu thật và duyệt bản cuối." : localOnly ? "Bản dựng MP4 có thể xem và tải tại đây. Chưa kết nối YouTube. Video cần hoàn tất kiểm tra nội dung, tư liệu, QC và được duyệt trước khi đăng thật." : "Trạng thái và liên kết được lấy từ adapter nền tảng. Xưởng chỉ đăng sau cửa duyệt xuất bản hoặc policy đã được cấu hình."}</p></div><button type="button" class="button quiet" data-view="settings">Xem cấu hình</button>`,
  );
  const projects = [...state.projects]
    .reverse()
    .filter(
      (p) =>
        p.publication ||
        latest(p, "final_video") ||
        state.gates.some((g) => g.kind === "publish" && g.id.endsWith(p.id)),
    );
  const groups = {
    ready: (p) => ["ready", "checking"].includes(publishingStatus(p)),
    scheduled: (p) => publishingStatus(p) === "scheduled",
    published: (p) => publishingStatus(p) === "published",
    attention: (p) =>
      ["failed", "rolled_back", "attention"].includes(publishingStatus(p)),
  };
  $("publishing-count").textContent = projects.length;
  $("publishing-ready").textContent = projects.filter(groups.ready).length;
  $("publishing-scheduled").textContent = projects.filter(
    groups.scheduled,
  ).length;
  $("publishing-live").textContent = projects.filter(groups.published).length;
  const visible =
    publishingFilter === "all"
      ? projects
      : projects.filter(groups[publishingFilter]);
  const labels = {
    ready: "Chờ duyệt xuất bản",
    checking: "Chờ kiểm tra",
    scheduled: "Đã lên lịch",
    published: "Đã xuất bản",
    failed: "Đăng thất bại",
    rolled_back: "Đã hoàn tác",
    attention: "Cần xử lý",
  };
  html(
    "publishing-list",
    visible
      .map((p) => {
        const status = publishingStatus(p),
          mock = demoPublication(p),
          gate = state.gates.find(
            (g) => g.kind === "publish" && g.id.endsWith(p.id),
          );
        const scheduled = p.publication?.scheduled_at
          ? new Date(p.publication.scheduled_at)
          : null;
        const time =
          scheduled && Number.isFinite(scheduled.getTime())
            ? scheduled.toLocaleString("vi-VN", {
                timeZone: "Asia/Ho_Chi_Minh",
                hour: "2-digit",
                minute: "2-digit",
                day: "2-digit",
                month: "2-digit",
                year: "numeric",
              })
            : "Chưa có lịch đăng";
        const thumb = latest(p, "thumbnail");
        return `<article class="project-card publishing-card" data-publication="${esc(p.id)}"><div class="poster poster-${p.format}">${thumb && thumb.provider !== "fake" ? `<img src="${esc(thumb.url)}" alt="Thumbnail ${esc(p.title)}" loading="lazy">` : `<span class="poster-title">${esc(p.title)}</span>`}<span class="poster-label">${p.format === "short" ? "VIDEO NGẮN · 9:16" : "VIDEO DÀI · 16:9"}</span></div><div class="card-body"><div class="card-top"><span class="badge ${groups.attention(p) ? "red" : status === "published" ? "success" : "amber"}">${esc(labels[status] || status)}</span>${mock ? '<span class="badge neutral">Mô phỏng</span>' : ""}</div><h3>${esc(p.metadata?.title || p.title)}</h3><dl class="publishing-meta"><div><dt>${mock ? "Lịch mô phỏng" : "Lịch đăng · giờ Việt Nam"}</dt><dd>${esc(time)}</dd></div><div><dt>Đích đăng</dt><dd>${mock ? "Demo · chưa đăng YouTube" : localOnly ? "Chưa kết nối YouTube" : esc(state.platform === "youtube" ? "YouTube" : state.platform)}</dd></div><div><dt>Review nội dung / tư liệu / video</dt><dd>${Object.values(p.reviews).filter((r) => r.verdict === "pass").length}/3 đạt${mock ? " · dữ liệu demo" : ""}</dd></div></dl><div class="publishing-actions">${latest(p, "final_video") && latest(p, "final_video").provider !== "fake" ? `<button type="button" class="button primary small" data-video-preview="${esc(p.id)}">Xem video ▶</button>` : ""}<button type="button" class="button quiet small" data-publishing-project="${esc(p.id)}">Metadata & QC</button>${gate ? `<button type="button" class="button primary small" data-publishing-gate="${esc(gate.id)}">Kiểm tra & duyệt →</button>` : !p.publication ? '<span class="muted">Xưởng sẽ mở yêu cầu duyệt khi đủ điều kiện.</span>' : !mock && safeURL(p.publication.url) ? external(p.publication.url, "Mở video trên nền tảng") : '<span class="muted">Chưa có liên kết video thật.</span>'}</div></div></article>`;
      })
      .join("") ||
      emptyPanel(
        projects.length
          ? "Không có video ở trạng thái này"
          : "Chưa có video để xuất bản",
        projects.length
          ? "Chọn trạng thái khác để xem lịch đăng và video."
          : "Hoàn thiện bản dựng và các review. Video sẽ xuất hiện ở đây khi có bản cuối hoặc yêu cầu duyệt xuất bản.",
      ),
  );
  document
    .querySelectorAll("[data-publishing-project]")
    .forEach(
      (b) => (b.onclick = () => detail(b.dataset.publishingProject, "review")),
    );
  document
    .querySelectorAll("[data-video-preview]")
    .forEach((b) => (b.onclick = () => detail(b.dataset.videoPreview)));
  document.querySelectorAll("[data-publishing-gate]").forEach(
    (b) =>
      (b.onclick = () => {
        switchView("review");
        $("reason-" + b.dataset.publishingGate)?.focus();
      }),
  );
  bindDynamic();
}
function detailContent(p) {
  const phase = stageOf(p),
    final = latest(p, "final_video") || latest(p, "draft_video"),
    brief = p.request || p.brief;
  if (detailTab === "overview")
    return `<div class="project-workflow">${stages.map((s, i) => `<span class="project-step ${i < phase ? "done" : i === phase ? "current" : ""}"><span>${i < phase ? "✓" : i + 1}</span>${s.label}</span>`).join("")}</div>${final && final.provider !== "fake" ? `<video class="player" src="${esc(final.url)}" controls preload="metadata"></video>` : emptyPanel(state.demo ? "Đây là dự án mô phỏng" : "Bản dựng sẽ xuất hiện tại đây", state.demo ? "Bạn có thể thử workflow, đọc kịch bản và xem checklist. Tệp media demo không phải video thật." : "Xưởng đang thực hiện các bước sản xuất. Xem Kịch bản hoặc Cảnh & tư liệu để theo dõi.")}<div class="asset-links">${[
      "final_video",
      "draft_video",
      "captions",
    ]
      .map((k) => {
        const a = latest(p, k);
        return a && a.provider !== "fake"
          ? `<a class="button quiet" href="${esc(a.url)}" download="${esc(p.id)}.${k === "captions" ? "srt" : "mp4"}">↓ ${{ final_video: "Bản cuối MP4", draft_video: "Bản nháp MP4", captions: "Phụ đề SRT" }[k]}</a>`
          : "";
      })
      .join(
        "",
      )}</div><div class="content-card"><h3>Brief sáng tạo</h3>${cinemaBrief(p)}${brief ? `<div class="brief-grid"><div><small>ĐỐI TƯỢNG</small><p>${esc(brief.audience)}</p></div><div><small>ĐỊNH DẠNG</small><p>${p.format === "short" ? "Video ngắn · 9:16" : "Video dài · 16:9"}</p></div><div><small>THỜI LƯỢNG MỤC TIÊU</small><p>${esc(p.minutes)} phút</p></div><div><small>NGÂN SÁCH TEXT MODEL</small><p>${number(brief.budget_tokens)} token tối đa</p></div></div>${(brief.boundaries || []).length ? `<h4>Yêu cầu nội dung</h4><ul>${brief.boundaries.map((b) => `<li>${esc(b)}</li>`).join("")}</ul>` : ""}${p.brief?.angle ? `<p><strong>Góc tiếp cận:</strong> ${esc(p.brief.angle)}</p>` : ""}` : "<p>Chưa có brief trong dữ liệu đã lưu.</p>"}</div>`;
  if (detailTab === "script")
    return `${p.script ? `<div class="content-card"><div class="card-top"><span class="badge">KỊCH BẢN · V${esc(p.script.version)}</span><span class="muted">${number(p.script.word_count)} từ · ${esc(p.script.estimated_minutes)} phút ước lượng</span></div><h3>Hook mở đầu</h3><p class="script-text hook-text">${esc(p.script.hook)}</p>${(p.script.sections || []).map((s, i) => `<section class="script-section"><span class="eyebrow">PHẦN ${i + 1}</span><h3>${esc(s.heading)}</h3><p class="script-text">${esc(s.narration)}</p>${s.visual_notes ? `<div class="notice"><small>GỢI Ý HÌNH ẢNH</small><p>${esc(s.visual_notes)}</p></div>` : ""}</section>`).join("")}<h3>Lời kêu gọi hành động</h3><p class="script-text">${esc(p.script.cta || "Chưa có CTA")}</p></div>` : emptyPanel("Kịch bản đang được chuẩn bị", "Kịch bản sẽ hiển thị khi biên kịch hoàn thành bước này.")}<div class="content-card"><h3>Nguồn nghiên cứu & kiểm chứng</h3>${(p.research?.sources || []).length ? p.research.sources.map((s) => `<div class="timeline-row">${external(s.url, s.title || s.url)}<small>${esc(s.type || "Nguồn tham khảo")} ${s.accessed ? "· " + esc(s.accessed) : ""}</small></div>`).join("") : "<p>Chưa có danh sách nguồn trong hồ sơ nghiên cứu.</p>"}${(p.script?.claims || []).map((c) => `<div class="timeline-row"><p>${esc(c.text)}</p><small>${c.source ? external(c.source, c.source) : "Chưa có nguồn"} · ${c.needs_verification ? "Cần kiểm chứng" : "Đã khai báo"}</small></div>`).join("")}</div>`;
  if (detailTab === "scenes")
    return p.scenes.length
      ? `<div class="scene-list">${p.scenes
          .map((s, i) => {
            const visual = [...p.media]
              .reverse()
              .find((a) => a.scene_id === s.id && ["scene_image", "scene_video"].includes(a.kind));
            const clip = visual?.kind === "scene_video" ? visual : null;
            const audio = [...p.media]
              .reverse()
              .find((a) => a.scene_id === s.id && a.kind === "scene_audio");
            const provenance = visual?.provenance || {};
            return `<article class="scene">${visual && visual.provider !== "fake" ? (visual.kind === "scene_video" ? `<video src="${esc(visual.url)}" controls preload="metadata"></video>` : `<img src="${esc(visual.url)}" alt="Cảnh ${i + 1}" loading="lazy">`) : `<div class="scene-placeholder">${icon("media")}<span>${state.demo || visual?.provider === "fake" ? "Tư liệu mô phỏng" : "Đang chuẩn bị hình ảnh"}</span></div>`}<div class="scene-body"><div class="card-top"><span class="badge">CẢNH ${String(i + 1).padStart(2, "0")}</span><small>${Number(s.duration_s).toFixed(1)} giây ${s.locked ? "· Đã khóa" : ""}</small></div><p class="script-text">${esc(s.narration)}</p>${s.visual_prompt ? `<div class="storyboard-prompt"><small>STORYBOARD · CHỈ DẪN HÌNH ẢNH</small><p>${esc(s.visual_prompt)}</p></div>` : ""}${clip && clip.provider !== "fake" ? `<a class="scene-clip-link" href="${esc(clip.url)}" download="${esc(s.id)}.mp4">↓ Tải clip cảnh ${i + 1}</a>` : ""}${audio && audio.provider !== "fake" ? `<audio controls preload="none" src="${esc(audio.url)}"></audio>` : ""}${visual ? `<div class="asset-provenance"><small>NGUỒN & QUYỀN SỬ DỤNG</small><p>${esc(provenance.generated_by)} · ${esc(provenance.license)}</p>${provenance.source_url ? external(provenance.source_url, "Xem nguồn") : ""}<p>${esc(provenance.attribution)}</p></div>` : ""}</div></article>`;
          })
          .join("")}</div>`
      : emptyPanel(
          "Chưa có cảnh dựng",
          "Cảnh sẽ được tạo từ kịch bản đã hoàn thành.",
        );
  if (detailTab === "review")
    return `<div class="review-summary">${["fact", "rights", "quality"]
      .map((k) => {
        const r = p.reviews[k];
        return `<article class="content-card"><span class="badge ${r?.verdict === "pass" ? "success" : r ? "red" : ""}">${r ? esc({ pass: "Đạt", fail: "Cần sửa", block: "Đang chặn" }[r.verdict] || r.verdict) : "Chưa kiểm tra"}</span><h3>${{ fact: "Nội dung & dữ kiện", rights: "Quyền sử dụng tư liệu", quality: "Chất lượng video" }[k]}</h3>${r?.findings?.length ? `<ul>${r.findings.map((f) => `<li>${esc(f.text)}${f.location ? " · " + esc(f.location) : ""}</li>`).join("")}</ul>` : "<p>" + (r?.verdict === "pass" ? "Review không ghi nhận vấn đề." : "Kết quả sẽ xuất hiện sau bước kiểm tra.") + "</p>"}</article>`;
      })
      .join(
        "",
      )}</div><div class="content-card"><h3>Kiểm tra file thực tế</h3>${p.qc ? `<details><summary>Xem kết quả đo QC</summary><pre>${esc(JSON.stringify(p.qc, null, 2))}</pre></details>` : "<p>Chưa có kết quả QC. Dữ liệu demo không thay thế kiểm tra file thật.</p>"}</div><div class="content-card"><h3>Đóng gói xuất bản</h3>${p.metadata ? `<h4>${esc(p.metadata.title)}</h4><p class="script-text">${esc(p.metadata.description)}</p><div class="tag-list">${(p.metadata.tags || []).map((t) => `<span class="badge">${esc(t)}</span>`).join("")}</div>${(p.metadata.chapters || []).map((c) => `<div class="timeline-row"><strong>${esc(c.time)}</strong> ${esc(c.label)}</div>`).join("")}` : "<p>Tiêu đề, mô tả và chapters đang được chuẩn bị.</p>"}${p.publication ? `<div class="notice"><strong>${esc({ published: "Đã xuất bản", scheduled: "Đã lên lịch", failed: "Đăng thất bại", rolled_back: "Đã hoàn tác" }[p.publication.status] || p.publication.status)}</strong><p>${demoPublication(p) ? "Kết quả mô phỏng · chưa có video thật trên nền tảng." : p.publication.url ? external(p.publication.url, "Mở video trên nền tảng") : "Chưa có liên kết video từ nền tảng."}</p>${p.publication.scheduled_at ? `<p>${esc(new Date(p.publication.scheduled_at).toLocaleString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh" }))}</p>` : ""}</div>` : ""}<button class="button primary" data-open-publishing>Theo dõi xuất bản →</button></div>`;
  const perf = p.performance;
  return perf
    ? `<div class="notice">${state.demo ? "Số liệu trong môi trường demo là dữ liệu mô phỏng." : "Số liệu từ performance snapshot đã lưu."} Khoảng đo: ${esc(perf.window_days)} ngày. Một số chỉ số có thể bằng 0 khi nền tảng chưa cung cấp dữ liệu; đối chiếu YouTube Studio khi cần.</div><div class="analytics-grid">${[
        ["Lượt xem", number(perf.views)],
        ["Lượt hiển thị", number(perf.impressions)],
        ["CTR", `${(Number(perf.ctr) * 100).toFixed(2)}%`],
        [
          "Thời lượng xem TB",
          `${Number(perf.avg_view_duration_s).toFixed(1)} giây`,
        ],
        ["Lượt thích", number(perf.likes)],
        ["Bình luận", number(perf.comments)],
      ]
        .map(
          ([k, v]) =>
            `<article class="mini-stat"><small>${k}</small><strong>${v}</strong></article>`,
        )
        .join(
          "",
        )}</div><div class="content-card"><h3>Retention theo các mốc đồng bộ</h3>${perf.retention_curve?.length ? `<div class="retention-list">${perf.retention_curve.map((pt) => `<div class="retention-row"><span>Mốc ${esc(pt.t)}</span><meter min="0" max="100" value="${Math.min(100, Math.max(0, Number(pt.pct)))}">${esc(pt.pct)}%</meter><strong>${Number(pt.pct).toFixed(1)}%</strong></div>`).join("")}</div>` : "<p>Chưa có dữ liệu retention.</p>"}</div>`
    : emptyPanel(
        "Chưa đồng bộ số liệu",
        "Sau khi đăng, đồng bộ dữ liệu YouTube để xem lượt xem, CTR và retention. SOP khuyến nghị kiểm tra sau 24 giờ, 7 ngày và 28 ngày.",
      );
}
function detail(id, tab = "overview") {
  current = id;
  detailTab = tab;
  const p = state?.projects.find((p) => p.id === id);
  if (!p) return;
  renderDetail(true);
  if (!$("detail-dialog").open) $("detail-dialog").showModal();
}
function renderDetail(force = false) {
  const p = state?.projects.find((p) => p.id === current);
  if (!p) return;
  const signature = JSON.stringify([p, detailTab]);
  if (!force && signature === detailSignature) return;
  // Preserve video/audio playback and reading position while background stages update.
  if (
    !force &&
    $("detail-dialog").open &&
    [...$("detail-body").querySelectorAll("video,audio")].some(
      (media) => !media.paused && !media.ended,
    )
  )
    return;
  detailSignature = signature;
  html(
    "detail-body",
    `<div class="detail-heading"><span class="badge">${p.format === "short" ? "VIDEO NGẮN · 9:16" : "VIDEO DÀI · 16:9"}</span>${productionBadges(p)}<h2>${esc(p.title)}</h2><p>${esc(states[p.state] || p.state)} · ${p.scenes.length} cảnh · ${esc(p.minutes)} phút mục tiêu</p></div><div class="detail-tabs" role="tablist" aria-label="Thông tin dự án">${[
      ["overview", "Tổng quan"],
      ["script", "Kịch bản"],
      ["scenes", "Cảnh & tư liệu"],
      ["review", "Kiểm tra & đăng"],
      ["analytics", "Số liệu"],
    ]
      .map(
        ([key, label]) =>
          `<button class="detail-tab ${detailTab === key ? "selected" : ""}" role="tab" aria-selected="${detailTab === key}" tabindex="${detailTab === key ? 0 : -1}" aria-controls="detail-panel" data-detail-tab="${key}">${label}</button>`,
      )
      .join(
        "",
      )}</div><div id="detail-panel" class="detail-panel" role="tabpanel">${detailContent(p)}</div>`,
  );
  document.querySelectorAll("[data-detail-tab]").forEach(
    (b) =>
      (b.onclick = () => {
        detailTab = b.dataset.detailTab;
        renderDetail(true);
        $("detail-body")
          .querySelector(`[data-detail-tab="${detailTab}"]`)
          .focus();
      }),
  );
  document.querySelectorAll("[data-detail-tab]").forEach((button) => {
    button.onkeydown = (event) => {
      const tabs = [...document.querySelectorAll("[data-detail-tab]")];
      const index = tabs.indexOf(button);
      const targets = {
        ArrowRight: (index + 1) % tabs.length,
        ArrowLeft: (index + tabs.length - 1) % tabs.length,
        Home: 0,
        End: tabs.length - 1,
      };
      if (Object.hasOwn(targets, event.key)) {
        event.preventDefault();
        tabs[targets[event.key]].click();
      }
    };
  });
  if (document.querySelector("[data-open-publishing]"))
    document.querySelector("[data-open-publishing]").onclick = () => {
      $("detail-dialog").close();
      switchView("publishing");
    };
}
const ideaKey = "xstudio.ideas.v1";
let ideaStorageError = "";
let ideas = [];
try {
  const saved = JSON.parse(localStorage.getItem(ideaKey) || "[]");
  if (!Array.isArray(saved)) throw Error();
  ideas = saved
    .filter((i) => i && typeof i.id === "string" && typeof i.title === "string")
    .slice(0, 200);
} catch {
  ideaStorageError =
    "Không thể đọc kho ý tưởng của trình duyệt. Bạn vẫn có thể tạo dự án trực tiếp.";
}
function saveIdeas(next) {
  try {
    localStorage.setItem(ideaKey, JSON.stringify(next));
    ideas = next;
    return true;
  } catch {
    toast(
      "Trình duyệt không cho phép lưu ý tưởng. Hãy tạo dự án trực tiếp hoặc bật lưu trữ.",
    );
    return false;
  }
}
function renderIdeas() {
  $("idea-count").textContent = ideas.length;
  const query = $("idea-search").value.toLocaleLowerCase("vi");
  const items = ideas.filter((i) =>
    i.title.toLocaleLowerCase("vi").includes(query),
  );
  html(
    "idea-list",
    items.length
      ? items
          .map(
            (i) =>
              `<article class="idea-card"><div class="card-top"><span class="badge ${i.project_id ? "success" : ""}">${i.project_id ? "Đã tạo dự án" : i.command_id ? "Đang xử lý yêu cầu" : i.format === "short" ? "VIDEO NGẮN" : "VIDEO DÀI"}</span><small>${esc(date(i.created_at))}</small></div><h3>${esc(i.title)}</h3><p>${esc(i.hook || i.notes || "Một câu chuyện đang chờ được phát triển.")}</p><small>${esc(i.audience || "Chưa xác định khán giả")}</small><div class="idea-actions">${i.project_id && state?.projects.some((p) => p.id === i.project_id) ? `<button class="button primary" data-project="${esc(i.project_id)}">Mở dự án →</button>` : i.command_id ? '<span class="muted">' + (i.error ? "Yêu cầu gặp lỗi · có thể thử lại" : "Chờ xưởng tạo dự án") + "</span>" : `<button class="button primary" data-convert="${esc(i.id)}">Phát triển thành video →</button>`}<button class="button quiet" data-edit-idea="${esc(i.id)}">Sửa</button><button class="idea-delete" data-delete-idea="${esc(i.id)}" aria-label="Xóa ý tưởng ${esc(i.title)}">×</button></div></article>`,
          )
          .join("")
      : emptyPanel(
          ideas.length
            ? "Không tìm thấy ý tưởng"
            : "Bắt đầu bằng một tia cảm hứng",
          ideas.length
            ? "Thử một từ khóa khác."
            : "Lưu câu hỏi, hook hoặc chủ đề bạn muốn kể. Khi sẵn sàng, chuyển ý tưởng thành video.",
        ),
  );
  if (ideaStorageError)
    $("idea-list").insertAdjacentHTML(
      "afterbegin",
      `<p class="notice error">${esc(ideaStorageError)}</p>`,
    );
  document
    .querySelectorAll("[data-edit-idea]")
    .forEach((b) => (b.onclick = () => openIdea(b.dataset.editIdea)));
  document.querySelectorAll("[data-delete-idea]").forEach(
    (b) =>
      (b.onclick = () => {
        const old = ideas.find((i) => i.id === b.dataset.deleteIdea);
        if (saveIdeas(ideas.filter((i) => i.id !== b.dataset.deleteIdea))) {
          renderIdeas();
          if (state) renderDashboard();
          toast("Đã xóa ý tưởng.");
          if (old) {
            $("toast").append(document.createTextNode(" "));
            const undo = document.createElement("button");
            undo.textContent = "Hoàn tác";
            undo.className = "toast-undo";
            undo.onclick = () => {
              if (saveIdeas([old, ...ideas])) {
                renderIdeas();
                if (state) renderDashboard();
                toast("Đã khôi phục ý tưởng.");
              }
            };
            $("toast").append(undo);
          }
        }
      }),
  );
  document.querySelectorAll("[data-convert]").forEach(
    (b) =>
      (b.onclick = () => {
        const i = ideas.find((i) => i.id === b.dataset.convert);
        openCreate(i);
      }),
  );
  bindProjects();
}
function openIdea(id = null) {
  editingIdea = id;
  const form = $("idea-form");
  form.reset();
  const idea = ideas.find((i) => i.id === id);
  if (idea)
    for (const key of ["title", "audience", "hook", "format", "notes"])
      if (form.elements[key]) form.elements[key].value = idea[key] || "";
  $("idea-error").textContent = "";
  $("idea-dialog").showModal();
}
function openCreate(idea = null) {
  convertedIdea = idea?.id || null;
  $("create-form").reset();
  $("create-error").textContent = "";
  setPreset(idea?.format || "long");
  if (idea)
    for (const key of ["title", "audience", "hook"])
      $("create-form").elements[key].value = idea[key] || "";
  if (idea?.notes) $("create-form").elements.boundaries.value = idea.notes;
  syncProductionOptions();
  $("create-dialog").showModal();
}
function syncProductionOptions() {
  const f = $("create-form").elements;
  const cinematic = f.production_type.value !== "explainer";
  $("cinema-fields").hidden = !cinematic;
  $("cinema-fields").disabled = !cinematic;
  const provider = state?.scene_video_provider || "none";
  const ready = Boolean(state?.scene_video_ready) && provider !== "none";
  const option = f.visual_mode.querySelector('[value="generated_video"]');
  option.disabled = !ready;
  option.textContent = provider === "fake"
    ? "Clip AI · mô phỏng"
    : "Clip AI (ComfyUI / lệnh cục bộ)";
  $("scene-video-note").classList.toggle("simulated", provider === "fake");
  $("scene-video-note").textContent = !state
    ? "Đang đọc cấu hình tạo clip của xưởng."
    : !ready
      ? "Chưa cấu hình bộ tạo clip. Ảnh có chuyển động vẫn có thể dùng cho phim và hoạt hình."
      : provider === "fake"
        ? "Mô phỏng tạo clip: chỉ thử quy trình, không tạo phim thật."
        : `Đã cấu hình ${provider === "comfyui" ? "ComfyUI" : provider === "command" ? "lệnh tạo clip cục bộ" : provider}. Khả năng kết nối và tài nguyên sẽ được kiểm tra khi tạo clip.`;
  syncStylePresets();
}
function syncStylePresets() {
  const value = $("create-form").elements.visual_style.value;
  document.querySelectorAll("[data-style-preset]").forEach((button) =>
    button.setAttribute("aria-pressed", String(value === stylePresets[button.dataset.stylePreset])),
  );
}
function setPreset(format) {
  const f = $("create-form").elements;
  f.format.value = format;
  f.target_minutes.value = format === "short" ? "0.8" : "8";
  document.querySelectorAll("[data-preset]").forEach((b) => {
    b.classList.toggle("selected", b.dataset.preset === format);
    b.setAttribute("aria-pressed", String(b.dataset.preset === format));
  });
}
function syncIdeas() {
  let changed = false;
  const next = ideas.map((i) => {
    if (!i.command_id || i.project_id) return i;
    const command = state.commands.find((c) => c.id === i.command_id);
    const p = state.projects.find(
      (p) =>
        p.brief?.channel_id ===
        "P" + i.command_id.replace(/[^a-zA-Z0-9]/g, "").slice(0, 24),
    );
    if (p) {
      changed = true;
      return { ...i, project_id: p.id };
    }
    if (command?.status === "error" || command?.planning_error) {
      changed = true;
      return {
        ...i,
        command_id: null,
        error: command.planning_error || command.error,
      };
    }
    return i;
  });
  if (changed && saveIdeas(next)) renderIdeas();
}
function bindDynamic() {
  document
    .querySelectorAll("[data-view]")
    .forEach((b) => (b.onclick = () => switchView(b.dataset.view)));
  document.querySelectorAll("[data-guide]").forEach(
    (b) =>
      (b.onclick = () => {
        guideStep = Number(b.dataset.guide);
        switchView("workflow");
      }),
  );
  document
    .querySelectorAll("[data-create]")
    .forEach((b) => (b.onclick = () => openCreate()));
  bindProjects();
}
function render() {
  const projects = state.projects;
  syncProductionOptions();
  $("stat-active").textContent = projects.filter(
    (p) =>
      !latest(p, "final_video") &&
      !["closed", "blocked", "escalated", "published", "analyzed"].includes(
        p.state,
      ),
  ).length;
  $("stat-review").textContent = state.gates.length;
  $("stat-done").textContent = projects.filter((p) =>
    latest(p, "final_video"),
  ).length;
  $("stat-mode").textContent = modes[state.automation] || state.automation;
  $("resource-note").textContent =
    `${state.threads} luồng CPU · ${state.render_mode === "bounded" ? "Dựng từng cảnh" : "Dựng theo đồ thị"}`;
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
  html(
    "activity-list",
    state.events
      .map(
        (e) =>
          `<div class="activity-row"><time>${esc(new Date(e.time).toLocaleTimeString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh", hour: "2-digit", minute: "2-digit" }))}<small>${esc(date(e.time))}</small></time><span>${esc(e.action)} ${e.video_id ? "· " + esc(projects.find((p) => p.id === e.video_id)?.title || e.video_id) : ""}</span><span class="badge">${esc(e.actor)}</span></div>`,
      )
      .join("") ||
      emptyPanel(
        "Chưa có hoạt động",
        "Nhật ký sẽ xuất hiện khi dự án đầu tiên bắt đầu.",
      ),
  );
  html(
    "settings-list",
    Object.entries({
      "Lời đọc": state.providers.tts,
      "Hình ảnh": state.providers.image,
      "Clip từng cảnh": state.scene_video_provider === "fake"
        ? "Mô phỏng"
        : state.scene_video_ready
          ? `${state.scene_video_provider} · đã cấu hình`
          : "Chưa cấu hình",
      "Dựng video": state.providers.video,
      "Nền tảng": state.platform,
      "Vận hành": modes[state.automation],
      "Tài nguyên": `${state.threads} luồng CPU · ${state.render_mode}`,
    })
      .map(
        ([k, v]) =>
          `<div class="setting"><small>${k}</small><strong>${esc(v)}</strong></div>`,
      )
      .join(""),
  );
  const errors = state.commands.filter(
    (c) => c.status === "error" || c.planning_error,
  );
  html(
    "command-errors",
    errors
      .map(
        (c) =>
          `<p class="notice error">Yêu cầu ${esc(c.kind)}: ${esc(c.planning_error || c.error)}</p>`,
      )
      .join("") +
      (state.worker_error
        ? `<p class="notice error">${esc(state.worker_error)}</p>`
        : ""),
  );
  for (const c of errors)
    if (!notified.has(c.id)) {
      notified.add(c.id);
      toast(c.planning_error || c.error);
    }
  if (pendingSettings) {
    const applied = Object.entries(pendingSettings.values).every(
      ([key, value]) => String(state[key]) === String(value),
    );
    const failed = state.commands.some(
      (c) => c.id === pendingSettings.id && c.status === "error",
    );
    if (applied) settingsDirty = false;
    if (applied || failed) pendingSettings = null;
  }
  if (!settingsDirty && !$("settings-form").contains(document.activeElement)) {
    const f = $("settings-form").elements;
    for (const key of ["automation", "threads", "style", "burn_captions"])
      f[key].value = String(state[key] ?? "");
  }
  syncIdeas();
  renderProjects();
  renderDashboard();
  renderGates();
  renderPublishing();
  if ($("detail-dialog").open) renderDetail();
}
async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    state = await api("/api/state");
    $("connection").textContent = "● Đã kết nối";
    render();
  } catch (e) {
    $("connection").textContent = "○ Mất kết nối";
    if (!$("login-dialog").open) $("command-errors").textContent = e.message;
  } finally {
    refreshing = false;
  }
}
bindDynamic();
for (const b of document.querySelectorAll("[data-close]"))
  b.onclick = () => $(b.dataset.close).close();
for (const id of ["new-project", "empty-create"])
  $(id).onclick = () => openCreate();
$("new-idea").onclick = () => openIdea();
$("idea-search").oninput = renderIdeas;
for (const b of document.querySelectorAll("[data-filter]"))
  b.onclick = () => {
    filter = b.dataset.filter;
    document
      .querySelectorAll("[data-filter]")
      .forEach((t) => t.classList.toggle("selected", t === b));
    renderProjects();
  };
for (const b of document.querySelectorAll("[data-publishing-filter]"))
  b.onclick = () => {
    publishingFilter = b.dataset.publishingFilter;
    document.querySelectorAll("[data-publishing-filter]").forEach((t) => {
      t.classList.toggle("selected", t === b);
      t.setAttribute("aria-pressed", String(t === b));
    });
    if (state) renderPublishing();
  };
for (const b of document.querySelectorAll("[data-layout]"))
  b.onclick = () => {
    layout = b.dataset.layout;
    document.querySelectorAll("[data-layout]").forEach((t) => {
      t.classList.toggle("selected", t === b);
      t.setAttribute("aria-pressed", String(t === b));
    });
    renderProjects();
  };
for (const b of document.querySelectorAll("[data-preset]"))
  b.onclick = () => setPreset(b.dataset.preset);
$("create-form").elements.format.onchange = (e) => setPreset(e.target.value);
$("create-form").elements.production_type.onchange = () => {
  const f = $("create-form").elements;
  if (f.production_type.value !== "explainer") {
    if (f.goal.value === "education") f.goal.value = "entertainment";
    if (f.audience.value === "Người muốn học hỏi")
      f.audience.value = "Khán giả yêu thích câu chuyện";
  }
  syncProductionOptions();
};
$("create-form").elements.visual_mode.onchange = syncProductionOptions;
$("create-form").elements.visual_style.oninput = syncStylePresets;
for (const button of document.querySelectorAll("[data-style-preset]"))
  button.onclick = () => {
    $("create-form").elements.visual_style.value = stylePresets[button.dataset.stylePreset];
    syncStylePresets();
  };
$("search").oninput = renderProjects;
$("run-toggle").onclick = () => {
  if (state)
    command(state.running ? "pause" : "run").catch((e) => toast(e.message));
};
$("login-dialog").addEventListener("cancel", (e) => e.preventDefault());
$("login-form").onsubmit = async (e) => {
  e.preventDefault();
  const button = e.target.querySelector("button");
  button.disabled = true;
  try {
    await api("/api/login", { token: new FormData(e.target).get("token") });
    $("login-dialog").close();
    e.target.reset();
    $("login-error").textContent = "";
    await refresh();
  } catch (err) {
    $("login-error").textContent = err.message;
  } finally {
    button.disabled = false;
  }
};
$("idea-form").onsubmit = (e) => {
  e.preventDefault();
  const values = Object.fromEntries(new FormData(e.target));
  values.title = values.title.trim();
  if (!values.title) {
    $("idea-error").textContent = "Hãy đặt tên cho ý tưởng.";
    return;
  }
  if (!editingIdea && ideas.length >= 200) {
    $("idea-error").textContent =
      "Kho đã có 200 ý tưởng. Xóa bớt ý tưởng trước khi thêm.";
    return;
  }
  const old = ideas.find((i) => i.id === editingIdea);
  const idea = {
    ...old,
    ...values,
    id: editingIdea || crypto.randomUUID(),
    created_at: old?.created_at || new Date().toISOString(),
  };
  if (
    saveIdeas(
      old
        ? ideas.map((i) => (i.id === editingIdea ? idea : i))
        : [idea, ...ideas],
    )
  ) {
    $("idea-dialog").close();
    renderIdeas();
    if (state) renderDashboard();
    toast("Đã lưu ý tưởng trên trình duyệt này.");
  }
};
$("create-form").onsubmit = async (e) => {
  e.preventDefault();
  const raw = Object.fromEntries(new FormData(e.target));
  const goal = {
    education: "Giáo dục / giải thích",
    inspiration: "Truyền cảm hứng",
    conversion: "Chuyển đổi",
    entertainment: "Giải trí",
  };
  const tone = {
    friendly: "Gần gũi, dễ hiểu",
    documentary: "Khách quan, tài liệu",
    energetic: "Năng lượng, nhanh gọn",
    reflective: "Chậm rãi, suy ngẫm",
    playful: "Vui vẻ, dí dỏm",
  };
  const additions = [
    raw.goal ? "Mục tiêu: " + (goal[raw.goal] || raw.goal) : "",
    raw.hook ? "Hook mong muốn: " + raw.hook : "",
    raw.message ? "Thông điệp chính: " + raw.message : "",
    raw.tone ? "Giọng điệu: " + (tone[raw.tone] || raw.tone) : "",
  ].filter(Boolean);
  const boundaries = [
    ...raw.boundaries
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean),
    ...additions,
  ];
  if (boundaries.length > 20) {
    $("create-error").textContent =
      "Tối đa 20 yêu cầu nội dung, bao gồm mục tiêu, hook và giọng điệu.";
    return;
  }
  const values = {
    title: raw.title.trim(),
    audience: raw.audience,
    target_minutes: Number(raw.target_minutes),
    format: raw.format,
    language: raw.language,
    budget_tokens: Number(raw.budget_tokens),
    boundaries,
    production_type: raw.production_type || "explainer",
    visual_mode: e.target.elements.visual_mode.value || "images",
    visual_style: (raw.visual_style || "").trim(),
    logline: (raw.logline || "").trim(),
    character_bible: (raw.character_bible || "").trim(),
    negative_prompt: (raw.negative_prompt || "").trim(),
    seed: Number(raw.seed || 0),
  };
  if (values.visual_mode === "generated_video" &&
      (!state?.scene_video_ready || !state.scene_video_provider || state.scene_video_provider === "none")) {
    $("create-error").textContent = "Chưa cấu hình bộ tạo clip. Chọn Ảnh có chuyển động hoặc cấu hình ComfyUI / lệnh tạo clip cục bộ.";
    return;
  }
  const button = e.target.querySelector("[type=submit]");
  button.disabled = true;
  try {
    const result = await command("create", values);
    if (convertedIdea)
      saveIdeas(
        ideas.map((i) =>
          i.id === convertedIdea
            ? { ...i, command_id: result.id, error: null }
            : i,
        ),
      );
    $("create-dialog").close();
    e.target.reset();
    renderIdeas();
    switchView("projects");
  } catch (err) {
    $("create-error").textContent = err.message;
  } finally {
    button.disabled = false;
  }
};
$("settings-form").oninput = $("settings-form").onchange = () => {
  settingsDirty = true;
  pendingSettings = null;
};
$("settings-form").onsubmit = async (e) => {
  e.preventDefault();
  const values = Object.fromEntries(new FormData(e.target));
  values.threads = Number(values.threads);
  values.burn_captions = values.burn_captions === "true";
  const b = e.target.querySelector("[type=submit]");
  b.disabled = true;
  const pending = { values, id: null };
  pendingSettings = pending;
  try {
    const result = await command("settings", values);
    pending.id = result.id;
  } catch (err) {
    if (pendingSettings === pending) pendingSettings = null;
    toast(err.message);
  } finally {
    b.disabled = false;
  }
};
$("date-label").textContent = new Date().toLocaleDateString("vi-VN", {
  timeZone: "Asia/Ho_Chi_Minh",
  weekday: "long",
  day: "numeric",
  month: "long",
});
renderIdeas();
renderGuide();
switchView(
  views[location.hash.slice(1)] ? location.hash.slice(1) : "dashboard",
);
window.addEventListener("hashchange", () =>
  switchView(
    views[location.hash.slice(1)] ? location.hash.slice(1) : "dashboard",
  ),
);
refresh();
setInterval(() => {
  if (!$("login-dialog").open && !document.hidden) refresh();
}, 3000);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && !$("login-dialog").open) refresh();
});
window.addEventListener("storage", (e) => {
  if (e.key === ideaKey) {
    try {
      const saved = JSON.parse(e.newValue || "[]");
      if (Array.isArray(saved)) {
        ideas = saved
          .filter(
            (i) => i && typeof i.id === "string" && typeof i.title === "string",
          )
          .slice(0, 200);
        renderIdeas();
        if (state) renderDashboard();
      }
    } catch {
      toast("Không thể đọc kho ý tưởng được cập nhật.");
    }
  }
});
