# Agent API（零第三方依赖版）

一个 `python3 run.py` 就能跑的工具型 Agent：给一个 OpenAI 兼容模型接口，它就能读写文件、执行代码和 shell、看图片、给视频抽帧、加载 Skill、跑工作流、开子会话，并且能自己长出新工具（插件）。

**不装任何第三方 Python 包**：只用标准库 `http.server` + `urllib`。手机 Termux 上 `git clone` 完直接跑，不会再遇到装包报错。唯一的可选系统依赖是 `ffmpeg`（只有视频抽帧/转音轨用到）。

---

## 它到底能干什么（对照常见疑问）

| 你会问的 | 实际答案 |
| --- | --- |
| 模型明明支持图片，为什么发图它不看？ | 以前确实不看——`run_python` 只能读字节，模型拿到的是文字。现在有专用工具 `view_image`，把图片以 `image_url` 多模态块直接塞进上下文，模型看到的是像素 |
| 为什么我的模型不在识图名单里？ | 名单只影响「自动判断」。默认逻辑：白名单命中=能看图，纯文本黑名单命中=不能，**其余一律按能看图处理**。sensenova / sensechat-v / 6.8-flash 已写进白名单 |
| 只能跑 Python，为什么不能跑 shell？ | `ENABLE_SHELL` **默认就是 true**，`run_shell` 与 `run_python` 同权，都在沙箱目录里跑 |
| 视频能不能抽帧？ | `video_probe`（读时长/分辨率/帧率/编码）、`video_frames`（按数量或时间点抽帧，产物能当附件喂给模型看）、`video_audio`（导音轨成 mp3/wav/m4a） |
| 删东西有高危授权吗？ | 有闸门 `guard`：静态规则 + 破坏面估算 + 受保护目录，命中就拒绝执行，回一句「为什么危险」，你明确同意后模型带 `confirm=true` 才会真跑 |
| 能自己长能力吗？ | 有插件系统，10 个工具（`plugin_create` / `validate` / `enable` / `update` / `reload` / `disable` / `delete` / `list` / `read`），Agent 自己写 `.py` 进去就能多出工具 |
| 别人打开我的地址也能用？ | 建了账号就要登录；还支持 Google 登录、给脚本发 API 密钥 |
| 忘了密码怎么办？ | 用手机上的身份验证器（Google Authenticator）输 6 位码，或用绑定的 Google，或用注册时给的恢复码，不依赖发邮件 |
| 能用手机上的身份验证器吗？ | 能：账号与安全里点「开始绑定」，扫二维码（或手输密钥），之后登录要密码 + 6 位动态码 |
| 底部那个「缓存 62% · 8.1k/13.1k」是什么？ | 本会话累计的**提示 token**里有多少走了服务商的 prompt cache（省钱的那部分）。分子=命中缓存，分母=提示 token，悬停能看到生成 token 和调用次数。切会话、刷新页面都会重新算；服务不返回用量（或模型不支持）时显示「缓存 —」，不会瞎编 |
| 网页上冒出红字 `ConnectionAbortedError: [Errno 103]`？ | 是手机息屏/切后台把连接掐了，不是代码错。现在：服务端每 10 秒发一次心跳、断流时把已收到的内容保留并标注「被截断」、模型流没吐字就断开自动重试 3 次；界面只提示一句「连接断了，再发一次」 |
| PDF / Word 能读吗？ | 能，而且不是只抓文字：`view_document` 按页抽文字，再把页面渲染成 PNG 挂进上下文，表格、版式、印章、扫描件模型都看得见。默认看前 4 页，`pages="5-8"` 接着看，单次最多 12 页 |
| Termux 里要装什么？ | 基础功能零依赖。想看 PDF 的页面图，装一句 `pkg install poppler`（顺带得到 `pdftotext`）；想让 Word 也整页转图，再装 `pkg install libreoffice`。不装也能读 Word 文字 |
| 能不能出声？ | 有 TTS：`speak_text` 工具 + 网页「语音合成」+ 终端 `/say`，产物是能直接播的音频 |
| 能不能自己按时干活？ | 有定时任务：cron 表达式到点自动跑一句话或一个工作流，网页/终端/Agent 三处都能建 |
| 会话都是「无标题」？ | 首轮对话后按首句自动起标题（`_guess_title`），不再是清一色无标题 |

---

## 快速开始

```bash
git clone https://github.com/cvcxx000/agent-api
cd agent-api
cp .env.example .env      # 填 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
python3 run.py
```

起来之后两种用法都能用：

- **网页**：浏览器开 `http://127.0.0.1:8000/ui`
- **终端**：另开一个窗口 `python3 scripts/tui.py`

`http://127.0.0.1:8000/health` 返回 `{"ok": true}` 就是好了。

没有 Key 也能先验链路：`LLM_MOCK=auto`（默认）时会用内置 MockLLM 真跑一次 `run_python`。

### 手机（Termux）

```bash
pkg install python git
# 只有抽帧/转音轨需要，别用就不装
pkg install ffmpeg
git clone https://github.com/cvcxx000/agent-api
cd agent-api && python run.py
```

手机浏览器开 `http://127.0.0.1:8000/ui`。想后台常驻：

```bash
nohup python run.py > api.log 2>&1 &
termux-wake-lock
```

---

## 网页 UI（`/ui`）

单文件 HTML，无 CDN、无构建，iOS inset grouped 打底，自带深色模式：

- **提供商**：先从目录添加（11 家模板，只给地址不预置假模型）→ 填 Key → 「拉取模型」调服务商 `GET /models`，列表里都是真实存在的模型；点一行立即切换
- **视觉开关**：每个提供商可单独开/关「支持图片输入」。关掉时附件不会静默丢弃，而是明确警告「附件没有发出去：当前模型被判定为不支持看图」
- **思考过程**：`reasoning_content` / `reasoning` / `thinking` 三种字段都认，单独折叠成一张卡
- **工具调用**：折叠卡，参数和输出都在，失败标红
- **附件**：粘贴或选图，画板 `✎` 手绘也能直接附上（落 `uploads/`，≤8MB）
- **产物**：出图、出片、搜索结果都在对话里直出，Markdown 链接可点、代码块一键复制
- **会话**：抽屉里列全部会话，标题自动生成，可删
- **设置**：默认模型、提供商、搜索服务、图片生成、视频生成、Web 服务器、数据备份、引擎与限流

---

## 账号与登录

**默认行为**：`/ui` 一律先登录。第一次打开没有账号时，第一屏就是「创建第一个账号」，建完立刻生效 —— 不会出现「别人打开我的地址也能直接用」的空窗。

如果你确实想回到「有账号才要求登录」的宽松模式，在 `.env` 里写 `AUTH_MODE=auto`；要完全关掉账号体系写 `AUTH_MODE=off`。

