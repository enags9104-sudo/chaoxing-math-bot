#!/usr/bin/env python3
"""独立 Edge 实例（CDP 端口 9334、独立 profile）的扩展实测驱动。

复用 temp_20261009_01\\login_watch.py 的纯标准库 WebSocket/CDP 客户端。
不动用户日常 Edge。

子命令：
  targets                列出 CDP 目标
  eval <url子串> <js>     在匹配目标里执行 JS，打印返回值
  console <url子串> [秒]  抓页面控制台输出（找 [cx-math] 日志）
  setkey                 把 config.py 的 ECNU_API_KEY 写入扩展 chrome.storage.local
  cookies                把超星 Cookie 注入独立 profile（免人工登录）
  test                   打开话题详情页 -> 点「一键生成回答」-> 等填入回复框
"""
import json
import os
import sys
import time
import urllib.request

PROJ = r"d:\download\Traes\temp_20261009_01"
sys.path.insert(0, PROJ)

import login_watch as lw  # noqa: E402  复用 _ws_connect/_ws_send/_ws_recv_text/_cdp_call

CDP_PORT = 9334
lw.CDP_PORT = CDP_PORT  # login_watch 默认 9333（Chrome），本工具用 Edge 的 9334


def _http(path):
    url = f"http://127.0.0.1:{CDP_PORT}{path}"
    return json.loads(urllib.request.urlopen(url, timeout=5).read().decode())


def get_targets():
    return _http("/json/list")


def find_target(substr):
    for t in get_targets():
        if substr in t.get("url", ""):
            return t
    return None


def ws_eval(ws_url, expr, await_promise=True, timeout=60):
    sock = lw._ws_connect(ws_url)
    try:
        sock.settimeout(timeout)
        lw._ws_send(sock, {
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {
                "expression": expr,
                "awaitPromise": await_promise,
                "returnByValue": True,
            },
        })
        while True:
            msg = json.loads(lw._ws_recv_text(sock))
            if msg.get("id") != 1:
                continue
            if "error" in msg:
                raise RuntimeError(str(msg["error"]))
            res = msg.get("result", {})
            if "exceptionDetails" in res:
                det = res["exceptionDetails"]
                exc = det.get("exception") or {}
                raise RuntimeError(exc.get("description") or det.get("text") or "eval error")
            return res.get("result", {}).get("value")
    finally:
        sock.close()


def ws_console(ws_url, seconds):
    """抓取页面控制台（含内容脚本）在 seconds 秒内的输出。"""
    sock = lw._ws_connect(ws_url)
    events = []
    try:
        sock.settimeout(2)
        lw._ws_send(sock, {"id": 1, "method": "Runtime.enable", "params": {}})
        end = time.time() + seconds
        while time.time() < end:
            try:
                msg = json.loads(lw._ws_recv_text(sock))
            except (OSError, RuntimeError, ValueError):
                continue  # 短超时，回到 deadline 判断
            if msg.get("method") == "Runtime.consoleAPICalled":
                args = " ".join(
                    str(a.get("value", a.get("description", "")))
                    for a in msg["params"].get("args", [])
                )
                events.append(f'{msg["params"].get("type")}: {args}')
            elif msg.get("method") == "Runtime.exceptionThrown":
                det = msg["params"].get("exceptionDetails", {})
                exc = det.get("exception", {})
                events.append("EXCEPTION: " + (exc.get("description") or det.get("text", "")))
        return events
    finally:
        sock.close()


EXT_ID = "cgjodcacpfpddlhogelmpikmofkpfjah"  # 本扩展 unpacked 加载后的固定 ID


def open_target(url, wait_ws=True):
    """开新标签页并返回其 webSocketDebuggerUrl。"""
    res = lw._cdp_call("Target.createTarget", {"url": url})
    tid = res["targetId"]
    if not wait_ws:
        return tid
    for _ in range(30):
        for x in get_targets():
            if x.get("id") == tid and x.get("webSocketDebuggerUrl"):
                return x["webSocketDebuggerUrl"]
        time.sleep(0.3)
    raise RuntimeError("新目标未出现在 CDP 列表")


# ---------------- 子命令 ----------------

def cmd_targets():
    for t in get_targets():
        print(f'{t.get("type"):15} {t.get("url", "")[:110]}')


