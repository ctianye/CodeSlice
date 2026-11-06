# CodeSlice

> 本地优先的「代码阅读」智能体桌面应用。把任意一个代码仓库拖进来，边读边问，
> 每个回答都带可点击的文件/行号引用；需要动文件或跑命令时，逐条向你申请授权。

CodeSlice 是一个跑在你机器上的 Electron 桌面程序 + 本地 Python 服务。
模型调用走你自己的 API Key，代码、索引、对话记录全部留在本地
（安装版在 `%APPDATA%\codeslice-desktop`，见[数据与凭据](#数据与凭据)），
不上传任何服务器。

---

## 目录

- [核心特性](#核心特性)
- [架构](#架构)
- [技术栈](#技术栈)
- [仓库结构](#仓库结构)
- [本地开发](#本地开发)
- [构建与打包](#构建与打包)
- [数据与凭据](#数据与凭据)
- [安全模型](#安全模型)
- [V1 范围与路线图](#v1-范围与路线图)
- [常见问题](#常见问题)

---

## 核心特性

| 能力 | 说明 |
| --- | --- |
| **边读边问** | 三段式界面：文件树 / 代码（Monaco）/ 对话。选中代码即可提问，回答锚定在具体行。 |
| **多标签编辑器** | 像 VS Code 那样同时开多个文件和笔记，标签可拖拽排序、中键或 × 关闭、右键「关闭其他」。点笔记里的引用会新开一个代码标签，原来的笔记不会消失。 |
| **集成终端** | 编辑器下方可展开的终端，直接跑命令；输出逐行流式到达，可拖拽调高、↑↓ 翻历史。 |
| **AI 可执行命令** | 对话里模型调 `run_command` 走的审批卡与终端是同一套沙箱：同一份危险命令黑名单，同一条 `command_rules` 授权规则。 |
| **工作流面板** | 侧栏「工作流」把 LangGraph 的四个节点（准备上下文 / 模型推理 / 执行工具 / 收尾）画成流程图，每个节点可改自己的参数，保存后下一轮生效。 |
| **可溯源的引用** | 每条回答附 `文件:行号` 引用块，点击直接跳转并用左侧色条标出范围；再点同一个引用块取消标亮。 |
| **混合检索** | SQLite FTS5 关键词检索 + numpy 余弦相似度语义检索，结果融合排序。 |
| **结构化索引** | Tree-sitter 解析 Python / TS / JS / Go / Rust / Java / C / C++ / JSON / Bash，按函数、类、模块切块。 |
| **逐条授权** | 写文件、执行命令必须经你批准，规则可设为「仅本次 / 本会话 / 总是」。没有「信任整个项目」一键放行。 |
| **代码锚点** | 笔记与引用绑定到代码位置；文件改动后自动标记为 `modified` / `moved` / `deleted`。 |
| **笔记入知识库** | 对话里一键把回答整理成固定模板笔记，自动进入检索管道可被再次引用，并可导出为 `.md`。知识库跨项目：一个面板看全部项目的笔记，可按任意层级的文件夹归档（`note_folders` 自引用树）。 |
| **代码对话卡** | 打开文件时，编辑器上方列出这个文件上的片段对话；鼠标悬停高亮对应行，点击回到那段对话。 |
| **只讲选中的代码** | 片段对话默认只看选中的那一段，不主动去别处读文件；需要周边信息时在输入框打开「联系上下文」。 |
| **按行提问** | 点行号选整行，再点别处继续累加、点同一行取消，「就这些行提问」把这一组行当作上下文。 |
| **对话分区** | 侧栏把「本项目对话」置顶、「其他对话」折叠在下，列表项标注来自哪个项目。 |
| **索引状态自检** | 打开项目即检查索引：从未索引时静默建立，已索引则静默增量刷新，无需手动操作；工具栏常驻「309 文件 · 1508 片段」累计徽标。 |
| **知识点标题** | 对话与笔记的标题由回答内容概括（「多轮对话消息两种写法」），不再是「关于 README.md 第 357-359 行」。 |
| **本地凭据** | API Key 存 Windows 凭据管理器，绝不写进 SQLite。 |

---

## 架构

```
┌─────────────────────────────────────────────────────────────┐
│  Electron 主进程 (desktop/)                                 │
│  · 启动后端子进程，读取 stdout 的握手行获取端口 + Token      │
│  · 通过 preload 把 apiBase / token 注入渲染进程             │
└───────────────┬─────────────────────────┬───────────────────┘
                │ BrowserWindow           │ spawn
                ▼                         ▼
┌───────────────────────────┐   ┌─────────────────────────────────┐
│ 渲染进程 (frontend/)       │   │ 本地服务 (backend/)             │
│ React 18 + TS + Monaco     │──▶│ FastAPI + Uvicorn              │
│ · 文件树 / 代码 / 对话     │HTTP│ · 127.0.0.1:<随机端口>          │
│ · SSE 流式对话             │SSE │ · X-Session-Token 鉴权          │
│ · 授权卡片                 │   │ · LangGraph 智能体编排          │
└───────────────────────────┘   └───────────┬─────────────────────┘
                                            │
                    ┌───────────────────────┼────────────────────┐
                    ▼                       ▼                    ▼
            ┌───────────────┐      ┌────────────────┐   ┌────────────────┐
            │ SQLite        │      │ Tree-sitter    │   │ 模型 API       │
            │ · 业务表       │      │ · 结构化切块    │   │ OpenAI 兼容 /  │
            │ · FTS5 全文    │      │ · 函数/类边界   │   │ Anthropic      │
            │ · 向量 (BLOB)  │      └────────────────┘   └────────────────┘
            │ · LangGraph    │
            │   checkpoint   │
            └───────────────┘
```

**启动握手**：后端在真正开始监听之后，向 stdout 打印一行

```
CODESLICE_READY {"port": 54746, "token": "xN-Nxo...", "version": "0.1.0"}
```

Electron 主进程读到这一行才知道该连哪个端口、用什么 Token。
`CODESLICE_PORT=0` 时端口由内核分配，因此安装版不会出现端口冲突。

---

## 技术栈

| 层 | 技术 | 选型理由 |
| --- | --- | --- |
| 桌面壳 | Electron + electron-builder (NSIS) | 需要管理子进程、原生文件对话框、系统凭据 |
| 界面 | React 18 + TypeScript + Vite 5 | 成熟的编辑器周边生态，构建快 |
| 代码显示 | Monaco Editor | 与 VS Code 同源，语法高亮与 diff 开箱即用 |
| API | FastAPI + Uvicorn | 原生 async、SSE 简单、Pydantic 校验 |
| 智能体 | LangGraph + LangChain Core | 显式状态图，中断/恢复（人工审批）是一等公民 |
| 模型接入 | `langchain-openai` / `langchain-anthropic` | 协议由用户显式选择，不做协议嗅探 |
| 存储 | SQLite（WAL）+ FTS5 | 零运维、单文件、可随应用一起搬运 |
| 向量检索 | numpy 余弦相似度 | V1 规模（万级 chunk）足够，且可替换成任意 VectorStore |
| 语法分析 | Tree-sitter + 逐语言 grammar 包 | 增量解析、跨语言、无需语言服务器 |
| 凭据 | `keyring`（Windows Credential Manager） | 交给操作系统保管，不落盘明文 |

---

## 仓库结构

```
CodeSplit/
├── backend/                  # 本地 Python 服务
│   ├── app/
│   │   ├── main.py           # FastAPI 入口 + CODESLICE_READY 握手
│   │   ├── api/              # 路由：projects / indexing / conversations / notes / terminal / graph_api ...
│   │   ├── agent/            # LangGraph 图、工具、prompts、checkpoint、节点配置
│   │   ├── core/             # 配置、SQLite、错误类型
│   │   ├── indexing/         # 语言识别、Tree-sitter 切块、索引器
│   │   ├── models/           # 模型适配器、embedding
│   │   ├── retrieval/        # 关键词 + 语义混合检索
│   │   ├── services/         # 路径校验、git、锚点、终端沙箱、命令执行
│   │   └── storage/          # 数据访问层
│   ├── pyproject.toml
│   ├── scripts/              # smoke / e2e_contract / ui_smoke / mock_model / cdp_reload 联调脚本
│   ├── tests/                # pytest（17 个文件、237 个用例）
│   └── .venv/                # uv 管理的虚拟环境（Python 3.11）
├── frontend/                 # Vite + React 渲染进程
│   └── src/
│       ├── components/       # FileTree / CodeViewer / ChatPane / EditorTabs / TerminalPanel / GraphPanel ...
│       ├── api.ts            # HTTP 客户端
│       └── bridge.ts         # Electron preload 桥接
├── desktop/                  # Electron 主进程 + 打包配置
├── docs/
│   ├── ARCHITECTURE.md       # 架构契约（冻结）
│   └── API_CONTRACT.md       # API 契约（冻结）
├── packaging/                # 打包流水线（见 packaging/README.md）
├── scripts/
│   └── dev.ps1               # 一键启动开发环境
```

---

## 本地开发

**前置**：Windows 10/11、Python 3.11、[uv](https://docs.astral.sh/uv/)、Node.js 24、npm。

### 一键启动

```powershell
pwsh -File scripts\dev.ps1
```

脚本会：固定 `CODESLICE_TOKEN=dev-token` 与端口 8000 → `uv sync` → 在独立窗口启动后端
→ 轮询 `/api/health` 直到就绪 → 启动 `desktop/npm run dev`。

### 分步启动

三个终端依次执行：

```powershell
# 1) 后端 —— http://127.0.0.1:8000
cd backend
uv sync
$env:CODESLICE_TOKEN = "dev-token"   # 可选：固定 Token，默认每次启动随机
uv run fastapi dev app/main.py --port 8000
```

> `fastapi dev` 有自己的 `--port` 参数，它不会读取 `CODESLICE_PORT` 环境变量
> （那个变量只在直接运行打包后的 `codeslice-backend.exe` 时生效）。

```powershell
# 2) 渲染进程（浏览器调试用）—— http://localhost:3000
cd frontend
npm install
npm run dev
```

```powershell
# 3) 桌面壳（会加载 1) 和 2)）
cd desktop
npm install
npm run dev
```

后端启动后，`GET http://127.0.0.1:8000/api/health` 可直接验证；交互式文档在
`/api/docs`。所有 `/api/*` 请求都需要 `X-Session-Token` 头（仅 `/api/health` 豁免）。

浏览器模式下（没有 Electron 注入 Token），可在界面里手动粘贴 Token，或写入
`frontend/.env.local`：

```
VITE_SESSION_TOKEN=dev-token
```

桌面壳在开发模式下从 `CODESLICE_SESSION_TOKEN` 读取 Token（`scripts\dev.ps1` 会同时
设置它与 `CODESLICE_TOKEN`）。若手动分步启动，需自行设置：

```powershell
$env:CODESLICE_SESSION_TOKEN = "dev-token"
$env:CODESLICE_DEV_URL = "http://127.0.0.1:3000"   # 可选，默认就是这个
cd desktop; npm run dev
```

---

## 构建与打包

```powershell
# 1) 后端 → packaging\dist-backend\codeslice-backend.exe（扁平布局）
pwsh -File packaging\build_backend.ps1

# 2) 前端 + Electron → desktop\release\CodeSlice-0.1.0-Setup.exe
pwsh -File packaging\build_desktop.ps1
```

第一步会额外跑自检与冒烟测试（真实启动 exe、握手、调 `/api/health`、验证未授权请求
返回 401、确认 SQLite 初始化）。第二步会先把 `frontend\dist` 同步到 `desktop\dist`
（electron-builder 既把 `desktop\dist` 打进 asar，又把它作为 `extraResources` 交给后端
HTTP 服务，两处必须是同一份产物），再校验安装包内确实包含
`resources\backend\codeslice-backend.exe`，并比对安装包里的前端资源名与
`desktop\dist` 是否一致（防止增量构建把上一版前端打进安装器）。

安装器构建分三步（`--dir` → rcedit 写图标/版本 → `--prepackaged` 封包），原因是
本地未签名时 electron-builder 会跳过 rcedit，而它的 `signAndEditExecutable` 路径需要
解压含符号链接的 winCodeSign 包，非管理员无法完成。脚本全程不修改 `desktop/` 的配置，
细节见 **[packaging/README.md](packaging/README.md)** 第 3 节。

后端产物直接放在 `packaging\dist-backend\`，不会被复制进 `desktop/`：
`electron-builder.yml` 的 `extraResources.from` 指的就是那里。

> 注意：`uv sync` / `uv run` 会把 PyInstaller 从 `backend\.venv` 里卸掉（它只在
> `packaging\requirements-packaging.txt` 里）。同步过依赖之后，重跑
> `build_backend.ps1` 时**不要**加 `-SkipInstall`。

打包细节、参数与故障排查见 **[packaging/README.md](packaging/README.md)**。

### 验证打包产物

安装包里的后端是 PyInstaller 单文件产物，工作目录、静态挂载与数据目录都和源码运行不同，
因此它需要单独的检查（都以 httpx 直连，绕开控制台编码问题）：

```powershell
# 打包后端已启动：codeslice-backend.exe 会打印 CODESLICE_READY {"port":..., "token":...}
backend\.venv\Scripts\python.exe backend\scripts\e2e_contract.py --base http://127.0.0.1:8199 --token pkgtoken
backend\.venv\Scripts\python.exe backend\scripts\check_packaged_notes.py --base http://127.0.0.1:8199/api --token pkgtoken
```

`--base` 传**裸 origin**（脚本自己拼 `/api`）；要让 `e2e_contract.py` 里的 `GET /` 通过，
启动时把 `CODESLICE_FRONTEND_DIST` 指向 `desktop\release\win-unpacked\resources\frontend`。
`check_packaged_notes.py` 专门覆盖中文笔记的标题往返、`note://` 入库、导出文件名与
「chunk 不重复标题」这些 PowerShell 控制台验证不了的部分。

> 改了 `backend/app/**` 之后，**先**重跑 `build_backend.ps1` 再跑 `build_desktop.ps1`：
> 后者只读 `packaging\dist-backend\`，不会重新编译后端，否则安装包里仍是旧代码。

---

## 数据与凭据

数据目录按运行方式不同，**安装版和开发版不共用同一个数据库**：

| 运行方式 | 数据目录 |
| --- | --- |
| 安装版（`CodeSlice.exe`） | `%APPDATA%\codeslice-desktop\` |
| 开发版（`uv run fastapi dev` / `codeslice-backend.exe` 手工启动） | `%LOCALAPPDATA%\CodeSlice\` |
| `scripts\dev.ps1` 一键启动 | 仓库下 `.devdata\` |

原因是：安装版由 Electron 主进程显式设置 `CODESLICE_DATA_DIR=app.getPath('userData')`，
而 `app.getPath('userData')` 取 `package.json` 的 `name`（`codeslice-desktop`），所以是
`%APPDATA%\codeslice-desktop`；开发模式下没人设置该变量，后端回落到
`backend/app/core/config.py` 的默认值 `%LOCALAPPDATA%\CodeSlice`。

| 内容 | 位置 |
| --- | --- |
| SQLite 数据库 | `<数据目录>\codeslice.db` |
| 日志 | `<数据目录>\logs\backend.log` |
| LangGraph 检查点 | `<数据目录>\checkpoints.db` |
| 窗口尺寸等桌面状态 | 仅安装版：`%APPDATA%\codeslice-desktop\window-state.json` |
| 模型 API Key | Windows 凭据管理器，服务名 **`codeslice`** |
| 会话 Token | 仅进程内存，每次启动随机生成 |

可用环境变量 `CODESLICE_DATA_DIR` 覆盖数据目录（开发脚本会指向仓库下的 `.devdata\`）。

**数据库表**

| 表 | 作用 |
| --- | --- |
| `projects` | 已打开的项目（路径、排除标记、最后索引时间） |
| `indexed_files` | 每个文件的索引状态：`indexed` / `skipped` / `error` |
| `chunks` | 代码块（函数/类/模块/文本块）+ 向量 BLOB + 内容哈希 |
| `chunks_fts` | FTS5 全文索引（中文按重叠二元组存词、英文小写词，关键词检索按覆盖率排序） |
| `conversations` / `messages` | 对话与消息（含引用、工具调用元数据） |
| `code_anchors` | 绑定到代码位置的锚点，状态 `unchanged` / `modified` / `moved` / `deleted` |
| `notes` | 阅读笔记；`folder_id` 指向知识库文件夹（可空 = 未分类） |
| `note_folders` | 知识库文件夹，`parent_id` 自引用成任意层级的树 |
| `command_rules` | 命令授权规则（`once` / `session` / `always`） |
| `approvals` | 待处理/已完成的授权请求 |
| `jobs` | 后台索引任务（进度、统计、错误） |
| `settings` | 非敏感配置（模型名、Base URL 等） |

---

## 安全模型

CodeSlice 按「不信任本地服务的调用方」来设计——因为同一台机器上的其他进程也能访问
回环端口。

1. **只监听回环地址**。服务绑定 `127.0.0.1`，不监听 `0.0.0.0`，局域网内不可达。
2. **随机端口**。`CODESLICE_PORT=0` 由内核分配空闲端口，避免与已知服务冲突，
   也不给固定端口的扫描留便利。
3. **每次启动的会话 Token**。随机生成（`secrets.token_urlsafe(24)`），通过 stdout
   握手交给 Electron 主进程，再经 preload 注入渲染进程。所有 `/api/*` 请求必须带
   `X-Session-Token`，否则 `401 {"detail": "invalid session token"}`。
   仅 `GET /api/health` 豁免（用于启动探活）。
4. **API Key 永不进数据库**。写入 Windows 凭据管理器，服务名 `codeslice`。
   `GET /api/settings` 只返回 `api_key_configured: bool`，不返回明文。
   读取走 `keyring.get_password`，删除走 `keyring.delete_password`。
5. **按命令授权，而不是「信任整个项目」**。写文件与执行命令都触发
   `approval_required` 事件，用户在授权卡片上看到具体内容（目标路径、完整命令、
   理由）后决定。可保存为规则：`once`（仅本次）/ `session`（本会话）/
   `always`（该项目内该模式）。**没有**「信任该项目全部操作」的一键开关——
   这正是为了让「读代码」的默认路径保持无副作用。
6. **路径逃逸防护**。所有文件操作都经 `resolve_in_project()` 校验，拒绝 `..`
   穿越与指向项目外的符号链接；`DEFAULT_IGNORES` 跳过 `.git`、`node_modules`、
   `.venv` 等目录。
7. **模型协议显式声明**。配置里必须选择 `openai-compatible` 或 `anthropic`，
   代码不做协议嗅探——避免把 Key 发给非预期的端点。

---

## V1 范围与路线图

**V1 已实现**

- 单项目、单用户、本地运行
- Tree-sitter 结构化切块 + FTS5/向量混合检索
- 流式对话、引用跳转、代码锚点、阅读笔记
- 代码对话卡：打开文件即列出该文件的片段对话，悬停高亮对应行、点击回到对话、一键生成笔记
- 编辑器知识点标记：有对话的行区间常驻描边，上方一条随滚动移动的标题条（不遮挡任何代码），
  点标题条回到那条对话
- 行号点选整行 / shift 扩展 / 行号栏拖选多行 / Ctrl·⌘ 点击累加多行，并针对大文件关掉了
  拖选时的高开销高亮、把 mousemove 合并到动画帧
- 片段对话默认只讲选中的代码，需要周边信息时用「联系上下文」开关显式放开
- 对话列表分区：本项目对话置顶、其他项目对话在下并标注来源项目
- 笔记整理与知识库回环：单条回答或整段会话一键整理成固定模板笔记（知识点 / 关联代码 /
  要点 / 何时使用），标题概括知识点而非「关于 X 第 N-M 行」，写入检索索引后可被后续提问
  引用，并支持导出 `.md`
- 笔记阅读器：点笔记在中间主面板渲染 Markdown，可就地编辑 / 重拟标题 / 导出 / 回到代码
- 知识库跨项目：一个面板看全部项目的笔记，可按任意层级文件夹归档（删除文件夹只把笔记
  落回未分类）
- 打开项目即自检索引状态：从未索引时静默建立，已索引静默增量刷新，工具栏显示累计的
  「N 文件 · M 片段」
- 写文件 / 执行命令的逐条授权与规则记忆
- 多标签编辑器：代码与笔记共用一条标签栏，可同时开多个、拖拽排序、中键或 × 关闭、右键
  「关闭其他」；点笔记里的引用会新开一个代码标签而不是把笔记顶掉
- 集成终端：编辑器下方可展开，命令输出逐行流式到达（不是跑完一次性刷出），可拖拽调高、
  ↑↓ 翻历史、运行中可停止；与 AI 的 `run_command` 共用同一份危险命令黑名单
- 工作流面板：侧栏「工作流」画出 LangGraph 的四个节点与它们之间的条件边，每个节点可改
  自己的参数（检索段数、模型、temperature、输出上限、追加指令、工具轮数上限、是否自动
  重拟标题），保存后下一轮对话生效，拓扑本身不可改
- 模型配置（OpenAI 兼容 / Anthropic）+ 连通性测试
- 本地向量模型一键下载：设置页内置 `bge-m3` / `qwen3-embedding:0.6b` 两个预设，
  通过本机 Ollama 下载并自动写入 Embedding 配置（切换模型后提示重建索引）
- PyInstaller 后端 + electron-builder NSIS 安装器

**本地向量模型（Ollama）**

「设置 → 模型 → 向量模型」里有两条预设卡片，下载与配置都在界面完成，不需要手填
Base URL：

| 预设 | 体积 | 上下文 | 参数 | 说明 |
| --- | --- | --- | --- | --- |
| `bge-m3` | 1.2 GB | 8K | 567M | 中英文 + 代码综合表现均衡 |
| `qwen3-embedding:0.6b` | 639 MB | 32K | 0.6B | 长文件与代码检索更强 |

两者都通过 Ollama 的 OpenAI 兼容端点 `http://127.0.0.1:11434/v1` 提供服务，
所以后端仍然只用一套 `OpenAIEmbeddings` 客户端。使用前提是本机已安装并启动
[Ollama](https://ollama.com/download)（未安装时卡片置灰，界面给出下载入口）。

- **下载**：`POST /api/settings/ollama/pull` 把 Ollama 的进度流转成 SSE，卡片上实时显示
  百分比；服务端**不**在下载完成后自动改配置。
- **使用**：点「使用此模型」才会 `PUT /api/settings/model` 写入
  `embedding = {model, base_url: <...>/v1, api_key: "ollama"}`。Key 走凭据管理器，
  占位串不会出现在 SQLite 里。
- **重建索引**：向量模型变了，旧向量与新 query 向量不在同一空间，必须重嵌。
  界面检测到 `embed_models` 与当前模型不一致时会先确认，再提供「重建索引」按钮。
  重新索引只重嵌向量，不会重新解析文件结构。
- **不用 Ollama**：展开「高级：手动配置」，仍可填任意云端 Embedding（模型名 / Base
  URL / API Key）。

**笔记进入知识库**

对话里有两个生成入口：回答下方「整理成笔记」总结**这一条回答**，对话标题栏「整理成笔记」
把**整段会话**折叠成一条（长会话常跨多个文件，逐条总结会丢掉主线）。生成的不是原文摘抄，
而是按固定模板重排的结构化笔记：

```
# <标题>          ← 知识点概括，不是「关于 README.md 第 357-359 行」
## 知识点
## 关联代码      ← 由 citations 从磁盘读取原文渲染，带 路径:起-止
## 要点
## 何时使用
```

标题由模型在 `## 标题` 一节单独给出（6–18 字），后端还会再清洗一遍：`foo.py:12-30`、
`第 357-359 行`、markdown 标记都会被去掉，以「关于」开头的直接判为无效并退回知识点首句。
旧笔记可以点「重拟标题」让模型重读正文改标题（`POST /api/notes/{id}/retitle`），模型失败时
保留原标题而不是报错。

对话标题走同一条规则，但**不额外调模型**：一轮回答结束（`finalize`）时，如果标题还是应用
自己起的占位（`全项目对话`、`关于 README.md 第 357-359 行`、`<项目> · 阅读笔记`），就用回答
的第一句可用内容改掉，并通过 `status.stage="title"` 推给界面即时刷新；用户自己改过的标题
永远不覆盖。「第一句可用内容」会跳过「抱歉…」这类空回复兜底话术，以及「我已经把…摸清楚
了」这种讲过程而非讲代码的开场白，继续往下找。

更早的对话不会再产生新一轮回答，占位标题会一直留着，所以启动时跑一次
`notegen.backfill_conversation_titles()`：从该对话已有笔记的标题（模型写过的最好）或最后一条
回答里取标题，改写时不碰 `updated_at`（否则整条侧栏会被历史对话顶到最上面），用户手写的
标题同样跳过。

- 笔记写入 `chunks` 时用 `path = note://<id>`、`kind = "note"`，与代码片段共享同一个
  FTS5 / 向量索引，因此「之前的笔记」能被普通检索和对话检索命中；命中项在文件面板中标
  为「知识库」，点击跳到笔记面板而不是编辑器。
- `## 关联代码` 的围栏长度按片段内最长的反引号串决定：引用一段 Markdown 时（片段自带
  ``` 围栏）外层用 ````，否则渲染会在第一个内层围栏处提前结束、把剩余内容当成正文段落。
- 模型调用失败不会丢内容：降级保存原始回答并标记 `summary_failed`，界面提示原因。
  输出预算耗尽（`finish_reason=length`）导致的空回复会用更大的预算再试一次。
- 编辑笔记即时刷新对应片段，删除笔记会同步移除片段与 FTS 行（`index_project` 末尾也会
  重新同步，避免全量重建索引后笔记消失）。
- 「导出」把笔记写到 `<data_dir>/notes/<项目名>/<标题>.md`，重名自动追加时间戳。

**笔记阅读器**

点侧栏任意笔记，Markdown 直接渲染在中间的主面板里（编辑器被替换），顶部是知识点标题与
状态、来源项目、编辑 / 重拟标题 / 导出 / 删除 / 回到代码。不再用侧栏内的小编辑器弹窗——
长笔记在窄栏里没法读。标题栏右侧还有一个文件夹选择器，选中即归档，不需要先回列表。

**知识库跨项目**

笔记面板不再是「当前项目的笔记」：范围条第一项是「全部项目 N」，列出所有项目的笔记并在
时间行标注来源（`CodeSplit`、`langchain-project`…），第二项「未分类」，下面是文件夹树。
文件夹可以任意嵌套（`note_folders` 是自引用表），新建 / 重命名 / 删除都在面板里完成。

- 归属不变：笔记仍属于创建它的项目（检索、导出、`note://` 片段都按项目走），跨项目只影响
  **阅读与归档**的入口，所以不会把不同项目的向量索引混在一起。
- 删除文件夹不删笔记：其中的笔记落回「未分类」，子文件夹级联删除。
- 移动必须显式传 `move_folder`（`PUT /api/notes/{id}`）：不传时 `folder_id: null` 和「不改」
  在 JSON 里无法区分，否则永远没法把笔记移出文件夹。
- 空对话不留垃圾：启动时清掉超过 60 分钟且一条消息都没有的会话；前端切换对话时也会删掉
  刚建但没发送的草稿。侧栏依赖「无消息的对话也要能列出」来显示正在输入的草稿，所以清理
  放在启动时机而不是列表查询里。

**代码对话卡**

打开一个有历史对话的文件，编辑器工具栏下方会出现一条「文件对话」卡带（不是浮层——
代码一行都不会被挡住）：

- 按行范围分组，组标签是 `L12–20` 这样的区间；同一区间超过 3 条对话时显示 `+N` 展开。
- 卡片标题优先显示该对话笔记的知识点标题，并带一个「知识点」小标；没有笔记时退回会话标题。
- 鼠标悬停某张卡，编辑器里对应行整体铺一层淡色底——只在悬停时出现，不打断阅读。
- 点卡片直接把对话载入右侧对话面板，同时侧栏高亮那一条并滚动到它（标题条点击同理：
  之前只改 `activeConversationId`，切到别的 tab 时那条对话根本看不见）。
- 卡片右侧是笔记按钮：已有笔记就显示「打开笔记」，没有就显示「生成笔记」，一次点击把
  该对话最新一条回答整理成笔记并跳到笔记阅读器。
- 新建一个片段对话后卡片立刻出现（发消息、生成笔记都会触发刷新）。

**编辑器里的知识点标记**

有对话的代码区间会常驻描边，区间上方还有一条随内容滚动的标题条（Monaco view zone，真的
让出 19px 空白带，不会盖住上一行代码）；点标题条跳到那条对话。这样不开侧栏也能看出
「这段代码已经被读过、结论是什么」。

- 点击行号选中整行，按住 shift 点另一行是扩展选区，从行号栏往下拖是多行选区。
- 点行号是**累加**：一行一行攒出一组互不相邻的行（选中的行左边有竖条、底色加深），再点
  同一行把它移出，底部动作条显示「已选 3 行 · 12, 40, 88」并可「就这些行提问」或「清除」。
  之前只有 Ctrl/⌘ 点击才累加，普通点击会清掉上一行——攒多行必须一直按着修饰键，实际没人
  这么用。
- 拖选做了针对性优化：关掉了每次选区变化都要扫描缓冲区的 occurrence 高亮与 bracket
  matching，拖选的 `mousemove` 也和 setSelection 一样用 `requestAnimationFrame` 合并，
  大文件里拖选不再发黏。

**片段对话只看选中的代码**

片段模式默认把问题锁在选中的那段代码上：system prompt 明确要求不主动读别的文件，缺少
信息就直说，`prepare` 也不会去跑检索、不会多吐 citation。需要周边实现时，在输入框左下角
打开「联系上下文」，这时才会带上同文件的相关片段并允许检索（`use_context=true`）。
之前片段对话会把 `hybrid.search` 的 8 条结果全部标成引用、模型也被告知「可以自己去读附近
代码」，于是回答经常跑题到整份文件。

数据来自 `GET /api/projects/{id}/conversations/by-file?path=`，`path` 会被服务端校验，
越界路径返回 400；每条额外带 `note_id` / `note_title` 供卡片与漂浮标签直接用。

**对话分区**

侧栏对话列表分两段：「本项目对话」在前（`updated_at` 倒序），「其他对话」在后并把每条
的所属项目名写进副标题。一次请求即可拿到两段数据
（`GET /api/conversations?project_id=&include_others=true`），切项目时跟着切。

**索引状态自检**

打开项目时会调 `GET /api/projects/{id}/index`：从未索引过就直接在后台建一次索引（不再让
用户先点按钮），已索引过则静默跑一次增量索引（只处理 size/mtime 变化的文件），完成后自动
刷新文件树与列表，不打断当前操作。增量任务不弹「收起」卡片，失败的静默刷新也不会打扰你，
手动按钮仍会正常报错。

工具栏上的「309 文件 · 1508 片段」是**当前索引的累计值**（`files` / `chunks` / `vectors`），
不是最近一次任务的增量。之前它读 `last_job.stats`，增量只扫了 7 个文件就显示
「文件 7 / 片段 7 / 向量 0」，看起来像索引是空的。未配置向量模型时 `vectors` 恒为 0，
检索退回关键词模式，这是正常状态而不是失败。

**已知局限（V1）**

- 助手回答用内置轻量 Markdown 渲染（标题 / 列表 / 行内代码 / 围栏代码块 / 粗体 / 链接 /
  管道表格），不做完整 Markdown（无 HTML 透传）；引用以可点击的「路径:行号」小片呈现。
  围栏按「长度 ≥ 开栏」匹配闭合，因此引用自带 ``` 的 Markdown 片段不会提前截断。
- 代码对话卡按「行范围完全相等」分组：`L12–20` 与 `L13–20` 会被算作两组，不会合并。
- 侧栏「文件 / 对话 / 笔记 / 锚点 / 工作流」是标签页切换，不是全部堆叠展示。
- 编辑器是「一条标签栏 + 一个可见面板」：同时开多个文件/笔记时只有一个在上层，切换靠标签；
  没有 VS Code 那样的左右分屏。
- 终端**不是 PTY**：命令一次性执行、彼此独立，所以 `cd` 不跨命令保留、`vim` 这类全屏程序
  不能用，输出按纯文本渲染（不解析 ANSI 颜色）。
- 终端历史只在内存里（每项目 200 条），重启即清空——命令行可能带 `-H` token，不能落进
  明文 SQLite。
- 工作流面板改的是节点参数，不是拓扑：边的走向固定（`prepare → agent ⇄ run_one → finalize`），
  没有拖拽连线的画布；节点上的运行统计是进程内的上一轮数字，不持久化。
- 引用标亮的状态放在 `AppState` 而不是编辑器组件里：从笔记点引用会卸载阅读器、重建编辑器，
  组件内的 ref 会归零，「再点一次取消」就永远失效了。
- 行号点选是累积的，没有「单击只选这一行」的快捷方式；想从头开始选就点「清除」或直接在
  文本区拖选（拖选会接管、丢弃已挑的行）。
- 知识库的文件夹是平铺的树（按 `parent_id` 递归），没有拖拽排序；移动笔记走阅读器里的
  下拉选择器，或在文件夹视图里点「移出文件夹」。
- 索引是全项目粒度的：文件夹视图只影响笔记归档，不会为某个文件夹单独建索引。
- 一个会话只有一条笔记：单条回答生成与整段对话生成会覆盖同一条记录，方便随时刷新结论，
  但没法为同一会话保留多份不同粒度的笔记。
- 索引进度 900ms 轮询；进程重启后不会续接未完成的索引任务（需重新点「重建索引」）。
  （增量刷新失败的静默重试同样只在用按钮触发时才提示。）
- 「停止生成」只中断前端的 SSE 读取，服务端当前这一轮模型调用不会被取消。
- 设置界面可覆盖写入 API Key，但暂时没有单独的「清除 Key」入口（需要时可用
  `DELETE /api/settings/model/key?slot=chat`）。
- 关闭窗口前没有二次确认，正在进行的流式回答会被直接打断。
- 没有配置 Embedding 模型时，混合检索自动退回「关键词 + FTS5」模式，
  语义召回不可用（UI 会提示「未配置向量模型，检索将退回关键词模式」）。
- 向量模型下载依赖本机 Ollama；没有 Ollama 时只能用「高级：手动配置」走云端 API。
  下载进度条只反映 Ollama 的报告，断网后重试需重新点击下载（Ollama 自身会续传已下
  好的层）。
- 第一版用 Tree-sitter 做结构切块，不含语言服务器（LSP）的跨文件符号解析；
  未内置语法的语言退化为按行分块，检索仍可用。

**后续（V1 之外）**

| 方向 | 说明 |
| --- | --- |
| 三维知识图谱 | 把模块依赖、调用关系、对话脉络可视化成可导航的三维图 |
| 热力图 | 按修改频率 / 对话关注度 / 复杂度给文件与函数着色 |
| MCP 文档导入 | 通过 MCP 接入外部文档源（Confluence、Notion 等），与代码一起检索 |
| 多语言 LSP | 用语言服务器替代/补充 Tree-sitter，获得跨文件符号解析 |
| 全文重构智能体 | 跨文件、多步、可回滚的批量改动 |
| 多用户 / 云端同步 | V1 明确不做——数据留在本机是这个产品的核心承诺 |

---

## 常见问题

**Q：提示 `401 {"detail": "invalid session token"}` 怎么办？**

Token 每次启动都会变。开发时固定它：

```powershell
$env:CODESLICE_TOKEN = "dev-token"
uv run fastapi dev app/main.py
```

然后在前端界面里粘贴同一个 `dev-token`，或写进 `frontend/.env.local` 的
`VITE_SESSION_TOKEN`。用 `scripts/dev.ps1` 启动则自动处理。

**Q：Windows 提示「已保护你的电脑」/ 杀毒软件报毒？**

PyInstaller 产出的 exe 没有代码签名，属于典型误报。点「更多信息 → 仍要运行」，
或把安装目录加入白名单。正式分发需要购买代码签名证书。

**Q：为什么某些语言没有结构化大纲？**

当前内置 Tree-sitter 语法：Python、JavaScript、TypeScript/TSX、Go、Rust、Java、
C、C++、JSON、Bash。其余语言（Markdown、YAML、CSS 等）会退化为纯文本分块——
检索仍然可用，只是没有函数/类级别的切块。

可用自检确认语法库是否完整：

```powershell
packaging\dist-backend\codeslice-backend.exe --selftest
```

**Q：模型连不上 / 报 404？**

先在「设置 → 模型」里点「测试连接」。注意：
- Base URL 要写到版本前缀（如 `https://api.openai.com/v1`），不要带 `/chat/completions`；
- 协议必须与端点匹配（OpenAI 兼容 vs Anthropic），CodeSlice 不会自动猜；
- 部分代理服务不支持 `stream=true`，可先关掉流式再试。

**Q：索引很慢 / 一直卡在「索引中」？**

大仓库首次索引需要时间。默认跳过 `.gitignore` 与内置忽略列表中的目录，
单文件上限 2 MB。若某文件解析失败，会记录为 `error` 状态而不中断整体索引。
可在项目设置里排除特定目录后重新索引。

**Q：数据存在哪？卸载会删吗？**

安装版在 `%APPDATA%\codeslice-desktop\`，开发版在 `%LOCALAPPDATA%\CodeSlice\`
（详见[数据与凭据](#数据与凭据)）。卸载程序默认保留这些目录
（`deleteAppDataOnUninstall: false`，避免误删笔记与对话）；
需要彻底清理时手动删除即可。API Key 在 Windows 凭据管理器中，需单独删除。

**Q：能同时打开多个项目吗？**

可以，项目列表支持多个仓库。但同一时刻的对话绑定一个项目（`mode` 为
`snippet` 或 `project`），避免检索范围含混。

**Q：改了 `CODESLICE_PORT` 但 `fastapi dev` 还是起在 8000？**

这是 fastapi-cli 的行为，不是配置没生效：`fastapi dev` 定义了**自己的** `--port`
选项，并直接对 `app.main:app` 跑 uvicorn，所以 `app/main.py` 里的 `main()`（也就
是读取 `CODESLICE_PORT` 的那段）根本不会执行。开发时显式传参：

```powershell
uv run fastapi dev app/main.py --port 8123
```

`CODESLICE_PORT` 只对**打包后的** `codeslice-backend.exe` 生效（安装版默认
`CODESLICE_PORT=0`，由内核分配端口）。`scripts\dev.ps1` 已经处理好了这一点。

**Q：安装后 `CodeSlice.exe` 是 Electron 的默认图标？**

说明打包时 rcedit 那一步没生效。正常的打包输出里应该有
`[ok] icon verified: ...` 与 `[ok] version info: CodeSlice 0.1.0.0`。
原因是 `desktop/electron-builder.yml` 里 `signAndEditExecutable: false`（本地不签名），
electron-builder 因此跳过写图标/版本；而打开它的替代路径需要解压含符号链接的
winCodeSign 包，非管理员做不到。`packaging\build_desktop.ps1` 改为
「`--dir` → 直接调缓存里的 `rcedit-x64.exe` → `--prepackaged`」三步绕开该限制，
细节见 [packaging/README.md](packaging/README.md)。

**Q：安装包里是旧版界面？**

electron-builder 不会清理 `extraResources` 的旧文件，增量构建可能让
`resources\frontend\index.html` 仍旧指向上一版的 `index-*.js`。
`build_desktop.ps1` 会比对包内资源名与 `desktop\dist\index.html`，
不一致直接失败。遇到时执行 `pwsh -File packaging\build_desktop.ps1 -Clean`。

**Q：`uv sync` 之后打包报 `No module named 'PyInstaller'`？**

`uv sync` 会把 `backend\.venv` 严格对齐 `pyproject.toml`，而 PyInstaller 只在
`packaging\requirements-packaging.txt` 里，于是被卸掉。重跑
`pwsh -File packaging\build_backend.ps1`（**不加** `-SkipInstall`）即可。

---

## 许可

未定。此仓库处于早期开发阶段。
