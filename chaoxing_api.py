#!/usr/bin/env python3
"""超星泛雅讨论区 API 封装（基于浏览器抓包 + Cookie 鉴权）。"""
import json
import urllib.request
import urllib.parse
import os

from config import (
    BBSID,
    CLASS_ID,
    CPI,
    COURSE_ID,
    IMAGES_DIR,
    MY_UID,
    STATE_FILE,
)

# 从浏览器抓包复制的 Cookie（登录失效时需重新抓包更新 config.py）
from config import CHAOXING_COOKIE as COOKIE

# 守护进程续期后写入的新 Cookie 文件，优先于 config 中的静态 Cookie
COOKIE_FILE = os.path.join(os.path.dirname(os.path.abspath(STATE_FILE)), "cookie.txt")


def current_cookie() -> str:
    """每次请求时动态取 Cookie：cookie.txt（守护进程续期产物）优先。"""
    try:
        with open(COOKIE_FILE, "r", encoding="utf-8") as f:
            value = f.read().strip()
        if value:
            return value
    except OSError:
        pass
    return COOKIE

TOPIC_LIST_URL = (
    f"https://groupweb.chaoxing.com/course/topic/{BBSID}/getTopicList"
    f"?folder_uuid=&page={{page}}&pageSize={{size}}&kw=&courseId={COURSE_ID}"
    "&isSetTop=&selectedStartTime=&selectedEndTime=&selectedType=0"
    "&learnSilverStartTime=&learnSilverEndTime=&lastAuxValue="
)

HEADERS = {
    "Cookie": COOKIE,
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36",
    "Origin": "https://groupweb.chaoxing.com",
    "Referer": (
        "https://groupweb.chaoxing.com/course/topic/topicList"
        f"?courseid={COURSE_ID}&clazzid={CLASS_ID}&cpi={CPI}&ut=s&bbsid={BBSID}"
    ),
}


def _headers() -> dict:
    h = dict(HEADERS)
    h["Cookie"] = current_cookie()
    return h


def _post(url, body: dict) -> dict:
    data = urllib.parse.urlencode(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=_headers(), method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


class AuthExpired(Exception):
    """登录态失效（服务器返回'请重新登录'）。"""


def get_topics(page=1, size=20) -> list:
    body = {
        "tags": f"classId0000001,classId{CLASS_ID},courseId{COURSE_ID}",
    }
    url = TOPIC_LIST_URL.format(page=page, size=size)
    resp = _post(url, body)
    if resp.get("status") is False and "登录" in (resp.get("msg") or ""):
        raise AuthExpired(resp.get("msg"))
    return resp.get("datas", []) or []


def post_reply(topic_uuid: str, text: str) -> dict:
    """在话题下发布一条回复。返回接口响应 dict。"""
    import re
    import uuid as uuidlib

    # urlToken 由话题详情页 HTML 下发，伪造会被服务器拒绝
    detail_url = (
        f"https://groupweb.chaoxing.com/course/topic/v3/bbs/{BBSID}/{topic_uuid}/replysList"
        f"?courseId={COURSE_ID}&classId={CLASS_ID}&isLearnSilver=0&cpi={CPI}"
        "&knowledgeEnc=&ut=s"
    )
    req = urllib.request.Request(
        detail_url,
        headers={
            "Cookie": current_cookie(),
            "User-Agent": HEADERS["User-Agent"],
            "Referer": HEADERS["Referer"],
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        html = resp.read().decode("utf-8", "ignore")
    m = re.search(r"urlToken\s*:\s*'([0-9a-f]{16,})'", html)
    if not m:
        return {"msg": "urlToken not found", "status": False}
    url_token = m.group(1)

    url = f"https://groupweb.chaoxing.com/pc/invitation/{topic_uuid}/addReplys"
    body = {
        "courseId": COURSE_ID,
        "classId": CLASS_ID,
        "replyId": "-1",
        "uuid": str(uuidlib.uuid4()),
        # 页面 JS 先 percent-encode 一次，urlencode 再编码一次 => 双重编码
        "topic_content": urllib.parse.quote(text),
        "anonymous": "",
        "urlToken": url_token,
        "bbsid": BBSID,
    }
    return _post(url, body)


def topic_images(topic) -> list:
    imgs = topic.get("contentImgs") or ""
    return [u for u in imgs.split(";") if u.strip()]


def load_state() -> dict:
    if os.path.isfile(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"handled": {}}


def save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    topics = get_topics()
    print(f"got {len(topics)} topics")
    for t in topics[:5]:
        print(
            t["id"], t.get("createrName"), "|",
            (t.get("title") or t.get("content") or "")[:40].replace("\n", " "),
            "| imgs:", len(topic_images(t)), "| replies:", t.get("reply_count"),
        )
