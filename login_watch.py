#!/usr/bin/env python3
"""超星登录守护进程（后台静默运行，无黑窗）。

用 pythonw 启动本脚本即可完全无窗口运行：
    pythonw login_watch.py

职责：
1. 每 5 分钟检测一次超星 Cookie 是否失效（登录页跳转 / 返回非 JSON 即判定失效）；
2. 失效时自动弹出一个专用 Chrome 登录窗口（独立 profile，不影响用户正在用的浏览器），
   等待人工登录续期；
3. 登录成功后通过 Chrome DevTools Protocol（纯标准库 WebSocket 客户端）
   自动抓取新的超星 Cookie，原子写入 D:\\ecnu_math_bot\\cookie.txt；
4. 机器人（bot.py）每轮请求都会重读该文件，续期后无需重启、自动恢复。

命令行：
    python login_watch.py            # 正式运行（建议 pythonw 启动）
    python login_watch.py --check    # 单次检测并打印结果（调试用）
    python login_watch.py --login    # 立即弹出登录窗口续期一次（测试用）
"""
import base64
import json
import os
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chaoxing_api as cx

WORK_DIR = os.path.dirname(os.path.abspath(cx.STATE_FILE))
COOKIE_FILE = cx.COOKIE_FILE
LOG_FILE = os.path.join(WORK_DIR, "watchdog.log")
FLAG_FILE = os.path.join(WORK_DIR, "cookie_expired.flag")
PID_FILE = os.path.join(WORK_DIR, "watchdog.pid")
PROFILE_DIR = os.path.join(WORK_DIR, "chrome_profile")

CHECK_INTERVAL = 300   # 正常检测间隔（秒）
LOGIN_POLL = 3         # 弹窗后抓取 Cookie 的轮询间隔（秒）
LOGIN_TIMEOUT = 600    # 等待人工登录的最长时间（秒）
CDP_PORT = 9333        # 避开常用 9222

LOGIN_URL = "https://passport2.chaoxing.com/login?refer=https%3A%2F%2Fi.chaoxing.com"
# 登录后打开讨论区页面，让专用 Chrome 拿到 groupweb 会话 Cookie
TOPIC_LIST_URL = (
    "https://groupweb.chaoxing.com/course/topic/topicList"
    f"?courseid={cx.COURSE_ID}&clazzid={cx.CLASS_ID}&cpi={cx.CPI}"
    f"&ut=s&bbsid={cx.BBSID}&fid=206"
)


def log(msg: str):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n"
    try:
        os.makedirs(WORK_DIR, exist_ok=True)
        if os.path.isfile(LOG_FILE) and os.path.getsize(LOG_FILE) > 500_000:
            os.remove(LOG_FILE)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


# ---------------- Cookie 有效性检测 ----------------

def cookie_valid():
    """True=有效  False=已失效（登录掉了）  None=网络问题，无法判断。"""
    try:
        cx.get_topics(page=1, size=1)
        return True
    except cx.AuthExpired:  # 服务器明确返回"请重新登录"
        return False
    except urllib.error.HTTPError as e:
        return False if e.code in (401, 403) else None
    except urllib.error.URLError:
        return None
    except (ValueError, KeyError):  # JSONDecodeError 属 ValueError：拿到的是登录页 HTML
        return False
    except Exception as e:  # 兜底：不因未知错误误弹登录窗
        log(f"check error: {e}")
        return None


# ---------------- 极简 WebSocket 客户端（CDP 用，纯标准库） ----------------

def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise RuntimeError("socket closed")
        buf += chunk
    return buf


