import {
  apiBase,
  getToken,
} from './bridge'
import type {
  AgentStreamEvent,
  CommandRule,
  CodeAnnotation,
  CommandRuleCreateBody,
  Conversation,
  ConversationDetail,
  CreateConversationBody,
  FileContent,
  GeneratedNote,
  GitDiff,
  GitStatus,
  GraphNodeDetail,
  GraphPayload,
  GraphSummaryEvent,
  GraphNode,
  Health,
  IndexStatus,
  Job,
  ModelSettings,
  ModelSettingsUpdate,
  ModelTestResult,
  Note,
  NoteCreateBody,
  NoteExport,
  NoteFolder,
  NoteUpdateBody,
  OllamaPullEvent,
  OllamaPullStatus,
  OllamaStatus,
  Project,
  ResumeBody,
  RetrievedChunk,
  TreeNode,
} from './types'

/** `{"detail": "..."}` per the contract; kept as a class for instanceof checks. */
export class ApiError extends Error {
  readonly status: number
  readonly detail: string

  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

/** FastAPI 422 bodies carry an array of validation errors instead of a string. */
function detailFromBody(body: unknown, fallback: string): string {
  if (body && typeof body === 'object') {
    const detail = (body as { detail?: unknown }).detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      const parts = detail.map((item) => {
        if (item && typeof item === 'object') {
          const loc = (item as { loc?: unknown }).loc
          const msg = (item as { msg?: unknown }).msg
          const where = Array.isArray(loc) ? loc.join('.') : ''
          return where ? `${where}: ${String(msg ?? '')}` : String(msg ?? '')
        }
        return String(item)
      })
      return parts.filter(Boolean).join('; ') || fallback
    }
  }
  return fallback
}

async function parseError(res: Response): Promise<ApiError> {
  let body: unknown = null
  const text = await res.text().catch(() => '')
  if (text) {
    try {
      body = JSON.parse(text)
    } catch {
      body = null
    }
  }
  const fallback = text || `请求失败（HTTP ${res.status}）`
  return new ApiError(res.status, detailFromBody(body, fallback))
}

function headers(extra?: Record<string, string>): Record<string, string> {
  const h: Record<string, string> = { ...extra }
  const token = getToken()
  if (token) h['X-Session-Token'] = token
  return h
}

export interface RequestOptions {
  method?: string
  body?: unknown
  /** Disable JSON content-type (used for SSE POST bodies we still send as JSON). */
  signal?: AbortSignal
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, signal } = options
  const init: RequestInit = {
    method,
    headers: headers(body === undefined ? undefined : { 'Content-Type': 'application/json' }),
    signal,
  }
  if (body !== undefined) init.body = JSON.stringify(body)

  const res = await fetch(`${apiBase()}${path}`, init)
  if (!res.ok) throw await parseError(res)
  if (res.status === 204) return undefined as T
  const text = await res.text()
  if (!text) return undefined as T
  return JSON.parse(text) as T
}

/** GET /api/health — the only unauthenticated endpoint. */
export const getHealth = (signal?: AbortSignal): Promise<Health> => request<Health>('/api/health', { signal })

/* -------------------------------- projects --------------------------------- */

export const listProjects = (): Promise<Project[]> => request<Project[]>('/api/projects')

export const createProject = (body: { path: string; name?: string | null }): Promise<Project> =>
  request<Project>('/api/projects', { method: 'POST', body })

export const getProject = (projectId: number): Promise<Project> =>
  request<Project>(`/api/projects/${projectId}`)

export const openProject = (projectId: number): Promise<Project> =>
  request<Project>(`/api/projects/${projectId}/open`, { method: 'POST' })

export const deleteProject = (projectId: number): Promise<{ ok: boolean }> =>
  request<{ ok: boolean }>(`/api/projects/${projectId}`, { method: 'DELETE' })