三种进门方式：

- **密码登录**：邮箱 + 密码（`hashlib.scrypt` 存哈希，不存明文）
- **Google 登录**：`Sign in with Google`，OAuth 2.0 授权码流程，零依赖手写
- **密钥登录**：`ak_…` 的 API 密钥，给终端、脚本、别的设备用

### 注册

第一次打开 `/ui` 会被送到 `/login`，显示「创建第一个账号」（`AUTH_MODE=off` 除外）。第一个账号是 **owner**（这台设备的主人），会同时给你一串 **恢复码**：

```
563FD9-CCEDAC-D97F6B
```

**恢复码只显示这一次**，抄到纸上或存进密码管理器 —— 忘记密码、又没绑 Google 时，只能靠它。之后在「设置 → 账号与安全」里随时能重新生成。

注册默认只允许第一个人。想让别人也注册，owner 在「账号与安全 → Google 登录」里勾上「允许别人注册新账号」。

### 忘记密码（不靠邮件）

点登录页「忘记密码？」，填邮箱，会告诉你这个账号有哪些可用方式：

1. **身份验证器（Google Authenticator，推荐）**：输入手机上的 6 位动态码（或一个没用过的备用码）→ 直接设新密码。
2. **Google 身份认证**：跳 Google 授权 → 验证你确实是这个 Google 账号的主人 → 回到本站，拿一张 10 分钟有效、只能用一次的重置票 → 设新密码。全程不需要 SMTP。
3. **恢复码**：输入注册时那串 → 设新密码。旧恢复码立刻作废，同时给你一串新的。

重置成功会**把所有设备下线**（包括当前浏览器），必须重新登录 —— 这是故意的。

### 身份验证器（Google Authenticator）

这是最省事的一条：不依赖 Google 客户端 ID，也不用发邮件。

绑定：

1. 设置 → 账号与安全 → 身份验证器 → 「开始绑定（生成二维码）」
2. 手机装 Google Authenticator（或任意兼容 TOTP 的应用：Aegis、1Password、Microsoft Authenticator 都行）
3. 扫页面上的二维码；扫不动就点「手动输入」填那串 32 位密钥（类型选「基于时间」）
4. 输入手机上出现的 6 位码，点「确认启用」
5. 页面会给你 **10 个备用码**（`XXXX-XXXX`），只显示这一次，抄下来 —— 手机丢了就靠它

启用后：

- **登录**变成两步：密码 → 6 位动态码。同一个码不能重复用（防止重放）
- **忘记密码**多了一条：输 6 位码（或备用码）直接设新密码，不需要邮件、不需要 Google
- 备用码**每个只能用一次**；用完了在「账号与安全」里重新生成（要输一次当前动态码）

实现细节：标准 TOTP（HMAC-SHA1、30 秒一格、6 位、前后各容忍一格时钟差），二维码由项目自带的纯 Python 编码器生成（不联网、不调用第三方服务）。

### Google 登录怎么配

1. 打开 https://console.cloud.google.com/apis/credentials → 「创建凭据」→「OAuth 客户端 ID」→ 类型选 **Web 应用**
2. 「已获授权的重定向 URI」填：

   ```
   http://127.0.0.1:8000/api/auth/google/callback
   ```

   端口要和你实际跑的一致。设置页会把这条现成的地址显示出来，直接复制。
3. 建好后把 **Client ID / Client Secret** 粘到：设置 → 账号与安全 → Google 登录 → 保存
4. 回到 `/login`，就会出现「用 Google 登录」

**两个坑提前说清**：

- Google **只允许 `127.0.0.1` / `localhost` 的回环地址用 http**。你用 `http://192.168.1.x:8000` 打开时，Google 会以 `redirect_uri_mismatch` 拒绝 —— 这是 Google 的规矩，不是本项目的 bug。手机自己跑服务时就是 `127.0.0.1`，没问题。
- 同一个邮箱先用密码注册、后来又用 Google 登录，会自动把两者**绑到同一个账号**（不会多出一个空账号）。

### 密钥登录（给脚本 / 终端）

「账号与安全 → API 密钥」里生成，形如 `ak_...`，**只显示一次**。之后两种带法都认：

```bash
curl -H "Authorization: Bearer ak_xxx" http://127.0.0.1:8000/api/sessions
curl -H "X-API-Token: ak_xxx"        http://127.0.0.1:8000/api/sessions

python3 scripts/tui.py --token ak_xxx
```

密钥可以起名字（Termux / 笔记本 / 某个脚本）、看最后使用时间、随时吊销。删掉某个密钥不影响其它密钥，也不影响网页登录。

**API 密钥和 `API_TOKEN` 的区别**：`API_TOKEN` 是全局管理员钥匙，一个通吃，适合完全信任的脚本；API 密钥是账号自己的，可吊销、能看出是谁在用。

### 游客试用（临时匿名）

登录页有一行「游客试用（临时匿名，24 小时后自动清空）」，点一下就能用，不用注册。它适合「别人临时想试一下这台设备」的场景：

- **只活在它自己那一个 ID 里**：每个游客有独立目录 `workspace/guests/<gid>`，上传、读文件、生成图片 / 语音 / 视频、文档页图全都落在里面。请求进来时沙箱根会被换成这个目录（`security.active_root()`，请求级 contextvar），`../` 和绝对路径一样越不出去。
- **不能升级成正式账号**：角色固定 `guest`，改密码 / 生成 API 密钥 / 绑身份验证器 / Google 绑定 / 改提供商和全局设置全部 403；到期自动删除账号、会话和目录。
- **一分钟一条**：`/api/chat` 与 `/api/chat/stream` 按游客上次发言时间限流，第二条立刻 429（`code=guest_rate_limit`）。API 端也能用，适合服务器端冒烟测试。
- **100MB 独享空间**：上传前按目录实际占用算，放不下直接拒绝；开聊前也会查一次，满了就提示删自己的文件。
- **Python / 终端要解锁码**：开游客时返回一串 `XXXX-XXXX` 的解锁码（只显示这一次）。没解锁时 `run_python` / `run_shell` 工具直接不可见、直接调用也返回 ERROR；账号面板或终端 `/unlock 解锁码` 之后才开放，开放后仍然只能动自己的目录。
- **其余闸门**：子 Agent、插件、定时任务、Cookie、工作流、搜索接口、`/api/execute/python` 一律不给；能用的只有聊天、会话、自己的文件、技能、上传、预览、TTS 出声。

相关配置：`GUEST_ENABLED=1`（默认开；`0` 关掉）、`GUEST_TTL_HOURS=24`、`GUEST_QUOTA_MB=100`、`GUEST_RATE_SECONDS=60`。终端里 `python3 scripts/tui.py` 后直接 `/guest` 开一个，`/unlock 码` 解锁，`/whoami` 看剩余身份。

