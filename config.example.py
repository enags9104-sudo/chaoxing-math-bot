#!/usr/bin/env python3
"""配置模板：复制为 config.py 后填入自己的值（config.py 已被 .gitignore 排除）。"""

# 华东师大 LLM 网关（OpenAI 兼容，ecnu-max 支持图片输入）
ECNU_API_KEY = "sk-xxxxxxxx"
ECNU_BASE_URL = "https://chat.ecnu.edu.cn/open/api/v1"
ECNU_MODEL = "ecnu-max"

# 超星 Cookie：登录网页版后，F12 -> Network -> 任选一个 groupweb.chaoxing.com
# 请求 -> Request Headers -> cookie 整段复制。登录过期后需重新复制。
CHAOXING_COOKIE = ""

# 课程参数：
#   BBSID     讨论区 iframe 地址 course/topic/topicList?...&bbsid=xxx
#   COURSE_ID / CLASS_ID / CPI  课程页地址栏 courseid / clazzid / cpi
#   MY_UID    Cookie 中的 _uid
BBSID = ""
COURSE_ID = ""
CLASS_ID = ""
CPI = ""
MY_UID = ""

# 图片工作区与状态文件（建议放 D 盘等非系统盘）
IMAGES_DIR = r"D:\ecnu_math_bot\images"
STATE_FILE = r"D:\ecnu_math_bot\handled.json"