export const getTree = (
  projectId: number,
  path = '',
  depth = 2,
  signal?: AbortSignal,
): Promise<TreeNode[]> => {
  const params = new URLSearchParams({ path, depth: String(depth) })
  return request<TreeNode[]>(`/api/projects/${projectId}/tree?${params.toString()}`, { signal })
}

export const getFile = (projectId: number, path: string, signal?: AbortSignal): Promise<FileContent> => {
  const params = new URLSearchParams({ path })
  return request<FileContent>(`/api/projects/${projectId}/file?${params.toString()}`, { signal })
}

/**
 * Write a text file back to disk.
 *
 * `expectedMtime` is the mtime that came back with the file when it was opened:
 * the server refuses the write when the file changed on disk in the meantime, so
 * an edit made against a stale buffer cannot silently overwrite someone else's
 * work. Returns the same shape as `getFile`, so the pane can adopt the new
 * mtime/size without another round trip.
 */
export const saveFile = (
  projectId: number,
  path: string,
  content: string,
  expectedMtime?: number | null,
): Promise<FileContent> =>
  request<FileContent>(`/api/projects/${projectId}/file`, {
    method: 'PUT',
    body: { path, content, expected_mtime: expectedMtime ?? null },
  })

/**
 * URL for a raw image referenced by a Markdown file.
 *
 * `<img src>` cannot carry the `X-Session-Token` header, so the asset endpoint
 * takes the token as a query parameter instead.
 */
export const assetUrl = (projectId: number, path: string): string => {
  const params = new URLSearchParams({ path })
  const token = getToken()
  if (token) params.set('token', token)
  return `${apiBase()}/api/projects/${projectId}/asset?${params.toString()}`
}

export const getGitStatus = (projectId: number): Promise<GitStatus> =>
  request<GitStatus>(`/api/projects/${projectId}/git/status`)

export const getGitDiff = (projectId: number, path?: string): Promise<GitDiff> => {
  const qs = path ? `?${new URLSearchParams({ path }).toString()}` : ''
  return request<GitDiff>(`/api/projects/${projectId}/git/diff${qs}`)
}

/* ---------------------------- indexing / search ----------------------------- */

export const startIndex = (projectId: number): Promise<{ job_id: number }> =>
  request<{ job_id: number }>(`/api/projects/${projectId}/index`, { method: 'POST' })

/** Index state for one project, including which embedding model built the vectors. */
export const getIndexStatus = (projectId: number, signal?: AbortSignal): Promise<IndexStatus> =>
  request<IndexStatus>(`/api/projects/${projectId}/index`, { signal })

export const getJob = (jobId: number, signal?: AbortSignal): Promise<Job> =>
  request<Job>(`/api/jobs/${jobId}`, { signal })

export const searchProject = (
  projectId: number,
  q: string,
  topK = 8,
  signal?: AbortSignal,
): Promise<RetrievedChunk[]> => {
  const params = new URLSearchParams({ q, top_k: String(topK) })
  return request<RetrievedChunk[]>(`/api/projects/${projectId}/search?${params.toString()}`, { signal })
}

/* ---------------------------------- graph ---------------------------------- */

/**
 * The project's code knowledge graph.
 *
 * The whole payload comes at once: the layout algorithm needs every node and
 * edge, and paginating a graph is far more work than it saves over localhost.
 */
export const getGraph = (projectId: number, signal?: AbortSignal): Promise<GraphPayload> =>
  request<GraphPayload>(`/api/projects/${projectId}/graph`, { signal })

export const getNotesGraph = (signal?: AbortSignal): Promise<GraphPayload> =>
  request<GraphPayload>('/api/graphs/notes', { signal })

/** Persist settled coordinates so the next visit can skip force-atlas entirely. */
export const saveGraphLayout = (
  scope: 'code' | 'notes',
  projectId: number,
  layoutKey: string,
  positions: Record<string, { x: number; y: number }>,
): Promise<{ ok: boolean }> =>
  request<{ ok: boolean }>(
    scope === 'code'
      ? `/api/projects/${projectId}/graph/layout`
      : '/api/graphs/notes/layout',
    { method: 'PUT', body: { layout_key: layoutKey, positions } },
  )

