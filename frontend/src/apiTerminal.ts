/**
 * Client for the integrated terminal API (backend/app/api/terminal.py).
 *
 * The API answers `POST /run` with `text/event-stream`, so this module parses
 * SSE frames from a `fetch` body instead of using `EventSource` (which cannot
 * POST and cannot carry the session token header).
 */

import { apiBase, getToken } from './bridge'

export type TerminalStreamName = 'stdout' | 'stderr'

export interface TerminalStartEvent {
  type: 'start'
  command: string
  cwd: string
}

export interface TerminalOutputEvent {
  type: 'output'
  stream: TerminalStreamName
  text: string
}

export interface TerminalExitEvent {
  type: 'exit'
  code: number
  duration_ms: number
  truncated: boolean
}

export interface TerminalErrorEvent {
  type: 'error'
  detail: string
}

export interface TerminalDoneEvent {
  type: 'done'
}

export type TerminalEvent =
  | TerminalStartEvent
  | TerminalOutputEvent
  | TerminalExitEvent
  | TerminalErrorEvent
  | TerminalDoneEvent

/** One finished command, as stored by the backend ring buffer. */
export interface TerminalHistoryItem {
  id: string
  command: string
  cwd: string
  source: 'user' | 'agent'
  exit_code: number
  started_at: string
  duration_ms: number
  output_tail: string
}

export interface TerminalHandlers {
  onStart?: (event: TerminalStartEvent) => void
  onOutput?: (event: TerminalOutputEvent) => void
  onExit?: (event: TerminalExitEvent) => void
  onError?: (event: TerminalErrorEvent) => void
  onDone?: () => void
}

function authHeaders(extra?: Record<string, string>): Record<string, string> {
  const headers: Record<string, string> = { ...extra }
  const token = getToken()
  if (token) headers['X-Session-Token'] = token
  return headers
}

/** FastAPI answers `{"detail": "..."}`; 422 uses an array of issues. */
async function readError(res: Response): Promise<string> {
  const text = await res.text().catch(() => '')
  if (text) {
    try {
      const body: unknown = JSON.parse(text)
      if (body && typeof body === 'object') {
        const detail = (body as { detail?: unknown }).detail
        if (typeof detail === 'string') return detail
        if (Array.isArray(detail)) {
          const parts = detail.map((item) => {
            if (item && typeof item === 'object') {
              const msg = (item as { msg?: unknown }).msg
              return String(msg ?? '')
            }
            return String(item)
          })
          const joined = parts.filter(Boolean).join('; ')
          if (joined) return joined
        }
      }
    } catch {
      /* not JSON: fall through to the raw body */
    }
  }
  return text.trim() || `请求失败（HTTP ${res.status}）`
}

