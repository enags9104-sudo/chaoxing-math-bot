# 超星讨论区数学问答机器人 — 项目报告

> 项目代号：chaoxing-math-bot
> 完成时间：2026-10-09
> 作者：董天放（华东师范大学，非计算机专业，借助 AI 编程工具完成）

---

## 1. 项目背景与目标

《高等数学A（一）荣誉课程》在超星泛雅平台设有讨论区，同学经常拍照上传手写题目提问。
本项目把本人的超星账号改造为一个**自动答疑机器人**，目标如下：

1. 持续监控课程讨论区的新话题；
2. **只回答数学问题**（行政、分数、闲聊等一律不回复）；
3. 提取帖子中的图片，由具备视觉能力的 `ecnu-max` 大模型识别题目并作答；
4. **图片看不清直接跳过**，不发布低质量回复；
5. 回复以本人账号自动发布，答案要求"尽可能正确"。

## 2. 总体架构

```
┌─────────────────┐   每 90 秒轮询    ┌──────────────────────┐
│  超星讨论区      │ ◄── getTopicList ─┤  bot.py（主循环）     │
│ groupweb.chaoxing│                   │  - 基线/去重          │
└────────┬────────┘                   │  - 数学问题判定        │
         │ 图片 URL                     │  - 状态记录            │
         ▼                             └──────────┬───────────┘
┌─────────────────┐                               │
│ 图片工作区       │ ◄── 下载原图 ─────────────────┤
│ D:\ecnu_math_bot │                               │
│   \images        │ ── base64 ──► ┌───────────────▼──────────┐
└─────────────────┘               │  ecnu-max（视觉大模型）    │
                                  │  识图 + 判断 + 解题        │
                                  └───────────┬──────────────┘
                                              │ 纯文本答案
                                              ▼
                                  ┌──────────────────────────┐
                                  │ addReplys 发布回复        │
                                  │ （需先取详情页 urlToken）   │
                                  └──────────────────────────┘
```

代码结构（三个文件，均零第三方依赖，仅用 Python 标准库）：

| 文件 | 职责 |
|------|------|
| `ecnu_api.py` | 封装 ecnu-max 网关（OpenAI 兼容协议，支持图片输入） |
| `chaoxing_api.py` | 超星讨论区接口：话题列表、发布回复、状态/图片路径管理 |
| `bot.py` | 主循环：轮询 → 过滤 → 下载图片 → 作答 → 回复 → 记录状态 |
| `config.py` | 本地敏感配置（**不入库**），由 `config.example.py` 复制而来 |

## 3. 关键技术点

### 3.1 ecnu-max 视觉网关

- 接口：`https://chat.ecnu.edu.cn/open/api/v1/chat/completions`（OpenAI 兼容），
  模型 `ecnu-max`，图片以 `data:image/jpeg;base64,...` 形式随 `image_url` 传入。
- 实测该模型可正确识别手写/拍照的数学题目并给出解答，一次调用可携带多张图片。

### 3.2 超星讨论区接口逆向（本项目核心难点）

通过 Chrome DevTools 抓包得到三个关键接口：

**① 话题列表**（信息最全，含图片）

```
POST https://groupweb.chaoxing.com/course/topic/{bbsid}/getTopicList
      ?folder_uuid=&page=1&pageSize=20&kw=&courseId=...&selectedType=0&...
Body: tags=classId0000001,classId{clazzId},courseId{courseId}
Header: X-Requested-With: XMLHttpRequest + Cookie
```

返回 JSON 的每个话题含：`id / uuid / title / content / contentImgs（分号分隔的原图 URL）
/ reply_count / create_time / create_puid` 等，**一次请求即可拿到作答所需的全部素材**。

**② 话题详情页 HTML**（为了拿 `urlToken`）

```
GET https://groupweb.chaoxing.com/course/topic/v3/bbs/{bbsid}/{话题uuid}/replysList?...
```

页面源码中内嵌 `urlToken:'<32位hex>'`，正则提取即可。

**③ 发布回复**

```
POST https://groupweb.chaoxing.com/pc/invitation/{话题uuid}/addReplys
Body: courseId & classId & replyId=-1 & uuid(随机uuid4) & topic_content
      & anonymous= & urlToken & bbsid
```