/**
 * Streams the AI relationship summary as SSE frames.
 *
 * The first delta should paint immediately: a hub with dozens of neighbours can
 * take a while to explain, and waiting for the whole JSON answer leaves the rail
 * blank. The generic POST-SSE helper owns framing and abort handling.
 */
export async function* streamGraphSummary(
  scope: 'code' | 'notes',
  projectId: number,
  nodeId: string,
  signal?: AbortSignal,
): AsyncGenerator<GraphSummaryEvent> {
  const path = scope === 'code'
    ? `/api/projects/${projectId}/graph/summary/stream`
    : '/api/graphs/notes/summary/stream'
  yield* postJsonSse<GraphSummaryEvent>(path, { node_id: nodeId }, signal)
}

export const getConversationGraphTargets = (id: string, signal?: AbortSignal):
  Promise<{ project_id: number; targets: GraphNode[] }> =>
  request(`/api/conversations/${encodeURIComponent(id)}/graph-targets`, { signal })

/** Force a rebuild, for when the user changed ignore rules or wants a fresh view. */
export const rebuildGraph = (
  projectId: number,
): Promise<{ nodes: number; edges: number; communities: number; rebuilt: boolean }> =>
  request<{ nodes: number; edges: number; communities: number; rebuilt: boolean }>(
    `/api/projects/${projectId}/graph/build`,
    { method: 'POST' },
  )

/** One node plus its immediate connections, for the click-through panel. */
export const getGraphNode = (
  projectId: number,
  nodeId: string,
  depth = 1,
  signal?: AbortSignal,
): Promise<GraphNodeDetail> => {
  const params = new URLSearchParams({ node_id: nodeId, depth: String(depth) })
  return request<GraphNodeDetail>(`/api/projects/${projectId}/graph/node?${params.toString()}`, { signal })
}

/** Resolve a code position to the graph node that encloses it. */
export const locateGraphNode = (
  projectId: number,
  path: string,
  line: number,
  signal?: AbortSignal,
): Promise<{ node: GraphNode | null }> => {
  const params = new URLSearchParams({ path, line: String(line) })
  return request<{ node: GraphNode | null }>(
    `/api/projects/${projectId}/graph/locate?${params.toString()}`,
    { signal },
  )
}

/* --------------------------------- settings -------------------------------- */
export const getModelSettings = (): Promise<ModelSettings> =>
  request<ModelSettings>('/api/settings/model')

export const updateModelSettings = (body: ModelSettingsUpdate): Promise<ModelSettings> =>
  request<ModelSettings>('/api/settings/model', { method: 'PUT', body })

export const testModel = (): Promise<ModelTestResult> =>
  request<ModelTestResult>('/api/settings/model/test', { method: 'POST' })

/**
 * Delete the stored API key for a slot. Not part of API_CONTRACT.md, but the
 * backend exposes it (backend/app/api/settings.py:53) and without it the UI has
 * no way to clear a wrong key once saved.
 */
export const clearModelKey = (slot: 'chat' | 'embedding' = 'chat'): Promise<{ ok: boolean }> =>
  request<{ ok: boolean }>(`/api/settings/model/key?slot=${slot}`, { method: 'DELETE' })

/* ---------------------------------- ollama --------------------------------- */

export const getOllamaStatus = (): Promise<OllamaStatus> => request<OllamaStatus>('/api/settings/ollama/status')

export const getOllamaPullStatus = (): Promise<OllamaPullStatus> =>
  request<OllamaPullStatus>('/api/settings/ollama/pull')

/**
 * Streams download progress for one preset model.
 *
 * Reuses `postSse` so the framing/abort behaviour matches chat streaming. The
 * backend rejects an unknown model with 400 *before* the stream opens; that
 * arrives as a single `error` event, not a thrown ApiError.
 */
export function pullOllamaModel(model: string, signal?: AbortSignal): AsyncGenerator<OllamaPullEvent> {
  return postJsonSse<OllamaPullEvent>('/api/settings/ollama/pull', { model }, signal)
}