function asText(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

/** Narrow an arbitrary JSON payload to a terminal event; null when unknown. */
function toEvent(payload: unknown): TerminalEvent | null {
  if (!payload || typeof payload !== 'object') return null
  const raw = payload as Record<string, unknown>
  switch (raw.type) {
    case 'start':
      return { type: 'start', command: asText(raw.command), cwd: asText(raw.cwd) || '.' }
    case 'output':
      return {
        type: 'output',
        stream: raw.stream === 'stderr' ? 'stderr' : 'stdout',
        text: asText(raw.text),
      }
    case 'exit':
      return {
        type: 'exit',
        code: asNumber(raw.code),
        duration_ms: asNumber(raw.duration_ms),
        truncated: raw.truncated === true,
      }
    case 'error':
      return { type: 'error', detail: asText(raw.detail) || '执行失败' }
    case 'done':
      return { type: 'done' }
    default:
      return null
  }
}

/** Index of the next frame terminator, tolerating CRLF from proxies. */
function findFrameEnd(buffer: string): number {
  const lf = buffer.indexOf('\n\n')
  const crlf = buffer.indexOf('\r\n\r\n')
  if (lf < 0) return crlf
  if (crlf < 0) return lf
  return Math.min(lf, crlf)
}

function parseFrame(frame: string): TerminalEvent[] {
  const events: TerminalEvent[] = []
  for (const rawLine of frame.split(/\r?\n/)) {
    const line = rawLine.trim()
    if (!line || line.startsWith(':')) continue // comment / keep-alive
    if (!line.startsWith('data:')) continue
    const payload = line.slice(5).trim()
    if (!payload || payload === '[DONE]') continue
    try {
      const event = toEvent(JSON.parse(payload))
      if (event) events.push(event)
    } catch {
      /* a truncated frame is dropped rather than failing the whole stream */
    }
  }
  return events
}

/**
 * Run one command and report its frames as they arrive.
 *
 * Resolves when the stream ends (normally, on abort, or on a transport error —
 * the last case is delivered through `onError`). A rejected command or a bad
 * `cwd` arrives as an `error` frame inside an HTTP 200 stream, which is exactly
 * what the panel renders as a red line.
 */
export async function runTerminal(
  projectId: number,
  command: string,
  cwd: string | undefined,
  handlers: TerminalHandlers,
  signal?: AbortSignal,
): Promise<void> {
  let res: Response
  try {
    res = await fetch(`${apiBase()}/api/projects/${projectId}/terminal/run`, {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json', Accept: 'text/event-stream' }),
      body: JSON.stringify(cwd ? { command, cwd } : { command }),
      signal,
    })
  } catch (err) {
    if (signal?.aborted) return
    handlers.onError?.({ type: 'error', detail: `无法连接本机服务：${(err as Error).message}` })
    handlers.onDone?.()
    return
  }

  if (!res.ok) {
    handlers.onError?.({ type: 'error', detail: await readError(res) })
    handlers.onDone?.()
    return
  }
  if (!res.body) {
    handlers.onError?.({ type: 'error', detail: '服务未返回数据流' })
    handlers.onDone?.()
    return
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let sawDone = false

  const emit = (event: TerminalEvent): void => {
    switch (event.type) {
      case 'start':
        handlers.onStart?.(event)
        break
      case 'output':
        handlers.onOutput?.(event)
        break
      case 'exit':
        handlers.onExit?.(event)
        break
      case 'error':
        handlers.onError?.(event)
        break
      case 'done':
        sawDone = true
        handlers.onDone?.()
        break
    }
  }

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let sep = findFrameEnd(buffer)
      while (sep >= 0) {
        const frame = buffer.slice(0, sep)
        buffer = buffer.slice(sep + (buffer[sep] === '\r' ? 4 : 2))
        for (const event of parseFrame(frame)) emit(event)
        sep = findFrameEnd(buffer)
      }
    }
    for (const event of parseFrame(buffer)) emit(event)
    if (!sawDone) handlers.onDone?.()
  } catch (err) {
    if (!signal?.aborted) {
      handlers.onError?.({ type: 'error', detail: `读取数据流失败：${(err as Error).message}` })
      handlers.onDone?.()
    }
  } finally {
    try {
      reader.releaseLock()
    } catch {
      /* already released */
    }
  }
}

/** Recent commands for this project, newest first. */
export async function getTerminalHistory(
  projectId: number,
  signal?: AbortSignal,
): Promise<TerminalHistoryItem[]> {
  const res = await fetch(`${apiBase()}/api/projects/${projectId}/terminal/history`, {
    headers: authHeaders(),
    signal,
  })
  if (!res.ok) throw new Error(await readError(res))
  const body: unknown = await res.json()
  if (!body || typeof body !== 'object') return []
  const items = (body as { items?: unknown }).items
  if (!Array.isArray(items)) return []
  return items.flatMap((item): TerminalHistoryItem[] => {
    if (!item || typeof item !== 'object') return []
    const raw = item as Record<string, unknown>
    return [
      {
        id: asText(raw.id),
        command: asText(raw.command),
        cwd: asText(raw.cwd) || '.',
        source: raw.source === 'agent' ? 'agent' : 'user',
        exit_code: asNumber(raw.exit_code),
        started_at: asText(raw.started_at),
        duration_ms: asNumber(raw.duration_ms),
        output_tail: asText(raw.output_tail),
      },
    ]
  })
}

/**
 * Ask the backend to kill whatever is still running for this project.
 *
 * Normally the abort of the run stream is enough (the server kills the child
 * when the generator is closed), but this also covers a panel that was closed
 * before the disconnect was noticed. Best effort: never throws.
 */
export async function cancelTerminal(projectId: number): Promise<number> {
  try {
    const res = await fetch(`${apiBase()}/api/projects/${projectId}/terminal/cancel`, {
      method: 'POST',
      headers: authHeaders(),
    })
    if (!res.ok) return 0
    const body: unknown = await res.json()
    if (!body || typeof body !== 'object') return 0
    return asNumber((body as { cancelled?: unknown }).cancelled)
  } catch {
    return 0
  }
}
