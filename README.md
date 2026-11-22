# Agent API（零第三方依赖版）

一个可以直接跑的「工具型 Agent」API 服务：给一个 OpenAI 兼容的模型 API，它就能读写文件、执行 Python、管理 Cookie、加载 Skill、跑工作流，并在运行过程中自己开新会话、派发子任务（最多 3 个并发）。

**只用 Python 标准库**：`http.server` + `urllib`，不需要 `pip install` 任何东西。手机（Termux / Pydroid 等）上也不会再遇到装包报错。

## 需求对照

| 你的需求 | 实现 |
| --- | --- |
| 提供 API | `POST /api/chat`（一次性）和 `POST /api/chat/stream`（SSE 流式），另有一组直调 REST 接口 |
| 读取 / 写入文件 | `read_file` / `write_file` / `edit_file` / `list_dir` / `grep_files` / `move_path` / `delete_path`，全部限制在沙箱目录 |
| 运行 Python 代码 | `run_python`，独立子进程 + 超时强杀 + stdout/stderr/exit code 回传 |
| 获取 Cookie | `get_cookie` / `set_cookie` / `get_cookie_header` / `import_cookie_from_storage_state`（可选 CDP 直读） |
| 允许载入 Skill | 扫描 `SKILL.md`，`list_skills` / `read_skill` / `load_skill`（载入后写进系统提示词长期生效） |
| 工作流 | `workflows/*.json`，支持 tool / python / agent 三种步骤和 `{{ }}` 变量插值 |
| 允许设置系统提示词 | 全局默认 + 每会话覆盖 + 运行中 `set_system_prompt` 工具/接口 |
| 运行中新开会话、分派子任务 | `spawn_agent` 创建独立子会话并后台执行，父 Agent 用 `wait_agent` 收结果 |
| 一次只能分配三个 | `AGENT_MAX_SUBAGENTS=3`，第 4 个直接 409，不会悄悄排队 |
| 多家提供商 / 真实模型 | 网页「提供商」→ 从目录添加 → 填 Key → **拉取模型**（`GET {base}/models`），只列真实存在的模型；没 Key 的空壳启动时自动清理 |
| 思考结果 | DeepSeek `reasoning_content`、`reasoning`、`thinking` 都能识别，SSE 里单独发 `reasoning_delta`，网页折叠成「思考过程」 |
| 生成图片 | `generate_image` 工具 + 设置页「图片生成」，OpenAI 形状 `POST /images/generations`，产物落 `workspace/media/` |
| 生成视频 | `generate_video` 工具 + 设置页「视频生成」，支持 OpenAI `/videos`、硅基流动 `/video/submit`、通用异步三种轮询 |
| 搜索配置 | 设置页「搜索服务」：Tavily / 博查 / Serper / DuckDuckGo（免 Key）/ 自定义 JSON 网关；Agent 用 `web_search` + `fetch_url` |
| 画板 | 网页里手绘草图 → 自动作为图片附件随下一句话发给模型（需在该提供商页打开 Vision） |
| 终端版界面 | `python3 scripts/tui.py`：流式思考/正文/工具卡、`/model` 选模型、`/image`、`/video`、`/search`、`/md` 渲染 |
| Markdown「点得动」 | 链接可点、`![](…)` 出图、`/files/*.mp4` 出视频、代码块一键复制、文件面板可直接渲染 MD |

## 快速开始

```bash
cd agent-api
cp .env.example .env        # 填 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
python3 run.py
```

启动后有两种用法都能用：

- **网页**：手机/电脑浏览器打开 `http://127.0.0.1:8000/ui`（或根路径 `/`），直接聊，工具调用实时显示。
- **终端**：另一个窗口跑 `python3 scripts/tui.py`（流式 TUI）或 `python3 scripts/repl.py`（轻量 REPL）。

打开 `http://127.0.0.1:8000/health` 看到 `{"ok": true}` 就说明起来了。

没有 Key 也能先跑：`LLM_MOCK=auto`（默认）时会用内置 MockLLM，真实走一次 `run_python`，用来验证工具链路。

手机（Termux）示例：

```bash
pkg install python
cd agent-api
python run.py
```

然后在手机浏览器里开 `http://127.0.0.1:8000/ui` 即可。

## 三种用法

### 1）网页 UI（`/ui`）