/* ------------------------------ conversations ------------------------------- */

export const createConversation = (body: CreateConversationBody): Promise<Conversation> =>
  request<Conversation>('/api/conversations', { method: 'POST', body })

export const listConversations = (
  projectId: number,
  signal?: AbortSignal,
  options?: { includeOthers?: boolean },
): Promise<Conversation[]> => {
  const params = new URLSearchParams({ project_id: String(projectId) })
  if (options?.includeOthers) params.set('include_others', 'true')
  return request<Conversation[]>(`/api/conversations?${params.toString()}`, { signal })
}

/**
 * Conversations anchored to one file, ordered by starting line.
 *
 * Backs the code cards in the editor. The server resolves `path` under the
 * project root, so a path outside the project comes back as 400.
 */
export const listConversationsByFile = (
  projectId: number,
  path: string,
  signal?: AbortSignal,
): Promise<Conversation[]> =>
  request<Conversation[]>(
    `/api/projects/${projectId}/conversations/by-file?${new URLSearchParams({ path }).toString()}`,
    { signal },
  )

/** Summarises one assistant answer into a saved note; resolves with the note. */
export const createNoteFromMessage = (conversationId: string, messageId: string): Promise<GeneratedNote> =>
  request<GeneratedNote>(
    `/api/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/note`,
    { method: 'POST' },
  )

export const getConversation = (conversationId: string, signal?: AbortSignal): Promise<ConversationDetail> =>
  request<ConversationDetail>(`/api/conversations/${encodeURIComponent(conversationId)}`, { signal })

export const deleteConversation = (conversationId: string): Promise<{ ok: boolean }> =>
  request<{ ok: boolean }>(`/api/conversations/${encodeURIComponent(conversationId)}`, { method: 'DELETE' })

/* ----------------------------------- SSE ----------------------------------- */

/**
 * Opens an SSE POST stream and yields parsed `data: {...}` frames.
 *
 * Both `POST /messages` and `POST /resume` are JSON requests that answer with
 * `text/event-stream`, so EventSource cannot be used. The server closes the
 * stream after `done`; a nonzero HTTP status is reported as one `error` event
 * *before* any frame is yielded (e.g. 400 "model not configured"), and transport
 * errors surface the same way so the caller only has one failure channel.
 *
 * Generic over the event union so the Ollama pull stream can share the framing
 * and abort handling without pretending its events are agent events.
 */
export async function* postJsonSse<T extends { type: string }>(
  path: string,
  body: unknown,
  signal?: AbortSignal,
): AsyncGenerator<T> {
  let res: Response
  try {
    res = await fetch(`${apiBase()}${path}`, {
      method: 'POST',
      headers: headers({ 'Content-Type': 'application/json', Accept: 'text/event-stream' }),
      body: JSON.stringify(body),
      signal,
    })
  } catch (err) {
    if (signal?.aborted) return
    yield { type: 'error', detail: `无法连接本机服务：${(err as Error).message}` } as unknown as T
    return
  }

  if (!res.ok) {
    const apiErr = await parseError(res)
    yield { type: 'error', detail: apiErr.detail } as unknown as T
    return
  }
  if (!res.body) {
    yield { type: 'error', detail: '服务未返回数据流' } as unknown as T
    return
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      // Frames are separated by a blank line; tolerate \r\n from proxies.
      let sep = findFrameEnd(buffer)
      while (sep >= 0) {
        const frame = buffer.slice(0, sep)
        buffer = buffer.slice(sep + (buffer[sep] === '\r' ? 4 : 2))
        const events = parseFrame<T>(frame)
        for (const evt of events) yield evt
        sep = findFrameEnd(buffer)
      }
    }
    const tail = parseFrame<T>(buffer)
    for (const evt of tail) yield evt
  } catch (err) {
    if (!signal?.aborted) {
      yield { type: 'error', detail: `读取数据流失败：${(err as Error).message}` } as unknown as T
    }
  } finally {
    try {
      reader.releaseLock()
    } catch {
      /* already released */
    }
  }
}

