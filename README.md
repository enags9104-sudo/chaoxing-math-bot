# chaoxing-math-bot

超星泛雅讨论区**数学问答机器人**：自动监控课程讨论区新话题，调用具备视觉能力的
`ecnu-max` 大模型识别帖子配图并作答，只回复数学问题，图片看不清则跳过。

> 完整的设计、逆向过程与测试结果见 [REPORT.md](REPORT.md)。

## 功能特性

- 每 90 秒轮询讨论区，自动发现新话题
- `getTopicList` 一次拿到标题、正文、原图 URL、回复数等全部信息
- 图片下载到本地工作区（默认 `D:\ecnu_math_bot\images`），base64 送入 ecnu-max 识图
- 单次调用同时完成三重判断：`UNCLEAR`（图看不清，跳过）/ `NOT_MATH`（非数学问题，跳过）/ 正常作答
- 基线机制：只回答上线之后新发的帖子，绝不刷屏历史话题
- `handled.json` 状态持久化，重启不重复回复；接口异常自动重试（最多 3 次）
- **登录守护进程 `login_watch.py`**：后台静默运行（pythonw 无黑窗），Cookie 失效时
  自动弹出登录窗口，人工登录后经 CDP 自动抓取新 Cookie，机器人免重启自动恢复
- 零第三方依赖，纯 Python 标准库实现

## 项目结构

```
bot.py               主循环：轮询 → 过滤 → 下载图片 → 作答 → 回复 → 记录状态
chaoxing_api.py      超星接口封装：getTopicList / replysList(urlToken) / addReplys
ecnu_api.py          ecnu-max 网关封装（OpenAI 兼容，支持图片输入）
login_watch.py       登录守护进程：静默检测失效 → 弹窗 → CDP 自动续 Cookie
config.example.py    配置模板（复制为 config.py 后填写）
REPORT.md            完整项目报告
```

## 快速开始

```bash
# 1. 准备配置（config.py 已被 .gitignore 排除，不会提交）
cp config.example.py config.py
#    填入：ECNU_API_KEY、CHAOXING_COOKIE、BBSID/COURSE_ID/CLASS_ID/CPI/MY_UID

# 2. 首次运行（记录已有话题为基线，开始轮询）
python bot.py

# 其他用法
python bot.py --once            # 只跑一轮
python bot.py --backfill 3      # 首次运行时留最新 3 条参与处理

# 3. 启动登录守护进程（后台静默运行，无黑窗）
pythonw login_watch.py
#    Cookie 约 7 天过期；失效时会自动弹出登录窗口，人工登录后
#    自动写入 D:\ecnu_math_bot\cookie.txt，bot 每轮重读该文件，无需重启
```

### 配置项获取方式

| 配置 | 获取方式 |
|------|----------|
| `ECNU_API_KEY` | 华东师大 LLM 网关密钥 |
| `CHAOXING_COOKIE` | 登录网页版 → F12 → Network → 任选 groupweb 请求 → Request Headers → 整段复制 cookie |
| `BBSID` | 讨论区 iframe 地址 `course/topic/topicList?...&bbsid=xxx` |
| `COURSE_ID` / `CLASS_ID` / `CPI` | 课程页地址栏 `courseid` / `clazzid` / `cpi` |
| `MY_UID` | Cookie 中的 `_uid` |

## 关键实现要点

1. **回复接口 `addReplys` 的两个坑**
   - `topic_content` 需要**双重 URL 编码**（页面 JS 编码一次 + 表单序列化一次）；
   - `urlToken` 必须先 GET 话题详情页 HTML、用正则提取服务端下发的一次性令牌，
     伪造随机值会被拒并提示"请勿同时在多个网页发布话题或回复"。
2. **enc 参数会过期**：课程页链接显示"无效的参数"时，从 `i.chaoxing.com`
   课程列表重新点击进入即可拿到新链接。
3. 更多细节（抓包分析、提示词设计、测试数据）见 [REPORT.md](REPORT.md)。

## 安全说明

- `config.py` 含 API key 与登录 Cookie，等同账号凭据，**已被 .gitignore 排除，严禁提交**；
- Cookie 约 7 天过期，失效后需重新抓包更新；
- 请遵守平台规范与讨论区约定，控制回复频率与质量。

## 免责声明

本项目仅供学习与技术研究使用，自动发布内容的责任由使用者自行承担。
