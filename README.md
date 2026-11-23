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
| 忘了密码怎么办？ | 用绑定的 Google 做身份认证，或用注册时给的恢复码，不依赖发邮件 |
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

1. **Google 身份认证**（推荐）：跳 Google 授权 → 验证你确实是这个 Google 账号的主人 → 回到本站，拿一张 10 分钟有效、只能用一次的重置票 → 设新密码。全程不需要 SMTP，也不用发邮件。
2. **恢复码**：输入注册时那串 → 设新密码。旧恢复码立刻作废，同时给你一串新的。

重置成功会**把所有设备下线**（包括当前浏览器），必须重新登录 —— 这是故意的。

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

### 安全上做了什么

- 密码 scrypt（n=16384, r=8, p=1）+ 随机 salt，比较用常量时间
- 登录态是 32 字节随机 token，服务端只存 sha256；Cookie 是 `HttpOnly; SameSite=Lax`，JS 读不到，跨站 POST 带不上
- 连续错 5 次锁 15 分钟（锁定期正确密码也登不进去）
- 一个账号最多 20 个登录会话，超了踢最旧的；「退出所有设备」一键清
- 设备列表能看到 IP / 客户端 / 登录时间
- 改密码、重置密码都会把其它设备下线
- 所有认证动作写日志（`agent.auth`），服务端日志里能查
- `data/auth.json` 权限 600，里面只有哈希，没有明文密码和明文密钥

**边界说明**：这是「一台设备一个主人」的工具，多个账号共用同一个 workspace 和同一批会话文件（没做多租户隔离）。真要隔离，换 `DATA_DIR` / `SANDBOX_ROOT` 再起一个实例。

## 终端界面（`scripts/tui.py`）

```bash
python3 scripts/tui.py                                   # 默认 http://127.0.0.1:8000
python3 scripts/tui.py --base http://127.0.0.1:8000 --token 你的TOKEN
```

| 命令 | 作用 |
| --- | --- |
| `/model` | 列真实模型挑编号，立即切换 |
| `/whoami` | 看现在用的是哪个账号 / 密钥身份 |
| `/say 文字` | 合成语音（TTS），打印音频地址；Agent 自己念的话终端里显示 `语音：<地址>` |
| `/cron` | 列出定时任务，输编号立刻跑一次；`/cron on|off 编号` 开关，`/cron del 编号` 删除 |
| `/sessions` | 列出所有会话（带 id 和标题），可以挑一个继续 |
| `/session` | 看当前会话 id 与标题 |
| `/new` | 开新会话 |
| `/skills` | 看这个服务端有哪些 Skill |
| `/see 路径` | 直接把一张图发给模型看（走 `view_image` 同一条通路） |
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
| `SANDBOX_ROOT` | `./workspace` | 文件与执行的沙箱根 |
| `DATA_DIR` | `./data` | 会话、Cookie、插件状态、临时脚本落盘位置 |
| `SKILLS_DIRS` | `./skills,./workspace/.skills` | Skill 搜索目录（逗号分隔） |
| `WORKFLOWS_DIR` | `./workflows` | 工作流目录 |
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

自建别名、代理网关、没见过的模型名，一律按「能看图」放行，不再静音。想强制关就在提供商页关掉视觉开关。

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

- **文件**：所有路径 `resolve()` 后必须位于 `SANDBOX_ROOT`，`../` 和软链接逃逸都拒绝
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
│  ├─ auth.py                 # 账号：scrypt 密码、Cookie 会话、API 密钥、Google OAuth
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

临时沙箱里跑一遍：账号体系（注册 / 登录 / 密钥 / 恢复码重置 / 锁定）、鉴权、文件读写、路径逃逸、Python 执行与超时、Cookie、Skill 扫描、Mock 对话、运行时改提示词、载入 Skill、3 子 Agent 并发 + 第 4 个拒绝、工作流、SSE 与思考流、提供商目录/拉模型/切换/清理、搜索与图片视频配置、上传、Markdown 预览越界、全部前端静态资源。

---

## 扩展

- **加工具（推荐）**：写个插件扔 `plugins/`，不用改仓库代码——见上面「插件系统」
- **加工具（内置）**：`agent_api/tools/` 里写 `register(registry)`，在 `tools/__init__.py` 的 `build_registry` 挂上
- **加模型形状**：`llm.py` 里加一个 `BaseLLM.chat(messages, tools)`，返回 OpenAI 格式 message 即可
- **换端口/域名**：改 `.env`，无需动代码
</content>
