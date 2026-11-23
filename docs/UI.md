# Web 界面使用说明

地址：`http://127.0.0.1:8000/ui`（根路径 `/` 是同一个页面）。纯静态资源 + JSON 接口，无 CDN、无构建、离线可用。

## 页面结构

| 资源 | 作用 |
| --- | --- |
| `agent_api/ui/page.html` | 页面骨架（顶栏、抽屉、全屏面板、输入区） |
| `agent_api/ui/app.css` | 全部样式（iOS 分组卡片 + 毛玻璃导航 + 深色模式） |
| `agent_api/ui/core.js` | 工具函数、Markdown 渲染、工具卡、抽屉、面板栈、`apiGet/apiPost/apiDelete` |
| `agent_api/ui/sessions.js` | 会话列表、切换、导出、技能 / 工作流 / 子任务 / 文件面板 |
| `agent_api/ui/chat.js` | SSE 发送与流式渲染、全局事件绑定、初始化 |
| `agent_api/ui/providers.js` | 提供商列表页、详情、模型增删、测试 |
| `agent_api/ui/settings.js` | 设置页与各子页 |
| `agent_api/ui/tts.js` | 设置 → 语音合成（预置 / Key / 模型 / 音色 / 语速 / 格式 + 试朗读） |
| `agent_api/ui/schedules.js` | 定时任务面板（列表 / 新建 / 跑一次 / 开关 / 删除） |
| `agent_api/ui/canvas.js` | 画板与文件面板里的图片预览 |

## 顶栏

`☰` 抽屉 · 标题（会话标题 + 状态小字）· `子任务 n/3` 徽标（有跑动才出现）· `设置` · `新会话`。

状态小字：`在线 · 已连真实模型` / `Mock 模式（未配 Key）` / `离线`。

## 抽屉

- **会话列表**：标题、`session id`、条数、最后时间；点一条切换并回显历史，`✕` 删除（二次确认）。
- **导出当前会话（Markdown）**：走后端 `GET /api/sessions/{id}/export`，整段 messages（含工具输出）下载 `.md`。
- **定时任务**：开面板看全部任务（cron 的人话说法、下次触发时间、上次状态/耗时/次数），可新建、跑一次、暂停、删除。
- **模型与服务**：`◎ 提供商`（副标题显示「N 家 · M 家启用 · 当前模型」）与 `⚙ 设置`。
- **功能**：技能 / 工作流 / 子任务 / 文件，各开一个全屏面板。
- **对话设置**：系统提示词（新建会话时生效）、导出、清空。

## 提供商（`/api/providers`）

每家一张卡：圆形首字母图标、名称、备注、徽标（`当前` / `有 Key` / `无 Key` / `启用` / `停用` / `n 个模型`）、右侧 `⋮` 菜单。

- `⋮`：`启用 / 停用`（`POST /api/providers/{pid}/toggle`）、`测一句`（`POST /api/providers/{pid}/test`）、`删除`（`DELETE /api/providers/{pid}`）。
- 点卡片展开详情：`BASE URL`、`API Key`（显示尾号，不回传明文）、`保存 Key`、`清除 Key`、模型列表（`用这个` 即激活、`✕` 删除）、`添加模型`。
- `用这个` → `POST /api/providers/{pid}/activate`：把该家的 base/model/key 装进内存并重建客户端，**立即生效，不写 `.env`**。该家没 Key 时自动进 `Mock`，不会报错。
- 内置 11 家：商汤日日新、商汤大装置、DeepSeek、硅基流动、月之暗面、OpenRouter、Gemini、AiHubMix、通义千问、OpenAI、本地 Ollama。

Key 存在 `data/providers.json`（`chmod 600`），重启仍在；接口只返回 `has_key` 与 `key_tail`。

## 设置（`/api/ui/settings`）

条目式，点进去是子页，`‹` 返回上一层。

| 条目 | 内容 |
| --- | --- |
| 默认模型和提示词 | 当前模型 + 全局默认系统提示词（存 `data/default_system.md`，可保存 / 恢复内置） |
| 提供商 | 跳到提供商列表 |
| Web 服务器 | 网页地址、接口地址、监听、鉴权状态、`复制网页地址` |
| 数据备份 | `下载备份 zip`（`GET /api/backup`：会话 + `providers.json` + 提示词），以及数据 / 沙箱 / 技能 / 工作流目录 |
| 引擎与限流 | Python / Shell 开关、最大工具轮数、子任务并发、限流（`POST /api/ui/llm` 带 `rate_limit`，立即生效） |
| 语音合成 | 预置（OpenAI TTS / 硅基流动 CosyVoice / 自定义）、Base URL、Key、模型、音色、语速、格式，点「试朗读」当场播 |
| 定时任务 | 任务数 + 进入定时任务面板 |

## 视觉与主题

风格是三种系统设计的混合，不是抄某一家的皮：

| 来源 | 取了什么 |
| --- | --- |
| iOS | inset grouped 卡片、纯白卡 + hairline 分隔、毛玻璃顶栏 / 底部输入区、Continuous 圆角、系统蓝 `#0A84FF`、文字按钮（`设置` / `新会话` 是蓝字，不是灰底块） |
| HarmonyOS | 大圆角卡片（16–22px）、柔和双层投影、提供商 46px 蓝→紫渐变图标、胶囊徽标 |
| OriginOS / vivo | 大留白、明确的字号层级（19/17/15.5/13/11）、克制的灰阶 |

