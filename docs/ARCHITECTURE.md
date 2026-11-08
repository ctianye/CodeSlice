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
                        rules, jobs, index, search, chat, terminal, graph）
  indexing/             过滤、语言识别、切片、Embedding 调用
  retrieval/            关键词 + 向量混合检索
  models/               模型 / Embedding 适配层、凭据
  services/             路径校验、git、锚点、终端沙箱（sandbox / terminal）
  agent/                LangGraph 图、状态、事件、授权中断、节点配置
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
7. **索引由后端维护**：项目打开和 `watchfiles` 检测到源码变化时自动排队增量扫描，任务按项目去重；笔记先同步关键词索引，再异步生成向量。切换 embedding 模型会自动补建向量，检索只使用当前模型的向量，renderer 只显示状态。
7. **上下文默认锁在选区**：片段对话默认只把选中的代码交给模型，不主动检索（`use_context=false`），需要周边实现时由用户显式打开「联系上下文」。早期版本无条件跑 `hybrid.search` 并把全部命中塞进 citation，导致「问一段代码、答整份文件」。
8. **知识库跨项目、索引不跨项目**：`notes` 仍归属创建它的项目（检索与 `note://` 片段按项目走），阅读与归档由一个全局面板承担；`note_folders` 用 `parent_id` 自引用成树。移动必须显式 `move_folder`，否则 `null` 无法与「不改」区分。
9. **会话标题与笔记标题独立生成**：占位会话标题在 `finalize` 里根据整段问答和助手结论生成，笔记标题由笔记整理提示词单独提炼知识点；历史会话回填只读最后一条可用回答，不借用笔记标题。用户手改过的会话标题永不覆盖，回填也不碰 `updated_at`。
10. **编辑器是多标签但单面板**：`AppState` 里存 `tabs: EditorTab[]` + `activeTabIndex`，`openFile` / `noteView` 降级为 `activeTab` 的 `useMemo` 派生值——这样二十多处消费者（CodePane、NoteReader、FileTree、NotesPanel…）不必知道标签的存在，行为和单文档时完全一致。点笔记里的引用会把文件**追加**成新标签而不是替换当前笔记，避免「看引用就丢掉正在读的笔记」。
11. **终端与 Agent 共用一套命令策略**：危险命令黑名单（`services/sandbox.py` 的 `BLOCKED_FRAGMENTS`）是同一个对象，AI 的 `run_command` 和用户在终端敲的命令命中同一份名单；终端历史只放内存环形缓冲（命令可能带 `-H` token，不能进明文 SQLite）。实现是 `Popen(shell=True)` + 读线程 + `queue` 的真流式，不是 PTY，因此没有跨命令的 shell 状态。
12. **工作流节点可配、拓扑不可改，且只有后端接口**：`agent/node_config.py` 的 `NODES` 既是默认值表也是校验 schema，`GET /api/agent/graph` 直接序列化它，节点在运行时读自己的合并配置（存在 `settings` 表的 `agent.node_config` 一个 JSON 键里），所以保存后下一轮即刻生效、无需重启；边的走向由 `graph.py` 固定。这条能力**只暴露成后端 HTTP 接口**，桌面前端不再画流程图——用户要调的是 LangGraph 这一层的行为，不需要在界面上看一张只读的拓扑图。

13. **Markdown 默认渲染、编辑才进 Monaco**：`.md` / `.mdx` 打开即走 `MarkdownDoc`（复用对话与笔记那套零依赖渲染器，另加图片解析），不挂 Monaco；工具栏「阅读 / 编辑」切到编辑态才换成可写的 Monaco。理由：代码阅读器里 Markdown 的源码视图没有价值，而正文有。图片走 `GET /asset` 的 **query token** 鉴权（`<img>` 无法带自定义头），路径按文档所在目录解析相对路径、`/` 开头视为仓库根（与 GitHub 一致），远程 URL 一律不解析以免绕过 CSP。
14. **保存以 mtime 做乐观锁，不做合并**：`PUT /file` 带 `expected_mtime`，与磁盘当前值差超过 1 毫秒就 400 拒绝，前端把这条英文原因翻成「文件在打开后被改动过，请重新打开再编辑」。不做三方合并：文档编辑器一旦自动合并，用户很难判断最终写进仓库的是什么。未保存草稿按 tab 路径留在 `docDraftsRef`，切标签或切渲染模式都不会无声丢失。

## 分层边界

| 层 | 可以做 | 不可以做 |
|---|---|---|
| renderer | 展示、调用 API、Monaco 交互 | 直接读磁盘 / 起进程 |
| Electron main | 启动/回收 Python 子进程、注入令牌、窗口生命周期 | 业务数据写入 |
| FastAPI | 校验、编排、读写本地数据 | 直接调用模型 SDK 以外的网络写操作 |
| agent/tools | 通过受控工具读写文件、跑命令 | 绕过授权节点直接落盘 |
| services/terminal | 在项目根内、黑名单外起子进程 | 跳出项目目录 / 复用 shell 状态 |
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
