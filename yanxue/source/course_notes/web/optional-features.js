let batchSelectionSignature = "";
let workspacePath = "";
let runtimeInstallRunning = false;

function renderBatchSelection() {
  const course = snapshot?.recorder?.courses?.find(item => String(item.course_id) === $("#course-select").value);
  const signature = JSON.stringify([course?.course_id, course?.recordings]);
  if (signature === batchSelectionSignature) return;
  batchSelectionSignature = signature;
  $("#batch-recordings").innerHTML = course ? (course.recordings || []).map((rec, index) => `<label><input type="checkbox" data-recording-index="${index}"><span>${escapeHtml(rec.name || `第 ${index + 1} 节`)}<small>${escapeHtml(rec.time || "")}</small></span></label>`).join("") : "请先选择课程。";
  updateBatchCount();
}

function updateBatchCount() {
  const count = $$("#batch-recordings input:checked").length;
  $("#batch-selection-count").textContent = `已选择 ${count} 节`;
  $("#start-batch").disabled = !count || snapshot?.job.running || snapshot?.watcher.running;
}

async function loadWorkspaceOptions() {
  try {
    const [local, options] = await Promise.all([api("/api/courses/local"), api("/api/course/options")]);
    $("#workspace-select").innerHTML = local.courses.map(course => `<option value="${escapeHtml(course.name)}" ${course.active ? "selected" : ""}>${escapeHtml(course.title)}</option>`).join("");
    $("#course-template-profile").value = options.profile;
    $("#course-terms").value = options.terms.join("\n");
    $("#course-prompt").value = options.prompt;
  } catch (error) { notify(error.message, true); }
}

function updateOptionalFeatures(data) {
  $("#batch-options").classList.toggle("hidden", !data.recorder.logged_in);
  renderBatchSelection();
  updateBatchCount();
  $("#workspace-select").disabled = data.job.running || data.watcher.running;
  if (workspacePath !== data.course.path) { workspacePath = data.course.path; loadWorkspaceOptions(); }
  const batch = data.batch || {available:false};
  $("#batch-progress").classList.toggle("hidden", !batch.available);
  if (batch.available) {
    const states = {queued:"等待开始", running:"处理中", paused:"已暂停", error:"需要重试", done:"已完成", canceled:"已取消", skipped:"已跳过", pending:"等待处理", notes_done:"笔记已完成"};
    const count = batch.items.filter(item => item.state === "done").length;
    $("#batch-progress-title").textContent = `${batch.course.name} · ${states[batch.state] || batch.state}`;
    $("#batch-progress-summary").textContent = `${count} / ${batch.items.length} 节完成${batch.generate_review ? " · 含复习包" : ""}`;
    $("#batch-progress-items").innerHTML = batch.items.map(item => `<div class="batch-item"><span>${escapeHtml(item.name)}<small>${escapeHtml(item.time || "")}</small></span><b>${states[item.state] || item.state}</b>${item.error ? `<p>${escapeHtml(item.error)}</p>` : ""}</div>`).join("");
    const runningBatch = data.job.running && data.job.action.includes("批量");
    $("#pause-batch").classList.toggle("hidden", !runningBatch);
    $("#resume-batch").classList.toggle("hidden", ["done", "canceled"].includes(batch.state) || data.job.running);
    if (!$("#cancel-batch")) {
      $("#resume-batch").insertAdjacentHTML("afterend", '<button id="cancel-batch" class="ghost">取消批次</button><button id="skip-batch" class="ghost">跳过失败节次</button>');
      $("#cancel-batch").addEventListener("click", () => { if (confirm("取消后保留所有已完成内容。正在处理的当前节会先完成，是否继续？")) post("/api/batch/cancel", {}, "已请求取消，已完成内容保留"); });
      $("#skip-batch").addEventListener("click", () => post("/api/batch/skip", {}, "已跳过失败节次，请点击继续批次"));
    }
    $("#cancel-batch").classList.toggle("hidden", ["done", "canceled"].includes(batch.state));
    $("#skip-batch").classList.toggle("hidden", data.job.running || batch.state !== "error");
  }
  $$("#course-options-form button, #install-browser, #install-ffmpeg").forEach(button => button.disabled = data.job.running || data.watcher.running);
  if (runtimeInstallRunning && !data.job.running) { runtimeInstallRunning = false; loadRuntime(); }
}

