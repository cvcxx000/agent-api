# Web 界面使用说明

页面地址：`http://127.0.0.1:8000/ui`（根路径 `/` 也是同一个页面）。单文件 HTML，不依赖 CDN，离线可用。

## 顶栏

| 元素 | 作用 |
| --- | --- |
| ☰ | 打开抽屉：会话列表、功能入口、模型切换、对话设置、导出 |
| 状态小字 | `在线 · 已连真实模型` / `Mock 模式（未配 Key）` / `离线` |
| `子任务 n/3` 徽标 | 有子任务在跑时才出现，点一下直接进子任务面板 |
| 新会话 | 清空当前 session id，下一句新建会话 |

## 抽屉

- **会话列表**：读 `/api/sessions`，显示标题、`session id`、消息条数、最后更新时间。点一条切换并回显历史；右边 `✕` 删除（二次确认，真删 `data/sessions/*.json`）。
- **功能**：技能 / 工作流 / 子任务 / 文件，各自打开一个全屏面板。
- **模型**：预设下拉 + `BASE URL` + 模型 ID + `API Key` + 每分钟请求上限 + 强制 Mock 勾选，`应用模型` / `测一句`。
- **对话设置**：`API_TOKEN`、系统提示词（新会话生效）、单轮最大工具轮数。
- **导出当前会话（Markdown）**：抓整段 messages（含工具输出）拼成 `.md` 下载。

## 面板

### 技能（`/api/skills`）

列出每个 `SKILL.md` 的 `name` / `description` / 目录。`载入到当前会话` 调 `POST /api/sessions/{id}/skills/{name}`，之后每一轮都带着它；`看全文` 直接读内容。
没有会话时会先自动建一个（标题 `面板操作`）。

### 工作流（`/api/workflows`）

列出 `workflows/*.json`，按 `inputs` 自动生成输入框（带默认值），`运行` 调 `POST /api/workflows/{name}/run`，结果以 JSON 展开。
如果当前有会话，会把 `session_id` 一起带上。

### 子任务（`/api/subagents`）

并发数 / 上限 / 最大深度显示在顶部。每张卡显示标题、状态（`running` / `done` / `failed` / `cancelled`）、深度、创建与结束时间、任务描述。
- `看结果`：取 `result` 与 `error`；
- `取消`：`POST /api/subagents/{id}/cancel`；
- `新建子任务`：直接 `POST /api/subagents` 派活。
面板打开时每 2 秒自动刷新；页面其它地方每 8 秒刷一次徽标计数。

### 文件（`/api/dir`、`/api/files`）

按相对 `workspace` 的路径列目录，可改路径重新打开；下方填文件路径点 `预览` 读内容（`limit` 默认 300 行）。

## 模型切换

`应用模型` 调 `POST /api/ui/llm`：

```json
{"base_url":"https://token.sensenova.cn/v1","model":"SenseChat-5","api_key":"sk-...","rate_limit":"7","mock":false}
```

- 只改内存里的 `settings` 并重建 `llm`，**不写 `.env`**，重启后回到 `.env` 的配置；
- `api_key` 留空表示不改动现有 Key；
- 勾选 `强制 Mock` 时 `mock=true`，不调真模型；
- `rate_limit` 写进进程环境变量，立即对新的客户端生效（`0` = 不限速）。

`测一句` 走一次 `POST /api/chat`（`"只回复两个字：收到"`），会消耗一次上游请求额度 —— 7 次/分钟的情况下别无脑连点。

## 已知取舍

- 模型 Key 只在内存里，浏览器也不存 Key（只存 `API_TOKEN` 和系统提示词）；
- 页面刷新不会丢当前会话（`session id` 存在 `localStorage`），但服务端重启后 `data/sessions` 里的会话仍然在；
- 目录/文件面板按文本展示工具输出，不解析成可点击的树 —— 这是为了保持零依赖、零构建。
