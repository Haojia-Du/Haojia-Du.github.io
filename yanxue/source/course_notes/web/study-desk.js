let materialState = { files: [], evidence: [] };
let activeNoteTab = "preview";
let reviewState = null;
let reviewIndex = 0;
let reviewPending = false;
let previousJobRunning = false;
let loadedLecture = "";

const md = window.markdownit({ html: false, linkify: false, breaks: false });
md.inline.ruler.before("escape", "course-math", (state, silent) => {
  const begin = state.pos;
  const display = state.src.slice(begin, begin + 2) === "$$";
  if (state.src[begin] !== "$" || state.src[begin - 1] === "\\") return false;
  const delimiter = display ? "$$" : "$";
  const end = state.src.indexOf(delimiter, begin + delimiter.length);
  if (end <= begin + delimiter.length) return false;
  if (!silent) {
    const token = state.push("course_math", "", 0);
    token.content = state.src.slice(begin + delimiter.length, end);
    token.meta = { display };
  }
  state.pos = end + delimiter.length;
  return true;
});
md.renderer.rules.course_math = (tokens, index) => {
  try { return temml.renderToString(tokens[index].content, { displayMode: tokens[index].meta.display, trust: false, throwOnError: false }); }
  catch (_) { return `<code>${escapeHtml(tokens[index].content)}</code>`; }
};

function renderMarkdown(target, content) {
  target.innerHTML = md.render(content);
  target.querySelectorAll("a").forEach(link => {
    link.target = "_blank";
    link.rel = "noopener noreferrer";
  });
}

function renderNotePreview() {
  const preview = $("#note-preview");
  renderMarkdown(preview, $("#note-editor").value);
  const walker = document.createTreeWalker(preview, NodeFilter.SHOW_TEXT);
  const matches = [];
  while (walker.nextNode()) {
    const node = walker.currentNode;
    if (node.parentElement.closest("code,pre,math,a")) continue;
    if (/〔来源：[^〕]+〕/.test(node.textContent)) matches.push(node);
  }
  matches.forEach(node => {
    const fragment = document.createDocumentFragment();
    let cursor = 0;
    for (const match of node.textContent.matchAll(/〔来源：([^〕]+)〕/g)) {
      fragment.append(document.createTextNode(node.textContent.slice(cursor, match.index)));
      const button = document.createElement("button");
      button.className = "source-cite";
      button.textContent = match[0];
      button.addEventListener("click", () => jumpToSource(match[1]));
      fragment.append(button);
      cursor = match.index + match[0].length;
    }
    fragment.append(document.createTextNode(node.textContent.slice(cursor)));
    node.replaceWith(fragment);
  });
}

function showNoteTab(tab) {
  activeNoteTab = tab;
  $$("[data-note-tab]").forEach(button => button.classList.toggle("active", button.dataset.noteTab === tab));
  $("#note-preview").classList.toggle("hidden", tab !== "preview");
  $("#note-editor").classList.toggle("hidden", tab !== "edit");
  $("#review-panel").classList.toggle("hidden", tab !== "review");
  if (tab === "preview") renderNotePreview();
  if (tab === "review") loadReview();
}

async function loadStudyDesk(lecture, kind) {
  loadedLecture = lecture;
  materialState = await api(`/api/materials?lecture=${encodeURIComponent(lecture)}`);
  $("#material-select").innerHTML = materialState.files.map(file => `<option value="${file.id}">${escapeHtml(file.name)}</option>`).join("");
  $("#generate-review").disabled = kind === "qa" || snapshot?.job.running;
  $("#note-editor").readOnly = kind === "qa";
  reviewIndex = 0;
  reviewState = null;
  showMaterial();
  showNoteTab("preview");
}

function materialUrl(id) {
  return `/api/material?lecture=${encodeURIComponent(loadedLecture)}&index=${id}`;
}

function timeSeconds(value) {
  return value.split(":").reduce((total, part) => total * 60 + Number(part), 0);
}