单文件 HTML，无 CDN、无构建步骤，视觉是 iOS inset grouped 为主，叠华为 HarmonyOS 的大圆角/渐变图标与 vivo 的留白节奏，自带深色模式：

- **提供商**：先「从目录添加」（11 家 LLM 模板，只给地址和说明，不预置假模型），填 Key 后点「拉取模型」调服务商 `GET /models`，拉回来的是真实列表；模型选择器里点一行立即切换；
- 顶部模型胶囊显示当前提供商 + 模型，没配就写「未配置模型」，不会假装有；
- 思考过程单独折叠成一张卡，正文流式逐字；工具调用是折叠卡，参数和输出都在；
- 输入区支持粘贴/选择图片（作为附件），画板 `✎` 手绘后也能直接附上；
- 图片生成、视频生成、搜索结果都会在对话里直接出图 / 出片 / 出列表；
- Markdown 里的链接可点、图片直接显示、代码块带「复制」；
- 「设置」分成默认模型、提供商、搜索服务、图片生成、视频生成、Web 服务器、数据备份、引擎与限流；
- 「终端版」入口会告诉你 `python3 scripts/tui.py` 怎么连。

### 2）终端界面（`scripts/tui.py`）

```bash
python3 scripts/tui.py                                   # 默认 http://127.0.0.1:8000
python3 scripts/tui.py --base http://127.0.0.1:8000 --token 你的TOKEN
```

命令：`/model`（列真实模型挑编号，立即切换）、`/providers`、`/search 关键词`、`/image 提示词`、`/video 提示词`、`/md 路径`（把 Markdown 渲染成终端样式）、`/file 路径`、`/new`、`/session`、`/quit`。其余输入直接发给 Agent，思考（暗色）、正文（绿色）、工具卡（品红）都是流式的。

### 3）终端 REPL（`scripts/repl.py`）

```bash
python3 scripts/repl.py                              # 默认连 127.0.0.1:8000
python3 scripts/repl.py --base http://1.2.3.4:8000 --token abc
```

内置命令：`/new` 开新会话、`/session` 看当前 id、`/system <文本>` 改系统提示词、`/system-` 清空、`/help`、`/exit`。工具调用与输出直接打在屏幕上。

### 4）curl / 代码调用

见下方「接口一览」与 curl 示例，适合接到别的程序里。

## 配置（.env）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | 监听地址。只本机用可设 `127.0.0.1` |
| `API_TOKEN` | 空 | 留空不鉴权；对外暴露时强烈建议设置，请求头 `X-API-Token` |
| `LLM_BASE_URL` | `https://api.openai.com/v1` | 任意 OpenAI 兼容接口（DeepSeek/通义/日日新/vLLM/Ollama…） |
| `LLM_API_KEY` | 空 | 模型 Key |
| `LLM_MODEL` | `gpt-4o-mini` | 模型名 |
| `LLM_TEMPERATURE` / `LLM_MAX_TOKENS` / `LLM_TIMEOUT` | `0.3` / `4096` / `120` | 采样与超时 |
| `LLM_MOCK` | `auto` | `auto`=没 Key 就用 Mock；`true` 强制 Mock；`false` 强制真实 |
| `RATE_LIMIT_PER_MIN` | `7` | **上游限速**：每分钟最多发多少次模型请求，主/子 Agent 共用同一配额；`0` = 不限速 |
| `AGENT_MAX_TURNS` | `16` | 单轮对话里最多几次「模型→工具」循环 |
| `AGENT_HISTORY_LIMIT` | `80` | 发给模型的历史消息上限（自动从 user 消息处截断） |
| `AGENT_MAX_SUBAGENTS` | `3` | **子 Agent 并发上限** |
| `AGENT_MAX_SUBAGENT_DEPTH` | `1` | 1 = 子 Agent 不能再派子 Agent |
| `ENABLE_PYTHON` / `ENABLE_SHELL` | `true` / `false` | 是否允许执行代码 / shell |
| `PYTHON_TIMEOUT` | `60` | 单次执行的默认超时（秒，上限 600） |
| `SANDBOX_ROOT` | `./workspace` | 文件与代码执行的沙箱根 |
| `DATA_DIR` | `./data` | 会话、Cookie、临时脚本落盘位置 |
| `SKILLS_DIRS` | `./skills,./workspace/.skills` | Skill 搜索目录（逗号分隔） |
| `WORKFLOWS_DIR` | `./workflows` | 工作流目录 |
| `SYSTEM_PROMPT` / `SYSTEM_PROMPT_FILE` | 空 | 直接覆盖默认系统提示词 / 从文件读取 |