/** Agent-event view of {@link postJsonSse}. */
export function postSse(path: string, body: unknown, signal?: AbortSignal): AsyncGenerator<AgentStreamEvent> {
  return postJsonSse<AgentStreamEvent>(path, body, signal)
}

function findFrameEnd(buffer: string): number {
  const lf = buffer.indexOf('\n\n')
  const crlf = buffer.indexOf('\r\n\r\n')
  if (lf < 0) return crlf
  if (crlf < 0) return lf
  return Math.min(lf, crlf)
}

function parseFrame<T extends { type: string }>(frame: string): T[] {
  const out: T[] = []
  for (const rawLine of frame.split(/\r?\n/)) {
    const line = rawLine.trim()
    if (!line || line.startsWith(':')) continue // comment/keep-alive
    if (!line.startsWith('data:')) continue
    const payload = line.slice(5).trim()
    if (!payload || payload === '[DONE]') continue
    try {
      const parsed = JSON.parse(payload) as T
      if (parsed && typeof parsed === 'object' && typeof (parsed as { type?: unknown }).type === 'string') {
        out.push(parsed)
      } else {
        out.push({ type: 'unknown', raw: parsed } as unknown as T)
      }
    } catch {
      out.push({ type: 'unknown', raw: payload } as unknown as T)
    }
  }
  return out
}

/**
 * Send a user message; yields token/status/citation/... events.
 *
 * `graphAnchor` names the graph node the question is about. The backend renders
 * that node's neighbourhood into the prompt, so the model can answer "who calls
 * this" from real edges instead of guessing at file contents.
 */
export function streamChat(
  conversationId: string,
  content: string,
  signal?: AbortSignal,
  options?: { useContext?: boolean; graphAnchor?: string | null },
): AsyncGenerator<AgentStreamEvent> {
  const body: Record<string, unknown> = { content }
  if (options?.useContext) body.use_context = true
  if (options?.graphAnchor) body.graph_anchor = options.graphAnchor
  return postSse(`/api/conversations/${encodeURIComponent(conversationId)}/messages`, body, signal)
}

/** Resolve an approval interrupt; yields the continuation stream. */
export function streamResume(
  conversationId: string,
  body: ResumeBody,
  signal?: AbortSignal,
): AsyncGenerator<AgentStreamEvent> {
  return postSse(`/api/conversations/${encodeURIComponent(conversationId)}/resume`, body, signal)
}

/* ----------------------------------- notes --------------------------------- */

export const listAnnotations = (projectId: number, filePath: string, signal?: AbortSignal): Promise<CodeAnnotation[]> =>
  request<CodeAnnotation[]>(`/api/projects/${projectId}/annotations?${new URLSearchParams({ file_path: filePath })}`, { signal })

export const createAnnotation = (projectId: number, body: Pick<CodeAnnotation, 'file_path' | 'start_line' | 'end_line' | 'selected_code' | 'title' | 'body'>): Promise<CodeAnnotation> =>
  request<CodeAnnotation>(`/api/projects/${projectId}/annotations`, { method: 'POST', body })

export const deleteAnnotation = (id: number): Promise<{ ok: boolean }> =>
  request<{ ok: boolean }>(`/api/annotations/${id}`, { method: 'DELETE' })

/**
 * Lists notes for the knowledge base.
 *
 * Without any argument every project's notes come back in one list: the
 * knowledge base is shared across projects, and each note carries
 * `project_name` so the panel can show where it came from. Passing `projectId`
 * narrows it to one project (the per-project sidebar view), `folderId` to one
 * folder of the user's tree, and `unfiled` to notes that are not in a folder.
 */
export const listNotes = async (
  options: { projectId?: number | null; folderId?: number | null; unfiled?: boolean } = {},
  signal?: AbortSignal,
): Promise<Note[]> => {
  const params = new URLSearchParams()
  if (options.projectId != null) params.set('project_id', String(options.projectId))
  if (options.folderId != null) params.set('folder_id', String(options.folderId))
  if (options.unfiled) params.set('unfiled', 'true')
  const query = params.toString()
  return request<Note[]>(`/api/notes${query ? `?${query}` : ''}`, { signal })
}