function showMaterial(locator = "") {
  const file = materialState.files.find(item => String(item.id) === $("#material-select").value);
  const video = $("#source-video");
  const pdf = $("#source-pdf");
  video.pause();
  video.classList.add("hidden");
  pdf.classList.add("hidden");
  $("#source-evidence").replaceChildren();
  $("#source-locators").replaceChildren();
  if (!file) { $("#source-evidence").textContent = "此课程单元尚无来源材料。"; return; }
  if (file.kind === "media") {
    video.classList.remove("hidden");
    const url = materialUrl(file.id);
    if (video.getAttribute("src") !== url) video.src = url;
    const match = locator.match(/\d{1,2}:\d{2}(?::\d{2})?/);
    if (match) {
      const seek = () => { video.currentTime = timeSeconds(match[0]); };
      if (video.readyState >= 1) seek(); else video.addEventListener("loadedmetadata", seek, {once:true});
    }
  } else if (file.suffix === ".pdf") {
    pdf.classList.remove("hidden");
    const page = locator.match(/第?\s*(\d+)\s*页/);
    pdf.src = materialUrl(file.id) + (page ? `#page=${page[1]}` : "");
  }
  let evidence = materialState.evidence.filter(item => item.source === file.name);
  if (locator) {
    const exact = evidence.filter(item => locator.includes(item.locator));
    if (exact.length) evidence = exact;
  }
  if (!evidence.length) {
    $("#source-evidence").textContent = file.kind === "media" ? "转录完成后，这里会显示分段讲稿与时间点。" : "提取证据后可在此逐页阅读；PDF 也可在上方直接预览。";
  }
  evidence.forEach(item => {
    const section = document.createElement("section");
    const title = document.createElement("button");
    title.className = "evidence-locator";
    title.textContent = item.locator;
    title.addEventListener("click", () => showMaterial(item.locator));
    const text = document.createElement("p");
    text.textContent = item.text;
    section.append(title, text);
    $("#source-evidence").append(section);
  });
  if (file.suffix === ".pptx" || file.suffix === ".docx") {
    const message = document.createElement("p");
    message.className = "muted-note";
    message.textContent = "当前显示按页或段落提取的课件内容；保留原文件，不转换其版式。";
    $("#source-locators").append(message);
  }
}

function jumpToSource(citation) {
  const file = materialState.files.find(item => citation.includes(item.name));
  if (!file) return notify("未找到对应原文件，请从左侧材料列表核对。", true);
  $("#material-select").value = String(file.id);
  showMaterial(citation);
}

async function loadReview() {
  const lecture = loadedLecture;
  try {
    const data = await api(`/api/review?lecture=${encodeURIComponent(lecture)}`);
    if (loadedLecture !== lecture) return;
    reviewState = data;
    renderReview();
  } catch (error) { notify(error.message, true); }
}