### 商家版（卖订阅）

第一次打开会进 `/setup` 初始化页，先问一句「这台设备怎么用？」：

- **个人版**：一台设备一个主人，用自己的模型 Key；注册默认关，游客按 `GUEST_ENABLED` 决定。
- **商家版**：商家配自己的模型 Key，用户自己注册、自己买套餐；每个会员独立目录和额度。
- **没选**：按个人版规则跑（单主人、注册关），直到你选。

选完会写进 `data/deploy.json`；也可以用环境变量 `DEPLOY_MODE=merchant` 直接锁死，锁死后网页不能再改。

初始化页会一次做完：选模式、填管理员邮箱 / 密码、勾权限（游客、注册、兑换码）、勾同意《用户协议》《商家协议》。建完直接登录进 `/ui`。

商家版的完整流程：

1. 第一个注册的人是 `owner`（商家）。他在「设置 → 模型」里填一次模型 Key，所有会员共用这一个上游。
2. 「账号与安全 → 商家管理」里改套餐：默认 `start`（30 天 / 300 条 / ¥9.90 / 1GB）和 `pro`（90 天 / 1500 条 / ¥26.90 / 3GB），价格、天数、条数、空间都能改。
3. 先在「商家后台」里**确认开启兑换码**（默认是关的，点开启会弹安全警告：码等于现金，泄露会被别人兑走）。开启后生成兑换码（`AG-XXXX-XXXX`，一次一批）。**码只显示这一次**，服务端只存哈希和掩码，丢了只能重新生成。
4. 把码卖给用户。用户自己注册，进「账号与安全 → 订阅」输入码，立刻开通；再输一张就在原到期时间上往后加、条数额度累加。
5. 兑换码规则：**一个码全局只能兑一次**，谁先兑谁得，兑完立刻失效，别人再用会提示「已经被用过了」；同一个人可以拿多张不同的码续期，到期时间往后加、条数累加。
   - 商家后台有「兑换码记录」：没用的码可以作废；已用的码点「回滚并收回订阅」会清掉使用者的整份订阅，再把码放回未使用（不能只放回码，否则一码能换两次订阅）。
   - 会员额度列表里也能单独「清空订阅」，用于退款、测试或换套餐。
6. 用户聊天时按条数扣（`/api/chat` 与 `/api/chat/stream` 都算一条），`tokens_used` 只记账不影响额度；额度或有效期用完返回 `402 subscription_required` / `429 subscription_quota`。

会员的边界：

- 文件独立：`workspace/users/<uid>`，跟游客一样是请求级沙箱根，`../` 出不去；上传和 `write_file` 按套餐 `storage_mb` 卡上限。
- 不能改全局：提供商、设置、搜索 / 图片 / 视频配置、备份、Cookie、插件、定时任务、工作流全部 403；`/api/tools` 里也看不到这些工具。
- 不能跑本机代码：`run_python` / `run_shell` 对会员直接隐藏 + 拒绝（共享主机，多租户不该给 shell）。文件读写、看图、搜网页、生成图片 / 语音 / 视频照常。
- 自己的账号功能照常：改密码、身份验证器、API 密钥、退出登录。

**收钱这一步**：兑换码不需要任何支付资质，先卖卡最省事。想「付完自动发码」，支付宝当面付 / 手机网站支付要**商户号**（个体户或企业，个人收款码不能自动核单），拿到后在异步通知里调一次发码 / `billing.redeem()` 就行，业务逻辑不用改。

### 初始化与重置

- 第一次部署：打开 `/setup`，选模式、建管理员、设权限、勾协议，一步完成。第一个账号就是 `owner`。
- 老账号库如果没有 owner（历史版本建的号），服务启动后会把最早注册的正式账号自动升成 `owner`。
- 管理员在「账号与安全 → 初始化 / 重置」里可以重置：要输管理员密码 + 再输 `RESET` 确认。重置会删除 `auth.json`、`billing.json`、`deploy.json`、`policy.json`，回到 `/setup`；**会话和 workspace 文件保留**。
- 协议是模板：`/terms/user`、`/terms/merchant`，正式对外前请让律师按经营地改写。

### 安全上做了什么

- 密码 scrypt（n=16384, r=8, p=1）+ 随机 salt，比较用常量时间
- 登录态是 32 字节随机 token，服务端只存 sha256；Cookie 是 `HttpOnly; SameSite=Lax`，JS 读不到，跨站 POST 带不上
- 连续错 5 次锁 15 分钟（锁定期正确密码也登不进去）
- 一个账号最多 20 个登录会话，超了踢最旧的；「退出所有设备」一键清
- 设备列表能看到 IP / 客户端 / 登录时间
- 改密码、重置密码都会把其它设备下线
- 所有认证动作写日志（`agent.auth`），服务端日志里能查
- `data/auth.json` 权限 600，里面只有哈希，没有明文密码和明文 API 密钥
- 唯一的例外是身份验证器的 TOTP 密钥（要算 6 位码必须留原文），也只在这台设备的 `auth.json` 里，不上传、不联网

**边界说明**：个人版是「一台设备一个主人」，多个账号共用同一个 workspace；商家版的会员有独立目录 `users/<uid>`，但仍然是同一个进程、同一套上游 Key，适合小规模自用 / 熟客订阅，不适合当公网多租户 SaaS 直接开放。真要卖给陌生人，至少加 HTTPS 反向代理、限制注册、给每个会员单独限速，并接受「会员能看到商家配的模型名」这一点。

## 终端界面（`scripts/tui.py`）

```bash
python3 scripts/tui.py                                   # 默认 http://127.0.0.1:8000
python3 scripts/tui.py --base http://127.0.0.1:8000 --token 你的TOKEN
```

| 命令 | 作用 |
| --- | --- |
| `/model` | 列真实模型挑编号，立即切换 |
| `/login 邮箱 密码` | 终端里直接登录（账号开了两步验证就再加 6 位码）；Cookie 存 `~/.agent-api-tui.json`，下次启动自动带上 |
| `/logout` | 退出登录，清掉本地 Cookie |
| `/whoami` | 看现在用的是哪个账号 / 密钥身份 |
| `/say 文字` | 合成语音（TTS），打印音频地址；Agent 自己念的话终端里显示 `语音：<地址>` |
| `/cron` | 列出定时任务，输编号立刻跑一次；`/cron on|off 编号` 开关，`/cron del 编号` 删除 |
| `/sessions` | 列出所有会话（带 id 和标题），可以挑一个继续 |
| `/session` | 看当前会话 id 与标题 |
| `/new` | 开新会话 |
| `/skills` | 看这个服务端有哪些 Skill |
| `/see 路径` | 直接把一张图发给模型看（走 `view_image` 同一条通路） |
| `/open 路径` | 挂一份 PDF / Word：自动抽文字、前几页自动转成图片，下一句一起发出去 |
| `/image 提示词` | 生成图片 |
| `/video 提示词` | 生成视频（提交→轮询→下载 mp4） |
| `/search 关键词` | 走设置页配的搜索服务 |
| `/md 路径` | 把 Markdown 渲染成终端样式 |
| `/file 路径` | 预览沙箱内文件 |
| `/help`、`/quit` | 帮助、退出 |