- **深色模式自动跟随系统**（`prefers-color-scheme`），两套颜色变量全在 `app.css` 顶部的 `:root` 里，改配色只动那里；`page.html` 里给了浅 / 深两个 `theme-color`。
- 顶栏状态点：在线为绿色圆点，离线/报错变灰变红。
- 首页空态有欢迎卡（标题 + 一句说明 + 三个快捷 chip，点一下回填输入框），发出第一条消息后消失，点 `新会话` 重新出现。
- 设置 / 提供商都是"白卡 + 左侧彩色圆角图标 + 右侧 `>`"的条目式布局；图标按条目顺序取蓝 / 紫 / 青 / 橙 / 绿。
- 提供商页顶部有 `搜索提供商` 输入框，按名称 / id / 备注 / BASE URL 实时过滤（纯前端，不打接口）。
- 没有用 `color-mix()`、没有用 CDN 字体、没有外链图标：老一些的华为 / vivo 浏览器也能正常显示。

## 接口一览

```
GET    /api/providers
POST   /api/providers                     {id,name,notes,base_url,models}
DELETE /api/providers/{pid}
POST   /api/providers/{pid}/toggle        {enabled:true|false}
POST   /api/providers/{pid}/models        {model:"..."}
DELETE /api/providers/{pid}/models/{model}
POST   /api/providers/{pid}/activate      {model:"可选"}
POST   /api/providers/{pid}/test          {model:"可选",prompt:"可选"}
GET    /api/ui/settings
GET    /api/ui/prompt
POST   /api/ui/prompt                     {text:"..."}  空字符串 = 恢复内置
DELETE /api/ui/prompt
POST   /api/ui/llm                        {base_url,model,api_key,rate_limit,mock}
GET    /api/backup                        返回 zip

GET    /api/ui/tts                        语音合成配置（不回传明文 key）
POST   /api/ui/tts                        保存配置 {preset,base_url,api_key,model,voice,format,speed}
POST   /api/ui/tts/speak                  合成 {text,voice?,speed?,format?} → {url,bytes,...}

GET    /api/schedules                     任务列表（含 human / next_run / last_status）
POST   /api/schedules                     新建 {name,cron,kind,prompt|workflow,inputs?,enabled?}
GET    /api/schedules/{id}                单个任务
POST   /api/schedules/{id}                改名 / 改 cron / 改内容 / {enabled:false}
DELETE /api/schedules/{id}                删除
POST   /api/schedules/{id}/run            立刻跑一次（{wait:true} 阻塞到跑完）

GET    /api/sessions/{id}/export          导出 Markdown（含工具输出）
```

`/files/media/{name}`、`/files/uploads/{name}` 支持 HTTP Range（`206`），浏览器里的音频/视频可以拖进度。

## 已知取舍

- 页面刷新不丢会话（`session id` 存 `localStorage`），服务重启后 `data/sessions/*.json` 还在；
- 浏览器只存 `API_TOKEN`、系统提示词、`session id`，不存模型的 Key；
- 改 `HOST` / `PORT` / `API_TOKEN` / Python 与 Shell 开关仍要改 `.env` 再重启（页面里只读展示）。
- 定时任务按**服务器本地时区**触发；Termux 上就是手机时区。
- 定时任务同时最多跑 2 个（`SCHEDULE_CONCURRENCY`），多的排队；失败原因会写进对应 `[定时]` 会话。

## 登录页（`/login`）

独立页面（`agent_api/ui/login.html` + `ui/login.js`），一个有七种状态的卡片：

- 登录：邮箱 + 密码；配了 Google 会出现「用 Google 登录」
- 注册：第一个账号会提示是 owner；注册成功直接展示**恢复码**（只显示一次，可一键复制）
- 忘记密码：先查邮箱，按返回列出可用方式（Google / 恢复码），不暴露「账号是否存在」以外的信息
- 恢复码重置：邮箱 + 恢复码 + 新密码，成功后给新恢复码
- Google 票重置：从 `?reset_ticket=…` 进来，10 分钟内只能用一次
- 密钥登录：粘 `ak_…` 换 Cookie
- 错误态：Google 回调失败会 302 到 `/login?error=…` 并显示原因

浅色 / 深色跟随 `localStorage.agent_theme`，与主界面一致。

## 账号与安全（设置里）

`settings.js` 新增「⛨ 账号与安全」入口，内容由 `account.js`（`showAccount()`）渲染：

- 当前账号：名字、邮箱、角色、这次是用 Cookie / 密钥 / API Token 进来的
- 改密码：旧密码 + 新密码，成功后返回新恢复码，其它设备立刻下线
- API 密钥：列表（名字、前缀、创建时间、最后使用时间）、生成（只显示一次）、吊销
- 登录设备：IP / 客户端 / 时间，最多显示 10 条
- 退出登录 / 退出所有设备
- Google 配置（仅 owner）：Client ID、Secret、重定向 URI 提示、「允许别人注册」
