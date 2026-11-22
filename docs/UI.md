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

## 顶栏

`☰` 抽屉 · 标题（会话标题 + 状态小字）· `子任务 n/3` 徽标（有跑动才出现）· `设置` · `新会话`。

状态小字：`在线 · 已连真实模型` / `Mock 模式（未配 Key）` / `离线`。

## 抽屉

- **会话列表**：标题、`session id`、条数、最后时间；点一条切换并回显历史，`✕` 删除（二次确认）。
- **导出当前会话（Markdown）**：抓整段 messages（含工具输出）下载 `.md`。
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
```

## 已知取舍

- 页面刷新不丢会话（`session id` 存 `localStorage`），服务重启后 `data/sessions/*.json` 还在；
- 浏览器只存 `API_TOKEN`、系统提示词、`session id`，不存模型的 Key；
- 改 `HOST` / `PORT` / `API_TOKEN` / Python 与 Shell 开关仍要改 `.env` 再重启（页面里只读展示）。
