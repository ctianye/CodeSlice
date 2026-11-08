# API Contract（V1，前后端/模块共用）

所有接口前缀 `/api`。除 `GET /api/health` 外，所有请求必须带请求头：

```
X-Session-Token: <token>
```

缺失/错误返回 `401 {"detail": "invalid session token"}`。
错误统一 `{"detail": "<message>"}`；校验错误用 FastAPI 默认 422 结构。

## 1. 系统

### GET /api/health
→ `{"status": "ok", "version": "0.1.0", "data_dir": "C:\\Users\\x\\.codeslice"}`

## 2. 项目 / 文件

### POST /api/projects
body `{"path": "D:\\repo", "name": "repo"}`（name 可省略，取目录名）
→ `Project`

### GET /api/projects
→ `[Project]`

### GET /api/projects/{project_id}
→ `Project`

### DELETE /api/projects/{project_id}
→ `{"ok": true}`（只删记录与索引，不动磁盘）

`Project` = `{"id": int, "name": str, "path": str, "created_at": str, "last_opened_at": str | null, "indexed_at": str | null}`

### GET /api/projects/{project_id}/tree?path=&depth=2
相对路径（`""` 为根），返回 `[TreeNode]`：
`TreeNode` = `{"name": str, "path": str, "is_dir": bool, "children": [TreeNode] | null}`
忽略 `.git/node_modules/dist/build/.venv/__pycache__/.idea/.vscode` 等；单层最多 500 项。

### GET /api/projects/{project_id}/file?path=src/main.py
→ `{"path": str, "language": str, "content": str, "size": int, "mtime": float, "truncated": bool}`
路径必须落在项目根内，越界 400。>2MB 时 `truncated=true` 只给前 2MB。

### PUT /api/projects/{project_id}/file
body `{"path": str, "content": str, "expected_mtime": float | null}`。
把整份 `content` 以 UTF-8 写回 `path`，响应与 `GET /file` 同构
（`{"path", "language", "content", "size", "mtime", "truncated": false}`）。
目标必须已存在：不存在 404 `file not found: <path>`，是目录 400 `not a file: <path>`，
超过 `max_file_bytes` 400 `file too large: <N> bytes`。`expected_mtime` 给值时会与磁盘
当前 mtime 比对（容差 0.001 秒），不一致 400 `file changed on disk since it was opened`
——这条是为了不静默覆盖别人刚改过的文件，前端据此提示「重新打开再编辑」。
校验全部通过才落盘，失败时磁盘保持原样。写文件同样受 `resolve_in_project` 约束，越界 400。

### GET /api/projects/{project_id}/asset?path=docs/screenshots/welcome.png&token=<会话令牌>
返回图片原始字节，`Content-Type` 由扩展名推断，`Cache-Control: no-cache`。
**鉴权走 query 参数 `token`**（不是 `X-Session-Token` 头），因为 `<img src>` 无法带自定义头；
令牌不对 401 `invalid token`。扩展名白名单 `.png .jpg .jpeg .gif .webp .bmp .ico .svg`，
其余 400 `unsupported asset type: <后缀>`；文件不存在 404；上限 8MB。
桌面前端用它渲染 Markdown 里的相对路径图片（CSP 的 `img-src` 已放行 `http://127.0.0.1:*`）。

### GET /api/projects/{project_id}/git/status
→ `{"is_repo": bool, "branch": str | null, "head": str | null, "dirty": bool,
    "staged": [str], "modified": [str], "untracked": [str]}`（非 git 仓库给空值）

### GET /api/projects/{project_id}/git/diff?path=
→ `{"path": str | null, "diff": str}`（`git diff` + `--stat` 文本，上限 200KB）

## 3. 索引 / 检索

### POST /api/projects/{project_id}/index
→ `{"job_id": int}`（兼容性接口，后台线程执行，立即返回）

正常流程由 `POST /api/projects/{project_id}/open` 在后端启动增量索引，并持续监听项目文件变化；
embedding 配置变更后也会为已登记项目自动排队重建。renderer 只读取任务状态，不负责创建或重建索引。

### GET /api/jobs/{job_id}
→ `{"id": int, "kind": str, "status": "pending|running|done|error", "progress": int,
    "message": str, "stats": {"files": int, "chunks": int, "vectors": int} | null, "error": str | null}`

### GET /api/projects/{project_id}/index
→ `{"project": Project, "last_job": Job | null, "files": int, "chunks": int, "vectors": int,
    "indexed_at": str | null, "embed_models": [str], "active_embed_model": str | null,
    "embedding_configured": bool}`