### 限速说明（测试 Key 只有 7 次/分钟）

`RATE_LIMIT_PER_MIN=7` 时，所有请求排队匀速发出（约每 8.6 秒一次），并在遇到 `429` 时读 `Retry-After` 头自动退避（最少 1 秒、最多 60 秒），不会把你的 Key 打到被封。

注意：Agent 每回一次话可能花掉多次请求（调工具 → 再调模型），所以 7 次额度下“一句话”大约能跑 3 到 4 轮工具循环。想快点就把这个值调大，或换成正常额度的 Key 设 `0`。

### 日日新（商榘 SenseNova）填法

商榘有两套 OpenAI 兼容入口，路径只填到 `/v1`（程序会自己拼 `/chat/completions`）：

| 平台 | `LLM_BASE_URL` | 说明 |
| --- | --- | --- |
| Token Plan（新） | `https://token.sensenova.cn/v1` | 控制台里的模型 ID 有 `sensenova-u1.5-lite` 等，聊天选对话模型 |
| 大装置 ModelStudio | `https://api.sensenova.cn/compatible-mode/v1` | 常见对话模型 ID：`SenseChat-5`、`SenseChat-5-1202` |

验证一行：

```bash
curl -s https://token.sensenova.cn/v1/chat/completions \
  -H "Authorization: Bearer 你的Key" -H 'Content-Type: application/json' \
  -d '{"model":"你的模型ID","messages":[{"role":"user","content":"说一句你好"}]}'
```

返回里有 `choices` 就是通了；报 `401` 是 Key 错，`404` 是 `LLM_MODEL` 名字不对。

## 接口一览

