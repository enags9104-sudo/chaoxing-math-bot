// 超星数学助手 — 设置弹窗
const FIELDS = ["apiKey", "baseUrl", "model", "prompt"];
const DEFAULTS = {
  baseUrl: "https://chat.ecnu.edu.cn/open/api/v1",
  model: "ecnu-max",
};

function $(id) {
  return document.getElementById(id);
}

async function load() {
  const s = await chrome.storage.local.get(FIELDS);
  $("apiKey").value = s.apiKey || "";
  $("baseUrl").value = s.baseUrl || DEFAULTS.baseUrl;
  $("model").value = s.model || DEFAULTS.model;
  $("prompt").value = s.prompt || "";
  if (s.apiKey) show("已载入现有配置", false);
}

function show(text, isError) {
  const el = $("msg");
  el.textContent = text;
  el.className = isError ? "err" : "";
}

async function save() {
  const data = {
    apiKey: $("apiKey").value.trim(),
    baseUrl: $("baseUrl").value.trim(),
    model: $("model").value.trim(),
    prompt: $("prompt").value.trim(),
  };
  if (!data.apiKey) {
    show("API Key 不能为空", true);
    return;
  }
  try {
    await chrome.storage.local.set(data);
    show("已保存", false);
    setTimeout(() => show("", false), 2500);
  } catch (e) {
    show("保存失败：" + e.message, true);
  }
}

$("save").addEventListener("click", save);
load();
