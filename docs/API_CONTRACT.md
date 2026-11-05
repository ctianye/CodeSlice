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

### GET /api/projects/{project_id}/git/status
→ `{"is_repo": bool, "branch": str | null, "head": str | null, "dirty": bool,
    "staged": [str], "modified": [str], "untracked": [str]}`（非 git 仓库给空值）

### GET /api/projects/{project_id}/git/diff?path=
→ `{"path": str | null, "diff": str}`（`git diff` + `--stat` 文本，上限 200KB）

## 3. 索引 / 检索

### POST /api/projects/{project_id}/index
→ `{"job_id": int}`（后台线程执行，立即返回）

### GET /api/jobs/{job_id}
→ `{"id": int, "kind": str, "status": "pending|running|done|error", "progress": int,
    "message": str, "stats": {"files": int, "chunks": int, "vectors": int} | null, "error": str | null}`

### GET /api/projects/{project_id}/index
→ `{"project": Project, "last_job": Job | null, "files": int, "indexed_at": str | null,
    "embed_models": [str], "active_embed_model": str | null, "embedding_configured": bool}`
`embed_models` 是当前索引里出现过的向量模型名（去重）；`active_embed_model` 是**已配置**的模型；
`embedding_configured` 为 `false` 时检索只走关键词。前端用 `embed_models` 里出现与
`active_embed_model` 不同的值来判断「切换模型后需要重建索引」。

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
用这个端点列出某个文件上已有的对话；每条额外带 `has_note: bool` 与 `note_id: int | null`
（该会话生成的笔记 id，卡片据此直接跳到笔记）。

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

### GET /api/conversations/{conversation_id}
→ `{"conversation": Conversation, "messages": [Message], "pending_interrupt": Interrupt | null}`
`Message` = `{"id": str, "role": "user"|"assistant"|"tool", "content": str,
"created_at": str, "citations": [Citation] | null, "meta": object | null}`
`Citation` = `{"path": str, "start_line": int, "end_line": int, "symbol": str | null}`
`Interrupt` = `{"id": str, "kind": "write_file"|"run_command",
"payload": {"path"?: str, "diff"?: str, "command"?: str, "cwd"?: str,
"reason": str, "tool_call_id": str}}`

### POST /api/conversations/{conversation_id}/messages  （SSE, `text/event-stream`）
body `{"content": str}`
逐条 `data: <AgentEvent JSON>\n\n`，事件 `type`：
- `token` `{delta}`            —— 模型增量文本
- `status` `{stage, detail?}`  —— `classify|retrieve|plan|execute|verify|summarize`
- `citation` `{path,start_line,end_line,symbol}` —— 引用的代码位置
- `tool_call` `{name, args, call_id}`
- `tool_result` `{call_id, ok, summary}`
- `approval_required` `{id, kind, payload}` —— 需要用户授权，流随后结束
- `message` `{message: Message}` —— 本回合最终落库的助手消息
- `error` `{detail}`
- `done` `{}`

### POST /api/conversations/{conversation_id}/resume
body `{"interrupt_id": str, "decision": "approve"|"deny"|"approve_session"|"approve_always",
"remember_pattern": str | null, "note": str | null}`
同样返回 **SSE** 流（事件同上）。

### DELETE /api/conversations/{conversation_id}
→ `{"ok": true}`

## 6. 笔记 / 代码锚点 / 命令规则

### GET/POST /api/notes  （POST body `{"project_id", "title", "content_md", "conversation_id"?}`）
`Note` = `{"id": int, "project_id": int, "conversation_id": str | null, "title": str,
"content_md": str, "created_at": str, "updated_at": str, "indexed": bool}`
`indexed` 表示这条笔记当前是否在检索索引里（写入/编辑时同步，删除时同步移除）。

### PUT/DELETE /api/notes/{note_id}
PUT body `{"title"?, "content_md"?}` → `Note`（同步刷新其检索片段）。
DELETE → `{"ok": true}`（同时删除对应检索片段与 FTS 行）。

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

## 7. 事件/错误约定

- SSE 每行 `data: {...}`，`\n\n` 结尾；结束后服务端主动关流。
- 模型未配置时 `POST messages` 返回 400 `{"detail": "model not configured"}`（HTTP 层，不进 SSE）。
- 所有时间 ISO8601 UTC（`2026-11-05T10:20:30Z`）。