async function loadRuntime() {
  try {
    const report = await api("/api/runtime");
    $("#runtime-summary").textContent = `v${report.version} · ${report.native ? "Windows 启动版" : "源码版"} · FFmpeg ${report.ffmpeg && report.ffprobe ? "可用" : "未齐全"} · 浏览器组件 ${report.chromium ? "可用" : "未安装"} · 文档组件 ${report.pdf && report.pptx ? "可用" : "未齐全"}`;
  } catch (error) { notify(error.message, true); }
}

$("#batch-recordings").addEventListener("change", updateBatchCount);
$("#batch-select-all").addEventListener("click", () => { $$("#batch-recordings input").forEach(input => input.checked = true); updateBatchCount(); });
$("#batch-select-none").addEventListener("click", () => { $$("#batch-recordings input").forEach(input => input.checked = false); updateBatchCount(); });
$("#start-batch").addEventListener("click", async () => {
  const indices = $$("#batch-recordings input:checked").map(input => Number(input.dataset.recordingIndex));
  if (!indices.length) return notify("请先勾选需要处理的录播。", true);
  await post("/api/batch/start", {course_id:$("#course-select").value, indices, profile:$("#profile-select").value, include_files:$("#include-files").checked, generate_review:$("#batch-review").checked}, "已开始批量处理，完成的课程单元会逐节保存");
});
$("#pause-batch").addEventListener("click", () => post("/api/batch/pause", {}, "将在当前节结束后暂停"));
$("#resume-batch").addEventListener("click", () => post("/api/batch/resume", {}, "正在继续未完成的批次"));
$("#workspace-select").addEventListener("change", async () => {
  const result = await post("/api/course/select", {name:$("#workspace-select").value}, "课程工作区已切换");
  if (result) { $("#profile-select").value = snapshot.course.profile; loadConnectionSettings(); }
});
$("#course-select").addEventListener("change", async () => {
  const course = snapshot?.recorder?.courses?.find(item => String(item.course_id) === $("#course-select").value);
  if (!course) return;
  try { const options = await api(`/api/course/options?title=${encodeURIComponent(course.name)}`); if (options.exists) $("#profile-select").value = options.profile; }
  catch (error) { notify(error.message, true); }
});
$("#course-options-form").addEventListener("submit", async event => {
  event.preventDefault();
  const terms = $("#course-terms").value.split(/\r?\n/).map(value => value.trim()).filter(Boolean);
  const result = await post("/api/course/options", {profile:$("#course-template-profile").value, terms, prompt:$("#course-prompt").value}, "这门课的模板与术语已保存");
  if (result) $("#profile-select").value = snapshot.course.profile;
});
$("#runtime-check").addEventListener("click", loadRuntime);
$("#install-browser").addEventListener("click", async () => { const result = await post("/api/browser/install", {}, "正在按需安装浏览器组件，首次下载可能较大"); if (result) runtimeInstallRunning = true; });
$("#install-ffmpeg").addEventListener("click", async () => { const result = await post("/api/ffmpeg/install", {}, "正在尝试通过 Windows 包管理器安装 FFmpeg"); if (result) runtimeInstallRunning = true; });
$("#update-check").addEventListener("click", async () => {
  $("#update-summary").textContent = "正在检查版本…";
  try {
    const result = await api("/api/update");
    $("#update-summary").textContent = result.available ? `发现 v${result.latest}，当前 v${result.current}。可自行下载更新，现有版本继续可用。` : `当前为 v${result.current}，暂无更新。`;
    $("#update-download").classList.toggle("hidden", !result.available);
  } catch (error) { $("#update-summary").textContent = error.message; }
});
loadRuntime();