除 `/health`、`/`、`/ui`、`/favicon.ico` 外都要带 `X-API-Token`（如果设置了 `API_TOKEN`）。

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/` 、`/ui` | 网页聊天页（免鉴权） |
| GET | `/health` | 健康检查 |
| GET | `/api/config` | 当前配置（不含密钥） |
| GET | `/api/tools` | 已注册工具及其 JSON Schema |
| GET | `/api/providers` | 提供商列表 + 当前模型（只列真的存在的条目和模型） |
| GET | `/api/providers/catalog` | 预置模板目录（11 家 LLM / 图片 / 视频 / 搜索） |
| POST | `/api/providers` / `/api/providers/add` | 自定义添加 / 从模板添加（不自动造模型） |
| POST | `/api/providers/{pid}/fetch-models` | 调服务商 `GET /models`，写回真实模型列表 |
| POST | `/api/providers/{pid}/activate` | 切到该提供商的某个模型（立即重建客户端） |
| POST | `/api/providers/{pid}/vision` | 该提供商是否支持图片输入（多模态附件） |
| POST | `/api/providers/prune` | 清理「没 Key 又不是当前在用」的空壳 |
| GET / POST | `/api/ui/settings`、`/api/ui/llm`、`/api/ui/prompt` | 设置摘要 / 运行时覆盖 / 默认提示词 |
| GET / POST | `/api/ui/search` | 搜索服务配置（Tavily / 博查 / Serper / DDG / 自定义） |
| POST | `/api/ui/search/test` | 直接试搜一条（网页和 TUI 都用它） |
| GET / POST | `/api/ui/media`、`/api/ui/media/{kind}` | 图片/视频生成配置（`kind=image|video`） |
| POST | `/api/ui/media/{kind}/generate` | 手动试生成，产物落 `workspace/media/` |
| POST | `/api/ui/upload` | 图片附件上传（base64 data_url，≤8MB） |
| GET | `/api/ui/preview?path=` | 沙箱内文本/Markdown 预览（2MB 上限、防越界） |
| GET | `/files/media/{name}`、`/files/uploads/{name}` | 生成的图片/视频与上传附件（文件名白名单 + 防穿越） |
| POST | `/api/chat` | 对话，返回最终答复 + 每一步工具调用 |
| POST | `/api/chat/stream` | 同上，SSE 逐事件推送 |
| GET / POST | `/api/sessions` | 会话列表 / 新建 |
| GET / DELETE | `/api/sessions/{id}` | 会话详情 / 删除 |
| GET | `/api/sessions/{id}/messages` | 历史消息 |
| POST | `/api/sessions/{id}/system-prompt` | 替换或追加系统提示词 |
| POST | `/api/sessions/{id}/skills/{name}` | 把某个 Skill 载入该会话 |
| GET / POST | `/api/files` | 读文件 / 写文件 |
| POST | `/api/files/edit` | 精确替换 |
| GET | `/api/dir` | 列目录（`recursive=1`） |
| GET | `/api/search` | 正则搜索（`pattern=…&glob=*.py`） |
| POST | `/api/execute/python` | 直接跑 Python |
| GET / POST | `/api/cookies` | 查询 / 写入 Cookie |
| DELETE | `/api/cookies/{domain}` | 删除某域 Cookie |
| GET | `/api/cookies/{domain}/header` | 拼成 `name=value; …` |
| GET | `/api/skills` / `/api/skills/{name}` | Skill 列表 / 读取 |
| GET | `/api/workflows` | 工作流列表 |
| POST | `/api/workflows/{name}/run` | 执行工作流 |
| GET / POST | `/api/subagents` | 子 Agent 列表 / 派发 |
| GET | `/api/subagents/{id}` | 子 Agent 状态与结果 |
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

SSE 事件类型：`user` / `reasoning_delta`（思考逐字） / `content_delta`（正文逐字） / `assistant` / `tool_call` / `tool_result` / `warning` / `error` / `end` / `final`。

## 内置工具

| 工具 | 说明 |
| --- | --- |
| `read_file` `write_file` `edit_file` `list_dir` | 基础文件操作（路径强制在沙箱内） |
| `make_dir` `move_path` `delete_path` | 目录/移动/删除（目录删除需 `recursive=true`） |
| `grep_files` | 正则搜索，跳过二进制与依赖目录 |
| `run_python` | 子进程执行，超时 `kill -9` 整个进程组 |
| `run_shell` | 默认关闭，`ENABLE_SHELL=true` 打开 |
| `get_cookie` `set_cookie` `delete_cookie` `get_cookie_header` | Cookie 仓库读写 |
| `import_cookie_from_storage_state` | 从 Playwright `storage_state.json` 导入 |
| `import_cookie_from_browser` | 通过 CDP 连已登录 Chrome（需自己装 playwright） |
| `list_skills` `read_skill` `load_skill` | Skill 发现与载入 |
| `list_workflows` `run_workflow` | 工作流 |
| `spawn_agent` `wait_agent` `list_agents` `cancel_agent` | 子任务编排 |
| `set_system_prompt` `get_system_prompt` `list_tools` | 运行时自省与自我约束 |
| `web_search` | 走设置页配的搜索服务，返回 title/url/snippet |
| `fetch_url` | 打开一个网页，去脚本/样式后转纯文本 |
| `generate_image` | 按提示词出图，落 `workspace/media/`，输出 `![…](/files/media/…)` |
| `generate_video` | 提交异步视频任务并轮询，落 `workspace/media/`，输出 `MEDIA_VIDEO:` 标记 |

## 子 Agent 机制

- `spawn_agent(task=…, title=…, system_prompt=…)` 立即返回 `sub_id`，子 Agent 在**独立会话**（`kind=subagent`）里后台跑，不阻塞父对话。
- 并发计数由 `AGENT_MAX_SUBAGENTS` 控制，默认 3。第 4 个请求直接返回 `409 ConflictError`，提示先 `wait_agent` 或 `cancel_agent`。
- `wait_agent(sub_id, timeout=300)` 阻塞取结果；超时返回 `status=running`，不会破坏子任务。
- 子 Agent 深度默认 1 层：它自己不能再派子 Agent，避免任务无限分裂。
- 子 Agent 的完整轨迹可通过 `GET /api/subagents/{id}?include_trace=1` 查看。

## Skill 格式

在 `skills/<名字>/SKILL.md` 放一个文件即可被发现：

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

- `load_skill` 会把全文追加进当前会话的系统提示词，之后每一轮都生效。
- 只有一个原则：SKILL.md 里写的步骤要能被现有工具执行。

## 工作流格式（JSON）

`workflows/demo_pipeline.json`：

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

- `tool`：直接调某个工具，`args` 支持插值。
- `python`：等价于 `run_python`。
- `agent`：起一个内联 Agent 步骤（不占子 Agent 并发名额，默认最多 8 轮）。
- 插值变量：`{{ inputs.xxx }}`、`{{ steps.<id>.output }}`。
- 某步返回 `ERROR:` 时默认中断（可设 `"stop_on_error": false`）。

## Cookie 的三种来源

1. **手工/程序写入**：`POST /api/cookies` 或 `set_cookie`，支持 `a=1; b=2` 这种 Cookie 头字符串。
2. **浏览器登录态文件**：先用浏览器工具导出 `storage_state.json` 放进 workspace，再 `import_cookie_from_storage_state`。
3. **直连已登录 Chrome**：Chrome 以 `--remote-debugging-port=9222` 启动，`import_cookie_from_browser`（需要额外装 `playwright`，非必需）。

存下来的 Cookie 会按域名模糊匹配（`.example.com` 与 `example.com` 视为同域），Agent 运行中用 `get_cookie_header` 直接拿到请求头。

## 安全边界（请认真看）

- **文件**：所有路径都做 `resolve()` 后校验必须位于 `SANDBOX_ROOT`，`../` 和软链接逃逸都会被拒绝。
- **代码**：`run_python` 本质上就是本机代码执行，服务默认只适合本机/可信网络使用；对外暴露时务必设置 `API_TOKEN`，并考虑关闭 `ENABLE_PYTHON` 或改用容器隔离。
- **鉴权**：`API_TOKEN` 为空 = 不鉴权；这是为了方便本机调试，不是默认安全配置。
- **请求体**：超过 20MB 直接 413。

## 目录结构

```
agent-api/
├─ run.py                     # 启动入口
├─ .env.example               # 配置模板
├─ agent_api/
│  ├─ server.py               # ThreadingHTTPServer + 路由 + SSE
│  ├─ api.py                  # 所有接口实现
│  ├─ webui.py                # 单文件网页聊天页（/ 、/ui）
│  ├─ agent.py                # 模型→工具循环
│  ├─ llm.py                  # urllib 调 OpenAI 兼容接口 + SSE 思考流 + Mock + 限速
│  ├─ providers.py            # 提供商目录 / 真实模型拉取 / 空壳清理 / Vision
│  ├─ presets.py              # 四类预置模板（LLM / 图片 / 视频 / 搜索）
│  ├─ media.py                # 图片与视频生成（三种视频轮询形状）
│  ├─ search.py               # 五种搜索实现
│  ├─ net.py                  # urllib 小封装 + 真实错误回传
│  ├─ sessions.py             # 会话落盘 + 子 Agent 线程调度
│  ├─ skills.py               # SKILL.md 扫描
│  ├─ workflows.py            # JSON 工作流引擎
│  ├─ cookies.py              # Cookie 仓库
│  ├─ security.py             # 沙箱与鉴权
│  ├─ ui/                     # page.html + app.css + core/sessions/canvas/chat/providers/settings.js
│  ├─ tools/                  # 各类工具实现（含 web_tools / media_tools）
│  └─ prompts/default_system.md
├─ skills/data-report/SKILL.md
├─ workflows/demo_pipeline.json
├─ workspace/                 # 默认沙箱（Agent 的文件都在这）
├─ data/                      # 会话 / Cookie / 临时脚本
└─ scripts/
   ├─ repl.py                 # 终端交互式对话（轻量）
   ├─ tui.py                  # 终端界面（思考/正文/工具流式 + /model /image /video /search /md）
   └─ smoke_test.py           # 端到端自检（42 项）
```

## 自检

```bash
python3 scripts/smoke_test.py
```

会在临时沙箱里跑一遍：鉴权、文件读写、路径逃逸拦截、Python 执行与超时、Cookie、Skill 扫描、Mock 对话、运行时改系统提示词、载入 Skill、3 子 Agent 并发 + 第 4 个拒绝、工作流、SSE 流式与思考流、提供商空种/目录/自定义模型/切换/清理、搜索与图片视频配置、上传、Markdown 预览越界、全部前端静态资源。

## 扩展

- **加工具**：在 `agent_api/tools/` 里写一个 `register(registry)`，然后在 `tools/__init__.py` 的 `build_registry` 里挂上。
- **加模型**：`llm.py` 里再实现一个 `BaseLLM.chat(messages, tools)`，返回 OpenAI 格式的 message 即可。
- **换端口/域名**：改 `.env` 即可，无需动代码。
- **手机后台常驻**：Termux 里可以用 `nohup python run.py > api.log 2>&1 &`，或配合 `termux-wake-lock`。