`files` / `chunks` / `vectors` 是**当前索引里的累计值**（不是最近一次任务增量）。`vectors` 只统计
当前配置模型的向量；切换模型后旧向量不会参与检索，后台更新完成前继续用关键词结果兜底。未配置
embedding 时 `vectors` 为 0，检索只走关键词，这是正常状态。
`embed_models` 是当前索引里出现过的向量模型名（去重）；`active_embed_model` 是**已配置**的模型；
`embedding_configured` 为 `false` 时检索只走关键词。embedding 模型切换后的向量补建由后端自动完成。

### GET /api/projects/{project_id}/search?q=&top_k=8
→ `[RetrievedChunk]`
`RetrievedChunk` = `{"path": str, "start_line": int, "end_line": int, "symbol": str | null,
"kind": str, "content": str, "score": float, "retriever": "keyword|vector|hybrid"}`
笔记也在这条管道里：`kind="note"` 时 `path` 为 `note://<note_id>`、行号为 0，
前端据此跳到笔记面板而不是编辑器。

## 4. 设置 / 模型

### GET /api/settings/model
→ `{"chat": ModelConfig, "embedding": EmbeddingConfig | null}`
`ModelConfig` = `{"protocol": "openai-compatible"|"anthropic", "model": str,
"base_url": str | null, "temperature": float, "api_key_configured": bool}`（永不返回明文 Key）

### PUT /api/settings/model
body `{"chat": {... 可选 "api_key": str}, "embedding": {...|null}}`
→ 同 GET。`api_key` 提供时写入系统凭据库（keyring，服务名 `codeslice`）。

### POST /api/settings/model/test
→ `{"ok": bool, "latency_ms": int | null, "detail": str}`

### GET /api/settings/ollama/status
探测本机 Ollama（默认 `http://127.0.0.1:11434`，可用环境变量 `CODESLICE_OLLAMA_URL` 覆盖）。
**总是 200**，Ollama 不在时 `installed=false` 且 `error` 有值。
→ `{"installed": bool, "version": str | null, "models": [str],
    "base_url": str, "openai_base_url": str, "presets": [OllamaPreset], "error": str | null}`
`OllamaPreset` = `{"id": str, "model": str, "label": str, "size": str, "size_bytes": int,
"context": str, "params": str, "description": str, "requirement": str}`
目前固定两个预设：`bge-m3`、`qwen3-embedding:0.6b`。

### POST /api/settings/ollama/pull  （SSE, `text/event-stream`）
body `{"model": "bge-m3" | "qwen3-embedding:0.6b"}`（**只接受预设名单**，其他值 400
`{"detail": "unsupported model: ..."}`，不会开流）
逐条 `data: <event JSON>\n\n`：
- `progress` `{model, status, detail, total, completed, percent}` —— `percent` 0~100
- `error` `{model, detail}`
- `done` `{model}`
下载完成后服务端**不写任何配置**；由前端用户确认后再调 `PUT /api/settings/model`
写 `embedding: {"model", "base_url": "<openai_base_url>", "api_key": "ollama"}`。

## 5. 对话 / Agent（SSE）

### POST /api/conversations
body `{"project_id": int, "title": str | null, "file_path": str | null,
"start_line": int | null, "end_line": int | null, "selected_code": str | null, "mode": "snippet"|"project"}`
→ `Conversation` = `{"id": str, "project_id": int, "title": str, "mode": str,
"file_path": str | null, "start_line": int | null, "end_line": int | null,
"created_at": str, "updated_at": str}`
`mode="snippet"` 表示“片段对话”，检索只围绕该文件/选区；`project` 表示项目问答。

### GET /api/conversations?project_id=&include_others=false
→ `[Conversation]`
`include_others=true` 时先返回 `project_id` 本项目的会话，再追加其他项目的会话（各自按
`updated_at` 倒序），供侧栏「其他项目」分组使用；不传 `project_id` 时返回全部。每条会话
额外带 `project_name: str | null` 与 `has_note: bool`（该会话是否已生成笔记）。

### GET /api/projects/{project_id}/conversations/by-file?path=
→ `[Conversation]`（绑定到该文件的会话，按起始行升序）
`path` 是项目内相对路径，越界（如 `../x`）返回 400，文件不存在返回 404。编辑器里的代码卡片
用这个端点列出某个文件上已有的对话；每条额外带 `has_note: bool`、`note_id: int | null`
（该会话生成的笔记 id，卡片据此直接跳到笔记）与 `note_title: str | null`（笔记标题；代码卡片
和编辑器里漂浮的知识点标签优先显示它，因为会话标题描述的是选区而不是知识点）。