思考（暗色）、正文（绿色）、工具卡（品红）都是流式打印。

### 别的入口

```bash
python3 scripts/repl.py          # 轻量 REPL：/new /session /system /system- /help /exit
python3 scripts/chat_cli.py      # 一次性问一句，拿答复就走，适合脚本调用
python3 scripts/smoke_test.py    # 端到端自检
```

---

## 配置（.env）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | 监听地址。只本机用可设 `127.0.0.1` |
| `API_TOKEN` | 空 | 留空不鉴权；对外暴露务必设置，请求头 `X-API-Token` |
| `LLM_BASE_URL` | `https://api.openai.com/v1` | 任意 OpenAI 兼容接口（DeepSeek / 通义 / 日日新 / vLLM / Ollama…） |
| `LLM_API_KEY` | 空 | 模型 Key |
| `LLM_MODEL` | `gpt-4o-mini` | 模型名 |
| `LLM_TEMPERATURE` / `LLM_MAX_TOKENS` / `LLM_TIMEOUT` | `0.3` / `4096` / `120` | 采样与超时 |
| `LLM_MOCK` | `auto` | `auto`=没 Key 用 Mock；`true`/`false` 强制 |
| `RATE_LIMIT_PER_MIN` | `7` | 上游每分钟请求上限，主/子 Agent 共用；`0`=不限 |
| `AGENT_MAX_TURNS` | `16` | 单轮最多几次「模型→工具」循环 |
| `AGENT_HISTORY_LIMIT` | `80` | 发给模型的历史条数上限 |
| `AGENT_MAX_SUBAGENTS` | `3` | 子 Agent 并发上限 |
| `AGENT_MAX_SUBAGENT_DEPTH` | `1` | 1 = 子 Agent 不能再派子 Agent |
| `MAX_TOOL_OUTPUT_CHARS` | `20000` | 单个工具输出回传模型的字数上限 |
| `ENABLE_PYTHON` | `true` | 允许 `run_python` |
| `ENABLE_SHELL` | **`true`** | 允许 `run_shell`（同权，都在沙箱内）。只想给 Python 就改 false |
| `PYTHON_TIMEOUT` / `SHELL_TIMEOUT` | `60` / `60` | 单次执行超时（秒，上限 600） |
| `DANGEROUS_CONFIRM` | `true` | 高危命令闸门开关。`false` = 关掉拦截，不建议 |
| `SANDBOX_ROOT` | `./workspace` | 文件与执行的沙箱根（游客换 `guests/<gid>`，商家会员换 `users/<uid>`） |
| `DATA_DIR` | `./data` | 会话、Cookie、插件状态、临时脚本落盘位置；`deploy.json`、`billing.json` 也在这里 |
| `SKILLS_DIRS` | `./skills,./workspace/.skills` | Skill 搜索目录（逗号分隔） |
| `WORKFLOWS_DIR` | `./workflows` | 工作流目录 |
| `DEPLOY_MODE` | 空 | `personal` / `merchant`。空 = 第一次打开让你选；设了就锁死，网页不能改 |
| `GUEST_ENABLED` | `1` | 游客试用开关。`0` = 登录页不出现游客入口 |
| `GUEST_TTL_HOURS` | `24` | 游客活多久（小时），到期自动删账号 / 会话 / 目录 |
| `GUEST_QUOTA_MB` | `100` | 每个游客的独立空间上限（MB） |
| `GUEST_RATE_SECONDS` | `60` | 游客两条消息的最小间隔（秒） |
| `SYSTEM_PROMPT` / `SYSTEM_PROMPT_FILE` | 空 | 覆盖默认系统提示词 / 从文件读 |

### 限速说明（测试 Key 只有 7 次/分钟）

`RATE_LIMIT_PER_MIN=7` 时所有请求排队匀速发出（约 8.6 秒一次），遇到 `429` 读 `Retry-After` 自动退避（1~60 秒）。注意 Agent 回一句话可能花掉多次请求（调工具→再调模型），7 次额度下大约能跑 3~4 轮工具循环。正常额度的 Key 设 `0`。

### 日日新（商汤 SenseNova）

路径只填到 `/v1`，程序自己拼 `/chat/completions`：

| 平台 | `LLM_BASE_URL` | 常见模型 ID |
| --- | --- | --- |
| Token Plan | `https://token.sensenova.cn/v1` | `sensenova-u1.5-lite` 等 |
| 大装置 ModelStudio | `https://api.sensenova.cn/compatible-mode/v1` | `SenseChat-5`、`SenseChat-5-1202` |

```bash
curl -s https://token.sensenova.cn/v1/chat/completions \
  -H "Authorization: Bearer 你的Key" -H 'Content-Type: application/json' \
  -d '{"model":"你的模型ID","messages":[{"role":"user","content":"说一句你好"}]}'
```

返回有 `choices` 就是通了；`401` 是 Key 错，`404` 是模型名不对。

---

## 看图（`view_image`）

模型支持多模态，但工具返回只能是文字——这是以前的断点。现在：

```
view_image("uploads/pic.png")   # 或 /see uploads/pic.png 在终端里
```

流程是：校验后缀（png/jpg/jpeg/webp/gif）→ 大小 ≤4MB → `resolve_in_sandbox` 防越界 → 先问提供商视觉开关（关着就直接拒绝，不浪费一次请求）→ 返回 `VIEW_IMAGE: <绝对路径>` → agent 抓到这个标记，在该轮工具循环结束后追加一条带附件的 user 消息，`_parts_for` 拼成 `image_url` data:URL。

所以模型拿到的不是「文件名」，是像素。每轮最多 4 张。

**识图判定三层**（`providers.py`）：

1. `_VISION_RE` 白名单命中 → 能看图（`vision`、`-vl`、`4o`、`4.1`、`gemini`、`claude-3`、`glm-4v`、`omni`、`multimodal`、`pixtral`、`llava`、`qwen2.5-vl`、`moonshot`、`kimi`、`sensenova`、`sensechat-v`、`6.8-flash`…）
2. `_TEXT_ONLY_RE` 命中 → 纯文本（`deepseek-chat`/`reasoner`/`coder`/`v3`、`gpt-3.5`、`o3-mini`、`text-davinci`、`claude-2`、`qwen-max`、`chatglm-2/3`、`ernie-speed`/`tiny`、`command-r`、`yi-large`/`medium`）
3. 都不命中 → 默认能看图

