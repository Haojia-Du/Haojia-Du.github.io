const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
let snapshot = null;
let lastEvent = 0;
let selectedPaths = [];
let toastTimer = null;
let noteContext = null;
let courseSignature = "";

const profileNames = { economics: "经济学", stem: "理工科", humanities: "人文社科", law: "法学", language: "语言课程", general: "通用课程" };
const stages = ["collecting", "downloading", "generating", "done"];

async function api(path, options = {}) {
  const init = { headers: { "Content-Type": "application/json" }, ...options };
  if (init.body && typeof init.body !== "string") init.body = JSON.stringify(init.body);
  const response = await fetch(path, init);
  const data = await response.json();
  if (!response.ok || !data.ok) throw new Error(data.error || `请求失败：${response.status}`);
  return data;
}

function notify(message, error = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.className = `toast show${error ? " error" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.className = "toast", 3400);
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
}

function timeText(timestamp) {
  if (!timestamp) return "—";
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(timestamp * 1000));
}

function statusName(status) {
  return { final: "已定稿", draft: "已有初稿", offline: "证据包", pending: "待处理" }[status] || status;
}

function actionButton(label, action, lecture, extraClass = "") {
  return `<button class="${extraClass}" data-action="${action}" data-lecture="${encodeURIComponent(lecture)}">${label}</button>`;
}

function renderLectures(lectures) {
  const body = $("#lecture-body");
  if (!lectures.length) {
    body.innerHTML = `<tr><td colspan="5" class="empty">尚未导入课程材料。可以从上方选择录播一键处理，或在下方导入其他材料。</td></tr>`;
    return;
  }
  body.innerHTML = lectures.map(item => {
    const actions = [
      item.has_draft ? actionButton("在此阅读", "open-draft", item.name) : actionButton("正式生成", "build", item.name),
      item.status === "offline" ? actionButton("正式生成", "build", item.name, "secondary") : "",
      ["failed", "running"].includes(item.validation?.status) ? actionButton("重新检验并续跑", "build", item.name, "secondary") : "",
      item.has_final ? actionButton("阅读定稿", "open-final", item.name) : "",
      item.has_qa ? actionButton("查看质检", "open-qa", item.name, "secondary") : "",
      item.has_draft ? actionButton("定稿", "finalize", item.name, "secondary") : actionButton("离线提取", "offline", item.name, "secondary"),
      actionButton("文件夹", "open-folder", item.name, "secondary")
    ].join("");
    const check = item.validation || {};
    const labels = {failed:"检验失败", warning:"待人工核对", running:"尚未完成检验", passed:"结构检验通过"};
    const checkText = check.error || (check.warnings || []).join("；");
    const checkHtml = check.status ? `<details><summary>${escapeHtml(labels[check.status] || "未检验")}</summary><p>${escapeHtml(check.stage || "")}：${escapeHtml(checkText || "未检出结构异常，不代表语义完全正确。")}</p>${check.suggestion ? `<p>${escapeHtml(check.code)}：${escapeHtml(check.suggestion)}</p>` : ""}</details>` : "";
    const stateText = check.status === "failed" ? "处理未通过" : statusName(item.status);
    return `<tr><td>${escapeHtml(item.name)}</td><td>${item.sources} 个</td><td><span class="status-pill ${item.status}">${stateText}</span>${checkHtml}</td><td>${timeText(item.updated_at)}</td><td><div class="table-actions">${actions}</div></td></tr>`;
  }).join("");
}

function fillCourses(courses) {
  const signature = JSON.stringify(courses.map(c => [c.course_id, c.name, (c.recordings || []).length]));
  if (signature === courseSignature) return;
  courseSignature = signature;
  const selected = $("#course-select").value;
  $("#course-select").innerHTML = `<option value="">选择课程</option>` + courses.map(course => `<option value="${escapeHtml(course.course_id)}">${escapeHtml(course.name)}（${(course.recordings || []).length} 节）</option>`).join("");
  if (courses.some(c => String(c.course_id) === selected)) $("#course-select").value = selected;
  fillRecordings();
}

function fillRecordings() {
  const select = $("#recording-select");
  const course = snapshot?.recorder.courses.find(item => String(item.course_id) === $("#course-select").value);
  if (!course) {
    select.innerHTML = `<option value="">先选择课程</option>`;
    select.disabled = true;
    if (typeof renderBatchSelection === "function") renderBatchSelection();
    return;
  }
  select.disabled = false;
  select.innerHTML = `<option value="">选择一节录播</option>` + (course.recordings || []).map((item, index) => {
    const detail = [item.time, item.teacher].filter(Boolean).join(" · ");
    return `<option value="${index}">${escapeHtml(item.name || `第 ${index + 1} 节`)}${detail ? `｜${escapeHtml(detail)}` : ""}</option>`;
  }).join("");
  if (typeof renderBatchSelection === "function") renderBatchSelection();
}

function renderRecorder(recorder, job) {
  const chip = $("#recorder-chip");
  const state = recorder.logged_in ? "logged" : (recorder.running ? "ready" : "off");
  chip.className = `connection-chip ${state}`;
  chip.querySelector("b").textContent = recorder.logged_in ? `已登录${recorder.username ? ` · ${recorder.username}` : ""}` : (recorder.running ? "等待登录" : "尚未连接");
  $("#recorder-start").classList.toggle("hidden", recorder.running);
  $("#login-form").classList.toggle("hidden", !recorder.running || recorder.logged_in);
  $("#one-click-form").classList.toggle("hidden", !recorder.logged_in);
  if (recorder.semester && !$("#pku-semester").value) $("#pku-semester").value = recorder.semester;
  fillCourses(recorder.courses || []);
  const profileSelect = $("#profile-select");
  if (!profileSelect.dataset.initialized) {
    profileSelect.value = snapshot?.course.profile || "general";
    profileSelect.dataset.initialized = "true";
  }

  const current = recorder.one_click_stage || "idle";
  const activeIndex = stages.indexOf(current);
  $$("#stage-track [data-stage]").forEach((node, index) => {
    node.classList.toggle("active", index === activeIndex && current !== "done");
    node.classList.toggle("complete", current === "done" || (activeIndex >= 0 && index < activeIndex));
    node.classList.toggle("error", current === "error" && index === Math.max(0, activeIndex));
  });
  $("#stage-track").classList.toggle("failed", current === "error");
  $("#run-one-click").textContent = job.running && job.action.includes("一键") ? "正在自动处理…" : "一键生成学习资料";
}

function render(data) {
  snapshot = data;
  const { course, environment, lectures, job, watcher, recorder } = data;
  $("#side-course").textContent = course.title;
  $("#subtitle").textContent = `${course.title} · ${profileNames[course.profile] || course.profile} · 本机学习台`;
  $("#config-title").textContent = course.title;
  $("#config-profile").textContent = profileNames[course.profile] || course.profile;
  $("#config-path").textContent = course.path;

  const keyStatus = environment.api_key_status;
  $("#api-status").textContent = keyStatus === "valid" ? "可用" : (keyStatus === "invalid" ? "无效" : (environment.api_key_present ? "已配置" : "未配置"));
  $("#api-detail").textContent = keyStatus === "valid" ? "连接验证通过" : (keyStatus === "invalid" ? "请在连接设置中更新" : (environment.api_key_present ? "可在连接设置中测试" : "请在下方连接设置中填写"));
  $("#downloader-status").textContent = recorder.running ? (recorder.logged_in ? "已登录" : "已启动") : (environment.downloader_exists ? "可启动" : "未配置");
  $("#downloader-detail").textContent = recorder.logged_in ? (recorder.semester || "教学网会话有效") : "本机 PKU-Recorder";
  $("#material-count").textContent = environment.detected_materials;
  $("#lecture-count").textContent = lectures.length;
  const finished = lectures.filter(item => ["draft", "final"].includes(item.status)).length;
  $("#lecture-summary").textContent = `${finished} 个已有正式初稿`;
  renderLectures(lectures);
  renderRecorder(recorder, job);

  const chip = $("#job-chip");
  chip.className = `job-chip ${job.running ? "running" : "idle"}`;
  chip.innerHTML = `<span></span>${job.running ? escapeHtml(job.action) : (job.error ? "上次任务失败" : "空闲")}`;
  $$(`button[data-action], #sync-online, #sync-offline, #import-files, #check-key, #start-recorder, #run-one-click, #reload-courses, #login-form button`).forEach(button => button.disabled = job.running || watcher.running);
  const watchButton = $("#toggle-watch");
  watchButton.querySelector("span:last-child").textContent = watcher.running ? "停止自动处理" : "开启自动处理";
  watchButton.classList.toggle("watching", watcher.running);
  $("#task-error").classList.toggle("hidden", !job.error || job.running);
    $("#task-error-message").textContent = (job.error || "") + (job.failure ? ` ${job.failure.code}：${job.failure.suggestion}` : "");
  $("#retry-task").classList.toggle("hidden", !job.can_retry);
  if (typeof updateStudyStatus === "function") updateStudyStatus(job);
  if (typeof updateOptionalFeatures === "function") updateOptionalFeatures(data);
    if (typeof updateFirstRun === "function") updateFirstRun(data);
  if (typeof updateSimpleView === "function") updateSimpleView(data);
}