### POST /api/conversations/{conversation_id}/messages/{message_id}/note
把一条**助手**消息整理成保存在该项目下的阅读笔记（同会话已有笔记时覆盖更新）。
→ `{"id", "project_id", "conversation_id", "title", "content_md", "created_at",
"updated_at", "summary_failed": bool, "summary_error": str | null, "refs": int,
"indexed": bool}`；消息不存在 404、`role != assistant` 400、消息为空 400。
同步返回（非 SSE）：只做一次短模型调用，卡片拿到结果后直接切成「笔记」状态。
笔记正文是固定模板：`# 标题` / `## 知识点` / `## 关联代码` / `## 要点` / `## 何时使用`，
`## 关联代码` 由该消息的 citations 从磁盘读取原文渲染。
**模型失败不再报 400**：仍然保存降级正文（原始回答 + 来源位置），置 `summary_failed=true`
并在 `summary_error` 写明原因。`refs` 是关联代码段数，`indexed` 表示是否已写入检索索引。
**标题描述知识点，不复用会话标题**：模型在 `## 标题` 一节给出独立于来源对话的 8–22 字知识点概括，
`_clean_title` 会剔除 `foo.py:12-30`、`第 N-M 行` 这类位置、markdown 标记与「关于」开头；
清洗后为空则退回知识点首句，再退回 `<项目名> · 阅读笔记`。

### POST /api/conversations/{conversation_id}/note
把**整段会话**（所有 user/assistant 轮次）整理成一条笔记，返回体同上。
长会话常跨多个文件，逐条回答生成笔记会丢掉主线，所以整段会话用一次模型调用折叠；
`## 关联代码` 收集会话里出现过的全部引用（去重、按首次出现排序，上限 4 段）。
该会话已有笔记时原地更新，因此单条生成与整段生成共用同一个 note 与同一个检索片段。
会话没有任何内容时返回 400。

### GET /api/conversations/{conversation_id}
→ `{"conversation": Conversation, "messages": [Message], "pending_interrupt": Interrupt | null}`
`Message` = `{"id": str, "role": "user"|"assistant"|"tool", "content": str,
"created_at": str, "citations": [Citation] | null, "meta": object | null}`
`Citation` = `{"path": str, "start_line": int, "end_line": int, "symbol": str | null}`
`Interrupt` = `{"id": str, "kind": "write_file"|"run_command",
"payload": {"path"?: str, "diff"?: str, "command"?: str, "cwd"?: str,
"reason": str, "tool_call_id": str}}`

### POST /api/conversations/{conversation_id}/messages  （SSE, `text/event-stream`）
body `{"content": str, "use_context": bool}`
`use_context` 只在片段对话里有意义，默认 `false`：此时模型只看得到选中的那段代码，
prompt 里明确禁止主动去别处读文件，也不会返回 citation。用户在输入框打开「联系上下文」
后传 `true`，才会追加同文件内的相关片段并允许检索。
逐条 `data: <AgentEvent JSON>\n\n`，事件 `type`：
- `token` `{delta}`            —— 模型增量文本
- `status` `{stage, detail?}`  —— `classify|retrieve|plan|execute|verify|summarize|title`
  （`title` 是本回合结束后把占位标题改成知识点标题时发的一次状态，`detail` 就是新标题）
- `citation` `{path,start_line,end_line,symbol}` —— 引用的代码位置
- `tool_call` `{name, args, call_id}`
- `tool_result` `{call_id, ok, summary}`
- `approval_required` `{id, kind, payload}` —— 需要用户授权，流随后结束
- `message` `{message: Message}` —— 本回合最终落库的助手消息
- `error` `{detail}`
- `done` `{}`

占位标题（`新对话` / `项目问答` / `foo.py:12-30` / `<项目> · 阅读笔记`）会在 `finalize` 里被
替换成根据回答整理的知识点标题，并 emit `status.stage="title"`；用户手动改过的标题不会被覆盖。

### POST /api/conversations/{conversation_id}/resume
body `{"interrupt_id": str, "decision": "approve"|"deny"|"approve_session"|"approve_always",
"remember_pattern": str | null, "note": str | null}`
同样返回 **SSE** 流（事件同上）。

### DELETE /api/conversations/{conversation_id}
→ `{"ok": true}`

## 6. 笔记 / 代码锚点 / 命令规则

