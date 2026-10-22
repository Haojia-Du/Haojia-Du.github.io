let simplePaths = [];
let simpleBusy = false;
let simpleBookSignature = "";
const simpleDrawer = $("#settings-drawer");

function openSimpleSettings(target = "connections") {
  if (!simpleDrawer.open) simpleDrawer.showModal();
  document.body.classList.add("settings-open");
  const node = document.getElementById(target);
  if (node && simpleDrawer.contains(node)) {
    let parent = node.parentElement;
    while (parent && parent !== simpleDrawer) { if (parent.tagName === "DETAILS") parent.open = true; parent = parent.parentElement; }
    node.scrollIntoView({block:"nearest"});
    if (["INPUT", "SELECT", "TEXTAREA"].includes(node.tagName)) node.focus();
  }
}

function chooseSimpleSource(source) {
  const campus = source === "campus";
  document.body.classList.toggle("campus-mode", campus);
  $("#choose-local").setAttribute("aria-pressed", String(!campus));
  $("#choose-campus").setAttribute("aria-pressed", String(campus));
}

function simpleName(path) { return String(path).split(/[\\/]/).pop(); }

async function pickSimpleFiles() {
  try {
    const data = await api("/api/pick-files", {method:"POST", body:{}});
    if (!Array.isArray(data.paths) || !data.paths.length) return;
    simplePaths = data.paths;
    $("#simple-title").value = simpleName(simplePaths[0]).replace(/\.[^.]+$/, "");
    $("#simple-selected").textContent = `已选择 ${simplePaths.length} 个材料：` + simplePaths.map(simpleName).join("、");
    $("#simple-start-task").disabled = simpleBusy;
    $("#simple-evidence").disabled = simpleBusy;
  } catch (error) { notify(error.message, true); }
}

async function startSimpleTask(offline) {
  if (!simplePaths.length || simpleBusy) return;
  if (!offline && !snapshot?.environment.api_key_present) {
    openSimpleSettings("setting-key");
    return notify("首次使用，请在这里连接 AI；以后会保留你的连接设置。");
  }
  simpleBusy = true;
  $("#simple-start-task").disabled = true;
  $("#simple-evidence").disabled = true;
  try {
    await post("/api/simple/start", {title:$("#simple-title").value.trim(), paths:simplePaths, offline}, "已开始整理，完成后会出现在下方的笔记中");
  } finally {
    simpleBusy = Boolean(snapshot?.job.running || snapshot?.watcher.running);
    $("#simple-start-task").disabled = simpleBusy || !simplePaths.length;
    $("#simple-evidence").disabled = simpleBusy || !simplePaths.length;
  }
}

function renderSimpleBooks(data) {
  const signature = JSON.stringify([data.course.path, data.lectures]);
  if (signature !== simpleBookSignature) {
    simpleBookSignature = signature;
    const ordered = [...data.lectures].sort((a,b) => b.updated_at - a.updated_at);
    $("#simple-books").innerHTML = ordered.length ? ordered.map(item => {
      const check = item.validation || {};
      const failed = check.status === "failed" || check.status === "running";
      const summary = failed ? "还没整理完成，已完成的部分会保留。" : check.status === "warning" ? "笔记已生成，有些内容建议核对。" : item.status === "offline" ? "原文已整理，尚未生成 AI 笔记。" : item.has_final ? "你已保存定稿，可以继续阅读。" : item.has_draft ? "笔记已生成，可以阅读、核对和复习。" : "材料已导入，可以开始整理。";
      const primary = item.has_final ? actionButton("阅读笔记", "open-final", item.name, "primary") : failed ? actionButton("继续整理", "build", item.name, "primary") : item.has_draft ? actionButton(item.status === "offline" ? "阅读原文" : "阅读笔记", "open-draft", item.name, "primary") : actionButton("开始整理", "build", item.name, "primary");
      const extras = [item.has_draft && failed ? actionButton("查看已完成部分", "open-draft", item.name, "secondary") : "", item.status === "offline" ? actionButton("生成笔记", "build", item.name, "secondary") : "", item.has_qa ? actionButton("查看核对建议", "open-qa", item.name, "secondary") : "", actionButton("材料文件夹", "open-folder", item.name, "secondary")].join("");
      return `<article class="simple-book"><h3>${escapeHtml(item.name)}</h3><p>${escapeHtml(summary)}</p><div class="simple-actions">${primary}</div><details><summary>更多操作</summary><div class="table-actions">${extras}</div></details></article>`;
    }).join("") : '<div class="simple-empty">这里还没有笔记。<br>先在上面选择一份录播或课件，整理完成后就能阅读。</div>';
  }
  $$("#simple-books button").forEach(button => button.disabled = Boolean(data.job.running || data.watcher.running));
}