function renderReview() {
  const panel = $("#review-panel");
  panel.replaceChildren();
  if (!reviewState?.available) {
    panel.textContent = reviewPending ? "正在生成复习包；完成后会在这里显示。" : "保存好笔记后，点击“生成复习包”。将生成自测题、问答卡与考前提纲。";
    return;
  }
  if (reviewState.stale) {
    const warning = document.createElement("p");
    warning.className = "notice";
    warning.textContent = "笔记已修改，当前复习包可能过期。请重新生成后使用。";
    panel.append(warning);
  }
  const heading = document.createElement("h3"); heading.textContent = "自测题 · 先回答，再展开参考答案"; panel.append(heading);
  reviewState.quiz.forEach((item, index) => {
    const detail = document.createElement("details");
    const summary = document.createElement("summary"); summary.textContent = `${index + 1}. ${item.question}`;
    const answer = document.createElement("div"); renderMarkdown(answer, item.answer);
    const source = document.createElement("small"); source.textContent = "出处：" + item.source;
    detail.append(summary, answer, source); panel.append(detail);
  });
  const cardsHeading = document.createElement("h3"); cardsHeading.textContent = "复习卡片"; panel.append(cardsHeading);
  const cards = reviewState.cards;
  if (cards.length) {
    reviewIndex = Math.min(reviewIndex, cards.length - 1);
    const card = cards[reviewIndex];
    const frame = document.createElement("article"); frame.className = "flashcard";
    const count = document.createElement("small"); count.textContent = `${reviewIndex + 1} / ${cards.length}`;
    const question = document.createElement("h3"); question.textContent = card.question;
    const answer = document.createElement("div"); answer.className = "hidden"; renderMarkdown(answer, card.answer);
    const source = document.createElement("small"); source.textContent = "出处：" + card.source; answer.append(source);
    const flip = document.createElement("button"); flip.textContent = "翻面查看答案"; flip.className = "primary"; flip.onclick = () => answer.classList.toggle("hidden");
    const actions = document.createElement("div"); actions.className = "card-actions";
    [["上一张", () => { reviewIndex = (reviewIndex - 1 + cards.length) % cards.length; renderReview(); }],
      ["还需复习", () => rateCard("again")], ["已掌握", () => rateCard("known")],
      ["下一张", () => { reviewIndex = (reviewIndex + 1) % cards.length; renderReview(); }]].forEach(([label, action]) => {
      const button = document.createElement("button"); button.textContent = label; button.onclick = action; actions.append(button);
    });
    const progress = document.createElement("small");
    const known = Object.values(reviewState.progress || {}).filter(v => v.rating === "known").length;
    progress.textContent = `已记录 ${known} 张掌握卡片（记录保存在本机）`;
    frame.append(count, question, flip, answer, actions, progress); panel.append(frame);
  }
  const outlineTitle = document.createElement("h3"); outlineTitle.textContent = "考前提纲（依据笔记整理，不预测考试范围）";
  const outline = document.createElement("div"); outline.className = "markdown-view"; renderMarkdown(outline, reviewState.outline);
  const download = document.createElement("button"); download.className = "ghost"; download.textContent = "导出完整复习包 Markdown";
  download.onclick = async () => {
    const data = await api(`/api/note?lecture=${encodeURIComponent(loadedLecture)}&kind=review`);
    const url = URL.createObjectURL(new Blob([data.content], {type:"text/markdown;charset=utf-8"}));
    const link = document.createElement("a"); link.href = url; link.download = `${loadedLecture}-复习包.md`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  panel.append(outlineTitle, outline, download);
}

async function rateCard(rating) {
  await post("/api/review/progress", {lecture:loadedLecture, index:reviewIndex, rating}, "复习进度已记录");
  reviewIndex = (reviewIndex + 1) % reviewState.cards.length;
  await loadReview();
}

function updateStudyStatus(job) {
  $("#generate-review").disabled = job.running || noteContext?.kind === "qa";
  if (previousJobRunning && !job.running && reviewPending) {
    reviewPending = false;
    loadReview();
  }
  previousJobRunning = job.running;
}

async function loadConnectionSettings() {
  try {
    const data = await api("/api/settings");
    $("#setting-downloader").value = data.downloader_root;
    $("#setting-base-url").value = data.models.base_url || "https://api.openai.com/v1";
    ["writer", "reviewer", "transcriber"].forEach(name => $("#setting-" + name).value = data.models[name] || "");
    $("#setting-key").placeholder = data.has_key ? "已配置，留空保留；输入新值可替换" : "填写服务商提供的 API Key";
    $("#setting-remember").checked = data.remembered;
    $("#connection-summary").textContent = `${data.has_key ? "密钥已配置" : "尚未配置密钥"} · FFmpeg ${data.ffmpeg && data.ffprobe ? "可用" : "未齐全"}`;
  } catch (error) { notify(error.message, true); }
}

function settingsValues() {
  const values = {downloader_root:$("#setting-downloader").value, base_url:$("#setting-base-url").value, api_key:$("#setting-key").value, remember_key:$("#setting-remember").checked};
  ["writer", "reviewer", "transcriber"].forEach(name => values[name] = $("#setting-" + name).value);
  return values;
}

async function storeSettings(test = false) {
  const result = await post("/api/settings", settingsValues(), "连接设置已保存");
  if (!result) return;
  $("#setting-key").value = "";
  await loadConnectionSettings();
  if (test) await post("/api/check-key", {}, "连接验证通过");
}

$("#settings-form").addEventListener("submit", event => { event.preventDefault(); storeSettings(); });
$("#test-connection").addEventListener("click", () => storeSettings(true));
$("#clear-key").addEventListener("click", async () => {
  const result = await post("/api/settings", {...settingsValues(), api_key:"", clear_key:true}, "工作台密钥已移除");
  if (result) { $("#setting-key").value = ""; loadConnectionSettings(); }
});
$("#retry-task").addEventListener("click", () => post("/api/retry", {}, "正在从断点继续"));
$("#material-select").addEventListener("change", () => showMaterial());
$$("[data-note-tab]").forEach(button => button.addEventListener("click", () => showNoteTab(button.dataset.noteTab)));
$("#generate-review").addEventListener("click", async () => {
  const result = await post("/api/review/generate", {lecture:loadedLecture}, "正在生成复习包，已生成的批次会复用");
  if (result) { reviewPending = true; previousJobRunning = true; showNoteTab("review"); }
});
loadConnectionSettings();