### GET /api/notes?project_id=&folder_id=&unfiled=
→ `[Note]`
不传 `project_id` 时返回**所有项目**的笔记（知识库是跨项目的入口），每条带
`project_name: str | null` 供列表标注来源；传了则只返回该项目。`folder_id` 限定某个文件夹，
`unfiled=true` 只给未归档的（两者互斥，`unfiled` 优先）。
`Note` = `{"id": int, "project_id": int, "project_name": str | null, "folder_id": int | null,
"conversation_id": str | null, "title": str, "content_md": str, "created_at": str,
"updated_at": str, "indexed": bool}`
`indexed` 表示这条笔记当前是否在检索索引里（写入/编辑时同步，删除时同步移除）。
笔记仍然归属它被创建时的项目（检索、导出都按项目走），但**阅读和归档不受项目限制**。

### POST /api/notes
body `{"project_id", "title", "content_md", "conversation_id"?, "folder_id"?}` → `Note`

### PUT/DELETE /api/notes/{note_id}
PUT body `{"title"?, "content_md"?, "folder_id"?, "move_folder"?}` → `Note`（同步刷新其检索片段）。
`folder_id` 只有配合 `move_folder=true` 才会生效——否则 `null` 与「不改」无法区分，
移出文件夹就永远表达不出来。DELETE → `{"ok": true}`（同时删除对应检索片段与 FTS 行）。

### GET /api/note-folders → `[NoteFolder]`
`NoteFolder` = `{"id": int, "parent_id": int | null, "name": str, "created_at": str,
"note_count": int}`（`note_count` 只统计直接归在该文件夹下的笔记，不含子文件夹）

### POST /api/note-folders   body `{"name", "parent_id"?}` → `NoteFolder`
### PUT /api/note-folders/{folder_id}   body `{"name"?, "parent_id"?, "move"?}` → `NoteFolder`
`parent_id` 同样需要 `move=true` 才生效；不存在返回 404。
### DELETE /api/note-folders/{folder_id} → `{"ok": true}`
删除文件夹**不删笔记**：其中的笔记落回「未分类」（`folder_id` 置空），子文件夹级联删除。

### GET /api/notes/{note_id}
→ `Note`（带当前 `indexed` 状态）；笔记不存在 404。
笔记阅读器靠它按 id 取整篇正文，不需要先拉列表。

### POST /api/notes/{note_id}/retitle
让模型读完正文重新起一个知识点标题，用于修正早期生成的、标题里带着文件名和行号的旧笔记。
→ `Note & {"retitle_failed": bool}`；成功时写库并刷新检索片段，`retitle_failed=false`。
**模型失败不报 500**：原样返回旧笔记并置 `retitle_failed=true`，避免用户点一下就把标题弄丢。
笔记不存在 404。

### POST /api/notes/{note_id}/export
把笔记写成项目 `.md` 文件，目录为 `<data_dir>/notes/<项目名>/`，重名自动加时间戳。
→ `{"path": str, "directory": str, "filename": str, "bytes": int}`；笔记不存在 404。

### GET /api/projects/{project_id}/anchors
`Anchor` = `{"id": int, "conversation_id": str, "file_path": str, "commit_sha": str | null,
"symbol": str | null, "start_line": int, "end_line": int, "snippet": str | null,
"code_hash": str | null, "status": "unchanged|modified|moved|deleted", "checked_at": str | null}`

### POST /api/projects/{project_id}/anchors/refresh
→ `{"updated": int, "deleted": int, "moved": int}`（重新计算状态；deleted 时把快照保留在 snippet 上）

### GET/POST/PUT/DELETE /api/projects/{project_id}/command-rules
`CommandRule` = `{"id": int, "project_id": int, "pattern": str,
"scope": "once"|"session"|"always", "needs_confirm": bool, "created_at": str}`

## 7. 终端 / 沙箱（SSE）

终端与 Agent 的 `run_command` 共用同一套命令策略（`app/services/sandbox.py` 的
`BLOCKED_FRAGMENTS`），所以用户在面板里能跑的命令和 AI 能跑的命令永远一致。

### POST /api/projects/{project_id}/terminal/run  （SSE, `text/event-stream`）
body `{"command": str, "cwd"?: str}`；`cwd` 相对项目根，越界 / 不存在直接报错。
帧序列：

```
data: {"type": "start", "command": str, "cwd": str}
data: {"type": "output", "stream": "stdout"|"stderr", "text": str}   # 逐行，可能很多帧
data: {"type": "exit", "code": int, "duration_ms": int, "truncated": bool}
data: {"type": "done"}
```

被拒绝或失败时是 `start → error → done`（**没有** `exit` 帧，HTTP 仍是 200）：

