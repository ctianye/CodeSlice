import {
  apiBase,
  getToken,
} from './bridge'
import type {
  AgentStreamEvent,
  Anchor,
  AnchorRefreshResult,
  CommandRule,
  CommandRuleCreateBody,
  Conversation,
  ConversationDetail,
  CreateConversationBody,
  FileContent,
  GitDiff,
  GitStatus,
  Health,
  Job,
  ModelSettings,
  ModelSettingsUpdate,
  ModelTestResult,
  Note,
  NoteCreateBody,
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
export const getHealth = (): Promise<Health> => request<Health>('/api/health')

/* -------------------------------- projects --------------------------------- */

export const listProjects = (): Promise<Project[]> => request<Project[]>('/api/projects')

export const createProject = (body: { path: string; name?: string | null }): Promise<Project> =>
  request<Project>('/api/projects', { method: 'POST', body })

export const getProject = (projectId: number): Promise<Project> =>
  request<Project>(`/api/projects/${projectId}`)

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

export const getGitStatus = (projectId: number): Promise<GitStatus> =>
  request<GitStatus>(`/api/projects/${projectId}/git/status`)

export const getGitDiff = (projectId: number, path?: string): Promise<GitDiff> => {
  const qs = path ? `?${new URLSearchParams({ path }).toString()}` : ''
  return request<GitDiff>(`/api/projects/${projectId}/git/diff${qs}`)
}

/* ---------------------------- indexing / search ----------------------------- */

export const startIndex = (projectId: number): Promise<{ job_id: number }> =>
  request<{ job_id: number }>(`/api/projects/${projectId}/index`, { method: 'POST' })

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

/* ------------------------------ conversations ------------------------------- */

export const createConversation = (body: CreateConversationBody): Promise<Conversation> =>
  request<Conversation>('/api/conversations', { method: 'POST', body })

export const listConversations = (projectId: number, signal?: AbortSignal): Promise<Conversation[]> =>
  request<Conversation[]>(`/api/conversations?${new URLSearchParams({ project_id: String(projectId) }).toString()}`, {
    signal,
  })

export const getConversation = (conversationId: string, signal?: AbortSignal): Promise<ConversationDetail> =>
  request<ConversationDetail>(`/api/conversations/${encodeURIComponent(conversationId)}`, { signal })

export const deleteConversation = (conversationId: string): Promise<{ ok: boolean }> =>
  request<{ ok: boolean }>(`/api/conversations/${encodeURIComponent(conversationId)}`, { method: 'DELETE' })

/* ----------------------------------- SSE ----------------------------------- */

/**
 * Opens an SSE POST stream and yields parsed `data: {...}` events.
 *
 * Both `POST /messages` and `POST /resume` are JSON requests that answer with
 * `text/event-stream`, so EventSource cannot be used. The server closes the
 * stream after `done`; a nonzero HTTP status is raised as ApiError *before*
 * any event is yielded (e.g. 400 "model not configured"), and transport errors
 * surface as an `error` event so the caller only has one failure channel.
 */
export async function* postSse(path: string, body: unknown, signal?: AbortSignal): AsyncGenerator<AgentStreamEvent> {
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
    yield { type: 'error', detail: `无法连接本机服务：${(err as Error).message}` }
    return
  }

  if (!res.ok) {
    const apiErr = await parseError(res)
    yield { type: 'error', detail: apiErr.detail }
    return
  }
  if (!res.body) {
    yield { type: 'error', detail: '服务未返回数据流' }
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
        const events = parseFrame(frame)
        for (const evt of events) yield evt
        sep = findFrameEnd(buffer)
      }
    }
    const tail = parseFrame(buffer)
    for (const evt of tail) yield evt
  } catch (err) {
    if (!signal?.aborted) {
      yield { type: 'error', detail: `读取数据流失败：${(err as Error).message}` }
    }
  } finally {
    try {
      reader.releaseLock()
    } catch {
      /* already released */
    }
  }
}

function findFrameEnd(buffer: string): number {
  const lf = buffer.indexOf('\n\n')
  const crlf = buffer.indexOf('\r\n\r\n')
  if (lf < 0) return crlf
  if (crlf < 0) return lf
  return Math.min(lf, crlf)
}

function parseFrame(frame: string): AgentStreamEvent[] {
  const out: AgentStreamEvent[] = []
  for (const rawLine of frame.split(/\r?\n/)) {
    const line = rawLine.trim()
    if (!line || line.startsWith(':')) continue // comment/keep-alive
    if (!line.startsWith('data:')) continue
    const payload = line.slice(5).trim()
    if (!payload || payload === '[DONE]') continue
    try {
      const parsed = JSON.parse(payload) as AgentStreamEvent
      if (parsed && typeof parsed === 'object' && typeof (parsed as { type?: unknown }).type === 'string') {
        out.push(parsed)
      } else {
        out.push({ type: 'unknown', raw: parsed })
      }
    } catch {
      out.push({ type: 'unknown', raw: payload })
    }
  }
  return out
}

/** Send a user message; yields token/status/citation/... events. */
export function streamChat(
  conversationId: string,
  content: string,
  signal?: AbortSignal,
): AsyncGenerator<AgentStreamEvent> {
  return postSse(`/api/conversations/${encodeURIComponent(conversationId)}/messages`, { content }, signal)
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

/**
 * `GET /api/notes` requires a `project_id` query parameter (the backend returns
 * 422 without it, even though API_CONTRACT.md does not document the filter).
 * The client-side filter is kept as a safety net so a future backend that
 * ignores the parameter still yields per-project notes.
 */
export const listNotes = async (projectId: number, signal?: AbortSignal): Promise<Note[]> => {
  const notes = await request<Note[]>(`/api/notes?project_id=${projectId}`, { signal })
  return notes.filter((n) => n.project_id === projectId)
}

export const createNote = (body: NoteCreateBody): Promise<Note> =>
  request<Note>('/api/notes', { method: 'POST', body })

export const updateNote = (noteId: number, body: Partial<NoteCreateBody>): Promise<Note> =>
  request<Note>(`/api/notes/${noteId}`, { method: 'PUT', body })

export const deleteNote = (noteId: number): Promise<{ ok: boolean }> =>
  request<{ ok: boolean }>(`/api/notes/${noteId}`, { method: 'DELETE' })

/* ---------------------------------- anchors -------------------------------- */

export const listAnchors = (projectId: number, signal?: AbortSignal): Promise<Anchor[]> =>
  request<Anchor[]>(`/api/projects/${projectId}/anchors`, { signal })

export const refreshAnchors = (projectId: number): Promise<AnchorRefreshResult> =>
  request<AnchorRefreshResult>(`/api/projects/${projectId}/anchors/refresh`, { method: 'POST' })

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