async function refresh() {
  try { render(await api("/api/status")); }
  catch (error) {
    if (typeof showSimpleConnectionError === "function") showSimpleConnectionError();
    else notify(error.message, true);
  }
}

async function pollEvents() {
  try {
    const data = await api(`/api/events?after=${lastEvent}`);
    const terminal = $("#terminal");
    if (data.events.length && terminal.querySelector(".muted")) terminal.innerHTML = "";
    for (const event of data.events) {
      lastEvent = Math.max(lastEvent, event.id);
      const line = document.createElement("div");
      line.className = `log ${event.level}`;
      line.dataset.time = event.time;
      line.textContent = event.message;
      terminal.appendChild(line);
    }
    if (data.events.length) terminal.scrollTop = terminal.scrollHeight;
  } catch (_) {}
}

async function post(path, body = {}, message = "任务已提交") {
  try {
      const guarded = ["/api/simple/start", "/api/one-click", "/api/batch/start", "/api/batch/resume", "/api/sample", "/api/build", "/api/sync", "/api/review/generate", "/api/watch/start"].includes(path);
      if (guarded && typeof taskRequestLimit === "function") body.request_limit = taskRequestLimit();
      if (guarded && path !== "/api/sample" && !body.offline) {
        const scope = path === "/api/watch/start" ? "整个自动监听会话累计" : "本次任务";
        const notice = ["/api/simple/start", "/api/one-click"].includes(path) ? "将使用已连接的 AI 整理这些材料，可能产生费用。是否开始？" : `将向配置的 AI 服务发送课程音频或提取内容，可能产生费用。${scope}请求上限 ${body.request_limit || 100} 次，不是金额上限。确认开始？`;
        if (!confirm(notice)) return null;
        body.confirmed = true;
      }
      if (path === "/api/retry" && !confirm("重试会按原任务的请求上限重新分配额度，已完成内容会复用，可能继续产生费用。确认继续？")) return null;
    const data = await api(path, { method: "POST", body });
    notify(message);
    await refresh();
    return data;
  } catch (error) { notify(error.message, true); return null; }
}