**踩过的两个坑（踩坑记录）：**

1. **`topic_content` 是双重 URL 编码**：页面 JS 先 `encodeURIComponent` 一次，
   表单序列化再编码一次。Python 侧需 `urlencode({"topic_content": quote(text)})`，
   否则服务器收到乱码。
2. **`urlToken` 必须从详情页 HTML 提取**：最初用随机 32 位 hex 伪造，服务器返回
   `"发布失败，请勿同时在多个网页发布话题或回复！"`。定位过程：抓包对比浏览器成功
   请求与脚本失败请求的全部字段，唯一差异即 `urlToken`；进一步在详情页源码中
   搜索该值得到答案——它是服务端在页面渲染时下发的一次性令牌。

此外，课程页地址栏的 `enc` 参数会过期（显示"无效的参数"），需从
`i.chaoxing.com` 课程列表重新点击进入获取新链接。

### 3.3 回答策略与过滤规则

单一 prompt 同时完成三件事（一次调用，省时省费）：

1. 图片模糊/看不清 → 返回 `UNCLEAR`，跳过；
2. 非数学问题（行政、分数、闲聊）→ 返回 `NOT_MATH`，跳过；
3. 否则输出**纯文本**中文解答：关键思路 → 主要步骤 → 易错点；
   公式以行内文字（`x^2`、`∫`、`lim`）表示，适配超星纯文本回复框。

补充的硬性规则（代码层，不依赖模型判断）：

- **基线机制**：首次运行把当时已有话题全部记为 `baseline`，只回答之后新发的，
  避免一次性刷屏历史 124 条帖子；
- 跳过自己发的帖（`create_puid == MY_UID`）；
- 单个话题最多尝试 3 次（网络/接口异常），之后标记 `gave_up`。

### 3.4 状态与存储