function updateSimpleView(data) {
  simpleBusy = Boolean(data.job.running || data.watcher.running);
  $("#subtitle").textContent = "选材料，开始整理，然后安心阅读。";
  $("#simple-connection-error").classList.add("hidden");
  $("#simple-setup-hint").classList.toggle("hidden", Boolean(data.environment.api_key_present));
  for (const id of ["simple-start-task", "simple-evidence"]) $("#" + id).disabled = simpleBusy || !simplePaths.length;
  $("#simple-pick").disabled = simpleBusy;
  $("#workspace-select").classList.toggle("hidden", $("#workspace-select").querySelectorAll("option").length < 2);
  $("#simple-progress").classList.toggle("hidden", !simpleBusy);
  $("#simple-progress").textContent = data.job.running ? "正在整理，请保持程序开启。完成后可在下方阅读；已完成的分段会自动保留。" : data.watcher.running ? "正在自动接收新材料，可在设置中停止。" : "";
  if (data.job.error && !data.job.running) {
    $("#task-error-message").textContent = data.job.failure?.suggestion || "这次没有完成。已完成的内容会保留，可以从断点继续，或在设置中查看详情。";
    $("#simple-error-detail").textContent = data.job.error;
  }
  renderSimpleBooks(data);
}

function showSimpleConnectionError() {
  $("#simple-connection-error").textContent = "工作台暂时没有连接上。请重新双击桌面上的燕学启动。";
  const retry = document.createElement("button"); retry.className = "ghost"; retry.textContent = "重新连接";
  retry.addEventListener("click", refresh); $("#simple-connection-error").append(retry);
  $("#simple-connection-error").classList.remove("hidden");
  $("#simple-start-task").disabled = true;
  $("#simple-evidence").disabled = true;
}

// Move existing controls instead of duplicating them: their values and handlers survive.
const settingsContent = $("#settings-content");
settingsContent.append($("#connections"));
const advancedConnections = document.createElement("details");
advancedConnections.className = "wide";
advancedConnections.innerHTML = '<summary>教学网与更多模型选项（可选）</summary><div class="settings-grid"></div>';
for (const id of ["setting-downloader", "setting-reviewer", "setting-transcriber"]) advancedConnections.querySelector(".settings-grid").append($("#" + id).closest("label"));
$("#settings-form").append(advancedConnections);
const folderPicker = document.createElement("button");
folderPicker.type = "button"; folderPicker.className = "ghost"; folderPicker.textContent = "选择文件夹";
folderPicker.addEventListener("click", async () => {
  try {
    const result = await api("/api/pick-folder", {method:"POST", body:{}});
    if (result.path) $("#setting-downloader").value = result.path;
  } catch (error) { notify(error.message, true); }
});
$("#setting-downloader").closest("label").append(folderPicker);
const more = document.createElement("details");
more.innerHTML = '<summary>试跑、用量保护与高级工具</summary><div class="header-tools"></div><div class="simple-error-info"><h3>最近一次失败的详细信息</h3><p id="simple-error-detail">暂无失败记录。</p></div>';
more.querySelector(".header-tools").append($("#refresh"), $("#toggle-watch"));
for (const selector of ["#getting-started", "#overview", "#importer", ".course-template", "#activity"]) more.append($(selector));
settingsContent.append(more);
$("#campus").append($("#batch-progress"));
const detailButton = document.createElement("button");
detailButton.className = "ghost"; detailButton.textContent = "查看详情";
detailButton.addEventListener("click", () => openSimpleSettings("simple-error-detail"));
$("#task-error").append(detailButton);

$("#open-settings").addEventListener("click", () => openSimpleSettings());
$("#close-settings").addEventListener("click", () => simpleDrawer.close());
simpleDrawer.addEventListener("close", () => { document.body.classList.remove("settings-open"); $("#open-settings").focus(); });
simpleDrawer.addEventListener("click", event => { if (event.target === simpleDrawer) simpleDrawer.close(); });
$("#choose-local").addEventListener("click", () => chooseSimpleSource("local"));
$("#choose-campus").addEventListener("click", () => chooseSimpleSource("campus"));
$("#simple-pick").addEventListener("click", pickSimpleFiles);
$("#simple-start-task").addEventListener("click", () => startSimpleTask(false));
$("#simple-evidence").addEventListener("click", () => startSimpleTask(true));
$("#simple-connect").addEventListener("click", () => openSimpleSettings("setting-key"));
document.addEventListener("click", event => {
  const anchor = event.target.closest('a[href^="#"]');
  if (!anchor) return;
  const target = document.getElementById(anchor.getAttribute("href").slice(1));
  if (target?.id === "campus") { event.preventDefault(); chooseSimpleSource("campus"); simpleDrawer.close(); target.scrollIntoView({block:"start"}); return; }
  if (target && simpleDrawer.contains(target)) { event.preventDefault(); openSimpleSettings(target.id); }
});
if (snapshot) updateSimpleView(snapshot);