## 文档（`view_document`）

PDF 和 Word 走同一条路：**先抽文字，再按页拆成图片**，两样一起进上下文。

```
view_document("uploads/合同.pdf")                 # 默认前 4 页
view_document("uploads/合同.pdf", pages="5-8")    # 接着看下一片
view_document("uploads/说明书.docx", dpi=150)      # 清晰度调高
```

- **PDF 文字**：有 `pdftotext` 就用它（版式保持得最好）；没有就退回内置的极简解析（解 FlateDecode + 扫 `Tj/TJ`，普通文本 PDF 够用，扫描件和复杂中文字体不保证）
- **PDF 图片**：`pdftoppm` → `mutool` → `gs` 依次尝试，三者有其一就能出图；模型看到的是 `image_url` data:URL，不是文件名
- **DOCX**：zip + XML 抽段落文字（零依赖，永远可用）；`word/media/` 里的插图原样抽出来给模型看；装了 `soffice` 就整份转 PDF 再按页渲染
- **.doc**：需要 `soffice`
- **分片**：单次最多 12 页，返回里会写「还有 N 页没看」和下一段的调用参数，不会一口气把 200 页塞爆上下文

**附件自动挂载**：网页上传或终端 `/open` 挂上的 PDF / Word，服务端在开跑前就把文字抽好、前 6 页渲染成 PNG，塞进 `attachments`，跟图片走同一条 `_parts_for` 通路。你不用先喊一句「请调用 view_document」。

**Termux 依赖**（都是可选的系统命令行，不是 pip）：

```bash
pkg install poppler        # pdftoppm + pdftotext + pdfinfo：PDF 页面图与文字
pkg install libreoffice    # soffice：Word / .doc 整页转图（比较大，按需装）
```

没装 poppler 时功能不会崩：照样给 PDF 文字，并在结果里写一句「这台机器没有 PDF 渲染器（Termux 里跑一句 pkg install poppler 就好）」。

---

## 视频三件套（需要 ffmpeg）

| 工具 | 作用 |
| --- | --- |
| `video_probe` | 读时长、分辨率、帧率、编码。先看这个再决定怎么处理 |
| `video_frames` | 抽帧：默认 6 张，可指定数量、时间点、缩放；产物落 `media/`，可直接 `view_image` 喂给模型看 |
| `video_audio` | 导音轨成 mp3 / wav / m4a |

没装 ffmpeg 时会直接告诉你命令（`pkg install ffmpeg` / `apt install ffmpeg`），不会给你一个看不懂的报错。

---

## 语音合成（TTS）

零依赖直接调 OpenAI 兼容的 `POST /audio/speech`，回来的是音频字节，落到 `workspace/media/`，网页 `<audio>` 直接播。

三处入口：

- **网页**：设置 → 语音合成，选预置 → 填 Key/模型/音色 → 保存 → 「试朗读」
- **终端**：`/say 你好，我是你的 Agent`
- **Agent**：`speak_text(text, voice?, speed?, format?)`，返回 `MEDIA_AUDIO: /files/media/xxx.mp3`，网页聊天里自动出现播放器

预置三家：OpenAI TTS（`gpt-4o-mini-tts`，音色 alloy/echo/fable/onyx/nova/shimmer）、硅基流动 CosyVoice、自定义（任何实现 `/audio/speech` 的中转站）。

细节上都做了防呆：没配置时直接说「设置 → 语音合成」而不是报 500；一次最多 4096 字；返回 JSON 而不是音频时（中转站出错也回 200 的常见坑）会当成错误抛出来，不会把错误信息存成一个「音频文件」。

## 定时任务

到点自动跑，不用你记着催。任务存 `data/schedules.json`，调度线程每 20 秒检查一次。

**cron 写法**：5 段（分 时 日 月 周），支持 `*`、`*/n`、`a-b`、`a-b/n`、逗号列表、月份/星期英文缩写，外加 `@hourly` `@daily` `@weekly` `@monthly` `@every 10m` `@every 2h`。

```
0 9 * * *        每天 09:00
*/15 * * * *     每 15 分钟
30 8 * * 1-5     工作日 08:30
0 9 1 * *        每月 1 号 09:00
@every 2h        每 2 小时
```

**两种动作**：

- `kind=chat`：到点把 `prompt` 当成一句话发给 Agent（能调工具、能读写文件）
- `kind=workflow`：到点跑 `workflows/` 里的某个工作流，带 `inputs`

**行为约定**：

- 每个任务有自己的会话 `[定时] 名字`，跑完的对话都在里面，能翻历史
- 同一个任务不会叠着跑：上一次没结束，这一次跳过
- 上次运行时间、状态、错误、耗时、跑过多少次都记在 `data/schedules.json`，网页和 `/cron` 都能看到
- 日 和 周 都限定的时候按 Vixie cron 老规矩：**任一命中即可**（`0 9 1 * 1` 是「每月 1 号或每周一」）
- 非法 cron 在创建时就拒：`0 25 * * *` → 「cron 取值 25 超出范围 0-23」
- **日历上不存在的时间也拒**：`0 0 31 2 *`（2 月 31 日）会被拒掉，而不是建一条永远不跑的死任务；闰日 `0 0 29 2 *` 则能正常算出 2028-02-29
- **改 cron 立刻生效**：不用等旧时间点触发，调度器检测到 cron 变了就按新表达式重算下一次
- **时区是服务器本地时区**（Termux 上就是手机时区）。写成 `0 9 * * *` 就是手机本地 9 点，不会按 UTC 算
- **同时最多跑 2 个**（`SCHEDULE_CONCURRENCY` 可调）；同一分钟撞上多个任务时，多的排队等，不会一起打模型把手机顶死
- **失败会写进会话**：任务没跑成（模型报错、工作流名写错）也会在 `[定时] 名字` 那个会话里留一条原因，不用去翻 JSON

**三处入口**：网页抽屉「定时任务」、终端 `/cron`、以及直接跟 Agent 说「每天早上 9 点帮我读一下日报」——它会调 `schedule_create`。

## 音频与视频能拖动进度

`/files/media/...`、`/files/uploads/...` 支持 HTTP Range（`206` + `Content-Range`）。这不是凑数：

- 浏览器 `<audio>` / `<video>` 要能拖进度、Safari 要见到 206 才肯播 TTS 的音频和生成的视频
- 大文件只读请求的那一段，不把几十 MB 整个读进内存（手机上很容易被 OOM 干掉）

## 会话导出