def cmd_eval(argv):
    if len(argv) < 2:
        raise SystemExit("用法: eval <url子串> <js表达式>")
    t = find_target(argv[0])
    if not t:
        raise SystemExit(f"没有匹配 {argv[0]!r} 的目标")
    print(json.dumps(ws_eval(t["webSocketDebuggerUrl"], argv[1]),
                     ensure_ascii=False, indent=2))


def cmd_console(argv):
    seconds = float(argv[1]) if len(argv) > 1 else 6
    t = find_target(argv[0])
    if not t:
        raise SystemExit(f"没有匹配 {argv[0]!r} 的目标")
    for line in ws_console(t["webSocketDebuggerUrl"], seconds):
        print(line)


def cmd_setkey():
    from config import ECNU_API_KEY, ECNU_BASE_URL, ECNU_MODEL

    ws = open_target(f"chrome-extension://{EXT_ID}/popup.html")
    expr = (
        "(async () => {"
        f"await chrome.storage.local.set({{apiKey:{json.dumps(ECNU_API_KEY)},"
        f"baseUrl:{json.dumps(ECNU_BASE_URL)},model:{json.dumps(ECNU_MODEL)}}});"
        "const s = await chrome.storage.local.get(['apiKey','baseUrl','model']);"
        "return {id: chrome.runtime.id, baseUrl: s.baseUrl, model: s.model,"
        "apiKeyLen: (s.apiKey || '').length};"
        "})()"
    )
    print(json.dumps(ws_eval(ws, expr), ensure_ascii=False, indent=2))


def _cookie_params():
    from config import CHAOXING_COOKIE

    # config 的 Cookie 含课程级参数（265577882enc 等），cookie.txt 缺这些；
    # 以 config 为基准，再补充 cookie.txt 里多出的项
    merged = {}
    for raw in (CHAOXING_COOKIE,):
        for kv in raw.split(";"):
            kv = kv.strip()
            if not kv or "=" not in kv:
                continue
            name, _, value = kv.partition("=")
            merged.setdefault(name.strip(), value.strip())
    try:
        with open(lw.COOKIE_FILE, "r", encoding="utf-8") as f:
            extra = f.read().strip()
        for kv in extra.split(";"):
            kv = kv.strip()
            if not kv or "=" not in kv:
                continue
            name, _, value = kv.partition("=")
            merged.setdefault(name.strip(), value.strip())
    except OSError:
        pass
    return [{
        "name": n, "value": v,
        "domain": ".chaoxing.com", "path": "/",
        "secure": False, "httpOnly": False,
        "sameSite": "Lax", "expires": -1,
    } for n, v in merged.items()]


def cmd_cookies():
    params = _cookie_params()
    try:
        lw._cdp_call("Storage.setCookies", {"cookies": params})
        print(f"browser 级注入 {len(params)} 个 cookie OK")
        return
    except Exception as e:
        print(f"Storage.setCookies 失败，改用页面级: {e}")
    t = get_targets()[0]
    sock = lw._ws_connect(t["webSocketDebuggerUrl"])
    try:
        lw._ws_send(sock, {"id": 1, "method": "Network.enable", "params": {}})
        lw._ws_send(sock, {"id": 2, "method": "Network.setCookies",
                           "params": {"cookies": params}})
        while True:
            msg = json.loads(lw._ws_recv_text(sock))
            if msg.get("id") == 2:
                if "error" in msg:
                    raise RuntimeError(str(msg["error"]))
                print(f"页面级注入 {len(params)} 个 cookie OK")
                return
    finally:
        sock.close()


def _detail_url(topic_uuid):
    import chaoxing_api as cx
    return (
        f"https://groupweb.chaoxing.com/course/topic/v3/bbs/{cx.BBSID}/{topic_uuid}/replysList"
        f"?courseId={cx.COURSE_ID}&classId={cx.CLASS_ID}&isLearnSilver=0"
        f"&cpi={cx.CPI}&knowledgeEnc=&ut=s"
    )


def _pick_topic():
    import chaoxing_api as cx
    topics = cx.get_topics(1, 20)
    topics = [t for t in topics if t.get("create_puid") != cx.MY_UID]
    topics.sort(key=lambda x: x.get("create_time", 0), reverse=True)
    with_img = [t for t in topics if cx.topic_images(t)]
    pool = with_img or topics
    if not pool:
        raise SystemExit("讨论区没有可测话题")
    t = pool[0]
    return t, cx.topic_images(t)


