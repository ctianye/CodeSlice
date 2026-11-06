# CodeSlice 第一版架构（V1）

本地优先的“读代码 / 学代码”桌面 Agent。用户贡献的截图和文档已确定分层：

- **桌面端**（Electron + React + TypeScript + Monaco）负责项目浏览、代码阅读、对话、授权交互。
- **本机 Python 服务**（FastAPI + LangGraph + LangChain）负责代码分析、检索、Agent 工作流、本地命令执行。
- **模型 API** 通过统一适配层接入，用户自行配置厂商 / Base URL / 模型 / API Key（Key 存 Windows 凭据管理器，不入库）。
- **数据**：SQLite（业务数据 + FTS5 关键词索引 + LangGraph checkpoint），向量索引本地化（numpy 余弦检索，接口可替换 Chroma）。

```
┌────────────── Windows 桌面端 ──────────────┐
│ Electron 主进程 → 启动本机 Python 服务     │
│  (随机端口 + 会话令牌，仅监听 127.0.0.1)   │
│  Renderer: Monaco / 文件树 / 对话 / 授权   │
└───────────────┬────────────────────────────┘
                │ HTTP + SSE（X-Session-Token）
┌───────────────▼──── 本机 Python 服务 ───────┐
│ FastAPI 接口层                              │
│  ├ projects / files / git / index / search  │
│  ├ conversations / chat(SSE) / resume        │
│  ├ notes / note_folders / code_anchors / command_rules      │
│  └ settings(model, embedding) / credentials  │
│ LangGraph 学习工作流 + SQLite checkpointer   │
│  ├ indexing: 过滤 → 语言识别 → Tree-sitter    │
│  ├ retrieval: FTS5 关键词 + 向量混合检索      │
│  └ tools: 只读文件 / 写文件 / git / 命令      │
└─────────────────────────────────────────────┘
```

## 目录结构

```
backend/app/
  main.py               FastAPI 组装 + 令牌中间件 + 生命周期
  core/config.py        运行时配置（数据目录、令牌、端口）
  core/db.py            SQLite 连接、schema、迁移
  core/errors.py        统一错误
  storage/              repositories（projects/conversations/…）
  api/                  路由（projects, files, settings, notes, anchors,
                        rules, jobs, index, search, chat）
  indexing/             过滤、语言识别、切片、Embedding 调用
  retrieval/            关键词 + 向量混合检索
  models/               模型 / Embedding 适配层、凭据
  tools/                受控工具（fs / git / command）
  agent/                LangGraph 图、状态、事件、授权中断
frontend/               Vite + React + TS（renderer）
desktop/                Electron main + preload + 打包配置
tests/                  pytest（后端）
packaging/              PyInstaller spec、下载/构建脚本
docs/                   本文件与 API_CONTRACT.md
```

## 关键决策

1. **单用户本地服务**：只监听 127.0.0.1，启动时生成随机端口与会话令牌；Electron 把两者注入 renderer（`window.codeslice`）。所有 `/api/*` 请求必须带 `X-Session-Token`，健康检查除外。
2. **不做“猜协议”**：用户的模型配置里显式给 `protocol`（`openai-compatible` / `anthropic`），适配层按它选 client。Embedding 单独配置，不含则退化为纯关键词检索。
3. **授权粒度按命令规则**：不提供“信任整个项目”的一键放行。文件写入与命令执行都走授权节点，规则表 `command_rules` 记录项目、命令模式、范围（once/session/always）、是否仍需确认。
4. **代码与对话解耦但可追溯**：`code_anchors` 记录文件路径、commit、符号名、行范围、代码摘要与 hash；代码被改动后锚点状态标为 `modified/moved/deleted`，历史对话保留，用户可手动重新关联。
5. **检查点进 SQLite**：LangGraph `SqliteSaver`，授权暂停后可恢复；`jobs` 表给索引进度的轮询。
6. **向量索引可替换**：`retrieval/vector_store.py` 定义 `VectorStore` 协议，V1 用 SQLite BLOB + numpy 实现，避免 Chroma 打包体积/兼容风险。
7. **上下文默认锁在选区**：片段对话默认只把选中的代码交给模型，不主动检索（`use_context=false`），需要周边实现时由用户显式打开「联系上下文」。早期版本无条件跑 `hybrid.search` 并把全部命中塞进 citation，导致「问一段代码、答整份文件」。
8. **知识库跨项目、索引不跨项目**：`notes` 仍归属创建它的项目（检索与 `note://` 片段按项目走），阅读与归档由一个全局面板承担；`note_folders` 用 `parent_id` 自引用成树。移动必须显式 `move_folder`，否则 `null` 无法与「不改」区分。
9. **标题由内容决定**：会话与笔记的标题都从回答内容提炼（`notegen.title_from_answer` 不调模型，`retitle` 才调），占位标题（`新对话`、`foo.py:12-30`）在 `finalize` 里被替换并 emit `status.stage="title"`；用户手改过的标题永不覆盖。历史对话不会再走 `finalize`，所以启动时由 `backfill_conversation_titles()` 补一次（优先取该对话笔记的标题，其次取最后一条回答；改写不碰 `updated_at`，避免历史对话被顶到侧栏最上面）。

## 分层边界

| 层 | 可以做 | 不可以做 |
|---|---|---|
| renderer | 展示、调用 API、Monaco 交互 | 直接读磁盘 / 起进程 |
| Electron main | 启动/回收 Python 子进程、注入令牌、窗口生命周期 | 业务数据写入 |
| FastAPI | 校验、编排、读写本地数据 | 直接调用模型 SDK 以外的网络写操作 |
| agent/tools | 通过受控工具读写文件、跑命令 | 绕过授权节点直接落盘 |
| indexing | 只读项目文件、写索引表 | 修改用户源码 |

## 开发/运行

- 后端：`cd backend && uv run fastapi dev app/main.py`（默认生成临时令牌并打印；`--token` 可通过环境变量 `CODESLICE_TOKEN` 固定）。
- 前端：`cd frontend && npm run dev`（Vite 3000 端口，代理到后端）。
- 桌面：`cd desktop && npm run dev`（Electron 拉起后端 + 加载 Vite）。
- 打包：`packaging/build_backend.ps1` → PyInstaller；`desktop/npm run dist` → electron-builder 出 Setup.exe。

## 第一版范围（明确不做）

- 不做多用户 / 云同步；不做 LSP 语义补全；不做完整项目级重构 Agent（只做单文件写入 + 命令执行 + 测试验证）。
- 非 Tree-sitter 支持的语言退化为文本块检索，仍可提问。
- 三维知识图谱、热力图、MCP 文档导入属于后续版本（文档里已记为 Roadmap）。