async function openNote(lecture, kind) {
  try {
    const data = await api(`/api/note?lecture=${encodeURIComponent(lecture)}&kind=${encodeURIComponent(kind)}`);
    noteContext = { lecture, kind };
    $("#note-title").textContent = lecture;
    $("#note-kind").textContent = kind === "qa" ? "QUALITY REPORT · 质检报告" : (kind === "final" ? "FINAL NOTE · 定稿" : "MARKDOWN NOTE · 初稿");
    $("#note-path").textContent = data.path;
    $("#note-editor").value = data.content;
    $("#note-editor").readOnly = kind === "qa";
    $("#save-draft").classList.toggle("hidden", kind === "qa");
    $("#save-final").classList.toggle("hidden", kind === "qa");
    $("#note-modal").classList.remove("hidden");
    document.body.classList.add("modal-open");
    await loadStudyDesk(lecture, kind, data.content);
  } catch (error) { notify(error.message, true); }
}

async function saveNote(finalize) {
  if (!noteContext) return;
  finalize = finalize || noteContext.kind === "final";
  const result = await post("/api/note/save", { lecture: noteContext.lecture, content: $("#note-editor").value, finalize }, finalize ? "已保存为定稿" : "修改已保存");
  if (result && finalize) noteContext.kind = "final";
  if (result) renderNotePreview();
}

