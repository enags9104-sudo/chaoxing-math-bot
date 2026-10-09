#!/usr/bin/env python3
"""超星讨论区数学问答机器人（持续监控版）。

- 首次运行：把当时已有的话题全部记为 baseline，只回答之后新发的话题
- 之后每 INTERVAL 秒轮询一次：新话题 -> 判断是否数学问题 -> 下载配图
  -> ecnu-max 识图作答 -> 通过 addReplys 回复
- 图片临时存放 D:\\ecnu_math_bot\\images，处理状态存 D:\\ecnu_math_bot\\handled.json

用法：
  python bot.py                # 首次：记录基线并开始循环
  python bot.py --backfill 3   # 首次：留最新 3 条参与处理
  python bot.py --once         # 只跑一轮，不循环
"""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chaoxing_api as cx
import ecnu_api

INTERVAL = 90          # 轮询间隔（秒）
MAX_ATTEMPTS = 3       # 单个话题最多尝试次数（接口报错时）

ANSWER_PROMPT = """你是大学高等数学课程讨论区的答疑助教。下面是同学在超星讨论区发的帖子（含图片）。

要求：
1. 先判断图片是否清晰可辨。如果图片模糊、太小、反光、看不清题目内容，请只回复一行：UNCLEAR
2. 如果这不是数学/学习问题（例如行政、分数、闲聊、求资料），请只回复一行：NOT_MATH
3. 否则，请准确解答这个问题：先给出关键思路，再给出主要步骤，必要时指出易错点。用中文，简洁清晰，适合直接发到讨论区（纯文本，不要 markdown 符号，公式用行内文字如 x^2、∫、lim 表示），不要自称 AI，不要留下"作为AI"之类的话。

帖子标题：{title}
帖子正文：{content}
"""


def download_images(topic_id, urls):
    os.makedirs(cx.IMAGES_DIR, exist_ok=True)
    paths = []
    for i, url in enumerate(urls, 1):
        path = os.path.join(cx.IMAGES_DIR, f"{topic_id}_{i}.jpg")
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": cx.HEADERS["User-Agent"]}
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
            if len(data) < 2048:
                continue
            with open(path, "wb") as f:
                f.write(data)
            paths.append(path)
        except Exception as e:
            print(f"  image download failed: {e}")
    return paths


def answer_topic(title, content, image_paths):
    prompt = ANSWER_PROMPT.format(title=title or "（无）", content=content or "（无）")
    if image_paths:
        msgs = [{"type": "text", "text": prompt}]
        for p in image_paths:
            msgs.append({
                "type": "image_url",
                "image_url": {"url": ecnu_api.image_to_data_uri(p)},
            })
        return ecnu_api.chat([{"role": "user", "content": msgs}])
    return ecnu_api.chat([{"role": "user", "content": prompt}])


def decide(answer):
    if not answer:
        return False, "empty"
    head = answer.strip().upper()
    if head.startswith("UNCLEAR"):
        return False, "unclear"
    if head.startswith("NOT_MATH"):
        return False, "not_math"
    return True, "answered"


def process_topic(t):
    """处理单个话题，返回 (是否结束处理, 原因)。"""
    tid = str(t["id"])
    title = (t.get("title") or "").strip()
    content = (t.get("content") or "").strip()
    if not title and not content:
        return True, "empty_topic"
    # 不回复自己发的帖
    if t.get("create_puid") == cx.MY_UID or t.get("createrName") == "董天放":
        return True, "own_post"

    urls = cx.topic_images(t)
    print(f"[{tid}] imgs={len(urls)} {title or content[:40]}")
    paths = download_images(tid, urls)
    print(f"  downloaded {len(paths)} images")
    answer = answer_topic(title, content, paths)
    ok, reason = decide(answer)
    if not ok:
        print(f"  skip: {reason}")
        return True, reason
    answer = answer.strip()
    resp = cx.post_reply(t["uuid"], answer)
    msg = resp.get("msg", "")
    print(f"  reply -> {msg}")
    if "成功" in msg:
        return True, "answered"
    print(f"  reply response: {json.dumps(resp, ensure_ascii=False)[:300]}")
    return False, f"reply_failed:{msg}"


def load_state():
    if os.path.isfile(cx.STATE_FILE):
        with open(cx.STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"started": "", "handled": {}, "attempts": {}}


def save_state(state):
    os.makedirs(os.path.dirname(cx.STATE_FILE), exist_ok=True)
    with open(cx.STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def run_once(state, log_time=None):
    try:
        topics = cx.get_topics()
    except Exception as e:
        print(f"get_topics failed: {e}")
        return state
    for t in sorted(topics, key=lambda x: x.get("create_time", 0), reverse=True):
        tid = str(t["id"])
        if tid in state["handled"]:
            continue
        attempts = state["attempts"].get(tid, 0)
        if attempts >= MAX_ATTEMPTS:
            state["handled"][tid] = "gave_up"
            continue
        state["attempts"][tid] = attempts + 1
        try:
            done, reason = process_topic(t)
        except Exception as e:
            print(f"  error: {e}")
            done = False
            reason = "error"
        if done:
            state["handled"][tid] = reason
            state["attempts"].pop(tid, None)
        save_state(state)
    return state


def init_baseline(state, backfill=0):
    topics = cx.get_topics()
    topics.sort(key=lambda x: x.get("create_time", 0), reverse=True)
    state["started"] = datetime.now().isoformat()
    for i, t in enumerate(topics):
        tid = str(t["id"])
        if tid not in state["handled"]:
            # backfill>0 时保留最新 N 条待处理
            state["handled"][tid] = "baseline" if i >= backfill else "pending_backfill"
    # pending_backfill 会在此后被 run_once 正常处理
    state["handled"] = {
        k: v for k, v in state["handled"].items() if v != "pending_backfill"
    }
    save_state(state)
    print(f"baseline: {len(topics)} topics recorded, backfill={backfill}")


if __name__ == "__main__":
    args = sys.argv[1:]
    backfill = 0
    once = "--once" in args
    if "--backfill" in args:
        backfill = int(args[args.index("--backfill") + 1])

    state = load_state()
    if not state["started"]:
        init_baseline(state, backfill)

    while True:
        print(f"--- {datetime.now().strftime('%H:%M:%S')} poll ---")
        state = run_once(state)
        if once:
            break
        time.sleep(INTERVAL)
