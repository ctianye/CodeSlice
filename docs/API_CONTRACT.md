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

### GET /api/projects/{project_id}/search?q=&top_k=8
→ `[RetrievedChunk]`
`RetrievedChunk` = `{"path": str, "start_line": int, "end_line": int, "symbol": str | null,
"kind": str, "content": str, "score": float, "retriever": "keyword|vector|hybrid"}`

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

## 5. 对话 / Agent（SSE）

### POST /api/conversations
body `{"project_id": int, "title": str | null, "file_path": str | null,
"start_line": int | null, "end_line": int | null, "selected_code": str | null, "mode": "snippet"|"project"}`
→ `Conversation` = `{"id": str, "project_id": int, "title": str, "mode": str,
"file_path": str | null, "start_line": int | null, "end_line": int | null,
"created_at": str, "updated_at": str}`
`mode="snippet"` 表示“片段对话”，检索只围绕该文件/选区；`project` 表示项目问答。

### GET /api/conversations?project_id=
→ `[Conversation]`

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
"content_md": str, "created_at": str, "updated_at": str}`

### PUT/DELETE /api/notes/{note_id}

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
