// 超星数学助手 — 内容脚本（注入到 groupweb.chaoxing.com 话题详情页）
// 职责：注入「一键生成回答」按钮、采集标题/正文/配图、把答案填进回复框
(() => {
  const BTN_ID = "cx-math-gen-btn";
  // 显眼样式：渐变底 + 发光呼吸动画
  const IDLE_BG = "linear-gradient(135deg, #2f7bff 0%, #6a4dff 100%)";
  const IDLE_SHADOW = "0 4px 14px rgba(76, 101, 255, 0.45)";

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  async function until(fn, timeout = 15000, interval = 300) {
    const end = Date.now() + timeout;
    while (Date.now() < end) {
      const v = fn();
      if (v) return v;
      await sleep(interval);
    }
    return null;
  }

  // 页面上没有标题时会显示「无标题」占位
  function getTitle() {
    const t = document.querySelector(".topicDetail_title");
    let s = t ? t.textContent.trim() : "";
    if (!s) {
      const e = document.querySelector(".topicDetail_title_empty");
      s = e ? e.textContent.trim() : "";
    }
    return s === "无标题" ? "" : s;
  }

  function getContent() {
    const root = document.querySelector(".topicDetail_detail .richText");
    return root ? root.innerText.replace(/\u200b/g, "").trim() : "";
  }

  function getImages() {
    const root = document.querySelector(".topicDetail_detail .richText");
    if (!root) return [];
    const out = [];
    for (const img of root.querySelectorAll("img")) {
      const url = img.currentSrc || img.src || img.getAttribute("data-original") || "";
      if (/^https?:/.test(url) && out.indexOf(url) === -1) out.push(url);
    }
    return out.slice(0, 4);
  }

  function fillReply(text) {
    const ta = document.querySelector('textarea[placeholder="回复话题"]');
    if (!ta) throw new Error("找不到回复输入框");
    ta.focus();
    ta.value = text;
    ta.dispatchEvent(new Event("input", { bubbles: true }));
    ta.dispatchEvent(new Event("change", { bubbles: true }));

    const wrap = ta.parentElement;
    const wc = wrap && wrap.querySelector(".wordcount");
    if (wc) wc.textContent = String(text.length);

    // 页面用行内固定高度，需手动回撑
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight + 4, 700) + "px";
    ta.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  // 按钮专用样式：发光呼吸动画 + 悬停上浮，只需注入一次
  function ensureStyles() {
    if (document.getElementById("cx-math-style")) return;
    const style = document.createElement("style");
    style.id = "cx-math-style";
    style.textContent = `
      @keyframes cxMathPulse {
        0%, 100% { box-shadow: 0 4px 14px rgba(76, 101, 255, 0.45); }
        50% { box-shadow: 0 4px 24px rgba(122, 77, 255, 0.9); }
      }
      #${BTN_ID} { animation: cxMathPulse 2s ease-in-out infinite; }
      #${BTN_ID}:hover { transform: translateY(-2px); }
      #${BTN_ID}[data-busy="1"] { animation: none; }
    `;
    document.head.appendChild(style);
  }

  function createButton(host, onClick) {
    ensureStyles();
    const btn = document.createElement("div");
    btn.id = BTN_ID;
    btn.textContent = "一键生成回答";
    btn.dataset.label = btn.textContent; // 固定初始文案，flash 提示后按此恢复
    btn.title = "用 ecnu-max 识图生成数学解答，填入下方回复框（不会自动发布）";
    Object.assign(btn.style, {
      background: IDLE_BG,
      color: "#fff",
      fontSize: "15px",
      fontWeight: "700",
      letterSpacing: "1px",
      lineHeight: "38px",
      height: "38px",
      padding: "0 22px",
      marginLeft: "12px",
      borderRadius: "19px",
      border: "1px solid rgba(255, 255, 255, 0.45)",
      boxShadow: IDLE_SHADOW,
      textShadow: "0 1px 2px rgba(0, 0, 0, 0.25)",
      cursor: "pointer",
      userSelect: "none",
      whiteSpace: "nowrap",
      float: "right",
      transition: "opacity .15s, transform .15s, box-shadow .15s",
    });
    btn.addEventListener("mouseenter", () => {
      if (btn.dataset.busy !== "1") {
        btn.style.opacity = "0.9";
        btn.style.boxShadow = "0 8px 20px rgba(106, 77, 255, 0.6)";
      }
    });
    btn.addEventListener("mouseleave", () => {
      if (btn.dataset.busy !== "1") {
        btn.style.opacity = "1";
        btn.style.boxShadow = IDLE_SHADOW;
      }
    });
    btn.addEventListener("click", onClick);
    host.appendChild(btn);
    return btn;
  }

  // 短暂把按钮文字换成结果提示，再恢复
  function flash(btn, msg, color, keep = 3000) {
    const original = btn.dataset.label || btn.textContent;
    btn.textContent = msg;
    btn.style.background = color; // 纯色覆盖渐变
    setTimeout(() => {
      btn.textContent = original;
      btn.style.background = IDLE_BG; // 恢复渐变
    }, keep);
  }

  async function onClick(event) {
    const btn = event.currentTarget;
    if (btn.dataset.busy === "1") return;

    const payload = {
      title: getTitle(),
      content: getContent(),
      images: getImages(),
    };
    if (!payload.title && !payload.content && !payload.images.length) {
      flash(btn, "没有可作答的内容", "#909399");
      return;
    }

    btn.dataset.busy = "1";
    btn.textContent = "生成中…";
    btn.style.opacity = "0.7";
    btn.style.cursor = "wait";

    try {
      const res = await chrome.runtime.sendMessage({ type: "generate", payload });
      if (!res) throw new Error("后台脚本无响应，请重新加载扩展");
      if (!res.ok) throw new Error(res.error || "未知错误");

      const head = res.text.trim().toUpperCase();
      if (head.startsWith("UNCLEAR")) {
        flash(btn, "图片看不清，已跳过", "#e6a23c");
        return;
      }
      if (head.startsWith("NOT_MATH")) {
        flash(btn, "非数学问题，已跳过", "#e6a23c");
        return;
      }

      fillReply(res.text);
      flash(btn, "已填入回复框", "#67c23a");
    } catch (err) {
      console.error("[cx-math] 生成失败:", err);
      flash(btn, "失败：" + ((err && err.message) || err), "#f56c6c", 5000);
    } finally {
      btn.dataset.busy = "0";
      btn.style.opacity = "1";
      btn.style.cursor = "pointer";
    }
  }

  function ensureInjected() {
    if (document.getElementById(BTN_ID)) return;
    const host = document.querySelector(".topicDetail_title_right");
    if (host) createButton(host, onClick);
  }

  async function main() {
    const host = await until(() => document.querySelector(".topicDetail_title_right"));
    if (!host) {
      console.warn("[cx-math] 未找到标题栏，本页可能不是话题详情页");
      return;
    }
    ensureInjected();
    // 话题页内容会局部重渲染，按钮被冲掉时自动补回
    let queued = false;
    new MutationObserver(() => {
      if (queued || document.getElementById(BTN_ID)) return;
      queued = true;
      setTimeout(() => {
        queued = false;
        ensureInjected();
      }, 300);
    }).observe(document.body, { childList: true, subtree: true });
  }

  main();
})();