document.addEventListener("click", event => {
  const button = event.target.closest("button");
  if (!button) return;
  const action = button.dataset.action;
  const lecture = button.dataset.lecture ? decodeURIComponent(button.dataset.lecture) : "";
  if (action === "build") post("/api/build", { lecture, offline: false, force: false }, "正式生成任务已提交");
  if (action === "offline") post("/api/build", { lecture, offline: true, force: false }, "离线提取任务已提交");
  if (action === "finalize") post("/api/finalize", { lecture }, "已提交定稿");
  if (action === "open-draft") openNote(lecture, "draft");
  if (action === "open-final") openNote(lecture, "final");
  if (action === "open-qa") openNote(lecture, "qa");
  if (action === "open-folder") post("/api/open", { lecture, kind: "folder" }, "正在打开文件夹");
});

$("#start-recorder").addEventListener("click", () => {
  if (!snapshot?.environment.downloader_exists && typeof openSimpleSettings === "function") {
    openSimpleSettings("setting-downloader");
    return notify("首次连接教学网，请先在设置中选择下载器所在文件夹。");
  }
  post("/api/recorder/start", {}, "正在启动本机教学网连接");
});
$("#login-form").addEventListener("submit", async event => {
  event.preventDefault();
  const username = $("#pku-username").value.trim();
  const password = $("#pku-password").value;
  if (!username || !password) return notify("请填写学号和 IAAA 密码。", true);
  const result = await post("/api/recorder/login", { username, password, semester: $("#pku-semester").value.trim() }, "正在登录并读取课程");
  if (result) $("#pku-password").value = "";
});
$("#reload-courses").addEventListener("click", () => post("/api/recorder/courses", { semester: $("#pku-semester").value.trim() }, "正在重新读取课程"));
$("#course-select").addEventListener("change", fillRecordings);
$("#run-one-click").addEventListener("click", () => {
  const courseId = $("#course-select").value;
  const index = $("#recording-select").value;
  if (!courseId || index === "") return notify("请先选择课程和一节录播。", true);
  post("/api/one-click", { course_id: courseId, recording_index: Number(index), include_files: $("#include-files").checked, profile: $("#profile-select").value }, "一键任务已开始，可以留在本页查看进度");
});

$("#sync-online").addEventListener("click", () => post("/api/sync", { offline: false }, "同步与生成任务已提交"));
$("#sync-offline").addEventListener("click", () => post("/api/sync", { offline: true }, "离线同步任务已提交"));
$("#check-key").addEventListener("click", () => post("/api/check-key", {}, "正在验证 API Key"));
$("#refresh").addEventListener("click", refresh);
$("#open-course").addEventListener("click", () => post("/api/open", { lecture: "", kind: "course" }, "正在打开工作目录"));
$("#toggle-watch").addEventListener("click", async () => snapshot?.watcher.running ? post("/api/watch/stop", {}, "正在停止自动处理") : post("/api/watch/start", { offline: false }, "自动处理已开启"));
$("#pick-files").addEventListener("click", async () => {
  try { const data = await api("/api/pick-files", { method: "POST", body: {} }); selectedPaths = data.paths; $("#import-paths").value = selectedPaths.join("\n"); }
  catch (error) { notify(error.message, true); }
});
$("#import-files").addEventListener("click", () => {
  const title = $("#import-title").value.trim();
  if (!title || !selectedPaths.length) return notify("请填写名称并选择至少一个文件。", true);
  post("/api/import", { title, paths: selectedPaths }, "材料导入任务已提交");
});

$("#close-note").addEventListener("click", () => { $("#source-video").pause(); $("#note-modal").classList.add("hidden"); document.body.classList.remove("modal-open"); });
$("#note-modal").addEventListener("click", event => { if (event.target.id === "note-modal") $("#close-note").click(); });
$("#save-draft").addEventListener("click", () => saveNote(false));
$("#save-final").addEventListener("click", () => saveNote(true));
document.addEventListener("keydown", event => { if (event.key === "Escape" && !$("#note-modal").classList.contains("hidden")) $("#close-note").click(); });

$$(`.nav-item[data-scroll]`).forEach(button => button.addEventListener("click", () => {
  $$(".nav-item").forEach(item => item.classList.remove("active"));
  button.classList.add("active");
  document.getElementById(button.dataset.scroll).scrollIntoView({ behavior: "smooth" });
}));

refresh();
pollEvents();
setInterval(refresh, 1800);
setInterval(pollEvents, 900);