`GET /api/sessions/{id}/export` 直接把一个会话导成 Markdown（带每个角色的分段、工具输出），网页抽屉里也有「导出当前会话」。

## 高危命令闸门（`guard`）

三道关，任一中招就拒绝执行：

**1. 静态规则**——`rm -rf /`、`mkfs`、`dd of=/dev/`、`curl … | sh`、`chmod -R 777 /`、`sudo`、fork 炸弹、`reboot`/`shutdown`、`git push -f`、`git reset --hard`、`git clean -fdx`、`truncate -s 0`、`find / -delete`、Python 里的 `shutil.rmtree("/")` / `os.system("rm -rf …")` 等。

**2. 破坏面估算**——按真实文件系统递归数目标文件数：一次 ≥100 个文件要确认，扫描上限 5000；目标就是工作区根（`rm -rf .`）也会被拦。

**3. 受保护目录**——路径任一段命中 `uploads / outputs / media / data / plugins / skills / workflows / .git / .skills / .plugins` 就拒绝；`rm -rf *` 这类通配删除同样拦。

被拦时的回复会写清楚：**为什么危险、影响范围多大、要不要继续**。你明确同意后，模型带 `confirm: true` 重调即可放行——不是死锁，是让你知道自己在删什么。

---

## 插件系统（万物皆插件）

插件就是一个 `.py` 文件，放仓库根的 `plugins/`（跟着项目走、能提交）或沙箱里的 `workspace/.plugins/`（Agent 自己创建时写这里）。仓库自带一个示例 `plugins/word_count.py`，**默认是停用的**，跟 Agent 说「启用 word_count」它会让你确认一次。

两种写法都行，注意 handler 签名固定是 `handler(ctx, args) -> str`：

```python
# 写法 A：PLUGIN + register
PLUGIN = {"name": "word_count", "description": "统计文本字数", "version": "1.0", "tags": ["text"]}

def word_count(ctx, args):
    text = str(args.get("text") or "")
    return f"字符 {len(text)} · 汉字 {sum(1 for c in text if '\u4e00' <= c <= '\u9fff')}"

def register(registry):
    registry.register(
        "word_count",
        "统计一段文本的字符数 / 汉字数",
        {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        word_count,
    )
```

```python
# 写法 B：TOOLS 列表
TOOLS = [{
    "name": "reverse_text",
    "description": "把文本倒过来",
    "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    "handler": lambda ctx, args: str(args.get("text") or "")[::-1],
}]
```

规则与安全边界：

- 工具名 `^[a-z][a-z0-9_]{1,40}$`，重名会覆盖并记录
- 新建后**默认停用**；`plugin_enable` 必须用户明确同意后带 `confirm=true`
- `plugin_validate` 是干跑：只 import + register 到临时注册表，不启用、不写状态，语法/结构/重名问题当场返回
- 已启用的插件被 `plugin_update` 改写后**立即热重载**（先 unregister 再重新注册）
- 插件抛异常不会拖垮服务：错误被吞下并记进 `record.error`，`plugin_list` 里能看到
- 状态存 `data/plugins.json`，启动时只装 enabled 的那些

Agent 侧的工具：`plugin_list` `plugin_create` `plugin_update` `plugin_read` `plugin_validate` `plugin_enable` `plugin_disable` `plugin_reload` `plugin_delete`。

---

## 内置工具（51 个）

| 类别 | 工具 |
| --- | --- |
| 文件 | `read_file` `write_file` `edit_file` `list_dir` `make_dir` `move_path` `delete_path` `grep_files` |
| 执行 | `run_python` `run_shell`（都过闸门） |
| Cookie | `get_cookie` `set_cookie` `delete_cookie` `get_cookie_header` `import_cookie_from_storage_state` `import_cookie_from_browser` |
| 联网 | `web_search` `fetch_url` |
| 媒体 | `generate_image` `generate_video` `view_image` `video_probe` `video_frames` `video_audio` `speak_text` |
| 文档 | `view_document`（pdf / docx / doc：按页抽文字 + 页面渲染成图） |
| 定时 | `schedule_list` `schedule_create` `schedule_update` `schedule_delete` `schedule_run` |
| Skill | `list_skills` `read_skill` `load_skill` |
| 插件 | 上面那 9 个 |
| 子任务 | `spawn_agent` `wait_agent` `list_agents` `cancel_agent` |
| 工作流 | `list_workflows` `run_workflow` |
| 自省 | `set_system_prompt` `get_system_prompt` `list_tools` |

`GET /api/tools` 拿完整 JSON Schema。

---

## 接口一览