- `D:\ecnu_math_bot\images\`：帖子图片临时工作区（按 `{话题id}_{序号}.jpg` 命名）；
- `D:\ecnu_math_bot\handled.json`：处理状态 `{"handled": {id: 原因}, "attempts": {...}}`，
  原因取值 `answered / unclear / not_math / baseline / own_post / gave_up ...`，
  保证每个话题只处理一次、重启不重复回复；
- 图片工作区按用户要求放在 D 盘，避免占用系统盘。

### 3.5 凭据过期与自动续期（登录守护进程）

超星 Cookie 中的 `p_auth_token` 是登录后 **7 天绝对过期**（JWT exp），`JSESSIONID`
可能因闲置更早失效。若不处理，机器人会静默空转——表面在跑，实际一条都答不了。

方案由 `login_watch.py` 守护进程实现，全链路无人值守：

1. **静默检测**：用 `pythonw` 启动，无任何窗口；每 5 分钟调用一次 `getTopicList`，
   服务器返回 `{"status":false,"msg":"用户信息异常，请重新登录"}` 即抛出
   `AuthExpired` 判定失效。刻意区分三种结果：**有效 / 失效 / 网络异常（None）**，
   网络抖动不会误弹登录窗。
2. **自动弹窗**：失效时写入 `cookie_expired.flag`，并弹出一个**专用 Chrome 登录窗口**
   （独立 profile 存于 `D:\ecnu_math_bot\chrome_profile`，开启 CDP 调试端口 9333，
   不影响用户正在使用的浏览器），等待人工登录（最长 10 分钟，超时下次检测再弹）。
3. **自动抓取**：登录后守护进程用**纯标准库实现的 WebSocket 客户端**连接 CDP，
   调用 `Storage.getCookies` 抓取全部 `.chaoxing.com` Cookie；随后通过
   `Target.createTarget` 打开讨论区页面补齐 groupweb 会话 Cookie（`urlToken`
   依赖该会话），原子写入 `D:\ecnu_math_bot\cookie.txt`，再用真实接口校验，通过即关窗。
4. **免重启恢复**：`chaoxing_api.current_cookie()` 每次请求都重读 `cookie.txt`
   （存在则覆盖 config 中的静态 Cookie），bot 主循环无需重启、下一轮自动恢复。
5. 工程细节：PID 文件单实例锁、日志写入 `watchdog.log`（超 500KB 自动轮转）、
   弹窗前落 flag 文件便于外部观察状态。

**实测记录（2026-10-09）**：强制走一次续期流程，19:20:42 检测到失效并弹窗，
19:21:01 人工登录完成，19:21:05 新 Cookie 校验通过，全程 23 秒。

## 4. 实施过程

| 步骤 | 内容 | 结果 |
|------|------|------|
| 1 | 创建 D 盘图片工作区 `D:\ecnu_math_bot\images` | ✅ |
| 2 | 验证 ecnu-max 视觉接口（用课程页截图测试） | ✅ 正确描述页面 |
| 3 | 外部 Chrome 登录超星（内置浏览器故障，改用 Chrome DevTools MCP） | ✅ |
| 4 | 进入讨论区，抓包分析列表/详情/回复接口 | ✅ |
| 5 | Python 复现 getTopicList（Cookie 鉴权） | ✅ 返回 20 条话题 |
| 6 | 端到端测试：下载图片 → ecnu-max 作答 | ✅ 三道极限题完整解答 |
| 7 | 浏览器手工发 1 条回复，抓取 addReplys 报文 | ✅ 回复发表成功 |
| 8 | 脚本发回复失败（伪造 urlToken）→ 定位并修复 | ✅ 回复发表成功 |
| 9 | 状态文件 + 基线机制 + 后台轮询上线 | ✅ 90 秒/轮 |
| 10 | 敏感信息剥离（config.py + .gitignore）后开源 | ✅ |
| 11 | 登录守护进程（静默检测 + 弹窗续期 + CDP 自动抓 Cookie） | ✅ 23 秒完成续期 |

## 5. 测试与验证结果

| 测试项 | 结果 |
|--------|------|
| ecnu-max 图片识别（课程页截图） | ✅ 正确识别为泛雅讨论区页面 |
| 话题列表接口（20 条分页数据） | ✅ 字段完整，含图片 URL |
| 图片下载（`p.cldisk.com` 原图） | ✅ 单图/三图均成功 |
| 解题质量："这三题"（3 张图、3 道极限题） | ✅ 给出 e^(-1)、15150、1 的完整推导与易错点 |
| 解题质量："练习"（伯努利不等式证明图） | ✅ 识别图片内容并补全思路（API 发布，回复数 +1） |
| 浏览器发布回复 | ✅ 回复发表成功 |
| 脚本发布回复（修复 urlToken 后） | ✅ 回复发表成功 |
| 跳过逻辑 | ✅ 非数学帖/图不清返回标记不发布 |
| 重启去重 | ✅ handled.json 幂等，不重复回复 |

## 6. 安全与合规

- **敏感信息隔离**：API key 与超星 Cookie 全部移入 `config.py`，该文件被
  `.gitignore` 排除；仓库只提供 `config.example.py` 模板，报告与提交历史中不含任何凭据。
- Cookie 相当于账号密码，**切勿提交或外传**；登录过期（约 7 天）后需重新抓包更新。
- 本项目仅用于课程学习与技术研究；使用自动回复请遵守超星平台规范与课程讨论区约定，
  控制回复频率与质量，避免刷屏。

## 7. 使用方法

```bash
# 1. 复制配置模板并填入自己的 API key / Cookie / 课程参数
cp config.example.py config.py

# 2. 首次运行：记录已有话题为基线，开始后台轮询
python bot.py

# 常用参数
python bot.py --once            # 只跑一轮
python bot.py --backfill 3      # 首次运行时留最新 3 条参与处理
```

## 8. 局限与后续改进

**局限：**
- 依赖浏览器 Cookie，登录过期需人工重新抓包；
- 图片识别与解题质量取决于 ecnu-max，极潦草的手写仍可能误判（已有 UNCLEAR 兜底，
  但非 100%）；数学证明类问题的"对错"本身有主观性；
- 轮询式架构，单机运行，电脑关机即停止。

**改进方向：**
- 自动检测 Cookie 失效并提醒（或对接超星官方 API）；
- 回复前增加"历史回复上下文"，避免与楼层已有答案重复；
- 接入定时任务/云服务器实现 7×24 运行；
- 增加难度分级：超纲题只给提示，完整解答留给同学讨论。

---

*本报告与源代码同步开源，仅供学习交流。*
