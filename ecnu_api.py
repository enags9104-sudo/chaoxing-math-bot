#!/usr/bin/env python3
"""调用 ecnu-max 网关的封装（OpenAI 兼容）。"""
import base64
import json
import mimetypes
import urllib.request

try:
    from config import ECNU_API_KEY as API_KEY
    from config import ECNU_BASE_URL as BASE_URL
    from config import ECNU_MODEL as MODEL
except ImportError:  # 无 config.py 时回退到环境变量
    import os

    BASE_URL = os.environ.get("ECNU_LLM_BASE_URL", "https://chat.ecnu.edu.cn/open/api/v1")
    API_KEY = os.environ.get("ECNU_LLM_API_KEY", "")
    MODEL = os.environ.get("ECNU_LLM_MODEL", "ecnu-max")


def image_to_data_uri(path):
    mime, _ = mimetypes.guess_type(path)
    if not mime:
        mime = "image/png"
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def chat(messages, timeout=180):
    payload = {"model": MODEL, "stream": False, "messages": messages}
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def ask_image(image_path, question, timeout=180):
    """带一张图片提问，返回模型文本。"""
    return chat(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    {
                        "type": "image_url",
                        "image_url": {"url": image_to_data_uri(image_path)},
                    },
                ],
            }
        ],
        timeout=timeout,
    )


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("usage: python ecnu_api.py <image_path> [question]")
        sys.exit(2)
    q = " ".join(sys.argv[2:]) or "这张图片里的内容是什么？"
    print(ask_image(sys.argv[1], q))