除 `/health`、`/`、`/ui`、`/login`、`/favicon.ico` 和 `/api/auth/*` 里标了公开的几条外，都要带凭证：网页用登录 Cookie，脚本用 `X-API-Token: ak_…`（账号密钥）或 `X-API-Token: 你的API_TOKEN`（全局钥匙），`Authorization: Bearer ak_…` 也认。

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/`、`/ui` | 网页聊天页（免鉴权） |
| GET | `/health` | 健康检查 |
| GET | `/api/config` | 当前配置（不含密钥） |
| GET | `/api/tools` | 已注册工具及 JSON Schema |
| GET | `/api/providers` | 提供商列表 + 当前模型 |
| GET | `/api/providers/catalog` | 预置模板目录 |
| POST | `/api/providers`、`/api/providers/add` | 自定义添加 / 从模板添加 |
| GET / DELETE | `/api/providers/{pid}` | 单个提供商详情 / 删除 |
| POST | `/api/providers/{pid}/fetch-models` | 调服务商 `GET /models` 写回真实列表 |
| POST | `/api/providers/{pid}/models`、`/models/{model}` | 增删单个模型条目 |
| POST | `/api/providers/{pid}/activate` | 切到该提供商的某个模型（立即重建客户端） |
| POST | `/api/providers/{pid}/toggle` | 启用/停用该提供商 |
| POST | `/api/providers/{pid}/test` | 用当前配置试连一次 |
| POST | `/api/providers/{pid}/vision` | 该提供商是否支持图片输入 |
| POST | `/api/providers/prune` | 清理「没 Key 又不在用」的空壳 |
| GET / POST | `/api/ui/settings`、`/api/ui/llm`、`/api/ui/prompt` | 设置摘要 / 运行时覆盖 / 默认提示词 |
| GET / POST | `/api/ui/search` | 搜索服务配置（Tavily / 博查 / Serper / DDG / 自定义） |
| POST | `/api/ui/search/test` | 试搜一条（网页和 TUI 共用） |
| GET / POST | `/api/ui/tts` | 语音合成配置（预置 / Key / 模型 / 音色 / 语速 / 格式） |
| POST | `/api/ui/tts/speak` | 合成一段语音，产物落 `workspace/media/` |
| GET / POST | `/api/schedules` | 定时任务列表 / 新建 |
| GET / DELETE | `/api/schedules/{id}` | 单个任务详情 / 删除 |
| POST | `/api/schedules/{id}` | 改名字、cron、内容，或 `{"enabled": false}` 暂停 |
| POST | `/api/schedules/{id}/run` | 立刻跑一次（`{"wait": true}` 阻塞到跑完） |
| GET | `/api/sessions/{id}/export` | 导出该会话为 Markdown |
| GET | `/api/auth/status` | 是否已有账号 / 是否允许注册 / Google 是否可用（公开） |
| POST | `/api/auth/register` | 注册（第一个账号是 owner，返回一次性恢复码） |
| POST | `/api/auth/login` | 邮箱密码登录，下发 HttpOnly Cookie |
| POST | `/api/auth/key-login` | 用 `ak_…` 密钥登录（换 Cookie） |
| POST | `/api/auth/logout` | 退出当前设备 |
| POST | `/api/auth/logout-all` | 退出所有设备 |
| GET | `/api/auth/me` | 当前账号 / 身份来源（cookie / key / token） |
| POST | `/api/auth/password` | 改密码（顺带把其它设备下线，返回新恢复码） |
| POST | `/api/auth/forgot` | 查有哪些重置方式（Google / 恢复码，公开） |
| POST | `/api/auth/reset` | 用恢复码重置 |
| POST | `/api/auth/google/reset` | 用 Google 身份认证发的一次性票重置 |
| GET | `/api/auth/google/start` | 跳 Google 授权（`purpose=login\|reset`） |
| GET | `/api/auth/google/callback` | Google 回调（成功下发 Cookie / 重置票） |
| GET / POST | `/api/auth/keys` | 列出 / 生成 API 密钥 |
| DELETE | `/api/auth/keys/{id}` | 吊销某个密钥 |
| GET | `/api/auth/devices` | 登录中的设备 |
| GET / POST | `/api/auth/google/config` | Google 客户端配置（owner） |
| POST | `/api/auth/totp/setup` | 生成 TOTP 密钥 + 二维码 + otpauth 链接 |
| POST | `/api/auth/totp/enable` | 输 6 位码确认启用，返回 10 个备用码 |
| POST | `/api/auth/totp/disable` | 关闭两步验证（6 位码或密码确认） |
| POST | `/api/auth/totp/backup` | 重新生成备用码 |
| POST | `/api/auth/login/totp` | 密码通过后的第二步：交 6 位码换 Cookie |
| POST | `/api/auth/reset/totp` | 用 6 位码 / 备用码重置密码 |
| GET / POST | `/api/ui/media`、`/api/ui/media/{kind}` | 图片/视频生成配置（`kind=image\|video`） |
| POST | `/api/ui/media/{kind}/generate` | 手动试生成，产物落 `workspace/media/` |
| POST | `/api/ui/upload` | 附件上传（base64 data_url，≤8MB） |
| GET | `/api/ui/preview?path=` | 沙箱内文本/Markdown 预览（2MB 上限、防越界） |
| POST | `/api/backup` | 打包导出 data 目录 |
| GET | `/files/media/{name}`、`/files/uploads/{name}` | 产物与附件（文件名白名单 + 防穿越） |
| POST | `/api/chat` | 对话，返回最终答复 + 每步工具调用 |
| POST | `/api/chat/stream` | 同上，SSE 逐事件 |
| GET / POST | `/api/sessions` | 会话列表 / 新建 |
| GET / DELETE | `/api/sessions/{id}` | 会话详情 / 删除 |
| GET | `/api/sessions/{id}/messages` | 历史消息 |
| POST | `/api/sessions/{id}/system-prompt` | 替换或追加系统提示词 |
| POST | `/api/sessions/{id}/skills/{name}` | 把某个 Skill 载入该会话 |
| GET / POST | `/api/files` | 读 / 写文件 |
| POST | `/api/files/edit` | 精确替换 |
| GET | `/api/dir` | 列目录（`recursive=1`） |
| GET | `/api/search` | 正则搜索（`pattern=…&glob=*.py`） |
| POST | `/api/execute/python` | 直接跑 Python |
| GET / POST | `/api/cookies` | 查询 / 写入 |
| GET / DELETE | `/api/cookies/{domain}` | 读 / 删某域 |
| GET | `/api/cookies/{domain}/header` | 拼成 `name=value; …` |
| GET | `/api/skills`、`/api/skills/{name}` | Skill 列表 / 读取 |
| GET | `/api/workflows` | 工作流列表 |
| POST | `/api/workflows/{name}/run` | 执行工作流 |
| GET / POST | `/api/subagents` | 子 Agent 列表 / 派发 |
| GET | `/api/subagents/{id}` | 状态与结果 |
| POST | `/api/subagents/{id}/wait` | 阻塞等待（`{"timeout": 300}`） |
| POST | `/api/subagents/{id}/cancel` | 取消 |

### curl 示例

```bash
# 新建会话聊一句
curl -s http://127.0.0.1:8000/api/chat \
  -H 'Content-Type: application/json' -H 'X-API-Token: your-token' \
  -d '{"message":"在 workspace 里建个 hello.py 打印 1+1，并跑一遍","title":"demo"}'

# 流式
curl -N http://127.0.0.1:8000/api/chat/stream \
  -H 'Content-Type: application/json' -H 'X-API-Token: your-token' \
  -d '{"message":"统计 workspace 下所有 .py 的行数"}'

# 直接写文件 / 跑代码
curl -s -X POST http://127.0.0.1:8000/api/files \
  -H 'Content-Type: application/json' -H 'X-API-Token: your-token' \
  -d '{"path":"notes/todo.md","content":"# TODO\n- 写测试\n"}'

curl -s -X POST http://127.0.0.1:8000/api/execute/python \
  -H 'Content-Type: application/json' -H 'X-API-Token: your-token' \
  -d '{"code":"import sys; print(sys.version)"}'
```

SSE 事件：`user` / `reasoning_delta` / `content_delta` / `assistant` / `tool_call` / `tool_result` / `warning` / `error` / `end` / `final`。

---

## Skill 格式

`skills/<名字>/SKILL.md` 放一个文件就会被发现：

```markdown
---
name: data-report
description: 把数据文件整理成 Markdown 报告，先校验再下结论
---

