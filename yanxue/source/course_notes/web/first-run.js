function taskRequestLimit() {
  const value = Number($("#request-limit").value);
  if (!Number.isInteger(value) || value < 1 || value > 10000) throw new Error("请求上限应为1～10000的整数。");
  return value;
}

$("#export-diagnostics").addEventListener("click", async () => {
  try {
    const data = await api("/api/diagnostics");
    const url = URL.createObjectURL(new Blob([JSON.stringify(data.report, null, 2)], {type:"application/json"}));
    const link = document.createElement("a");
    link.href = url; link.download = "yanxue-diagnostics.json";
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    notify("脱敏诊断已下载到本机，未上传任何信息。");
  } catch (error) { notify(error.message, true); }
});

function updateFirstRun(data) {
  const env = data.environment;
  $("#setup-progress").textContent = [
    `下载器：${env.downloader_exists ? "目录已连接" : "请在连接设置填写目录（本地导入可跳过）"}`,
    `AI：${env.api_key_status === "valid" ? "连接已验证，模型需试跑验证" : env.api_key_present ? "已填写，待测试" : "待配置"}`,
    `教学网：${data.recorder.logged_in ? "已登录" : "待登录（本地导入可跳过）"}`,
    `音频组件：${env.ffmpeg ? "已找到，完整状态见组件检查" : "请在组件与版本安装"}`
  ].join(" · ");
  const usage = data.request_usage || {};
  $("#request-usage").textContent = usage.limit ? `最近一次任务已发起 ${usage.used} / ${usage.limit} 次 AI 请求（含失败和重试，不代表实际扣费）。` : "尚无任务用量记录。";
  $("#run-sample").disabled = data.job.running || data.watcher.running;
  $("#pick-sample").disabled = data.job.running || data.watcher.running;
  if (data.watcher.error) $("#setup-progress").textContent += ` · 自动监听已停止：${data.watcher.error}`;
  const errors = [env.settings_error, data.request_usage?._record_error, data.batch?.error && !data.batch.available ? data.batch.error : ""].filter(Boolean);
  if (errors.length) $("#setup-progress").textContent += " · " + errors.join("；");
}

$("#pick-sample").addEventListener("click", async () => {
  try {
    const result = await api("/api/pick-files", {method:"POST", body:{media_only:true}});
    if (result.paths?.length) {
      $("#sample-source").value = result.paths[0];
      $("#sample-consent").checked = false;
      notify("文件已选好，请确认后开始试跑；此操作未发送文件。");
    }
  } catch (error) { notify(error.message, true); }
});

$("#run-sample").addEventListener("click", async () => {
  if (!$("#sample-consent").checked) return notify("请先确认样本发送与费用提示。", true);
  try { await post("/api/sample", {source:$("#sample-source").value.trim(), confirmed:true, request_limit:taskRequestLimit()}, "已开始前3分钟试跑，完成后请在书架核对"); }
  catch (error) { notify(error.message, true); }
});
$("#sample-source").addEventListener("input", () => { $("#sample-consent").checked = false; });
for (const id of ["estimate-minutes", "estimate-rate", "estimate-extra"]) {
  $("#" + id).addEventListener("input", () => {
    const values = ["estimate-minutes", "estimate-rate", "estimate-extra"].map(key => $("#" + key).value);
    if (values.some(value => value === "" || !Number.isFinite(Number(value)) || Number(value) < 0)) {
      $("#cost-estimate").textContent = "请填写全部估算参数；未知费用不能当作0。"; return;
    }
    $("#cost-estimate").textContent = `粗略估算：${(Number(values[0])*Number(values[1])+Number(values[2])).toFixed(2)}（按你填写的统一币种）。不含额外重试，实际以供应商账单为准；请求次数上限不等于金额上限。`;
  });
}