/* -------------------------------- note folders ----------------------------- */

export const listNoteFolders = (signal?: AbortSignal): Promise<NoteFolder[]> =>
  request<NoteFolder[]>('/api/note-folders', { signal })

export const createNoteFolder = (body: {
  name: string
  parent_id?: number | null
}): Promise<NoteFolder> => request<NoteFolder>('/api/note-folders', { method: 'POST', body })

export const renameNoteFolder = (
  folderId: number,
  body: { name?: string; parent_id?: number | null; move?: boolean },
): Promise<NoteFolder> => request<NoteFolder>(`/api/note-folders/${folderId}`, { method: 'PUT', body })

export const deleteNoteFolder = (folderId: number): Promise<{ ok: boolean }> =>
  request<{ ok: boolean }>(`/api/note-folders/${folderId}`, { method: 'DELETE' })

export const createNote = (body: NoteCreateBody): Promise<Note> =>
  request<Note>('/api/notes', { method: 'POST', body })

/** One note by id; used when the reader is opened straight onto a note. */
export const getNote = (noteId: number, signal?: AbortSignal): Promise<Note> =>
  request<Note>(`/api/notes/${noteId}`, { signal })

/**
 * Summarises a whole conversation (every user/assistant turn) into one note.
 *
 * Same response shape as the single-message variant, so the caller can reuse
 * its "summary failed" handling.
 */
export const createNoteFromConversation = (conversationId: string): Promise<GeneratedNote> =>
  request<GeneratedNote>(`/api/conversations/${encodeURIComponent(conversationId)}/note`, {
    method: 'POST',
  })

export const updateNote = (noteId: number, body: NoteUpdateBody): Promise<Note> =>
  request<Note>(`/api/notes/${noteId}`, { method: 'PUT', body })

export const deleteNote = (noteId: number): Promise<{ ok: boolean }> =>
  request<{ ok: boolean }>(`/api/notes/${noteId}`, { method: 'DELETE' })

/**
 * Asks the model for a knowledge-point title and stores it.
 *
 * Older notes were titled after their code range ("关于 README.md 第 357-359
 * 行"); this rewrites one into a title that describes what was learned. When the
 * model call fails the backend keeps the old title and flags `retitle_failed`
 * instead of returning an error, so a failure never loses the note.
 */
export const retitleNote = (noteId: number): Promise<Note & { retitle_failed?: boolean }> =>
  request<Note & { retitle_failed?: boolean }>(`/api/notes/${noteId}/retitle`, { method: 'POST' })

/** Writes the note to a `.md` file under the app data dir; resolves with its path. */
export const exportNote = (noteId: number): Promise<NoteExport> =>
  request<NoteExport>(`/api/notes/${noteId}/export`, { method: 'POST' })

/* ------------------------------ command rules ------------------------------ */

export const listCommandRules = (projectId: number, signal?: AbortSignal): Promise<CommandRule[]> =>
  request<CommandRule[]>(`/api/projects/${projectId}/command-rules`, { signal })

export const createCommandRule = (projectId: number, body: CommandRuleCreateBody): Promise<CommandRule> =>
  request<CommandRule>(`/api/projects/${projectId}/command-rules`, { method: 'POST', body })

export const updateCommandRule = (
  projectId: number,
  ruleId: number,
  body: Partial<CommandRuleCreateBody>,
): Promise<CommandRule> =>
  request<CommandRule>(`/api/projects/${projectId}/command-rules/${ruleId}`, { method: 'PUT', body })

export const deleteCommandRule = (projectId: number, ruleId: number): Promise<{ ok: boolean }> =>
  request<{ ok: boolean }>(`/api/projects/${projectId}/command-rules/${ruleId}`, { method: 'DELETE' })