```
data: {"type": "error", "detail": "已拒绝执行：该命令命中内置的危险命令黑名单。"}
```

- 真流式：`Popen(shell=True)` + 两个读线程 + `queue`，子进程吐一行就推一帧
  （`ping` 实测每秒一帧），不是攒到进程结束后一次性返回。
- 输出总量上限 200 KB，超出置 `truncated: true` 并停止读取；命令默认超时 300 秒，超时 kill 并推 `error`。
- **不是 PTY**：`cd` / `vim` 这类跨命令状态和全屏 TUI 不工作，命令之间互相独立。

### GET /api/projects/{project_id}/terminal/history?limit=50
`limit` 走 `Query(ge=1, le=200)`，越界 422。→ `{"items": [TerminalHistoryItem]}`，**最新在前**。
`TerminalHistoryItem` = `{"id": str, "command": str, "cwd": str, "source": "user"|"agent",
"exit_code": int, "started_at": str, "duration_ms": int, "output_tail": str}`
`cwd` 是相对项目根的标签（根目录为 `"."`）；`output_tail` 只保留最后 6 行、最多 2000 字符。
历史在**内存环形缓冲**（每项目 200 条），进程重启即清空——命令行可能带 `-H` token，不能进明文 SQLite。

### POST /api/projects/{project_id}/terminal/cancel
→ `{"ok": true, "cancelled": int}`（该项目的活动子进程数，空闲时 0）。

## 8. Agent 工作流节点配置（仅后端接口）

图拓扑是**固定的**（`prepare → agent ⇄ run_one → finalize`），可改的是每个节点的行为；
配置存在 `settings` 表的 `agent.node_config` 一个 JSON 键下，每一轮节点运行时才读取，
所以保存后**下一轮对话立即生效，不需要重启**。

这一节只有 HTTP 接口，桌面前端**没有**对应的流程图界面：想改节点参数直接调下面两条路由
（`curl` / 脚本 / 未来的设置页都行）。

### GET /api/agent/graph
```json
{
  "nodes": [{
    "id": "agent",
    "label": "模型推理",
    "kind": "input|llm|tool|output",
    "description": str,
    "editable": {"<key>": {"type": "int|float|bool|string|text", "min"?: num, "max"?: num}},
    "defaults": {"<key>": any},
    "config": {"<key>": any},
    "stats": {"last_tokens": int, "last_duration_ms": int, "last_chunks": int, ...}
  }],
  "edges": [{"from": str, "to": str, "label"?: str, "conditional"?: bool}]
}
```
节点顺序即执行顺序：`prepare`(准备上下文) / `agent`(模型推理) / `run_one`(执行工具) / `finalize`(收尾)。
`editable` 就是表单 schema，前端按它渲染字段，后端加一个开关不需要改前端。
`stats` 是**进程内**的上一轮数字，不持久化。

### PUT /api/agent/graph/nodes/{node_id}
body `{"config": {<部分字段>}}` → 单节点（形状同上）。只提交想改的键，其余保持已存值。
- 未知节点 → 404 `{"detail": "unknown node: xxx"}`
- 未知键 → 400 `{"detail": "unknown config key for agent: xxx"}`
- 越界 → 400 `{"detail": "agent.temperature must be <= 2"}`
- 类型不符 → 400（`bool` 不被当成数字，`"true"` 不会被当成 `true`）

各节点可改项：

| 节点 | 键 | 说明 |
|---|---|---|
| prepare | `retrieval_top_k` (1..20) | 检索片段数，默认 6 |
| prepare | `include_git_branch` (bool) | 是否把当前分支写进 system prompt |
| agent | `model` (str) | 留空沿用设置里的模型；填写则覆盖本节点使用的模型名 |
| agent | `temperature` (0..2) / `max_tokens` (1024..262144) | 覆盖设置里的采样参数 |
| agent | `extra_prompt` (text) | 追加在 system prompt 末尾的额外要求 |
| agent | `max_tool_rounds` (1..30) | 工具调用轮数上限 |
| run_one | `max_tool_rounds` / `auto_approve_readonly_tools` | 只读工具是否免确认（当前只读工具本就不打断） |
| finalize | `auto_retitle` (bool) | 是否把占位标题改成知识点标题 |

## 9. 事件/错误约定

- SSE 每行 `data: {...}`，`\n\n` 结尾；结束后服务端主动关流。
- 模型未配置时 `POST messages` 返回 400 `{"detail": "model not configured"}`（HTTP 层，不进 SSE）。
- 所有时间 ISO8601 UTC（`2026-11-05T10:20:30Z`）。
