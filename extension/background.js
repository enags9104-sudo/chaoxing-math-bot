// 超星数学助手 — MV3 后台服务工作线程
// 职责：跨域抓取帖子配图（host_permissions 免 CORS）并调用 ecnu-max 视觉接口
// 接口密钥存 chrome.storage.local，不写死在代码里

const DEFAULT_BASE_URL = "https://chat.ecnu.edu.cn/open/api/v1";
const DEFAULT_MODEL = "ecnu-max";
const MAX_IMAGES = 4;
const TIMEOUT_MS = 180000;

// 与 bot.py 的 ANSWER_PROMPT 保持一致
const DEFAULT_PROMPT = `你是大学高等数学课程讨论区的答疑助教。下面是同学在超星讨论区发的帖子（含图片）。

要求：
1. 先判断图片是否清晰可辨。如果图片模糊、太小、反光、看不清题目内容，请只回复一行：UNCLEAR
2. 如果这不是数学/学习问题（例如行政、分数、闲聊、求资料），请只回复一行：NOT_MATH
3. 否则，请准确解答这个问题：先给出关键思路，再给出主要步骤，必要时指出易错点。用中文，简洁清晰，适合直接发到讨论区（纯文本，不要 markdown 符号，公式用行内文字如 x^2、∫、lim 表示），不要自称 AI，不要留下"作为AI"之类的话。

帖子标题：{title}
帖子正文：{content}`;

async function getConfig() {
  const s = await chrome.storage.local.get(["baseUrl", "model", "apiKey", "prompt"]);
  return {
    baseUrl: (s.baseUrl || "").trim() || DEFAULT_BASE_URL,
    model: (s.model || "").trim() || DEFAULT_MODEL,
    apiKey: (s.apiKey || "").trim(),
    prompt: (s.prompt || "").trim() || DEFAULT_PROMPT,
  };
}

async function toDataUri(url) {
  const res = await fetch(url, { credentials: "include" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const blob = await res.blob();
  if (blob.size < 1024) return null; // 占位图/加载失败的小文件
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let bin = "";
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
  }
  return `data:${blob.type || "image/jpeg"};base64,${btoa(bin)}`;
}

async function generate(payload) {
  const cfg = await getConfig();
  if (!cfg.apiKey) {
    throw new Error("尚未设置 API Key：点击浏览器工具栏的扩展图标填写");
  }

  const text = cfg.prompt
    .replace("{title}", payload.title || "（无）")
    .replace("{content}", payload.content || "（无）");

  const parts = [{ type: "text", text }];
  const urls = (payload.images || []).slice(0, MAX_IMAGES);
  let gotImages = 0;
  for (const url of urls) {
    try {
      const uri = await toDataUri(url);
      if (uri) {
        parts.push({ type: "image_url", image_url: { url: uri } });
        gotImages++;
      }
    } catch (e) {
      console.warn("[cx-math] 图片抓取失败:", url, e);
    }
  }
  if (urls.length && !gotImages) {
    throw new Error("配图全部下载失败，无法识图作答");
  }

  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  let resp;
  try {
    resp = await fetch(`${cfg.baseUrl}/chat/completions`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${cfg.apiKey}`,
      },
      body: JSON.stringify({
        model: cfg.model,
        stream: false,
        messages: [{ role: "user", content: parts }],
      }),
      signal: ctrl.signal,
    });
  } catch (e) {
    throw new Error(e.name === "AbortError" ? "接口超时（3 分钟）" : `请求失败：${e.message}`);
  } finally {
    clearTimeout(timer);
  }

  if (!resp.ok) {
    const body = await resp.text().catch(() => "");
    throw new Error(`接口返回 ${resp.status}：${body.slice(0, 300)}`);
  }

  const data = await resp.json();
  const out = data && data.choices && data.choices[0] && data.choices[0].message
    ? data.choices[0].message.content
    : "";
  if (!out) throw new Error("模型返回为空");
  return out.trim();
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || msg.type !== "generate") return;
  generate(msg.payload || {})
    .then((text) => sendResponse({ ok: true, text }))
    .catch((e) => sendResponse({ ok: false, error: String((e && e.message) || e) }));
  return true; // 异步 sendResponse，须保持消息通道打开
});