# 步骤
1. list_dir / read_file 确认输入
2. run_python 统计行数、空值、异常
3. write_file 输出报告到 outputs/
```

`load_skill` 把全文追加进当前会话的系统提示词，之后每轮生效。唯一原则：SKILL.md 里写的步骤得能被现有工具执行。

---

## 工作流（JSON）

```json
{
  "name": "demo_pipeline",
  "inputs": {"path": {"default": "outputs/demo.txt"}},
  "steps": [
    {"id": "seed", "tool": "write_file", "args": {"path": "{{ inputs.path }}", "content": "a\nb\n"}},
    {"id": "stats", "python": "print(len(open('{{ inputs.path }}').read().splitlines()))"},
    {"id": "summary", "agent": "根据 {{ steps.stats.output }} 用两句话总结，不要编造数字"}
  ]
}
```

- `tool` 直接调工具，`args` 支持插值
- `python` 等价于 `run_python`
- `agent` 起一个内联 Agent 步骤（不占子 Agent 名额，默认最多 8 轮）
- 插值变量：`{{ inputs.xxx }}`、`{{ steps.<id>.output }}`
- 某步返回 `ERROR:` 默认中断，可设 `"stop_on_error": false`

---

## 子 Agent 机制

- `spawn_agent(task=…, title=…, system_prompt=…)` 立即返回 `sub_id`，子 Agent 在独立会话（`kind=subagent`）后台跑，不阻塞父对话
- 并发上限 `AGENT_MAX_SUBAGENTS`（默认 3），第 4 个直接 `409`，不会悄悄排队
- `wait_agent(sub_id, timeout=300)` 阻塞取结果；超时返回 `running`，不破坏子任务
- 深度默认 1 层，子 Agent 不能再派子 Agent
- 完整轨迹：`GET /api/subagents/{id}?include_trace=1`

---

## Cookie 的三种来源

1. **手工/程序写入**：`POST /api/cookies` 或 `set_cookie`，支持 `a=1; b=2` 这种头字符串
2. **登录态文件**：浏览器导出 `storage_state.json` 放进 workspace，再 `import_cookie_from_storage_state`
3. **直连已登录 Chrome**：Chrome 带 `--remote-debugging-port=9222` 启动，`import_cookie_from_browser`（需要 `playwright`，非必需）

按域名模糊匹配（`.example.com` 与 `example.com` 视为同域），运行中用 `get_cookie_header` 直接取请求头。

---

## 安全边界（请认真看）

- **文件**：所有路径 `resolve()` 后必须位于当前请求的沙箱根（普通账号是 `SANDBOX_ROOT`，游客是 `SANDBOX_ROOT/guests/<gid>`），`../` 和软链接逃逸都拒绝
- **shell / Python**：都是本机代码执行，过 `guard` 闸门但**不是容器隔离**。对外暴露时务必设 `API_TOKEN`，并考虑关掉 `ENABLE_SHELL` / `ENABLE_PYTHON` 或上容器
- **鉴权**：`API_TOKEN` 为空 = 不鉴权，这是为了方便本机调试，不是默认安全配置
- **请求体**：超过 20MB 直接 413

---

## 目录结构

```
agent-api/
├─ run.py                     # 启动入口
├─ .env.example               # 配置模板
├─ agent_api/
│  ├─ server.py               # ThreadingHTTPServer + 路由 + SSE
│  ├─ api.py / ui_api.py      # 业务接口 / 网页专用接口
│  ├─ webui.py                # 单页网页（/、/ui）
│  ├─ agent.py                # 模型→工具循环 + 多模态拼装 + 看图标记
│  ├─ llm.py                  # urllib 调 OpenAI 兼容接口 + SSE + Mock + 限速
│  ├─ providers.py            # 提供商目录 / 拉模型 / 空壳清理 / 视觉判定
│  ├─ guard.py                # 高危命令闸门（静态规则 + 破坏面 + 受保护目录）
│  ├─ plugins.py              # 插件加载 / 校验 / 热重载
│  ├─ presets.py              # LLM / 图片 / 视频 / 搜索模板
│  ├─ media.py                # 图片与视频生成（三种轮询形状）
│  ├─ tts.py                  # 语音合成（/audio/speech，二进制直收）
│  ├─ schedules.py            # 定时任务：cron 解析 + 调度线程 + 执行
│  ├─ auth.py                 # 账号：scrypt 密码、Cookie 会话、API 密钥、TOTP、Google OAuth
│  ├─ qr.py                   # 纯 Python 二维码（给身份验证器扫码用）
│  ├─ auth_api.py             # /api/auth/* 路由
│  ├─ search.py               # 五种搜索实现
│  ├─ net.py                  # urllib 封装 + 真实错误回传
│  ├─ sessions.py             # 会话落盘 + 自动标题 + 子 Agent 调度
│  ├─ skills.py / workflows.py / cookies.py / security.py
│  ├─ tools/                  # 12 个工具模块（fs/py/web/media/video/vision/plugin/skill/…）
│  ├─ ui/                     # page.html + login.html + app.css + 各功能 js（含 account.js）
│  └─ prompts/default_system.md
├─ plugins/                   # 你写的插件放这
├─ skills/data-report/SKILL.md
├─ workflows/demo_pipeline.json
├─ workspace/                 # 默认沙箱（Agent 的文件都在这）
├─ data/                      # 会话 / Cookie / 插件状态 / 临时脚本
└─ scripts/
   ├─ tui.py                  # 终端界面（流式 + /see /sessions /skills /model /md …）
   ├─ repl.py                 # 轻量 REPL
   ├─ chat_cli.py             # 一次性问答
   └─ smoke_test.py           # 端到端自检
```

---

## 自检

```bash
python3 scripts/smoke_test.py
```

临时沙箱里跑一遍：账号体系（注册 / 登录 / 密钥 / 恢复码重置 / 身份验证器 / 锁定）、鉴权、文件读写、路径逃逸、Python 执行与超时、Cookie、Skill 扫描、Mock 对话、运行时改提示词、载入 Skill、3 子 Agent 并发 + 第 4 个拒绝、工作流、SSE 与思考流、token 用量与缓存命中、PDF/Word 抽字与页面渲染、提供商目录/拉模型/切换/清理、搜索与图片视频配置、上传、Markdown 预览越界、游客（创建 / 隔离目录 / 100MB 配额 / 一分钟一条 / 改设置 403 / 解锁码 / 到期清空）、商家版（套餐 / 兑换码 / 402 / 429 / 扣额度 / 会员权限）、全部前端静态资源。

它自己用随机端口，**正在 8000 跑着服务也能直接跑**（以前会把 `port=0` 当成 8000，直接撞出 `Errno 98 Address already in use`）。

---

## 扩展

- **加工具（推荐）**：写个插件扔 `plugins/`，不用改仓库代码——见上面「插件系统」
- **加工具（内置）**：`agent_api/tools/` 里写 `register(registry)`，在 `tools/__init__.py` 的 `build_registry` 挂上
- **加模型形状**：`llm.py` 里加一个 `BaseLLM.chat(messages, tools)`，返回 OpenAI 格式 message 即可
- **换端口/域名**：改 `.env`，无需动代码
</content>