def _ws_connect(url: str):
    if not url.startswith("ws://"):
        raise RuntimeError(f"unsupported ws url: {url}")
    hostport, _, path = url[5:].partition("/")
    host, _, port = hostport.partition(":")
    sock = socket.create_connection((host, int(port or 80)), timeout=10)
    key = base64.b64encode(os.urandom(16)).decode()
    req = (
        f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\n"
        f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    )
    sock.sendall(req.encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise RuntimeError("ws handshake failed")
        buf += chunk
    if b" 101 " not in buf.split(b"\r\n", 1)[0]:
        raise RuntimeError("ws handshake rejected")
    return sock


def _ws_send(sock, obj):
    data = json.dumps(obj).encode()
    mask = os.urandom(4)
    n = len(data)
    if n < 126:
        header = bytes([0x81, 0x80 | n])
    elif n < 65536:
        header = bytes([0x81, 0x80 | 126]) + struct.pack(">H", n)
    else:
        header = bytes([0x81, 0x80 | 127]) + struct.pack(">Q", n)
    payload = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    sock.sendall(header + mask + payload)


def _ws_recv_text(sock) -> str:
    """读取一条完整文本消息（含分片、ping/pong 处理）。"""
    chunks = []
    while True:
        b1, b2 = _recv_exact(sock, 2)
        fin, opcode = b1 & 0x80, b1 & 0x0F
        ln = b2 & 0x7F
        if ln == 126:
            ln = struct.unpack(">H", _recv_exact(sock, 2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", _recv_exact(sock, 8))[0]
        mask = _recv_exact(sock, 4) if b2 & 0x80 else None
        data = _recv_exact(sock, ln)
        if mask:
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        if opcode == 0x9:  # ping -> pong
            pong = bytes([0x8A, 0x80 | len(data)]) + os.urandom(4)
            sock.sendall(pong + bytes(b ^ 0 for b in data))
            continue
        if opcode == 0x8:
            raise RuntimeError("ws closed")
        if opcode in (0x1, 0x0):
            chunks.append(data)
            if fin:
                return b"".join(chunks).decode("utf-8", "ignore")


def _cdp_call(method: str, params: dict = None):
    ver = json.loads(
        urllib.request.urlopen(
            f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=5
        ).read().decode()
    )
    sock = _ws_connect(ver["webSocketDebuggerUrl"])
    try:
        _ws_send(sock, {"id": 1, "method": method, "params": params or {}})
        while True:
            msg = json.loads(_ws_recv_text(sock))
            if msg.get("id") == 1:
                if "error" in msg:
                    raise RuntimeError(str(msg["error"]))
                return msg.get("result", {})
    finally:
        sock.close()


def cdp_cookies():
    """从专用 Chrome 抓取全部 Cookie；未就绪返回 None。"""
    for method, key in (("Storage.getCookies", "cookies"),
                        ("Network.getAllCookies", "cookies")):
        try:
            result = _cdp_call(method)
            if key in result:
                return result[key]
        except Exception:
            continue
    return None


# ---------------- 登录窗口 ----------------

def find_browser():
    candidates = [
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                     r"Google\Chrome\Application\chrome.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     r"Google\Chrome\Application\chrome.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     r"Google\Chrome\Application\chrome.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     r"Microsoft\Edge\Application\msedge.exe"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def _chaoxing_cookie_str(cookies) -> str:
    pairs = [
        f"{c['name']}={c['value']}"
        for c in cookies
        if "chaoxing.com" in (c.get("domain") or "")
    ]
    return "; ".join(pairs)


def _has_login(cookies) -> bool:
    names = {c["name"] for c in cookies if "chaoxing.com" in (c.get("domain") or "")}
    return "_uid" in names or "UID" in names


def ensure_login() -> bool:
    """弹出登录窗口，等待人工登录，抓取并保存新 Cookie。成功返回 True。"""
    log("检测到登录失效，弹出登录窗口……")
    with open(FLAG_FILE, "w", encoding="utf-8") as f:
        f.write(datetime.now().isoformat())

    exe = find_browser()
    if not exe:
        log("未找到 Chrome/Edge，无法弹出登录窗口")
        return False

    proc = subprocess.Popen([
        exe,
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
        LOGIN_URL,
    ])
    deadline = time.time() + LOGIN_TIMEOUT
    opened_topic_page = False

    while time.time() < deadline:
        time.sleep(LOGIN_POLL)
        if proc.poll() is not None:
            log("登录窗口被关闭，本轮放弃（下次检测会重新弹出）")
            return False
        cookies = cdp_cookies()
        if not cookies or not _has_login(cookies):
            continue  # 尚未登录
        if not opened_topic_page:
            # 登录成功：打开讨论区页面以获取 groupweb 会话 Cookie
            try:
                _cdp_call("Target.createTarget", {"url": TOPIC_LIST_URL})
                opened_topic_page = True
                log("登录成功，正在获取讨论区会话 Cookie……")
                time.sleep(4)
                cookies = cdp_cookies() or cookies
            except Exception as e:
                log(f"打开讨论区页面失败: {e}")
        cookie_str = _chaoxing_cookie_str(cookies)
        if not cookie_str:
            continue
        tmp = COOKIE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(cookie_str)
        os.replace(tmp, COOKIE_FILE)
        if cookie_valid() is True:
            log("新 Cookie 校验通过，续期完成 ✔")
            try:
                os.remove(FLAG_FILE)
            except OSError:
                pass
            proc.terminate()
            return True
        log("新 Cookie 暂未通过校验，继续等待……")
        opened_topic_page = False  # 下轮重新尝试

    log("等待登录超时（10 分钟）")
    try:
        proc.terminate()
    except OSError:
        pass
    return False


# ---------------- 主循环 ----------------

def already_running() -> bool:
    if os.path.isfile(PID_FILE):
        try:
            with open(PID_FILE, "r", encoding="utf-8") as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)
            return True
        except (OSError, ValueError):
            pass
    os.makedirs(WORK_DIR, exist_ok=True)
    with open(PID_FILE, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    return False


def main():
    if already_running():
        return
    log(f"watchdog 启动 (pid {os.getpid()})")
    try:
        while True:
            v = cookie_valid()
            if v is True:
                if os.path.isfile(FLAG_FILE):
                    try:
                        os.remove(FLAG_FILE)
                    except OSError:
                        pass
                    log("Cookie 已恢复有效")
                else:
                    log("Cookie 有效")
            elif v is False:
                log("Cookie 已失效")
                ensure_login()
            else:
                log("网络异常，跳过本轮检测")
            time.sleep(CHECK_INTERVAL)
    finally:
        try:
            os.remove(PID_FILE)
        except OSError:
            pass


if __name__ == "__main__":
    if "--check" in sys.argv:
        print("cookie_valid =", cookie_valid())
    elif "--login" in sys.argv:
        print("ensure_login ->", ensure_login())
    else:
        main()