PROBE = """(() => {
  const btn = document.getElementById('cx-math-gen-btn');
  const ta = document.querySelector('textarea[placeholder="回复话题"]');
  const titleEl = document.querySelector('.topicDetail_title');
  const emptyEl = document.querySelector('.topicDetail_title_empty');
  const root = document.querySelector('.topicDetail_detail .richText');
  const imgs = root ? [...root.querySelectorAll('img')]
    .map(i => i.currentSrc || i.src || '').filter(u => /^https?:/.test(u)) : [];
  return {
    href: location.href.slice(0, 140),
    login: /passport2\\.chaoxing\\.com\\/login/.test(location.href),
    host: !!document.querySelector('.topicDetail_title_right'),
    btn: btn ? btn.textContent : null,
    taLen: ta ? ta.value.length : null,
    title: ((titleEl || emptyEl) || {}).textContent || '',
    contentLen: root ? root.innerText.replace(/\\u200b/g, '').trim().length : -1,
    imgs: imgs.length,
  };
})()"""


def cmd_test():
    import chaoxing_api as cx
    cx.current_cookie = lambda: cx.COOKIE  # cookie.txt 缺课程级 cookie，改用 config 的
    t, img_urls = _pick_topic()
    title = (t.get("title") or "").strip()
    uuid = t["uuid"]
    print(f"测试话题: {title or '(无标题)'}  uuid={uuid}  配图={len(img_urls)}")

    res = lw._cdp_call("Target.createTarget", {"url": _detail_url(uuid)})
    tid = res["targetId"]

    ws = None
    for _ in range(20):
        for x in get_targets():
            if x.get("id") == tid:
                ws = x["webSocketDebuggerUrl"]
                break
        if ws:
            break
        time.sleep(0.3)
    if not ws:
        raise SystemExit("新标签页未出现在 CDP 目标列表")

    def probe():
        return ws_eval(ws, PROBE, timeout=30)

    # 1) 登录态 + 按钮注入（内容脚本自己会等 15s）
    info = None
    for _ in range(30):
        time.sleep(1)
        info = probe()
        if info["login"]:
            raise SystemExit("被重定向到登录页：独立 profile 未登录，请先人工登录")
        if info["btn"]:
            break
    print(f"probe: {json.dumps(info, ensure_ascii=False)}")
    if not info["btn"]:
        print("按钮未出现，抓 6 秒控制台日志：")
        for line in ws_console(ws, 6):
            print("  ", line)
        raise SystemExit("按钮注入失败")

    # 2) 点击
    ws_eval(ws, "document.getElementById('cx-math-gen-btn').click(); 'clicked'")
    print("已点击「一键生成回答」，等待生成…")

    # 3) 等结果
    t0 = time.time()
    last = None
    while time.time() - t0 < 210:
        time.sleep(3)
        info = probe()
        cur = (info["btn"], info["taLen"])
        if cur != last:
            last = cur
            print(f'  [{time.time()-t0:6.1f}s] 按钮={info["btn"]!r} 回复框字数={info["taLen"]}')
        btn = info["btn"] or ""
        if info["taLen"] and info["taLen"] > 0:
            val = ws_eval(
                ws,
                'document.querySelector(\'textarea[placeholder="回复话题"]\').value',
                timeout=20,
            )
            print("\n=== 回复框内容（未发布）===")
            print(val)
            print(f"=== 共 {len(val)} 字，耗时 {time.time()-t0:.1f}s ===")
            return
        if btn.startswith(("失败", "图片看不清", "非数学问题", "没有可作答")):
            for line in ws_console(ws, 3):
                print("  ", line)
            raise SystemExit(f"生成终止于状态: {btn}")
    raise SystemExit("等待超时（210s）")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        raise SystemExit(__doc__)
    cmd, rest = args[0], args[1:]
    if cmd == "targets":
        cmd_targets()
    elif cmd == "eval":
        cmd_eval(rest)
    elif cmd == "console":
        cmd_console(rest)
    elif cmd == "setkey":
        cmd_setkey()
    elif cmd == "cookies":
        cmd_cookies()
    elif cmd == "test":
        cmd_test()
    else:
        raise SystemExit(f"未知子命令: {cmd}")
